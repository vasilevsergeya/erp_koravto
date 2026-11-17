import asyncio
from collections import Counter, defaultdict
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from mobis_api.client import MobisClient, MobisClientError, MobisUnavailableError
from mobis_api.config import Settings, load_settings

EMPTY = '<div class="nodata">no results</div>'


def settings(**changes):
    return replace(Settings(
        host="127.0.0.1", port=8000, auth_username="test", auth_password="test",
        mobis_base_url="https://www.mobis-as.com", timeout_seconds=1,
        request_attempts=2, max_store_pages=100,
        proxies=tuple(f"http://proxy-{i}:8080" for i in range(10)),
        proxy_min_interval_seconds=0, proxy_cooldown_seconds=0,
        request_deadline_seconds=2,
    ), **changes)


def html(page, brand, last=10):
    return (
        '<ul class="table-d"><li role="row">'
        '<span class="t-td">Dealer</span><span class="t-td">Brand</span>'
        f'<span class="t-td">{brand}-{page}</span><span class="t-td">Seoul</span>'
        '<span class="t-td">02-123-4567</span></li></ul>'
        f'<ul class="indicator"><li><a onclick="fnSInventoryPageLoad({last})">{last}</a></li></ul>'
    )


class RecordingClient(MobisClient):
    def __init__(self, config=None, last=10):
        super().__init__(config or settings())
        self.last = last
        self.seen = []
        self.active = set()
        self.by_brand = Counter()
        self.peak = 0
        self.peak_by_brand = Counter()
        self.finished = []

    async def response(self, page, brand, proxy):
        await asyncio.sleep(0.003 * (4 - page % 3))
        return html(page, brand, self.last)

    async def _get_once(self, path, params, referer_path, proxy):
        page, brand = params['pageIndex'], params['hkgb']
        assert proxy not in self.active, 'same proxy used concurrently'
        self.active.add(proxy)
        self.by_brand[brand] += 1
        self.peak = max(self.peak, len(self.active))
        self.peak_by_brand[brand] = max(self.peak_by_brand[brand], self.by_brand[brand])
        self.seen.append((page, brand, proxy))
        try:
            result = await self.response(page, brand, proxy)
            self.finished.append((page, brand))
            return result
        finally:
            self.by_brand[brand] -= 1
            self.active.remove(proxy)


