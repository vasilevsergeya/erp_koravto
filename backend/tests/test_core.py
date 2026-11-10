import json
import logging
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("PUBLIC_URL", "https://erp.korean.parts")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "01" * 32)
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "02" * 32)

import pyotp
from cryptography.exceptions import InvalidTag

from app.core.logging import SafeJSONFormatter
from app.core.security import decrypt, encrypt, hash_password, verify_password, verify_totp
from app.integrations.channels.wazzup import WazzupAdapter
from app.services import storage
from app.workers.worker import advance_status, normalized_phone


class CoreTests(unittest.TestCase):
    def test_encryption_is_randomized_authenticated_and_context_bound(self):
        a = encrypt("secret-api-key", "wazzup.api_key")
        b = encrypt("secret-api-key", "wazzup.api_key")
        self.assertNotEqual(a, b)
        self.assertNotIn("secret-api-key", a)
        self.assertEqual(decrypt(a, "wazzup.api_key"), "secret-api-key")
        with self.assertRaises(InvalidTag):
            decrypt(a, "smtp.password")

    def test_argon2_password_hash(self):
        value = hash_password("temporary-test-password")
        self.assertTrue(value.startswith("$argon2id$"))
        self.assertTrue(verify_password("temporary-test-password", value))
        self.assertFalse(verify_password("wrong", value))

    def test_totp_replay_is_rejected(self):
        secret = pyotp.random_base32()
        counter = int(time.time()) // 30
        code = pyotp.TOTP(secret).at(counter * 30)
        self.assertEqual(verify_totp(secret, code, -1), counter)
        self.assertIsNone(verify_totp(secret, code, counter))

    def test_logs_omit_untrusted_messages_and_exception(self):
        record = logging.LogRecord(
            "httpx", logging.ERROR, "", 0, "https://secret-token:password@host", (), None
        )
        result = SafeJSONFormatter().format(record)
        self.assertNotIn("password", result)
        self.assertNotIn("secret-token", result)
        self.assertEqual(json.loads(result)["event"], "runtime")

    def test_status_does_not_regress(self):
        self.assertEqual(advance_status("read", "sent"), "read")
        self.assertEqual(advance_status("sent", "delivered"), "delivered")

    def test_webhook_validates_identifiers(self):
        with self.assertRaises(ValueError):
            WazzupAdapter.parse_webhook({"messages": [{"messageId": "x"}]})
        self.assertEqual(WazzupAdapter.parse_webhook({"test": True}), ([], []))

    def test_phone_normalization(self):
        self.assertEqual(normalized_phone("12025550123"), "+12025550123")

    def test_private_network_downloads_rejected(self):
        for address in ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:127.0.0.1"]:
            with self.assertRaises(ValueError):
                storage.validate_public_ip(address)

    def test_local_storage_and_path_traversal(self):
        from pathlib import Path

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(storage, "LOCAL_ROOT", Path(directory)),
        ):
            key, backend = storage.save_file(b"example")
            self.assertEqual(storage.read_file(key, backend), b"example")
            self.assertEqual((Path(directory) / key).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                storage.read_file("../../etc/passwd", "local")


if __name__ == "__main__":
    unittest.main()
