import base64
import hashlib
import json
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from itsdangerous import URLSafeTimedSerializer

from app.core.config import config

password_hasher = PasswordHasher(time_cost=3, memory_cost=32768, parallelism=1)


def hash_password(value: str) -> str:
    return password_hasher.hash(value)


def verify_password(value: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, value)
    except VerificationError:
        return False


def encrypt(value, context: str) -> str:
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(bytes.fromhex(config().master_encryption_key)).encrypt(
        nonce, json.dumps(value, ensure_ascii=False).encode(), context.encode()
    )
    return base64.urlsafe_b64encode(nonce + ciphertext).decode()


def decrypt(value: str, context: str):
    raw = base64.urlsafe_b64decode(value)
    plaintext = AESGCM(bytes.fromhex(config().master_encryption_key)).decrypt(
        raw[:12], raw[12:], context.encode()
    )
    return json.loads(plaintext)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_totp(secret: str, code: str, last_counter: int) -> int | None:
    if not (code.isdigit() and len(code) == 6):
        return None
    counter = int(time.time()) // 30
    totp = pyotp.TOTP(secret)
    for candidate in [counter, counter - 1, counter + 1]:
        if candidate > last_counter and secrets.compare_digest(totp.at(candidate * 30), code):
            return candidate
    return None


def file_signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config().secret_key, salt="crm-attachment-v1")
