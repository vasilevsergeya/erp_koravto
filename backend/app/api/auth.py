import secrets
from datetime import timedelta
from typing import cast

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from redis import RedisError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import (
    decrypt,
    encrypt,
    hash_password,
    token_hash,
    verify_password,
    verify_totp,
)
from app.core.settings import redis_client
from app.models import AuditEvent, LoginSession, User, now

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE = "__Host-crm_session"
DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def rate_limit(key: str, limit: int, seconds: int):
    script = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"
    try:
        count = int(cast(str, redis_client.eval(script, 1, "crm:limit:" + key, str(seconds))))
    except RedisError:
        raise HTTPException(503, "Сервис временно недоступен. Попробуйте позже.") from None
    if count > limit:
        raise HTTPException(
            429, "Слишком много попыток. Попробуйте позже.", headers={"Retry-After": str(seconds)}
        )


def auth_session(request: Request, db: Session = Depends(get_db)) -> tuple[LoginSession, User]:
    raw = request.cookies.get(COOKIE, "")
    session = db.get(LoginSession, token_hash(raw)) if raw else None
    if session is None or session.expires_at <= now():
        raise HTTPException(401, "Войдите в систему")
    user = db.get(User, session.user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Войдите в систему")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not secrets.compare_digest(session.csrf_token, request.headers.get("X-CSRF-Token", "")):
            raise HTTPException(403, "Обновите страницу и повторите действие")
    return session, user


def current_user(auth=Depends(auth_session)) -> User:
    session, user = auth
    if (
        user.must_change_password
        or not session.second_factor_verified
        or (user.role == "admin" and not user.totp_enabled)
    ):
        raise HTTPException(403, "Завершите настройку безопасности")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Доступ только для администратора")
    return user


def describe(session, user):
    return {
        "email": user.email,
        "role": user.role,
        "csrf_token": session.csrf_token,
        "must_change_password": user.must_change_password,
        "totp_enabled": user.totp_enabled,
        "totp_verified": session.second_factor_verified,
    }


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class PasswordInput(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class TOTPInput(BaseModel):
    code: str = Field(min_length=6, max_length=6)


@router.post("/login")
def login(body: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit("login:" + (request.client.host if request.client else "unknown"), 40, 900)
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()).with_for_update())
    valid = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not user.active:
        raise HTTPException(401, "Неверный email или пароль")
    if user.locked_until and user.locked_until > now():
        raise HTTPException(429, "Вход временно заблокирован. Повторите через 15 минут.")
    if not valid:
        user.failed_logins += 1
        if user.failed_logins >= 10:
            user.locked_until = now() + timedelta(minutes=15)
            user.failed_logins = 0
        db.commit()
        raise HTTPException(401, "Неверный email или пароль")
    # Successful password does not reset failed TOTP attempts for an enrolled admin.
    if not user.totp_enabled:
        user.failed_logins = 0
        user.locked_until = None
    raw = secrets.token_urlsafe(48)
    session = LoginSession(
        token_hash=token_hash(raw),
        user_id=user.id,
        csrf_token=secrets.token_hex(32),
        expires_at=now() + timedelta(minutes=15),
        second_factor_verified=False,
    )
    db.add(session)
    db.add(AuditEvent(user_id=user.id, action="auth.password_verified", target="session"))
    db.commit()
    response.set_cookie(
        COOKIE, raw, httponly=True, secure=True, samesite="lax", max_age=28800, path="/"
    )
    return describe(session, user)


@router.get("/me")
def me(auth=Depends(auth_session)):
    return describe(*auth)


@router.post("/password")
def change_password(body: PasswordInput, auth=Depends(auth_session), db: Session = Depends(get_db)):
    session, user = auth
    if not user.must_change_password and not session.second_factor_verified:
        raise HTTPException(403, "Подтвердите второй фактор")
    if verify_password(body.password, user.password_hash):
        raise HTTPException(400, "Новый пароль должен отличаться от временного")
    user.password_hash = hash_password(body.password)
    user.must_change_password = False
    db.execute(
        delete(LoginSession).where(
            LoginSession.user_id == user.id, LoginSession.token_hash != session.token_hash
        )
    )
    db.add(AuditEvent(user_id=user.id, action="auth.password_changed", target="user"))
    db.commit()
    return describe(session, user)


@router.post("/totp/setup")
def setup_totp(auth=Depends(auth_session), db: Session = Depends(get_db)):
    session, user = auth
    session = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == session.token_hash).with_for_update()
    )
    if session is None:
        raise HTTPException(401, "Войдите в систему")
    if user.must_change_password:
        raise HTTPException(403, "Сначала смените временный пароль")
    if user.totp_enabled:
        raise HTTPException(409, "Второй фактор уже настроен")
    if not session.pending_totp_secret:
        session.pending_totp_secret = encrypt(
            pyotp.random_base32(), "totp-pending:" + session.token_hash
        )
        db.commit()
    secret = decrypt(session.pending_totp_secret, "totp-pending:" + session.token_hash)
    return {
        "secret": secret,
        "uri": pyotp.TOTP(secret).provisioning_uri(user.email, issuer_name="KORAVTO CRM"),
    }


@router.post("/totp/verify")
def check_totp(body: TOTPInput, auth=Depends(auth_session), db: Session = Depends(get_db)):
    session, existing = auth
    rate_limit("totp:" + existing.id, 20, 900)
    user = db.scalar(select(User).where(User.id == existing.id).with_for_update())
    if user is None:
        raise HTTPException(401, "Войдите в систему")
    if user.must_change_password:
        raise HTTPException(403, "Сначала смените временный пароль")
    if user.locked_until and user.locked_until > now():
        raise HTTPException(429, "Вход временно заблокирован")
    if user.totp_enabled:
        if not user.totp_secret:
            raise HTTPException(403, "Обратитесь к администратору для восстановления доступа")
        secret = decrypt(user.totp_secret, "totp:" + user.id)
    elif session.pending_totp_secret:
        secret = decrypt(session.pending_totp_secret, "totp-pending:" + session.token_hash)
    else:
        raise HTTPException(400, "Начните настройку второго фактора")
    counter = verify_totp(secret, body.code, user.totp_counter)
    if counter is None:
        user.failed_logins += 1
        if user.failed_logins >= 10:
            user.locked_until = now() + timedelta(minutes=15)
            user.failed_logins = 0
        db.commit()
        raise HTTPException(400, "Неверный или уже использованный код")
    user.totp_secret = encrypt(secret, "totp:" + user.id)
    user.totp_enabled = True
    user.totp_counter = counter
    user.failed_logins = 0
    user.locked_until = None
    session.pending_totp_secret = None
    session.second_factor_verified = True
    session.expires_at = now() + timedelta(hours=8)
    db.add(AuditEvent(user_id=user.id, action="auth.totp_verified", target="session"))
    db.commit()
    return describe(session, user)


@router.post("/logout")
def logout(response: Response, auth=Depends(auth_session), db: Session = Depends(get_db)):
    session, _ = auth
    db.delete(session)
    db.commit()
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return {"ok": True}