class ParallelPagesTests(unittest.IsolatedAsyncioTestCase):
    def assert_released(self, client):
        self.assertFalse(client.active)
        self.assertFalse(any(s.busy for s in client.pool.states))

    async def test_all_pages_ordered_even_when_responses_finish_out_of_order(self):
        c = RecordingClient()
        result = await c.get_brand_stores('877112K000', 'KIA')
        self.assertEqual([s.name for s in result], [f'K-{p}' for p in range(1, 11)])
        self.assertEqual(Counter(p for p, _, _ in c.seen), Counter(range(1, 11)))
        self.assertEqual(c.peak, 3)
        self.assertNotEqual([p for p, _ in c.finished], list(range(1, 11)))
        self.assert_released(c)

    async def test_both_brands_have_independent_three_page_windows(self):
        c = RecordingClient()
        result = await c.get_stores('877112K000')
        for brand, code in [('HYUNDAI', 'H'), ('KIA', 'K')]:
            self.assertEqual([s.name for s in result[brand]], [f'{code}-{p}' for p in range(1, 11)])
            self.assertEqual(c.peak_by_brand[code], 3)
        self.assertEqual(c.peak, 6)
        self.assert_released(c)

    async def test_shared_pool_bounds_multiple_simultaneous_api_calls(self):
        c = RecordingClient(settings(proxies=tuple(f'http://p{i}:80' for i in range(4))), last=5)
        results = await asyncio.gather(*(c.get_stores(str(i)) for i in range(3)))
        self.assertEqual(c.peak, 4)
        self.assertTrue(all(len(r[b]) == 5 for r in results for b in r))
        self.assert_released(c)

    async def test_failed_page_retries_with_another_proxy(self):
        class Retry(RecordingClient):
            failed = False
            async def response(self, page, brand, proxy):
                if page == 3 and not self.failed:
                    self.failed = True
                    return '<input id="maintenance" value="Y">'
                return await super().response(page, brand, proxy)
        c = Retry(last=5)
        self.assertEqual(len(await c.get_brand_stores('877112K000', 'KIA')), 5)
        proxies = [proxy for page, _, proxy in c.seen if page == 3]
        self.assertEqual(len(proxies), 2)
        self.assertNotEqual(proxies[0], proxies[1])
        self.assert_released(c)

    async def test_page_failure_cancels_siblings_and_never_returns_partial_list(self):
        class Failure(RecordingClient):
            async def response(self, page, brand, proxy):
                if page == 1:
                    return html(page, brand, self.last)
                if page == 2:
                    while len(self.active) < 3:
                        await asyncio.sleep(0)
                    raise MobisClientError('unavailable')
                await asyncio.Event().wait()
        c = Failure(settings(request_attempts=1))
        with self.assertRaises(MobisUnavailableError):
            await asyncio.wait_for(c.get_brand_stores('877112K000', 'KIA'), 1)
        self.assert_released(c)

    async def test_empty_page_stops_in_order_and_ignores_later_speculative_error(self):
        class Empty(RecordingClient):
            async def response(self, page, brand, proxy):
                if page == 2:
                    await asyncio.sleep(0.02)
                    return EMPTY
                if page == 3:
                    raise MobisClientError('error beyond no-data boundary')
                return await super().response(page, brand, proxy)
        c = Empty(settings(request_attempts=1))
        result = await c.get_brand_stores('877112K000', 'KIA')
        self.assertEqual([s.name for s in result], ['K-1'])
        self.assertLessEqual(max(p for p, _, _ in c.seen), 4)
        self.assert_released(c)

    async def test_first_page_empty_does_not_request_other_pages(self):
        class Empty(RecordingClient):
            async def response(self, page, brand, proxy):
                return EMPTY
        c = Empty()
        self.assertEqual(await c.get_brand_stores('877112K000', 'KIA'), [])
        self.assertEqual(len(c.seen), 1)

    async def test_single_page_does_not_spawn_more_requests(self):
        c = RecordingClient(last=1)
        self.assertEqual(len(await c.get_brand_stores('877112K000', 'KIA')), 1)
        self.assertEqual(len(c.seen), 1)

    async def test_configured_page_cap_is_respected(self):
        c = RecordingClient(settings(max_store_pages=4))
        self.assertEqual(len(await c.get_brand_stores('877112K000', 'KIA')), 4)
        self.assertEqual(sorted(p for p, _, _ in c.seen), [1, 2, 3, 4])

    async def test_concurrency_one_keeps_sequential_mode(self):
        c = RecordingClient(settings(store_page_concurrency=1), last=5)
        self.assertEqual(len(await c.get_brand_stores('877112K000', 'KIA')), 5)
        self.assertEqual(c.peak, 1)

    async def test_caller_cancellation_releases_all_proxies(self):
        started = asyncio.Event()
        class Blocked(RecordingClient):
            async def response(self, page, brand, proxy):
                if page == 1:
                    return html(page, brand, self.last)
                if len(self.active) == 3:
                    started.set()
                await asyncio.Event().wait()
        c = Blocked()
        task = asyncio.create_task(c.get_brand_stores('877112K000', 'KIA'))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_released(c)

    async def test_deadline_cancels_pending_pages_and_releases_proxies(self):
        class Slow(RecordingClient):
            async def response(self, page, brand, proxy):
                if page == 1:
                    return html(page, brand, self.last)
                await asyncio.Event().wait()
        c = Slow(settings(request_deadline_seconds=0.03))
        with self.assertRaises(MobisUnavailableError):
            await c.get_brand_stores('877112K000', 'KIA')
        self.assert_released(c)

    def test_config_defaults_and_sequential_override(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'config.ini'
            self.assertEqual(load_settings(path).store_page_concurrency, 3)
            path.write_text('[mobis]\nstore_page_concurrency=1\n')
            self.assertEqual(load_settings(path).store_page_concurrency, 1)
            path.write_text('[mobis]\nstore_page_concurrency=0\n')
            self.assertEqual(load_settings(path).store_page_concurrency, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
