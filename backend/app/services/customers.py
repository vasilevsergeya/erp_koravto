"""Customer identity ownership. Structural writes share one transaction lock.

Take this lock before conversation/task/board row locks, including inbound ingestion.
Names and unverified telephone strings never resolve a person across channels.
"""

import hashlib
import json
import re

import phonenumbers
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.core.settings import redis_client
from app.models import (
    Customer,
    CustomerEvent,
    CustomerIdentity,
    CustomerNumber,
    CustomerProfileNote,
    now,
)


def structure_lock(db, *, wait=True):
    if not wait:
        return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(734011)")))
    db.execute(text("SELECT pg_advisory_xact_lock(734011)"))
    return True


def phone_identity(value, country=None):
    try:
        parsed = phonenumbers.parse(value, country)
        if phonenumbers.is_valid_number(parsed):
            return (
                phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
                (phonenumbers.region_code_for_number(parsed) or "")[:2]
                if phonenumbers.region_code_for_number(parsed) != "001"
                else "",
            )
    except phonenumbers.NumberParseException:
        pass
    return None, ""


def chat_identity(conversation):
    return hashlib.sha256(
        json.dumps(
            [conversation.channel_id, conversation.chat_type, conversation.chat_id],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def customer_row(db, customer_id, *, version=None):
    customer = db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Клиент не найден")
    if customer.merged_into_id:
        raise HTTPException(409, "Клиент объединён. Откройте актуальную карточку")
    if version is not None and customer.version != version:
        raise HTTPException(409, "Карточку уже изменили. Обновите её и повторите действие")
    return customer


def touch(customer):
    customer.version += 1
    customer.updated_at = now()


def event(db, customer, actor, action, details=None):
    db.add(
        CustomerEvent(customer_id=customer.id, actor_id=actor, action=action, details=details or {})
    )


def attach_conversation(db, conversation):
    """Caller holds structure_lock. No sends, commits, or external requests."""
    if conversation.customer_id:
        return db.get(Customer, conversation.customer_id)
    if conversation.chat_type != "whatsapp":
        return None  # A group or future channel is not a person identified by its chat ID.
    value, country = phone_identity(conversation.phone or "+" + conversation.chat_id)
    scoped = chat_identity(conversation)
    identifier = db.scalar(
        select(CustomerIdentity).where(
            CustomerIdentity.type == ("phone" if value else "wazzup_chat"),
            CustomerIdentity.value == (value or scoped),
        )
    )
    if identifier:
        customer = customer_row(db, identifier.customer_id)
    else:
        customer = Customer(
            name=conversation.name, country=country, default_owner_id=conversation.assignee_id
        )
        db.add(customer)
        db.flush()
        identifier = CustomerIdentity(
            customer_id=customer.id,
            type="phone" if value else "wazzup_chat",
            value=value or scoped,
            source="wazzup",
            verified=bool(value),
        )
        db.add(identifier)
        db.flush()
    if value:
        chat = db.scalar(
            select(CustomerIdentity).where(
                CustomerIdentity.type == "wazzup_chat", CustomerIdentity.value == scoped
            )
        )
        if chat is None:
            db.add(
                CustomerIdentity(
                    customer_id=customer.id,
                    type="wazzup_chat",
                    value=scoped,
                    source="wazzup",
                    verified=True,
                )
            )
        conversation.phone = value
    conversation.customer_id = customer.id
    conversation.customer_identity_id = identifier.id
    if not conversation.assignee_id and customer.default_owner_id:
        conversation.assignee_id = customer.default_owner_id
    return customer


def publish_customers():
    try:
        redis_client.publish("crm:events", json.dumps({"type": "customers.changed"}))
    except Exception:
        pass  # Polling recovers committed changes if Redis is unavailable.


def parse_customer_number(value):
    match = re.fullmatch(r"[cс]-?([0-9]+)", value.strip(), re.IGNORECASE)
    if not match:
        return None
    number = int(match[1])
    return number if number <= 9223372036854775807 else 0


def numbered_customer(number):
    return select(CustomerNumber.customer_id).where(CustomerNumber.number == number)


def display_number(db, customer_id):
    if not customer_id:
        return None
    number = db.scalar(
        select(func.min(CustomerNumber.number)).where(CustomerNumber.customer_id == customer_id)
    )
    return f"C-{number:05d}" if number else None


def latest_profile_note(db, customer_id):
    return db.scalar(
        select(CustomerProfileNote)
        .where(CustomerProfileNote.customer_id == customer_id)
        .order_by(CustomerProfileNote.id.desc())
        .limit(1)
    )
