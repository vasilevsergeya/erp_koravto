import csv
import io
import re
import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.api.auth import admin_user, current_user
from app.core.db import get_db
from app.core.security import file_signer
from app.core.settings import get_setting
from app.models import (
    Attachment,
    AuditEvent,
    Board,
    Conversation,
    Notification,
    Task,
    TaskComment,
    TaskHistory,
    TaskType,
    User,
    now,
    uid,
)
from app.services.tasks import history, notify, publish_tasks, system_note

router = APIRouter(prefix="/api", tags=["tasks"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskFields(Input):
    title: str = Field(min_length=1, max_length=250)
    board_id: str
    type_id: str
    status_id: str | None = None
    description: str = Field("", max_length=20000)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    assignee_id: str | None = None
    due_at: datetime | None = None
    conversation_id: str | None = None
    order_no: str = Field("", max_length=100)
    part_no: str = Field("", max_length=100)
    quantity: int | None = Field(None, ge=1, le=1000000)
    labels: list[str] = Field(default_factory=list, max_length=30)
    watchers: list[str] = Field(default_factory=list, max_length=50)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("due_at")
    @classmethod
    def aware_date(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Specify timezone")
        return value

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, value):
        if any(not x.strip() or len(x.strip()) > 60 for x in value):
            raise ValueError("Invalid label")
        return list(dict.fromkeys(x.strip() for x in value))


class TaskCreate(TaskFields):
    client_id: uuid.UUID


class TaskPatch(Input):
    version: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    board_id: str | None = None
    type_id: str | None = None
    status_id: str | None = None
    priority: str | None = None
    assignee_id: str | None = None
    due_at: datetime | None = None
    conversation_id: str | None = None
    order_no: str | None = None
    part_no: str | None = None
    quantity: int | None = None
    labels: list[str] | None = None
    watchers: list[str] | None = None
    attachment_ids: list[str] | None = None
    completion_comment: str = Field("", max_length=5000)


def board_row(db, board_id, lock=False):
    query = select(Board).where(Board.id == board_id)
    board = db.scalar(query.with_for_update() if lock else query)
    if not board:
        raise HTTPException(404, "Доска не найдена")
    return board


def state(board, status_id):
    item = next((s for s in board.statuses if s["id"] == status_id), None)
    if not item:
        raise HTTPException(400, "Статус не принадлежит выбранной доске")
    return item


def member(db, value):
    if value:
        user = db.get(User, value)
        if not user or not user.active:
            raise HTTPException(400, "Выберите действующего сотрудника")


def validate_fields(db, fields, actor, old=None):
    member(db, fields.assignee_id)
    for item in fields.watchers:
        member(db, item)
    kind = db.get(TaskType, fields.type_id)
    if not kind or (not kind.active and (not old or old.type_id != kind.id)):
        raise HTTPException(400, "Выберите действующий тип задачи")
    if fields.conversation_id and not db.get(Conversation, fields.conversation_id):
        raise HTTPException(400, "Диалог не найден")
    for file_id in fields.attachment_ids:
        attachment = db.get(Attachment, file_id)
        if not attachment or (
            attachment.uploaded_by != actor and (not old or file_id not in old.attachment_ids)
        ):
            raise HTTPException(400, "Недоступное вложение")


def task_row(db, key, lock=False):
    if re.fullmatch(r"T-[0-9]{1,18}", key):
        query = select(Task).where(Task.number == int(key[2:]))
    else:
        query = select(Task).where(Task.id == key)
    task = db.scalar(query.with_for_update() if lock else query)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    return task


def person(db, user_id):
    user = db.get(User, user_id) if user_id else None
    return user.email if user else "Не назначен"


def describe(task, db):
    board = db.get(Board, task.board_id)
    kind = db.get(TaskType, task.type_id)
    conversation = db.get(Conversation, task.conversation_id) if task.conversation_id else None
    customer_name = ""
    if conversation:
        customer_name = conversation.name or conversation.phone
        if len(conversation.onec_clients) == 1:
            customer_name = conversation.onec_clients[0].get("name") or customer_name
    return {
        **{name: getattr(task, name) for name in TaskFields.model_fields},
        "id": task.id,
        "number": task.number,
        "code": f"T-{task.number}",
        "version": task.version,
        "author_id": task.author_id,
        "author_name": person(db, task.author_id),
        "assignee_name": person(db, task.assignee_id),
        "board_name": board.name,
        "status": state(board, task.status_id),
        "type_name": kind.name,
        "customer_name": customer_name,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
        "overdue": bool(task.due_at and task.due_at < now() and not task.completed_at),
    }


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, user=Depends(current_user), db: Session = Depends(get_db)):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": "task:" + str(body.client_id)},
    )
    existing = db.scalar(select(Task).where(Task.client_id == str(body.client_id)))
    if existing:
        if existing.author_id != user.id:
            raise HTTPException(409, "Идентификатор уже использован")
        return describe(existing, db)
    board = board_row(db, body.board_id, lock=True)
    if board.archived:
        raise HTTPException(400, "Доска в архиве")
    fields = TaskFields(**body.model_dump(exclude={"client_id"}))
    fields.status_id = fields.status_id or board.statuses[0]["id"]
    status = state(board, fields.status_id)
    validate_fields(db, fields, user.id)
    task = Task(
        **fields.model_dump(),
        client_id=str(body.client_id),
        author_id=user.id,
        completed_at=now() if status["terminal"] else None,
    )
    db.add(task)
    db.flush()
    history(db, task, user.id, {"created": [None, f"T-{task.number}"]})
    notify(
        db,
        task,
        [task.assignee_id, *task.watchers],
        user.id,
        "task.created",
        "Создана новая задача",
    )
    system_note(db, task, user.id, "создана")
    db.commit()
    publish_tasks()
    return describe(task, db)


