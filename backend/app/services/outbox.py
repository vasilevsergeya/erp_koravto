"""Database outbox. Unknown delivery is never blindly retried after Wazzup's 60s window."""

import asyncio
import contextlib
import json
import random
from datetime import timedelta

from sqlalchemy import select, text, update

from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import file_signer
from app.core.settings import get_setting, redis_client
from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter
from app.models import Conversation, DeliveryReceipt, Message, now, uid

ERRORS = {
    "BAD_CONTACT": "Номер не зарегистрирован в WhatsApp или недоступен.",
    "MESSAGES_NOT_TEXT_FIRST": "Тариф Wazzup не позволяет писать этому номеру первым.",
    "MESSAGES_IS_SPAM": "Wazzup отклонил сообщение как спам.",
    "SPAM": "Wazzup отклонил сообщение как спам.",
    "CHANNEL_NOT_FOUND": "Канал WhatsApp не найден.",
    "CHANNEL_BLOCKED": "Канал WhatsApp заблокирован.",
    "CHANNEL_UNAVAILABLE": "Канал WhatsApp недоступен. Проверьте подключение телефона.",
    "TOO_MANY_EXT_REQS": "Слишком много отправок. Попробуйте позже.",
    "key_not_configured": "Ключ Wazzup не настроен.",
    "delivery_unknown": "Результат отправки не подтверждён. Проверьте переписку в WhatsApp перед повтором.",
    "REPEATED_CRM_MESSAGE_ID": "Wazzup уже принял это сообщение. Ожидается подтверждение доставки.",
}


def error_text(code):
    return ERRORS.get(code, "Ошибка отправки: " + str(code)) if code else None


def emit(event="messages.changed", **payload):
    # A failed live notification must not roll back a committed message.
    with contextlib.suppress(Exception):
        redis_client.publish("crm:events", json.dumps({"type": event, **payload}))


def status_forward(old, new):
    ranks = {
        "queued": 0,
        "sending": 0,
        "retry": 0,
        "uncertain": 0,
        "accepted": 1,
        "sent": 2,
        "delivered": 3,
        "read": 4,
    }
    if old in ("delivered", "read") and new == "error":
        return old
    if old in ranks and new in ranks and ranks[new] < ranks[old]:
        return old
    return new if new in {*ranks, "inbound", "error", "note"} else old


