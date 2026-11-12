"""Contract checks and isolated Postgres acceptance; no messages go to real Wazzup."""

import asyncio
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import hash_password, token_hash
from app.core.settings import invalidate_settings, put_setting
from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter
from app.main import app
from app.models import (
    Conversation,
    DeliveryReceipt,
    LoginSession,
    Message,
    User,
    WebhookEvent,
    now,
    uid,
)
from app.services.outbox import deliver_message, status_forward
from app.workers.worker import process_webhook, recover_pending


class SendingContractTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler):
        original = httpx.AsyncClient
        return patch(
            "app.integrations.channels.wazzup.httpx.AsyncClient",
            side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(handler)),
        )

    async def test_exact_payload_and_provider_error_codes(self):
        payload = {
            "channelId": uid(),
            "chatId": "12025550123",
            "chatType": "whatsapp",
            "text": "Hello",
            "crmMessageId": uid(),
        }
        external_id = uid()

        def handler(request):
            self.assertEqual(request.url.path, "/v3/message")
            self.assertEqual(json.loads(request.content), payload)
            return httpx.Response(201, json={"messageId": external_id})

        with self.client(handler):
            self.assertEqual(
                (await WazzupAdapter("test-only").send_message(payload))["messageId"], external_id
            )
        with self.client(
            lambda r: httpx.Response(
                400, json={"error": "BAD_CONTACT", "description": "DO NOT EXPOSE"}
            )
        ):
            with self.assertRaises(IntegrationError) as caught:
                await WazzupAdapter("test-only").send_message(payload)
            self.assertEqual(caught.exception.code, "BAD_CONTACT")
            self.assertFalse(caught.exception.ambiguous)

    async def test_ambiguous_success_body_and_5xx_are_never_reported_as_sent(self):
        for response in [httpx.Response(201, text="unexpected"), httpx.Response(503)]:
            with self.client(lambda r: response):
                with self.assertRaises(IntegrationError) as caught:
                    await WazzupAdapter("test-only").send_message({})
                self.assertTrue(caught.exception.ambiguous)

    async def test_duplicate_id_alias_and_monotonic_delivery(self):
        with self.client(lambda r: httpx.Response(400, json={"error": "repeatedCrmMessageId"})):
            with self.assertRaises(IntegrationError) as caught:
                await WazzupAdapter("test-only").send_message({})
            self.assertEqual(caught.exception.code, "REPEATED_CRM_MESSAGE_ID")
        self.assertEqual(status_forward("read", "sent"), "read")
        self.assertEqual(status_forward("delivered", "error"), "delivered")


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated Postgres")
class OutboundAcceptance(unittest.TestCase):
    def setUp(self):
        self.raw, self.csrf, self.user_id, self.conversation_id = uid(), uid(), uid(), uid()
        with SessionLocal() as db:
            db.add(
                User(
                    id=self.user_id,
                    email=uid() + "@example.invalid",
                    password_hash=hash_password(uid()),
                    must_change_password=False,
                )
            )
            db.flush()
            db.add(
                LoginSession(
                    token_hash=token_hash(self.raw),
                    user_id=self.user_id,
                    csrf_token=self.csrf,
                    expires_at=now() + timedelta(hours=1),
                )
            )
            db.add(
                Conversation(
                    id=self.conversation_id,
                    channel_id=uid(),
                    chat_id="12025550123",
                    phone="+12025550123",
                )
            )
            put_setting(db, "wazzup.api_key", "TEST-ONLY-NOT-REAL", True, self.user_id)
            db.commit()
        invalidate_settings()
        self.client = TestClient(
            app,
            base_url=config().public_url,
            headers={"Origin": config().public_url, "X-CSRF-Token": self.csrf},
        )
        self.client.cookies.set(COOKIE, self.raw)
        self.context = {"redis": AsyncMock()}
        self.context["redis"].set.return_value = True
        self.temp = tempfile.TemporaryDirectory()
        self.storage_patch = patch("app.services.storage.LOCAL_ROOT", Path(self.temp.name))
        self.storage_patch.start()

    def tearDown(self):
        self.client.close()
        self.storage_patch.stop()
        self.temp.cleanup()
        with SessionLocal() as db:
            for m in db.scalars(
                select(Message).where(
                    Message.author_id == self.user_id,
                    Message.status.in_(["queued", "retry", "sending"]),
                )
            ):
                m.status = "error"
            db.commit()

    def queue(self, **changes):
        body = {"client_id": uid(), "text": "Test reply"}
        body.update(changes)
        response = self.client.post(
            f"/api/conversations/{self.conversation_id}/messages", json=body
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), body

    def deliver(self, message_id, result=None, error=None):
        with (
            patch.object(
                WazzupAdapter,
                "send_message",
                AsyncMock(return_value=result or {"messageId": uid()}, side_effect=error),
            ) as sender,
            patch("app.services.outbox.asyncio.sleep", AsyncMock()),
        ):
            asyncio.run(deliver_message(self.context, message_id))
            return sender

    def test_duplicate_http_retry_and_changed_request(self):
        msg, body = self.queue()
        repeated = self.client.post(
            f"/api/conversations/{self.conversation_id}/messages", json=body
        )
        self.assertEqual(repeated.json()["id"], msg["id"])
        body["text"] = "Different"
        self.assertEqual(
            self.client.post(
                f"/api/conversations/{self.conversation_id}/messages", json=body
            ).status_code,
            409,
        )
        with SessionLocal() as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == self.conversation_id)
                ),
                1,
            )

    def test_file_caption_are_separate_and_note_never_leaves_crm(self):
        response = self.client.post(
            "/api/uploads", files={"file": ("test.txt", b"File contents", "text/plain")}
        )
        self.assertEqual(response.status_code, 201, response.text)
        msg, _ = self.queue(attachment_id=response.json()["id"], text="Caption")
        sender = self.deliver(msg["id"])
        payload = sender.call_args.args[0]
        self.assertIn("contentUri", payload)
        self.assertNotIn("text", payload)
        with SessionLocal() as db:
            caption = db.scalar(
                select(Message).where(
                    Message.conversation_id == self.conversation_id, Message.body == "Caption"
                )
            )
            self.assertIsNotNone(caption)
            self.assertEqual(caption.status, "queued")
        note, _ = self.queue(text="Internal only", is_private_note=True)
        sender = self.deliver(note["id"])
        sender.assert_not_called()

    def test_staff_cannot_send_upload_or_create_and_private_quote_rejected(self):
        note, _ = self.queue(text="Secret note", is_private_note=True)
        r = self.client.post(
            f"/api/conversations/{self.conversation_id}/messages",
            json={"client_id": uid(), "text": "Reply", "quoted_message_id": note["id"]},
        )
        self.assertEqual(r.status_code, 400)
        with SessionLocal() as db:
            db.get(User, self.user_id).role = "staff"
            db.commit()
        self.assertEqual(
            self.client.post(
                f"/api/conversations/{self.conversation_id}/messages",
                json={"client_id": uid(), "text": "No"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/uploads", files={"file": ("x.txt", b"x")}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/api/conversations",
                json={"client_id": uid(), "phone": "+12025550124", "text": "No"},
            ).status_code,
            403,
        )

    def test_status_before_response_and_duplicate_echo(self):
        msg, _ = self.queue()
        external = uid()
        with SessionLocal() as db:
            original = db.get(Message, msg["id"])
            db.add(DeliveryReceipt(external_id=external, status="read"))
            db.add(
                Message(
                    conversation_id=self.conversation_id,
                    channel_id=original.channel_id,
                    external_id=external,
                    direction="out",
                    author_type="employee_phone",
                    body="Test reply",
                    status="delivered",
                )
            )
            db.commit()
        self.deliver(msg["id"], {"messageId": external})
        with SessionLocal() as db:
            row = db.get(Message, msg["id"])
            self.assertEqual(row.status, "read")
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == self.conversation_id)
                ),
                1,
            )

    def test_network_retry_stays_inside_window_then_requires_confirmation(self):
        msg, _ = self.queue()
        self.deliver(msg["id"], error=IntegrationError("connection_failed", ambiguous=True))
        with SessionLocal() as db:
            row = db.get(Message, msg["id"])
            self.assertEqual(row.status, "retry")
            row.send_started_at = now() - timedelta(seconds=80)
            row.next_attempt_at = now() - timedelta(seconds=1)
            db.commit()
        sender = self.deliver(msg["id"])
        sender.assert_not_called()
        r = self.client.post("/api/messages/" + msg["id"] + "/retry", json={})
        self.assertEqual(r.status_code, 409)
        r = self.client.post(
            "/api/messages/" + msg["id"] + "/retry", json={"confirm_duplicate_risk": True}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "queued")

    def test_bad_contact_remains_visible_with_text_and_no_auto_retry(self):
        msg, _ = self.queue()
        self.deliver(msg["id"], error=IntegrationError("BAD_CONTACT"))
        rows = self.client.get(f"/api/conversations/{self.conversation_id}/messages").json()
        self.assertEqual(rows[-1]["text"], "Test reply")
        self.assertEqual(rows[-1]["status"], "error")
        self.assertIn("WhatsApp", rows[-1]["error_text"])
        self.deliver(msg["id"]).assert_not_called()

    def test_new_number_idempotence_limit_and_phone_normalization(self):
        channel_id = uid()
        available = [
            {"channelId": channel_id, "name": "Test", "transport": "whatsapp", "state": "active"}
        ]
        body = {
            "client_id": uid(),
            "phone": "+1 (202) 555-0199",
            "text": "Hello",
            "channel_id": channel_id,
        }
        with patch.object(WazzupAdapter, "list_channels", AsyncMock(return_value=available)):
            r = self.client.post("/api/conversations", json=body)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertEqual(r.json()["phone"], "+12025550199")
            again = self.client.post("/api/conversations", json=body)
            self.assertEqual(again.json()["id"], r.json()["id"])
            with SessionLocal() as db:
                put_setting(db, "inbox.first_messages_per_hour", 1, False, self.user_id)
                db.commit()
            invalidate_settings()
            body.update(client_id=uid(), phone="+12025550198")
            self.assertEqual(self.client.post("/api/conversations", json=body).status_code, 429)
        with SessionLocal() as db:
            put_setting(db, "inbox.first_messages_per_hour", 20, False, self.user_id)
            db.commit()
        invalidate_settings()

    def test_inbound_dedup_reopens_closed_dialog_and_increments_once(self):
        with SessionLocal() as db:
            conversation = db.get(Conversation, self.conversation_id)
            conversation.state = "closed"
            payload = {
                "messages": [
                    {
                        "messageId": uid(),
                        "channelId": conversation.channel_id,
                        "chatId": conversation.chat_id,
                        "status": "inbound",
                        "type": "text",
                        "text": "Incoming test",
                    }
                ]
            }
            events = [WebhookEvent(payload=payload), WebhookEvent(payload=payload)]
            db.add_all(events)
            db.commit()
            ids = [e.id for e in events]
        for event_id in ids:
            asyncio.run(process_webhook(self.context, event_id))
        with SessionLocal() as db:
            conversation = db.get(Conversation, self.conversation_id)
            self.assertEqual(conversation.state, "waiting_us")
            self.assertEqual(conversation.unread_count, 1)
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.conversation_id == conversation.id)
                ),
                1,
            )

    def test_restart_recovers_queue_and_never_resends_stale_inflight(self):
        first, _ = self.queue()
        second, _ = self.queue(text="Second in channel")
        with SessionLocal() as db:
            channel = db.get(Conversation, self.conversation_id).channel_id
            stale = Message(
                conversation_id=self.conversation_id,
                channel_id=channel,
                external_id="local:" + uid(),
                direction="out",
                author_type="user",
                author_id=self.user_id,
                body="Interrupted",
                status="sending",
                send_started_at=now() - timedelta(minutes=3),
                next_attempt_at=now() - timedelta(seconds=1),
            )
            event = WebhookEvent(payload={})
            db.add_all([stale, event])
            db.commit()
            stale_id, event_id = stale.id, event.id
        with patch(
            "app.workers.worker.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(recover_pending(self.context))
        queued = [call.args for call in self.context["redis"].enqueue_job.call_args_list]
        self.assertIn(("deliver_message", first["id"]), queued)
        self.assertNotIn(("deliver_message", second["id"]), queued)
        self.assertLess(
            queued.index(("process_webhook", event_id)),
            queued.index(("deliver_message", first["id"])),
        )
        with SessionLocal() as db:
            self.assertEqual(db.get(Message, stale_id).status, "uncertain")
            db.get(WebhookEvent, event_id).state = "processed"
            db.commit()

    def test_search_pagination_and_order_filters(self):
        self.queue(text="UniqueSearchToken Желаем проверить")
        for q in ["UniqueSearchToken", "+1 (202) 555-0123"]:
            response = self.client.get("/api/conversations", params={"q": q})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(self.conversation_id, [c["id"] for c in response.json()])
        response = self.client.patch(
            "/api/conversations/" + self.conversation_id, json={"order_numbers": ["ORDER-TEST-42"]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get("/api/conversations", params={"q": "ORDER-TEST-42"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(self.conversation_id, [c["id"] for c in response.json()])
        with SessionLocal() as db:
            channel = db.get(Conversation, self.conversation_id).channel_id
            for i in range(55):
                db.add(
                    Message(
                        conversation_id=self.conversation_id,
                        channel_id=channel,
                        external_id=uid(),
                        direction="in",
                        author_type="customer",
                        body=str(i),
                        created_at=now() - timedelta(minutes=60 - i),
                    )
                )
            db.commit()
        first = self.client.get("/api/conversations/" + self.conversation_id + "/messages").json()
        older = self.client.get(
            "/api/conversations/" + self.conversation_id + "/messages",
            params={"before": first[0]["id"]},
        ).json()
        self.assertEqual(len(first), 50)
        self.assertEqual(len(older), 6)
        self.assertFalse(set(m["id"] for m in first) & set(m["id"] for m in older))

    def test_upload_limits_and_signed_download(self):
        response = self.client.post(
            "/api/uploads", files={"file": ("safe.html", b"<script>alert(1)</script>", "text/html")}
        )
        self.assertEqual(response.status_code, 201, response.text)
        msg, _ = self.queue(attachment_id=response.json()["id"], text="")
        download = self.client.get(msg["attachment_url"])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["content-type"], "application/octet-stream")
        self.assertIn("attachment;", download.headers["content-disposition"])
        self.assertEqual(self.client.get(msg["attachment_url"] + "bad").status_code, 404)
        big = self.client.post(
            "/api/uploads", files={"file": ("big.bin", b"x" * (10 * 1024 * 1024 + 1))}
        )
        self.assertEqual(big.status_code, 400)