def apply_change(db, task, fields, actor, comment=""):
    status = state(db.get(Board, fields.board_id), fields.status_id)
    changes = {
        key: (getattr(task, key), value)
        for key, value in fields.model_dump().items()
        if getattr(task, key) != value
    }
    if not changes:
        return
    was_done = bool(task.completed_at)
    for key, (_, value) in changes.items():
        setattr(task, key, value)
    task.completed_at = (task.completed_at or now()) if status["terminal"] else None
    task.updated_at, task.version = now(), task.version + 1
    history(db, task, actor, changes)
    if "assignee_id" in changes:
        notify(db, task, [task.assignee_id], actor, "task.assigned", "Вам назначена задача")
    if "due_at" in changes:
        notify(db, task, [task.assignee_id], actor, "task.due", "Изменён срок задачи")
    if "status_id" in changes or "board_id" in changes:
        notify(
            db,
            task,
            [task.author_id, *(task.watchers if task.completed_at else [])],
            actor,
            "task.status",
            "Статус: " + status["name"],
        )
    if task.completed_at and not was_done:
        system_note(db, task, actor, "завершена («" + status["name"] + "»)", comment)
    elif was_done and not task.completed_at:
        system_note(db, task, actor, "открыта заново")
    if comment:
        db.add(TaskComment(task_id=task.id, author_id=actor, body=comment, mentions=[]))


@router.patch("/tasks/{key}")
def patch_task(
    key: str, body: TaskPatch, user=Depends(current_user), db: Session = Depends(get_db)
):
    original = task_row(db, key)
    target_id = body.board_id or original.board_id
    # Same lock order as status removal; serializes transitions with board edits.
    for board_id in sorted({original.board_id, target_id}):
        board_row(db, board_id, lock=True)
    db.expire(original)
    task = task_row(db, key, lock=True)
    if task.version != body.version:
        raise HTTPException(409, "Задачу уже изменили. Обновите карточку и повторите действие")
    values = body.model_dump(exclude_unset=True, exclude={"version", "completion_comment"})
    if any(
        value is None and key not in ("assignee_id", "due_at", "conversation_id", "quantity")
        for key, value in values.items()
    ):
        raise HTTPException(422, "Пустое значение недопустимо")
    if "board_id" in values and values["board_id"] != task.board_id:
        board = board_row(db, values["board_id"])
        if board.archived:
            raise HTTPException(400, "Доска в архиве")
        values.setdefault("status_id", board.statuses[0]["id"])
    from pydantic import ValidationError

    try:
        fields = TaskFields(**{**{k: getattr(task, k) for k in TaskFields.model_fields}, **values})
    except ValidationError:
        raise HTTPException(422, "Проверьте заполнение полей задачи") from None
    validate_fields(db, fields, user.id, task)
    apply_change(db, task, fields, user.id, body.completion_comment)
    db.commit()
    publish_tasks()
    return describe(task, db)


