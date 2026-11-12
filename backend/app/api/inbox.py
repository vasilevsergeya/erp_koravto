import mimetypes
import uuid
from datetime import timedelta
from typing import Literal
from urllib.parse import quote

import phonenumbers
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from itsdangerous import BadSignature, SignatureExpired
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.core.db import get_db
from app.core.security import file_signer
from app.core.settings import get_setting
from app.integrations.channels.wazzup import IntegrationError, WazzupAdapter
from app.models import Attachment, AuditEvent, Board, Conversation, Message, User, now
from app.services.outbox import emit, error_text
from app.services.storage import MAX_FILE_SIZE, read_file, save_file

router = APIRouter(prefix="/api", tags=["inbox"])


def writer(user=Depends(current_user)):
    if user.role not in ("admin", "manager"):
        raise HTTPException(403, "Отправка сообщений доступна менеджерам и администраторам")
    return user


def locked_conversation(db, conversation_id):
    row = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id).with_for_update()
    )
    if not row:
        raise HTTPException(404, "Диалог не найден")
    return row


def describe_conversation(c, db):
    last = db.scalar(
        select(Message)
        .where(Message.conversation_id == c.id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    return {
        "id": c.id,
        "name": c.name,
        "phone": c.phone,
        "chat_type": c.chat_type,
        "channel_id": c.channel_id,
        "state": c.state,
        "updated_at": c.updated_at,
        "assignee_id": c.assignee_id,
        "needs_human": c.needs_human,
        "ai_mode": c.ai_mode,
        "order_numbers": c.order_numbers,
        "unread_count": c.unread_count,
        "last_message": last.body[:120] if last else "",
        "last_status": last.status if last else "",
    }


def describe_message(m, db):
    author = db.get(User, m.author_id) if m.author_id else None
    attachment = db.get(Attachment, m.attachment_id) if m.attachment_id else None
    return {
        "id": m.id,
        "direction": m.direction,
        "author_type": m.author_type,
        "author_name": author.email if author else None,
        "text": m.body,
        "kind": m.kind,
        "status": m.status,
        "edited": m.edited,
        "deleted": m.deleted,
        "created_at": m.created_at,
        "is_private_note": m.is_private_note,
        "error_code": m.error_code,
        "error_text": error_text(m.error_code),
        "quoted_message_id": m.quoted_message_id,
        "quoted_text": m.quoted_text,
        "attachment_name": attachment.filename if attachment else None,
        "attachment_mime": attachment.mime if attachment else None,
        "attachment_url": "/api/files/" + file_signer().dumps(m.attachment_id)
        if attachment
        else None,
    }


@router.get("/conversations")
def conversations(
    user=Depends(current_user),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200),
    state: str = "",
    assignee: str = "",
    channel: str = "",
    needs_human: bool = False,
):
    query = select(Conversation)
    if state:
        query = query.where(Conversation.state == state)
    if assignee:
        query = query.where(Conversation.assignee_id == (user.id if assignee == "me" else assignee))
    if channel:
        query = query.where(Conversation.channel_id == channel)
    if needs_human:
        query = query.where(Conversation.needs_human.is_(True))
    if q.strip():
        q = q.strip()
        fulltext = select(Message.conversation_id).where(
            func.to_tsvector(text("'simple'"), Message.body).op("@@")(
                func.plainto_tsquery(text("'simple'"), q)
            )
        )
        digits = "".join(ch for ch in q if ch.isascii() and ch.isdigit())
        conditions = [
            Conversation.name.icontains(q, autoescape=True),
            Conversation.order_numbers.op("@>")([q]),
            Conversation.id.in_(fulltext),
        ]
        if digits:
            conditions.append(Conversation.phone.contains(digits))
        query = query.where(or_(*conditions))
    rows = db.scalars(
        query.order_by(
            case((Conversation.state == "waiting_us", 0), else_=1),
            Conversation.updated_at.desc(),
            Conversation.id,
        )
        .offset(offset)
        .limit(50)
    )
    return [describe_conversation(c, db) for c in rows]


@router.get("/conversations/counts")
def counts(user=Depends(current_user), db: Session = Depends(get_db)):
    return {
        "all": db.scalar(select(func.count()).select_from(Conversation)),
        "waiting_us": db.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.state == "waiting_us")
        ),
        "mine": db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.assignee_id == user.id)
        ),
        "human": db.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.needs_human)
        ),
        "closed": db.scalar(
            select(func.count()).select_from(Conversation).where(Conversation.state == "closed")
        ),
    }


