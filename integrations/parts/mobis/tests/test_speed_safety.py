import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from mobis_api.client import MobisClient, MobisClientError, MobisUnavailableError
from mobis_api.config import Settings
from mobis_api.parser import (
    MobisBlockedError, MobisInvalidResponseError, MobisMaintenanceError,
    MobisRateLimitedError, MobisSessionExpiredError, ParsedPrice,
    parse_price, parse_stores,
)
from mobis_api import main

EMPTY = '<input id="black" value="N"><div class="nodata"><p>검색된 부품이 없습니다.</p></div>'
PRICE = '<ul class="table-d"><li role="row"><span class="t-td">877112K000</span><span class="t-td">몰딩</span><span class="t-td">MOULDING</span><span class="t-td">6,270 원</span></li></ul>'
STORES = '<ul class="table-d"><li role="row"><span class="t-td">대리점</span><span class="t-td">기아</span><span class="t-td">Store</span><span class="t-td">Seoul</span><span class="t-td">02-123-4567</span></li></ul>'


def settings(**kw):
    return replace(Settings(
        host="127.0.0.1", port=8000, auth_username="test", auth_password="test",
        mobis_base_url="https://www.mobis-as.com", timeout_seconds=1,
        request_attempts=3, max_store_pages=100,
        proxies=("http://proxy-a:8080", "http://proxy-b:8080"),
        proxy_min_interval_seconds=0, proxy_cooldown_seconds=0,
    ), **kw)


@pytest.mark.parametrize("flag,error", [
    ("maintenance", MobisMaintenanceError), ("maintaince", MobisMaintenanceError),
    ("rateLimited", MobisRateLimitedError), ("sessionExpired", MobisSessionExpiredError),
    ("black", MobisBlockedError),
])
def test_source_error_never_becomes_empty(flag, error):
    html = f'<input name="{flag}" value="Y">' + EMPTY
    with pytest.raises(error):
        parse_price(html, "877112K000")
    with pytest.raises(error):
        parse_stores(html)


@pytest.mark.parametrize("html", ["", "<html>Please log in</html>", '<ul class="table-d"></ul>'])
def test_unknown_markup_is_not_absence(html):
    with pytest.raises(MobisInvalidResponseError):
        parse_price(html, "877112K000")
    with pytest.raises(MobisInvalidResponseError):
        parse_stores(html)


def test_valid_empty_returns_after_one_attempt_and_rotation_survives_calls():
    class Client(MobisClient):
        seen = None
        async def _get_once(self, path, params, referer_path, proxy):
            self.seen.append(proxy)
            return EMPTY
    async def run():
        c = Client(settings())
        c.seen = []
        for _ in range(3):
            assert await c.get_brand_price("877112K000", "HYUNDAI") is None
        assert c.seen == ["http://proxy-a:8080", "http://proxy-b:8080", "http://proxy-a:8080"]
    asyncio.run(run())


def test_maintenance_followed_by_network_errors_does_not_return_null():
    class Client(MobisClient):
        calls = 0
        async def _get_once(self, *args):
            self.calls += 1
            if self.calls == 1:
                return '<input id="maintenance" value="Y">' + EMPTY
            raise MobisClientError("transport error")
    async def run():
        c = Client(settings())
        with pytest.raises(MobisUnavailableError):
            await c.get_brand_price("877112K000", "HYUNDAI")
        assert c.calls == 3
        assert not any(s.busy for s in c.pool.states)
    asyncio.run(run())


def test_sessions_and_cookies_reused_without_crossing_proxies():
    class Client(MobisClient):
        def __init__(self):
            super().__init__(settings())
            self.calls = []
            self.created = []
        def _make_http_client(self, proxy):
            self.created.append(proxy)
            async def handle(request):
                self.calls.append((proxy, request.url.path))
                if request.url.path.endswith("_part.do"):
                    return httpx.Response(200, text="<html>Search</html>", headers={"set-cookie": f"session={proxy[-6]}; Path=/"})
                assert request.headers["cookie"] == f"session={proxy[-6]}"
                assert request.headers["referer"].endswith("/simple_search_part.do")
                return httpx.Response(200, text=PRICE)
            return httpx.AsyncClient(base_url=self.settings.mobis_base_url, transport=httpx.MockTransport(handle))
    async def run():
        c = Client()
        for _ in range(4):
            assert (await c.get_brand_price("877112K000", "KIA")).price == "6270"
        assert len(c.created) == 2
        assert len([p for _, p in c.calls if p.endswith("_part.do")]) == 2
        assert len(c.calls) == 6
        clients = [s.client for s in c.pool.states]
        await c.aclose()
        assert all(client.is_closed for client in clients)
    asyncio.run(run())


def test_each_proxy_has_at_most_one_active_operation_across_api_calls():
    class Client(MobisClient):
        async def _get_once(self, path, params, referer_path, proxy):
            assert proxy not in self.active
            self.active.add(proxy)
            self.max_active = max(self.max_active, len(self.active))
            try:
                await asyncio.sleep(0.01)
                return PRICE
            finally:
                self.active.remove(proxy)
    async def run():
        c = Client(settings())
        c.active = set()
        c.max_active = 0
        await asyncio.gather(*(c.get_prices("877112K000") for _ in range(5)))
        assert c.max_active == 2
        assert not any(s.busy for s in c.pool.states)
    asyncio.run(run())


