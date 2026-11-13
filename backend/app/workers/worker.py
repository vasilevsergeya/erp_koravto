import asyncio
import contextlib
import json
import logging
import mimetypes
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import phonenumbers
from arq.connections import RedisSettings
from sqlalchemy import delete, select, text, update

from app.core.config import config
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.core.settings import get_setting
from app.integrations.channels.wazzup import WazzupAdapter
from app.models import (
    Attachment,
    Conversation,
    DeliveryReceipt,
    LoginSession,
    Message,
    WebhookEvent,
    now,
)
from app.services.ai import queue_inbound, supersede
from app.services.customers import attach_conversation, structure_lock
from app.services.notifications import notify_inbound
from app.services.onec_customers import refresh_customers_loop
from app.services.outbox import deliver_message, status_forward
from app.services.storage import download_attachment, save_file
from app.services.tasks import overdue_reminders

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
    return status_forward(old, new)


def safe_error(item):
    error = item.get("error") or {}
    code = error.get("error", "") if isinstance(error, dict) else ""
    return code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,99}", code) else None


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
            if messages and not structure_lock(db, wait=False):
                # Do not block this event loop while another job downloads an attachment.
                # The durable pending event is retried by recovery; no attempt is consumed.
                event.next_attempt_at = now() + timedelta(seconds=1)
                db.commit()
                return
            for item in messages:
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": item["channelId"] + ":" + item["chatId"]},
                )
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
                    if existing.status == "error":
                        existing.error_code = safe_error(item)
                    existing.deleted = existing.deleted or bool(item.get("isDeleted"))
                    if existing.direction == "in" and (
                        item.get("isEdited") or item.get("isDeleted")
                    ):
                        supersede(db, existing.conversation_id, "message_changed")
                    changed.add(existing.conversation_id)
                    continue
                conversation = db.scalar(
                    select(Conversation)
                    .where(
                        Conversation.channel_id == item["channelId"],
                        Conversation.chat_id == item["chatId"],
                    )
                    .with_for_update()
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
                attach_conversation(db, conversation)
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
                    parsed_url = urlsplit(str(content_uri))
                    filename = (
                        parse_qs(parsed_url.query)
                        .get(
                            "filename",
                            [unquote(parsed_url.path.rsplit("/", 1)[-1]) or "attachment"],
                        )[0]
                        .replace("\\", "/")
                        .rsplit("/", 1)[-1][:200]
                    )
                    mime = mimetypes.guess_type(filename)[0] or {
                        "image": "image/jpeg",
                        "audio": "audio/ogg",
                        "video": "video/mp4",
                    }.get(message.kind, "application/octet-stream")
                    attachment = Attachment(
                        storage_key=key,
                        backend=backend,
                        filename=filename,
                        size=len(data),
                        mime=mime,
                    )
                    db.add(attachment)
                    db.flush()
                    message.attachment_id = attachment.id
                quote = item.get("quotedMessage") or {}
                message.quoted_text = (
                    str(quote.get("text", ""))[:1000] if isinstance(quote, dict) else ""
                )
                message.error_code = safe_error(item) if message.status == "error" else None
                if inbound:
                    conversation.unread_count += 1
                db.add(message)
                db.flush()
                if inbound:
                    queue_inbound(db, conversation, message)
                    notify_inbound(db, conversation, message)
                else:
                    supersede(db, conversation.id, "manual_reply")
                if (
                    timestamp >= conversation.updated_at
                    or conversation.updated_at > now() - timedelta(seconds=2)
                ):
                    conversation.state = "waiting_us" if inbound else "waiting_customer"
                    conversation.updated_at = timestamp
                changed.add(conversation.id)
            db.flush()
            for item in statuses:
                receipt = db.get(DeliveryReceipt, item["messageId"])
                status = str(item.get("status", ""))
                if status not in ("sent", "delivered", "read", "error"):
                    continue
                if receipt is None:
                    receipt = DeliveryReceipt(external_id=item["messageId"], status=status)
                    db.add(receipt)
                else:
                    receipt.status = advance_status(receipt.status, status)
                receipt.error_code = safe_error(item) if receipt.status == "error" else None
                receipt.updated_at = now()
                query = select(Message).where(Message.external_id == item["messageId"])
                if item.get("channelId"):
                    query = query.where(Message.channel_id == item["channelId"])
                matching = db.scalars(query).all()
                for message in matching:
                    message.status = advance_status(
                        message.status, str(item.get("status", message.status))
                    )
                    if message.status == "error":
                        message.error_code = safe_error(item)
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
    last_task_reminders = 0.0
    while True:
        try:
            await ctx["redis"].set("crm:worker:heartbeat", str(time.time()), ex=45)
            if time.time() - last_task_reminders > 60:
                last_task_reminders = time.time()
                try:
                    await asyncio.to_thread(overdue_reminders)
                except Exception as error:
                    log.warning("task.reminder_failed", extra={"error_type": type(error).__name__})
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
                sending = db.scalars(
                    select(Message).where(
                        Message.status == "sending", Message.next_attempt_at < now()
                    )
                ).all()
                for msg in sending:
                    msg.status, msg.error_code, msg.next_attempt_at = (
                        "uncertain",
                        "delivery_unknown",
                        None,
                    )
                outgoing = db.scalars(
                    select(Message.id)
                    .where(
                        Message.status.in_(["queued", "retry"]), Message.next_attempt_at <= now()
                    )
                    .distinct(Message.channel_id)
                    .order_by(Message.channel_id, Message.created_at, Message.id)
                    .limit(20)
                ).all()
                db.commit()
                if time.time() - last_cleanup > 3600:
                    days = get_setting(db, "inbox.auto_close_days", 14)
                    db.execute(
                        update(Conversation)
                        .where(
                            Conversation.state != "closed",
                            Conversation.updated_at < now() - timedelta(days=days),
                        )
                        .values(state="closed")
                    )
                    db.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
                    db.commit()
                    last_cleanup = time.time()
            for event_id in pending:
                await ctx["redis"].enqueue_job(
                    "process_webhook", event_id, _job_id="webhook:" + event_id
                )
            for message_id in outgoing:
                await ctx["redis"].enqueue_job(
                    "deliver_message", message_id, _job_id="outbound:" + message_id
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("worker.recovery_wait", extra={"error_type": type(error).__name__})
        await asyncio.sleep(1)


async def startup(ctx):
    configure_logging("worker")
    ctx["recovery_task"] = asyncio.create_task(recover_pending(ctx))
    ctx["onec_task"] = asyncio.create_task(refresh_customers_loop())


async def shutdown(ctx):
    ctx["onec_task"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ctx["onec_task"]
    ctx["recovery_task"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await ctx["recovery_task"]
    await ctx["redis"].delete("crm:worker:heartbeat")


class WorkerSettings:
    functions = [process_webhook, deliver_message]
    redis_settings = RedisSettings.from_dsn(config().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 2
    job_timeout = 120
    keep_result = 0
    health_check_interval = 10
    health_check_key = "crm:worker:arq_health"
    log_results = False
