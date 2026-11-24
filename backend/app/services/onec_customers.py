"""Bounded lookup independent of message delivery; never writes back to 1C."""

import asyncio
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import partial

from sqlalchemy import or_, select, text, update

from app.core.db import SessionLocal
from app.core.settings import get_setting
from app.integrations.onec import OneCAdapter, OneCError, exact_phone, phone_country, phone_digits
from app.models import Conversation, CustomerIdentity, now

log = logging.getLogger("crm")
TTL = timedelta(minutes=15)
ERROR_TTL = timedelta(minutes=1)
# Keep slow 1C I/O off the API event loop and leave three DB slots for CRM traffic.
_LOOKUP_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crm-onec")


def connection_settings(db):
    values = tuple(
        str(get_setting(db, "onec." + key, "")) for key in ("base_url", "username", "password")
    )
    fingerprint = hashlib.sha256(json.dumps(values).encode()).hexdigest()
    return values, fingerprint


def lookup_phone(c):
    return phone_digits(c.phone) if c.chat_type == "whatsapp" else ""


def confirmed_emails(db, customer_id):
    return (
        set(
            db.scalars(
                select(CustomerIdentity.value).where(
                    CustomerIdentity.customer_id == customer_id,
                    CustomerIdentity.type == "email",
                    CustomerIdentity.verified,
                )
            )
        )
        if customer_id
        else set()
    )


def matches_account(account, phone, emails):
    return bool(
        (phone and exact_phone(account.get("matched_phone", "")) == phone)
        or account.get("matched_email", "").strip().lower() in emails
    )


def customer_snapshot(c, db, *, settings=None):
    values, fingerprint = settings or connection_settings(db)
    phone = lookup_phone(c)
    valid = c.onec_phone == phone and c.onec_config == fingerprint
    clients = c.onec_clients if valid else []
    emails = (
        confirmed_emails(db, c.customer_id)
        if any(cl.get("matched_email") for cl in clients)
        else set()
    )
    verified = [cl for cl in clients if matches_account(cl, exact_phone(c.phone), emails)]
    candidates = [cl for cl in clients if cl not in verified]
    clients = verified
    checked = c.onec_checked_at if valid else None
    error = c.onec_error if valid else None
    if not all(values):
        status, clients, checked, error = "not_configured", [], None, "not_configured"
    elif not phone:
        status, clients, checked, error = "invalid_phone", [], None, "invalid_phone"
    elif error:
        status = "unavailable"
    elif not checked:
        status = "pending"
    else:
        status = (
            "matched"
            if len(clients) == 1
            else "multiple"
            if clients
            else "unverified"
            if candidates
            else "not_found"
        )
    return {
        "status": status,
        "clients": clients,
        "candidates": candidates if all(values) and phone else [],
        "phone_country": phone_country(c.phone),
        "checked_at": checked,
        "stale": bool(checked and (error or checked < now() - TTL)),
        "error": error,
    }


async def refresh_customer(conversation_id: str, *, force: bool = False):
    return await asyncio.get_running_loop().run_in_executor(
        _LOOKUP_EXECUTOR, partial(_run_refresh, conversation_id, force)
    )


def _run_refresh(conversation_id: str, force: bool):
    return asyncio.run(_refresh_customer(conversation_id, force=force))


async def _refresh_customer(conversation_id: str, *, force: bool = False):
    with SessionLocal() as db:
        c = db.get(Conversation, conversation_id)
        if c is None:
            return None
        values, fingerprint = connection_settings(db)
        phone = lookup_phone(c)
        if not all(values) or not phone:
            return customer_snapshot(c, db)
        # Independent advisory lock, not a conversation row lock: incoming messages keep flowing.
        acquired = db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "onec:" + conversation_id},
        )
        if not acquired:
            result = customer_snapshot(c, db)
            result["refreshing"] = True
            return result
        valid = c.onec_phone == phone and c.onec_config == fingerprint
        if valid and c.onec_attempted_at:
            if c.onec_attempted_at > now() - timedelta(seconds=10):
                return customer_snapshot(c, db)
            if not force and c.onec_next_at and c.onec_next_at > now():
                return customer_snapshot(c, db)
        # Phone/config changes must never leave the previous customer's data visible.
        if not valid:
            c.onec_clients, c.onec_checked_at = [], None
        c.onec_phone, c.onec_config, c.onec_attempted_at = phone, fingerprint, now()
        try:
            adapter = OneCAdapter(*values)
            accounts = await adapter.customers(phone)
            emails = confirmed_emails(db, c.customer_id)
            for email in emails:
                if any(a.get("matched_email", "").lower() == email for a in accounts):
                    continue
                try:
                    additional = await adapter.customers_by_email(email)
                except OneCError:
                    continue  # Older 1C publications only implement the phone contract.
                known = {a["counterparty_id"] for a in accounts if a.get("counterparty_id")}
                accounts.extend(
                    a
                    for a in additional
                    if a.get("matched_email", "").lower() == email
                    and a.get("counterparty_id") not in known
                    and a not in accounts
                )
            c.onec_clients = accounts
            c.onec_checked_at, c.onec_error = now(), None
            c.onec_next_at = now() + TTL
        except OneCError as error:
            c.onec_error = error.code
            c.onec_next_at = now() + ERROR_TTL
        from app.services.customer_orders import recalculate

        recalculate(db, c.customer_id)
        db.commit()
        return customer_snapshot(c, db)


async def refresh_customers_loop():
    while True:
        try:
            with SessionLocal() as db:
                values, fingerprint = connection_settings(db)
                target = None
                if all(values):
                    target = db.scalar(
                        select(Conversation.id)
                        .where(
                            Conversation.chat_type == "whatsapp",
                            Conversation.phone != "",
                            or_(
                                Conversation.onec_next_at.is_(None),
                                Conversation.onec_config != fingerprint,
                                Conversation.onec_next_at <= now(),
                            ),
                            or_(
                                Conversation.state != "closed",
                                Conversation.onec_checked_at.is_(None),
                                Conversation.updated_at > Conversation.onec_checked_at,
                            ),
                        )
                        .order_by(Conversation.onec_attempted_at.asc().nullsfirst())
                        .limit(1)
                    )
                    # Invalid/group phone IDs should not monopolize the background sweep.
                    if target and not lookup_phone(db.get(Conversation, target)):
                        db.execute(
                            update(Conversation)
                            .where(Conversation.id == target)
                            .values(
                                onec_next_at=now() + TTL,
                                onec_attempted_at=now(),
                                onec_config=fingerprint,
                            )
                        )
                        db.commit()
                        target = None
            if target:
                await refresh_customer(target)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("onec.refresh_failed", extra={"error_type": type(error).__name__})
        await asyncio.sleep(2)
