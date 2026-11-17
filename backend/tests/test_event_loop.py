"""A saturated database pool must not block HTTP/WebSocket event-loop progress."""

import asyncio
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.pool import QueuePool

from app.api.ai import Decision, draft, send_draft
from app.api.auth import COOKIE
from app.api.inbox import MessageInput, NewConversation, channels, new_conversation, send
from app.core.config import config
from app.main import websocket_updates


class WebsocketPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_bilingual_routes_do_not_block_database_cleanup(self):
        async def exercise(route, *args):
            pool = QueuePool(MagicMock, pool_size=1, max_overflow=0, timeout=0.4)
            held = pool.connect()
            entered = threading.Event()
            released = False

            def blocked_lookup(*_args):
                entered.set()
                connection = pool.connect()
                connection.close()
                raise HTTPException(404, "synthetic lookup complete")

            async def finish_other_request():
                nonlocal released
                while not entered.is_set():
                    await asyncio.sleep(0)
                held.close()
                released = True

            db = MagicMock()
            db.get.side_effect = blocked_lookup
            release = asyncio.create_task(finish_other_request())
            try:
                with self.assertRaises(HTTPException) as error:
                    await route(*args, user=MagicMock(), db=db)
                self.assertEqual(error.exception.status_code, 404)
                self.assertTrue(released, route.__name__)
            finally:
                held.close()
                release.cancel()
                await asyncio.gather(release, return_exceptions=True)
                pool.dispose()

        cid = str(uuid.uuid4())
        cases = [
            (draft, cid),
            (send_draft, cid, Decision()),
            (send, cid, MessageInput(client_id=uuid.uuid4(), text="Hello", source_language="en")),
            (
                new_conversation,
                NewConversation(client_id=uuid.uuid4(), phone="+12025550123", text="Hello"),
            ),
        ]
        for route, *args in cases:
            with self.subTest(route=route.__name__):
                await exercise(route, *args)

    async def exercise_authorization(self, blocked_call):
        pool = QueuePool(MagicMock, pool_size=1, max_overflow=0, timeout=0.4)
        held = pool.connect()
        entered = threading.Event()
        released = False
        calls = 0

        def authorize(_raw):
            nonlocal calls
            calls += 1
            if calls < blocked_call:
                return True
            entered.set()
            connection = pool.connect()
            connection.close()
            return False

        async def finish_other_request():
            nonlocal released
            while not entered.is_set():
                await asyncio.sleep(0)
            held.close()
            released = True

        websocket = SimpleNamespace(
            cookies={COOKIE: "synthetic"},
            headers={"origin": config().public_url},
            close=AsyncMock(),
            accept=AsyncMock(),
            send_json=AsyncMock(),
        )
        redis = MagicMock()
        redis.aclose = AsyncMock()
        pubsub = AsyncMock()
        redis.pubsub.return_value.__aenter__.return_value = pubsub
        release = asyncio.create_task(finish_other_request())
        try:
            with (
                patch("app.main.websocket_authorized", side_effect=authorize),
                patch("app.main.Redis.from_url", return_value=redis),
            ):
                await websocket_updates(websocket)
            self.assertTrue(released, "The event loop must finish another request's DB cleanup")
            websocket.close.assert_awaited_once_with(code=1008)
            self.assertEqual(calls, blocked_call)
            if blocked_call == 2:
                redis.aclose.assert_awaited_once()
        finally:
            held.close()
            release.cancel()
            await asyncio.gather(release, return_exceptions=True)
            pool.dispose()

    async def test_initial_authorization_does_not_block_database_cleanup(self):
        await self.exercise_authorization(1)

    async def test_periodic_authorization_does_not_block_database_cleanup(self):
        await self.exercise_authorization(2)

    async def test_channels_release_database_before_provider_request(self):
        db = MagicMock()
        main_thread = threading.get_ident()

        def read_setting(*args):
            self.assertNotEqual(threading.get_ident(), main_thread)
            return "synthetic-key"

        async def list_channels():
            db.commit.assert_called_once()
            return [{"channelId": "synthetic"}]

        adapter = MagicMock()
        adapter.list_channels = list_channels
        with (
            patch("app.api.inbox.get_setting", side_effect=read_setting),
            patch("app.api.inbox.WazzupAdapter", return_value=adapter),
        ):
            self.assertEqual(await channels(user=MagicMock(), db=db), [{"channelId": "synthetic"}])