def filtered_tasks(
    q: str = Query("", max_length=200),
    board_id: str | None = None,
    status_id: str | None = None,
    type_id: str | None = None,
    author_id: str | None = None,
    assignee_id: str | None = None,
    conversation_id: str | None = None,
    label: str | None = None,
    view: Literal["all", "open", "mine", "created", "overdue"] = "open",
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    due_from: datetime | None = None,
    due_to: datetime | None = None,
    sort: Literal["updated", "due", "created", "number"] = "updated",
    related_me: bool = False,
    user=Depends(current_user),
):
    query = select(Task)
    for name, value in [
        ("board_id", board_id),
        ("status_id", status_id),
        ("type_id", type_id),
        ("author_id", author_id),
        ("conversation_id", conversation_id),
    ]:
        if value:
            query = query.where(getattr(Task, name) == value)
    if assignee_id:
        query = query.where(
            Task.assignee_id.is_(None)
            if assignee_id == "unassigned"
            else Task.assignee_id == assignee_id
        )
    if view != "all":
        query = query.where(Task.completed_at.is_(None))
    if view == "mine":
        query = query.where(Task.assignee_id == user.id)
    if view == "created":
        query = query.where(Task.author_id == user.id)
    if view == "overdue":
        query = query.where(Task.due_at < now())
    if related_me:
        query = query.where(or_(Task.author_id == user.id, Task.assignee_id == user.id))
    if q:
        query = query.where(
            or_(
                Task.title.ilike("%" + q + "%"),
                Task.order_no.ilike("%" + q + "%"),
                Task.part_no.ilike("%" + q + "%"),
                Task.description.ilike("%" + q + "%"),
                Task.conversation_id.in_(
                    select(Conversation.id).where(
                        or_(
                            Conversation.name.ilike("%" + q + "%"),
                            Conversation.phone.ilike("%" + q + "%"),
                        )
                    )
                ),
                Task.number == (int(q[2:]) if re.fullmatch(r"T-[0-9]{1,18}", q) else -1),
            )
        )
    if label:
        query = query.where(Task.labels.contains([label]))
    for column, lower, upper in [
        (Task.created_at, created_from, created_to),
        (Task.due_at, due_from, due_to),
    ]:
        if any(x and x.tzinfo is None for x in (lower, upper)):
            raise HTTPException(422, "Укажите часовой пояс даты")
        if lower:
            query = query.where(column >= lower)
        if upper:
            query = query.where(column < upper)
    return (
        query.order_by(Task.due_at.asc().nulls_last(), Task.id)
        if sort == "due"
        else query.order_by(
            {"updated": Task.updated_at, "created": Task.created_at, "number": Task.number}[
                sort
            ].desc(),
            Task.id,
        )
    )


@router.get("/tasks")
def list_tasks(
    query=Depends(filtered_tasks),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "total": total,
        "items": [describe(t, db) for t in db.scalars(query.offset(offset).limit(limit))],
    }


