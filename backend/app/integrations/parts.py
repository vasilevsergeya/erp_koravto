"""Async client for ERP's Mobis and Renault services (no credentials in code)."""
import re
from typing import Literal
from urllib.parse import quote

import httpx


def normalize_part_number(value: str) -> str:
    return re.sub(r"[\s\-‐‑‒–—−]+", "", value).upper()


class PartsClient:
    """Use as an async context manager; errors remain errors, never zero stock.

    Internal URLs: http://mobis:8000/api/v1 and http://renault:8000.
    External URLs: https://erp.korean.parts/api/mobis/v1 and
    https://erp.korean.parts/api/renault. Credentials are supplied by the caller.
    """

    def __init__(
        self,
        *,
        mobis_auth: tuple[str, str],
        renault_auth: tuple[str, str],
        mobis_url: str = "http://mobis:8000/api/v1",
        renault_url: str = "http://renault:8000",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.mobis = httpx.AsyncClient(
            base_url=mobis_url.rstrip("/") + "/", auth=mobis_auth,
            timeout=httpx.Timeout(180, connect=10), transport=transport,
        )
        self.renault = httpx.AsyncClient(
            base_url=renault_url.rstrip("/") + "/", auth=renault_auth,
            timeout=httpx.Timeout(300, connect=10), transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.mobis.aclose()
        await self.renault.aclose()

    async def mobis_price(self, part_number: str, brand: Literal["HYUNDAI", "KIA"] | None = None) -> dict:
        return await self._mobis_part(part_number, "price", brand)

    async def mobis_stores(self, part_number: str, brand: Literal["HYUNDAI", "KIA"] | None = None) -> dict:
        return await self._mobis_part(part_number, "stores", brand)

    async def _mobis_part(self, part_number: str, kind: str, brand: str | None) -> dict:
        response = await self.mobis.get(
            f"parts/{quote(normalize_part_number(part_number), safe='')}/{kind}",
            params={"brand": brand} if brand else {},
        )
        response.raise_for_status()
        return response.json()

    async def renault_parts(self, part_numbers: list[str]) -> dict:
        response = await self.renault.post("parts", json={"part_numbers": [normalize_part_number(part) for part in part_numbers]})
        response.raise_for_status()
        return response.json()
