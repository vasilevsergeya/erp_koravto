"""Task acceptance in selftest's disposable Postgres; no external deliveries."""

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.auth import COOKIE
from app.core.config import config
from app.core.db import SessionLocal
from app.core.security import hash_password, token_hash
from app.core.settings import invalidate_settings, put_setting
from app.main import app
from app.models import (
    Attachment,
    Board,
    Conversation,
    LoginSession,
    Message,
    Notification,
    Task,
    TaskHistory,
    TaskType,
    User,
    now,
    uid,
)
from app.services.tasks import overdue_reminders


@unittest.skipUnless(os.getenv("CRM_INTEGRATION_TEST") == "1", "Requires isolated Postgres")
class TasksAcceptance(unittest.TestCase):
    def setUp(self):
        self.admin, self.staff, self.other = uid(), uid(), uid()
        self.conversation, self.board, self.kind = uid(), uid(), uid()
        self.statuses = [
            {"id": uid(), "name": name, "color": "blue", "position": i, "terminal": i == 2}
            for i, name in enumerate(["Новая", "В работе", "Готово"])
        ]
        with SessionLocal() as db:
            for user_id, role in [
                (self.admin, "admin"),
                (self.staff, "staff"),
                (self.other, "manager"),
            ]:
                db.add(
                    User(
                        id=user_id,
                        email=user_id + "@example.invalid",
                        password_hash=hash_password(uid()),
                        role=role,
                        must_change_password=False,
                    )
                )
            db.flush()
            db.add_all(
                [
                    Board(id=self.board, name=uid(), position=100, statuses=self.statuses),
                    TaskType(id=self.kind, name=uid()),
                    Conversation(
                        id=self.conversation,
                        channel_id=uid(),
                        chat_id=uid(),
                        name="Task test customer",
                    ),
                ]
            )
            db.commit()
        self.client = self.session(self.admin)
        self.staff_client = self.session(self.staff)
        self.tmp = tempfile.TemporaryDirectory()
        self.files = patch("app.services.storage.LOCAL_ROOT", Path(self.tmp.name))
        self.files.start()

    def tearDown(self):
        self.client.close()
        self.staff_client.close()
        self.files.stop()
        self.tmp.cleanup()

    def session(self, user_id):
        raw, csrf = uid(), uid()
        with SessionLocal() as db:
            db.add(
                LoginSession(
                    token_hash=token_hash(raw),
                    user_id=user_id,
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

    def create(self, client=None, **changes):
        body = {
            "client_id": uid(),
            "board_id": self.board,
            "type_id": self.kind,
            "title": "Buy parts 😀",
            "assignee_id": self.staff,
            **changes,
        }
        response = (client or self.client).post("/api/tasks", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), body

    def change(self, task, **changes):
        response = self.client.patch(
            "/api/tasks/" + task["id"], json={"version": task["version"], **changes}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_create_from_chat_retry_notification_and_completion_are_internal(self):
        task, body = self.create(
            conversation_id=self.conversation, order_no="000012", watchers=[self.other]
        )
        duplicate = self.client.post("/api/tasks", json=body)
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.json()["id"], task["id"])
        with SessionLocal() as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.client_id == body["client_id"])
                ),
                1,
            )
            messages = db.scalars(
                select(Message).where(Message.conversation_id == self.conversation)
            ).all()
            self.assertEqual(len(messages), 1)
            self.assertTrue(messages[0].is_private_note)
            self.assertEqual(messages[0].status, "internal")
            self.assertTrue(db.get(Conversation, self.conversation).task_updated)
        notices = self.staff_client.get("/api/notifications").json()
        self.assertEqual(notices["unread"], 1)
        self.assertEqual(notices["items"][0]["payload"]["number"], task["number"])
        done = self.staff_client.patch(
            "/api/tasks/" + task["id"],
            json={
                "version": task["version"],
                "status_id": self.statuses[2]["id"],
                "completion_comment": "Получено на склад",
            },
        )
        self.assertEqual(done.status_code, 200, done.text)
        self.assertIsNotNone(done.json()["completed_at"])
        detail = self.client.get("/api/tasks/" + task["code"]).json()
        self.assertEqual(detail["comments"][0]["text"], "Получено на склад")
        self.assertEqual(len(detail["history"]), 2)
        with SessionLocal() as db:
            messages = db.scalars(
                select(Message).where(Message.conversation_id == self.conversation)
            ).all()
            self.assertEqual(len(messages), 2)
            self.assertTrue(
                all(m.status == "internal" and m.direction == "system" for m in messages)
            )
        self.client.post("/api/conversations/" + self.conversation + "/read")
        self.assertFalse(
            self.client.get("/api/conversations/" + self.conversation).json()["task_updated"]
        )
        reopened = self.change(done.json(), status_id=self.statuses[1]["id"])
        self.assertIsNone(reopened["completed_at"])

    def test_concurrent_edits_and_immutable_author(self):
        task, _ = self.create()
        changed = self.change(task, title="Updated")
        self.assertEqual(changed["version"], 2)
        stale = self.client.patch(
            "/api/tasks/" + task["id"], json={"version": 1, "title": "Lost update"}
        )
        self.assertEqual(stale.status_code, 409)
        forbidden = self.client.patch(
            "/api/tasks/" + task["id"], json={"version": 2, "author_id": self.staff}
        )
        self.assertEqual(forbidden.status_code, 422)
        self.assertEqual(self.client.get("/api/tasks/" + task["id"]).json()["title"], "Updated")

    def test_status_removal_moves_tasks_and_preserves_history(self):
        task, _ = self.create()
        path = f"/api/boards/{self.board}/statuses/{self.statuses[0]['id']}"
        self.assertEqual(self.client.delete(path).status_code, 422)
        self.assertEqual(
            self.staff_client.delete(path, params={"move_to": self.statuses[2]["id"]}).status_code,
            403,
        )
        moved = self.client.delete(path, params={"move_to": self.statuses[2]["id"]})
        self.assertEqual(moved.status_code, 200, moved.text)
        task = self.client.get("/api/tasks/" + task["id"]).json()
        self.assertEqual(task["status_id"], self.statuses[2]["id"])
        self.assertIsNotNone(task["completed_at"])
        self.assertEqual(len(task["history"]), 2)

    def test_board_status_types_and_active_member_validation(self):
        self.assertEqual(
            self.staff_client.post("/api/boards", json={"name": "Forbidden"}).status_code, 403
        )
        created = self.client.post("/api/boards", json={"name": uid()})
        self.assertEqual(created.status_code, 201)
        new_board = created.json()
        task, _ = self.create()
        bad = self.client.patch(
            "/api/tasks/" + task["id"],
            json={"version": 1, "status_id": new_board["statuses"][0]["id"]},
        )
        self.assertEqual(bad.status_code, 400)
        for values in [
            {"board_id": None},
            {"priority": "wrong"},
            {"due_at": "2027-01-01T09:00:00"},
            {"labels": [" "]},
        ]:
            response = self.client.patch("/api/tasks/" + task["id"], json={"version": 1, **values})
            self.assertEqual(response.status_code, 422, response.text)
        with SessionLocal() as db:
            db.get(User, self.other).active = False
            db.commit()
        self.assertEqual(
            self.client.patch(
                "/api/tasks/" + task["id"], json={"version": 1, "assignee_id": self.other}
            ).status_code,
            400,
        )
        task = self.change(task, board_id=new_board["id"])
        self.assertEqual(task["status_id"], new_board["statuses"][0]["id"])
        self.assertEqual(
            self.client.post(
                "/api/boards/" + new_board["id"] + "/statuses",
                json={"name": "Awaiting", "position": 2},
            ).status_code,
            201,
        )

    def test_filters_report_scope_and_csv_formula_protection(self):
        task, _ = self.create(
            title="=HYPERLINK(test)",
            due_at=(now() - timedelta(days=1)).isoformat(),
            labels=["urgent"],
            order_no="000123",
        )
        self.create(assignee_id=self.other)
        own, _ = self.create(client=self.staff_client, assignee_id=self.admin)
        interval = {
            "start": (now() - timedelta(days=1)).isoformat(),
            "end": (now() + timedelta(days=1)).isoformat(),
            "board_id": self.board,
        }
        report = self.staff_client.get("/api/reports/assignments", params=interval)
        self.assertEqual(report.status_code, 200, report.text)
        rows = report.json()["rows"]
        self.assertEqual(sum(r["total"] for r in rows), 2)
        self.assertEqual(sum(r["overdue"] for r in rows), 1)
        admin_report = self.client.get("/api/reports/assignments", params=interval).json()
        self.assertEqual(sum(r["total"] for r in admin_report["rows"]), 3)
        found = self.client.get(
            "/api/tasks", params={"q": "000123", "view": "overdue", "label": "urgent"}
        ).json()
        self.assertEqual([t["id"] for t in found["items"]], [task["id"]])
        data = self.client.get("/api/tasks/export", params={"board_id": self.board}).text
        self.assertIn("'=HYPERLINK(test)", data)
        self.assertIn("000123", data)
        self.change(own, status_id=self.statuses[2]["id"])
        rows = self.staff_client.get("/api/reports/assignments", params=interval).json()["rows"]
        self.assertEqual(sum(r["completed"] for r in rows), 1)
        self.assertTrue(any(r["hours"] is not None for r in rows))

    def test_staff_upload_and_file_ownership(self):
        file = self.staff_client.post(
            "/api/task-uploads", files={"file": ("proof.txt", b"Acceptance proof", "text/plain")}
        )
        self.assertEqual(file.status_code, 201)
        task, _ = self.create(client=self.staff_client, attachment_ids=[file.json()["id"]])
        detail = self.staff_client.get("/api/tasks/" + task["id"]).json()
        self.assertEqual(
            self.staff_client.get(detail["attachments"][0]["url"]).content, b"Acceptance proof"
        )
        forbidden = self.client.post(
            "/api/tasks",
            json={
                "client_id": uid(),
                "title": "Bad upload",
                "board_id": self.board,
                "type_id": self.kind,
                "attachment_ids": [file.json()["id"]],
            },
        )
        self.assertEqual(forbidden.status_code, 400)
        with SessionLocal() as db:
            self.assertEqual(db.get(Attachment, file.json()["id"]).uploaded_by, self.staff)

    def test_mentions_notifications_cannot_mark_another_users_notifications(self):
        task, _ = self.create()
        with SessionLocal() as db:
            before = db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == self.other)
            )
        response = self.staff_client.post(
            "/api/tasks/" + task["id"] + "/comments",
            json={"text": "@manager Please review", "mentions": [self.other]},
        )
        self.assertEqual(response.status_code, 201, response.text)
        with SessionLocal() as db:
            after = db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == self.other)
            )
            self.assertEqual(after, before + 1)
        notices = self.client.get("/api/notifications").json()["items"]
        self.staff_client.post("/api/notifications/read", json={"ids": [n["id"] for n in notices]})
        self.assertTrue(
            all(n["read_at"] is None for n in self.client.get("/api/notifications").json()["items"])
        )
        self.client.post("/api/notifications/read", json={"ids": [n["id"] for n in notices]})
        self.assertEqual(self.client.get("/api/notifications").json()["unread"], 0)

    def test_daily_reminder_deduplicates_and_ignores_completed_tasks(self):
        task, _ = self.create(due_at=(now() - timedelta(days=2)).isoformat())
        completed, _ = self.create(
            due_at=(now() - timedelta(days=2)).isoformat(), status_id=self.statuses[2]["id"]
        )
        with SessionLocal() as db:
            for key, value in [
                ("tasks.timezone", "UTC"),
                ("tasks.reminder_hour", 0),
                ("tasks.workdays", "1,2,3,4,5,6,7"),
            ]:
                put_setting(db, key, value, False, self.admin)
            db.commit()
        invalidate_settings()
        overdue_reminders()
        overdue_reminders()
        with SessionLocal() as db:
            reminders = db.scalars(
                select(Notification).where(
                    Notification.kind == "task.overdue",
                    Notification.user_id.in_([self.admin, self.staff]),
                )
            ).all()
            self.assertEqual(len([n for n in reminders if n.payload["task_id"] == task["id"]]), 2)
            self.assertEqual(
                len([n for n in reminders if n.payload["task_id"] == completed["id"]]), 0
            )

    def test_security_and_no_null_status(self):
        with TestClient(app, base_url=config().public_url) as anonymous:
            self.assertEqual(anonymous.get("/api/tasks").status_code, 401)
            self.assertEqual(anonymous.get("/api/notifications").status_code, 401)
        task, _ = self.create()
        response = self.client.patch(
            "/api/tasks/" + task["id"],
            json={"version": 1, "title": "Bad"},
            headers={"X-CSRF-Token": "bad"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/tasks/" + task["id"]).json()["version"], 1)
        with SessionLocal() as db:
            self.assertEqual(
                db.scalar(
                    select(func.count())
                    .select_from(TaskHistory)
                    .where(TaskHistory.task_id == task["id"])
                ),
                1,
            )
