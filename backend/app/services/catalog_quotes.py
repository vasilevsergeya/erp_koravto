"""Supplier-backed unit prices for customer replies."""

import asyncio
import re
from configparser import BasicInterpolation, ConfigParser
from configparser import Error as ConfigError
from decimal import Decimal, InvalidOperation

import httpx

from app.integrations.parts import PartsClient, normalize_part_number
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
    inbound = [m["text"].upper() for m in messages if m["role"] == "in"]
    result = []
    for part in dict.fromkeys(normalize_part_number(p) for p in parts):
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.]{0,39}", part):
            continue
        # Accept separators inside one SKU, never across messages or separate lines.
        spelling = r"(?:[^\S\r\n]|[\-‐‑‒–—−])*".join(map(re.escape, part))
        pattern = re.compile(
            r"(?<![A-Z0-9\-‐‑‒–—−])(?<![A-Z0-9]\.)" + spelling + r"(?![A-Z0-9\-‐‑‒–—−]|\.[A-Z0-9])"
        )
        if any(pattern.search(message) for message in inbound):
            result.append(part)
    return result


def amount_usd(formula: SaleFormula, raw: str | float | int | None) -> str | None:
    if raw is None:
        return None
    won = Decimal(str(raw))
    if not won.is_finite() or abs(won) > 10**18:
        raise ValueError("Invalid supplier price")
    return formula.sale_usd(won)


SUPPLIER_ATTEMPTS = 6
REQUEST_SECONDS = 50
# Mobis and Renault can run consecutively for one part. Leave room for retries
# of both catalogs, including their pauses. The worker lease uses the same budget.
LOOKUP_SECONDS = 2 * SUPPLIER_ATTEMPTS * (REQUEST_SECONDS + 5) + 5


