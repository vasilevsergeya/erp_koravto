"""Employee access and single-use invitations. Tokens never appear in request URLs."""

import re
import secrets
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.api.auth import admin_user, rate_limit
from app.core.config import config
from app.core.db import get_db
from app.core.security import hash_password, token_hash
from app.models import AuditEvent, LoginSession, User, UserAccessToken, now, uid
from app.services.notifications import DeliveryError, smtp_send, smtp_settings
from app.services.outbox import emit

router = APIRouter(prefix="/api", tags=["employees"])


def lock_access(db):
    # Serialize changes to the last administrator and token consumption.
    db.execute(text("SELECT pg_advisory_xact_lock(74291834)"))


def describe(user):
    return {
        k: getattr(user, k)
        for k in (
            "id",
            "email",
            "role",
            "active",
            "must_change_password",
            "created_at",
            "access_version",
        )
    }


class Invite(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["admin", "manager", "staff"] = "manager"
    send_email: bool = False

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("Invalid email")
        return value


class Access(BaseModel):
    version: int = Field(ge=1)
    role: Literal["admin", "manager", "staff"]
    active: bool


class Reset(BaseModel):
    version: int = Field(ge=1)
    send_email: bool = False


def issue(db, user, actor, send_email):
    raw = secrets.token_urlsafe(48)
    db.execute(delete(UserAccessToken).where(UserAccessToken.user_id == user.id))
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    user.password_hash = hash_password(secrets.token_urlsafe(48))
    user.must_change_password = True
    user.failed_logins = 0
    user.locked_until = None
    user.access_version += 1
    expiry = now() + timedelta(hours=24)
    db.add(UserAccessToken(token_hash=token_hash(raw), user_id=user.id, expires_at=expiry))
    db.add(AuditEvent(user_id=actor.id, action="user.access_link_issued", target=user.id))
    cfg = smtp_settings(db) if send_email else None
    db.commit()
    url = config().public_url + "/#access=" + raw
    delivery = "not_requested"
    if cfg:
        try:
            smtp_send(
                cfg,
                user.email,
                "Доступ к KORAVTO CRM",
                "Установите пароль по одноразовой ссылке (24 часа):\n" + url,
                delivery_id=uid(),
            )
            delivery = "sent"
        except DeliveryError as error:
            delivery = "uncertain" if error.uncertain else str(error)
    emit("team.changed")
    return {"user": describe(user), "url": url, "expires_at": expiry, "email_status": delivery}


@router.get("/admin/users")
def list_users(user=Depends(admin_user), db: Session = Depends(get_db)):
    return [describe(u) for u in db.scalars(select(User).order_by(User.email))]


@router.post("/admin/users")
def invite(body: Invite, actor=Depends(admin_user), db: Session = Depends(get_db)):
    lock_access(db)
    if db.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(409, "Сотрудник с таким email уже существует")
    user = User(email=body.email, role=body.role, password_hash="", access_version=1)
    db.add(user)
    db.flush()
    return issue(db, user, actor, body.send_email)


@router.patch("/admin/users/{user_id}")
def access(user_id: str, body: Access, actor=Depends(admin_user), db: Session = Depends(get_db)):
    lock_access(db)
    user = db.get(User, user_id, populate_existing=True)
    if not user:
        raise HTTPException(404, "Сотрудник не найден")
    if user.access_version != body.version:
        raise HTTPException(409, "Доступ сотрудника изменился. Обновите список")
    if user_id == actor.id and (not body.active or body.role != "admin"):
        raise HTTPException(409, "Свой доступ администратора изменяет другой администратор")
    if user.active and user.role == "admin" and (not body.active or body.role != "admin"):
        another = db.scalar(
            select(User.id).where(User.id != user_id, User.active, User.role == "admin")
        )
        if not another:
            raise HTTPException(409, "Нужен хотя бы один активный администратор")
    user.role, user.active = body.role, body.active
    user.access_version += 1
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.execute(delete(UserAccessToken).where(UserAccessToken.user_id == user.id))
    db.add(AuditEvent(user_id=actor.id, action="user.access_changed", target=user.id))
    db.commit()
    emit("team.changed")
    return describe(user)


@router.post("/admin/users/{user_id}/reset-password")
def reset(user_id: str, body: Reset, actor=Depends(admin_user), db: Session = Depends(get_db)):
    lock_access(db)
    user = db.get(User, user_id, populate_existing=True)
    if not user or not user.active:
        raise HTTPException(404, "Активный сотрудник не найден")
    if user.access_version != body.version:
        raise HTTPException(409, "Доступ сотрудника изменился. Обновите список")
    if user_id == actor.id:
        raise HTTPException(409, "Свой пароль измените в личных настройках")
    return issue(db, user, actor, body.send_email)


class Redeem(BaseModel):
    token: str = Field(min_length=32, max_length=100)
    password: str = Field(min_length=7, max_length=256)


@router.post("/auth/access")
def redeem(body: Redeem, request: Request, db: Session = Depends(get_db)):
    rate_limit("access:" + (request.client.host if request.client else "unknown"), 20, 900)
    lock_access(db)
    token = db.get(UserAccessToken, token_hash(body.token))
    user = db.get(User, token.user_id) if token else None
    if not token or token.expires_at <= now() or not user or not user.active:
        raise HTTPException(
            400, "Ссылка недействительна или истекла. Запросите новую у администратора"
        )
    user.password_hash = hash_password(body.password)
    user.must_change_password = False
    user.failed_logins = 0
    user.locked_until = None
    user.access_version += 1
    db.execute(delete(UserAccessToken).where(UserAccessToken.user_id == user.id))
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.add(AuditEvent(user_id=user.id, action="user.access_link_used", target=user.id))
    db.commit()
    return {"ok": True, "email": user.email}
