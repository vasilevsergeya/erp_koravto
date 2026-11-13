"""Customer acceptance, including migration of existing chats. Synthetic isolated DB only."""

import asyncio
import importlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal, engine
from app.core.security import hash_password, token_hash
from app.main import app
from app.models import (
    Board,
    Conversation,
    CustomerIdentity,
    CustomerNote,
    LoginSession,
    Message,
    Task,
    TaskType,
    User,
    WebhookEvent,
    now,
    uid,
)
from app.services.customers import attach_conversation, phone_identity, structure_lock
from app.workers.worker import process_webhook


class PhoneTests(unittest.TestCase):
    def test_valid_and_invalid_numbers(self):
        self.assertEqual(phone_identity("(202) 555-0101", "US"), ("+12025550101", "US"))
        self.assertEqual(phone_identity("not a number"), (None, ""))
        self.assertEqual(phone_identity("+80012345678")[1], "")


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated Postgres")
class CustomersAcceptance(unittest.TestCase):
    def setUp(self):
        self.admin, self.staff = uid(), uid()
        self.board, self.kind, self.status = uid(), uid(), uid()
        with SessionLocal() as db:
            for ident, role in [(self.admin, "admin"), (self.staff, "staff")]:
                db.add(
                    User(
                        id=ident,
                        email=ident + "@example.invalid",
                        role=role,
                        password_hash=hash_password(uid()),
                        must_change_password=False,
                    )
                )
            db.add(
                Board(
                    id=self.board,
                    name=uid(),
                    position=100,
                    statuses=[
                        dict(id=self.status, name="New", position=0, terminal=False, color="blue")
                    ],
                )
            )
            db.add(TaskType(id=self.kind, name=uid()))
            db.commit()
        self.client = self.session(self.admin)
        self.staff_client = self.session(self.staff)

    def tearDown(self):
        self.client.close()
        self.staff_client.close()

    def session(self, user):
        raw, csrf = uid(), uid()
        with SessionLocal() as db:
            db.add(
                LoginSession(
                    token_hash=token_hash(raw),
                    user_id=user,
                    csrf_token=csrf,
                    expires_at=now() + timedelta(hours=1),
                )
            )
            db.commit()
        client = TestClient(
            app,
            base_url=config().public_url,
            headers={"Origin": config().public_url, "X-CSRF-Token": csrf},
        )
        client.cookies.set(COOKIE, raw)
        return client

    def create(self, **values):
        body = {"client_id": uid(), "name": "Customer " + uid(), **values}
        response = self.client.post("/api/customers", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), body

    def chat(self, phone, name="Shared name", channel=None):
        with SessionLocal() as db:
            structure_lock(db)
            c = Conversation(
                channel_id=channel or uid(), chat_id=phone.lstrip("+"), phone=phone, name=name
            )
            db.add(c)
            db.flush()
            attach_conversation(db, c)
            db.commit()
            return c.id, c.customer_id

    def get(self, customer_id):
        response = self.client.get("/api/customers/" + customer_id)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def task(self, **values):
        response = self.client.post(
            "/api/tasks",
            json=dict(
                client_id=uid(), title="Test task", board_id=self.board, type_id=self.kind, **values
            ),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_identity_resolution_cross_channel_never_by_name_or_invalid_number(self):
        first, customer = self.chat("+12025550111")
        second, same = self.chat("+12025550111")
        third, other = self.chat("+12025550112")
        self.assertEqual(customer, same)
        self.assertNotEqual(customer, other)
        raw1, invalid1 = self.chat("broken-phone")
        raw2, invalid2 = self.chat("broken-phone")
        self.assertNotEqual(invalid1, invalid2)
        self.assertEqual(len(self.get(customer)["phones"]), 1)
        self.assertEqual(self.get(customer)["conversation_count"], 2)
        self.assertFalse(self.get(invalid1)["conversations"][0]["phone_verified"])
        with SessionLocal() as db:
            c = Conversation(channel_id=uid(), chat_id=uid(), chat_type="whatsgroup")
            db.add(c)
            db.flush()
            self.assertIsNone(attach_conversation(db, c))
            db.commit()

    def test_profile_version_filters_csv_permissions_and_retry(self):
        c, body = self.create(name='=HYPERLINK("bad")', phone="+12025550113", tags=["test-tag"])
        duplicate = self.client.post("/api/customers", json=body)
        self.assertEqual(duplicate.json()["id"], c["id"])
        changed = self.client.patch(
            "/api/customers/" + c["id"],
            json={
                "version": c["version"],
                "name": "=formula",
                "segment": "wholesale",
                "country": "US",
                "language": "ar",
                "default_owner_id": self.admin,
            },
        ).json()
        self.assertEqual(changed["version"], 2)
        self.assertEqual(
            self.client.patch(
                "/api/customers/" + c["id"], json={"version": 1, "name": "stale"}
            ).status_code,
            409,
        )
        rows = self.client.get(
            "/api/customers?q=(202)%20555-0113&segment=wholesale&tag=test-tag&country=US"
        ).json()
        self.assertEqual([r["id"] for r in rows["items"]], [c["id"]])
        csv = self.client.get("/api/customers/export?q=12025550113")
        self.assertIn("'=formula", csv.text)
        self.assertEqual(self.staff_client.get("/api/customers/" + c["id"]).status_code, 200)
        self.assertEqual(self.staff_client.post("/api/customers", json=body).status_code, 403)
        self.assertEqual(self.staff_client.get("/api/customers/export").status_code, 403)
        self.assertEqual(
            self.staff_client.patch(
                "/api/customers/" + c["id"], json={"version": 2, "name": "Forbidden"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/customers/" + c["id"] + "/notes",
                json={"client_id": uid(), "text": "note"},
                headers={"X-CSRF-Token": "wrong"},
            ).status_code,
            403,
        )

    def test_merge_and_split_preserve_messages_tasks_notes_and_alias(self):
        chat_a, a = self.chat("+12025550114", name="Source")
        chat_b, b = self.chat("+12025550115", name="Target")
        task_a = self.task(conversation_id=chat_a)
        loose = self.task(customer_id=a)
        task_b = self.task(conversation_id=chat_b)
        note_id = uid()
        note = self.client.post(
            "/api/customers/" + a + "/notes", json={"client_id": note_id, "text": "Keep note 😀"}
        )
        self.assertEqual(note.status_code, 201)
        self.client.post(
            "/api/customers/" + a + "/notes", json={"client_id": note_id, "text": "Keep note 😀"}
        )
        before = self.get(a)
        target = self.get(b)
        with SessionLocal() as db:
            message_ids = list(
                db.scalars(select(Message.id).where(Message.conversation_id.in_([chat_a, chat_b])))
            )
        request = dict(
            version=before["version"], into_id=b, into_version=target["version"], confirm=True
        )
        merged = self.client.post("/api/customers/" + a + "/merge", json=request)
        self.assertEqual(merged.status_code, 200, merged.text)
        result = merged.json()
        self.assertEqual(result["conversation_count"], 2)
        self.assertEqual(len(result["phones"]), 2)
        self.assertEqual(len(result["notes"]), 1)
        self.assertEqual(len(result["tasks"]), 3)
        self.assertEqual(self.get(a)["id"], b)
        self.assertEqual(
            self.client.post("/api/customers/" + a + "/merge", json=request).status_code, 200
        )
        with SessionLocal() as db:
            self.assertEqual(
                message_ids,
                list(
                    db.scalars(
                        select(Message.id).where(Message.conversation_id.in_([chat_a, chat_b]))
                    )
                ),
            )
            self.assertEqual(db.get(Task, task_a["id"]).customer_id, b)
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(CustomerNote)
                    .where(CustomerNote.client_id == note_id)
                ),
                1,
            )
        phone = next(p for p in result["phones"] if p["value"] == "+12025550114")
        split = self.client.post(
            "/api/customers/" + b + "/identities/" + phone["id"] + "/detach",
            json={"version": result["version"], "confirm": True, "name": "Separate"},
        )
        self.assertEqual(split.status_code, 200, split.text)
        new = split.json()
        self.assertEqual([c["id"] for c in new["conversations"]], [chat_a])
        self.assertEqual([t["id"] for t in new["tasks"]], [task_a["id"]])
        self.assertEqual(len(new["notes"]), 0)
        kept = self.get(b)
        self.assertEqual({t["id"] for t in kept["tasks"]}, {loose["id"], task_b["id"]})
        self.assertEqual(len(kept["notes"]), 1)
        extra, new_id = self.chat("+12025550114")
        self.assertEqual(new_id, new["id"])

    def test_conflicts_and_task_link_consistency(self):
        a, _ = self.create(phone="+12025550116")
        b, _ = self.create(phone="+12025550117")
        conflict = self.client.post(
            "/api/customers/" + a["id"] + "/identities",
            json={"version": a["version"], "phone": "+1 202 555 0117", "country": "US"},
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            self.client.post(
                "/api/customers/" + a["id"] + "/merge",
                json={"version": 1, "into_id": b["id"], "into_version": 1},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/customers/" + a["id"] + "/merge",
                json={"version": 1, "into_id": b["id"], "into_version": 999, "confirm": True},
            ).status_code,
            409,
        )
        chat, _ = self.chat("+12025550116")
        response = self.client.post(
            "/api/tasks",
            json=dict(
                client_id=uid(),
                title="Wrong",
                board_id=self.board,
                type_id=self.kind,
                conversation_id=chat,
                customer_id=b["id"],
            ),
        )
        self.assertEqual(response.status_code, 400)
        task = self.task(customer_id=a["id"])
        self.assertEqual(
            self.client.get("/api/tasks?customer_id=" + a["id"]).json()["items"][0]["id"],
            task["id"],
        )
        self.assertEqual(
            self.client.patch(
                "/api/customers/" + a["id"], json={"version": 1, "default_owner_id": self.staff}
            ).status_code,
            400,
        )

    def test_simultaneous_same_phone_creates_one_customer(self):
        def create(_):
            with self.session(self.admin) as client:
                return client.post(
                    "/api/customers", json={"client_id": uid(), "phone": "+12025550118"}
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(create, range(2))), [201, 409])
        with SessionLocal() as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(CustomerIdentity)
                    .where(
                        CustomerIdentity.type == "phone", CustomerIdentity.value == "+12025550118"
                    )
                ),
                1,
            )

    def test_live_webhook_new_outbound_and_default_manager_use_identity(self):
        profile, _ = self.create(phone="+12025550119", default_owner_id=self.admin)
        channel, event_id = uid(), uid()
        with SessionLocal() as db:
            db.add(
                WebhookEvent(
                    id=event_id,
                    payload={
                        "messages": [
                            dict(
                                channelId=channel,
                                chatId="12025550119",
                                chatType="whatsapp",
                                messageId=uid(),
                                type="text",
                                text="hello",
                                status="inbound",
                            )
                        ]
                    },
                )
            )
            db.commit()
        asyncio.run(process_webhook({"redis": AsyncMock()}, event_id))
        c = self.get(profile["id"])
        self.assertEqual(c["conversation_count"], 1)
        chat_id = c["conversations"][0]["id"]
        with SessionLocal() as db:
            self.assertEqual(db.get(Conversation, chat_id).assignee_id, self.admin)
        with patch(
            "app.api.inbox.WazzupAdapter.list_channels",
            new=AsyncMock(
                return_value=[{"channelId": channel, "transport": "whatsapp", "state": "active"}]
            ),
        ):
            response = self.client.post(
                "/api/conversations",
                json={
                    "client_id": uid(),
                    "phone": "+12025550119",
                    "channel_id": channel,
                    "text": "Internal test queue only",
                },
            )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["customer_id"], profile["id"])
        self.assertEqual(response.json()["id"], chat_id)

    def test_busy_customer_lock_defers_webhook_without_losing_it(self):
        event_id = uid()
        with SessionLocal() as db:
            db.add(
                WebhookEvent(
                    id=event_id,
                    payload={
                        "messages": [
                            dict(
                                channelId=uid(),
                                chatId="12025550123",
                                chatType="whatsapp",
                                messageId=uid(),
                                type="text",
                                text="Pending",
                                status="inbound",
                            )
                        ]
                    },
                )
            )
            db.commit()
        with SessionLocal() as owner:
            structure_lock(owner)
            asyncio.run(asyncio.wait_for(process_webhook({"redis": AsyncMock()}, event_id), 2))
            with SessionLocal() as db:
                pending = db.get(WebhookEvent, event_id)
                self.assertEqual(pending.state, "pending")
                self.assertEqual(pending.attempts, 0)
            owner.rollback()
        asyncio.run(process_webhook({"redis": AsyncMock()}, event_id))
        with SessionLocal() as db:
            self.assertEqual(db.get(WebhookEvent, event_id).state, "processed")

    def test_migration_backfills_existing_data_without_rewriting_history(self):
        schema = "test_customer_migration_" + uid().replace("-", "")
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA " + schema))
            connection.execute(text("SET LOCAL search_path TO " + schema + ", public"))
            connection.execute(text("CREATE TABLE users(id varchar(36) PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE conversations(id varchar(36) PRIMARY KEY, channel_id text, chat_type text, chat_id text, phone text, name text, assignee_id varchar(36), created_at timestamptz)"
                )
            )
            connection.execute(
                text("CREATE TABLE tasks(id varchar(36) PRIMARY KEY, conversation_id varchar(36))")
            )
            connection.execute(
                text(
                    "INSERT INTO conversations VALUES ('a','one','whatsapp','12025550121','+12025550121','Name',NULL,now()), ('b','two','whatsapp','12025550121','+12025550121','Other',NULL,now()), ('c','one','whatsapp','12025550122','+12025550122','Name',NULL,now()), ('d','one','whatsgroup','group','group','Group',NULL,now())"
                )
            )
            connection.execute(text("INSERT INTO tasks VALUES ('task','a')"))
            migration = importlib.import_module("app.migrations.versions.0006_customers")
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
            self.assertEqual(connection.scalar(text("SELECT count(*) FROM customers")), 2)
            self.assertEqual(
                connection.scalar(text("SELECT customer_id FROM conversations WHERE id='a'")),
                connection.scalar(text("SELECT customer_id FROM conversations WHERE id='b'")),
            )
            self.assertIsNone(
                connection.scalar(text("SELECT customer_id FROM conversations WHERE id='d'"))
            )
            self.assertIsNotNone(connection.scalar(text("SELECT customer_id FROM tasks")))
            self.assertEqual(
                connection.scalar(text("SELECT name FROM conversations WHERE id='b'")), "Other"
            )
            connection.execute(text("SET LOCAL search_path TO public"))
            connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
