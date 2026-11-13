import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.api.auth import admin_user, current_user, rate_limit
from app.api.inbox import MessageInput, describe_message, enqueue, locked_conversation, writer
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
from app.services.outbox import emit

router = APIRouter(prefix="/api", tags=["ai"])


def describe(d):
    return {
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
            "mode",
            "policy",
            "tool_calls",
            "created_at",
            "decided_at",
            "auto_at",
            "prompt_id",
        ]
    }


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
def draft(conversation_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    if not db.get(Conversation, conversation_id):
        raise HTTPException(404, "Диалог не найден")
    row = db.scalar(
        select(AIDraft)
        .where(AIDraft.conversation_id == conversation_id)
        .order_by(AIDraft.created_at.desc(), AIDraft.id.desc())
        .limit(1)
    )
    cfg = settings(db)
    return {
        "item": describe(row) if row else None,
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


@router.post("/drafts/{draft_id}/send")
@router.post("/drafts/{draft_id}/send-edited")
def send_draft(draft_id: str, body: Decision, user=Depends(writer), db: Session = Depends(get_db)):
    row = db.get(AIDraft, draft_id)
    if not row:
        raise HTTPException(404, "Черновик не найден")
    conversation = locked_conversation(db, row.conversation_id)
    row = db.scalar(
        select(AIDraft)
        .where(AIDraft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    final = body.final_text if body.final_text is not None else row.body
    if row.decision in ("sent", "edited"):
        if row.decided_by != user.id or row.final_text != final:
            raise HTTPException(409, "Решение уже принято")
        return describe_message(db.get(Message, row.message_id), db)
    if (
        row.decision != "pending"
        or row.state not in ("ready", "escalated")
        or not current(db, row, conversation)
    ):
        raise HTTPException(409, "Черновик устарел. Обновите диалог")
    if not final.strip():
        raise HTTPException(400, "Введите ответ")
    msg = enqueue(
        db,
        conversation,
        MessageInput(client_id=uuid.uuid5(uuid.UUID(row.id), "approved"), text=final),
        user,
    )
    row.decision = "sent" if final == row.body else "edited"
    row.final_text = final
    row.decided_by = user.id
    row.decided_at = now()
    row.message_id = msg.id
    row.auto_at = None
    # Only manager-approved examples, never automatic replies. Index after confirmed delivery.
    inbound = db.get(Message, row.inbound_id)
    assert inbound is not None
    db.add(
        ApprovedExample(
            draft_id=row.id,
            intent=row.intent,
            language=row.language,
            question=inbound.body[:10000],
            reply=final,
        )
    )
    db.add(AuditEvent(user_id=user.id, action="ai.draft." + row.decision, target=row.id))
    db.commit()
    emit("ai.changed", conversations=[conversation.id])
    emit(conversations=[conversation.id])
    return describe_message(msg, db)


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
    if not body.reason.strip():
        raise HTTPException(400, "Укажите причину отклонения")
    row.decision = "rejected"
    row.reason = body.reason
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