@router.get("/conversations/{conversation_id}")
def conversation_detail(
    conversation_id: str, user=Depends(current_user), db: Session = Depends(get_db)
):
    row = db.get(Conversation, conversation_id)
    if not row:
        raise HTTPException(404, "Диалог не найден")
    return describe_conversation(row, db)


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: str,
    offset: int = Query(0, ge=0),
    before: str | None = None,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Message).where(Message.conversation_id == conversation_id)
    if before:
        cursor = db.get(Message, before)
        if not cursor or cursor.conversation_id != conversation_id:
            raise HTTPException(400, "Некорректный курсор")
        query = query.where(
            or_(
                Message.created_at < cursor.created_at,
                (Message.created_at == cursor.created_at) & (Message.id < cursor.id),
            )
        )
    rows = db.scalars(
        query.order_by(Message.created_at.desc(), Message.id.desc()).offset(offset).limit(50)
    ).all()
    return [describe_message(m, db) for m in reversed(rows)]


class MessageInput(BaseModel):
    client_id: uuid.UUID
    text: str = Field("", max_length=10000)
    attachment_id: str | None = None
    quoted_message_id: str | None = None
    is_private_note: bool = False


class NewConversation(MessageInput):
    phone: str = Field(min_length=5, max_length=100)
    country: str = Field("RU", min_length=2, max_length=2)
    channel_id: str = Field("", max_length=100)
    name: str = Field("", max_length=200)


def enqueue(db, conversation, body, user):
    message_id = str(body.client_id)
    # A stable client UUID makes HTTP retries idempotent even after losing the response.
    existing = db.get(Message, message_id)
    if existing:
        if existing.conversation_id != conversation.id or existing.author_id != user.id:
            raise HTTPException(409, "Идентификатор отправки уже используется")
        original_text = existing.body
        if existing.attachment_id and not existing.is_private_note:
            caption = db.get(Message, str(uuid.uuid5(body.client_id, "caption")))
            original_text = caption.body if caption else ""
        if (
            original_text != body.text
            or existing.attachment_id != body.attachment_id
            or existing.quoted_message_id != body.quoted_message_id
            or existing.is_private_note != body.is_private_note
        ):
            raise HTTPException(
                409, "Содержание отправки изменилось. Обновите диалог перед повтором."
            )
        return existing
    if not body.text.strip() and not body.attachment_id:
        raise HTTPException(400, "Введите текст или выберите файл")
    attachment = db.get(Attachment, body.attachment_id) if body.attachment_id else None
    if body.attachment_id and (not attachment or attachment.uploaded_by != user.id):
        raise HTTPException(400, "Файл не найден среди ваших загрузок")
    quoted = db.get(Message, body.quoted_message_id) if body.quoted_message_id else None
    if body.quoted_message_id and (
        not quoted
        or quoted.conversation_id != conversation.id
        or quoted.is_private_note
        or quoted.author_type == "system"
    ):
        raise HTTPException(400, "Нельзя процитировать это сообщение")
    timestamp = now()
    msg = Message(
        id=message_id,
        conversation_id=conversation.id,
        channel_id=conversation.channel_id,
        external_id="local:" + message_id,
        direction="out",
        author_type="user",
        author_id=user.id,
        body=body.text if not attachment or body.is_private_note else "",
        attachment_id=body.attachment_id,
        kind="document" if attachment else "text",
        status="note" if body.is_private_note else "queued",
        is_private_note=body.is_private_note,
        quoted_message_id=body.quoted_message_id,
        quoted_text=quoted.body[:1000] if quoted else "",
        created_at=timestamp,
        next_attempt_at=None if body.is_private_note else timestamp,
    )
    db.add(msg)
    if attachment and body.text.strip() and not body.is_private_note:
        caption_id = str(uuid.uuid5(body.client_id, "caption"))
        db.add(
            Message(
                id=caption_id,
                conversation_id=conversation.id,
                channel_id=conversation.channel_id,
                external_id="local:" + caption_id,
                direction="out",
                author_type="user",
                author_id=user.id,
                body=body.text,
                status="queued",
                created_at=timestamp + timedelta(microseconds=1),
                next_attempt_at=timestamp,
            )
        )
    if not body.is_private_note:
        conversation.updated_at = timestamp
        if conversation.state == "closed":
            conversation.state = "waiting_customer"
    db.add(
        AuditEvent(
            user_id=user.id,
            action="message.note" if body.is_private_note else "message.queued",
            target=message_id,
        )
    )
    db.flush()
    return msg


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def send(
    conversation_id: str, body: MessageInput, user=Depends(writer), db: Session = Depends(get_db)
):
    conversation = locked_conversation(db, conversation_id)
    msg = enqueue(db, conversation, body, user)
    db.commit()
    emit(conversations=[conversation.id])
    return describe_message(msg, db)


