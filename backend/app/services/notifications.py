"""Opt-in employee notifications with durable delivery state, no historical mail flood."""

import asyncio
import logging
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate

import aiohttp
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import config
from app.core.db import SessionLocal
from app.core.settings import get_setting
from app.models import Notification, NotificationDelivery, User, now, uid
from app.services.outbox import emit


class DeliveryError(Exception):
    def __init__(self, code, retry=False, uncertain=False):
        super().__init__(code)
        self.retry = retry
        self.uncertain = uncertain


def smtp_settings(db):
    return {
        k: get_setting(db, "smtp." + k, d)
        for k, d in [
            ("host", ""),
            ("port", 587),
            ("username", ""),
            ("password", ""),
            ("from_email", ""),
            ("security", "starttls"),
        ]
    }


def smtp_send(cfg, to, subject, body, delivery_id=None, check_only=False):
    if not cfg["host"] or not cfg["from_email"]:
        raise DeliveryError("smtp_not_configured")
    stage = "connect"
    client: smtplib.SMTP | None = None
    try:
        if cfg["security"] == "ssl":
            client = smtplib.SMTP_SSL(
                cfg["host"], int(cfg["port"]), timeout=10, context=ssl.create_default_context()
            )
        else:
            client = smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=10)
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if cfg["username"]:
            client.login(cfg["username"], cfg["password"])
        if check_only:
            client.noop()
            return
        message = EmailMessage()
        message["From"] = cfg["from_email"]
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = (
            "<crm-" + delivery_id + "@" + config().public_url.split("://")[1] + ">"
        )
        message.set_content(body)
        stage = "send"
        client.send_message(message)
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError("smtp_auth_failed") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise DeliveryError("smtp_rejected") from None
    except (smtplib.SMTPException, OSError):
        raise DeliveryError(
            "smtp_unavailable", retry=stage == "connect", uncertain=stage == "send"
        ) from None
    finally:
        if client:
            try:
                client.close()
            except OSError:
                pass


async def telegram_request(token, method, body=None):
    if not token:
        raise DeliveryError("telegram_not_configured")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
            async with client.post(
                "https://api.telegram.org/bot" + token + "/" + method,
                json=body or {},
                allow_redirects=False,
            ) as response:
                result = await response.json()
                if response.status == 429:
                    raise DeliveryError("telegram_rate_limit", retry=True)
                if response.status >= 500:
                    raise DeliveryError("telegram_unavailable", uncertain=method == "sendMessage")
                if response.status != 200 or not result.get("ok"):
                    raise DeliveryError("telegram_rejected")
                return result["result"]
    except (aiohttp.ClientError, TimeoutError, ValueError):
        raise DeliveryError("telegram_unavailable", uncertain=method == "sendMessage") from None


def notification_text(notification):
    p = notification.payload
    title = str(p.get("title") or "Уведомление CRM")[:200]
    link = config().public_url
    if p.get("task_id"):
        link += "/tasks/" + p["task_id"]
    elif p.get("conversation_id"):
        link += "/?conversation=" + p["conversation_id"]
    return title, str(p.get("text") or "")[:2000] + "\n\n" + link


def prepare_deliveries():
    with SessionLocal() as db:
        # A crashed delivery may have succeeded externally. Never blindly duplicate it.
        db.execute(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.state == "sending",
                NotificationDelivery.next_attempt_at < now(),
            )
            .values(state="uncertain", error="delivery_unknown")
        )
        notifications = db.execute(
            select(Notification, User)
            .join(User, User.id == Notification.user_id)
            .where(User.active, Notification.created_at > now() - timedelta(days=3))
            .order_by(Notification.created_at.desc())
            .limit(1000)
        ).all()
        for n, user in notifications:
            prefs = user.notification_prefs or {}
            for channel in ["email", "telegram"]:
                if not prefs.get(channel):
                    continue
                enabled = prefs.get(channel + "_since")
                if not enabled or n.created_at < datetime.fromisoformat(enabled):
                    continue
                if channel == "telegram" and not user.telegram_chat_id:
                    continue
                db.execute(
                    insert(NotificationDelivery)
                    .values(
                        id=uid(),
                        notification_id=n.id,
                        channel=channel,
                        state="pending",
                        attempts=0,
                        next_attempt_at=now(),
                        created_at=now(),
                    )
                    .on_conflict_do_nothing(index_elements=["notification_id", "channel"])
                )
        db.commit()
        return db.scalars(
            select(NotificationDelivery.id)
            .where(
                NotificationDelivery.state.in_(["pending", "retry"]),
                NotificationDelivery.next_attempt_at <= now(),
            )
            .order_by(NotificationDelivery.created_at)
            .limit(20)
        ).all()


