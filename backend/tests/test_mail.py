"""The mail viewer uses read-only Gmail calls and the existing 1C email contract."""

import base64
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import token_hash
from app.core.settings import invalidate_settings, redis_client
from app.integrations.gmail import REFRESH_URL, GmailMailbox, bearer, describe
from app.integrations.mail_text import visible_mail_text
from app.main import app
from app.models import Conversation, LoginSession, Setting, User, now, uid
from app.services.mail_matches import cache_key

BOX = "parts@example.test"
EMAIL = "buyer+parts@example.test"


def message(ident, at, sender, recipient, labels, body, mime="text/plain"):
    return {
        "id": ident,
        "internalDate": str(at),
        "labelIds": labels,
        "snippet": "Preview",
        "payload": {
            "mimeType": mime,
            "headers": [
                {"name": "Subject", "value": "Order"},
                {"name": "From", "value": sender},
                {"name": "To", "value": recipient},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
        },
    }


def sample():
    return {
        "id": "abc123",
        "messages": [
            message("2", 2001, BOX, EMAIL, ["SENT"], "Answer"),
            message(
                "1",
                2000,
                EMAIL,
                BOX,
                ["INBOX", "UNREAD"],
                "<style>x</style><p>Здравствуйте</p>",
                "text/html",
            ),
            message("3", 2002, BOX, "other@example.test", ["DRAFT"], "Private draft"),
        ],
    }


class GmailContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_thread_order_drafts_html_and_attachment_names(self):
        thread = sample()
        payload = thread["messages"][0]["payload"]
        payload.update(
            mimeType="multipart/mixed",
            parts=[
                {"mimeType": "text/plain", "body": payload["body"]},
                {
                    "mimeType": "application/pdf",
                    "filename": "order.pdf",
                    "body": {"attachmentId": "1"},
                },
            ],
        )
        result = describe(thread, BOX, full=True)
        self.assertEqual([m["id"] for m in result["messages"]], ["1", "2"])
        self.assertEqual([m["outgoing"] for m in result["messages"]], [False, True])
        self.assertEqual(result["messages"][0]["text"], "Здравствуйте")
        self.assertEqual(result["messages"][1]["attachments"], ["order.pdf"])
        self.assertEqual(result["contacts"], [{"email": EMAIL, "name": ""}])
        self.assertTrue(result["unread"])

    def test_keep_only_authored_paragraphs_and_inline_answers(self):
        for raw, expected in [
            (
                "Price > 100 USD.\r\nOn Sat, 28 Nov,\r\nAlex <a@example.test> wrote:\r\n"
                " > First?\r\nYes.\r\n >> Nested question\r\nTwo pieces.",
                "Price > 100 USD.\n\nYes.\n\nTwo pieces.",
            ),
            ("Ответ.\n\n28.11.2026 Анна <a@example.test> написала:\n> Текст", "Ответ."),
            ("Ответ.\nсб, 28 нояб. 2026 г.,\nАнна <a@example.test>:\n> Текст", "Ответ."),
            ("-----Original Message-----\n> Old question\nMy answer below.", "My answer below."),
            (
                "My reply\n__________\nFrom: Customer\nSent: Saturday\nTo: Buyer\n"
                "Subject: Request\nOld message without prefixes",
                "My reply",
            ),
            (
                "From: Seoul\nTo: Sydney\nSubject: Test shipment",
                "From: Seoul\nTo: Sydney\nSubject: Test shipment",
            ),
            (">> Nested history only", ""),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(visible_mail_text(raw), expected)

    def test_gmail_metadata_preview_does_not_repeat_a_flattened_reply(self):
        letter = message("1", 100, EMAIL, BOX, ["INBOX"], "OK")
        letter["snippet"] = "Price &gt; 10. On Sat, 28 Nov, Alex wrote: &gt; Old message"
        row = describe({"id": "test", "messages": [letter]}, BOX)
        self.assertEqual(row["snippet"], "Price > 10.")

    def test_html_quote_containers_and_text_after_them(self):
        raw = (
            "<head><style>.unused{}</style></head><p>Answer &amp; price</p>"
            "<div class='gmail_quote'><div class='gmail_attr'>On Saturday:</div>"
            "<blockquote><div>Old reply</div></blockquote></div>"
            "<p>Deliver Monday.</p><div class='moz-cite-prefix'>Someone wrote:</div>"
            "<blockquote type='cite'><blockquote>Older</blockquote>Old</blockquote>"
            "<div>&gt; another quote<br>One more answer.</div>"
        )
        self.assertEqual(
            visible_mail_text(raw, html=True),
            "Answer & price\n\nDeliver Monday.\n\nOne more answer.",
        )

    def test_quote_only_plain_does_not_return_html_history_or_gmail_snippet(self):
        letter = message("1", 100, EMAIL, BOX, ["INBOX"], "> old message")
        plain = letter["payload"].copy()
        html = message("1", 100, EMAIL, BOX, ["INBOX"], "<p>old message</p>", "text/html")[
            "payload"
        ]
        letter["payload"].update(
            mimeType="multipart/mixed",
            parts=[
                {"mimeType": "multipart/alternative", "parts": [html, plain]},
                {"filename": "quote.pdf", "mimeType": "application/pdf"},
            ],
        )
        letter["snippet"] = "old message from Gmail"
        result = describe({"id": "test", "messages": [letter]}, BOX, full=True)["messages"][0]
        self.assertEqual(result["text"], "")
        self.assertEqual(result["snippet"], "")
        self.assertEqual(result["attachments"], ["quote.pdf"])

    async def test_address_dialogs_across_topics_and_recipients(self):
        other = "second@example.test"
        messages = [
            message("1", 100, EMAIL.upper(), BOX, ["INBOX"], "First topic"),
            message("2", 200, "alias@example.test", EMAIL, ["SENT"], "Second topic"),
            message("3", 300, other, BOX, ["INBOX"], "Only for second contact"),
            message("4", 400, BOX, EMAIL, ["DRAFT"], "Private"),
        ]
        messages[1]["payload"]["headers"].extend(
            [
                {"name": "Cc", "value": BOX + ", " + EMAIL.upper()},
                {"name": "Bcc", "value": other},
            ]
        )
        messages[2]["payload"]["headers"].append({"name": "Cc", "value": EMAIL})
        for item in messages:
            item["threadId"] = "t" + item["id"]
            item["payload"]["headers"][0]["value"] = item["threadId"]

        def handler(req):
            path = req.url.path.rsplit("/", 1)[1]
            if path == "profile":
                return httpx.Response(200, json={"emailAddress": BOX})
            if path == "messages":
                if req.url.params["q"].endswith("search"):
                    self.assertEqual(req.url.params.get("pageToken"), "cursor")
                else:
                    self.assertIn('from:"' + EMAIL + '"', req.url.params["q"])
                return httpx.Response(
                    200,
                    json={
                        "messages": [{"id": m["id"]} for m in messages],
                        "nextPageToken": "older",
                    },
                )
            return httpx.Response(200, json=messages[int(path) - 1])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gmail = GmailMailbox(client, "test")
            page = await gmail.dialog_page("cursor", "search")
            self.assertEqual(
                [(r["email"], r["threadId"]) for r in page["dialogs"]],
                [(other, "t3"), (EMAIL, "t2")],
            )
            history = await gmail.dialog_page("", "", EMAIL)
            self.assertEqual(history["next"], "older")
            self.assertEqual(
                [(m["subject"], m["outgoing"]) for m in history["messages"]],
                [("t1", False), ("t2", True)],
            )

    async def test_separate_text_body_is_loaded_without_downloading_named_files(self):
        thread = sample()
        part = thread["messages"][1]["payload"]
        data = part["body"].pop("data")
        part["body"]["attachmentId"] = "separate-text"

        def handler(req):
            if req.url.path.endswith("/profile"):
                return httpx.Response(200, json={"emailAddress": BOX})
            if req.url.path.endswith("/threads/abc123"):
                return httpx.Response(200, json=thread)
            self.assertTrue(req.url.path.endswith("/messages/1/attachments/separate-text"))
            return httpx.Response(200, json={"data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GmailMailbox(client, "test").thread("abc123")
        self.assertEqual(result["messages"][0]["text"], "Здравствуйте")

    async def test_refresh_and_pagination_only_call_google(self):
        requests = []

        def handler(req):
            requests.append(req)
            if str(req.url) == REFRESH_URL:
                self.assertNotIn("Authorization", req.headers)
                return httpx.Response(200, json={"access_token": "test-token", "expires_in": 3600})
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.headers["Authorization"], "Bearer test-token")
            self.assertEqual(req.url.host, "gmail.googleapis.com")
            if req.url.path.endswith("/profile"):
                return httpx.Response(200, json={"emailAddress": BOX})
            if req.url.path.endswith("/threads"):
                self.assertEqual(req.url.params["pageToken"], "page2")
                self.assertIn("filter", req.url.params["q"])
                return httpx.Response(
                    200, json={"threads": [{"id": "abc123"}], "nextPageToken": "page3"}
                )
            self.assertEqual(req.url.params["format"], "metadata")
            return httpx.Response(200, json=sample())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            credential = json.dumps(
                {
                    "refresh_token": uid(),
                    "client_id": "demo",
                    "client_secret": "demo",
                    "token_uri": "https://example.invalid/ignored",
                }
            )
            gmail = GmailMailbox(client, await bearer(client, credential))
            page = await gmail.threads("page2", "filter")
            self.assertEqual(page["threads"][0]["count"], 2)
            self.assertEqual(page["next"], "page3")
            self.assertEqual(await bearer(client, credential), "test-token")
            self.assertEqual(len(requests), 4)


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Needs isolated PostgreSQL")
class MailAcceptance(unittest.TestCase):
    def test_settings_permissions_email_lookup_and_whatsapp_isolation(self):
        with SessionLocal() as db:
            raw = uid()
            admin = User(
                id=uid(),
                email=uid() + "@example.test",
                display_name="Test",
                role="admin",
                password_hash="test",
                active=True,
                must_change_password=False,
            )
            db.add(admin)
            db.flush()
            db.add(
                LoginSession(
                    token_hash=token_hash(raw),
                    user_id=admin.id,
                    csrf_token=raw,
                    expires_at=now() + timedelta(hours=1),
                )
            )
            db.commit()
            admin_id = admin.id
            initial_count = db.scalar(select(func.count()).select_from(Conversation))
        with TestClient(
            app, base_url=config().public_url, headers={"Origin": config().public_url}
        ) as client:
            self.assertEqual(client.get("/api/mail/threads").status_code, 401)
            client.cookies.set(COOKIE, raw)
            client.headers["X-CSRF-Token"] = raw
            path = "/api/settings/gmail.credentials"
            self.assertEqual(client.put(path, json={"value": "demo-token"}).json()["value"], "••••")
            conn = "/api/settings/gmail/connection"
            self.assertEqual(client.get(conn).json()["mode"], "oauth")
            with patch("app.api.settings.GmailImapMailbox.check") as check:
                self.assertEqual(
                    client.put(conn, json={"email": BOX, "password": "short"}).status_code, 400
                )
                check.assert_not_called()
                payload = {"email": BOX, "password": "abcd efgh ijkl mnop"}
                from app.integrations.gmail import GmailError

                check.side_effect = GmailError("imap_login")
                self.assertEqual(client.put(conn, json=payload).status_code, 400)
                self.assertEqual(client.get(conn).json()["mode"], "oauth")
                check.side_effect = None
                self.assertEqual(
                    client.put(conn, json=payload).json(), {"email": BOX, "mode": "imap"}
                )
                self.assertEqual(client.get(conn).json(), {"email": BOX, "mode": "imap"})
                with SessionLocal() as db:
                    self.assertNotIn(
                        "abcdefghijklmnop", str(db.get(Setting, "gmail.credentials").value)
                    )
                self.assertNotIn("abcdefghijklmnop", client.get("/api/settings").text)
                self.assertEqual(client.put(path, json={"value": "demo-token"}).status_code, 200)
            with SessionLocal() as db:
                self.assertNotIn("demo-token", str(db.get(Setting, "gmail.credentials").value))
                db.get(User, admin_id).role = "manager"
                db.commit()
            self.assertEqual(client.put(path, json={"value": ""}).status_code, 403)
            self.assertEqual(client.get("/api/settings").status_code, 403)
            self.assertEqual(client.put(conn, json=payload).status_code, 403)
            self.assertEqual(client.get(conn).status_code, 403)
            calls = []

            def handler(req):
                self.assertEqual(req.method, "GET")
                if req.url.host == "onec.invalid":
                    calls.append(req)
                    self.assertEqual(req.url.path, "/prod/hs/koravto/customers")
                    self.assertEqual(dict(req.url.params), {"email": EMAIL, "match": "exact"})
                    return httpx.Response(
                        200, json={"clients": [{"name": "Test", "realizations": ["Demo"]}]}
                    )
                if req.url.path.endswith("/profile"):
                    return httpx.Response(200, json={"emailAddress": BOX})
                return httpx.Response(200, json=sample())

            original = httpx.AsyncClient
            with (
                patch(
                    "httpx.AsyncClient",
                    side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
                ),
                patch(
                    "app.api.mail.connection_settings",
                    return_value=(("http://onec.invalid/prod", "user", "password"), "v"),
                ),
            ):
                base = "/api/mail/threads/abc123"
                self.assertEqual(client.get(base).json()["contacts"][0]["email"], EMAIL)
                self.assertEqual(
                    client.get(base + "/customer", params={"email": EMAIL}).json()["clients"][0][
                        "realizations"
                    ],
                    ["Demo"],
                )
                self.assertEqual(
                    client.get(base + "/customer", params={"email": BOX}).status_code, 404
                )
                self.assertEqual(client.post(base).status_code, 405)
                self.assertEqual(len(calls), 1)
                cached = redis_client.get(cache_key("v", EMAIL))
                self.assertEqual(json.loads(cached), {"found": True, "phones": []})
            with SessionLocal() as db:
                self.assertEqual(
                    db.scalar(select(func.count()).select_from(Conversation)), initial_count
                )
                db.delete(db.get(Setting, "gmail.credentials"))
                db.commit()
                invalidate_settings()
            self.assertIsNone(client.get("/api/mail/threads").json()["mailbox"])
