"""Refresh cached photos independently of webhook delivery."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal
from app.models import CustomerAvatar, now
from app.services.storage import download_attachment

log = logging.getLogger("crm")


def remember_avatar(db, customer_id, item):
    contact = item.get("contact")
    url = contact.get("avatarUri") if isinstance(contact, dict) else None
    if (
        not customer_id
        or item.get("status") != "inbound"
        or not isinstance(url, str)
        or not url.startswith("https://")
        or len(url) > 4096
    ):
        return
    stmt = insert(CustomerAvatar).values(customer_id=customer_id, source=url, next_attempt_at=now())
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[CustomerAvatar.customer_id],
            set_={"source": url},
            where=CustomerAvatar.source != url,
        )
    )


def image_type(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


async def refresh_one():
    with SessionLocal() as db:
        row = db.scalar(
            select(CustomerAvatar)
            .where(CustomerAvatar.next_attempt_at <= now())
            .order_by(CustomerAvatar.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not row:
            return False
        owner, url = row.customer_id, row.source
        row.next_attempt_at = now() + timedelta(days=7)
        db.commit()
    data = await download_attachment(url, max_bytes=512 * 1024)
    mime = image_type(data)
    if mime:
        with SessionLocal() as db:
            db.execute(
                update(CustomerAvatar)
                .where(CustomerAvatar.customer_id == owner)
                .values(photo=data, mime=mime, updated_at=now())
            )
            db.commit()
    return True


async def refresh_avatars_loop():
    while True:
        try:
            work = await refresh_one()
        except Exception as error:
            log.info("avatar.refresh_failed", extra={"error_type": type(error).__name__})
            work = True
        await asyncio.sleep(1 if work else 30)
