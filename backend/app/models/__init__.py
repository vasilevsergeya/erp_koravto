import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
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
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


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
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_mode: Mapped[str] = mapped_column(String(20), default="default")
    order_numbers: Mapped[list] = mapped_column(JSONB, default=list)
    onec_clients: Mapped[list] = mapped_column(JSONB, default=list)
    onec_phone: Mapped[str] = mapped_column(String(100), default="")
    onec_config: Mapped[str] = mapped_column(String(64), default="")
    onec_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onec_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onec_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    onec_error: Mapped[str | None] = mapped_column(String(40))
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    task_updated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
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
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    quoted_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    quoted_text: Mapped[str] = mapped_column(Text, default="")
    is_private_note: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    send_attempts: Mapped[int] = mapped_column(Integer, default=0)
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    storage_key: Mapped[str] = mapped_column(String(100))
    backend: Mapped[str] = mapped_column(String(10))
    filename: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    mime: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DeliveryReceipt(Base):
    __tablename__ = "delivery_receipts"
    external_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskType(Base):
    __tablename__ = "task_types"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    number: Mapped[int] = mapped_column(BigInteger, Identity(start=1001), unique=True)
    client_id: Mapped[str] = mapped_column(String(36), unique=True)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), index=True)
    status_id: Mapped[str] = mapped_column(String(36), index=True)
    type_id: Mapped[str] = mapped_column(ForeignKey("task_types.id"))
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), index=True)
    order_no: Mapped[str] = mapped_column(String(100), default="")
    part_no: Mapped[str] = mapped_column(String(100), default="")
    quantity: Mapped[int | None] = mapped_column(Integer)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    watchers: Mapped[list] = mapped_column(JSONB, default=list)
    attachment_ids: Mapped[list] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskComment(Base):
    __tablename__ = "task_comments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    mentions: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskHistory(Base):
    __tablename__ = "task_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    changes: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB)
    dedupe_key: Mapped[str | None] = mapped_column(String(180), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