@router.post("/conversations", status_code=201)
async def new_conversation(
    body: NewConversation, user=Depends(writer), db: Session = Depends(get_db)
):
    try:
        parsed = phonenumbers.parse(body.phone, body.country.upper())
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError()
        phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, ValueError):
        raise HTTPException(400, "Укажите действительный телефон с кодом страны") from None
    existing_message = db.get(Message, str(body.client_id))
    if existing_message:
        c = db.get(Conversation, existing_message.conversation_id)
        if existing_message.author_id != user.id or not c or c.phone != phone:
            raise HTTPException(409, "Идентификатор отправки уже используется")
        enqueue(db, c, body, user)
        return describe_conversation(c, db)
    channel_id = body.channel_id or get_setting(db, "wazzup.default_channel_id", "")
    try:
        channels = await WazzupAdapter(get_setting(db, "wazzup.api_key", "")).list_channels()
    except IntegrationError as error:
        raise HTTPException(502, "Не удалось проверить каналы Wazzup: " + error.code) from None
    choices = [c for c in channels if c["transport"] == "whatsapp"]
    if not channel_id and len(choices) == 1:
        channel_id = choices[0]["channelId"]
    selected = next((c for c in choices if c["channelId"] == channel_id), None)
    if not selected or selected["state"] != "active":
        raise HTTPException(400, "Выберите активный канал WhatsApp")
    # Serialize first-contact accounting across all managers and channels.
    db.execute(text("SELECT pg_advisory_xact_lock(734010)"))
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.channel_id == channel_id, Conversation.chat_id == phone[1:])
        .with_for_update()
    )
    if not conversation:
        count = db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action == "conversation.first",
                AuditEvent.created_at >= now() - timedelta(hours=1),
            )
        )
        if count >= get_setting(db, "inbox.first_messages_per_hour", 20):
            raise HTTPException(429, "Достигнут лимит первых сообщений новым номерам за час")
        conversation = Conversation(
            channel_id=channel_id,
            chat_id=phone[1:],
            phone=phone,
            name=body.name.strip(),
            assignee_id=user.id,
        )
        db.add(conversation)
        db.flush()
        db.add(AuditEvent(user_id=user.id, action="conversation.first", target=conversation.id))
    enqueue(db, conversation, body, user)
    db.commit()
    emit(conversations=[conversation.id])
    return describe_conversation(conversation, db)


class ConversationPatch(BaseModel):
    assignee_id: str | None = None
    state: Literal["waiting_us", "waiting_customer", "closed"] | None = None
    needs_human: bool | None = None
    ai_mode: Literal["default", "draft", "off"] | None = None
    name: str | None = Field(None, max_length=200)
    order_numbers: list[str] | None = Field(None, max_length=50)


