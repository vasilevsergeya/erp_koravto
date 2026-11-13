"""Bounded Wazzup checks, safe subscription recovery and deduplicated alerts."""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import config
from app.core.db import SessionLocal
from app.core.settings import direct_secret, get_setting, redis_client
from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter
from app.models import IntegrationHealth, Notification, User, WebhookEvent, now, uid
from app.services.ai_config import settings, working_time
from app.services.outbox import emit

WARNINGS = {
    "channel_inactive": "Один из каналов Wazzup неактивен. Проверьте подключение WhatsApp.",
    "no_channels": "Wazzup не вернул доступных каналов.",
    "webhook_silent": "Более 30 минут нет вебхуков в рабочее время. Подписка проверена; сообщений могло не быть.",
    "other_subscription": "В Wazzup настроен адрес другой CRM. Автоматическая замена не выполнялась.",
    "subscription_missing": "Подписка Wazzup отсутствует. Подключите её в настройках.",
    "subscription_repaired": "Подписка Wazzup восстановлена автоматически.",
    "backlog": "Входящие события ожидают обработки больше 5 минут.",
    "check_failed": "Проверка Wazzup не выполнена. Проверьте интеграцию в настройках.",
}


async def check_once():
    with SessionLocal() as db:
        key = get_setting(db, "wazzup.api_key", "")
        if not key:
            return
        token = direct_secret(db, "wazzup.webhook_token")
        expected = config().public_url + "/api/webhooks/wazzup/" + token
        previous = db.get(IntegrationHealth, "wazzup")
        previous_state = dict(previous.details) if previous else {}
        last_hook = db.scalar(
            select(func.max(WebhookEvent.created_at)).where(WebhookEvent.state != "test")
        )
        oldest = db.scalar(
            select(func.min(WebhookEvent.created_at)).where(
                WebhookEvent.state.in_(["pending", "error", "failed"])
            )
        )
        quiet = (last_hook is None or last_hook < now() - timedelta(minutes=30)) and working_time(
            settings(db), now()
        )
    warnings = []
    info: dict = {"channels": [], "owned": previous_state.get("owned", False)}
    if oldest and oldest < now() - timedelta(minutes=5):
        warnings.append("backlog")
    try:
        adapter = WazzupAdapter(key)
        channels = await adapter.list_channels()
        info["channels"] = channels
        if not channels:
            warnings.append("no_channels")
        elif any(c.get("state") != "active" for c in channels):
            warnings.append("channel_inactive")
        sub = await adapter.check_subscription()
        address = sub.get("webhooksUri", "")
        subscribed = address == expected and bool(
            sub.get("subscriptions", {}).get("messagesAndStatuses")
        )
        if subscribed:
            info["owned"] = True
        elif address and address != expected:
            warnings.append("other_subscription")
        elif token and (address == expected or info["owned"]):
            # Only repair an owned subscription. Never take over a different CRM.
            await adapter.subscribe_webhooks(expected)
            sub = await adapter.check_subscription()
            subscribed = sub.get("webhooksUri") == expected and bool(
                sub.get("subscriptions", {}).get("messagesAndStatuses")
            )
            warnings.append("subscription_repaired" if subscribed else "subscription_missing")
        else:
            warnings.append("subscription_missing")
        info["subscribed"] = subscribed
        if quiet and any(c.get("state") == "active" for c in channels):
            warnings.append("webhook_silent")
    except IntegrationError:
        warnings.append("check_failed")
        info["channels"] = previous_state.get("channels", [])
    with SessionLocal() as db:
        row = db.get(IntegrationHealth, "wazzup")
        if row is None:
            row = IntegrationHealth(name="wazzup")
            db.add(row)
        prior_alerts = previous_state.get("alerted", {})
        alerts = {}
        for warning in warnings:
            last = prior_alerts.get(warning)
            alerts[warning] = last or now().isoformat()
            if last and datetime.fromisoformat(last) > now() - timedelta(hours=6):
                continue
            alerts[warning] = now().isoformat()
            for user_id in db.scalars(select(User.id).where(User.active, User.role == "admin")):
                db.execute(
                    insert(Notification)
                    .values(
                        id=uid(),
                        user_id=user_id,
                        kind="integration.alert",
                        payload={"title": "Wazzup · " + warning, "text": WARNINGS[warning]},
                        dedupe_key="monitor:"
                        + warning
                        + ":"
                        + user_id
                        + ":"
                        + str(int(now().timestamp()) // 21600),
                        created_at=now(),
                    )
                    .on_conflict_do_nothing(index_elements=["dedupe_key"])
                )
        row.details = {
            **info,
            "warnings": warnings,
            "alerted": alerts,
            "last_webhook_at": last_hook.isoformat() if last_hook else None,
        }
        row.checked_at = now()
        db.commit()
    emit("integrations.changed")
    emit("notifications.changed")


async def monitor_loop():
    # Single owner per interval across worker restarts/replicas. DB remains authoritative.
    while True:
        try:
            if redis_client.set("crm:monitor:interval", "1", nx=True, ex=300):
                async with asyncio.timeout(110):
                    await check_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.getLogger("crm").warning(
                "monitor.failed", extra={"error_type": type(error).__name__}
            )
        await asyncio.sleep(30)
