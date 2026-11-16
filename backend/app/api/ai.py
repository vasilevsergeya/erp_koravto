import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.api.auth import admin_user, current_user, rate_limit
from app.api.inbox import (
    MessageInput,
    describe_message,
    enqueue,
    locked_conversation,
    translate_before_send,
    writer,
)
from app.core.db import get_db
from app.core.settings import get_setting, invalidate_settings, put_setting
from app.integrations.openai import AIError, OpenAIAdapter
from app.models import (
    AIDraft,
    AIPrompt,
    AIUsage,
    ApprovedExample,
    AuditEvent,
    Conversation,
    Customer,
    Message,
    User,
    now,
)
from app.services.ai import (
    current,
    latest_inbound,
    month_spend,
    record_usage,
    source_hash,
    supersede,
)
from app.services.ai_config import BASE_PROMPT, AIConfig, config_version, settings
from app.services.dialog_languages import client_language, manager_language
from app.services.outbox import emit

router = APIRouter(prefix="/api", tags=["ai"])


def describe(d, reading_language=None):
    result = {
        k: getattr(d, k)
        for k in [
            "id",
            "conversation_id",
            "inbound_id",
            "generation",
            "state",
            "decision",
            "intent",
            "language",
            "body",
            "final_text",
            "reason",
            "rejection_category",
            "mode",
            "policy",
            "tool_calls",
            "created_at",
            "decided_at",
            "auto_at",
            "prompt_id",
        ]
    }

    language = reading_language or d.manager_language
    result.update(
        manager_language=language,
        manager_text=d.body if language == d.language else (d.manager_versions or {}).get(language),
        final_manager_text=d.final_manager_text,
        final_language=d.final_language,
    )
    return result


