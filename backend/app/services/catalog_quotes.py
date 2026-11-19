"""Supplier-backed unit prices for customer replies."""

import asyncio
import re
from configparser import BasicInterpolation, ConfigParser
from configparser import Error as ConfigError
from decimal import Decimal, InvalidOperation

import httpx

from app.integrations.parts import PartsClient
from app.services.parts_pricing import SaleFormula


def assistant_parts_client() -> PartsClient:
    def auth(supplier: str, section: str) -> tuple[str, str]:
        parser = ConfigParser(interpolation=BasicInterpolation() if supplier == "mobis" else None)
        with open(f"/run/config/{supplier}.ini", encoding="utf-8-sig") as stream:
            parser.read_file(stream)
        return parser.get(section, "username"), parser.get(section, "password")

    return PartsClient(mobis_auth=auth("mobis", "auth"), renault_auth=auth("renault", "api"))


def requested_numbers(parts: list[str], messages: list[dict]) -> list[str]:
    # A classifier can resolve "and the price?" against earlier customer messages,
    # but cannot introduce a SKU from an example, an order record or its own output.
    inbound = "\n".join(m["text"] for m in messages if m["role"] == "in").upper()
    result = []
    for part in dict.fromkeys(p.strip().upper() for p in parts):
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,39}", part):
            continue
        if re.search(
            r"(?<![A-Z0-9-])(?<![A-Z0-9]\.)" + re.escape(part) + r"(?![A-Z0-9-]|\.[A-Z0-9])",
            inbound,
        ):
            result.append(part)
    return result


def amount_usd(formula: SaleFormula, raw: str | float | int | None) -> str | None:
    if raw is None:
        return None
    won = Decimal(str(raw))
    if not won.is_finite() or abs(won) > 10**18:
        raise ValueError("Invalid supplier price")
    return formula.sale_usd(won)


async def lookup_part(client: PartsClient, part: str, formula: SaleFormula) -> dict:
    # Mobis accepts letters, digits and hyphens. Dotted EPC numbers go directly to Renault.
    if "." not in part:
        mobis = await client.mobis_price(part)
        if mobis["part_number"] != part:
            raise ValueError("Unexpected Mobis part number")
        offers = {}
        for brand in ("HYUNDAI", "KIA"):
            usd = amount_usd(formula, mobis[f"price_{brand}"])
            if usd is not None:
                offers[brand] = usd
        if offers:
            return {"status": "ok", "source": "Mobis", "unit_usd": offers}

    try:
        result = await client.renault_parts([part])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        error = exc.response.json()["error"]
        if error["code"] != "part_not_found" or error.get("part_number") != part:
            raise
        return {"status": "not_found", "source": "Renault"}

    (item,) = result["items"]
    if item["part_number"] != part:
        raise ValueError("Unexpected Renault part number")
    price = amount_usd(formula, item["price"])
    if price is None:
        return {"status": "no_price", "source": "Renault"}
    return {"status": "ok", "source": "Renault", "unit_usd": {"Renault": price}}


# Keep time for both the initial Mobis request and the fallback. Return completed
# positions if a supplier stalls on another part in the same customer message.
LOOKUP_SECONDS = 100


async def catalog_quotes(parts: list[str], formula: SaleFormula) -> list[dict]:
    results = {part: {"status": "unavailable"} for part in parts}
    limit = asyncio.Semaphore(3)

    async def resolve(client: PartsClient, part: str):
        async with limit:
            try:
                results[part] = await lookup_part(client, part, formula)
            except (httpx.HTTPError, KeyError, TypeError, ValueError, InvalidOperation):
                pass  # An unavailable supplier is distinct from a confirmed miss.

    try:
        async with assistant_parts_client() as client, asyncio.timeout(LOOKUP_SECONDS):
            await asyncio.gather(*(resolve(client, part) for part in parts))
    except (OSError, ConfigError, TimeoutError):
        pass
    return [
        {"name": "catalog_price", "arguments": {"part_number": part}, "result": result}
        for part, result in results.items()
    ]


def quote_lines(calls: list[dict]) -> str:
    return "\n".join(
        f"{call['arguments']['part_number']} · {brand} — {usd} USD"
        for call in calls
        if call["name"] == "catalog_price"
        for brand, usd in call["result"].get("unit_usd", {}).items()
    )
