"""Synthetic acceptance of decisions, cancellation, provider isolation and notification delivery."""

import asyncio
import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import hash_password, token_hash
from app.core.settings import invalidate_settings, put_setting
from app.integrations.openai import AIError, OpenAIAdapter
from app.main import app
from app.models import (
    AIDraft,
    AIUsage,
    ApprovedExample,
    Conversation,
    Customer,
    LoginSession,
    Message,
    Notification,
    NotificationDelivery,
    Setting,
    User,
    WebhookEvent,
    now,
    uid,
)
from app.services.ai import (
    Classification,
    auto_allowed,
    index_example,
    policy_check,
    process_draft,
    queue_auto,
    queue_inbound,
)
from app.services.ai_config import AIConfig, config_version, quote_price, working_time
from app.services.notifications import DeliveryError, deliver_notification, prepare_deliveries
from app.services.outbox import deliver_message
from app.workers.worker import process_webhook


def output(value):
    import json

    return [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]


def classification(intent="shipping", **kwargs):
    return dict(
        intent=intent,
        language="en",
        tone="neutral",
        escalation=False,
        exact_date=False,
        bot_question=False,
        orders=[],
        parts=[],
        vins=[],
        cities=[],
        amounts=[],
        **kwargs,
    )


class PolicyTests(unittest.TestCase):
    def test_defaults_budget_schedule_and_price(self):
        cfg = AIConfig()
        self.assertTrue(all(v == ["draft", "draft"] for v in cfg.modes.values()))
        self.assertEqual(quote_price(cfg, 1250)["status"], "coefficient_not_configured")
        cfg.coefficient = 1.1
        self.assertEqual(quote_price(cfg, 1250)["usd"], "2")
        cfg.rounding = "half"
        self.assertEqual(quote_price(cfg, 1250)["usd"], "1.5")
        cfg.holidays = [
            now().astimezone(__import__("zoneinfo").ZoneInfo(cfg.timezone)).date().isoformat()
        ]
        self.assertFalse(working_time(cfg, now()))
        with self.assertRaises(ValidationError):
            AIConfig(modes={**cfg.modes, "payment": ["auto", "draft"]})
        with self.assertRaises(ValidationError):
            AIConfig(timezone="unknown/nope")

    def test_code_guard_does_not_trust_model_language_or_facts(self):
        cfg = AIConfig()
        c = Classification.model_validate(classification("order_status"))
        result = policy_check(
            cfg,
            c,
            {"text": "Your order is ready and will be delivered on 12/12/2026.", "language": "en"},
            [],
        )
        self.assertIn("no_source_facts", result["reasons"])
        self.assertIn("exact_date", result["reasons"])
        result = policy_check(
            cfg,
            c,
            {"text": "Ваш заказ гарантированно будет доставлен завтра утром.", "language": "en"},
            [],
        )
        self.assertIn("language", result["reasons"])

    def test_adapter_does_not_return_provider_error_details(self):
        import httpx

        records = []
        adapter = OpenAIAdapter("synthetic-key", lambda *args: records.append(args))
        response = httpx.Response(401, json={"error": {"message": "sensitive provider payload"}})
        with patch("httpx.AsyncClient.post", AsyncMock(return_value=response)):
            with self.assertRaisesRegex(AIError, "provider_http_401"):
                asyncio.run(adapter.request("responses", {"model": "fake"}, "test"))
        self.assertNotIn("sensitive", str(records))


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated database")
class AIAcceptance(unittest.TestCase):
    def setUp(self):
        self.user, self.staff, self.cid, self.mid = uid(), uid(), uid(), uid()
        self.cfg = AIConfig()
        self.cfg.policies["shipping"] = "Shipping terms are confirmed individually by the manager."
        with SessionLocal() as db:
            for ident, role in [(self.user, "admin"), (self.staff, "staff")]:
                db.add(
                    User(
                        id=ident,
                        email=ident + "@example.invalid",
                        role=role,
                        password_hash=hash_password(uid()),
                        must_change_password=False,
                    )
                )
            customer = Customer(name="Synthetic customer")
            db.add(customer)
            db.flush()
            c = Conversation(
                id=self.cid,
                customer_id=customer.id,
                channel_id=uid(),
                chat_id="12025550101",
                phone="+12025550101",
                assignee_id=self.user,
            )
            db.add(c)
            db.flush()
            m = Message(
                id=self.mid,
                conversation_id=c.id,
                channel_id=c.channel_id,
                external_id=uid(),
                direction="in",
                author_type="customer",
                body="Could you please explain your shipping process?",
                status="inbound",
            )
            db.add(m)
            db.flush()
            queue_inbound(db, c, m)
            db.flush()
            self.did = db.scalar(select(AIDraft.id).where(AIDraft.inbound_id == m.id))
            put_setting(db, "ai.config", self.cfg.model_dump(), False, self.user)
            put_setting(db, "openai.api_key", "synthetic-not-a-real-key", True, self.user)
            db.commit()
        invalidate_settings()
        self.client = self.session(self.user)
        self.staff_client = self.session(self.staff)

    def tearDown(self):
        self.client.close()
        self.staff_client.close()
        with SessionLocal() as db:
            for key in ("ai.config", "openai.api_key", "smtp.host", "smtp.from_email"):
                row = db.get(Setting, key)
                if row:
                    db.delete(row)
            db.commit()
        invalidate_settings()

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
        client = TestClient(
            app,
            base_url=config().public_url,
            headers={"Origin": config().public_url, "X-CSRF-Token": csrf},
        )
        client.cookies.set(COOKIE, raw)
        return client

    def configure(self, **fields):
        for key, value in fields.items():
            setattr(self.cfg, key, value)
        with SessionLocal() as db:
            put_setting(db, "ai.config", self.cfg.model_dump(), False, self.user)
            db.commit()
        invalidate_settings()

    def generate(self, intent="shipping", **kw):
        reply = "Our manager can explain the available shipping options and help with your delivery question."
        with patch.object(
            OpenAIAdapter,
            "response",
            AsyncMock(
                side_effect=[
                    output(classification(intent, **kw)),
                    output({"text": reply, "language": "en"}),
                ]
            ),
        ):
            asyncio.run(process_draft({}, self.did))
        with SessionLocal() as db:
            return db.get(AIDraft, self.did)

    def test_draft_manual_approval_idempotence_and_confirmed_examples(self):
        row = self.generate()
        self.assertEqual(row.state, "ready")
        self.assertIsNone(row.auto_at)
        final = "Thank you for your message. Our manager will help with your shipping question."
        response = self.client.post(
            "/api/drafts/" + self.did + "/send-edited", json={"final_text": final}
        )
        self.assertEqual(response.status_code, 200, response.text)
        repeated = self.client.post(
            "/api/drafts/" + self.did + "/send-edited", json={"final_text": final}
        )
        self.assertEqual(repeated.json()["id"], response.json()["id"])
        with SessionLocal() as db:
            examples = db.scalars(
                select(ApprovedExample).where(ApprovedExample.draft_id == self.did)
            ).all()
            self.assertEqual(len(examples), 1)
            example = examples[0].id
        with patch.object(
            OpenAIAdapter, "embedding", AsyncMock(return_value=[0.01] * 1536)
        ) as embed:
            asyncio.run(index_example({}, example))
            embed.assert_not_called()
            with SessionLocal() as db:
                db.get(Message, response.json()["id"]).status = "sent"
                db.commit()
            asyncio.run(index_example({}, example))
            self.assertEqual(embed.call_count, 1)
        with SessionLocal() as db:
            self.assertEqual(len(db.get(ApprovedExample, example).embedding), 1536)

    def test_escalation_and_no_stock_facts_never_auto(self):
        self.configure(modes={**self.cfg.modes, "order_status": ["auto", "auto"]})
        row = self.generate("order_status")
        self.assertIsNone(row.auto_at)
        self.assertIn("no_source_facts", row.policy["reasons"])
        with SessionLocal() as db:
            db.get(AIDraft, self.did).state = "queued"
            db.commit()
        row = self.generate("payment")
        self.assertEqual(row.state, "escalated")
        with SessionLocal() as db:
            self.assertTrue(db.get(Conversation, self.cid).needs_human)
            self.assertTrue(
                db.scalar(
                    select(Notification.id).where(
                        Notification.user_id == self.user, Notification.kind == "ai.escalation"
                    )
                )
            )

    def test_new_inbound_and_manual_phone_cancel_old_auto(self):
        self.generate()
        with SessionLocal() as db:
            c = db.get(Conversation, self.cid)
            d = db.get(AIDraft, self.did)
            d.mode = "auto"
            d.auto_at = now() - timedelta(seconds=1)
            self.cfg.modes["shipping"] = ["auto", "auto"]
            put_setting(db, "ai.config", self.cfg.model_dump(), False, self.user)
            d.policy = {**d.policy, "config_version": config_version(self.cfg), "passed": True}
            db.commit()
        invalidate_settings()
        queue_auto()
        with SessionLocal() as db:
            d = db.get(AIDraft, self.did)
            self.assertEqual(d.decision, "auto_sent")
            outgoing = d.message_id
            c = db.get(Conversation, self.cid)
            event = WebhookEvent(
                payload={
                    "messages": [
                        {
                            "channelId": c.channel_id,
                            "chatId": c.chat_id,
                            "messageId": uid(),
                            "chatType": "whatsapp",
                            "type": "text",
                            "text": "A second question",
                            "status": "inbound",
                        }
                    ]
                }
            )
            db.add(event)
            db.commit()
            eid = event.id
        asyncio.run(process_webhook({"redis": AsyncMock()}, eid))
        with patch("app.services.outbox.WazzupAdapter.send_message", AsyncMock()) as send:
            asyncio.run(deliver_message({"redis": AsyncMock()}, outgoing))
            send.assert_not_called()
        with SessionLocal() as db:
            self.assertEqual(db.get(Message, outgoing).status, "cancelled")
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(AIDraft)
                    .where(AIDraft.conversation_id == self.cid)
                ),
                2,
            )
        asyncio.run(process_webhook({"redis": AsyncMock()}, eid))
        with SessionLocal() as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(AIDraft)
                    .where(AIDraft.conversation_id == self.cid)
                ),
                2,
            )

    def test_stale_generation_cannot_be_sent(self):
        async def answer(*args, **kwargs):
            with SessionLocal() as db:
                c = db.get(Conversation, self.cid)
                m = Message(
                    conversation_id=c.id,
                    channel_id=c.channel_id,
                    external_id=uid(),
                    direction="in",
                    author_type="customer",
                    body="Newer request",
                    status="inbound",
                    created_at=now() + timedelta(seconds=1),
                )
                db.add(m)
                db.flush()
                queue_inbound(db, c, m)
                db.commit()
            return output(classification())

        responses = [0]

        async def race(*args, **kwargs):
            responses[0] += 1
            if responses[0] == 1:
                return await answer()
            return output(
                {"text": "We will check your question with our manager.", "language": "en"}
            )

        with patch.object(OpenAIAdapter, "response", race):
            asyncio.run(process_draft({}, self.did))
        with SessionLocal() as db:
            self.assertEqual(db.get(AIDraft, self.did).decision, "superseded")
        self.assertEqual(
            self.client.post("/api/drafts/" + self.did + "/send", json={}).status_code, 409
        )

    def test_budget_prompt_and_mode_rechecked_before_auto(self):
        self.generate()
        self.configure(modes={**self.cfg.modes, "shipping": ["auto", "auto"]}, monthly_limit=1)
        with SessionLocal() as db:
            d = db.get(AIDraft, self.did)
            c = db.get(Conversation, self.cid)
            d.policy = {**d.policy, "passed": True, "config_version": config_version(self.cfg)}
            self.assertTrue(auto_allowed(db, d, c))
            db.add(AIUsage(model="test", purpose="test", cost_usd=2))
            db.flush()
            self.assertFalse(auto_allowed(db, d, c))
            db.rollback()
        p = self.client.post("/api/ai/prompts", json={"body": "Short and polite"})
        self.assertEqual(p.status_code, 201, p.text)
        with SessionLocal() as db:
            self.assertFalse(
                auto_allowed(db, db.get(AIDraft, self.did), db.get(Conversation, self.cid))
            )

    def test_permissions_config_validation_and_reports(self):
        self.assertEqual(self.staff_client.get("/api/ai/settings").status_code, 403)
        self.assertEqual(
            self.staff_client.post(
                "/api/conversations/" + self.cid + "/draft/regenerate"
            ).status_code,
            403,
        )
        initial = self.client.get("/api/ai/settings").json()
        bad = initial["config"]
        bad["modes"]["payment"] = ["auto", "auto"]
        self.assertEqual(
            self.client.put(
                "/api/ai/settings", json={"version": initial["version"], "config": bad}
            ).status_code,
            422,
        )
        self.generate()
        rejected = self.client.post(
            "/api/drafts/" + self.did + "/reject", json={"reason": "Needs a specific answer"}
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        report = self.client.get("/api/reports/ai")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertTrue(report.json()["groups"])
        csrf = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.client.post("/api/ai/prompts", json={"body": "bad"}).status_code, 403)
        self.client.headers["X-CSRF-Token"] = csrf

    def test_provider_failure_keeps_manual_messaging_available(self):
        with patch.object(
            OpenAIAdapter, "response", AsyncMock(side_effect=AIError("provider_http_429"))
        ):
            asyncio.run(process_draft({}, self.did))
        with SessionLocal() as db:
            self.assertEqual(db.get(AIDraft, self.did).state, "unavailable")
        r = self.client.post(
            "/api/conversations/" + self.cid + "/messages",
            json={"client_id": uid(), "text": "Manual response"},
        )
        self.assertEqual(r.status_code, 201, r.text)

    def test_notification_opt_in_deduplication_and_uncertain_delivery(self):
        with SessionLocal() as db:
            old = Notification(
                user_id=self.user,
                kind="test",
                payload={"title": "Old", "text": "old"},
                created_at=now() - timedelta(days=1),
            )
            db.add(old)
            put_setting(db, "smtp.host", "smtp.example.invalid", False, self.user)
            put_setting(db, "smtp.from_email", "test@example.invalid", False, self.user)
            db.commit()
        invalidate_settings()
        r = self.client.put("/api/me/notifications", json={"email": True})
        self.assertEqual(r.status_code, 200, r.text)
        with SessionLocal() as db:
            n = Notification(
                user_id=self.user, kind="test", payload={"title": "New", "text": "new"}
            )
            db.add(n)
            db.commit()
            nid = n.id
        prepare_deliveries()
        prepare_deliveries()
        with SessionLocal() as db:
            rows = db.scalars(
                select(NotificationDelivery).where(NotificationDelivery.notification_id == nid)
            ).all()
            self.assertEqual(len(rows), 1)
            ident = rows[0].id
            self.assertFalse(
                db.scalar(
                    select(NotificationDelivery.id).where(
                        NotificationDelivery.notification_id == old.id
                    )
                )
            )
        with patch(
            "app.services.notifications.smtp_send",
            side_effect=DeliveryError("delivery_unknown", uncertain=True),
        ) as send:
            asyncio.run(deliver_notification(ident))
            asyncio.run(deliver_notification(ident))
            self.assertEqual(send.call_count, 1)
        with SessionLocal() as db:
            self.assertEqual(db.get(NotificationDelivery, ident).state, "uncertain")
        self.assertFalse(self.staff_client.get("/api/notifications/deliveries").json())
        self.assertEqual(
            self.client.put("/api/me/notifications", json={"telegram": True}).status_code, 400
        )
