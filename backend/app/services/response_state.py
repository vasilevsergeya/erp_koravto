"""A reply acknowledges only incoming messages known when that reply was composed."""

from sqlalchemy import func, select

from app.models import Message


def refresh_unanswered(db, conversation):
    db.flush()
    conversation.unread_count = (
        db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.direction == "in",
                Message.inbound_seq > conversation.answered_seq,
                Message.deleted.is_(False),
            )
        )
        or 0
    )
    if conversation.state != "closed" or conversation.unread_count:
        conversation.state = "waiting_us" if conversation.unread_count else "waiting_customer"


def acknowledge_reply(db, conversation, message):
    if (
        message.is_private_note
        or message.direction != "out"
        or message.deleted
        or message.reply_acknowledged
    ):
        return
    if message.status not in ("sent", "delivered", "read"):
        return
    if message.reply_through_seq is None:
        # Phone echoes and historical messages must not acknowledge newer incoming messages.
        message.reply_through_seq = (
            db.scalar(
                select(func.max(Message.inbound_seq)).where(
                    Message.conversation_id == conversation.id,
                    Message.direction == "in",
                    Message.created_at <= message.created_at,
                )
            )
            or 0
        )
    conversation.answered_seq = max(conversation.answered_seq, message.reply_through_seq)
    message.reply_acknowledged = True
    refresh_unanswered(db, conversation)
