"""Transactional task history, internal chat events and durable notifications."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from redis import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import SessionLocal
from app.core.settings import get_setting, redis_client
from app.models import Conversation, Message, Notification, Task, TaskHistory, User, now, uid


def publish_tasks():
    try:
        redis_client.publish("crm:events", json.dumps({"type": "tasks.changed"}))
    except RedisError:
        pass  # The UI also polls; every change and notification is committed to Postgres.


def notify(db, task, recipients, actor, kind, text, dedupe=None):
    recipients = set(recipients) - {None, actor}
    if not recipients:
        return
    for user_id in db.scalars(select(User.id).where(User.id.in_(recipients), User.active)):
        db.execute(
            insert(Notification)
            .values(
                id=uid(),
                user_id=user_id,
                kind=kind,
                payload={
                    "task_id": task.id,
                    "number": task.number,
                    "title": task.title,
                    "text": text,
                },
                dedupe_key=f"{dedupe}:{user_id}" if dedupe else None,
                created_at=now(),
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )


def history(db, task, actor, changes):
    def serial(value):
        return value.isoformat() if isinstance(value, datetime) else value

    db.add(
        TaskHistory(
            task_id=task.id,
            user_id=actor,
            changes={key: [serial(old), serial(new)] for key, (old, new) in changes.items()},
        )
    )


def system_note(db, task, actor, action, comment=""):
    if not task.conversation_id:
        return
    conversation = db.scalar(
        select(Conversation).where(Conversation.id == task.conversation_id).with_for_update()
    )
    if not conversation:
        return
    conversation.task_updated = True
    db.add(
        Message(
            conversation_id=conversation.id,
            channel_id=conversation.channel_id,
            external_id="task:" + uid(),
            direction="system",
            author_type="system",
            author_id=actor,
            kind="system",
            status="internal",
            is_private_note=True,
            body=f"Задача T-{task.number} {action}: {task.title}"
            + ("\n" + comment if comment else ""),
        )
    )


def overdue_reminders():
    with SessionLocal() as db:
        local = now().astimezone(ZoneInfo(get_setting(db, "tasks.timezone", "Asia/Seoul")))
        days = str(get_setting(db, "tasks.workdays", "1,2,3,4,5")).split(",")
        if str(local.isoweekday()) not in days or local.hour < int(
            get_setting(db, "tasks.reminder_hour", 9)
        ):
            return
        # Bound each transaction; repeat calls safely skip previously emitted reminders.
        for task in db.scalars(
            select(Task).where(Task.completed_at.is_(None), Task.due_at < now()).order_by(Task.id)
        ).yield_per(200):
            notify(
                db,
                task,
                [task.author_id, task.assignee_id],
                None,
                "task.overdue",
                "Срок задачи истёк",
                f"overdue:{local.date()}:{task.id}",
            )
        db.commit()
    publish_tasks()
