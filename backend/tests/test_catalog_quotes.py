import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.integrations.parts import PartsClient
from app.services.ai import generate, policy_check
from app.services.ai_config import AIConfig
from app.services.catalog_quotes import catalog_quotes, requested_numbers
from app.services.parts_pricing import SaleFormula
from app.services.translations import translate_text

FORMULA = "0.002 * W + 0 * EXP(-W / 10000)"
MOBIS = "58310G7A30"
RENAULT = "1147112920"


def supplier(handler):
    return PartsClient(
        mobis_auth=("m", "test"), renault_auth=("r", "test"), transport=httpx.MockTransport(handler)
    )


def mobis(part, hyundai=None, kia=None):
    return httpx.Response(
        200, json={"part_number": part, "price_HYUNDAI": hyundai, "price_KIA": kia}
    )


def epc(part, price):
    return httpx.Response(200, json={"items": [{"part_number": part, "price": price}]})


def output(value):
    return [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]


def draft(parts, handler, *, request="price", expression=FORMULA, text=None, messages=None):
    classified = dict(
        intent="availability_price",
        price_request=request,
        language="en",
        tone="neutral",
        escalation=False,
        exact_date=False,
        bot_question=False,
        orders=[],
        parts=parts,
        vins=[],
        cities=[],
        amounts=[],
    )
    writer = AsyncMock()
    writer.response.side_effect = [
        output(classified),
        output(
            {
                "text": text or "Here are the current unit prices for the parts you requested.",
                "language": "en",
                "greeting_name": "",
            }
        ),
    ]
    cfg = AIConfig(coefficient=99, signature="Nina")
    db = MagicMock()
    db.return_value.__enter__.return_value.get.return_value = SimpleNamespace(
        value={"value": expression}
    )
    context = {"messages": messages or [{"role": "in", "text": "Prices for " + ", ".join(parts)}]}
    with (
        patch("app.services.ai.SessionLocal", db),
        patch("app.services.catalog_quotes.assistant_parts_client", lambda: supplier(handler)),
    ):
        classification, answer, calls = asyncio.run(generate(writer, cfg, context, "", []))
    policy = policy_check(cfg, classification, answer, calls)
    return answer, calls, policy


def test_price_draft_uses_saved_formula_for_mobis_and_renault():
    seen = []

    def handle(req):
        seen.append((req.url.host, req.url.path))
        if req.url.host == "renault":
            assert json.loads(req.content) == {"part_numbers": [RENAULT]}
            return epc(RENAULT, 1100.5)
        if MOBIS in req.url.path:
            return mobis(MOBIS, "5000", "6000")
        return mobis(RENAULT)

    answer, calls, policy = draft([MOBIS, RENAULT, MOBIS], handle)
    assert policy["passed"], policy["reasons"]
    assert len(seen) == 3 and len(calls) == 2
    assert answer["text"].splitlines()[1:] == [
        f"{MOBIS} · HYUNDAI — 10.00 USD",
        f"{MOBIS} · KIA — 12.00 USD",
        f"{RENAULT} · Renault — 2.20 USD",
        "Nina",
    ]
    changed, _, _ = draft([RENAULT], handle, expression="0.01*W+0*EXP(-W/1)")
    assert f"{RENAULT} · Renault — 11.01 USD" in changed["text"]


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (
            httpx.Response(404, json={"error": {"code": "part_not_found", "part_number": RENAULT}}),
            "not_found",
        ),
        (httpx.Response(404, json={"detail": "route not found"}), "unavailable"),
        (epc(RENAULT, 0), "no_price"),
        (epc("wrong", 100), "unavailable"),
        (httpx.Response(503), "unavailable"),
    ],
)
def test_renault_errors_never_become_prices(response, status):
    def handle(req):
        return mobis(RENAULT, "0") if req.url.host == "mobis" else response

    answer, calls, policy = draft(
        [RENAULT], handle, text="Our manager will check the price for you."
    )
    assert calls[0]["result"]["status"] == status
    assert "USD" not in answer["text"]
    assert not policy["passed"]


def test_failed_mobis_does_not_trigger_renault_or_discard_other_part():
    def handle(req):
        assert req.url.host == "mobis"
        return mobis(MOBIS, kia="13000") if MOBIS in req.url.path else httpx.Response(502)

    answer, calls, policy = draft([RENAULT, MOBIS], handle, text="This costs $9999.")
    assert [c["result"]["status"] for c in calls] == ["unavailable", "ok"]
    assert "9999" not in answer["text"]
    assert "26.00 USD" in answer["text"]
    assert not policy["passed"]


def test_stock_only_missing_number_and_followup():
    def unexpected(_):
        pytest.fail("Unexpected supplier lookup")

    _, calls, _ = draft([MOBIS], unexpected, request="none")
    assert not calls
    _, calls, policy = draft(["made-up"], unexpected, messages=[{"role": "in", "text": "Price?"}])
    assert not calls and not policy["passed"]
    messages = [{"role": "in", "text": f"Can you check {MOBIS}?"}, {"role": "in", "text": "Price?"}]
    answer, calls, _ = draft([MOBIS], lambda _: mobis(MOBIS, "3000"), messages=messages)
    assert calls and "6.00 USD" in answer["text"]
    _, _, policy = draft([MOBIS], lambda _: mobis(MOBIS, "3000"), request="price_and_stock")
    assert not policy["passed"]


def test_dotted_epc_number_and_grounding():
    part = "AB.234"
    assert requested_numbers(
        ["AB", part, "234", "2345"],
        [{"role": "in", "text": "Price AB.234?"}, {"role": "out", "text": "2345"}],
    ) == [part]

    def handle(req):
        assert req.url.host == "renault"
        return epc(part, 5000)

    answer, _, _ = draft([part], handle)
    assert f"{part} · Renault — 10.00 USD" in answer["text"]


def test_slow_supplier_keeps_completed_results():
    async def handle(req):
        if MOBIS in req.url.path:
            return mobis(MOBIS, "2000")
        await asyncio.sleep(10)
        raise AssertionError("Expected cancellation")

    with (
        patch("app.services.catalog_quotes.assistant_parts_client", lambda: supplier(handle)),
        patch("app.services.catalog_quotes.LOOKUP_SECONDS", 0.02),
    ):
        calls = asyncio.run(catalog_quotes([MOBIS, RENAULT], SaleFormula.parse(FORMULA)))
    assert [c["result"]["status"] for c in calls] == ["ok", "unavailable"]


def test_catalog_rows_survive_translation():
    row = f"{RENAULT} · Renault — 10.00 USD"
    assert asyncio.run(translate_text(row, "en", "ru")) == row

    async def translate(_, body, purpose):
        value = body["input"][0]["content"]
        assert row not in value
        return {
            "status": "completed",
            "output": output({"text": value.replace("Unit price", "Цена")}),
        }

    with (
        patch("app.services.translations.SessionLocal"),
        patch("app.services.translations.settings", return_value=AIConfig()),
        patch("app.services.translations.get_setting", return_value="test"),
        patch("app.services.translations.month_spend", return_value=0),
        patch("app.services.translations.OpenAIAdapter.request", side_effect=translate),
    ):
        assert asyncio.run(translate_text("Unit price\n" + row, "en", "ru")) == "Цена\n" + row
