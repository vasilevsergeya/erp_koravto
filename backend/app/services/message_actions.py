"""Durable external edits/deletions; no optimistic success or blind network retries."""

from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.settings import get_setting
from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter
from app.integrations.openai import AIError
from app.models import Conversation, Message, MessageAction, MessageTranslation, now
from app.services.outbox import emit


async def process_action(ctx, action_id):
    with SessionLocal() as db:
        action = db.scalar(
            select(MessageAction).where(MessageAction.id == action_id).with_for_update()
        )
        if not action or action.state != "queued":
            return
        msg = db.get(Message, action.message_id)
        if not msg or msg.deleted or msg.version != action.version:
            action.state, action.error = "error", "message_changed"
            db.commit()
            return
        action.state, action.started_at = "processing", now()
        key, ext = get_setting(db, "wazzup.api_key", ""), msg.external_id
        body, kind = action.body, action.kind
        source, target = msg.delivery_source_language, msg.delivery_target_language
        cid, mid = msg.conversation_id, msg.id
        db.commit()
    wire_body = body
    try:
        if kind == "edit" and target:
            from app.services.translations import translate_text

            wire_body = await translate_text(body, source, target)
        await WazzupAdapter(key).change_message(ext, text=wire_body if kind == "edit" else None)
        state, error = "done", None
    except AIError as exc:
        state, error = "error", "translation_" + str(exc)
    except IntegrationError as exc:
        state, error = ("uncertain" if exc.ambiguous else "error"), exc.code
    with SessionLocal() as db:
        # Same lock order as delivery/webhooks and interactive requests.
        conversation = db.scalar(
            select(Conversation).where(Conversation.id == cid).with_for_update()
        )
        msg = db.scalar(select(Message).where(Message.id == mid).with_for_update())
        action = db.scalar(
            select(MessageAction).where(MessageAction.id == action_id).with_for_update()
        )
        if action is None:
            return
        action.state, action.error, action.completed_at = state, error, now()
        if state == "done" and msg:
            if kind == "delete":
                if not msg.deleted:
                    msg.deleted, msg.version = True, msg.version + 1
            else:
                msg.history = [
                    *msg.history,
                    {"body": msg.body, "at": now().isoformat(), "author_id": action.user_id},
                ]
                msg.body, msg.edited, msg.version = body, True, msg.version + 1
                msg.delivery_body = wire_body if target else None
                if target:
                    db.add(
                        MessageTranslation(
                            message_id=msg.id,
                            revision=msg.version,
                            source_language=source,
                            target_language=target,
                            original=body,
                            body=wire_body,
                            state="ready",
                        )
                    )
            if conversation:
                from app.services.response_state import refresh_unanswered

                refresh_unanswered(db, conversation)
        db.commit()
    emit(conversations=[cid])


def recover_actions(db):
    for row in db.scalars(
        select(MessageAction).where(
            MessageAction.state == "processing",
            MessageAction.started_at < now() - timedelta(minutes=2),
        )
    ):
        row.state, row.error = "uncertain", "change_unknown"
    return db.scalars(
        select(MessageAction.id)
        .where(MessageAction.state == "queued")
        .order_by(MessageAction.created_at)
        .limit(20)
    ).all()
