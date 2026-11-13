"""Synthetic acceptance; no requests to live Wazzup/OpenAI or customer messages."""

import asyncio
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import token_hash
from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter
from app.integrations.openai import AIError
from app.main import app
from app.models import (
    Conversation,
    Customer,
    LoginSession,
    Message,
    MessageTranslation,
    User,
    WebhookEvent,
    now,
    uid,
)
from app.services.message_actions import process_action
from app.services.outbox import deliver_message
from app.services.translations import translate_one
from app.workers.worker import process_webhook


class ChangeContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_methods_body_safe_errors_and_ambiguous_timeout(self):
        original = httpx.AsyncClient
        requests = []

        def handler(r):
            requests.append((r.method, r.url.path, r.content))
            return httpx.Response(204)

        with patch(
            "app.integrations.channels.wazzup.httpx.AsyncClient",
            side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(handler)),
        ):
            adapter = WazzupAdapter("synthetic")
            await adapter.change_message("message-id", text="Updated")
            await adapter.change_message("message-id")
        self.assertEqual([r[0] for r in requests], ["PATCH", "DELETE"])
        self.assertEqual(json.loads(requests[0][2]), {"text": "Updated"})
        self.assertEqual(requests[0][1], "/v3/message/message-id")
        for response, code, ambiguous in [
            (
                httpx.Response(
                    400, json={"error": "MESSAGES_EDITING_TIME_EXPIRED", "description": "SECRET"}
                ),
                "MESSAGES_EDITING_TIME_EXPIRED",
                False,
            ),
            (httpx.Response(503), "http_503", True),
        ]:
            with patch(
                "app.integrations.channels.wazzup.httpx.AsyncClient",
                side_effect=lambda **kw: original(
                    **kw, transport=httpx.MockTransport(lambda r: response)
                ),
            ):
                with self.assertRaises(IntegrationError) as caught:
                    await adapter.change_message("m", text="x")
                self.assertEqual(
                    (caught.exception.code, caught.exception.ambiguous), (code, ambiguous)
                )


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated database")
class DialogWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.user, self.other, self.customer, self.cid, self.channel = [uid() for _ in range(5)]
        self.clients = []
        self.context = {"redis": AsyncMock()}
        with SessionLocal() as db:
            for ident in [self.user, self.other]:
                db.add(
                    User(
                        id=ident,
                        email=ident + "@example.invalid",
                        password_hash="unused",
                        role="manager",
                        must_change_password=False,
                    )
                )
            db.add(Customer(id=self.customer, name="Synthetic customer"))
            db.flush()
            db.add(
                Conversation(
                    id=self.cid,
                    customer_id=self.customer,
                    channel_id=self.channel,
                    chat_id="12025550123",
                    phone="+12025550123",
                )
            )
            db.commit()
        self.client, self.other_client = self.session(self.user), self.session(self.other)

    def tearDown(self):
        for client in self.clients:
            client.close()
        with SessionLocal() as db:
            db.execute(
                update(Customer)
                .where(Customer.id == self.customer)
                .values(translate_incoming=False, translate_outgoing=False)
            )
            db.execute(
                update(Message)
                .where(Message.conversation_id == self.cid, Message.status == "translating")
                .values(status="cancelled")
            )
            db.commit()

    def session(self, ident):
        raw, csrf = uid(), uid()
        with SessionLocal() as db:
            db.add(
                LoginSession(
                    token_hash=token_hash(raw),
                    user_id=ident,
                    csrf_token=csrf,
                    expires_at=now() + timedelta(hours=1),
                )
            )
            db.commit()
        client = TestClient(
            app,
            base_url=config().public_url,
            headers={"Origin": config().public_url, "X-CSRF-Token": csrf},
        )
        client.cookies.set(COOKIE, raw)
        self.clients.append(client)
        return client

    def incoming(self, text="Question"):
        with SessionLocal() as db:
            event = WebhookEvent(
                payload={
                    "messages": [
                        {
                            "messageId": uid(),
                            "channelId": self.channel,
                            "chatId": "12025550123",
                            "status": "inbound",
                            "type": "text",
                            "text": text,
                        }
                    ]
                }
            )
            db.add(event)
            db.commit()
            ident = event.id
        asyncio.run(process_webhook(self.context, ident))
        return self.rows()[-1]

    def rows(self, client=None):
        return (client or self.client).get(f"/api/conversations/{self.cid}/messages").json()

    def conversation(self):
        return self.client.get("/api/conversations/" + self.cid).json()

    def send(self, text="Answer", **kw):
        result = self.client.post(
            f"/api/conversations/{self.cid}/messages", json={"client_id": uid(), "text": text, **kw}
        )
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()

    def action(self, row, action, client=None, **kw):
        return (client or self.client).post(
            "/api/messages/" + row["id"] + "/actions",
            json={"client_id": uid(), "version": row["version"], "action": action, **kw},
        )

    def test_viewing_and_notes_do_not_read_and_reply_does_not_hide_new_incoming(self):
        self.incoming()
        self.client.post(f"/api/conversations/{self.cid}/read")
        self.assertEqual(self.conversation()["unread_count"], 1)
        note = self.send("Internal", is_private_note=True)
        self.assertEqual(self.conversation()["unread_count"], 1)
        self.assertEqual(note["author_name"], self.user + "@example.invalid")
        msg = self.send()
        self.assertEqual(self.conversation()["unread_count"], 1)
        self.incoming("New question during send")
        with patch.object(
            WazzupAdapter, "send_message", AsyncMock(return_value={"messageId": uid()})
        ):
            asyncio.run(deliver_message(self.context, msg["id"]))
        self.assertEqual(self.conversation()["unread_count"], 1)
        self.assertEqual(self.conversation()["state"], "waiting_us")
        final = self.send("Second answer")
        with patch.object(
            WazzupAdapter, "send_message", AsyncMock(side_effect=IntegrationError("BAD_CONTACT"))
        ):
            asyncio.run(deliver_message(self.context, final["id"]))
        self.assertEqual(self.conversation()["unread_count"], 1)

    def test_manual_buttons_are_versioned_and_apply_to_whole_team(self):
        self.incoming()
        body = {"required": False, "inbound_seq": self.conversation()["inbound_seq"]}
        self.incoming("Another")
        path = f"/api/conversations/{self.cid}/response-state"
        self.assertEqual(self.client.post(path, json=body).status_code, 409)
        body["inbound_seq"] = self.conversation()["inbound_seq"]
        self.assertEqual(self.client.post(path, json=body).status_code, 200)
        self.assertEqual(
            self.other_client.get("/api/conversations/" + self.cid).json()["unread_count"], 0
        )
        body["required"] = True
        self.assertEqual(self.client.post(path, json=body).json()["unread_count"], 1)

    def test_old_delivery_receipt_cannot_override_manually_required_reply(self):
        self.incoming()
        reply = self.send()
        external = uid()
        with patch.object(
            WazzupAdapter, "send_message", AsyncMock(return_value={"messageId": external})
        ):
            asyncio.run(deliver_message(self.context, reply["id"]))
        c = self.conversation()
        self.assertEqual(c["unread_count"], 0)
        self.client.post(
            f"/api/conversations/{self.cid}/response-state",
            json={"required": True, "inbound_seq": c["inbound_seq"]},
        )
        with SessionLocal() as db:
            event = WebhookEvent(
                payload={
                    "statuses": [
                        {"messageId": external, "channelId": self.channel, "status": "read"}
                    ]
                }
            )
            db.add(event)
            db.commit()
            eid = event.id
        asyncio.run(process_webhook(self.context, eid))
        self.assertEqual(self.conversation()["unread_count"], 1)

    def test_notes_edit_delete_permissions_and_per_user_hide(self):
        row = self.send("Note", is_private_note=True)
        self.assertEqual(self.action(row, "edit", self.other_client, text="No").status_code, 403)
        edited = self.action(row, "edit", text="Corrected")
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.action(row, "edit", text="Stale").status_code, 409)
        updated = self.rows()[-1]
        self.assertEqual(updated["text"], "Corrected")
        self.assertEqual(updated["history"][0]["body"], "Note")
        self.assertEqual(self.action(updated, "hide").status_code, 200)
        self.assertNotIn(row["id"], [m["id"] for m in self.rows()])
        self.assertIn(row["id"], [m["id"] for m in self.rows(self.other_client)])
        self.assertEqual(self.action(updated, "delete").status_code, 200)
        deleted = self.rows(self.other_client)[-1]
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["text"], "")
        self.assertEqual(deleted["history"], [])

    def test_external_actions_only_confirm_after_provider_success(self):
        row = self.send("Original")
        with SessionLocal() as db:
            msg = db.get(Message, row["id"])
            msg.external_id = uid()
            msg.status = "sent"
            msg.sent_at = now()
            db.commit()
        row = self.rows()[-1]
        result = self.action(row, "edit", text="Updated")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.rows()[-1]["text"], "Original")
        with patch.object(
            WazzupAdapter,
            "change_message",
            AsyncMock(side_effect=IntegrationError("change_unknown", ambiguous=True)),
        ):
            asyncio.run(process_action(self.context, result.json()["id"]))
        self.assertEqual(self.rows()[-1]["text"], "Original")
        self.assertEqual(self.rows()[-1]["action"]["state"], "uncertain")
        self.assertEqual(self.action(row, "delete").status_code, 409)
        self.client.post(
            "/api/message-actions/" + result.json()["id"] + "/retry", json={"confirm": True}
        )
        with patch.object(WazzupAdapter, "change_message", AsyncMock()) as provider:
            asyncio.run(process_action(self.context, result.json()["id"]))
            provider.assert_awaited_once()
        row = self.rows()[-1]
        self.assertEqual(row["text"], "Updated")
        deleted = self.action(row, "delete")
        with patch.object(WazzupAdapter, "change_message", AsyncMock()):
            asyncio.run(process_action(self.context, deleted.json()["id"]))
        self.assertTrue(self.rows()[-1]["deleted"])

    def test_sort_uses_last_external_message_not_waiting_state_or_notes(self):
        ids = [uid(), uid()]
        with SessionLocal() as db:
            for i, cid in enumerate(ids):
                db.add(
                    Conversation(
                        id=cid,
                        channel_id=uid(),
                        chat_id=uid(),
                        state="waiting_us" if i == 0 else "waiting_customer",
                    )
                )
                db.flush()
                db.add(
                    Message(
                        conversation_id=cid,
                        channel_id=uid(),
                        external_id=uid(),
                        direction="in" if i == 0 else "out",
                        author_type="customer" if i == 0 else "user",
                        body="Visible",
                        created_at=now() - timedelta(minutes=20 - i * 10),
                    )
                )
            db.add(
                Message(
                    conversation_id=ids[0],
                    channel_id=uid(),
                    external_id=uid(),
                    direction="out",
                    author_type="user",
                    is_private_note=True,
                    body="New note",
                    created_at=now(),
                )
            )
            db.commit()
        rows = self.client.get("/api/conversations").json()
        found = [r["id"] for r in rows if r["id"] in ids]
        self.assertEqual(found, [ids[1], ids[0]])
        self.assertEqual(next(r for r in rows if r["id"] == ids[0])["last_message"], "Visible")

    def test_translation_preserves_original_blocks_delivery_and_caches_history(self):
        profile = self.client.get("/api/customers/" + self.customer).json()
        changed = self.client.patch(
            "/api/customers/" + self.customer,
            json={
                "version": profile["version"],
                "translate_incoming": True,
                "translate_outgoing": True,
                "translation_client_language": "en",
                "translation_manager_language": "ru",
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        incoming = self.incoming("Where is ORDER-42?")
        with patch(
            "app.services.translations.translate_text", AsyncMock(return_value="Где ORDER-42?")
        ) as provider:
            self.assertTrue(asyncio.run(translate_one()))
            provider.assert_awaited_once_with("Where is ORDER-42?", "en", "ru")
        row = self.rows()[-1]
        self.assertEqual(row["text"], incoming["text"])
        self.assertEqual(row["translation"]["text"], "Где ORDER-42?")
        outgoing = self.send("Заказ готов")
        self.assertEqual(outgoing["status"], "translating")
        with patch.object(WazzupAdapter, "send_message", AsyncMock()) as send:
            asyncio.run(deliver_message(self.context, outgoing["id"]))
            send.assert_not_awaited()
        with patch(
            "app.services.translations.translate_text",
            AsyncMock(side_effect=AIError("provider_unavailable")),
        ):
            asyncio.run(translate_one())
        self.assertEqual(self.rows()[-1]["status"], "translating")
        self.client.post("/api/messages/" + outgoing["id"] + "/translation/retry")
        with patch(
            "app.services.translations.translate_text", AsyncMock(return_value="Order is ready")
        ):
            asyncio.run(translate_one())
        with patch.object(
            WazzupAdapter, "send_message", AsyncMock(return_value={"messageId": uid()})
        ) as send:
            asyncio.run(deliver_message(self.context, outgoing["id"]))
            self.assertEqual(send.call_args.args[0]["text"], "Order is ready")
        row = self.rows()[-1]
        self.assertEqual(row["text"], "Заказ готов")
        self.assertEqual(row["delivery_text"], "Order is ready")
        self.assertEqual(self.conversation()["unread_count"], 0)
        with SessionLocal() as db:
            self.assertEqual(
                len(
                    db.scalars(
                        select(MessageTranslation).where(
                            MessageTranslation.message_id == outgoing["id"]
                        )
                    ).all()
                ),
                1,
            )

    def test_translated_edit_echo_preserves_manager_original_and_versions(self):
        with SessionLocal() as db:
            db.get(Customer, self.customer).translate_outgoing = True
            db.commit()
        row = self.send("Заказ готов")
        with patch(
            "app.services.translations.translate_text", AsyncMock(return_value="Order ready")
        ):
            asyncio.run(translate_one())
        external = uid()
        with patch.object(
            WazzupAdapter, "send_message", AsyncMock(return_value={"messageId": external})
        ):
            asyncio.run(deliver_message(self.context, row["id"]))
        row = self.rows()[-1]
        action = self.action(row, "edit", text="Заказ отправлен").json()
        with (
            patch(
                "app.services.translations.translate_text", AsyncMock(return_value="Order shipped")
            ),
            patch.object(WazzupAdapter, "change_message", AsyncMock()) as provider,
        ):
            asyncio.run(process_action(self.context, action["id"]))
            provider.assert_awaited_once_with(external, text="Order shipped")
        with SessionLocal() as db:
            event = WebhookEvent(
                payload={
                    "messages": [
                        {
                            "messageId": external,
                            "channelId": self.channel,
                            "chatId": "12025550123",
                            "status": "sent",
                            "isEdited": True,
                            "text": "Order shipped",
                        }
                    ]
                }
            )
            db.add(event)
            db.commit()
            eid = event.id
        asyncio.run(process_webhook(self.context, eid))
        updated = self.rows()[-1]
        self.assertEqual(updated["text"], "Заказ отправлен")
        self.assertEqual(updated["translation"]["text"], "Order shipped")
        self.assertEqual(updated["history"][0]["body"], "Заказ готов")
        with SessionLocal() as db:
            self.assertEqual(
                len(
                    db.scalars(
                        select(MessageTranslation).where(MessageTranslation.message_id == row["id"])
                    ).all()
                ),
                2,
            )

    def test_quote_uses_provider_id_and_unsent_translation_can_be_cancelled(self):
        incoming = self.incoming()
        reply = self.send("Quoted reply", quoted_message_id=incoming["id"])
        with SessionLocal() as db:
            external = db.get(Message, incoming["id"]).external_id
        with patch.object(
            WazzupAdapter, "send_message", AsyncMock(return_value={"messageId": uid()})
        ) as provider:
            asyncio.run(deliver_message(self.context, reply["id"]))
            self.assertEqual(provider.call_args.args[0]["refMessageId"], external)
        with SessionLocal() as db:
            db.get(Customer, self.customer).translate_outgoing = True
            db.commit()
        pending = self.send("Cancel this")
        self.assertEqual(self.action(pending, "delete").status_code, 200)
        with patch(
            "app.services.translations.translate_text",
            AsyncMock(return_value="Historical translation"),
        ) as translate:
            asyncio.run(translate_one())
            self.assertNotIn("Cancel this", [call.args[0] for call in translate.await_args_list])
        self.assertEqual(self.rows()[-1]["status"], "cancelled")
