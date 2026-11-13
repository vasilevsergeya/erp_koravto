import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import admin_user, current_user
from app.core.db import get_db
from app.models import AuditEvent, QuickReply
from app.services.outbox import emit

router = APIRouter(prefix="/api/quick-replies", tags=["quick replies"])


class ReplyInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1, max_length=10000)
    version: int = Field(1, ge=1)

    @field_validator("title", "body")
    @classmethod
    def validate_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Empty text")
        if any(key not in ("имя", "номер_заказа") for key in re.findall(r"\{([^{}]*)\}", value)):
            raise ValueError("Unknown placeholder")
        return value


def describe(row):
    return {"id": row.id, "title": row.title, "body": row.body, "version": row.version}


@router.get("")
def replies(user=Depends(current_user), db: Session = Depends(get_db)):
    return [describe(row) for row in db.scalars(select(QuickReply).order_by(QuickReply.title))]


@router.post("")
def create(body: ReplyInput, user=Depends(admin_user), db: Session = Depends(get_db)):
    row = QuickReply(title=body.title, body=body.body)
    db.add(row)
    db.flush()
    db.add(AuditEvent(user_id=user.id, action="quick_reply.created", target=row.id))
    db.commit()
    emit("quick_replies.changed")
    return describe(row)


@router.put("/{reply_id}")
def update(
    reply_id: str, body: ReplyInput, user=Depends(admin_user), db: Session = Depends(get_db)
):
    row = db.scalar(select(QuickReply).where(QuickReply.id == reply_id).with_for_update())
    if not row:
        raise HTTPException(404, "Быстрый ответ не найден")
    if row.version != body.version:
        raise HTTPException(409, "Быстрый ответ изменён другим сотрудником. Обновите список")
    row.title, row.body = body.title, body.body
    row.version += 1
    db.add(AuditEvent(user_id=user.id, action="quick_reply.updated", target=row.id))
    db.commit()
    emit("quick_replies.changed")
    return describe(row)


@router.delete("/{reply_id}")
def remove(reply_id: str, version: int, user=Depends(admin_user), db: Session = Depends(get_db)):
    row = db.scalar(select(QuickReply).where(QuickReply.id == reply_id).with_for_update())
    if not row:
        raise HTTPException(404, "Быстрый ответ не найден")
    if row.version != version:
        raise HTTPException(409, "Быстрый ответ изменён. Обновите список")
    db.add(AuditEvent(user_id=user.id, action="quick_reply.deleted", target=row.id))
    db.delete(row)
    db.commit()
    emit("quick_replies.changed")
    return {"ok": True}
