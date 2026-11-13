from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import admin_user
from app.core.config import config
from app.core.db import get_db
from app.core.settings import (
    SETTING_SCHEMA,
    direct_secret,
    get_setting,
    invalidate_settings,
    masked_settings,
    put_setting,
)
from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter
from app.integrations.onec import OneCError, customer_endpoint
from app.models import AuditEvent, User, WebhookEvent

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingInput(BaseModel):
    value: str | int = Field()


class SubscribeInput(BaseModel):
    replace_existing: bool = False


@router.get("")
def read_settings(user=Depends(admin_user), db: Session = Depends(get_db)):
    return masked_settings(db)


@router.put("/{key}")
def update_setting(
    key: str, body: SettingInput, user: User = Depends(admin_user), db: Session = Depends(get_db)
):
    if key not in SETTING_SCHEMA:
        raise HTTPException(404, "Настройка не найдена")
    kind, secret, _ = SETTING_SCHEMA[key]
    minimum, maximum = (0, 23) if key == "tasks.reminder_hour" else (1, 65535)
    if kind == "integer" and (type(body.value) is not int or not minimum <= body.value <= maximum):
        raise HTTPException(400, "Укажите целое число от 1 до 65535")
    if kind == "string" and (not isinstance(body.value, str) or len(body.value) > 8192):
        raise HTTPException(400, "Некорректное значение")
    if key == "smtp.security" and body.value not in ("starttls", "ssl"):
        raise HTTPException(400, "Выберите starttls или ssl")
    if key in ("smtp.host", "smtp.username", "smtp.from_email") and any(c in str(body.value) for c in "\r\n"):
        raise HTTPException(400, "Недопустимые символы")
    if key == "smtp.host" and body.value and ("/" in str(body.value) or ":" in str(body.value) or " " in str(body.value)):
        raise HTTPException(400, "Укажите имя SMTP-сервера без протокола и порта")
    if key == "tasks.timezone":
        try:
            ZoneInfo(str(body.value))
        except (ValueError, ZoneInfoNotFoundError):
            raise HTTPException(400, "Неизвестный часовой пояс") from None
    if key == "tasks.workdays":
        days = str(body.value).replace(" ", "").split(",")
        if not days or any(day not in list("1234567") for day in days):
            raise HTTPException(400, "Укажите номера рабочих дней через запятую: 1–7")
        body.value = ",".join(sorted(set(days)))
    if key == "onec.base_url" and body.value:
        try:
            body.value = customer_endpoint(str(body.value))
        except OneCError:
            raise HTTPException(
                400, "Укажите HTTP(S)-адрес публикации 1С без пароля и параметров"
            ) from None
    put_setting(db, key, body.value, secret, user.id)
    db.commit()
    invalidate_settings()
    return next(item for item in masked_settings(db) if item["key"] == key)


def adapter(db):
    return WazzupAdapter(get_setting(db, "wazzup.api_key", ""))


def callback_url(db):
    token = direct_secret(db, "wazzup.webhook_token")
    if not token:
        raise HTTPException(503, "Выполните первоначальную настройку системы")
    return config().public_url + "/api/webhooks/wazzup/" + token


@router.get("/wazzup/status")
def webhook_status(user=Depends(admin_user), db: Session = Depends(get_db)):
    last_test = db.scalar(
        select(func.max(WebhookEvent.created_at)).where(WebhookEvent.state == "test")
    )
    last_message = db.scalar(
        select(func.max(WebhookEvent.created_at)).where(WebhookEvent.state != "test")
    )
    pending = db.scalar(
        select(func.count())
        .select_from(WebhookEvent)
        .where(WebhookEvent.state.in_(["pending", "error"]))
    )
    recent = db.scalars(
        select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(50)
    ).all()
    return {
        "last_test_at": last_test,
        "last_webhook_at": last_message,
        "pending": pending,
        "events": [
            {
                "id": e.id,
                "state": e.state,
                "attempts": e.attempts,
                "created_at": e.created_at,
                "error": e.last_error,
            }
            for e in recent
        ],
    }


@router.post("/wazzup/check")
async def check_wazzup(user=Depends(admin_user), db: Session = Depends(get_db)):
    try:
        client = adapter(db)
        channels = await client.list_channels()
        subscription = await client.check_subscription()
        expected = callback_url(db)
        existing = subscription.get("webhooksUri", "")
        return {
            "channels": channels,
            "subscribed": existing == expected
            and bool(subscription.get("subscriptions", {}).get("messagesAndStatuses")),
            "has_other_subscription": bool(existing and existing != expected),
        }
    except IntegrationError as e:
        raise HTTPException(502, "Wazzup: " + e.code) from None


@router.post("/wazzup/subscribe")
async def subscribe(
    body: SubscribeInput, user: User = Depends(admin_user), db: Session = Depends(get_db)
):
    try:
        client = adapter(db)
        expected = callback_url(db)
        existing = (await client.check_subscription()).get("webhooksUri", "")
        if existing and existing != expected and not body.replace_existing:
            raise HTTPException(
                409,
                "В Wazzup уже настроена другая CRM. Замена подписки остановит доставку вебхуков в неё.",
            )
        # Release transaction before Wazzup calls us synchronously with a test webhook.
        db.commit()
        await client.subscribe_webhooks(expected)
        verified = await client.check_subscription()
        active = verified.get("webhooksUri") == expected and bool(
            verified.get("subscriptions", {}).get("messagesAndStatuses")
        )
        db.add(AuditEvent(user_id=user.id, action="wazzup.subscribed", target="wazzup"))
        db.commit()
        return {"ok": active}
    except IntegrationError as e:
        raise HTTPException(502, "Wazzup: " + e.code) from None
