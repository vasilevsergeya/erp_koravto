import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.api.inbox import writer
from app.api.tasks import csv_response
from app.api.tasks import describe as describe_task
from app.core.db import get_db
from app.models import (
    AuditEvent,
    Conversation,
    Customer,
    CustomerEvent,
    CustomerIdentity,
    CustomerNote,
    Task,
    User,
    now,
)
from app.services.customers import (
    chat_identity,
    customer_row,
    event,
    phone_identity,
    publish_customers,
    structure_lock,
    touch,
)
from app.services.onec_customers import customer_snapshot, refresh_customer
from app.services.tasks import history

router = APIRouter(prefix="/api", tags=["customers"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Profile(Input):
    name: str = Field("", max_length=200)
    language: str = Field("", max_length=20, pattern=r"^$|^[a-zA-Z]{2,3}(-[a-zA-Z]{2,4})?$")
    translate_incoming: bool = False
    translate_outgoing: bool = False
    translation_client_language: str = Field("en", pattern=r"^[a-zA-Z]{2,3}(-[a-zA-Z]{2,4})?$")
    translation_manager_language: str = Field("ru", pattern=r"^[a-zA-Z]{2,3}(-[a-zA-Z]{2,4})?$")
    segment: Literal["", "retail", "small_wholesale", "wholesale"] = ""
    country: str = Field("", pattern=r"^$|^[A-Z]{2}$")
    tags: list[str] = Field(default_factory=list, max_length=50)
    order_numbers: list[str] = Field(default_factory=list, max_length=100)
    default_owner_id: str | None = None

    @field_validator("tags", "order_numbers")
    @classmethod
    def entries(cls, values):
        if any(not x.strip() or len(x.strip()) > 100 for x in values):
            raise ValueError("Invalid entry")
        return list(dict.fromkeys(x.strip() for x in values))


class ProfilePatch(Profile):
    version: int = Field(ge=1)


class CreateCustomer(Profile):
    client_id: uuid.UUID
    phone: str = Field("", max_length=100)
    phone_country: str = Field("RU", pattern=r"^[A-Z]{2}$")


class Version(Input):
    version: int = Field(ge=1)


class IdentityInput(Version):
    phone: str = Field(min_length=5, max_length=100)
    country: str = Field("RU", pattern=r"^[A-Z]{2}$")


class MergeInput(Version):
    into_id: str
    into_version: int = Field(ge=1)
    confirm: bool = False


class DetachInput(Version):
    confirm: bool = False
    name: str = Field("", max_length=200)


class NoteInput(Input):
    client_id: uuid.UUID
    text: str = Field(min_length=1, max_length=20000)


def check_owner(db, owner):
    user = db.get(User, owner) if owner else None
    if owner and (not user or not user.active or user.role not in ("admin", "manager")):
        raise HTTPException(400, "Выберите действующего менеджера")


def related(db, customer):
    return db.scalars(
        select(Conversation)
        .where(Conversation.customer_id == customer.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
    ).all()


def summary(db, customer, chats=None):
    chats = related(db, customer) if chats is None else chats
    phones = db.scalars(
        select(CustomerIdentity)
        .where(CustomerIdentity.customer_id == customer.id, CustomerIdentity.type == "phone")
        .order_by(CustomerIdentity.created_at, CustomerIdentity.id)
    ).all()
    # A manual name always wins; cached 1C is used only when it identifies one client.
    display_name = customer.name
    if not customer.name_manual:
        for c in chats:
            snap = customer_snapshot(c, db)
            if len(snap["clients"]) == 1:
                display_name = snap["clients"][0]["name"] or display_name
                break
    return {
        "id": customer.id,
        "version": customer.version,
        **{field: getattr(customer, field) for field in Profile.model_fields},
        "display_name": display_name or (phones[0].value if phones else "Без имени"),
        "name_manual": customer.name_manual,
        "phones": [{"id": p.id, "value": p.value, "verified": p.verified} for p in phones],
        "conversation_count": len(chats),
        "open_conversations": sum(c.state != "closed" for c in chats),
        "open_tasks": db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.customer_id == customer.id, Task.completed_at.is_(None))
        ),
        "first_contact": min((c.created_at for c in chats), default=None),
        "last_contact": max((c.updated_at for c in chats), default=None),
        "created_at": customer.created_at,
        "updated_at": customer.updated_at,
    }


def detail(db, customer):
    chats = related(db, customer)
    result = summary(db, customer, chats)
    notes = db.scalars(
        select(CustomerNote)
        .where(CustomerNote.customer_id == customer.id)
        .order_by(CustomerNote.created_at.desc(), CustomerNote.id)
        .limit(201)
    ).all()
    events = db.scalars(
        select(CustomerEvent)
        .where(CustomerEvent.customer_id == customer.id)
        .order_by(CustomerEvent.created_at.desc(), CustomerEvent.id)
        .limit(100)
    ).all()

    def actor(user_id):
        user = db.get(User, user_id) if user_id else None
        return user.email if user else "Система"

    tasks = db.scalars(
        select(Task)
        .where(Task.customer_id == customer.id, Task.completed_at.is_(None))
        .order_by(Task.due_at.asc().nulls_last(), Task.id)
        .limit(100)
    ).all()
    result.update(
        {
            "conversations": [
                {
                    "id": c.id,
                    "name": c.name or c.phone,
                    "phone": c.phone,
                    "channel_id": c.channel_id,
                    "state": c.state,
                    "updated_at": c.updated_at,
                    "identity_id": c.customer_identity_id,
                    "phone_verified": bool(phone_identity(c.phone)[0]),
                    "order_numbers": c.order_numbers,
                    "onec": customer_snapshot(c, db),
                }
                for c in chats
            ],
            "notes": [
                {
                    "id": n.id,
                    "text": n.body,
                    "author": actor(n.author_id),
                    "created_at": n.created_at,
                }
                for n in notes[:200]
            ],
            "notes_more": len(notes) > 200,
            "tasks": [describe_task(t, db) for t in tasks],
            "events": [
                {
                    "id": e.id,
                    "action": e.action,
                    "details": e.details,
                    "actor": actor(e.actor_id),
                    "created_at": e.created_at,
                }
                for e in events
            ],
        }
    )
    return result


def customer_query(q, segment, tag, country, has_open):
    query = select(Customer).where(Customer.merged_into_id.is_(None))
    if q.strip():
        q = q.strip()
        digits = "".join(c for c in q if c.isascii() and c.isdigit())
        identities = select(CustomerIdentity.customer_id).where(
            CustomerIdentity.type == "phone",
            CustomerIdentity.value.contains(digits or q, autoescape=True),
        )
        chats = select(Conversation.customer_id).where(
            or_(
                Conversation.name.icontains(q, autoescape=True),
                cast(Conversation.onec_clients, Text).icontains(q, autoescape=True),
                Conversation.order_numbers.contains([q]),
            )
        )
        query = query.where(
            or_(
                Customer.name.icontains(q, autoescape=True),
                Customer.id.in_(identities),
                Customer.id.in_(chats),
                Customer.order_numbers.contains([q]),
            )
        )
    if segment:
        query = query.where(Customer.segment == segment)
    if tag:
        query = query.where(Customer.tags.contains([tag]))
    if country:
        query = query.where(Customer.country == country.upper())
    if has_open:
        query = query.where(
            Customer.id.in_(select(Conversation.customer_id).where(Conversation.state != "closed"))
        )
    return query.order_by(Customer.updated_at.desc(), Customer.id)


@router.get("/customers")
def customers(
    q: str = Query("", max_length=200),
    segment: str = "",
    tag: str = "",
    country: str = "",
    has_open: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    query = customer_query(q, segment, tag, country, has_open)
    return {
        "items": [summary(db, c) for c in db.scalars(query.offset(offset).limit(limit))],
        "total": db.scalar(select(func.count()).select_from(query.order_by(None).subquery())),
    }


@router.get("/customers/export")
def export_customers(
    q: str = Query("", max_length=200),
    segment: str = "",
    tag: str = "",
    country: str = "",
    has_open: bool = False,
    user=Depends(writer),
    db: Session = Depends(get_db),
):
    rows = db.scalars(customer_query(q, segment, tag, country, has_open).limit(10001)).all()
    if len(rows) > 10000:
        raise HTTPException(400, "Сузьте фильтр: экспорт ограничен 10 000 клиентов")
    data = [["Клиент", "Телефоны", "Язык", "Сегмент", "Страна", "Теги", "Открытые диалоги"]]
    for c in rows:
        s = summary(db, c)
        data.append(
            [
                s["display_name"],
                "; ".join(p["value"] for p in s["phones"]),
                c.language,
                c.segment,
                c.country,
                "; ".join(c.tags),
                s["open_conversations"],
            ]
        )
    return csv_response(data, "customers.csv")


@router.post("/customers", status_code=201)
def create_customer(body: CreateCustomer, user=Depends(writer), db: Session = Depends(get_db)):
    structure_lock(db)
    customer = db.get(Customer, str(body.client_id))
    if customer:
        creation = db.scalar(
            select(CustomerEvent).where(
                CustomerEvent.customer_id == customer.id,
                CustomerEvent.action == "created",
                CustomerEvent.actor_id == user.id,
            )
        )
        if not creation or customer.merged_into_id:
            raise HTTPException(409, "Идентификатор уже используется")
        return detail(db, customer)
    if not body.name and not body.phone:
        raise HTTPException(400, "Укажите имя или телефон")
    check_owner(db, body.default_owner_id)
    phone, country = phone_identity(body.phone, body.phone_country)
    if body.phone and not phone:
        raise HTTPException(400, "Укажите действительный телефон с кодом страны")
    if phone and db.scalar(
        select(CustomerIdentity).where(
            CustomerIdentity.type == "phone", CustomerIdentity.value == phone
        )
    ):
        raise HTTPException(409, "Этот телефон уже есть у клиента. Найдите его в списке")
    customer = Customer(
        id=str(body.client_id),
        name_manual=bool(body.name),
        **body.model_dump(exclude={"client_id", "phone", "phone_country"}),
    )
    customer.country = customer.country or country
    db.add(customer)
    db.flush()
    if phone:
        db.add(
            CustomerIdentity(
                customer_id=customer.id, type="phone", value=phone, verified=True, source="user"
            )
        )
    event(db, customer, user.id, "created")
    db.commit()
    publish_customers()
    return detail(db, customer)


@router.get("/customers/{customer_id}")
def get_customer(customer_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Клиент не найден")
    seen = set()
    original = customer.id
    while customer.merged_into_id:
        if customer.id in seen:
            raise HTTPException(409, "Повреждена связь объединения")
        seen.add(customer.id)
        next_customer = db.get(Customer, customer.merged_into_id)
        if next_customer is None:
            raise HTTPException(409, "Повреждена связь объединения")
        customer = next_customer
    return {
        **detail(db, customer),
        "redirected_from": original if original != customer.id else None,
    }


@router.patch("/customers/{customer_id}")
def edit_customer(
    customer_id: str, body: ProfilePatch, user=Depends(writer), db: Session = Depends(get_db)
):
    structure_lock(db)
    c = customer_row(db, customer_id, version=body.version)
    values = body.model_dump(exclude_unset=True, exclude={"version"})
    check_owner(db, values.get("default_owner_id"))
    changes = {k: [getattr(c, k), v] for k, v in values.items() if getattr(c, k) != v}
    if "name" in values:
        if c.name_manual != bool(values["name"]):
            changes["name_manual"] = [c.name_manual, bool(values["name"])]
        c.name_manual = bool(values["name"])
    for key, value in values.items():
        setattr(c, key, value)
    if changes:
        touch(c)
        event(db, c, user.id, "updated", {"fields": list(changes)})
    db.commit()
    publish_customers()
    return detail(db, c)


@router.post("/customers/{customer_id}/identities", status_code=201)
def add_identity(
    customer_id: str, body: IdentityInput, user=Depends(writer), db: Session = Depends(get_db)
):
    structure_lock(db)
    c = customer_row(db, customer_id, version=body.version)
    phone, country = phone_identity(body.phone, body.country)
    if not phone:
        raise HTTPException(400, "Укажите действительный телефон с кодом страны")
    existing = db.scalar(
        select(CustomerIdentity).where(
            CustomerIdentity.type == "phone", CustomerIdentity.value == phone
        )
    )
    if existing:
        if existing.customer_id != c.id:
            raise HTTPException(
                409, "Телефон уже принадлежит другому клиенту. Используйте объединение"
            )
        return detail(db, c)
    db.add(
        CustomerIdentity(customer_id=c.id, type="phone", value=phone, source="user", verified=True)
    )
    touch(c)
    event(db, c, user.id, "phone_added")
    db.commit()
    publish_customers()
    return detail(db, c)


@router.post("/customers/{customer_id}/notes", status_code=201)
def add_note(
    customer_id: str, body: NoteInput, user=Depends(writer), db: Session = Depends(get_db)
):
    structure_lock(db)
    c = customer_row(db, customer_id)
    old = db.scalar(select(CustomerNote).where(CustomerNote.client_id == str(body.client_id)))
    if old:
        if old.customer_id != c.id or old.author_id != user.id:
            raise HTTPException(409, "Идентификатор заметки уже используется")
        return detail(db, c)
    db.add(
        CustomerNote(
            client_id=str(body.client_id), customer_id=c.id, author_id=user.id, body=body.text
        )
    )
    touch(c)
    event(db, c, user.id, "note_added")
    db.commit()
    publish_customers()
    return detail(db, c)


def move_tasks(db, query, destination, actor):
    for task in db.scalars(query.with_for_update()):
        old = task.customer_id
        task.customer_id = destination
        task.version += 1
        task.updated_at = now()
        history(db, task, actor, {"customer_id": [old, destination]})


@router.post("/customers/{customer_id}/merge")
def merge_customer(
    customer_id: str, body: MergeInput, user=Depends(writer), db: Session = Depends(get_db)
):
    if not body.confirm or body.into_id == customer_id:
        raise HTTPException(400, "Подтвердите объединение с другим клиентом")
    structure_lock(db)
    original = db.get(Customer, customer_id)
    if original and original.merged_into_id == body.into_id:
        return get_customer(body.into_id, user, db)
    source = customer_row(db, customer_id, version=body.version)
    target = customer_row(db, body.into_id, version=body.into_version)
    identities = db.scalars(
        select(CustomerIdentity).where(CustomerIdentity.customer_id == source.id)
    ).all()
    chats = related(db, source)
    for item in identities:
        item.customer_id = target.id
    for chat in chats:
        chat.customer_id = target.id
    move_tasks(db, select(Task).where(Task.customer_id == source.id), target.id, user.id)
    for note in db.scalars(select(CustomerNote).where(CustomerNote.customer_id == source.id)):
        note.customer_id = target.id
    for old_event in db.scalars(
        select(CustomerEvent).where(CustomerEvent.customer_id == source.id)
    ):
        old_event.customer_id = target.id
    for field in ["name", "language", "segment", "country", "default_owner_id"]:
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))
            if field == "name":
                target.name_manual = source.name_manual
    target.tags = list(dict.fromkeys([*target.tags, *source.tags]))
    target.order_numbers = list(dict.fromkeys([*target.order_numbers, *source.order_numbers]))
    source.merged_into_id = target.id
    touch(source)
    touch(target)
    event(
        db,
        target,
        user.id,
        "merged",
        {"source_id": source.id, "conversations": len(chats), "identities": len(identities)},
    )
    db.add(
        AuditEvent(user_id=user.id, action="customer.merged", target=source.id + ":" + target.id)
    )
    db.commit()
    publish_customers()
    return detail(db, target)


@router.post("/customers/{customer_id}/identities/{identity_id}/detach")
def detach_identity(
    customer_id: str,
    identity_id: str,
    body: DetachInput,
    user=Depends(writer),
    db: Session = Depends(get_db),
):
    if not body.confirm:
        raise HTTPException(400, "Подтвердите отделение телефона")
    structure_lock(db)
    source = customer_row(db, customer_id, version=body.version)
    identity = db.get(CustomerIdentity, identity_id)
    if not identity or identity.customer_id != source.id or identity.type != "phone":
        raise HTTPException(404, "Телефон клиента не найден")
    target = Customer(
        name=body.name or source.name,
        name_manual=bool(body.name) or source.name_manual,
        language=source.language,
        country=phone_identity(identity.value)[1],
        segment=source.segment,
        tags=list(source.tags),
        default_owner_id=source.default_owner_id,
    )
    db.add(target)
    db.flush()
    chats = db.scalars(
        select(Conversation).where(
            Conversation.customer_id == source.id, Conversation.customer_identity_id == identity.id
        )
    ).all()
    identity.customer_id = target.id
    for chat in chats:
        chat.customer_id = target.id
        scoped = db.scalar(
            select(CustomerIdentity).where(
                CustomerIdentity.type == "wazzup_chat",
                CustomerIdentity.value == chat_identity(chat),
            )
        )
        if scoped:
            scoped.customer_id = target.id
    chat_ids = [c.id for c in chats]
    move_tasks(
        db,
        select(Task).where(Task.customer_id == source.id, Task.conversation_id.in_(chat_ids)),
        target.id,
        user.id,
    )
    touch(source)
    event(
        db,
        source,
        user.id,
        "phone_detached",
        {"destination_id": target.id, "conversations": len(chats)},
    )
    event(db, target, user.id, "split_created", {"source_id": source.id})
    db.add(AuditEvent(user_id=user.id, action="customer.detached", target=identity.id))
    db.commit()
    publish_customers()
    return detail(db, target)


@router.post("/customers/{customer_id}/onec/refresh")
async def refresh_onec(customer_id: str, user=Depends(writer), db: Session = Depends(get_db)):
    c = customer_row(db, customer_id)
    by_phone = {chat.phone: chat.id for chat in related(db, c)}
    db.rollback()  # No database transaction remains open during external requests.
    for conversation_id in list(by_phone.values())[:20]:
        await refresh_customer(conversation_id, force=True)
    db.expire_all()
    return get_customer(customer_id, user, db)
