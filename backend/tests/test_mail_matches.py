import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from itsdangerous import BadSignature
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from app.api import mail
from app.api.auth import current_user
from app.core.config import config
from app.core.db import SessionLocal, get_db
from app.core.settings import redis_client
from app.integrations.onec import OneCError
from app.main import app
from app.models import Conversation, Customer, CustomerIdentity, Message, uid
from app.services import mail_matches as mm


class MailMatchTests(unittest.TestCase):
    def test_normalizes_all_phones_and_rejects_suffixes(self):
        clients = [
            {"phones": ["+1 (202) 555-0123", "001 202 555 0123", "550123"]},
            {"matched_phone": "44 20 7946 0001"},
        ]
        self.assertEqual(mm.returned_phones(clients), ["+12025550123", "+442079460001"])

    def test_page_tickets_are_bound_to_mailbox(self):
        ticket = mm.page_signer("first").dumps(["buyer@example.test"])
        self.assertEqual(mm.page_signer("first").loads(ticket), ["buyer@example.test"])
        with self.assertRaises(BadSignature):
            mm.page_signer("second").loads(ticket)


@unittest.skipUnless(
    os.getenv("CRM_INTEGRATION_TEST") == "1", "Needs isolated PostgreSQL and Redis"
)
class MailMatchAcceptance(unittest.IsolatedAsyncioTestCase):
    async def test_whatsapp_badge_uses_only_correspondence_on_the_matching_card(self):
        direct, by_phone, different_card = [f"{uid()}@example.test" for _ in range(3)]
        phone = "+12025550123"
        fingerprint = uid()
        with (
            SessionLocal() as db,
            patch.object(mm, "connection_settings", return_value=(("url", "u", "p"), fingerprint)),
        ):
            card, other = Customer(), Customer()
            db.add_all([card, other])
            db.flush()
            db.add_all(
                [
                    CustomerIdentity(
                        customer_id=card.id, type="email", value=direct, verified=True
                    ),
                    CustomerIdentity(customer_id=card.id, type="phone", value=phone),
                    CustomerIdentity(
                        customer_id=other.id, type="email", value=different_card, verified=True
                    ),
                ]
            )
            for email in (direct, by_phone, different_card):
                await mm.remember_email_lookup(fingerprint, email, [{"phones": [phone]}])
            whatsapp, telegram = [
                Conversation(customer_id=card.id, channel_id=kind, chat_id=uid(), chat_type=kind)
                for kind in ("whatsapp", "telegram")
            ]
            db.add_all([whatsapp, telegram])
            db.flush()

            def message(chat, direction, private=False):
                return Message(
                    conversation_id=chat.id,
                    channel_id=chat.channel_id,
                    external_id=uid(),
                    direction=direction,
                    author_type="user",
                    is_private_note=private,
                )

            db.add_all([message(telegram, "in"), message(whatsapp, "system", private=True)])
            db.flush()
            emails = [direct, by_phone, different_card]
            self.assertEqual(mm.match_page(db, emails)["whatsapp_emails"], [])
            whatsapp.state = "closed"
            db.add(message(whatsapp, "out"))
            db.flush()
            result = mm.match_page(db, emails)
            self.assertEqual(
                result["matches"], {direct: "email", different_card: "email", by_phone: "phone"}
            )
            self.assertCountEqual(result["whatsapp_emails"], [direct, by_phone])
            # A confirmed email belongs to its own card even when 1C returns another card's phone.
            self.assertNotIn(different_card, result["whatsapp_emails"])
            with patch.object(redis_client, "mget", side_effect=ConnectionError()):
                self.assertEqual(mm.match_page(db, emails)["whatsapp_emails"], [direct])

    async def test_dialog_list_issues_ticket_and_status_uses_only_card(self):
        email = uid() + "@example.test"
        box = "sales@example.test"
        gmail = AsyncMock()
        gmail.dialog_page.return_value = {
            "mailbox": box,
            "dialogs": [{"email": email}],
            "next": None,
        }

        @asynccontextmanager
        async def mailbox(_value):
            yield gmail

        with (
            SessionLocal() as db,
            patch.dict(
                app.dependency_overrides,
                {
                    current_user: lambda: object(),
                    get_db: lambda: db,
                    mail.gmail_setting: lambda: "account",
                },
            ),
            patch.object(mail, "mailbox", mailbox),
            TestClient(
                app, base_url=config().public_url, headers={"Origin": config().public_url}
            ) as client,
        ):
            card = Customer(name="Standalone mail contact")
            db.add(card)
            db.flush()
            address = CustomerIdentity(
                customer_id=card.id, type="email", value=email, verified=True
            )
            db.add(address)
            db.flush()
            listing = client.get("/api/mail/dialogs")
            self.assertEqual(listing.status_code, 200)
            ticket = listing.json()["match_ticket"]
            endpoint = "/api/mail/dialogs/customer-matches"
            checked = client.post(endpoint, json={"ticket": ticket})
            self.assertEqual(
                checked.json(),
                {
                    "matches": {email: "email"},
                    "onec_emails": [],
                    "whatsapp_emails": [],
                    "pending": False,
                },
            )
            self.assertEqual(client.post(endpoint, json={"ticket": "forged"}).status_code, 400)
            gmail.dialog_page.assert_awaited_once_with("", "")

    async def test_exact_card_matches_queue_deduplication_and_card_edit(self):
        fingerprint, tag = uid(), uid()
        direct, buyer, unknown, offline, no_phone, other_phone = [
            s + tag + "@example.test" for s in "abcdef"
        ]
        settings = (("http://onec.invalid", "user", "password"), fingerprint)
        phone = "+12025550123"

        async def lookup(email):
            if email == offline:
                raise OneCError("unavailable")
            if email in (direct, no_phone):
                return [{"name": "Customer", "phones": []}]
            if email == other_phone:
                return [{"phones": ["+1 202 555 0199"]}]
            return [{"phones": ["+1 202 555 0123"]}] if email == buyer else []

        async with Redis.from_url(config().redis_url) as redis:
            # Use a per-test queue so this test only consumes its own jobs.
            with (
                SessionLocal() as db,
                patch.object(mm, "QUEUE", mm.QUEUE + ":" + tag),
                patch.object(mm, "connection_settings", return_value=settings),
                patch.object(
                    mm.OneCAdapter, "customers_by_email", AsyncMock(side_effect=lookup)
                ) as call,
            ):
                customer = Customer(name="Contact test")
                db.add(customer)
                db.flush()
                identity = CustomerIdentity(customer_id=customer.id, type="phone", value=phone)
                db.add_all(
                    [
                        identity,
                        CustomerIdentity(
                            customer_id=customer.id, type="email", value=direct, verified=True
                        ),
                    ]
                )
                db.flush()
                # Card contact with no conversations. Email alone is sufficient even with Redis down.
                with patch.object(redis_client, "mget", side_effect=ConnectionError()):
                    self.assertEqual(
                        mm.match_page(db, [direct.upper(), buyer])["matches"], {direct: "email"}
                    )
                emails = [
                    direct.upper(),
                    buyer,
                    unknown,
                    offline,
                    buyer.upper(),
                    no_phone,
                    other_phone,
                ]
                for _ in range(2):
                    self.assertEqual(
                        mm.match_page(db, emails),
                        {
                            "matches": {direct: "email"},
                            "onec_emails": [],
                            "whatsapp_emails": [],
                            "pending": True,
                        },
                    )
                self.assertEqual(await redis.zcard(mm.QUEUE), 6)
                call.assert_not_awaited()
                await asyncio.gather(*(mm.lookup_next(redis) for _ in range(6)))
                self.assertEqual(call.await_count, 6)
                self.assertEqual(
                    mm.match_page(db, emails),
                    {
                        "matches": {direct: "email", buyer: "phone"},
                        "onec_emails": [direct, buyer, no_phone, other_phone],
                        "whatsapp_emails": [],
                        "pending": False,
                    },
                )
                self.assertEqual(await redis.zcard(mm.QUEUE), 0)
                self.assertLessEqual(
                    await redis.ttl(mm.cache_key(fingerprint, offline)), mm.RETRY_TTL
                )
                self.assertGreater(
                    await redis.ttl(mm.cache_key(fingerprint, no_phone)), mm.MISS_TTL
                )
                # An explicit lookup in the customer panel replaces an earlier miss immediately.
                await mm.remember_email_lookup(fingerprint, unknown.upper(), [{"phones": []}])
                self.assertIn(unknown, mm.match_page(db, emails)["onec_emails"])
                await mm.remember_email_lookup(fingerprint, unknown, [], only_if_missing=True)
                self.assertIn(unknown, mm.match_page(db, emails)["onec_emails"])
                await mm.remember_email_lookup(fingerprint, unknown, [])
                self.assertNotIn(unknown, mm.match_page(db, emails)["onec_emails"])
                # A new card phone with the same suffix must not match a cached full number.
                identity.value = "+13105550123"
                db.flush()
                self.assertEqual(mm.match_page(db, emails)["matches"], {direct: "email"})
                self.assertEqual(call.await_count, 6)
                with patch.object(mm, "connection_settings", return_value=(("", "", ""), "off")):
                    self.assertEqual(
                        mm.match_page(db, emails),
                        {
                            "matches": {direct: "email"},
                            "onec_emails": [],
                            "whatsapp_emails": [],
                            "pending": False,
                        },
                    )
