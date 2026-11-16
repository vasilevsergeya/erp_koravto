import asyncio
import os
import secrets
import unittest
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault("PUBLIC_URL", "https://crm-test.invalid")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "01" * 32)
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "02" * 32)

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import token_hash
from app.main import app
from app.models import IntegrationHealth, LoginSession, User, WebhookEvent, now
from app.services.webhook_queue import describe_failure, error_code, pending_condition, summary
from app.workers.worker import process_webhook


class QueueReasonTests(unittest.TestCase):
    def test_only_allowlisted_exception_text_is_saved(self):
        self.assertEqual(error_code(ValueError("attachment_too_large")), "attachment_too_large")
        self.assertEqual(error_code(ValueError("https://secret.example/token")), "ValueError")
        self.assertEqual(error_code(RuntimeError("private data")), "RuntimeError")

    def test_failure_response_has_no_payload_or_raw_exception(self):
        event = WebhookEvent(
            id="test",
            payload={"private": "secret"},
            attempts=10,
            created_at=now(),
            last_error="https://secret.example/token",
        )
        result = describe_failure(event)
        self.assertNotIn("payload", result)
        self.assertNotIn("secret", str(result))


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires an isolated test database")
class SessionQueueIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.raw = secrets.token_urlsafe(32)
        self.csrf = secrets.token_hex(32)
        self.deadline = now() + timedelta(minutes=2)
        with SessionLocal() as db:
            user = User(
                email=secrets.token_hex(8) + "@example.invalid",
                password_hash="unused",
                active=True,
                role="admin",
                must_change_password=False,
            )
            db.add(user)
            db.flush()
            self.user_id = user.id
            db.add(
                LoginSession(
                    token_hash=token_hash(self.raw),
                    user_id=user.id,
                    csrf_token=self.csrf,
                    expires_at=self.deadline,
                )
            )
            db.commit()
        self.client = TestClient(
            app,
            base_url=config().public_url,
            headers={"Origin": config().public_url, "X-CSRF-Token": self.csrf},
        )
        self.client.cookies.set(COOKIE, self.raw)

    def tearDown(self):
        self.client.close()
        with SessionLocal() as db:
            db.execute(delete(LoginSession).where(LoginSession.user_id == self.user_id))
            db.execute(delete(User).where(User.id == self.user_id))
            db.commit()

    def test_refresh_extends_live_session_with_secure_cookie(self):
        response = self.client.post("/api/auth/refresh")
        self.assertEqual(response.status_code, 200)
        self.assertIn("session_expires_at", response.json())
        cookie = response.headers["set-cookie"].lower()
        for flag in ("httponly", "secure", "samesite=lax", "path=/"):
            self.assertIn(flag, cookie)
        with SessionLocal() as db:
            deadline = db.get(LoginSession, token_hash(self.raw)).expires_at
            self.assertGreater(deadline, now() + timedelta(hours=7, minutes=59))
            self.assertLessEqual(deadline, now() + timedelta(hours=8))
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

    def test_expired_revoked_and_inactive_sessions_cannot_refresh(self):
        with SessionLocal() as db:
            db.get(LoginSession, token_hash(self.raw)).expires_at = now() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.post("/api/auth/refresh").status_code, 401)
        with SessionLocal() as db:
            db.get(LoginSession, token_hash(self.raw)).expires_at = self.deadline
            db.get(User, self.user_id).active = False
            db.commit()
        self.assertEqual(self.client.post("/api/auth/refresh").status_code, 401)
        with SessionLocal() as db:
            db.delete(db.get(LoginSession, token_hash(self.raw)))
            db.commit()
        self.assertEqual(self.client.post("/api/auth/refresh").status_code, 401)

    def test_refresh_requires_csrf_and_same_origin(self):
        self.assertEqual(
            self.client.post("/api/auth/refresh", headers={"X-CSRF-Token": "bad"}).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/refresh", headers={"Origin": "https://other.invalid"}
            ).status_code,
            403,
        )
        with SessionLocal() as db:
            self.assertEqual(db.get(LoginSession, token_hash(self.raw)).expires_at, self.deadline)

    def test_temporary_password_deadline_is_not_extended(self):
        with SessionLocal() as db:
            db.get(User, self.user_id).must_change_password = True
            db.commit()
        self.assertEqual(self.client.post("/api/auth/refresh").status_code, 200)
        with SessionLocal() as db:
            self.assertEqual(db.get(LoginSession, token_hash(self.raw)).expires_at, self.deadline)

    def test_terminal_failure_leaves_pending_queue_and_is_visible_to_admin_only(self):
        with SessionLocal() as db:
            baseline = summary(db)
            event = WebhookEvent(
                payload={"test": True},
                state="error",
                attempts=9,
                created_at=now() - timedelta(days=1),
            )
            db.add(event)
            db.flush()
            event_id = event.id
            db.commit()
        try:
            with patch(
                "app.workers.worker.WazzupAdapter.parse_webhook",
                side_effect=ValueError("attachment_too_large"),
            ) as parser:
                asyncio.run(process_webhook({}, event_id))
                asyncio.run(process_webhook({}, event_id))
                self.assertEqual(parser.call_count, 1)
            with SessionLocal() as db:
                row = db.get(WebhookEvent, event_id)
                self.assertEqual(
                    (row.state, row.attempts, row.last_error),
                    ("failed", 10, "attachment_too_large"),
                )
                self.assertEqual(summary(db)["pending"], baseline["pending"])
                self.assertNotIn(
                    event_id, list(db.scalars(select(WebhookEvent.id).where(pending_condition())))
                )
                old_health = db.get(IntegrationHealth, "wazzup")
                previous = dict(old_health.details) if old_health else None
                db.merge(
                    IntegrationHealth(
                        name="wazzup", details={"warnings": ["backlog"], "channels": []}
                    )
                )
                db.commit()
            health = self.client.get("/api/integrations/health")
            self.assertEqual(health.status_code, 200)
            self.assertNotIn("backlog", [w["code"] for w in health.json()["warnings"]])
            failures = self.client.get("/api/integrations/events/failed")
            self.assertEqual(failures.status_code, 200)
            item = next(e for e in failures.json()["items"] if e["id"] == event_id)
            self.assertIn("10 МБ", item["reason"])
            self.assertNotIn("payload", item)
            with SessionLocal() as db:
                db.get(User, self.user_id).role = "employee"
                db.commit()
            self.assertEqual(self.client.get("/api/integrations/events/failed").status_code, 403)
        finally:
            with SessionLocal() as db:
                db.execute(delete(WebhookEvent).where(WebhookEvent.id == event_id))
                row = db.get(IntegrationHealth, "wazzup")
                if row:
                    if previous is None:
                        db.delete(row)
                    else:
                        row.details = previous
                db.commit()
