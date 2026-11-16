from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import admin_user, current_user
from app.core.db import get_db
from app.models import IntegrationHealth, WebhookEvent, now
from app.services.integration_monitor import WARNINGS
from app.services.webhook_queue import describe_failure, summary

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


@router.get("/health")
def health(user=Depends(current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        return {"checked_at": None, "warnings": [], "channels": []}
    row = db.get(IntegrationHealth, "wazzup")
    if not row:
        return {"checked_at": None, "warnings": [], "channels": []}
    # Queue truth comes from the event table, not a five-minute-old monitor snapshot.
    queue = summary(db)
    warnings = [code for code in row.details.get("warnings", []) if code != "backlog"]
    if queue["oldest_pending_at"] and queue["oldest_pending_at"] < now() - timedelta(minutes=5):
        warnings.append("backlog")
    return {
        "checked_at": row.checked_at,
        "warnings": [{"code": code, "text": WARNINGS.get(code, code)} for code in warnings],
        "channels": row.details.get("channels", []),
        "last_webhook_at": row.details.get("last_webhook_at"),
        "queue": queue,
    }


@router.get("/events/failed")
def failed_events(
    offset: int = Query(0, ge=0), user=Depends(admin_user), db: Session = Depends(get_db)
):
    return {
        **summary(db),
        "items": [
            describe_failure(row)
            for row in db.scalars(
                select(WebhookEvent)
                .where(WebhookEvent.state == "failed")
                .order_by(WebhookEvent.created_at.desc(), WebhookEvent.id.desc())
                .offset(offset)
                .limit(50)
            )
        ],
    }
