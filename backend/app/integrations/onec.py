"""Customer phone contract, including optional order details added on 2026-11-16."""

import asyncio
import json
import math
from urllib.parse import urlsplit, urlunsplit

import httpx
import phonenumbers


class OneCError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def customer_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        path = parsed.path.rstrip("/")
        # Accept the publication root or the exact service URL; not the web-client URL.
        if path.endswith("/ru_RU"):
            path = path[:-6]
        if not path.endswith("/hs/koravto/customers"):
            path += "/hs/koravto/customers"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except ValueError:
        raise OneCError("invalid_url") from None


def exact_phone(phone: str) -> str:
    """Canonical international number; never guess a country or accept suffixes."""
    if not isinstance(phone, str) or any(ch not in "+0123456789 ()-." for ch in phone):
        return ""
    try:
        parsed = phonenumbers.parse("+" + phone.lstrip("+"), None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return ""


def phone_digits(phone: str) -> str:
    return exact_phone(phone).lstrip("+")


def phone_country(phone: str) -> str:
    canonical = exact_phone(phone)
    if not canonical:
        return ""
    return phonenumbers.region_code_for_number(phonenumbers.parse(canonical, None)) or ""


class OneCAdapter:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url, self.username, self.password = base_url, username, password

    async def customers(self, phone: str) -> list[dict]:
        if not all((self.base_url, self.username, self.password)):
            raise OneCError("not_configured")
        digits = phone_digits(phone)
        if not digits:
            raise OneCError("invalid_phone")
        url = customer_endpoint(self.base_url)
        try:
            # No redirects/proxy environment: credentials stay on the configured private endpoint.
            async with (
                asyncio.timeout(10),
                httpx.AsyncClient(
                    timeout=httpx.Timeout(8, connect=3),
                    follow_redirects=False,
                    trust_env=False,
                    auth=httpx.BasicAuth(self.username, self.password),
                ) as client,
            ):
                async with client.stream(
                    "GET", url, params={"phone": digits, "match": "exact"}
                ) as response:
                    if response.status_code != 200:
                        code = {
                            401: "authentication",
                            403: "forbidden",
                            404: "not_published",
                            400: "invalid_phone",
                        }.get(response.status_code, "unavailable")
                        raise OneCError(code)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 4 * 1024 * 1024:
                            raise OneCError("response_too_large")
        except (httpx.HTTPError, TimeoutError):
            raise OneCError("unavailable") from None
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or not isinstance(data.get("clients"), list):
                raise ValueError
            clients = []
            for item in data["clients"]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not isinstance(item.get("realizations"), list)
                    or any(not isinstance(n, str) for n in item["realizations"])
                ):
                    raise ValueError
                # Keep same-name clients and duplicate order numbers separate, as 1C does.
                customer = {"name": item["name"], "realizations": item["realizations"]}
                if "matched_phone" in item:
                    if not isinstance(item["matched_phone"], str):
                        raise ValueError
                    matched = exact_phone(item["matched_phone"])
                    if matched:
                        customer["matched_phone"] = matched
                if "country" in item:
                    if not isinstance(item["country"], str):
                        raise ValueError
                    customer["country"] = item["country"]
                if "orders" in item:
                    orders = item["orders"]
                    if not isinstance(orders, list) or len(orders) != len(item["realizations"]):
                        raise ValueError
                    details = []
                    for number, order in zip(item["realizations"], orders, strict=True):
                        if (
                            not isinstance(order, dict)
                            or order.get("number") != number
                            or type(order.get("amount")) not in (int, float)
                            or (
                                isinstance(order["amount"], float)
                                and not math.isfinite(order["amount"])
                            )
                            or not isinstance(order.get("shipping_number"), str)
                            or not isinstance(order.get("project"), str)
                        ):
                            raise ValueError
                        details.append(
                            {
                                key: order[key]
                                for key in ("number", "amount", "shipping_number", "project")
                            }
                        )
                    customer["orders"] = details
                clients.append(customer)
            return clients
        except (ValueError, TypeError):
            raise OneCError("invalid_response") from None
