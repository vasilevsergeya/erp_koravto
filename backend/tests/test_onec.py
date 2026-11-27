"""1C contract and isolated CRM acceptance. No requests to live customer systems."""

import asyncio
import base64
import os
import threading
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import token_hash
from app.core.settings import invalidate_settings, put_setting
from app.integrations.onec import OneCAdapter, OneCError, customer_endpoint
from app.main import app
from app.models import Conversation, LoginSession, User, now, uid
from app.services.onec_customers import refresh_customer


class OneCContractTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, handler):
        original = httpx.AsyncClient
        return patch(
            "app.integrations.onec.httpx.AsyncClient",
            side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(handler)),
        )

    async def test_get_normalized_phone_basic_auth_and_duplicate_documents(self):
        clients = [
            {"name": "Одинаковое имя", "realizations": ["#TEST-42", "#TEST-42", "000012"]},
            {"name": "Одинаковое имя", "realizations": []},
        ]

        def respond(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/prod/hs/koravto/customers")
            self.assertEqual(request.url.params["phone"], "12025550123")
            auth = base64.b64encode(b"test-user:test-password").decode()
            self.assertEqual(request.headers["Authorization"], "Basic " + auth)
            return httpx.Response(200, json={"clients": clients})

        with self.transport(respond):
            data = await OneCAdapter(
                "http://onec.invalid/prod", "test-user", "test-password"
            ).customers("+1 (202) 555-0123")
        self.assertEqual(data, clients)

    async def test_endpoint_and_invalid_phone(self):
        for suffix in ("/prod", "/prod/", "/prod/ru_RU/", "/prod/hs/koravto/customers"):
            self.assertEqual(
                customer_endpoint("http://onec.invalid" + suffix),
                "http://onec.invalid/prod/hs/koravto/customers",
            )
        for url in ("file:///etc/passwd", "http://user:pass@host/prod", "https://host/?password=x"):
            with self.assertRaises(OneCError):
                customer_endpoint(url)
        with self.transport(lambda r: self.fail("Invalid phones must not call 1C")):
            for phone in ("12345", "123456789012345678", "12025550123@g.us"):
                with self.assertRaises(OneCError):
                    await OneCAdapter("http://onec.invalid/prod", "u", "p").customers(phone)

    async def test_order_details_preserve_zero_amounts_tracking_and_duplicate_numbers(self):
        clients = [
            {
                "name": "Synthetic customer",
                "country": "Australia",
                "email": "customer@example.com",
                "realizations": ["000123", "000123"],
                "orders": [
                    {
                        "number": "000123",
                        "number_1c": "КА00-000146",
                        "sales_channel_number": "000123",
                        "amount": 1234.56,
                        "shipping_number": "001234567890",
                        "project": "Korean.parts",
                    },
                    {
                        "number": "000123",
                        "number_1c": "000123",
                        "sales_channel_number": "",
                        "amount": 0,
                        "shipping_number": "",
                        "project": "",
                    },
                ],
            },
            {"name": "Synthetic customer", "country": "", "realizations": [], "orders": []},
        ]
        with self.transport(lambda r: httpx.Response(200, json={"clients": clients})):
            result = await OneCAdapter("http://onec.invalid/prod", "u", "p").customers(
                "12025550123"
            )
        self.assertEqual(result, clients)

    async def test_invalid_details_cannot_be_attached_to_other_orders(self):
        order = {"number": "000123", "amount": 12.34, "shipping_number": "0001", "project": "Web"}
        invalid = [
            {"country": None},
            {"email": ["customer@example.com"]},
            {"emails": "customer@example.com"},
            {"phones": [123]},
            {"orders": None},
            {"orders": []},
            {"orders": [order, order]},
            {"orders": [{**order, "number": "different-order"}]},
            {"orders": [{**order, "number_1c": 123}]},
            {"orders": [{**order, "shipping_number": 1}]},
            {"orders": [{**order, "project": {"name": "Web"}}]},
            *({"orders": [{**order, "amount": amount}]} for amount in (None, "12.34", True)),
        ]
        for fields in invalid:
            with self.subTest(fields=fields):
                client = {"name": "Synthetic", "realizations": ["000123"], **fields}
                with self.transport(lambda r: httpx.Response(200, json={"clients": [client]})):
                    with self.assertRaises(OneCError) as caught:
                        await OneCAdapter("http://onec.invalid/prod", "u", "p").customers(
                            "12025550123"
                        )
                self.assertEqual(caught.exception.code, "invalid_response")

    async def test_customer_profile_survives_phone_and_email_lookup(self):
        client = {
            "name": "Alex Morgan",
            "FirstName": "Alex",
            "LastName": "Morgan",
            "NickName": "Alex",
            "Company": "Example Auto Parts",
            "country": "Australia",
            "email": "alex@example.com",
            "emails": ["alex@example.com", "orders@example.com"],
            "phones": ["+61 2 5550 0100", "+61 491 570 006"],
            "Default_address_address1": "25 Example Street",
            "Default_address_address2": "Unit 4",
            "Default_address_address3": "",
            "Default_address_city": "Sydney",
            "Default_address_country": "Australia",
            "Default_address_zip": "0200",
            "Default_address_province_code": "NSW",
            "Ценообразование": "Опт",
            "Incoterms": "FCA",
            "realizations": [],
        }
        with self.transport(lambda r: httpx.Response(200, json={"clients": [client]})):
            adapter = OneCAdapter("http://onec.invalid/prod", "u", "p")
            self.assertEqual(await adapter.customers("12025550123"), [client])
            self.assertEqual(await adapter.customers_by_email("orders@example.com"), [client])

    async def test_nonfinite_amount_is_rejected(self):
        for amount in ("NaN", "Infinity", "-Infinity", "1e999"):
            body = (
                '{"clients":[{"name":"Synthetic","realizations":["01"],'
                '"orders":[{"number":"01","amount":' + amount + ","
                '"shipping_number":"","project":""}]}]}'
            )
            with self.transport(lambda r: httpx.Response(200, text=body)):
                with self.assertRaises(OneCError) as caught:
                    await OneCAdapter("http://onec.invalid/prod", "u", "p").customers("12025550123")
            self.assertEqual(caught.exception.code, "invalid_response")

    async def test_empty_result_and_safe_errors(self):
        for response, code in [
            (httpx.Response(200, json={"clients": []}), None),
            (httpx.Response(401, text="SECRET"), "authentication"),
            (httpx.Response(403, text="SECRET"), "forbidden"),
            (httpx.Response(404), "not_published"),
            (httpx.Response(302, headers={"Location": "https://other.invalid"}), "unavailable"),
            (httpx.Response(500, text="SECRET"), "unavailable"),
            (httpx.Response(200, text="<html>login</html>"), "invalid_response"),
            (
                httpx.Response(200, json={"clients": [{"name": "A", "realizations": [123]}]}),
                "invalid_response",
            ),
        ]:
            with self.transport(lambda r: response):
                adapter = OneCAdapter("http://onec.invalid/prod", "u", "p")
                if code:
                    with self.assertRaises(OneCError) as caught:
                        await adapter.customers("12025550123")
                    self.assertEqual(str(caught.exception), code)
                else:
                    self.assertEqual(await adapter.customers("12025550123"), [])

    async def test_timeout_is_a_safe_unavailable_result(self):
        def timeout(request):
            raise httpx.ReadTimeout("must not leak credentials or phone")

        with self.transport(timeout):
            with self.assertRaises(OneCError) as caught:
                await OneCAdapter("http://onec.invalid/prod", "u", "p").customers("12025550123")
        self.assertEqual(str(caught.exception), "unavailable")


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated Postgres")
class OneCAcceptance(unittest.TestCase):
    def setUp(self):
        self.cid, self.user, self.raw, self.csrf = uid(), uid(), uid(), uid()
        self.original_time = now() - timedelta(days=2)
        with SessionLocal() as db:
            db.add(
                User(
                    id=self.user,
                    email=uid() + "@example.invalid",
                    password_hash="unused",
                    must_change_password=False,
                )
            )
            db.flush()
            db.add(
                LoginSession(
                    token_hash=token_hash(self.raw),
                    user_id=self.user,
                    csrf_token=self.csrf,
                    expires_at=now() + timedelta(hours=1),
                )
            )
            db.add(
                Conversation(
                    id=self.cid,
                    channel_id=uid(),
                    chat_id="12025550123",
                    phone="+1 (202) 555-0123",
                    name="WhatsApp name",
                    order_numbers=["MANUAL-1"],
                    updated_at=self.original_time,
                )
            )
            for key, value, secret in (
                ("base_url", "http://onec.invalid/prod", False),
                ("username", "test-user", False),
                ("password", "test-secret-only", True),
            ):
                put_setting(db, "onec." + key, value, secret, self.user)
            db.commit()
        invalidate_settings()
        self.client = TestClient(
            app,
            base_url=config().public_url,
            headers={"Origin": config().public_url, "X-CSRF-Token": self.csrf},
        )
        self.client.cookies.set(COOKIE, self.raw)
        self.lookup = patch(
            "app.services.onec_customers.OneCAdapter.customers", new_callable=AsyncMock
        )
        self.mock = self.lookup.start()
        self.mock.return_value = [
            {
                "name": "Test Customer",
                "matched_phone": "+12025550123",
                "realizations": ["#TEST-42", "000001"],
            }
        ]

    def tearDown(self):
        self.lookup.stop()
        self.client.close()
        with SessionLocal() as db:
            put_setting(db, "onec.password", "", True, self.user)
            db.commit()
        invalidate_settings()

    def expire(self):
        with SessionLocal() as db:
            c = db.get(Conversation, self.cid)
            c.onec_attempted_at = now() - timedelta(minutes=20)
            c.onec_next_at = now() - timedelta(minutes=1)
            db.commit()

    def test_order_status_follows_verified_sources_and_manual_links(self):
        from app.services.customers import attach_conversation, structure_lock

        with SessionLocal() as db:
            structure_lock(db)
            c = db.get(Conversation, self.cid)
            c.phone = "+12025550871"
            owner = attach_conversation(db, c)
            db.commit()
            customer_id = owner.id
        path = "/api/conversations/" + self.cid

        def count():
            return self.client.get(path).json()["order_count"]

        def sync():
            with SessionLocal() as db:
                db.get(Conversation, self.cid).onec_attempted_at = None
                db.commit()
            self.assertEqual(self.client.post(path + "/customer/refresh").status_code, 200)

        account = {
            "name": "Customer",
            "matched_phone": "+12025550871",
            "counterparty_id": "A",
            "realizations": ["X"],
            "orders": [{"number": "X", "cancelled": False, "fully_returned": False}],
        }
        self.mock.return_value = [account]
        sync()
        self.assertEqual(count(), 2)  # One 1C order and MANUAL-1.
        self.assertIn(
            self.cid, {r["id"] for r in self.client.get("/api/conversations?group=regular").json()}
        )
        account["orders"][0]["fully_returned"] = True
        sync()
        self.assertEqual(count(), 1)
        self.client.patch(path, json={"order_numbers": []})
        self.assertEqual(count(), 0)
        links = "/api/customers/" + customer_id + "/counterparties"
        other = {
            "name": "Second",
            "matched_phone": "+12025550999",
            "counterparty_id": "B",
            "realizations": ["Y"],
            "orders": [{"number": "Y", "cancelled": False, "fully_returned": False}],
        }
        self.mock.return_value = [other]
        self.assertEqual(
            self.client.get(links + "/search", params={"phone": other["matched_phone"]}).json()[0][
                "id"
            ],
            "B",
        )
        selection = {"phone": other["matched_phone"], "account_id": "B", "linked": True}
        self.assertEqual(self.client.put(links, json=selection).status_code, 200)
        self.assertEqual(count(), 1)
        self.assertEqual(
            self.client.put(links, json={**selection, "linked": False}).status_code, 200
        )
        self.assertEqual(count(), 0)
        self.mock.return_value = [account, other]
        sync()
        self.assertIsNone(count())
        self.mock.return_value = [{**account, "matched_phone": other["matched_phone"]}]
        sync()
        self.assertEqual(count(), 0)

    def test_confirmed_email_match_and_removal(self):
        from app.services.customers import attach_conversation, structure_lock

        with SessionLocal() as db:
            structure_lock(db)
            owner = attach_conversation(db, db.get(Conversation, self.cid))
            db.commit()
            key, version = owner.id, owner.version
        profile = "/api/customers/" + key
        self.assertEqual(
            self.client.post(
                profile + "/emails", json={"email": "Shop@Example.test", "version": version}
            ).status_code,
            200,
        )
        self.mock.return_value = []
        account = {
            "name": "Customer",
            "matched_email": "shop@example.test",
            "counterparty_id": "EMA",
            "realizations": [],
        }
        with patch(
            "app.integrations.onec.OneCAdapter.customers_by_email",
            new_callable=AsyncMock,
            return_value=[account],
        ) as lookup:
            self.assertEqual(
                self.client.post("/api/conversations/" + self.cid + "/customer/refresh").json()[
                    "status"
                ],
                "matched",
            )
            lookup.assert_awaited_once_with("shop@example.test")
        self.assertEqual(self.client.get("/api/conversations/" + self.cid).json()["order_count"], 1)
        version = self.client.get(profile).json()["version"]
        self.assertEqual(
            self.client.post(
                profile + "/emails",
                json={"email": "shop@example.test", "version": version, "remove": True},
            ).status_code,
            200,
        )
        self.assertIsNone(self.client.get("/api/conversations/" + self.cid).json()["order_count"])

    def test_lookup_updates_display_search_and_preserves_chat_data(self):
        del self.mock.return_value[0]["matched_phone"]
        path = "/api/conversations/" + self.cid
        result = self.client.get(path + "/customer")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "matched")
        self.assertEqual(result.json()["candidates"], [])
        self.assertEqual(result.json()["clients"][0]["realizations"], ["#TEST-42", "000001"])
        self.client.get(path + "/customer")
        self.mock.assert_awaited_once_with("12025550123")
        c = self.client.get(path).json()
        self.assertEqual(c["display_name"], "Test Customer")
        self.assertEqual(c["name"], "WhatsApp name")
        self.assertEqual(c["order_numbers"], ["MANUAL-1"])
        with SessionLocal() as db:
            self.assertEqual(db.get(Conversation, self.cid).updated_at, self.original_time)
        for term in ("Test Customer", "#TEST-42", "000001"):
            response = self.client.get("/api/conversations", params={"q": term})
            self.assertIn(self.cid, [c["id"] for c in response.json()])

    def test_outage_keeps_cache_marked_stale_and_retry_recovers(self):
        path = "/api/conversations/" + self.cid + "/customer"
        self.client.get(path)
        self.expire()
        self.mock.side_effect = OneCError("unavailable")
        result = self.client.post(path + "/refresh").json()
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["stale"])
        self.assertEqual(result["clients"][0]["name"], "Test Customer")
        self.expire()
        self.mock.side_effect = None
        self.mock.return_value = []
        result = self.client.post(path + "/refresh").json()
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["clients"], [])

    def test_multiple_candidates_never_overwrite_chat_identity(self):
        self.mock.return_value = [
            {"name": "Same", "matched_phone": "+12025550123", "realizations": ["A", "A"]},
            {"name": "Same", "matched_phone": "+12025550123", "realizations": ["B"]},
        ]
        result = self.client.get("/api/conversations/" + self.cid + "/customer").json()
        self.assertEqual(result["status"], "multiple")
        self.assertEqual(result["clients"], [])
        self.assertEqual(result["candidates"], self.mock.return_value)
        c = self.client.get("/api/conversations/" + self.cid).json()
        self.assertEqual(c["display_name"], "WhatsApp name")

    def test_changed_phone_or_source_cannot_show_previous_customer(self):
        asyncio.run(refresh_customer(self.cid))
        with SessionLocal() as db:
            c = db.get(Conversation, self.cid)
            c.phone = "+12025550124"
            db.commit()
        self.mock.side_effect = OneCError("unavailable")
        result = self.client.get("/api/conversations/" + self.cid + "/customer").json()
        self.assertEqual(result["clients"], [])
        self.assertIsNone(result["checked_at"])
        self.assertEqual(self.mock.await_count, 2)
        self.mock.side_effect = None
        self.mock.return_value[0]["matched_phone"] = "+12025550124"
        with SessionLocal() as db:
            put_setting(db, "onec.username", "another-user", False, self.user)
            db.commit()
        invalidate_settings()
        result = self.client.get("/api/conversations/" + self.cid + "/customer").json()
        self.assertEqual(result["status"], "matched")
        self.assertEqual(self.mock.await_count, 3)

    def test_disabled_credentials_are_explicit_and_password_is_not_returned(self):
        settings = self.client.get("/api/settings").json()
        password = next(s for s in settings if s["key"] == "onec.password")
        self.assertEqual(password["value"], "••••")
        with SessionLocal() as db:
            put_setting(db, "onec.password", "", True, self.user)
            db.commit()
        invalidate_settings()
        result = self.client.get("/api/conversations/" + self.cid + "/customer").json()
        self.assertEqual(result["status"], "not_configured")
        self.mock.assert_not_awaited()

    def test_auth_csrf_and_staff_read_only(self):
        path = "/api/conversations/" + self.cid + "/customer"
        anonymous = TestClient(app, base_url=config().public_url)
        self.assertEqual(anonymous.get(path).status_code, 401)
        anonymous.close()
        self.client.headers["X-CSRF-Token"] = "invalid"
        self.assertEqual(self.client.post(path + "/refresh").status_code, 403)
        self.client.headers["X-CSRF-Token"] = self.csrf
        with SessionLocal() as db:
            db.get(User, self.user).role = "staff"
            db.commit()
        self.assertEqual(self.client.post(path + "/refresh").status_code, 403)
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.get("/api/settings").status_code, 403)

    def test_simultaneous_refresh_does_not_duplicate_the_1c_request(self):
        with SessionLocal() as other:
            other.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "onec:" + self.cid},
            )
            result = asyncio.run(refresh_customer(self.cid))
            self.assertTrue(result["refreshing"])
            self.mock.assert_not_awaited()
        self.assertEqual(asyncio.run(refresh_customer(self.cid))["status"], "matched")
        self.mock.assert_awaited_once()

    def test_parallel_cold_lookups_leave_event_loop_and_database_available(self):
        ids = [uid() for _ in range(7)]
        with SessionLocal() as db:
            for ident in ids:
                db.add(
                    Conversation(
                        id=ident, channel_id=uid(), chat_id="12025550123", phone="+12025550123"
                    )
                )
            db.commit()
        running = peak = 0
        guard = threading.Lock()

        async def slow_provider(phone):
            nonlocal running, peak
            with guard:
                running += 1
                peak = max(peak, running)
            try:
                await asyncio.sleep(0.15)
                return [
                    {
                        "name": "Synthetic customer",
                        "matched_phone": "+12025550123",
                        "realizations": [],
                    }
                ]
            finally:
                with guard:
                    running -= 1

        self.mock.side_effect = slow_provider

        async def exercise():
            ticks = 0
            finished = False

            async def heartbeat():
                nonlocal ticks
                while not finished:
                    ticks += 1
                    await asyncio.sleep(0.02)

            pulse = asyncio.create_task(heartbeat())
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=config().public_url,
                    cookies={COOKIE: self.raw},
                ) as client:
                    responses = await asyncio.wait_for(
                        asyncio.gather(
                            *(client.get("/api/conversations/" + i + "/customer") for i in ids)
                        ),
                        10,
                    )
                self.assertTrue(all(r.status_code == 200 for r in responses))
                return [r.json() for r in responses], ticks
            finally:
                finished = True
                await pulse

        rows, ticks = asyncio.run(exercise())
        self.assertTrue(all(r["status"] == "matched" for r in rows))
        self.assertGreater(ticks, 3)
        self.assertLessEqual(peak, 2)
        self.assertEqual(self.mock.await_count, 7)
