import asyncio

from mobis_api.client import MobisClient, MobisClientError
from mobis_api.config import Settings


def _settings(*, request_attempts: int = 2, proxies: tuple[str, ...] = ("http://proxy-a:8080", "http://proxy-b:8080")) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8000,
        auth_username="api_user",
        auth_password="change_me",
        mobis_base_url="https://www.mobis-as.com",
        timeout_seconds=30,
        request_attempts=request_attempts,
        max_store_pages=10,
        proxies=proxies,
    )


def test_price_retry_uses_next_proxy_after_black_marker() -> None:
    class StubClient(MobisClient):
        def __init__(self) -> None:
            super().__init__(_settings())
            self.seen_proxies: list[str | None] = []

        async def _get_once(self, path, params, referer_path, proxy):  # type: ignore[no-untyped-def]
            self.seen_proxies.append(proxy)
            if len(self.seen_proxies) == 1:
                return '<input type="hidden" id="black" value="Y" />'
            return """
            <ul class="table-d">
              <li role="row">
                <div><span class="t-td">338002F600</span></div>
                <div><span class="t-td">인젝터 어셈블리-연료</span></div>
                <div><span class="t-td">INJECTOR ASSY-FUEL</span></div>
                <div><span class="t-td">272,800 원</span></div>
              </li>
            </ul>
            """

    client = StubClient()

    parsed = asyncio.run(client.get_brand_price("338002F600", "HYUNDAI"))

    assert parsed is not None
    assert parsed.price == "272800"
    assert client.seen_proxies == ["http://proxy-a:8080", "http://proxy-b:8080"]


def test_collection_raises_after_all_network_attempts_fail() -> None:
    class StubClient(MobisClient):
        async def _get_once(self, path, params, referer_path, proxy):  # type: ignore[no-untyped-def]
            raise MobisClientError(f"failed via {proxy}")

    client = StubClient(_settings(request_attempts=2))

    try:
        asyncio.run(client.get_brand_price("338002F600", "HYUNDAI"))
    except MobisClientError as exc:
        assert "failed after 2 attempt" in str(exc)
    else:
        raise AssertionError("expected MobisClientError")


def test_price_retry_handles_non_httpx_proxy_error() -> None:
    class StubClient(MobisClient):
        def __init__(self) -> None:
            super().__init__(_settings())
            self.calls = 0

        async def _get_once(self, path, params, referer_path, proxy):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                raise MobisClientError("proxy protocol failed")
            return """
            <ul class="table-d">
              <li role="row">
                <div><span class="t-td">253975A001</span></div>
                <div><span class="t-td">쉬라우드-라디에이터</span></div>
                <div><span class="t-td">SHROUD</span></div>
                <div><span class="t-td">17,930 원</span></div>
              </li>
            </ul>
            """

    client = StubClient()

    parsed = asyncio.run(client.get_brand_price("253975A001", "HYUNDAI"))

    assert parsed is not None
    assert parsed.english_name == "SHROUD"
    assert client.calls == 2
