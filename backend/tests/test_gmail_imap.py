import base64
import unittest
from unittest.mock import MagicMock, patch

from imapclient.exceptions import LoginError
from imapclient.response_parser import parse_fetch_response

from app.integrations.gmail import GmailError
from app.integrations.gmail_imap import (
    HEADERS,
    GmailImapMailbox,
    address_page,
    app_password,
    connection,
    list_threads,
)

BOX = "sales@example.test"
BUYER = "purchase@example.test"
FAKE = "ABCD EFGH IJKL MNOP"


def wire_metadata(uid, sender, recipient, labels=b"", tid=123):
    headers = f"From: {sender}\r\nTo: {recipient}\r\nSubject: Parts\r\n\r\n".encode()
    # Use real IMAP response parsing, including multipart structure and IMAP literals.
    structure = (
        b'(("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "BASE64" 40 1)'
        b'("APPLICATION" "PDF" ("NAME" "parts.pdf") NIL NIL "BASE64" 99999999)'
        b'"MIXED")'
    )
    start = (
        f"{uid} (UID {uid} X-GM-THRID {tid} ".encode()
        + b"X-GM-LABELS ("
        + labels
        + b') FLAGS () INTERNALDATE "25-Nov-2026 12:15:02 +0000" BODYSTRUCTURE '
        + structure
        + b" BODY["
        + HEADERS.encode()
        + b"] {"
        + str(len(headers)).encode()
        + b"}"
    )
    return parse_fetch_response([(start, headers), b")"], normalise_times=False)[uid]


class GmailImapTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_threads_search_attachments_contacts_and_seen(self):
        rows = {12: wire_metadata(12, BUYER, BOX), 13: wire_metadata(13, BOX, BUYER, b"\\Sent")}
        info = {b"UIDNEXT": 14, b"UIDVALIDITY": 21}

        def fetch(ids, fields):
            if "X-GM-THRID" in fields:
                return {n: rows[n] for n in ids}
            self.assertIn(fields[0], ["BODY.PEEK[1]<0.1024>", "BODY.PEEK[1]"])
            key = b"BODY[1]<0>" if "<0." in fields[0] else b"BODY[1]"
            return {
                n: {
                    key: base64.b64encode("Письмо\n\n> Предыдущее письмо\n>> Другой ответ".encode())
                }
                for n in ids
            }

        with patch("app.integrations.gmail_imap.IMAPClient") as ctor:
            fake = ctor.return_value.__enter__.return_value
            fake.list_folders.return_value = [((b"\\All",), b"/", "[Gmail]/Вся почта")]
            fake.select_folder.return_value = info
            fake.search.return_value = [12, 13]
            fake.fetch.side_effect = fetch
            adapter = GmailImapMailbox(BOX, app_password(FAKE))
            result = await adapter.threads("", "Колодки")
            self.assertEqual(result["threads"][0]["snippet"], "Письмо")
            self.assertEqual(result["threads"][0]["id"], "7b")
            self.assertIsNone(result["next"])
            self.assertIn("X-GM-RAW", fake.search.call_args.args[0])
            thread = await adapter.thread("7b")
            self.assertEqual(thread["count"], 2)
            self.assertEqual(thread["contacts"], [{"email": BUYER, "name": ""}])
            self.assertEqual([m["outgoing"] for m in thread["messages"]], [False, True])
            self.assertEqual(thread["messages"][0]["text"], "Письмо")
            self.assertEqual(thread["messages"][1]["attachments"], ["parts.pdf"])
            self.assertEqual((await adapter.metadata("7b"))["id"], "7b")
            fake.login.assert_called_with(BOX, "ABCDEFGHIJKLMNOP")
            fake.select_folder.assert_called_with("[Gmail]/Вся почта", readonly=True)
            self.assertTrue(ctor.call_args.kwargs["ssl_context"].check_hostname)
            self.assertEqual(ctor.call_args.args, ("imap.gmail.com",))
            self.assertEqual(ctor.call_args.kwargs["port"], 993)
            fake.store.assert_not_called()

    def test_address_history_pages_cross_gmail_threads_and_filter_exact_sender(self):
        other = "other@example.test"
        rows = {
            uid: wire_metadata(
                uid,
                BOX if uid % 2 else BUYER.upper(),
                BUYER if uid % 2 else BOX,
                b"\\Sent" if uid % 2 else b"",
                tid=uid,
            )
            for uid in range(1, 18)
        }
        rows[17] = wire_metadata(17, other, BUYER, tid=16)
        info = {b"UIDNEXT": 18, b"UIDVALIDITY": 9}
        client = MagicMock()

        def search(criteria, **kwargs):
            upper = int(criteria[criteria.index("UID") + 1].split(":")[1])
            return [uid for uid in rows if uid <= upper]

        def fetch(ids, fields):
            if "X-GM-THRID" in fields:
                return {uid: rows[uid] for uid in ids}
            key = b"BODY[1]<0>" if "<0." in fields[0] else b"BODY[1]"
            return {uid: {key: b"SGVsbG8="} for uid in ids}

        client.search.side_effect = search
        client.fetch.side_effect = fetch
        listing = address_page(client, info, BOX, "", "", "")
        self.assertEqual({r["email"] for r in listing["dialogs"]}, {BUYER, other})
        first = address_page(client, info, BOX, "", "", BUYER)
        self.assertIn("X-GM-RAW", client.search.call_args.args[0])
        self.assertEqual(first["next"], "9.2")
        second = address_page(client, info, BOX, first["next"], "", BUYER)
        self.assertIsNone(second["next"])
        messages = first["messages"] + second["messages"]
        self.assertCountEqual([m["id"] for m in messages], [str(i) for i in range(1, 17)])
        self.assertTrue(all(m["outgoing"] == bool(int(m["id"]) % 2) for m in messages))
        self.assertTrue(all(m["text"] == "Hello" for m in messages))

    def test_login_failure_does_not_echo_credentials(self):
        with patch("app.integrations.gmail_imap.IMAPClient") as ctor:
            ctor.return_value.__enter__.return_value.login.side_effect = LoginError(
                "synthetic error"
            )
            with self.assertRaisesRegex(GmailError, "^imap_login$"), connection(BOX, "test"):
                self.fail()

    def test_pagination_resumes_at_next_uid_and_rejects_changed_folder(self):
        client = MagicMock()
        rows = {n: wire_metadata(n, BUYER, BOX, tid=n) for n in range(1, 20)}
        client.search.return_value = list(rows)

        def fetch(ids, fields):
            if "X-GM-THRID" in fields:
                return {n: rows[n] for n in ids}
            return {n: {b"BODY[1]<0>": b"QQ=="} for n in ids}

        client.fetch.side_effect = fetch
        info = {b"UIDVALIDITY": 100, b"UIDNEXT": 20}
        page = list_threads(client, info, BOX, "", "")
        self.assertEqual(len(page["threads"]), 15)
        self.assertEqual(page["next"], "100.4")
        client.search.return_value = [1, 2, 3, 4]
        last = list_threads(client, info, BOX, page["next"], "")
        self.assertIsNone(last["next"])
        self.assertIn("1:4", client.search.call_args.args[0])
        self.assertFalse({r["id"] for r in page["threads"]} & {r["id"] for r in last["threads"]})
        with self.assertRaisesRegex(GmailError, "page_expired"):
            list_threads(client, info, BOX, "2.4", "")
