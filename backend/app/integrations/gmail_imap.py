"""Read a Gmail mailbox over verified TLS using its Google app password."""

import asyncio
import imaplib
import json
import quopri
import re
import ssl
from base64 import b64decode
from binascii import Error as Base64Error
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from email import policy
from email.parser import BytesHeaderParser
from functools import partial

from imapclient import IMAPClient
from imapclient.exceptions import LoginError

from app.integrations.gmail import GmailError, decoded_header, describe, html_to_text

_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gmail-imap")
HEADERS = "HEADER.FIELDS (FROM TO CC SUBJECT)"
FIELDS = ["X-GM-THRID", "X-GM-LABELS", "INTERNALDATE", "FLAGS", f"BODY.PEEK[{HEADERS}]"]
PAGE = 15
MAX_TEXT = 12 * 1024 * 1024


def app_password(value: str) -> str:
    compact = "".join(value.split())
    if not re.fullmatch(r"[a-zA-Z0-9]{16}", compact):
        raise GmailError("password_format")
    return compact


def imap_credentials(value: str):
    if value.startswith("{"):
        data = json.loads(value)
        if isinstance(data, dict) and data.keys() == {"email", "app_password"}:
            return GmailImapMailbox(data["email"], data["app_password"])
    return None


@contextmanager
def connection(email, password):
    try:
        with IMAPClient(
            "imap.gmail.com",
            port=993,
            ssl=True,
            ssl_context=ssl.create_default_context(),
            timeout=12,
        ) as client:
            client.normalise_times = False
            client.login(email, password)
            folder = next(
                (
                    name
                    for flags, _, name in client.list_folders()
                    if b"\\all" in {flag.lower() for flag in flags}
                ),
                None,
            )
            if not folder:
                raise GmailError("all_mail_hidden")
            info = client.select_folder(folder, readonly=True)
            yield client, info
    except LoginError:
        raise GmailError("imap_login") from None
    except (OSError, imaplib.IMAP4.error):
        raise GmailError("imap_unavailable") from None


def metadata_message(uid, row):
    flags = {f.lower() for f in row.get(b"FLAGS", ())}
    labels = {f.lower() for f in row.get(b"X-GM-LABELS", ())}
    marks = [
        key
        for flag, key in (
            (b"\\sent", "SENT"),
            (b"\\draft", "DRAFT"),
            (b"\\spam", "SPAM"),
            (b"\\trash", "TRASH"),
            (b"\\chats", "CHAT"),
        )
        if flag in labels
    ]
    if b"\\seen" not in flags:
        marks.append("UNREAD")
    if b"\\draft" in flags or b"\\deleted" in flags:
        marks.append("DRAFT")
    raw = row[f"BODY[{HEADERS}]".encode()]
    headers = BytesHeaderParser(policy=policy.default).parsebytes(raw)
    return {
        "id": str(uid),
        "internalDate": int(row[b"INTERNALDATE"].timestamp() * 1000),
        "labelIds": marks,
        "payload": {"headers": [{"name": k, "value": str(v)} for k, v in headers.items()]},
    }


def params(items):
    return dict(zip(items[::2], items[1::2])) if isinstance(items, tuple) else {}


def text_parts(structure, files, prefix=""):
    """Choose the plain alternative, skip binary payloads, retain attachment names."""
    if not structure:
        return []
    if isinstance(structure[0], list):
        parts = [
            text_parts(part, files, f"{prefix}.{i}" if prefix else str(i))
            for i, part in enumerate(structure[0], 1)
        ]
        if structure[1].upper() == b"ALTERNATIVE":
            choices = [choice for choice in parts if choice]
            return next((p for p in choices if p[0][1] == b"PLAIN"), choices[-1] if choices else [])
        return [leaf for group in parts for leaf in group]
    media, subtype = structure[0].upper(), structure[1].upper()
    options = {k.upper(): v for k, v in params(structure[2]).items()}
    disposition = next(
        (
            v
            for v in structure[7:]
            if isinstance(v, tuple) and len(v) == 2 and v[0] in (b"ATTACHMENT", b"INLINE")
        ),
        None,
    )
    name = options.get(b"NAME")
    if disposition:
        name = next(
            (v for k, v in params(disposition[1]).items() if k.upper() == b"FILENAME"), name
        )
    if name:
        files.append(decoded_header(name.decode("utf-8", "replace")))
        return []
    if media == b"TEXT" and subtype in (b"PLAIN", b"HTML"):
        return [
            (prefix or "1", subtype, options.get(b"CHARSET", b"utf-8"), structure[5], structure[6])
        ]
    return []


def decode_text(raw, subtype, charset, encoding):
    if encoding.upper() == b"BASE64":
        try:
            raw = b64decode(b"".join(raw.split()) + b"===")
        except (ValueError, Base64Error):
            return ""
    elif encoding.upper() == b"QUOTED-PRINTABLE":
        raw = quopri.decodestring(raw)
    try:
        text = raw.decode(charset.decode("ascii"), "replace")
    except (LookupError, UnicodeError):
        text = raw.decode("utf-8", "replace")
    return (html_to_text(text) if subtype == b"HTML" else text).strip()


