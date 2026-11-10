from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from itsdangerous import BadSignature, SignatureExpired
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.core.db import get_db
from app.core.security import file_signer
from app.models import Attachment, Board, Conversation, Message
from app.services.storage import read_file

router = APIRouter(prefix="/api", tags=["inbox"])


@router.get("/conversations")
def conversations(
    user=Depends(current_user), db: Session = Depends(get_db), offset: int = Query(0, ge=0)
):
    rows = db.scalars(
        select(Conversation).order_by(Conversation.updated_at.desc()).offset(offset).limit(50)
    ).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "chat_type": c.chat_type,
            "channel_id": c.channel_id,
            "state": c.state,
            "updated_at": c.updated_at,
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: str,
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id)
        .offset(offset)
        .limit(50)
    ).all()
    return [
        {
            "id": m.id,
            "direction": m.direction,
            "author_type": m.author_type,
            "text": m.body,
            "kind": m.kind,
            "status": m.status,
            "edited": m.edited,
            "deleted": m.deleted,
            "created_at": m.created_at,
            "attachment_url": "/api/files/" + file_signer().dumps(m.attachment_id)
            if m.attachment_id
            else None,
        }
        for m in reversed(rows)
    ]


@router.get("/boards")
def boards(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        {"id": b.id, "name": b.name, "statuses": b.statuses}
        for b in db.scalars(select(Board).order_by(Board.position))
    ]


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
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(item.filename),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