async def supplier_lookup(read, part, diagnostics):
    for attempt in range(1, SUPPLIER_ATTEMPTS + 1):
        diagnostics["attempts"] = attempt
        pause = min(0.25 * 2 ** (attempt - 1), 4)
        try:
            async with asyncio.timeout(REQUEST_SECONDS):
                value = await read()
        except httpx.HTTPStatusError as exc:
            response = exc.response
            try:
                body = response.json()
            except ValueError:
                body = None
            error = body.get("error") if isinstance(body, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            diagnostics["error_code"] = (
                code if isinstance(code, str) else f"http_{response.status_code}"
            )
            if (
                response.status_code == 404
                and isinstance(error, dict)
                and code == "part_not_found"
                and error.get("part_number") == part
            ):
                diagnostics["status"] = "not_found"
                return None
            if response.status_code < 500 and response.status_code not in (408, 425, 429):
                return None
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isascii() and retry_after.isdecimal() and len(retry_after) <= 5:
                pause = min(5, max(pause, int(retry_after)))
        except (httpx.TransportError, TimeoutError):
            diagnostics["error_code"] = "transport_error"
        except (KeyError, TypeError, ValueError, InvalidOperation):
            diagnostics["error_code"] = "invalid_response"
        else:
            diagnostics.pop("error_code", None)
            return value
        if attempt < SUPPLIER_ATTEMPTS:
            await asyncio.sleep(pause)
    return None


async def lookup_part(
    client: PartsClient, part: str, formula: SaleFormula, stock: bool, entry: dict
):
    async def mobis_prices():
        payload = await client.mobis_price(part)
        if payload["part_number"] != part:
            raise ValueError("Unexpected Mobis part number")
        offers = {}
        for brand in ("HYUNDAI", "KIA"):
            price = amount_usd(formula, payload[f"price_{brand}"])
            if price is not None:
                offers[brand] = price
        return offers

    async def mobis_stores():
        payload = await client.mobis_stores(part)
        if payload["part_number"] != part:
            raise ValueError("Unexpected Mobis part number")
        counts = {}
        for item in payload["brands"]:
            brand = item["brand"]
            if brand in counts or not isinstance(item["stores"], list):
                raise ValueError("Invalid Mobis stores")
            counts[brand] = len(item["stores"])
        if counts.keys() != {"KIA", "HYUNDAI"}:
            raise ValueError("Incomplete Mobis brands")
        return counts

    async def load_prices():
        offers = await supplier_lookup(mobis_prices, part, entry.setdefault("price_lookup", {}))
        if offers is not None:
            entry.update(status="ok" if offers else "no_price", unit_usd=offers)

    async def load_stores():
        counts = await supplier_lookup(mobis_stores, part, entry.setdefault("stores_lookup", {}))
        if counts is not None:
            entry["availability"] = counts

    if "." not in part:
        entry["source"] = "Mobis"
        if stock:
            await asyncio.gather(load_prices(), load_stores())
        else:
            await load_prices()
        if entry["status"] != "no_price" or any(entry.get("availability", {}).values()):
            return

    async def renault_item():
        result = await client.renault_parts([part])
        (item,) = result["items"]
        if item["part_number"] != part:
            raise ValueError("Unexpected Renault part number")
        details: dict = {}
        if stock:
            quantity = item["stock"]
            if quantity is not None:
                if type(quantity) is not int or quantity < 0:
                    raise ValueError("Invalid Renault stock")
                # Only the availability indicator enters the draft and its source panel.
                details["availability"] = {"Renault": bool(quantity)}
        price = amount_usd(formula, item["price"])
        if price is None:
            details["status"] = "no_price"
        else:
            details.update(status="ok", unit_usd={"Renault": price})
        return details

    entry.update(status="unavailable", source="Renault")
    entry.pop("availability", None)
    entry.pop("unit_usd", None)
    details = await supplier_lookup(renault_item, part, entry)
    if details is not None:
        entry.update(details)


async def catalog_quotes(
    parts: list[str], formula: SaleFormula, *, stock: bool = False
) -> list[dict]:
    results: dict[str, dict] = {
        normalize_part_number(part): {"status": "unavailable"} for part in parts
    }
    limit = asyncio.Semaphore(3)

    async def resolve(client: PartsClient, part: str):
        async with limit:
            await lookup_part(client, part, formula, stock, results[part])

    try:
        async with assistant_parts_client() as client, asyncio.timeout(LOOKUP_SECONDS):
            await asyncio.gather(*(resolve(client, part) for part in results))
    except (OSError, ConfigError, TimeoutError):
        pass
    return [
        {"name": "catalog_price", "arguments": {"part_number": part}, "result": result}
        for part, result in results.items()
    ]


# Common reply languages have fixed wording. Other languages use the same
# short labels from the structured response, with no model-supplied prices or counts.
STOCK_LABELS = {
    "en": {
        "unit": "per piece",
        "listed": "Listed by suppliers",
        "stock": "In stock",
        "check": "We’ll check availability",
        "no_price": "We’ll confirm the price",
        "intro": "Here are the unit prices and availability we could verify.",
        "clarify": "Please confirm the part number for any other item you would like us to check.",
    },
    "ru": {
        "unit": "за штуку",
        "listed": "Есть у поставщиков",
        "stock": "В наличии",
        "check": "Мы уточним наличие",
        "no_price": "Мы уточним цену",
        "intro": "Вот что удалось проверить по ценам и наличию запрошенных деталей.",
        "clarify": "Уточните, пожалуйста, артикул каждой дополнительной детали для проверки.",
    },
}


def quote_lines(calls: list[dict], labels: dict[str, str], *, stock: bool) -> str:
    lines = []
    for call in calls:
        if call["name"] != "catalog_price":
            continue
        part = call["arguments"]["part_number"]
        result = call["result"]
        offers = result.get("unit_usd", {})
        counts = result.get("availability", {})
        brands = list(offers)
        if stock:
            brands.extend(b for b, n in counts.items() if n > 0 and b not in offers)
            if not brands:
                lines.append(f"{part} — {labels['no_price']}. {labels['check']}.")
                continue
        for brand in brands:
            price = f"{offers[brand]} USD {labels['unit']}" if brand in offers else ""
            if stock:
                quantity = counts.get(brand, 0)
                availability = labels["check"]
                if quantity > 0:
                    availability = labels["stock"] if brand == "Renault" else labels["listed"]
                price = f"{price or labels['no_price']} · {availability}"
            lines.append(f"{part} · {brand} — {price}")
    return "\n".join(lines)
