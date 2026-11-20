"""Synthetic bilingual delivery and identity tests. All external providers are mocked."""

import asyncio
import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import token_hash
from app.core.settings import invalidate_settings, put_setting
from app.integrations.onec import exact_phone
from app.integrations.openai import AIError
from app.main import app
from app.models import (
    AIDraft,
    ApprovedExample,
    Conversation,
    Customer,
    LoginSession,
    Message,
    MessageTranslation,
    Notification,
    User,
    now,
    uid,
)
from app.services.ai import context_snapshot, source_hash
from app.services.ai_config import AIConfig
from app.services.dialog_languages import (
    client_language,
    incoming_enabled,
    neutral_greeting,
    outgoing_language,
)
from app.services.onec_customers import refresh_customer
from app.services.translations import translate_one


class LanguageRules(unittest.TestCase):
    def test_manual_client_choice_survives_detection_and_personal_language(self):
        c = SimpleNamespace(
            translation_client_language="es",
            language="ar",
            incoming_translation_auto=True,
            translate_incoming=False,
            translate_outgoing=True,
        )
        self.assertEqual(client_language(c), "es")
        self.assertTrue(incoming_enabled(c, "ru"))
        self.assertFalse(incoming_enabled(c, "es"))
        self.assertEqual(outgoing_language(c, "ru"), "es")
        self.assertEqual(outgoing_language(c, "ru", "original"), "ru")
        c.translation_client_language = "auto"
        self.assertEqual(client_language(c), "ar")

    def test_name_guard_keeps_card_name_and_removes_foreign_greeting(self):
        for value, name in [
            ("Hello Majed! Your order is ready.", "Majed"),
            ("مرحباً ماجد، طلبك جاهز", "ماجد"),
            ("Hola Carlos, pedido listo.", "Carlos"),
        ]:
            changed, corrected = neutral_greeting(value, "Jose Barrientos", name)
            self.assertTrue(corrected)
            self.assertNotIn(name, changed)
        self.assertEqual(
            neutral_greeting("Hola Jose, pedido listo.", "Jose Barrientos"),
            ("Hola Jose, pedido listo.", False),
        )
        text = "Hello, how can I help?"
        self.assertEqual(neutral_greeting(text, "Jose Barrientos"), (text, False))

    def test_full_phone_only_no_suffix_or_country_guess(self):
        self.assertEqual(exact_phone("+1 (202) 555-0123"), "+12025550123")
        for value in ["550123", "90706160", "12025550123@g.us", "+1 202 555 0123 ext 4"]:
            self.assertEqual(exact_phone(value), "")
        self.assertNotEqual(exact_phone("+12025550123"), exact_phone("+442079550123"))


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated database")
class BilingualAcceptance(unittest.TestCase):
    def setUp(self):
        self.user, self.other, self.customer, self.cid, self.inbound, self.did = [
            uid() for _ in range(6)
        ]
        self.clients = []
        with SessionLocal() as db:
            db.add_all(
                [
                    User(
                        id=u,
                        email=u + "@example.invalid",
                        password_hash="unused",
                        role="manager",
                        must_change_password=False,
                        manager_language=lang,
                    )
                    for u, lang in [(self.user, "ru"), (self.other, "en")]
                ]
            )
            db.add(
                Customer(id=self.customer, name="Synthetic", language="ar", translate_outgoing=True)
            )
            db.flush()
            db.add(
                Conversation(
                    id=self.cid,
                    customer_id=self.customer,
                    channel_id=uid(),
                    chat_id="12025550123",
                    phone="+12025550123",
                    name="Card name",
                )
            )
            db.flush()
            m = Message(
                id=self.inbound,
                conversation_id=self.cid,
                channel_id=uid(),
                external_id=uid(),
                direction="in",
                author_type="client",
                body="مرحبا",
                created_at=now() - timedelta(minutes=1),
            )
            db.add(m)
            db.flush()
            db.add(
                AIDraft(
                    id=self.did,
                    conversation_id=self.cid,
                    inbound_id=self.inbound,
                    source_hash=source_hash(m),
                    state="ready",
                    decision="pending",
                    body="طلبك جاهز",
                    language="ar",
                    manager_language="ru",
                    manager_versions={"ru": "Ваш заказ готов", "en": "Your order is ready"},
                )
            )
            db.commit()
        self.client = self.session(self.user)
        self.other_client = self.session(self.other)

    def tearDown(self):
        for c in self.clients:
            c.close()
        with SessionLocal() as db:
            db.execute(
                update(MessageTranslation)
                .where(
                    MessageTranslation.message_id.in_(
                        select(Message.id).where(Message.conversation_id == self.cid)
                    )
                )
                .values(state="ready")
            )
            db.commit()

    def session(self, user):
        raw, csrf = uid(), uid()
        with SessionLocal() as db:
            db.add(
                LoginSession(
                    token_hash=token_hash(raw),
                    user_id=user,
                    csrf_token=csrf,
                    expires_at=now() + timedelta(hours=1),
                )
            )
            db.commit()
        c = TestClient(
            app,
            base_url=config().public_url,
            headers={"Origin": config().public_url, "X-CSRF-Token": csrf},
        )
        c.cookies.set(COOKIE, raw)
        self.clients.append(c)
        return c

    def test_ready_ai_sends_client_version_without_second_translation(self):
        with patch(
            "app.services.translations.translate_text",
            AsyncMock(side_effect=AssertionError("must reuse ready version")),
        ):
            body = {"source_language": "ru", "target_language": "ar"}
            r = self.client.post(f"/api/drafts/{self.did}/send", json=body)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["delivery_text"], "طلبك جاهز")
            self.assertEqual(r.json()["text"], "Ваш заказ готов")
            again = self.client.post(f"/api/drafts/{self.did}/send", json=body)
            self.assertEqual(again.json()["id"], r.json()["id"])
            self.assertEqual(
                self.other_client.post(f"/api/drafts/{self.did}/send", json=body).status_code, 409
            )
        with SessionLocal() as db:
            ex = db.scalar(select(ApprovedExample).where(ApprovedExample.draft_id == self.did))
            self.assertEqual(
                (ex.reply, ex.manager_reply, ex.language, ex.manager_language),
                ("طلبك جاهز", "Ваш заказ готов", "ar", "ru"),
            )

    def test_summary_language_is_personal_and_survives_a_new_session(self):
        path = "/api/me/summary-language"
        self.assertIsNone(self.client.get(path).json()["language"])
        for client, language in [(self.client, "de"), (self.other_client, "ko"), (self.client, "fr")]:
            saved = client.put(path, json={"language": language})
            self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.put(path, json={"language": "invalid"}).status_code, 422)
        self.assertEqual(self.session(self.user).get(path).json()["language"], "fr")
        self.assertEqual(self.other_client.get(path).json()["language"], "ko")
        self.assertEqual(self.client.get("/api/me/language").json()["language"], "ru")

    def test_edited_ai_translates_final_manager_text_once_and_preserves_both(self):
        with patch(
            "app.services.translations.translate_text", AsyncMock(return_value="Pedido enviado")
        ) as tr:
            body = {
                "final_text": "Заказ отправлен",
                "source_language": "ru",
                "target_language": "es",
            }
            r = self.client.post(f"/api/drafts/{self.did}/send-edited", json=body)
            self.assertEqual(r.status_code, 200, r.text)
            tr.assert_awaited_once_with("Заказ отправлен", "ru", "es")
            self.assertEqual(r.json()["delivery_text"], "Pedido enviado")
            self.client.post(f"/api/drafts/{self.did}/send-edited", json=body)
            self.assertEqual(tr.await_count, 1)

    def test_translation_failure_creates_no_message_then_same_key_retries(self):
        ident = uid()
        body = {
            "client_id": ident,
            "text": "Заказ готов",
            "source_language": "ru",
            "target_language": "ar",
        }
        with patch(
            "app.services.translations.translate_text",
            AsyncMock(side_effect=AIError("provider_unavailable")),
        ):
            r = self.client.post(f"/api/conversations/{self.cid}/messages", json=body)
            self.assertEqual(r.status_code, 502, r.text)
        with SessionLocal() as db:
            self.assertIsNone(db.get(Message, ident))
        with patch(
            "app.services.translations.translate_text", AsyncMock(return_value="طلبك جاهز")
        ) as tr:
            r = self.client.post(f"/api/conversations/{self.cid}/messages", json=body)
            self.assertEqual(r.status_code, 201, r.text)
            self.client.post(f"/api/conversations/{self.cid}/messages", json=body)
            tr.assert_awaited_once()
        body["text"] = "Changed"
        self.assertEqual(
            self.client.post(f"/api/conversations/{self.cid}/messages", json=body).status_code, 409
        )

    def test_personal_language_and_viewer_translation_do_not_change_delivery(self):
        self.assertEqual(self.client.get("/api/me/language").json()["language"], "ru")
        self.assertEqual(self.other_client.get("/api/me/language").json()["language"], "en")
        self.client.put("/api/me/language", json={"language": "es"})
        self.assertEqual(self.other_client.get("/api/me/language").json()["language"], "en")
        r = self.client.get(f"/api/conversations/{self.cid}/messages").json()[0]
        self.assertEqual(r["reading_translation"]["target"], "es")
        with patch("app.services.translations.translate_text", AsyncMock(return_value="Hola")):
            asyncio.run(translate_one())
        r = self.client.get(f"/api/conversations/{self.cid}/messages").json()[0]
        self.assertEqual(r["reading_translation"]["text"], "Hola")
        self.assertEqual(r["text"], "مرحبا")
        self.assertIsNone(r["delivery_text"])
        self.assertEqual(
            self.other_client.get(f"/api/conversations/{self.cid}/messages").json()[0][
                "reading_translation"
            ]["target"],
            "en",
        )

    def test_exhausted_legacy_translation_does_not_block_the_next_message(self):
        first, second = uid(), uid()
        with SessionLocal() as db:
            for ident, value in [(first, "First"), (second, "Second")]:
                db.add(
                    Message(
                        id=ident,
                        conversation_id=self.cid,
                        channel_id=uid(),
                        external_id="local:" + ident,
                        direction="out",
                        author_type="user",
                        author_id=self.user,
                        body=value,
                        status="translating",
                        delivery_source_language="ru",
                        delivery_target_language="es",
                    )
                )
            db.flush()
            db.add(
                MessageTranslation(
                    message_id=first,
                    revision=1,
                    original="First",
                    source_language="ru",
                    target_language="es",
                    state="error",
                    attempts=3,
                    next_attempt_at=now(),
                )
            )
            db.commit()
        with patch(
            "app.services.translations.translate_text", AsyncMock(return_value="Segundo")
        ) as provider:
            self.assertTrue(asyncio.run(translate_one()))
            provider.assert_awaited_once_with("Second", "ru", "es")
        with SessionLocal() as db:
            self.assertEqual(db.get(Message, first).status, "translating")
            self.assertEqual(db.get(Message, second).status, "queued")
            self.assertEqual(db.get(Message, second).delivery_body, "Segundo")

    def test_suffix_candidate_is_not_identity_or_ai_order(self):
        with SessionLocal() as db:
            for key, value in [
                ("base_url", "http://onec.invalid/prod"),
                ("username", "test"),
                ("password", "synthetic"),
            ]:
                put_setting(db, "onec." + key, value, key == "password", self.user)
            db.commit()
        invalidate_settings()
        candidate = {
            "name": "Wrong customer",
            "country": "Honduras",
            "realizations": ["OTHER-ORDER"],
            "matched_phone": "+442079550123",
        }
        with patch(
            "app.services.onec_customers.OneCAdapter.customers", AsyncMock(return_value=[candidate])
        ):
            snap = asyncio.run(refresh_customer(self.cid))
        self.assertEqual(snap["clients"], [])
        self.assertEqual(snap["candidates"], [candidate])
        self.assertEqual(snap["phone_country"], "US")
        self.assertEqual(snap["status"], "unverified")
        c = self.client.get(f"/api/conversations/{self.cid}").json()
        self.assertNotEqual(c["display_name"], "Wrong customer")
        with SessionLocal() as db:
            context = context_snapshot(db, db.get(Conversation, self.cid), AIConfig())
            self.assertNotIn("OTHER-ORDER", context["orders"])

    def test_bell_excludes_old_inbound_and_read_all_is_scoped(self):
        with SessionLocal() as db:
            for u, kind in [
                (self.user, "inbox.message"),
                (self.user, "integration.alert"),
                (self.user, "task.assigned"),
                (self.other, "task.assigned"),
            ]:
                db.add(Notification(user_id=u, kind=kind, payload={}, dedupe_key=uid()))
            db.commit()
        notices = self.client.get("/api/notifications").json()
        self.assertEqual(notices["unread"], 1)
        self.assertEqual([n["kind"] for n in notices["items"]], ["task.assigned"])
        self.assertEqual(self.client.post("/api/notifications/read-all", json={}).status_code, 200)
        self.assertEqual(self.client.get("/api/notifications").json()["unread"], 0)
        self.assertEqual(self.other_client.get("/api/notifications").json()["unread"], 1)
