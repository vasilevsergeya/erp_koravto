"""A saturated database pool must not block HTTP/WebSocket event-loop progress."""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.pool import QueuePool

from app.api.auth import COOKIE
from app.api.inbox import channels
from app.core.config import config
from app.main import websocket_updates


class WebsocketPoolTests(unittest.IsolatedAsyncioTestCase):
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