@router.patch("/conversations/{conversation_id}")
def change_conversation(
    conversation_id: str,
    body: ConversationPatch,
    user=Depends(writer),
    db: Session = Depends(get_db),
):
    c = locked_conversation(db, conversation_id)
    values = body.model_dump(exclude_unset=True)
    if values.get("assignee_id"):
        assignee = db.get(User, values["assignee_id"])
        if not assignee or not assignee.active or assignee.role not in ("manager", "admin"):
            raise HTTPException(400, "Выберите действующего менеджера")
    if any(v is None and k != "assignee_id" for k, v in values.items()):
        raise HTTPException(400, "Пустое значение недопустимо")
    if body.order_numbers is not None:
        if any(not item.strip() or len(item) > 100 for item in body.order_numbers):
            raise HTTPException(400, "Некорректный номер заказа")
        values["order_numbers"] = list(dict.fromkeys(item.strip() for item in body.order_numbers))
    for key, value in values.items():
        setattr(c, key, value)
    db.add(AuditEvent(user_id=user.id, action="conversation.updated", target=c.id))
    db.commit()
    emit(conversations=[c.id])
    return describe_conversation(c, db)


@router.post("/conversations/{conversation_id}/read")
def mark_read(conversation_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    c = locked_conversation(db, conversation_id)
    c.unread_count = 0
    db.commit()
    return {"ok": True}


class RetryInput(BaseModel):
    confirm_duplicate_risk: bool = False


@router.post("/messages/{message_id}/retry")
def retry(message_id: str, body: RetryInput, user=Depends(writer), db: Session = Depends(get_db)):
    msg = db.scalar(select(Message).where(Message.id == message_id).with_for_update())
    if (
        not msg
        or msg.author_type not in ("user", "ai")
        or msg.is_private_note
        or msg.status not in ("error", "uncertain")
    ):
        raise HTTPException(409, "Это сообщение нельзя отправить повторно")
    if msg.status == "uncertain" and not body.confirm_duplicate_risk:
        raise HTTPException(
            409, "Проверьте WhatsApp: сообщение могло дойти. Повтор может создать дубль."
        )
    if msg.send_started_at and now() - msg.send_started_at < timedelta(seconds=65):
        raise HTTPException(409, "Подождите минуту после предыдущей попытки")
    msg.status, msg.error_code = "queued", None
    msg.send_started_at, msg.send_attempts, msg.next_attempt_at = None, 0, now()
    db.add(AuditEvent(user_id=user.id, action="message.retry_confirmed", target=msg.id))
    db.commit()
    emit(conversations=[msg.conversation_id])
    return describe_message(msg, db)


@router.get("/channels")
async def channels(user=Depends(current_user), db: Session = Depends(get_db)):
    try:
        return await WazzupAdapter(get_setting(db, "wazzup.api_key", "")).list_channels()
    except IntegrationError as e:
        raise HTTPException(502, "Wazzup: " + e.code) from None


@router.get("/team")
def team(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        {"id": u.id, "name": u.email, "role": u.role}
        for u in db.scalars(select(User).where(User.active))
    ]


@router.get("/boards")
def boards(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        {"id": b.id, "name": b.name, "statuses": b.statuses}
        for b in db.scalars(select(Board).order_by(Board.position))
    ]


@router.post("/uploads", status_code=201)
def upload(file: UploadFile = File(), user=Depends(writer), db: Session = Depends(get_db)):
    content = file.file.read(MAX_FILE_SIZE + 1)
    if not content or len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, "Файл должен быть непустым и не больше 10 МБ")
    filename = (file.filename or "attachment").replace("\\", "/").split("/")[-1][:200]
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    key, backend = save_file(content)
    item = Attachment(
        storage_key=key,
        backend=backend,
        filename=filename,
        size=len(content),
        uploaded_by=user.id,
        mime=mime,
    )
    db.add(item)
    db.commit()
    return {"id": item.id, "filename": filename, "size": len(content)}


@router.get("/files/{signature}")
def download_file(signature: str, db: Session = Depends(get_db)):
    try:
        attachment_id = file_signer().loads(signature, max_age=600)
    except (BadSignature, SignatureExpired):
        raise HTTPException(404, "Ссылка недействительна") from None
    item = db.get(Attachment, attachment_id)
    if not item:
        raise HTTPException(404, "Файл не найден")
    content = read_file(item.storage_key, item.backend)
    inline = item.mime in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/mp4",
        "video/mp4",
        "video/webm",
    }
    return Response(
        content,
        media_type=item.mime if inline else "application/octet-stream",
        headers={
            "Content-Disposition": ("inline" if inline else "attachment")
            + "; filename*=UTF-8''"
            + quote(item.filename),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