def test_brands_run_concurrently_and_selected_brand_is_respected():
    class Client(MobisClient):
        async def get_brand_price(self, part, brand):
            self.started.add(brand)
            if len(self.started) == 2:
                self.barrier.set()
            await asyncio.wait_for(self.barrier.wait(), 0.5)
            return ParsedPrice(part, None, None, "100")
    async def run():
        c = Client(settings())
        c.started = set()
        c.barrier = asyncio.Event()
        assert set(await c.get_prices("877112K000")) == {"HYUNDAI", "KIA"}
        c.started.clear()
        assert set(await c.get_prices("877112K000", "KIA")) == {"KIA"}
        assert c.started == {"KIA"}
    asyncio.run(run())


def test_timeout_releases_proxy_for_next_request():
    class Client(MobisClient):
        slow = True
        async def _get_once(self, *args):
            if self.slow:
                await asyncio.sleep(1)
            return PRICE
    async def run():
        c = Client(settings(proxies=(), request_deadline_seconds=0.02))
        with pytest.raises(MobisUnavailableError, match="deadline"):
            await c.get_brand_price("877112K000", "KIA")
        assert not c.pool.states[0].busy
        c.slow = False
        assert await c.get_brand_price("877112K000", "KIA")
    asyncio.run(run())


def test_failed_brand_cancels_sibling():
    class Client(MobisClient):
        async def get_brand_price(self, part, brand):
            if brand == "HYUNDAI":
                await self.started.wait()
                raise MobisUnavailableError("unavailable")
            self.started.set()
            try:
                await asyncio.sleep(5)
            finally:
                self.cancelled.set()
    async def run():
        c = Client(settings())
        c.started, c.cancelled = asyncio.Event(), asyncio.Event()
        with pytest.raises(MobisUnavailableError):
            await c.get_prices("877112K000")
        assert c.cancelled.is_set()
    asyncio.run(run())


def test_full_inventory_still_collects_all_pages_and_rejects_partial_result():
    class Client(MobisClient):
        bad_second = False
        async def _get_once(self, path, params, referer_path, proxy):
            self.pages.append(params["pageIndex"])
            if params["pageIndex"] == 1:
                return STORES + '<ul class="indicator"><li><a onclick="fnSInventoryPageLoad(3)">3</a></li></ul>'
            if self.bad_second:
                return '<input id="maintenance" value="Y">'
            return STORES
    async def run():
        c = Client(settings())
        c.pages = []
        assert len(await c.get_brand_stores("877112K000", "KIA")) == 3
        assert c.pages == [1, 2, 3]
        c.bad_second = True
        with pytest.raises(MobisUnavailableError):
            await c.get_brand_stores("877112K000", "KIA")
    asyncio.run(run())


def test_api_lifespan_shares_client_and_keeps_response_shape(monkeypatch):
    seen = []
    async def get_prices(self, part, brand=None):
        seen.append((id(self), brand))
        return {name: ParsedPrice(part, None, "MOULDING", "6270") for name in ((brand,) if brand else ("HYUNDAI", "KIA"))}
    monkeypatch.setattr(MobisClient, "get_prices", get_prices)
    auth = (main.settings.auth_username, main.settings.auth_password)
    monkeypatch.setenv("ERP_MOBIS_PRICING_URL", "http://crm.test/internal/parts-pricing/mobis")
    async def quote(url, *, params):
        assert all(v == "6270" for v in params.values())
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "sale_usd_HYUNDAI": "11.00" if "hyundai" in params else None,
            "sale_usd_KIA": "11.00" if "kia" in params else None,
        })
    with TestClient(main.app) as client:
        monkeypatch.setattr(main.app.state.pricing_client, "get", quote)
        first = client.get("/api/v1/parts/877112K000/price", auth=auth)
        selected = client.get("/api/v1/parts/877112K000/price?brand=KIA", auth=auth)
        assert first.status_code == selected.status_code == 200
        assert set(first.json()) == {
            "part_number", "korean_name", "english_name", "price_HYUNDAI", "price_KIA",
            "sale_usd_HYUNDAI", "sale_usd_KIA",
        }
        assert first.json()["sale_usd_HYUNDAI"] == "11.00"
        assert selected.json()["sale_usd_HYUNDAI"] is None
        assert selected.json()["sale_usd_KIA"] == "11.00"
        assert selected.json()["price_HYUNDAI"] is None
        assert selected.json()["price_KIA"] == "6270"
        assert seen[0][0] == seen[1][0]
        assert seen[1][1] == "KIA"
        assert client.get("/api/v1/parts/877112K000/price?brand=invalid", auth=auth).status_code == 422


def test_api_source_unavailable_returns_retryable_503(monkeypatch):
    async def fail(*args, **kwargs):
        raise MobisUnavailableError("availability is unknown")
    monkeypatch.setattr(MobisClient, "get_stores", fail)
    with TestClient(main.app) as client:
        response = client.get("/api/v1/parts/877112K000/stores", auth=(main.settings.auth_username, main.settings.auth_password))
        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
        assert "brands" not in response.json()
