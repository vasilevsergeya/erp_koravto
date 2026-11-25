"""Count eligible 1C orders across confirmed counterparties of one CRM customer."""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.integrations.onec import OneCAdapter, OneCError, exact_phone, phone_digits
from app.models import Conversation, Customer, CustomerOneCLink, now
from app.services.onec_customers import connection_settings

log = logging.getLogger("crm")


def eligible_total(accounts, manual):
    if not accounts:
        return None
    orders = {}
    known_numbers = set()
    for owner, account in accounts.items():
        details = account.get("orders", [])
        if len(details) != len(account["realizations"]):
            return None
        for order in details:
            number = order["number"].strip()
            if not number or not all(
                type(order.get(key)) is bool for key in ("cancelled", "fully_returned")
            ):
                return None
            known_numbers.add(number)
            key = order.get("order_id") or (owner, number)
            orders[key] = not (order["cancelled"] or order["fully_returned"])
    return sum(orders.values()) + len({n.strip() for n in manual if n.strip()} - known_numbers)


def recalculate(db, customer_id):
    if not customer_id:
        return
    profile = db.scalar(select(Customer).where(Customer.id == customer_id).with_for_update())
    if not profile or profile.merged_into_id:
        return
    settings, fingerprint = connection_settings(db)
    accounts = {}
    manual = set(profile.order_numbers)
    # The most recently checked snapshot wins across dialogs of a shared counterparty.
    for c in db.scalars(
        select(Conversation)
        .where(Conversation.customer_id == customer_id)
        .order_by(Conversation.onec_checked_at.asc().nullsfirst(), Conversation.id)
    ):
        manual.update(c.order_numbers)
        phone = exact_phone(c.phone)
        if (
            not all(settings)
            or c.onec_config != fingerprint
            or not c.onec_checked_at
            or len(c.onec_clients) != 1
            or c.onec_phone != phone_digits(c.phone)
        ):
            continue
        account = c.onec_clients[0]
        key = account.get("counterparty_id") or (phone, 0)
        accounts[key] = account
    incomplete = False
    for link in db.scalars(
        select(CustomerOneCLink).where(
            CustomerOneCLink.customer_id == customer_id, CustomerOneCLink.config == fingerprint
        )
    ):
        if link.excluded:
            accounts.pop(link.account_id, None)
        elif link.snapshot and link.account_id not in accounts:
            accounts[link.account_id] = link.snapshot
        elif not link.snapshot and link.account_id not in accounts:
            incomplete = True
    profile.order_count = (
        eligible_total(accounts, manual) if all(settings) and not incomplete else None
    )
    profile.order_config = fingerprint


async def refresh_link():
    with SessionLocal() as db:
        values, fingerprint = connection_settings(db)
        if not all(values):
            return False
        link = db.scalar(
            select(CustomerOneCLink)
            .where(
                CustomerOneCLink.config == fingerprint,
                CustomerOneCLink.excluded.is_(False),
                CustomerOneCLink.next_check <= now(),
            )
            .order_by(CustomerOneCLink.next_check)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not link:
            return False
        key = (link.customer_id, link.config, link.account_id)
        phone = link.lookup_phone
        link.next_check = now() + timedelta(minutes=15)
        db.commit()
    try:
        accounts = await OneCAdapter(*values).customers(phone)
    except OneCError:
        return True
    with SessionLocal() as db:
        link = db.get(CustomerOneCLink, key, with_for_update=True)
        if link and not link.excluded and link.lookup_phone == phone:
            link.snapshot = next(
                (a for a in accounts if a.get("counterparty_id") == link.account_id), None
            )
            recalculate(db, link.customer_id)
            db.commit()
    return True


async def refresh_links_loop():
    while True:
        try:
            active = await refresh_link()
        except Exception as exc:
            log.warning("onec.link_refresh_failed", extra={"error_type": type(exc).__name__})
            active = False
        await asyncio.sleep(2 if active else 30)
