import asyncio

import httpx
import pytest

from app.integrations.parts import PartsClient


def test_supplier_methods_preserve_contracts():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json={"items": []})

    async def run():
        async with PartsClient(mobis_auth=("m", "pw"), renault_auth=("r", "pw"),
                               transport=httpx.MockTransport(handle)) as client:
            await client.mobis_price("92101T4200", "KIA")
            await client.mobis_stores("58310G7A30")
            await client.renault_parts(["1147112920"])
    asyncio.run(run())
    assert str(seen[0].url) == "http://mobis:8000/api/v1/parts/92101T4200/price?brand=KIA"
    assert str(seen[1].url) == "http://mobis:8000/api/v1/parts/58310G7A30/stores"
    assert str(seen[2].url) == "http://renault:8000/parts"
    assert seen[2].method == "POST"
    assert seen[2].content == b'{"part_numbers":["1147112920"]}'


def test_supplier_failure_is_not_reported_as_empty_stock():
    async def run():
        async with PartsClient(mobis_auth=("m", "pw"), renault_auth=("r", "pw"),
                               transport=httpx.MockTransport(
                                   lambda _: httpx.Response(503, json={"detail": "unavailable"})
                               )) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.mobis_stores("58310G7A30")
    asyncio.run(run())
