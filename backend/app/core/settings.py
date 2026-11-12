import threading
from typing import cast

from redis import Redis, RedisError
from sqlalchemy import select

from app.core.config import config
from app.core.security import decrypt, encrypt
from app.models import AuditEvent, Setting

# Secret classification is server-owned; API callers cannot change it.
SETTING_SCHEMA = {
    "inbox.first_messages_per_hour": ("integer", False, 20),
    "inbox.auto_close_days": ("integer", False, 14),
    "wazzup.api_key": ("string", True, ""),
    "wazzup.default_channel_id": ("string", False, ""),
    "openai.api_key": ("string", True, ""),
    "onec.base_url": ("string", False, ""),
    "onec.username": ("string", False, ""),
    "onec.password": ("string", True, ""),
    "smtp.host": ("string", False, ""),
    "smtp.port": ("integer", False, 587),
    "smtp.username": ("string", False, ""),
    "smtp.password": ("string", True, ""),
    "smtp.from_email": ("string", False, ""),
}
redis_client = Redis.from_url(config().redis_url, socket_connect_timeout=1, socket_timeout=1)
_cache: dict = {}
_cache_version: bytes | None = None
_lock = threading.Lock()


def get_setting(db, key: str, default=None):
    global _cache_version
    try:
        version = cast(bytes | None, redis_client.get("crm:settings:version"))
    except RedisError:
        version = None
    with _lock:
        if version is None or version != _cache_version:
            _cache.clear()
            _cache_version = version
        if version is not None and key in _cache:
            return _cache[key]
    row = db.get(Setting, key)
    if row is None:
        return default
    value = decrypt(row.value["encrypted"], key) if row.is_secret else row.value["value"]
    if version is not None:
        with _lock:
            _cache[key] = value
    return value


def invalidate_settings():
    with _lock:
        _cache.clear()
    try:
        redis_client.incr("crm:settings:version")
        redis_client.publish("crm:settings:changed", "reload")
    except RedisError:
        pass  # Database remains the source of truth; uncached reads work offline.


def put_setting(db, key: str, value, is_secret: bool, actor: str | None):
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.add(row)
    row.value = {"encrypted": encrypt(value, key)} if is_secret else {"value": value}
    row.is_secret = is_secret
    row.updated_by = actor
    db.add(AuditEvent(user_id=actor, action="setting.updated", target=key))


def masked_settings(db) -> list[dict]:
    result = []
    for key, (kind, secret, default) in SETTING_SCHEMA.items():
        value = get_setting(db, key, default)
        result.append(
            {
                "key": key,
                "type": kind,
                "is_secret": secret,
                "is_set": bool(value),
                "value": (
                    ("••••" if key == "onec.password" else "••••" + str(value)[-4:])
                    if value
                    else ""
                )
                if secret
                else value,
            }
        )
    return result


def direct_secret(db, key: str) -> str:
    # Security-sensitive webhook tokens bypass process caches.
    row = db.scalar(select(Setting).where(Setting.key == key))
    return decrypt(row.value["encrypted"], key) if row and row.is_secret else ""