async def deliver_message(ctx, message_id):
    token = uid()
    channel_key = None
    try:
        with SessionLocal() as db:
            msg = db.get(Message, message_id)
            if not msg or msg.status not in ("queued", "retry") or msg.is_private_note:
                return
            channel_key = "crm:outbound:lock:" + msg.channel_id
        if not await ctx["redis"].set(channel_key, token, nx=True, ex=120):
            return
        with SessionLocal() as db:
            initial = db.get(Message, message_id)
            if not initial:
                return
            db.scalar(select(Conversation).where(Conversation.id == initial.conversation_id).with_for_update())
            msg = db.scalar(select(Message).where(Message.id == message_id).with_for_update().execution_options(populate_existing=True))
            if not msg or msg.status not in ("queued", "retry"):
                return
            if msg.author_type == "ai" and not msg.send_started_at:
                from app.models import AIDraft
                from app.services.ai import auto_allowed
                draft = db.scalar(select(AIDraft).where(AIDraft.message_id == msg.id))
                conversation = db.get(Conversation, msg.conversation_id)
                if not draft or not auto_allowed(db, draft, conversation):
                    msg.status, msg.next_attempt_at, msg.error_code = "cancelled", None, "ai_superseded"
                    if draft:
                        draft.decision, draft.reason = "superseded", "policy_or_message_changed"
                    db.commit()
                    emit(conversations=[msg.conversation_id])
                    return
            if msg.next_attempt_at and msg.next_attempt_at > now():
                return
            # Preserve order in a conversation (file, then caption) and on a channel.
            earlier = db.scalar(
                select(Message.id)
                .where(
                    Message.channel_id == msg.channel_id,
                    Message.status.in_(["queued", "retry", "sending"]),
                    Message.created_at < msg.created_at,
                )
                .limit(1)
            )
            if earlier:
                return
            if msg.send_started_at and (now() - msg.send_started_at).total_seconds() >= 50:
                msg.status, msg.error_code = "uncertain", "delivery_unknown"
                msg.next_attempt_at = None
                db.commit()
                emit(conversations=[msg.conversation_id])
                return
            conversation = db.get(Conversation, msg.conversation_id)
            if not conversation:
                return
            key = get_setting(db, "wazzup.api_key", "")
            payload = {
                "channelId": msg.channel_id,
                "chatId": conversation.chat_id,
                "chatType": conversation.chat_type,
                "crmMessageId": msg.id,
            }
            if msg.attachment_id:
                payload["contentUri"] = (
                    config().public_url + "/api/files/" + file_signer().dumps(msg.attachment_id)
                )
            else:
                payload["text"] = msg.body
            if msg.quoted_message_id:
                quoted = db.get(Message, msg.quoted_message_id)
                if quoted and not quoted.external_id.startswith("local:"):
                    payload["refMessageId"] = quoted.external_id
            msg.status = "sending"
            msg.send_started_at = msg.send_started_at or now()
            msg.send_attempts += 1
            msg.next_attempt_at = now() + timedelta(seconds=90)
            db.commit()
            conversation_id = msg.conversation_id
        try:
            result = await WazzupAdapter(key).send_message(payload)
            failure = None
        except IntegrationError as error:
            result, failure = None, error
        with SessionLocal() as db:
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": payload["channelId"] + ":" + payload["chatId"]},
            )
            msg = db.scalar(select(Message).where(Message.id == message_id).with_for_update())
            if not msg:
                return
            if result:
                external_id = result["messageId"]
                echo = db.scalar(
                    select(Message)
                    .where(
                        Message.channel_id == msg.channel_id,
                        Message.external_id == external_id,
                        Message.id != msg.id,
                    )
                    .with_for_update()
                )
                if echo:
                    msg.status = status_forward("sent", echo.status)
                    db.execute(
                        update(Message)
                        .where(Message.quoted_message_id == echo.id)
                        .values(quoted_message_id=msg.id)
                    )
                    db.delete(echo)
                    db.flush()
                else:
                    msg.status = status_forward(msg.status, "sent")
                receipt = db.get(DeliveryReceipt, external_id)
                if receipt:
                    msg.status = status_forward(msg.status, receipt.status)
                msg.external_id = external_id
                msg.sent_at = now()
                msg.error_code = receipt.error_code if receipt and msg.status == "error" else None
                msg.next_attempt_at = None
                conversation = db.scalar(
                    select(Conversation)
                    .where(Conversation.id == msg.conversation_id)
                    .with_for_update()
                )
                if conversation and conversation.updated_at <= msg.created_at:
                    conversation.state = "waiting_customer"
            elif failure:
                code = failure.code
                if code == "REPEATED_CRM_MESSAGE_ID":
                    msg.status, msg.error_code = "accepted", code
                    msg.sent_at, msg.next_attempt_at = now(), None
                elif failure.ambiguous:
                    elapsed = (now() - (msg.send_started_at or now())).total_seconds()
                    delay = [2, 8, 30][min(msg.send_attempts - 1, 2)]
                    if msg.send_attempts < 4 and elapsed + delay + 5 < 55:
                        msg.status, msg.error_code = "retry", code
                        msg.next_attempt_at = now() + timedelta(seconds=delay)
                    else:
                        msg.status, msg.error_code, msg.next_attempt_at = (
                            "uncertain",
                            "delivery_unknown",
                            None,
                        )
                else:
                    msg.status, msg.error_code, msg.next_attempt_at = "error", code, None
            db.commit()
        emit(conversations=[conversation_id])
    finally:
        if channel_key:
            # Token comparison means an expired lock cannot release a later sender's lock.
            await asyncio.sleep(1 + random.random() * 0.3)
            with contextlib.suppress(Exception):
                await ctx["redis"].eval(
                    "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0",
                    1,
                    channel_key,
                    token,
                )