async def reading_version(db, draft_id, language):
    row = db.scalar(select(AIDraft).where(AIDraft.id == draft_id).with_for_update())
    if not row or not row.body or row.decision != "pending":
        return
    if language == row.language or (row.manager_versions or {}).get(language):
        return
    retry_key = "retry_after:" + language
    if (row.manager_versions or {}).get(retry_key, 0) > now().timestamp():
        return
    source, original = row.language or "auto", row.body
    row.manager_versions = {**(row.manager_versions or {}), retry_key: now().timestamp() + 120}
    db.commit()
    from app.services.translations import translate_text

    try:
        value = await translate_text(original, source, language)
    except AIError:
        value = None
    row = db.scalar(
        select(AIDraft)
        .where(AIDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row and row.body == original:
        versions = dict(row.manager_versions or {})
        if value:
            versions[language] = value
            versions.pop(retry_key, None)
        else:
            versions[retry_key] = now().timestamp() + 60
        row.manager_versions = versions
        db.commit()


@router.get("/ai/settings")
def read_config(user=Depends(admin_user), db: Session = Depends(get_db)):
    cfg = settings(db)
    return {
        "config": cfg.model_dump(),
        "version": config_version(cfg),
        "configured": bool(get_setting(db, "openai.api_key", "")),
        "month_cost": month_spend(db, cfg),
        "orders_source": "customer_names_and_numbers_only",
    }


class ConfigInput(BaseModel):
    config: AIConfig
    version: str


@router.put("/ai/settings")
def save_config(body: ConfigInput, user=Depends(admin_user), db: Session = Depends(get_db)):
    db.execute(text("SELECT pg_advisory_xact_lock(734020)"))
    if body.version != config_version(settings(db)):
        raise HTTPException(409, "Настройки изменились. Обновите страницу")
    if body.config.duty_user_id:
        manager = db.get(User, body.config.duty_user_id)
        if not manager or not manager.active or manager.role not in ("manager", "admin"):
            raise HTTPException(400, "Выберите действующего менеджера")
    put_setting(db, "ai.config", body.config.model_dump(), False, user.id)
    db.commit()
    invalidate_settings()
    return read_config(user, db)


@router.post("/ai/check")
async def check_ai(user=Depends(admin_user), db: Session = Depends(get_db)):
    rate_limit("ai-check:" + user.id, 5, 60)
    cfg = settings(db)
    key = get_setting(db, "openai.api_key", "")
    db.rollback()
    if not key:
        raise HTTPException(400, "Сначала сохраните ключ OpenAI")
    adapter = OpenAIAdapter(
        key,
        lambda model, purpose, usage, error: record_usage(None, cfg, model, purpose, usage, error),
    )
    try:
        # Small synthetic capability checks; no customer data is sent by this action.
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        for model in dict.fromkeys([cfg.model, cfg.classifier]):
            await adapter.response(
                model,
                'Return {"ok":true}.',
                [{"role": "user", "content": "Connection test"}],
                schema,
            )
        await adapter.embedding(cfg.embedding_model, "Connection test")
    except AIError as e:
        raise HTTPException(502, "OpenAI: " + str(e)) from None
    return {"ok": True}


@router.get("/ai/prompts")
def prompts(user=Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AIPrompt).order_by(AIPrompt.created_at.desc()).limit(100)).all()
    return {
        "base": BASE_PROMPT,
        "items": [
            {"id": p.id, "body": p.body, "active": p.active, "created_at": p.created_at}
            for p in rows
        ],
    }


class PromptInput(BaseModel):
    body: str = Field(min_length=1, max_length=16000)


@router.post("/ai/prompts", status_code=201)
def add_prompt(body: PromptInput, user=Depends(admin_user), db: Session = Depends(get_db)):
    db.execute(text("SELECT pg_advisory_xact_lock(734020)"))
    db.execute(update(AIPrompt).values(active=False))
    row = AIPrompt(body=body.body, created_by=user.id, active=True)
    db.add(row)
    db.flush()
    db.add(AuditEvent(user_id=user.id, action="ai.prompt.created", target=row.id))
    db.commit()
    return prompts(user, db)


@router.post("/ai/prompts/{prompt_id}/activate")
def activate(prompt_id: str, user=Depends(admin_user), db: Session = Depends(get_db)):
    db.execute(text("SELECT pg_advisory_xact_lock(734020)"))
    row = db.get(AIPrompt, prompt_id)
    if not row:
        raise HTTPException(404, "Версия не найдена")
    db.execute(update(AIPrompt).values(active=False))
    db.flush()
    row.active = True
    db.add(AuditEvent(user_id=user.id, action="ai.prompt.activated", target=row.id))
    db.commit()
    return prompts(user, db)


@router.get("/conversations/{conversation_id}/draft")
async def draft(conversation_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(Conversation, conversation_id):
        raise HTTPException(404, "Диалог не найден")
    row = db.scalar(
        select(AIDraft)
        .where(AIDraft.conversation_id == conversation_id)
        .order_by(AIDraft.created_at.desc(), AIDraft.id.desc())
        .limit(1)
    )
    if row:
        await reading_version(db, row.id, manager_language(user))
    cfg = settings(db)
    return {
        "item": describe(row, manager_language(user)) if row else None,
        "configured": bool(get_setting(db, "openai.api_key", "")),
        "enabled": cfg.enabled,
    }


@router.post("/conversations/{conversation_id}/draft/regenerate", status_code=201)
def regenerate(conversation_id: str, user=Depends(writer), db: Session = Depends(get_db)):
    rate_limit("ai-regenerate:" + user.id, 10, 60)
    conversation = locked_conversation(db, conversation_id)
    inbound = latest_inbound(db, conversation_id)
    if not inbound or inbound.deleted:
        raise HTTPException(400, "Нет входящего сообщения")
    if not get_setting(db, "openai.api_key", ""):
        raise HTTPException(400, "Ключ OpenAI не настроен")
    if conversation.needs_human:
        raise HTTPException(409, "Сначала снимите флаг «Нужен человек»")
    if not settings(db).enabled or conversation.ai_mode == "off":
        raise HTTPException(409, "AI выключен")
    supersede(db, conversation_id, "regenerated")
    generation = (
        db.scalar(select(func.max(AIDraft.generation)).where(AIDraft.inbound_id == inbound.id)) or 0
    ) + 1
    row = AIDraft(
        conversation_id=conversation_id,
        inbound_id=inbound.id,
        generation=generation,
        source_hash=source_hash(inbound),
    )
    db.add(row)
    db.commit()
    emit("ai.changed", conversations=[conversation_id])
    return describe(row)


class Decision(BaseModel):
    final_text: str | None = Field(None, min_length=1, max_length=10000)
    reason: str = Field("", max_length=1000)
    category: Literal["", "incorrect", "irrelevant", "tone", "other"] = ""
    source_language: str | None = Field(None, pattern=r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
    target_language: str | None = Field(None, pattern=r"^original$|^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
    attachment_id: str | None = None
    quoted_message_id: str | None = None


@router.post("/drafts/{draft_id}/send")
@router.post("/drafts/{draft_id}/send-edited")
async def send_draft(
    draft_id: str, body: Decision, user=Depends(writer), db: Session = Depends(get_db)
):
    fingerprint = body.model_dump(exclude={"reason", "category"})
    row = db.get(AIDraft, draft_id)
    if not row:
        raise HTTPException(404, "Черновик не найден")
    if row.decision in ("sent", "edited"):
        if row.decided_by != user.id or row.decision_input != fingerprint:
            raise HTTPException(409, "Решение уже принято")
        return describe_message(db.get(Message, row.message_id), db, user.id)
    conversation = db.get(Conversation, row.conversation_id)
    if not conversation:
        raise HTTPException(404, "Диалог не найден")
    if (
        row.decision != "pending"
        or row.state not in ("ready", "escalated")
        or not current(db, row, conversation)
    ):
        raise HTTPException(409, "Черновик устарел. Обновите диалог")
    reading_language = body.source_language or manager_language(user)
    profile = db.get(Customer, conversation.customer_id) if conversation.customer_id else None
    target = (
        reading_language
        if body.target_language == "original"
        else body.target_language or client_language(profile, row.language)
    )
    if not target:
        raise HTTPException(409, "Выберите язык отправки")
    original_body, original_language = row.body, row.language
    cid = row.conversation_id
    cached_reading = (
        original_body
        if reading_language == original_language
        else (row.manager_versions or {}).get(reading_language)
    )
    db.rollback()
    if body.final_text is None:
        manager_text = cached_reading or await translate_before_send(
            original_body, original_language or "auto", reading_language
        )
        final = (
            original_body
            if target == original_language
            else await translate_before_send(original_body, original_language or "auto", target)
        )
    else:
        manager_text = body.final_text
        final = await translate_before_send(manager_text, reading_language, target)
    if not manager_text.strip():
        raise HTTPException(400, "Введите ответ")
    conversation = locked_conversation(db, cid)
    row = db.scalar(
        select(AIDraft)
        .where(AIDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(409, "Черновик удалён. Текст не отправлен")
    if (
        row.decision in ("sent", "edited")
        and row.decided_by == user.id
        and row.decision_input == fingerprint
    ):
        return describe_message(db.get(Message, row.message_id), db, user.id)
    if (
        row.decision != "pending"
        or row.state not in ("ready", "escalated")
        or row.body != original_body
        or not current(db, row, conversation)
    ):
        raise HTTPException(409, "Черновик устарел. Текст не отправлен")
    msg = enqueue(
        db,
        conversation,
        MessageInput(
            client_id=uuid.uuid5(uuid.UUID(row.id), "approved"),
            text=manager_text,
            source_language=reading_language,
            target_language=target,
            attachment_id=body.attachment_id,
            quoted_message_id=body.quoted_message_id,
        ),
        user,
        prepared=(final, reading_language, target),
    )
    row.decision = "sent" if body.final_text is None else "edited"
    row.final_text, row.final_manager_text, row.final_language = final, manager_text, target
    row.decision_input = fingerprint
    row.decided_by, row.decided_at, row.message_id, row.auto_at = user.id, now(), msg.id, None
    inbound = db.get(Message, row.inbound_id)
    assert inbound is not None
    db.add(
        ApprovedExample(
            draft_id=row.id,
            intent=row.intent,
            language=target,
            question=inbound.body[:10000],
            reply=final,
            manager_reply=manager_text,
            manager_language=reading_language,
        )
    )
    db.add(AuditEvent(user_id=user.id, action="ai.draft." + row.decision, target=row.id))
    db.commit()
    emit("ai.changed", conversations=[conversation.id])
    emit(conversations=[conversation.id])
    return describe_message(msg, db, user.id)


@router.post("/drafts/{draft_id}/reject")
def reject(draft_id: str, body: Decision, user=Depends(writer), db: Session = Depends(get_db)):
    row = db.get(AIDraft, draft_id)
    if not row:
        raise HTTPException(404, "Черновик не найден")
    locked_conversation(db, row.conversation_id)
    row = db.scalar(
        select(AIDraft)
        .where(AIDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    if row.decision == "rejected":
        return describe(row)
    if row.decision != "pending":
        raise HTTPException(409, "Решение уже принято")
    if (not body.category or body.category == "other") and not body.reason.strip():
        raise HTTPException(400, "Укажите причину отклонения")
    row.decision = "rejected"
    row.reason = body.reason.strip()
    row.rejection_category = body.category or "other"
    db.add(
        Message(
            conversation_id=row.conversation_id,
            channel_id=row.conversation_id,
            external_id=str(uuid.uuid4()),
            direction="out",
            author_type="system",
            body="AI-черновик отклонён: " + row.rejection_category,
            status="system",
            is_private_note=True,
            author_id=user.id,
        )
    )
    row.decided_by = user.id
    row.decided_at = now()
    row.auto_at = None
    db.add(AuditEvent(user_id=user.id, action="ai.draft.rejected", target=row.id))
    db.commit()
    emit("ai.changed", conversations=[row.conversation_id])
    return describe(row)


@router.get("/reports/ai")
def report(
    days: int = Query(30, ge=1, le=365), user=Depends(admin_user), db: Session = Depends(get_db)
):
    from datetime import timedelta

    since = now() - timedelta(days=days)
    week = func.date_trunc("week", AIDraft.created_at).label("week")
    rows = db.execute(
        select(
            week,
            AIDraft.intent,
            AIDraft.decision,
            AIDraft.state,
            func.count(),
        )
        .where(AIDraft.created_at >= since)
        .group_by(week, AIDraft.intent, AIDraft.decision, AIDraft.state)
    ).all()
    drafts = db.scalars(
        select(AIDraft)
        .where(AIDraft.created_at >= since)
        .order_by(AIDraft.created_at.desc())
        .limit(100)
    ).all()
    usage = db.scalars(
        select(AIUsage)
        .where(AIUsage.created_at >= since)
        .order_by(AIUsage.created_at.desc())
        .limit(50)
    ).all()
    return {
        "groups": [
            {"week": r[0], "intent": r[1], "decision": r[2], "state": r[3], "count": r[4]}
            for r in rows
        ],
        "month_cost": month_spend(db, settings(db)),
        "recent": [describe(d) for d in drafts],
        "usage": [
            {
                "model": u.model,
                "purpose": u.purpose,
                "tokens_in": u.tokens_in,
                "tokens_out": u.tokens_out,
                "cost_usd": float(u.cost_usd),
                "error": u.error,
                "created_at": u.created_at,
            }
            for u in usage
        ],
    }


@router.post("/drafts/{draft_id}/pause")
def pause_draft(draft_id: str, user=Depends(writer), db: Session = Depends(get_db)):
    row = db.get(AIDraft, draft_id)
    if not row:
        raise HTTPException(404, "Черновик не найден")
    locked_conversation(db, row.conversation_id)
    row = db.scalar(
        select(AIDraft)
        .where(AIDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    if row.decision == "auto_sent":
        message = db.get(Message, row.message_id)
        if not message or message.status != "queued" or message.send_started_at:
            raise HTTPException(409, "Отправка уже началась")
        message.status = "cancelled"
        message.next_attempt_at = None
        row.decision = "pending"
        row.message_id = None
        row.decided_at = None
    if row.decision != "pending":
        raise HTTPException(409, "Решение уже принято")
    row.auto_at = None
    row.mode = "draft"
    db.commit()
    emit("ai.changed", conversations=[row.conversation_id])
    return describe(row)


@router.post("/drafts/{draft_id}/reading/retry")
def retry_reading(draft_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    rate_limit("draft-reading:" + user.id, 5, 60)
    row = db.scalar(select(AIDraft).where(AIDraft.id == draft_id).with_for_update())
    if not row:
        raise HTTPException(404, "Черновик не найден")
    versions = dict(row.manager_versions or {})
    versions.pop("retry_after:" + manager_language(user), None)
    row.manager_versions = versions
    db.commit()
    return {"ok": True}
