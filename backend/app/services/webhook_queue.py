"""Pending work and terminal failures are separate views of existing event rows."""

from sqlalchemy import func, select

from app.models import WebhookEvent

MAX_ATTEMPTS = 10
SAFE_REASONS = {
    "attachment_too_large": "Вложение превышает допустимый размер 10 МБ",
    "attachment_download_failed": "Не удалось скачать вложение у провайдера",
    "attachment_private_address": "Адрес вложения не является публичным",
    "attachment_invalid_url": "Недопустимый адрес вложения",
    "attachment_redirect_limit": "Превышен предел перенаправлений вложения",
    "s3_bucket_not_configured": "Хранилище вложений не настроено",
    "invalid_webhook_shape": "Неверная структура входящего события",
    "too_many_events": "Превышен размер пакета событий",
    "invalid_message": "В сообщении отсутствуют обязательные поля",
    "invalid_message_id": "Недопустимый идентификатор сообщения",
    "invalid_status": "Неверная структура статуса сообщения",
}


def error_code(error: Exception) -> str:
    # Never retain arbitrary exception messages (URLs and credentials can occur there).
    value = str(error) if isinstance(error, ValueError) else ""
    return value if value in SAFE_REASONS else type(error).__name__[:100]


def pending_condition():
    return WebhookEvent.state.in_(["pending", "error"]) & (WebhookEvent.attempts < MAX_ATTEMPTS)


def summary(db):
    count, oldest = db.execute(
        select(func.count(), func.min(WebhookEvent.created_at)).where(pending_condition())
    ).one()
    failed = db.scalar(
        select(func.count()).select_from(WebhookEvent).where(WebhookEvent.state == "failed")
    )
    return {"pending": count, "oldest_pending_at": oldest, "failed": failed}


def describe_failure(event):
    reason = event.last_error or "unknown"
    return {
        "id": event.id,
        "created_at": event.created_at,
        "attempts": event.attempts,
        "reason_code": reason if reason in SAFE_REASONS or reason.isidentifier() else "unknown",
        "reason": SAFE_REASONS.get(
            reason, "Ошибка обработки: " + (reason if reason.isidentifier() else "unknown")
        ),
    }
