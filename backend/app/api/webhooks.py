import json
import secrets

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.core.db import SessionLocal
from app.core.settings import direct_secret
from app.models import WebhookEvent, now

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def save_event(token: str, payload: dict) -> str:
    with SessionLocal() as db:
        expected = direct_secret(db, "wazzup.webhook_token")
        if not expected or not secrets.compare_digest(expected, token):
            raise HTTPException(404, "Not found")
        test = payload == {"test": True}
        event = WebhookEvent(
            payload=payload,
            state="test" if test else "pending",
            processed_at=now() if test else None,
        )
        db.add(event)
        db.commit()
        return event.id


@router.post("/wazzup/{token}")
async def wazzup_webhook(token: str, request: Request):
    if len(token) != 64:
        raise HTTPException(404, "Not found")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 1024 * 1024:
            raise HTTPException(413, "Payload too large")
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid JSON object")
    # PostgreSQL is the durable inbox. Worker dispatches it even after a Redis outage.
    await run_in_threadpool(save_event, token, payload)
    return {"ok": True}
