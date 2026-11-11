import os
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("PUBLIC_URL", "https://erp.korean.parts")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "01" * 32)
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "02" * 32)

from fastapi import HTTPException

from app.api.auth import admin_user, current_user
from app.core.security import token_hash
from app.main import app, websocket_authorized
from app.models import LoginSession, User, now


class PasswordOnlyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.user = User(
            id="test-admin",
            active=True,
            role="admin",
            must_change_password=False,
            totp_enabled=False,
        )
        self.raw = "test-session-token"
        self.session = LoginSession(
            token_hash=token_hash(self.raw),
            user_id=self.user.id,
            expires_at=now() + timedelta(minutes=10),
            second_factor_verified=False,
        )

    def websocket_access(self):
        db = MagicMock()
        db.get.side_effect = lambda model, key: self.session if model is LoginSession else self.user
        with patch("app.main.SessionLocal") as factory:
            factory.return_value.__enter__.return_value = db
            return websocket_authorized(self.raw)

    def test_password_authenticated_user_needs_no_totp_for_api_or_websocket(self):
        self.assertIs(current_user((self.session, self.user)), self.user)
        self.assertIs(admin_user(self.user), self.user)
        self.assertTrue(self.websocket_access())

    def test_temporary_password_still_blocks_api_and_websocket(self):
        self.user.must_change_password = True
        with self.assertRaises(HTTPException) as error:
            current_user((self.session, self.user))
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(self.websocket_access())

    def test_expired_and_inactive_sessions_still_block_websocket(self):
        self.session.expires_at = now() - timedelta(seconds=1)
        self.assertFalse(self.websocket_access())
        self.session.expires_at = now() + timedelta(minutes=10)
        self.user.active = False
        self.assertFalse(self.websocket_access())

    def test_password_only_login_does_not_grant_admin_role(self):
        self.user.role = "employee"
        with self.assertRaises(HTTPException) as error:
            admin_user(self.user)
        self.assertEqual(error.exception.status_code, 403)

    def test_retired_totp_endpoints_are_not_exposed(self):
        self.assertFalse(any("/totp/" in path for path in app.openapi()["paths"]))


if __name__ == "__main__":
    unittest.main()
