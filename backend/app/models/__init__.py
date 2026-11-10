import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="admin")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_counter: Mapped[int] = mapped_column(BigInteger, default=-1)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    second_factor_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_totp_secret: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Board(Base):
    __tablename__ = "boards"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    position: Mapped[int] = mapped_column(Integer)
    statuses: Mapped[list] = mapped_column(JSONB)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    payload: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(100))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("channel_id", "chat_id", name="uq_conversation_channel_chat"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    channel_id: Mapped[str] = mapped_column(String(100))
    chat_id: Mapped[str] = mapped_column(String(200))
    chat_type: Mapped[str] = mapped_column(String(40), default="whatsapp")
    name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    state: Mapped[str] = mapped_column(String(30), default="waiting_us")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("channel_id", "external_id", name="uq_message_channel_external"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(100), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    author_type: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(40), default="text")
    status: Mapped[str] = mapped_column(String(40), default="inbound")
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    history: Mapped[list] = mapped_column(JSONB, default=list)
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    storage_key: Mapped[str] = mapped_column(String(100))
    backend: Mapped[str] = mapped_column(String(10))
    filename: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
