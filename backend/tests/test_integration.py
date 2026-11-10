import os
import secrets
import subprocess
import time
import unittest
from unittest.mock import patch

import pyotp
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select

from app.cli import seed_boards
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.core.settings import direct_secret, redis_client
from app.main import app
from app.models import Board, Message, Setting, User, WebhookEvent


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires an isolated test database")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        with SessionLocal() as db:
            db.add(
                User(email="acceptance@example.invalid", password_hash=hash_password(cls.password))
            )
            seed_boards(db)
            cls.token = direct_secret(db, "wazzup.webhook_token")
        cls.worker = subprocess.Popen(
            ["arq", "app.workers.worker.WorkerSettings"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        cls.client = TestClient(
            app, base_url=config().public_url, headers={"Origin": config().public_url}
        )
        response = cls.client.post(
            "/api/auth/login",
            json={"email": "acceptance@example.invalid", "password": cls.password},
        )
        assert response.status_code == 200, response.status_code
        cls.client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        assert cls.client.get("/api/settings").status_code == 403
        assert (
            cls.client.post(
                "/api/auth/password", json={"password": secrets.token_urlsafe(24)}
            ).status_code
            == 200
        )
        enrollment = cls.client.post("/api/auth/totp/setup").json()
        cls.totp = enrollment["secret"]
        code = pyotp.TOTP(cls.totp).now()
        assert cls.client.post("/api/auth/totp/verify", json={"code": code}).status_code == 200
        for _ in range(30):
            if cls.client.get("/health").status_code == 200:
                break
            time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.worker.terminate()
        cls.worker.wait(timeout=15)
        redis_client.flushdb()  # Dedicated Redis database 15, asserted by selftest runner.

    def test_health_reports_database_redis_and_worker(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["services"], {"database": "ok", "redis": "ok", "worker": "ok"}
        )

    def test_settings_are_masked_encrypted_and_csrf_protected(self):
        value = secrets.token_urlsafe(20)
        response = self.client.put("/api/settings/smtp.password", json={"value": value})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["value"], "••••" + value[-4:])
        with SessionLocal() as db:
            row = db.get(Setting, "smtp.password")
            self.assertNotIn(value, str(row.value))
        bad_csrf = self.client.put(
            "/api/settings/smtp.password",
            json={"value": "wrong"},
            headers={"X-CSRF-Token": "invalid"},
        )
        self.assertEqual(bad_csrf.status_code, 403)

    def test_unauthorized_and_untrusted_origins_rejected(self):
        with TestClient(app, base_url=config().public_url) as client:
            self.assertEqual(client.get("/api/settings").status_code, 401)
            self.assertEqual(client.get("/api/docs").status_code, 401)
            self.assertEqual(
                client.post(
                    "/api/auth/login",
                    json={"email": "x@y.z", "password": "x"},
                    headers={"Origin": "https://other.invalid"},
                ).status_code,
                403,
            )

    def test_seed_boards_is_idempotent(self):
        with SessionLocal() as db:
            seed_boards(db)
            self.assertEqual(db.scalar(select(func.count()).select_from(Board)), 3)
        self.assertEqual(len(self.client.get("/api/boards").json()), 3)

    def test_webhook_test_token_and_message_deduplication(self):
        path = "/api/webhooks/wazzup/" + self.token
        self.assertEqual(
            self.client.post("/api/webhooks/wazzup/" + "0" * 64, json={"test": True}).status_code,
            404,
        )
        self.assertEqual(self.client.post(path, json={"test": True}).status_code, 200)
        payload = {
            "messages": [
                {
                    "messageId": "test-message",
                    "channelId": "test-channel",
                    "chatId": "12025550123",
                    "chatType": "whatsapp",
                    "status": "inbound",
                    "type": "text",
                    "text": "Тест · العربية · 😀",
                    "contact": {"name": "Acceptance test"},
                }
            ]
        }
        for _ in range(2):
            self.assertEqual(self.client.post(path, json=payload).status_code, 200)
        for _ in range(40):
            with SessionLocal() as db:
                pending = db.scalar(
                    select(func.count())
                    .select_from(WebhookEvent)
                    .where(WebhookEvent.state == "pending")
                )
                count = db.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.external_id == "test-message")
                )
            if pending == 0 and count == 1:
                break
            time.sleep(0.25)
        self.assertEqual(count, 1)
        self.assertEqual(pending, 0)
        conversations = self.client.get("/api/conversations").json()
        self.assertTrue(conversations)
        messages = self.client.get(
            "/api/conversations/" + conversations[0]["id"] + "/messages"
        ).json()
        self.assertEqual(messages[0]["text"], "Тест · العربية · 😀")

    def test_redis_outage_does_not_lose_webhook(self):
        with patch.object(redis_client, "ping", side_effect=RedisConnectionError()):
            self.assertEqual(self.client.get("/health").status_code, 503)
            response = self.client.post("/api/webhooks/wazzup/" + self.token, json={"test": True})
            self.assertEqual(response.status_code, 200)
        with SessionLocal() as db:
            self.assertGreater(
                db.scalar(
                    select(func.count())
                    .select_from(WebhookEvent)
                    .where(WebhookEvent.state == "test")
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
