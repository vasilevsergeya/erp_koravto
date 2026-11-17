from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar

import httpx

from .config import Settings
from .parser import (
    MobisBlockedError, MobisMaintenanceError, MobisRateLimitedError,
    MobisSessionExpiredError, MobisSourceError, ParsedPrice, ParsedStore,
    parse_max_page, parse_price, parse_stores, validate_source_status,
)

BRANDS = {"HYUNDAI": "H", "KIA": "K"}
logger = logging.getLogger(__name__)
T = TypeVar("T")


class MobisClientError(RuntimeError):
    pass


class MobisUnavailableError(MobisClientError):
    """All bounded attempts failed; the result is unknown, never absent."""


@dataclass
class ProxyState:
    index: int
    proxy: str | None = field(repr=False)
    client: httpx.AsyncClient | None = field(default=None, repr=False)
    warmed: dict[str, float] = field(default_factory=dict)
    busy: bool = False
    available_at: float = 0.0
    failures: int = 0
    completed: int = 0
    latency_seconds: float | None = None


class ProxyPool:
    """One active operation per proxy; fair rotation across all API requests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        proxies = tuple(dict.fromkeys(settings.proxies)) or (None,)
        self.states = [ProxyState(i, proxy) for i, proxy in enumerate(proxies)]
        self.cursor = 0
        self.condition = asyncio.Condition()

    async def acquire(self, attempted: set[int]) -> ProxyState:
        async with self.condition:
            while True:
                now = time.monotonic()
                # Prefer a different egress for each retry. If every egress was
                # tried, the same pool can be used again after its cooldown.
                choices = [s for s in self.states if s.index not in attempted] or self.states
                ready = [s for s in choices if not s.busy and s.available_at <= now]
                if ready:
                    state = min(ready, key=lambda s: (s.index - self.cursor) % len(self.states))
                    self.cursor = (state.index + 1) % len(self.states)
                    state.busy = True
                    state.available_at = now + self.settings.proxy_min_interval_seconds
                    return state
                deadlines = [s.available_at for s in choices if not s.busy]
                delay = max(0.001, min(deadlines) - now) if deadlines else None
                try:
                    await asyncio.wait_for(self.condition.wait(), timeout=delay)
                except TimeoutError:
                    pass

    async def release(self, state: ProxyState, elapsed: float, error: Exception | None) -> None:
        async with self.condition:
            state.busy = False
            state.completed += 1
            state.latency_seconds = elapsed if state.latency_seconds is None else (
                0.8 * state.latency_seconds + 0.2 * elapsed
            )
            cooldown = 0.0
            if error is not None:
                state.failures += 1
                cooldown = min(30.0, self.settings.proxy_cooldown_seconds * 2 ** min(state.failures - 1, 4))
                if isinstance(error, (MobisBlockedError, MobisRateLimitedError)):
                    cooldown = max(cooldown, 30.0)
                elif isinstance(error, MobisSessionExpiredError):
                    cooldown = self.settings.proxy_cooldown_seconds
            else:
                state.failures = 0
                if self.settings.proxy_slow_seconds and state.latency_seconds > self.settings.proxy_slow_seconds:
                    cooldown = self.settings.proxy_cooldown_seconds
            state.available_at = max(state.available_at, time.monotonic() + cooldown)
            self.condition.notify_all()


class MobisClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = ProxyPool(settings)
        self._states_by_proxy = {s.proxy: s for s in self.pool.states}

    def _attempt_count(self) -> int:
        return max(1, self.settings.request_attempts)

    def _make_http_client(self, proxy: str | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.mobis_base_url,
            headers={
                "Accept": "text/html, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "X-Requested-With": "XMLHttpRequest",
            },
            proxy=proxy,
            timeout=httpx.Timeout(self.settings.timeout_seconds, connect=min(5.0, self.settings.timeout_seconds)),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=60.0),
            follow_redirects=True,
        )

    async def _discard_client(self, state: ProxyState) -> None:
        client, state.client = state.client, None
        state.warmed.clear()
        if client is not None:
            await client.aclose()

    async def aclose(self) -> None:
        await asyncio.gather(*(self._discard_client(state) for state in self.pool.states))

    async def _get_once(
        self, path: str, params: dict[str, str | int], referer_path: str, proxy: str | None,
    ) -> str:
        state = self._states_by_proxy[proxy]
        if state.client is None:
            state.client = self._make_http_client(proxy)
        client = state.client
        headers = {"Referer": f"{self.settings.mobis_base_url}{referer_path}"}
        try:
            warmed_at = state.warmed.get(referer_path)
            if warmed_at is None or time.monotonic() - warmed_at >= self.settings.session_ttl_seconds:
                warmup = await client.get(referer_path, headers=headers)
                warmup.raise_for_status()
                validate_source_status(warmup.text)
                state.warmed[referer_path] = time.monotonic()
            response = await client.get(path, params=params, headers=headers)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise MobisRateLimitedError("Mobis returned HTTP 429") from None
            raise MobisClientError(f"Mobis returned HTTP {exc.response.status_code}") from None
        except httpx.HTTPError as exc:
            # Never put proxy URLs/passwords or raw transport exceptions in API errors/logs.
            raise MobisClientError(f"Mobis transport error ({type(exc).__name__})") from None
        except MobisSourceError:
            raise
        except Exception as exc:
            raise MobisClientError(f"Mobis client error ({type(exc).__name__})") from None

    async def _collect_with_retry(
        self, path: str, params: dict[str, str | int], referer_path: str, parser: Callable[[str], T],
    ) -> T:
        last_error: Exception | None = None
        attempted: set[int] = set()
        try:
            async with asyncio.timeout(self.settings.request_deadline_seconds):
                for attempt in range(1, self._attempt_count() + 1):
                    state = await self.pool.acquire(attempted)
                    attempted.add(state.index)
                    started = time.monotonic()
                    error: Exception | None = None
                    try:
                        html = await self._get_once(path, params, referer_path, state.proxy)
                        # Parsers only return empty for a validated no-data response.
                        # Maintenance, expired sessions, rate limits and malformed HTML raise.
                        return parser(html)
                    except (MobisSourceError, MobisClientError) as exc:
                        error = last_error = exc
                        await self._discard_client(state)
                        logger.warning(
                            "Mobis attempt %s/%s failed on proxy #%s (%s)",
                            attempt, self._attempt_count(), state.index + 1, type(exc).__name__,
                        )
                    finally:
                        await self.pool.release(state, time.monotonic() - started, error)
        except TimeoutError:
            raise MobisUnavailableError("Mobis request deadline exceeded; availability is unknown") from None
        error_type = type(last_error).__name__ if last_error else "unknown"
        raise MobisUnavailableError(
            f"Mobis collection failed after {self._attempt_count()} attempt(s) ({error_type}); availability is unknown"
        ) from None

    async def _brands(self, operation: Callable[[str], Awaitable[T]], brand: str | None) -> dict[str, T]:
        names = (brand,) if brand is not None else tuple(BRANDS)
        if any(name not in BRANDS for name in names):
            raise ValueError("brand must be HYUNDAI or KIA")
        tasks = [asyncio.create_task(operation(name)) for name in names]
        try:
            values = await asyncio.gather(*tasks)
        except BaseException:
            # A failed brand must not leave its sibling issuing requests in the background.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return dict(zip(names, values))

    async def get_brand_price(self, part_number: str, brand: str) -> ParsedPrice | None:
        return await self._collect_with_retry(
            "/simple_search_partLoad_v2.do",
            {"pageIndex": 1, "hkgb": BRANDS[brand], "vtyp": "", "catSeq": "", "srchType": "ptno", "inText": part_number},
            "/simple_search_part.do", lambda html: parse_price(html, part_number),
        )

    async def get_prices(self, part_number: str, brand: str | None = None) -> dict[str, ParsedPrice | None]:
        return await self._brands(lambda name: self.get_brand_price(part_number, name), brand)

    async def get_brand_stores(self, part_number: str, brand: str) -> list[ParsedStore]:
        def parse_store_page(html: str) -> tuple[list[ParsedStore], int]:
            return parse_stores(html), parse_max_page(html)

        params = {"pageIndex": 1, "hkgb": BRANDS[brand], "stype": "", "sido": "", "sigungu": "", "ptno": part_number}
        stores, max_page = await self._collect_with_retry(
            "/simple_search_inventoryLoad_v2.do", params, "/simple_search_inventory.do", parse_store_page,
        )
        if not stores:
            return stores
        last_page = min(max_page, self.settings.max_store_pages)
        concurrency = min(max(1, self.settings.store_page_concurrency), len(self.pool.states))

        async def fetch_page(page: int) -> list[ParsedStore]:
            return await self._collect_with_retry(
                "/simple_search_inventoryLoad_v2.do", {**params, "pageIndex": page},
                "/simple_search_inventory.do", parse_stores,
            )

        # Keep only a bounded window in flight. Consume in page order so the
        # response order and the first explicit no-data boundary stay unchanged.
        pending: dict[int, asyncio.Task[list[ParsedStore]]] = {}
        next_page = 2
        try:
            while next_page <= last_page and len(pending) < concurrency:
                pending[next_page] = asyncio.create_task(fetch_page(next_page))
                next_page += 1
            for page in range(2, last_page + 1):
                page_stores = await pending.pop(page)
                if not page_stores:
                    break
                stores.extend(page_stores)
                if next_page <= last_page:
                    pending[next_page] = asyncio.create_task(fetch_page(next_page))
                    next_page += 1
        finally:
            # An error/cancellation must never return a partial success or leave
            # page requests occupying proxies after their caller has finished.
            for task in pending.values():
                task.cancel()
            await asyncio.gather(*pending.values(), return_exceptions=True)
        return stores

    async def get_stores(self, part_number: str, brand: str | None = None) -> dict[str, list[ParsedStore]]:
        return await self._brands(lambda name: self.get_brand_stores(part_number, name), brand)


def is_source_blocked(exc: Exception) -> bool:
    return isinstance(exc, MobisBlockedError)
