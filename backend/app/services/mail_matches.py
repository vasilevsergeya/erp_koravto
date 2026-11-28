"""Mark mail correspondents using the contact fields of active customer cards."""

import asyncio
import hashlib
import json
import logging
import time
from typing import cast

from itsdangerous import URLSafeSerializer
from redis.exceptions import RedisError
from sqlalchemy import select

from app.core.config import config
from app.core.db import SessionLocal
from app.core.settings import redis_client
from app.integrations.onec import OneCAdapter, OneCError, exact_phone
from app.models import Customer, CustomerIdentity
from app.services.onec_customers import connection_settings

QUEUE = "crm:mail:email-lookups:v2"
HIT_TTL = 24 * 3600
MISS_TTL = 6 * 3600
RETRY_TTL = 60
log = logging.getLogger("crm")


def page_signer(gmail_setting):
    # Bind the page to the configured Gmail account, including after an account switch.
    scope = hashlib.sha256(gmail_setting.encode()).hexdigest()
    return URLSafeSerializer(config().secret_key, salt="mail-dialog-matches:" + scope)


def cache_key(fingerprint, email):
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return f"crm:mail:email-lookup:v2:{fingerprint}:{digest}"


def returned_phones(clients):
    phones = set()
    for client in clients:
        for raw in [*client.get("phones", []), client.get("matched_phone", "")]:
            raw = raw.strip()
            phone = exact_phone("+" + raw[2:] if raw.startswith("00") else raw)
            if phone:
                phones.add(phone)
    return sorted(phones)


def card_values(db, kind, values):
    if not values:
        return set()
    query = (
        select(CustomerIdentity.value)
        .join(Customer, Customer.id == CustomerIdentity.customer_id)
        .where(
            Customer.merged_into_id.is_(None),
            CustomerIdentity.type == kind,
            CustomerIdentity.value.in_(values),
        )
    )
    if kind == "email":
        query = query.where(CustomerIdentity.verified)
    return set(db.scalars(query))


def match_page(db, emails):
    emails = list(dict.fromkeys(email.strip().lower() for email in emails))
    direct = card_values(db, "email", emails)
    matches = dict.fromkeys(direct, "email")
    onec_emails: list[str] = []
    pending = False
    if emails:
        values, fingerprint = connection_settings(db)
        if all(values):
            try:
                cached = cast(
                    list[bytes | None],
                    redis_client.mget([cache_key(fingerprint, email) for email in emails]),
                )
                phones_by_email = {}
                queue = {}
                for email, raw in zip(emails, cached, strict=True):
                    if raw is None:
                        queue[json.dumps([fingerprint, email])] = time.time()
                    elif raw != b"null":
                        lookup = json.loads(raw)
                        if lookup["found"]:
                            onec_emails.append(email)
                            if email not in direct:
                                phones_by_email[email] = lookup["phones"]
                if queue:
                    with redis_client.pipeline() as pipe:
                        pipe.zadd(QUEUE, queue, nx=True)
                        pipe.expire(QUEUE, 3600)
                        pipe.execute()
                    pending = True
                phones = {phone for numbers in phones_by_email.values() for phone in numbers}
                known = card_values(db, "phone", phones)
                for email, numbers in phones_by_email.items():
                    if not known.isdisjoint(numbers):
                        matches[email] = "phone"
            except RedisError:
                pass  # Local email matches remain available; the next poll retries.
    return {"matches": matches, "onec_emails": onec_emails, "pending": pending}


async def remember_email_lookup(fingerprint, email, clients, *, only_if_missing=False):
    # The 1C endpoint matches by exact email. A returned customer can have no phone.
    cached = json.dumps({"found": bool(clients), "phones": returned_phones(clients)})
    try:
        await asyncio.to_thread(
            redis_client.set,
            cache_key(fingerprint, email),
            cached,
            ex=HIT_TTL if clients else MISS_TTL,
            nx=only_if_missing,
        )
    except RedisError:
        log.warning("mail.lookup_cache_unavailable")


def worker_connection():
    with SessionLocal() as db:
        return connection_settings(db)


async def lookup_next(redis):
    job = await redis.bzpopmin(QUEUE, timeout=5)
    if not job:
        return
    fingerprint, email = json.loads(job[1])
    key = cache_key(fingerprint, email)
    if await redis.exists(key):
        return
    # Two consumers may observe the same address while the remote call is in progress.
    # The adapter's hard timeout is 10s; this lease also recovers from worker restarts.
    if not await redis.set(key + ":busy", "1", nx=True, ex=30):
        return
    values, current = await asyncio.to_thread(worker_connection)
    if current != fingerprint or not all(values):
        return
    try:
        clients = await OneCAdapter(*values).customers_by_email(email)
        await remember_email_lookup(fingerprint, email, clients, only_if_missing=True)
    except OneCError:
        await redis.set(key, "null", ex=RETRY_TTL, nx=True)


async def phone_lookup_loop(redis):
    while True:
        try:
            await lookup_next(redis)
        except Exception as exc:
            log.warning("mail.phone_lookup_failed", extra={"error_type": type(exc).__name__})
            await asyncio.sleep(2)
