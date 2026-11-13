"""Versioned message actions; local hiding is private to the signed-in employee."""

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.inbox import describe_message, locked_conversation, writer
from app.core.db import get_db
from app.models import AuditEvent, Message, MessageAction, MessageHidden, MessageTranslation, now
from app.services.outbox import emit

router = APIRouter(prefix="/api", tags=["message-actions"])


class ActionInput(BaseModel):
    client_id: uuid.UUID
    version: int = Field(ge=1)
    action: Literal["edit", "delete", "hide"]
    text: str = Field("", max_length=10000)


def message_lock(db, ident):
    initial = db.get(Message, ident)
    if not initial:
        raise HTTPException(404, "Сообщение не найдено")
    conversation = locked_conversation(db, initial.conversation_id)
    message = db.scalar(
        select(Message)
        .where(Message.id == ident)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return conversation, message


@router.post("/messages/{message_id}/actions")
def change_message(
    message_id: str, body: ActionInput, user=Depends(writer), db: Session = Depends(get_db)
):
    c, msg = message_lock(db, message_id)
    if body.action == "hide":
        db.execute(
            insert(MessageHidden)
            .values(user_id=user.id, message_id=msg.id)
            .on_conflict_do_nothing()
        )
        db.commit()
        emit(conversations=[c.id])
        return {"state": "hidden"}
    prior = db.get(MessageAction, str(body.client_id))
    if prior:
        if (prior.message_id, prior.user_id, prior.kind, prior.body, prior.version) != (
            msg.id,
            user.id,
            body.action,
            body.text,
            body.version,
        ):
            raise HTTPException(409, "Идентификатор операции уже использован")
        return {"id": prior.id, "state": prior.state}
    if msg.author_type == "system" or (msg.author_id != user.id and user.role != "admin"):
        raise HTTPException(403, "Изменять сообщение может его автор или администратор")
    if msg.direction != "out" or msg.deleted:
        raise HTTPException(400, "Это сообщение нельзя изменить для клиента")
    if msg.version != body.version:
        raise HTTPException(409, "Сообщение уже изменилось. Обновите диалог.")
    if body.action == "edit" and (not body.text.strip() or msg.attachment_id):
        raise HTTPException(400, "Редактирование доступно для текстовых сообщений")
    active = db.scalar(
        select(MessageAction.id).where(
            MessageAction.message_id == msg.id,
            MessageAction.state.in_(["queued", "processing", "uncertain"]),
        )
    )
    if active:
        raise HTTPException(409, "Предыдущая операция ещё не завершена")
    unsent = (
        msg.external_id.startswith("local:")
        and msg.status in ("queued", "translating", "error")
        and not msg.send_started_at
    )
    if msg.is_private_note or unsent:
        msg.history = [
            *msg.history,
            {"body": msg.body, "at": now().isoformat(), "author_id": user.id},
        ]
        msg.version += 1
        if body.action == "delete":
            msg.deleted = True
            if unsent:
                msg.status, msg.next_attempt_at = "cancelled", None
        else:
            msg.body, msg.edited = body.text, True
            if unsent:
                from app.services.translations import prepare_outgoing

                msg.delivery_body = None
                msg.status, msg.next_attempt_at = "queued", now()
                prepare_outgoing(db, c, msg)
        db.add(
            AuditEvent(
                user_id=user.id,
                action=("note." if msg.is_private_note else "message.") + body.action,
                target=msg.id,
            )
        )
        db.commit()
        emit(conversations=[c.id])
        return {"state": "done", "message": describe_message(msg, db, user.id)}
    if msg.external_id.startswith("local:") or msg.status not in ("sent", "delivered", "read"):
        raise HTTPException(409, "Сообщение ещё не доставлено провайдеру")
    age = now() - (msg.sent_at or msg.created_at)
    if c.chat_type == "whatsapp" and age > (
        timedelta(minutes=15) if body.action == "edit" else timedelta(hours=24)
    ):
        raise HTTPException(
            400, "Истёк срок WhatsApp: редактирование — 15 минут, удаление — 24 часа"
        )
    row = MessageAction(
        id=str(body.client_id),
        message_id=msg.id,
        user_id=user.id,
        kind=body.action,
        body=body.text,
        version=msg.version,
    )
    db.add(row)
    db.add(
        AuditEvent(user_id=user.id, action="message." + body.action + "_requested", target=msg.id)
    )
    db.commit()
    emit(conversations=[c.id])
    return {"id": row.id, "state": row.state}


class ActionRetry(BaseModel):
    confirm: bool = False


@router.post("/message-actions/{action_id}/retry")
def retry_action(
    action_id: str, body: ActionRetry, user=Depends(writer), db: Session = Depends(get_db)
):
    row = db.get(MessageAction, action_id)
    if not row:
        raise HTTPException(404, "Операция не найдена")
    c, msg = message_lock(db, row.message_id)
    if row.user_id != user.id and user.role != "admin":
        raise HTTPException(403, "Доступ запрещён")
    if row.state not in ("error", "uncertain") or not body.confirm:
        raise HTTPException(409, "Подтвердите повтор после проверки сообщения в WhatsApp")
    if msg.version != row.version:
        raise HTTPException(409, "Сообщение уже изменилось. Обновите диалог.")
    row.state, row.error, row.started_at = "queued", None, None
    db.commit()
    emit(conversations=[c.id])
    return {"state": "queued"}


@router.post("/messages/{message_id}/translation/retry")
def retry_translation(message_id: str, user=Depends(writer), db: Session = Depends(get_db)):
    c, msg = message_lock(db, message_id)
    for row in db.scalars(
        select(MessageTranslation).where(
            MessageTranslation.message_id == msg.id,
            MessageTranslation.revision == msg.version,
            MessageTranslation.state == "error",
        )
    ):
        row.state, row.attempts, row.next_attempt_at = "pending", 0, now()
    db.commit()
    emit(conversations=[c.id])
    return {"ok": True}