def thread_rows(client, thread_id, fields):
    if not re.fullmatch(r"[0-9a-fA-F]{1,16}", thread_id):
        raise GmailError("not_found")
    uids = client.search(["UNDELETED", "UNDRAFT", "X-GM-THRID", str(int(thread_id, 16))])
    if not uids:
        raise GmailError("not_found")
    if len(uids) > 1000:
        raise GmailError("too_large")
    return client.fetch(uids, fields)


def read_thread(client, info, email, thread_id, full):
    rows = thread_rows(client, thread_id, FIELDS + (["BODYSTRUCTURE"] if full else []))
    thread = {
        "id": thread_id,
        "messages": [metadata_message(uid, row) for uid, row in rows.items()],
    }
    if not full:
        return thread
    result = describe(thread, email, full=True)
    if not result:
        raise GmailError("not_found")
    remaining = MAX_TEXT
    # Fetch equivalent sections together across messages. Named attachments stay on Gmail.
    sections: dict[str, dict] = {}
    texts: dict[str, list[str]] = {}
    for msg in result["messages"]:
        uid = int(msg["id"])
        parts = text_parts(rows[uid][b"BODYSTRUCTURE"], msg["attachments"])
        texts[msg["id"]] = [""] * len(parts)
        for i, (section, subtype, charset, encoding, size) in enumerate(parts):
            remaining -= int(size)
            if remaining < 0:
                raise GmailError("too_large")
            sections.setdefault(section, {})[uid] = (i, subtype, charset, encoding)
    for section, group in sections.items():
        for uid, row in client.fetch(list(group), [f"BODY.PEEK[{section}]"]).items():
            i, subtype, charset, encoding = group[uid]
            texts[str(uid)][i] = decode_text(
                row[f"BODY[{section}]".encode()], subtype, charset, encoding
            )
    for msg in result["messages"]:
        msg["text"] = "\n\n".join(filter(None, texts[msg["id"]]))
    return result


def list_threads(client, info, email, page, search):
    validity, high = int(info[b"UIDVALIDITY"]), int(info[b"UIDNEXT"]) - 1
    if page:
        cursor = re.fullmatch(r"(\d{1,10})\.(\d{1,10})", page)
        if not cursor or int(cursor[1]) != validity:
            raise GmailError("page_expired")
        high = min(high, int(cursor[2]))
    if high < 1:
        return {"mailbox": email, "threads": [], "next": None}
    # For browsing, limit the UID range before SEARCH. For an explicit search, Gmail's
    # index can find older matching messages without walking every intermediate page.
    low = 1 if search else max(1, high - 1023)
    criteria = ["UNDELETED", "UNDRAFT", "UID", f"{low}:{high}"]
    if search:
        criteria.extend(["X-GM-RAW", search + " "])  # Keep the query a single quoted IMAP argument.
    uids = sorted(client.search(criteria, charset="UTF-8" if search else None), reverse=True)
    batch = uids[:60]
    rows = client.fetch(batch, FIELDS + ["BODYSTRUCTURE"]) if batch else {}
    summaries = {}
    previews: dict[str, dict] = {}
    last = low
    for uid in batch:
        last = uid
        if uid not in rows:
            continue
        row = rows[uid]
        tid = format(int(row[b"X-GM-THRID"]), "x")
        if tid in summaries:
            continue
        latest = describe({"id": tid, "messages": [metadata_message(uid, row)]}, email)
        if latest:
            # The exact count and participants come from the full thread when opened.
            latest["count"] = None
            summaries[tid] = latest
            parts = text_parts(row[b"BODYSTRUCTURE"], [])
            if parts:
                section, subtype, charset, encoding, _ = parts[0]
                previews.setdefault(section, {})[uid] = (latest, subtype, charset, encoding)
            if len(summaries) == PAGE:
                break
    for section, group in previews.items():
        for uid, row in client.fetch(list(group), [f"BODY.PEEK[{section}]<0.1024>"]).items():
            latest, subtype, charset, encoding = group[uid]
            raw = row.get(f"BODY[{section}]<0>".encode(), b"")
            latest["snippet"] = " ".join(decode_text(raw, subtype, charset, encoding).split())[:180]
    if not batch or last == batch[-1] and len(batch) == len(uids):
        last = low
    return {
        "mailbox": email,
        "threads": list(summaries.values()),
        "next": f"{validity}.{last - 1}" if last > 1 else None,
    }


class GmailImapMailbox:
    def __init__(self, email, password):
        self.email = email
        self._password = password

    def _run(self, operation, args):
        with connection(self.email, self._password) as (client, info):
            return operation(client, info, self.email, *args)

    async def run(self, operation, *args):
        return await asyncio.get_running_loop().run_in_executor(
            _POOL, partial(self._run, operation, args)
        )

    async def check(self):
        await self.run(lambda client, info, email: None)

    async def address(self):
        return self.email

    async def threads(self, page, search):
        return await self.run(list_threads, page, search)

    async def thread(self, thread_id):
        return await self.run(read_thread, thread_id, True)

    async def metadata(self, thread_id):
        return await self.run(read_thread, thread_id, False)
