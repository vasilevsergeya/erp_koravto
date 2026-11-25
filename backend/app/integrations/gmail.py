"""Read Gmail threads through the authenticated mailbox's fixed Google API endpoints."""

import asyncio
import base64
import binascii
import hashlib
import json
import re
import time
from collections import OrderedDict
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses
from html import unescape
from html.parser import HTMLParser
from urllib.parse import quote

import httpx

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
REFRESH_URL = "https://oauth2.googleapis.com/token"
THREAD_ID = r"[a-zA-Z0-9_-]{1,128}"
HEADERS = ("From", "To", "Cc", "Bcc", "Subject")
_METADATA: OrderedDict[tuple, dict] = OrderedDict()
_access: tuple[str, float, str] = ("", 0, "")
_refresh_lock = asyncio.Lock()


class GmailError(Exception):
    pass


def credentials(value: str) -> str | dict[str, str]:
    value = value.strip()
    if value.startswith("{"):
        try:
            data = json.loads(value)
            fields = {key: data[key] for key in ("client_id", "client_secret", "refresh_token")}
            if all(isinstance(v, str) and v.strip() for v in fields.values()):
                return fields
        except (ValueError, TypeError, KeyError):
            pass
    elif re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value):
        return value
    raise GmailError("invalid_credentials")


async def bearer(client: httpx.AsyncClient, value: str) -> str:
    global _access
    data = credentials(value)
    if isinstance(data, str):
        return data
    key = hashlib.sha256(value.encode()).hexdigest()
    async with _refresh_lock:
        if _access[0] == key and _access[1] > time.monotonic():
            return _access[2]
        response = await client.post(REFRESH_URL, data={**data, "grant_type": "refresh_token"})
        if response.status_code != 200:
            raise GmailError("invalid_credentials" if response.status_code < 500 else "unavailable")
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token.isascii() or not token.isprintable():
            raise GmailError("invalid_credentials")
        _access = (key, time.monotonic() + max(0, int(body["expires_in"]) - 60), token)
        return token


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("head", "script", "style"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "br", "div", "li", "tr"):
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("head", "script", "style") and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in ("p", "div", "li", "tr"):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def html_to_text(body):
    parser = PlainHTML()
    parser.feed(body)
    parser.close()
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.text))


def decoded_header(value):
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        return value


def addresses(values):
    result = []
    for name, email in getaddresses(values):
        if re.fullmatch(r"[^@\s<>]+@[^@\s<>]+", email) and len(email) <= 254:
            result.append({"name": decoded_header(name).strip(), "email": email})
    return result


def header_values(part, key):
    return [h["value"] for h in part.get("headers", []) if h["name"].lower() == key.lower()]


