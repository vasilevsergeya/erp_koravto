import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr
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
from app.integrations.gmail import GmailError, credentials
from app.integrations.gmail_imap import GmailImapMailbox, app_password, imap_credentials
from app.integrations.onec import OneCError, customer_endpoint
from app.models import AuditEvent, User, WebhookEvent
from app.services.parts_pricing import FORMULA_KEY, SaleFormula

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
    if key == "gmail.credentials" and body.value:
        body.value = str(body.value).strip()
        try:
            credentials(body.value)
        except GmailError:
            raise HTTPException(400, "Укажите OAuth access token или JSON с refresh_token, client_id и client_secret.") from None
    if key == "smtp.security" and body.value not in ("starttls", "ssl"):
        raise HTTPException(400, "Выберите starttls или ssl")
    if key in ("smtp.host", "smtp.username", "smtp.from_email") and any(
        c in str(body.value) for c in "\r\n"
    ):
        raise HTTPException(400, "Недопустимые символы")
    if (
        key == "smtp.host"
        and body.value
        and ("/" in str(body.value) or ":" in str(body.value) or " " in str(body.value))
    ):
        raise HTTPException(400, "Укажите имя SMTP-сервера без протокола и порта")
    if key == FORMULA_KEY:
        try:
            SaleFormula.parse(str(body.value))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
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


class GmailConnectionInput(BaseModel):
    email: str = Field(max_length=254)
    password: SecretStr = Field(max_length=80)


@router.get("/gmail/connection")
def gmail_connection(user=Depends(admin_user), db: Session = Depends(get_db)):
    saved = direct_secret(db, "gmail.credentials")
    mailbox = imap_credentials(saved)
    return {
        "email": mailbox.email if mailbox else "",
        "mode": "imap" if mailbox else ("oauth" if saved else ""),
    }


@router.put("/gmail/connection")
async def connect_gmail(
    body: GmailConnectionInput, user=Depends(admin_user), db: Session = Depends(get_db)
):
    email = body.email.strip()
    if not re.fullmatch(r"[a-zA-Z0-9._+%-]+@[a-zA-Z0-9-]+(?:[.][a-zA-Z0-9-]+)+", email):
        raise HTTPException(400, "Введите полный адрес почтового ящика Google.")
    try:
        password = app_password(body.password.get_secret_value())
    except GmailError:
        raise HTTPException(
            400, "Пароль приложения должен содержать 16 символов. Пробелы между группами допустимы."
        ) from None
    try:
        await GmailImapMailbox(email, password).check()
    except GmailError as exc:
        if str(exc) == "imap_login":
            raise HTTPException(
                400, "Google отклонил вход. Проверьте адрес ящика и пароль приложения."
            ) from None
        if str(exc) == "all_mail_hidden":
            raise HTTPException(
                400, "В настройках ярлыков Gmail включите показ папки «Вся почта» в IMAP."
            ) from None
        raise HTTPException(
            502,
            "Не удалось подключиться к imap.gmail.com. Проверьте доступ к IMAP и повторите попытку.",
        ) from None
    put_setting(
        db,
        "gmail.credentials",
        json.dumps({"email": email, "app_password": password}),
        True,
        user.id,
    )
    db.commit()
    invalidate_settings()
    return {"email": email, "mode": "imap"}


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
