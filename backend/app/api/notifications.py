import asyncio
import hashlib
import json
import secrets
from typing import cast

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import admin_user, current_user, rate_limit
from app.core.db import get_db
from app.core.settings import get_setting, redis_client
from app.models import Notification, NotificationDelivery, now
from app.services.notifications import DeliveryError, smtp_send, smtp_settings, telegram_request

router = APIRouter(prefix="/api", tags=["notifications"])


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: bool = False
    telegram: bool = False
    browser: bool = False
    sound: bool = False


@router.get("/me/notifications")
def preferences(user=Depends(current_user), db: Session = Depends(get_db)):
    return {
        "preferences": Preferences.model_validate(
            {
                k: v
                for k, v in (user.notification_prefs or {}).items()
                if k in Preferences.model_fields
            }
        ).model_dump(),
        "email": user.email,
        "telegram_linked": bool(user.telegram_chat_id),
        "smtp_configured": bool(
            get_setting(db, "smtp.host", "") and get_setting(db, "smtp.from_email", "")
        ),
        "telegram_configured": bool(get_setting(db, "telegram.bot_token", "")),
    }


@router.put("/me/notifications")
def save_preferences(body: Preferences, user=Depends(current_user), db: Session = Depends(get_db)):
    if body.telegram and not user.telegram_chat_id:
        raise HTTPException(400, "Сначала подтвердите Telegram")
    old = user.notification_prefs or {}
    new = body.model_dump()
    for channel in ["email", "telegram"]:
        if new[channel]:
            new[channel + "_since"] = (
                old.get(channel + "_since") if old.get(channel) else now().isoformat()
            )
    user.notification_prefs = new
    db.commit()
    return preferences(user, db)


class TelegramLink(BaseModel):
    chat_id: str = Field(pattern=r"^[1-9][0-9]{3,18}$")


@router.post("/me/telegram/start")
async def link_start(body: TelegramLink, user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit("telegram-link:" + user.id, 3, 600)
    token = get_setting(db, "telegram.bot_token", "")
    db.rollback()
    code = str(secrets.randbelow(1000000)).zfill(6)
    try:
        await telegram_request(
            token,
            "sendMessage",
            {
                "chat_id": body.chat_id,
                "text": "KORAVTO CRM: код подтверждения "
                + code
                + ". Действует 10 минут. Не передавайте его другим.",
            },
        )
    except DeliveryError as e:
        raise HTTPException(502, "Telegram: " + str(e)) from None
    redis_client.setex(
        "crm:telegram-link:" + user.id,
        600,
        json.dumps({"chat_id": body.chat_id, "hash": hashlib.sha256(code.encode()).hexdigest()}),
    )
    return {"ok": True}


class TelegramCode(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/me/telegram/confirm")
def link_confirm(body: TelegramCode, user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit("telegram-code:" + user.id, 5, 600)
    key = "crm:telegram-link:" + user.id
    raw = redis_client.get(key)
    if not raw:
        raise HTTPException(400, "Код истёк")
    value = json.loads(cast(bytes, raw))
    if not secrets.compare_digest(value["hash"], hashlib.sha256(body.code.encode()).hexdigest()):
        raise HTTPException(400, "Неверный код")
    user.telegram_chat_id = value["chat_id"]
    db.commit()
    redis_client.delete(key)
    return preferences(user, db)


@router.delete("/me/telegram")
def unlink(user=Depends(current_user), db: Session = Depends(get_db)):
    user.telegram_chat_id = None
    user.notification_prefs = {**(user.notification_prefs or {}), "telegram": False}
    db.commit()
    return preferences(user, db)


@router.post("/notifications/check/{channel}")
async def check(channel: str, user=Depends(admin_user), db: Session = Depends(get_db)):
    rate_limit("notify-check:" + user.id, 5, 60)
    try:
        if channel == "email":
            cfg = smtp_settings(db)
            db.rollback()
            await asyncio.to_thread(smtp_send, cfg, "", "", "", check_only=True)
        elif channel == "telegram":
            token = get_setting(db, "telegram.bot_token", "")
            db.rollback()
            await telegram_request(token, "getMe")
        else:
            raise HTTPException(404, "Канал не найден")
    except DeliveryError as e:
        raise HTTPException(502, str(e)) from None
    return {"ok": True}


@router.get("/notifications/deliveries")
def deliveries(user=Depends(current_user), db: Session = Depends(get_db)):
    q = select(NotificationDelivery, Notification).join(
        Notification, Notification.id == NotificationDelivery.notification_id
    )
    if user.role != "admin":
        q = q.where(Notification.user_id == user.id)
    rows = db.execute(q.order_by(NotificationDelivery.created_at.desc()).limit(100)).all()
    return [
        {
            "id": d.id,
            "channel": d.channel,
            "state": d.state,
            "attempts": d.attempts,
            "error": d.error,
            "created_at": d.created_at,
            "delivered_at": d.delivered_at,
            "title": n.payload.get("title", ""),
        }
        for d, n in rows
    ]