async def deliver_notification(delivery_id):
    with SessionLocal() as db:
        row = db.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .with_for_update(skip_locked=True)
        )
        if not row or row.state not in ("pending", "retry") or row.next_attempt_at > now():
            return
        notification = db.get(Notification, row.notification_id)
        assert notification is not None
        user = db.get(User, notification.user_id)
        assert user is not None
        prefs = user.notification_prefs or {}
        if (
            not user.active
            or not prefs.get(row.channel)
            or (row.channel == "telegram" and not user.telegram_chat_id)
        ):
            row.state = "cancelled"
            db.commit()
            return
        cfg = (
            smtp_settings(db)
            if row.channel == "email"
            else get_setting(db, "telegram.bot_token", "")
        )
        configured = bool(cfg["host"] and cfg["from_email"]) if isinstance(cfg, dict) else bool(cfg)
        if not configured:
            row.error = row.channel + "_not_configured"
            row.next_attempt_at = now() + timedelta(minutes=5)
            db.commit()
            return
        title, body = notification_text(notification)
        recipient = user.email if row.channel == "email" else user.telegram_chat_id
        channel = row.channel
        row.state = "sending"
        row.attempts += 1
        row.next_attempt_at = now() + timedelta(minutes=2)
        db.commit()
    error = None
    try:
        if channel == "email":
            await asyncio.to_thread(
                smtp_send, cfg, recipient, "KORAVTO · " + title, body, delivery_id
            )
        else:
            await telegram_request(
                cfg,
                "sendMessage",
                {
                    "chat_id": recipient,
                    "text": title + "\n" + body,
                    "link_preview_options": {"is_disabled": True},
                },
            )
    except DeliveryError as e:
        error = e
    except Exception:
        error = DeliveryError("delivery_unknown", uncertain=True)
    with SessionLocal() as db:
        row = db.get(NotificationDelivery, delivery_id)
        assert row is not None
        if not error:
            row.state = "sent"
            row.delivered_at = now()
            row.error = None
        else:
            row.error = str(error)
            row.state = (
                "uncertain"
                if error.uncertain
                else "retry"
                if error.retry and row.attempts < 4
                else "failed"
            )
            row.next_attempt_at = now() + timedelta(seconds=60 * row.attempts)
        db.commit()
    emit("notifications.changed")


async def notification_loop():
    while True:
        try:
            ids = await asyncio.to_thread(prepare_deliveries)
            for ident in ids:
                await deliver_notification(ident)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.getLogger("crm").warning(
                "notifications.recovery.failed", extra={"error_type": type(error).__name__}
            )
        await asyncio.sleep(5)


def notify_inbound(db, conversation, message):
    query = select(User.id).where(User.active, User.role.in_(["admin", "manager"]))
    if conversation.assignee_id:
        query = query.where(User.id == conversation.assignee_id)
    for user_id in db.scalars(query):
        db.execute(
            insert(Notification)
            .values(
                id=uid(),
                user_id=user_id,
                kind="inbox.message",
                payload={
                    "conversation_id": conversation.id,
                    "title": "Новое сообщение",
                    "text": "Откройте диалог в CRM",
                },
                dedupe_key="inbound:" + message.id + ":" + user_id,
                created_at=now(),
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
