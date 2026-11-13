import asyncio
import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import hash_password, token_hash
from app.core.settings import invalidate_settings, put_setting
from app.integrations.channels.wazzup import WazzupAdapter
from app.main import app
from app.models import (
    AIDraft,
    Conversation,
    IntegrationHealth,
    LoginSession,
    Message,
    Notification,
    Setting,
    User,
    UserAccessToken,
    now,
    uid,
)
from app.services.integration_monitor import check_once
from app.services.message_content import rich_content


class ContentTests(unittest.TestCase):
    def test_coordinates_and_contacts_are_data_not_markup(self):
        self.assertEqual(rich_content("geo", "geo:55.75,37.61")["latitude"], 55.75)
        self.assertEqual(
            rich_content("geo", "https://maps.google.com/?q=55.75,37.61")["longitude"], 37.61
        )
        for value in ("91,0", "0,181", "nan,0", "javascript:alert(1)"):
            self.assertEqual(rich_content("geo", value), {})
        card = rich_content(
            "vcard", "BEGIN:VCARD\nFN:Alice\nTEL;TYPE=CELL:+1 (202) 555-0101\nEND:VCARD"
        )
        self.assertEqual(card, {"name": "Alice", "phones": ["+12025550101"]})


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated database")
class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.admin, self.manager, self.staff = uid(), uid(), uid()
        with SessionLocal() as db:
            for ident, role in [
                (self.admin, "admin"),
                (self.manager, "manager"),
                (self.staff, "staff"),
            ]:
                db.add(
                    User(
                        id=ident,
                        email=ident + "@example.invalid",
                        password_hash=hash_password("testpass7"),
                        role=role,
                        must_change_password=False,
                    )
                )
            db.commit()
        self.client = self.session(self.admin)
        self.manager_client = self.session(self.manager)
        self.staff_client = self.session(self.staff)

    def tearDown(self):
        for client in self.clients:
            client.close()

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
        self.clients.append(client)
        return client

    def test_invite_single_use_seven_character_password_and_no_implicit_email(self):
        body = {"email": uid() + "@example.invalid", "role": "manager"}
        self.assertEqual(self.staff_client.post("/api/admin/users", json=body).status_code, 403)
        self.assertEqual(self.manager_client.get("/api/admin/users").status_code, 403)
        with patch("app.api.team_admin.smtp_send") as send:
            response = self.client.post("/api/admin/users", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            send.assert_not_called()
        data = response.json()
        token = urlsplit(data["url"]).fragment.removeprefix("access=")
        with SessionLocal() as db:
            self.assertIsNotNone(db.get(UserAccessToken, token_hash(token)))
            self.assertIsNone(db.get(UserAccessToken, token))
        self.assertEqual(self.client.post("/api/admin/users", json=body).status_code, 409)
        fresh = TestClient(
            app, base_url=config().public_url, headers={"Origin": config().public_url}
        )
        self.addCleanup(fresh.close)
        self.assertEqual(
            fresh.post("/api/auth/access", json={"token": token, "password": "123456"}).status_code,
            422,
        )
        self.assertEqual(
            fresh.post(
                "/api/auth/access", json={"token": token, "password": "pass123"}
            ).status_code,
            200,
        )
        self.assertEqual(
            fresh.post(
                "/api/auth/access", json={"token": token, "password": "newpass7"}
            ).status_code,
            400,
        )
        self.assertEqual(
            fresh.post(
                "/api/auth/login", json={"email": body["email"], "password": "pass123"}
            ).status_code,
            200,
        )

    def test_access_changes_revoke_sessions_and_reset_links(self):
        own = next(r for r in self.client.get("/api/admin/users").json() if r["id"] == self.admin)
        self.assertEqual(
            self.client.patch(
                "/api/admin/users/" + self.admin,
                json={"role": "staff", "active": True, "version": own["access_version"]},
            ).status_code,
            409,
        )
        response = self.client.post(
            "/api/admin/users/" + self.manager + "/reset-password", json={"version": 1}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.manager_client.get("/api/auth/me").status_code, 401)
        self.assertEqual(
            self.client.patch(
                "/api/admin/users/" + self.manager,
                json={"role": "staff", "active": False, "version": 1},
            ).status_code,
            409,
        )
        version = response.json()["user"]["access_version"]
        self.assertEqual(
            self.client.patch(
                "/api/admin/users/" + self.manager,
                json={"role": "staff", "active": False, "version": version},
            ).status_code,
            200,
        )
        token = urlsplit(response.json()["url"]).fragment.removeprefix("access=")
        self.assertEqual(
            self.client.post(
                "/api/auth/access", json={"token": token, "password": "pass123"}
            ).status_code,
            400,
        )
        with SessionLocal() as db:
            self.assertIsNone(
                db.scalar(select(UserAccessToken).where(UserAccessToken.user_id == self.manager))
            )

    def test_quick_reply_permissions_placeholders_and_conflicts(self):
        body = {"title": "Greeting", "body": "Hello {имя}, order {номер_заказа}"}
        self.assertEqual(self.manager_client.post("/api/quick-replies", json=body).status_code, 403)
        self.assertEqual(
            self.client.post("/api/quick-replies", json={**body, "body": "{password}"}).status_code,
            422,
        )
        created = self.client.post("/api/quick-replies", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        r = created.json()
        path = "/api/quick-replies/" + r["id"]
        self.assertIn(r, self.staff_client.get("/api/quick-replies").json())
        self.assertEqual(
            self.client.put(path, json={**body, "body": "Updated", "version": 1}).status_code, 200
        )
        self.assertEqual(self.client.put(path, json={**body, "version": 1}).status_code, 409)
        self.assertEqual(self.client.delete(path + "?version=1").status_code, 409)
        self.assertEqual(self.client.delete(path + "?version=2").status_code, 200)

    def conversation(self, db):
        c = Conversation(
            channel_id=uid(), chat_id=uid(), name="Synthetic", assignee_id=self.manager
        )
        db.add(c)
        db.flush()
        return c

    def test_ai_filter_rejection_category_and_system_event(self):
        with SessionLocal() as db:
            c = self.conversation(db)
            m = Message(
                conversation_id=c.id,
                channel_id=c.channel_id,
                external_id=uid(),
                direction="in",
                author_type="customer",
                body="Hello",
            )
            db.add(m)
            db.flush()
            d = AIDraft(conversation_id=c.id, inbound_id=m.id, state="ready", body="Hi")
            db.add(d)
            db.commit()
            cid, did = c.id, d.id
        results = self.client.get("/api/conversations?ai_draft=true").json()
        self.assertTrue(any(c["id"] == cid and c["has_ai_draft"] for c in results))
        result = self.client.post("/api/drafts/" + did + "/reject", json={"category": "incorrect"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["rejection_category"], "incorrect")
        self.assertFalse(
            any(c["id"] == cid for c in self.client.get("/api/conversations?ai_draft=true").json())
        )
        with SessionLocal() as db:
            notices = db.scalars(
                select(Message).where(
                    Message.conversation_id == cid, Message.author_type == "system"
                )
            ).all()
            self.assertEqual(len(notices), 1)
            self.assertTrue(notices[0].is_private_note)
            self.assertEqual(notices[0].status, "system")

    def test_report_uses_first_delivered_reply_not_notes_or_failures(self):
        stamp = now() - timedelta(minutes=10)
        with SessionLocal() as db:
            c = self.conversation(db)
            for offset, direction, author, status, private in [
                (0, "in", "customer", "inbound", False),
                (30, "in", "customer", "inbound", False),
                (40, "out", "user", "sent", True),
                (50, "out", "user", "error", False),
                (120, "out", "user", "delivered", False),
            ]:
                db.add(
                    Message(
                        conversation_id=c.id,
                        channel_id=c.channel_id,
                        external_id=uid(),
                        direction=direction,
                        author_type=author,
                        author_id=self.manager if direction == "out" else None,
                        body="Synthetic",
                        status=status,
                        is_private_note=private,
                        created_at=stamp + timedelta(seconds=offset),
                    )
                )
            db.commit()
        day = stamp.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
        path = "/api/reports/inbox?start=" + day + "&end=" + day
        self.assertEqual(self.staff_client.get(path).status_code, 403)
        result = self.manager_client.get(path)
        self.assertEqual(result.status_code, 200, result.text)
        rows = result.json()["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["inbound"], 2)
        self.assertEqual(rows[0]["replies"], 1)
        self.assertEqual(rows[0]["average_seconds"], 120)

    def test_monitor_repairs_owned_subscription_but_does_not_replace_other_crm(self):
        with SessionLocal() as db:
            saved = {
                key: db.get(Setting, key) for key in ("wazzup.api_key", "wazzup.webhook_token")
            }
            snapshots = {
                key: (row.value, row.is_secret) if row else None for key, row in saved.items()
            }
            put_setting(db, "wazzup.api_key", "synthetic-test-key", True, None)
            put_setting(db, "wazzup.webhook_token", "a" * 64, True, None)
            row = db.get(IntegrationHealth, "wazzup")
            if row:
                db.delete(row)
                db.flush()
            db.add(IntegrationHealth(name="wazzup", details={"owned": True}))
            db.commit()
        invalidate_settings()
        try:
            expected = config().public_url + "/api/webhooks/wazzup/" + "a" * 64
            good = {"webhooksUri": expected, "subscriptions": {"messagesAndStatuses": True}}
            with (
                patch.object(
                    WazzupAdapter,
                    "list_channels",
                    AsyncMock(return_value=[{"channelId": "test", "state": "active"}]),
                ),
                patch.object(
                    WazzupAdapter, "check_subscription", AsyncMock(side_effect=[{}, good])
                ),
                patch.object(WazzupAdapter, "subscribe_webhooks", AsyncMock()) as subscribe,
            ):
                asyncio.run(check_once())
                subscribe.assert_awaited_once_with(expected)
            with (
                patch.object(
                    WazzupAdapter,
                    "list_channels",
                    AsyncMock(return_value=[{"channelId": "test", "state": "disabled"}]),
                ),
                patch.object(
                    WazzupAdapter,
                    "check_subscription",
                    AsyncMock(return_value={"webhooksUri": "https://other.invalid/hook"}),
                ),
                patch.object(WazzupAdapter, "subscribe_webhooks", AsyncMock()) as subscribe,
            ):
                asyncio.run(check_once())
                asyncio.run(check_once())
                subscribe.assert_not_awaited()
            with SessionLocal() as db:
                row = db.get(IntegrationHealth, "wazzup")
                self.assertIn("other_subscription", row.details["warnings"])
                events = db.scalars(
                    select(Notification).where(
                        Notification.user_id == self.admin,
                        Notification.dedupe_key.like("monitor:other_subscription:%"),
                    )
                ).all()
                self.assertEqual(len(events), 1)
        finally:
            with SessionLocal() as db:
                for key, snapshot in snapshots.items():
                    row = db.get(Setting, key)
                    if snapshot:
                        row.value, row.is_secret = snapshot
                    elif row:
                        db.delete(row)
                db.commit()
            invalidate_settings()