def message_body(part, attachments):
    filename = part.get("filename")
    if filename:
        attachments.append(filename)
        return ""
    kind = part.get("mimeType", "").lower()
    if kind.startswith("multipart/"):
        children = part.get("parts", [])
        if kind == "multipart/alternative":
            children = sorted(children, key=lambda p: p.get("mimeType") != "text/plain")
            for child in children:
                body = message_body(child, attachments)
                if body:
                    return body
            return ""
        return "\n\n".join(filter(None, (message_body(p, attachments) for p in children)))
    if kind not in ("text/plain", "text/html"):
        return ""
    data = part.get("body", {}).get("data", "")
    content_type = Message()
    content_type["Content-Type"] = next(iter(header_values(part, "Content-Type")), kind)
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (ValueError, binascii.Error):
        return ""
    try:
        body = raw.decode(content_type.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        body = raw.decode(errors="replace")
    if kind == "text/html":
        body = html_to_text(body)
    return body.strip()


def describe(thread, mailbox, *, full=False):
    messages = []
    contacts: dict[str, dict] = {}
    items = sorted(thread.get("messages", []), key=lambda item: int(item["internalDate"]))
    own = {mailbox.casefold()}
    for item in items:
        if "SENT" in item.get("labelIds", []):
            own.update(
                a["email"].casefold() for a in addresses(header_values(item["payload"], "From"))
            )
    for item in items:
        labels = item.get("labelIds", [])
        if any(label in labels for label in ("DRAFT", "SPAM", "TRASH", "CHAT")):
            continue
        part = item["payload"]
        sender = addresses(header_values(part, "From"))
        recipients = addresses(header_values(part, "To") + header_values(part, "Cc"))
        outgoing = "SENT" in labels or bool(sender and sender[0]["email"].casefold() in own)
        if outgoing:
            recipients.extend(addresses(header_values(part, "Bcc")))
        for contact in recipients if outgoing else sender:
            key = contact["email"].casefold()
            if key not in own:
                contacts[key] = contact
        msg = {
            "id": item["id"],
            "sender": sender[0] if sender else None,
            "recipients": recipients,
            "outgoing": outgoing,
            "unread": "UNREAD" in labels,
            "at": int(item["internalDate"]),
            "subject": decoded_header(next(iter(header_values(part, "Subject")), "")),
            "snippet": unescape(item.get("snippet", "")),
        }
        if full:
            names: list[str] = []
            msg["text"] = message_body(part, names) or msg["snippet"]
            msg["attachments"] = names
        messages.append(msg)
    if not messages:
        return None
    last = messages[-1]
    result = {
        "id": thread["id"],
        "subject": messages[0]["subject"] or last["subject"],
        "contacts": list(contacts.values()),
        "at": last["at"],
        "snippet": last["snippet"],
        "unread": any(m["unread"] for m in messages),
        "count": len(messages),
    }
    if full:
        result["messages"] = messages
    return result


def correspondent_query(email):
    # Quote the address as one search term, including addresses with a literal '+'.
    term = '"' + email.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "{" + " ".join(f"{field}:{term}" for field in ("from", "to", "cc", "bcc")) + "}"


def dialog_rows(summaries):
    rows = {}
    for summary in sorted(summaries, key=lambda s: s["at"], reverse=True):
        for contact in summary["contacts"]:
            key = contact["email"].casefold()
            if key not in rows:
                rows[key] = {
                    "email": key,
                    "name": contact["name"],
                    "threadId": summary["id"],  # Evidence for the existing 1C lookup.
                    "subject": summary["subject"],
                    "snippet": summary["snippet"],
                    "at": summary["at"],
                    "unread": summary["unread"],
                }
    return list(rows.values())


def belongs_to(summary, email):
    return any(c["email"].casefold() == email.casefold() for c in summary["contacts"])


class GmailMailbox:
    def __init__(self, client: httpx.AsyncClient, token: str):
        self.client = client
        self.auth = {"Authorization": "Bearer " + token}

    async def get(self, path, params=None):
        async with self.client.stream("GET", BASE + path, headers=self.auth, params=params) as res:
            if res.status_code != 200:
                raise GmailError(
                    {
                        401: "invalid_credentials",
                        403: "access_denied",
                        404: "not_found",
                        429: "rate_limited",
                    }.get(res.status_code, "unavailable")
                )
            body = bytearray()
            async for chunk in res.aiter_bytes():
                body.extend(chunk)
                if len(body) > 32 * 1024 * 1024:
                    raise GmailError("too_large")
        return await asyncio.to_thread(json.loads, body)

    async def address(self):
        return (await self.get("/profile", {"fields": "emailAddress"}))["emailAddress"]

    async def metadata(self, thread_id):
        return await self.get(
            "/threads/" + thread_id,
            {
                "format": "metadata",
                "metadataHeaders": HEADERS,
                "fields": "id,messages(id,labelIds,internalDate,snippet,payload/headers)",
            },
        )

    async def threads(self, page, search):
        mailbox = await self.address()
        params = {"maxResults": 15, "q": "-in:drafts -in:chats " + search}
        if page:
            params["pageToken"] = page
        listing = await self.get("/threads", params)
        limit = asyncio.Semaphore(4)

        async def summary(item):
            thread_id = item["id"]
            if not re.fullmatch(THREAD_ID, thread_id):
                raise GmailError("unavailable")
            history = item.get("historyId")
            key = (mailbox, thread_id, history)
            if history and key in _METADATA:
                _METADATA.move_to_end(key)
                return _METADATA[key]
            async with limit:
                try:
                    thread = await self.metadata(thread_id)
                except GmailError as exc:
                    if str(exc) == "not_found":
                        return None
                    raise
            result = describe(thread, mailbox)
            if result:
                result["snippet"] = result["snippet"] or unescape(item.get("snippet", ""))
                if history:
                    _METADATA[key] = result
                    if len(_METADATA) > 512:
                        _METADATA.popitem(last=False)
            return result

        rows = await asyncio.gather(
            *(summary(item) for item in listing.get("threads", [])), return_exceptions=True
        )
        for row in rows:
            if isinstance(row, BaseException):
                raise row
        return {
            "mailbox": mailbox,
            "threads": [r for r in rows if r],
            "next": listing.get("nextPageToken"),
        }

    async def thread(self, thread_id):
        mailbox = await self.address()
        thread = await self.get("/threads/" + thread_id, {"format": "full"})
        await self.load_text_bodies(thread.get("messages", []))
        result = await asyncio.to_thread(describe, thread, mailbox, full=True)
        if result is None:
            raise GmailError("not_found")
        return result

    async def load_text_bodies(self, messages):
        # Gmail may store a text body separately, just like a file attachment.
        remaining = 16 * 1024 * 1024
        for message in messages:
            if {"DRAFT", "SPAM", "TRASH", "CHAT"}.intersection(message.get("labelIds", [])):
                continue
            pending = [message["payload"]]
            while pending:
                part = pending.pop()
                if part.get("filename"):
                    continue
                kind = part.get("mimeType", "").lower()
                if kind.startswith("multipart/"):
                    pending.extend(part.get("parts", []))
                body = part.get("body", {})
                if (
                    kind not in ("text/plain", "text/html")
                    or not body.get("attachmentId")
                    or body.get("data")
                ):
                    continue
                if int(body.get("size", 0)) > remaining:
                    raise GmailError("too_large")
                path = (
                    "/messages/"
                    + quote(message["id"], safe="")
                    + "/attachments/"
                    + quote(body["attachmentId"], safe="")
                )
                body["data"] = (await self.get(path))["data"]
                remaining -= len(body["data"]) * 3 // 4
                if remaining < 0:
                    raise GmailError("too_large")

    async def dialog_page(self, page, search, email=""):
        mailbox = await self.address()
        query = correspondent_query(email) if email else search
        params = {"maxResults": 15 if email else 40, "q": "-in:drafts -in:chats " + query}
        if page:
            params["pageToken"] = page
        listing = await self.get("/messages", params)
        limit = asyncio.Semaphore(4)

        async def load(item):
            async with limit:
                options = (
                    {"format": "full"}
                    if email
                    else {
                        "format": "metadata",
                        "metadataHeaders": HEADERS,
                        "fields": "id,threadId,labelIds,internalDate,snippet,payload/headers",
                    }
                )
                try:
                    message = await self.get("/messages/" + quote(item["id"], safe=""), options)
                except GmailError as exc:
                    if str(exc) == "not_found":
                        return None
                    raise
                single = {"id": message["threadId"], "messages": [message]}
                summary = describe(single, mailbox)
                if not email or not summary:
                    return summary
                if not belongs_to(summary, email):
                    return None
                await self.load_text_bodies([message])
                detail = await asyncio.to_thread(describe, single, mailbox, full=True)
                return detail["messages"][0]

        items = await asyncio.gather(*(load(item) for item in listing.get("messages", [])))
        rows = [item for item in items if item]
        result = {"mailbox": mailbox, "next": listing.get("nextPageToken")}
        if email:
            return {**result, "messages": sorted(rows, key=lambda m: m["at"])}
        return {**result, "dialogs": dialog_rows(rows)}
