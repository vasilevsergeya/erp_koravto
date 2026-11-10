import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import phonenumbers
from arq.connections import RedisSettings
from sqlalchemy import delete, select

from app.core.config import config
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.integrations.channels.wazzup import WazzupAdapter
from app.models import Attachment, Conversation, LoginSession, Message, WebhookEvent, now
from app.services.storage import download_attachment, save_file

log = logging.getLogger("crm")


def normalized_phone(chat_id: str) -> str:
    try:
        phone = phonenumbers.parse("+" + chat_id.lstrip("+"), None)
        if phonenumbers.is_valid_number(phone):
            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return chat_id[:100]


def message_time(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return now()


def advance_status(old: str, new: str) -> str:
    levels = {"sent": 1, "delivered": 2, "read": 3}
    if old in levels and new in levels and levels[new] < levels[old]:
        return old
    return new[:40]


async def process_webhook(ctx, event_id: str):
    changed = set()
    try:
        with SessionLocal() as db:
            event = db.scalar(
                select(WebhookEvent)
                .where(WebhookEvent.id == event_id)
                .with_for_update(skip_locked=True)
            )
            if event is None or event.state not in ("pending", "error"):
                return
            messages, statuses = WazzupAdapter.parse_webhook(event.payload)
            for item in messages:
                existing = db.scalar(
                    select(Message).where(
                        Message.channel_id == item["channelId"],
                        Message.external_id == item["messageId"],
                    )
                )
                if existing:
                    existing.status = advance_status(
                        existing.status, str(item.get("status", existing.status))
                    )
                    if item.get("isEdited") and item.get("text") is not None:
                        existing.history = [
                            *existing.history,
                            {"body": existing.body, "at": now().isoformat()},
                        ]
                        existing.body = str(item["text"])[:100000]
                        existing.edited = True
                    existing.deleted = existing.deleted or bool(item.get("isDeleted"))
                    changed.add(existing.conversation_id)
                    continue
                conversation = db.scalar(
                    select(Conversation).where(
                        Conversation.channel_id == item["channelId"],
                        Conversation.chat_id == item["chatId"],
                    )
                )
                if conversation is None:
                    contact = item.get("contact") or {}
                    conversation = Conversation(
                        channel_id=item["channelId"],
                        chat_id=item["chatId"],
                        chat_type=str(item.get("chatType", "whatsapp"))[:40],
                        name=str(contact.get("name", ""))[:200],
                        phone=normalized_phone(item["chatId"]),
                    )
                    db.add(conversation)
                    db.flush()
                inbound = item.get("status") == "inbound" and not item.get("isEcho")
                timestamp = message_time(item.get("dateTime"))
                message = Message(
                    conversation_id=conversation.id,
                    channel_id=item["channelId"],
                    external_id=item["messageId"],
                    direction="in" if inbound else "out",
                    author_type="customer" if inbound else "employee_phone",
                    body=str(item.get("text") or "")[:100000],
                    kind=str(item.get("type", "text"))[:40],
                    status=str(item.get("status", "inbound"))[:40],
                    created_at=timestamp,
                    edited=bool(item.get("isEdited")),
                    deleted=bool(item.get("isDeleted")),
                )
                content_uri = item.get("contentUri")
                if content_uri:
                    data = await download_attachment(str(content_uri))
                    key, backend = await asyncio.to_thread(save_file, data)
                    attachment = Attachment(
                        storage_key=key, backend=backend, filename="attachment", size=len(data)
                    )
                    db.add(attachment)
                    db.flush()
                    message.attachment_id = attachment.id
                db.add(message)
                if (
                    timestamp >= conversation.updated_at
                    or conversation.updated_at > now() - timedelta(seconds=2)
                ):
                    conversation.state = "waiting_us" if inbound else "waiting_customer"
                    conversation.updated_at = timestamp
                changed.add(conversation.id)
            db.flush()
            for item in statuses:
                query = select(Message).where(Message.external_id == item["messageId"])
                if item.get("channelId"):
                    query = query.where(Message.channel_id == item["channelId"])
                matching = db.scalars(query).all()
                for message in matching:
                    message.status = advance_status(
                        message.status, str(item.get("status", message.status))
                    )
                    changed.add(message.conversation_id)
            event.state = "processed"
            event.attempts += 1
            event.last_error = None
            event.processed_at = now()
            db.commit()
    except Exception as error:
        with SessionLocal() as db:
            event = db.get(WebhookEvent, event_id)
            if event:
                event.attempts += 1
                event.state = "error" if event.attempts < 10 else "failed"
                event.last_error = type(error).__name__[:100]
                event.next_attempt_at = now() + timedelta(seconds=min(300, 2**event.attempts))
                db.commit()
        log.warning(
            "webhook.failed", extra={"event_id": event_id, "error_type": type(error).__name__}
        )
        return
    if changed:
        await ctx["redis"].publish(
            "crm:events", json.dumps({"type": "messages.changed", "conversations": list(changed)})
        )
    log.info("webhook.processed", extra={"event_id": event_id})


async def recover_pending(ctx):
    last_cleanup = 0.0
    while True:
        try:
            await ctx["redis"].set("crm:worker:heartbeat", str(time.time()), ex=45)
            with SessionLocal() as db:
                pending = db.scalars(
                    select(WebhookEvent.id)
                    .where(
                        WebhookEvent.state.in_(["pending", "error"]),
                        WebhookEvent.next_attempt_at <= now(),
                    )
                    .order_by(WebhookEvent.created_at)
                    .limit(100)
                ).all()
                if time.time() - last_cleanup > 3600:
                    db.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
                    db.commit()
                    last_cleanup = time.time()
            for event_id in pending:
                await ctx["redis"].enqueue_job(
                    "process_webhook", event_id, _job_id="webhook:" + event_id
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("worker.recovery_wait", extra={"error_type": type(error).__name__})
        await asyncio.sleep(1)


async def startup(ctx):
    configure_logging("worker")
    ctx["recovery_task"] = asyncio.create_task(recover_pending(ctx))


async def shutdown(ctx):
    ctx["recovery_task"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ctx["recovery_task"]
    await ctx["redis"].delete("crm:worker:heartbeat")


class WorkerSettings:
    functions = [process_webhook]
    redis_settings = RedisSettings.from_dsn(config().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 2
    job_timeout = 120
    keep_result = 0
    health_check_interval = 10
    health_check_key = "crm:worker:arq_health"
    log_results = False