def csv_response(rows, filename):
    output = io.StringIO()
    writer = csv.writer(output)
    for row in rows:
        writer.writerow(
            [
                ("'" + str(cell)) if str(cell).lstrip().startswith(("=", "+", "-", "@")) else cell
                for cell in row
            ]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tasks/export")
def export_tasks(query=Depends(filtered_tasks), db: Session = Depends(get_db)):
    tasks = db.scalars(query.limit(10001)).all()
    if len(tasks) > 10000:
        raise HTTPException(400, "Сузьте фильтр: экспорт ограничен 10 000 задач")
    rows = [
        [
            "Номер",
            "Заголовок",
            "Доска",
            "Статус",
            "Тип",
            "Автор",
            "Исполнитель",
            "Срок UTC",
            "Создана UTC",
            "Закрыта UTC",
            "Заказ",
        ]
    ]
    for task in tasks:
        item = describe(task, db)
        rows.append(
            [
                item[k] or ""
                for k in [
                    "code",
                    "title",
                    "board_name",
                    "status",
                    "type_name",
                    "author_name",
                    "assignee_name",
                    "due_at",
                    "created_at",
                    "completed_at",
                    "order_no",
                ]
            ]
        )
        rows[-1][3] = item["status"]["name"]
    return csv_response(rows, "tasks.csv")


@router.get("/tasks/{key}")
def task_detail(key: str, user=Depends(current_user), db: Session = Depends(get_db)):
    task = task_row(db, key)
    result = describe(task, db)
    result["comments"] = [
        {
            "id": c.id,
            "author_id": c.author_id,
            "author_name": person(db, c.author_id),
            "text": c.body,
            "mentions": c.mentions,
            "created_at": c.created_at,
        }
        for c in db.scalars(
            select(TaskComment)
            .where(TaskComment.task_id == task.id)
            .order_by(TaskComment.created_at)
        )
    ]
    result["history"] = [
        {
            "id": h.id,
            "user_name": person(db, h.user_id),
            "changes": h.changes,
            "created_at": h.created_at,
        }
        for h in db.scalars(
            select(TaskHistory)
            .where(TaskHistory.task_id == task.id)
            .order_by(TaskHistory.created_at.desc())
            .limit(200)
        )
    ]
    result["attachments"] = [
        {
            "id": a.id,
            "name": a.filename,
            "size": a.size,
            "url": "/api/files/" + file_signer().dumps(a.id),
        }
        for a in db.scalars(select(Attachment).where(Attachment.id.in_(task.attachment_ids)))
    ]
    return result


class CommentInput(Input):
    text: str = Field(min_length=1, max_length=10000)
    mentions: list[str] = Field(default_factory=list, max_length=50)


@router.post("/tasks/{key}/comments", status_code=201)
def comment(
    key: str, body: CommentInput, user=Depends(current_user), db: Session = Depends(get_db)
):
    task = task_row(db, key, lock=True)
    for item in body.mentions:
        member(db, item)
    db.add(TaskComment(task_id=task.id, author_id=user.id, body=body.text, mentions=body.mentions))
    task.updated_at = now()
    notify(
        db,
        task,
        [task.author_id, task.assignee_id, *task.watchers, *body.mentions],
        user.id,
        "task.comment",
        "Новый комментарий к задаче",
    )
    db.commit()
    publish_tasks()
    return {"ok": True}


class BoardInput(Input):
    name: str = Field(min_length=1, max_length=80)
    position: int = Field(0, ge=0, le=10000)
    archived: bool = False


def describe_board(board):
    return {
        "id": board.id,
        "name": board.name,
        "position": board.position,
        "archived": board.archived,
        "statuses": sorted(board.statuses, key=lambda s: s["position"]),
    }


@router.get("/boards")
def boards(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        describe_board(b) for b in db.scalars(select(Board).order_by(Board.position, Board.name))
    ]


@router.post("/boards", status_code=201)
def create_board(body: BoardInput, user=Depends(admin_user), db: Session = Depends(get_db)):
    if db.scalar(select(Board.id).where(Board.name == body.name)):
        raise HTTPException(409, "Доска с таким именем уже есть")
    board = Board(
        **body.model_dump(),
        statuses=[
            {"id": uid(), "name": name, "color": color, "position": i, "terminal": i == 2}
            for i, (name, color) in enumerate(
                [("Новая", "blue"), ("В работе", "orange"), ("Готово", "green")]
            )
        ],
    )
    db.add(board)
    db.flush()
    db.add(AuditEvent(user_id=user.id, action="board.created", target=board.id))
    db.commit()
    publish_tasks()
    return describe_board(board)


@router.put("/boards/{board_id}")
def edit_board(
    board_id: str, body: BoardInput, user=Depends(admin_user), db: Session = Depends(get_db)
):
    board = board_row(db, board_id, lock=True)
    if db.scalar(select(Board.id).where(Board.name == body.name, Board.id != board.id)):
        raise HTTPException(409, "Доска с таким именем уже есть")
    for key, value in body.model_dump().items():
        setattr(board, key, value)
    db.add(AuditEvent(user_id=user.id, action="board.updated", target=board.id))
    db.commit()
    publish_tasks()
    return describe_board(board)


class StatusInput(Input):
    name: str = Field(min_length=1, max_length=80)
    color: Literal["gray", "blue", "orange", "green", "red", "violet", "teal"] = "blue"
    terminal: bool = False
    position: int = Field(0, ge=0, le=1000)


@router.post("/boards/{board_id}/statuses", status_code=201)
def create_status(
    board_id: str, body: StatusInput, user=Depends(admin_user), db: Session = Depends(get_db)
):
    board = board_row(db, board_id, lock=True)
    if len(board.statuses) >= 20 or any(s["name"] == body.name for s in board.statuses):
        raise HTTPException(400, "Имя занято или достигнут лимит 20 статусов")
    board.statuses = [*board.statuses, {**body.model_dump(), "id": uid()}]
    db.add(AuditEvent(user_id=user.id, action="board.status_created", target=board.id))
    db.commit()
    publish_tasks()
    return describe_board(board)


@router.put("/boards/{board_id}/statuses/{status_id}")
def edit_status(
    board_id: str,
    status_id: str,
    body: StatusInput,
    user=Depends(admin_user),
    db: Session = Depends(get_db),
):
    board = board_row(db, board_id, lock=True)
    old = state(board, status_id)
    if any(s["name"] == body.name and s["id"] != status_id for s in board.statuses):
        raise HTTPException(400, "Имя статуса уже занято")
    if old["terminal"] != body.terminal and db.scalar(
        select(Task.id).where(Task.board_id == board.id, Task.status_id == status_id).limit(1)
    ):
        raise HTTPException(
            409,
            "В статусе есть задачи. Создайте новый статус и перенесите задачи при удалении старого",
        )
    board.statuses = [
        {**body.model_dump(), "id": status_id} if s["id"] == status_id else s
        for s in board.statuses
    ]
    db.add(AuditEvent(user_id=user.id, action="board.status_updated", target=board.id))
    db.commit()
    publish_tasks()
    return describe_board(board)


@router.delete("/boards/{board_id}/statuses/{status_id}")
def delete_status(
    board_id: str,
    status_id: str,
    move_to: str,
    user=Depends(admin_user),
    db: Session = Depends(get_db),
):
    board = board_row(db, board_id, lock=True)
    state(board, status_id)
    state(board, move_to)
    if status_id == move_to:
        raise HTTPException(400, "Выберите другой статус")
    tasks = db.scalars(
        select(Task)
        .where(Task.board_id == board_id, Task.status_id == status_id)
        .order_by(Task.id)
        .with_for_update()
    ).all()
    for task in tasks:
        fields = TaskFields(**{k: getattr(task, k) for k in TaskFields.model_fields})
        fields.status_id = move_to
        apply_change(db, task, fields, user.id)
    board.statuses = [s for s in board.statuses if s["id"] != status_id]
    db.add(AuditEvent(user_id=user.id, action="board.status_removed", target=board.id))
    db.commit()
    publish_tasks()
    return describe_board(board)


class TypeInput(Input):
    name: str = Field(min_length=1, max_length=80)
    active: bool = True
    position: int = Field(0, ge=0, le=10000)


@router.get("/task-types")
def task_types(user=Depends(current_user), db: Session = Depends(get_db)):
    return [
        {"id": t.id, "name": t.name, "active": t.active, "position": t.position}
        for t in db.scalars(select(TaskType).order_by(TaskType.position, TaskType.name))
    ]


@router.post("/task-types", status_code=201)
def save_type(
    body: TypeInput,
    type_id: str | None = None,
    user=Depends(admin_user),
    db: Session = Depends(get_db),
):
    kind = db.get(TaskType, type_id) if type_id else TaskType()
    if not kind:
        raise HTTPException(404, "Тип не найден")
    existing = db.scalar(select(TaskType).where(TaskType.name == body.name))
    if existing and existing.id != kind.id:
        raise HTTPException(409, "Тип с таким именем уже есть")
    for key, value in body.model_dump().items():
        setattr(kind, key, value)
    db.add(kind)
    db.flush()
    db.add(AuditEvent(user_id=user.id, action="task_type.updated", target=kind.id))
    db.commit()
    publish_tasks()
    return {"id": kind.id, **body.model_dump()}


@router.get("/reports/assignments")
def assignments(
    start: datetime,
    end: datetime,
    board_id: str | None = None,
    type_id: str | None = None,
    format: Literal["json", "csv"] = "json",
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    if start.tzinfo is None or end.tzinfo is None or end <= start or (end - start).days > 3660:
        raise HTTPException(422, "Укажите корректный период с часовым поясом")
    query = select(
        Task.author_id,
        Task.assignee_id,
        func.count().label("total"),
        func.count().filter(Task.completed_at.is_(None)).label("open"),
        func.count().filter(Task.completed_at.is_not(None)).label("completed"),
        func.count().filter(Task.completed_at.is_(None), Task.due_at < now()).label("overdue"),
        func.avg(
            case(
                (
                    Task.completed_at.is_not(None),
                    func.extract("epoch", Task.completed_at - Task.created_at) / 3600,
                )
            )
        ).label("hours"),
    ).where(Task.created_at >= start, Task.created_at < end)
    if user.role != "admin":
        query = query.where(or_(Task.author_id == user.id, Task.assignee_id == user.id))
    if board_id:
        query = query.where(Task.board_id == board_id)
    if type_id:
        query = query.where(Task.type_id == type_id)
    rows = []
    for r in db.execute(query.group_by(Task.author_id, Task.assignee_id)).mappings():
        rows.append(
            {
                **dict(r),
                "author_name": person(db, r["author_id"]),
                "assignee_name": person(db, r["assignee_id"]),
                "hours": round(float(r["hours"]), 1) if r["hours"] is not None else None,
            }
        )
    if format == "csv":
        return csv_response(
            [
                [
                    "Автор",
                    "Исполнитель",
                    "Всего",
                    "Открыто",
                    "Завершено",
                    "Просрочено",
                    "Среднее время выполнения, ч",
                ],
                *[
                    [
                        r[k] if r[k] is not None else ""
                        for k in [
                            "author_name",
                            "assignee_name",
                            "total",
                            "open",
                            "completed",
                            "overdue",
                            "hours",
                        ]
                    ]
                    for r in rows
                ],
            ],
            "assignments.csv",
        )
    return {"start": start, "end": end, "rows": rows}


@router.get("/notifications")
def notifications(
    user=Depends(current_user), db: Session = Depends(get_db), offset: int = Query(0, ge=0)
):
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id)
        .offset(offset)
        .limit(50)
    )
    unread = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return {
        "unread": unread,
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "payload": n.payload,
                "created_at": n.created_at,
                "read_at": n.read_at,
            }
            for n in rows
        ],
    }


class ReadInput(Input):
    ids: list[str] = Field(max_length=100)


@router.post("/notifications/read")
def read_notifications(body: ReadInput, user=Depends(current_user), db: Session = Depends(get_db)):
    db.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.id.in_(body.ids),
            Notification.read_at.is_(None),
        )
        .values(read_at=now())
    )
    db.commit()
    return {"ok": True}


@router.get("/tasks-config")
def tasks_config(user=Depends(current_user), db: Session = Depends(get_db)):
    timezone = get_setting(db, "tasks.timezone", "Asia/Seoul")
    ZoneInfo(timezone)
    return {"timezone": timezone}
