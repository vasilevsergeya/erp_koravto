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


def draft(
    parts,
    handler,
    *,
    request="price",
    expression=FORMULA,
    text=None,
    messages=None,
    language="en",
    extra_answer=None,
):
    classified = dict(
        intent="availability_price",
        price_request=request,
        language=language,
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
                "language": language,
                "greeting_name": "",
                **(extra_answer or {}),
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
        patch("app.services.catalog_quotes.asyncio.sleep", new_callable=AsyncMock),
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
        f"{MOBIS} · HYUNDAI — 10.00 USD per piece",
        f"{MOBIS} · KIA — 12.00 USD per piece",
        f"{RENAULT} · Renault — 2.20 USD per piece",
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


def stores(part, hyundai, kia):
    return httpx.Response(
        200,
        json={
            "part_number": part,
            "brands": [
                {"brand": "HYUNDAI", "stores": [{}] * hyundai},
                {"brand": "KIA", "stores": [{}] * kia},
            ],
        },
    )


@pytest.mark.parametrize(
    ("lang", "phrase"), [("en", "We’ll check availability"), ("ru", "Мы уточним наличие")]
)
def test_availability_only_checks_both_catalogs_and_labels_each_item(lang, phrase):
    known, absent = "X123", "X124"

    def handle(req):
        if req.url.host == "renault":
            assert json.loads(req.content)["part_numbers"] == [absent]
            return httpx.Response(
                200, json={"items": [{"part_number": absent, "price": 1200, "stock": 37}]}
            )
        part = req.url.path.split("/")[-2]
        if req.url.path.endswith("/stores"):
            return stores(part, 0, 2 if part == known else 0)
        return mobis(part, "1000", "900") if part == known else mobis(part)

    answer, calls, policy = draft(
        [known, absent],
        handle,
        request="none",
        language=lang,
        text="Here are our supplier listings and unit prices."
        if lang == "en"
        else "Вот цены за штуку и сведения о наличии у поставщиков.",
    )
    rows = answer["text"].splitlines()
    unit = "per piece" if lang == "en" else "за штуку"
    assert any(f"{known} · HYUNDAI — 2.00 USD {unit} · {phrase}" in line for line in rows)
    assert any(
        f"{known} · KIA — 1.80 USD {unit} · " in line and phrase not in line for line in rows
    )
    assert any(
        f"{absent} · Renault — 2.40 USD {unit} · " in line and "37" not in line for line in rows
    )
    assert calls[0]["result"]["availability"] == {"HYUNDAI": 0, "KIA": 2}
    assert policy["passed"], policy


@pytest.mark.parametrize("stock", [0, None])
def test_renault_zero_or_unknown_keeps_price(stock):
    def handle(req):
        if req.url.host == "renault":
            return httpx.Response(
                200, json={"items": [{"part_number": RENAULT, "price": 750, "stock": stock}]}
            )
        return stores(RENAULT, 0, 0) if req.url.path.endswith("/stores") else mobis(RENAULT)

    answer, _, policy = draft([RENAULT], handle, request="price_and_stock")
    assert f"{RENAULT} · Renault — 1.50 USD per piece · We’ll check availability" in answer["text"]
    assert policy["passed"] == (stock == 0)


def test_unavailable_stores_preserve_mobis_price_and_unpriced_sku_is_visible():
    def handle(req):
        if req.url.host == "renault":
            return httpx.Response(
                404, json={"error": {"code": "part_not_found", "part_number": RENAULT}}
            )
        p = req.url.path.split("/")[-2]
        if req.url.path.endswith("/stores"):
            return httpx.Response(503) if p == MOBIS else stores(p, 0, 0)
        return mobis(p, kia="2000") if p == MOBIS else mobis(p)

    answer, _, policy = draft([MOBIS, RENAULT], handle, request="price_and_stock")
    assert f"{MOBIS} · KIA — 4.00 USD per piece · We’ll check availability" in answer["text"]
    assert f"{RENAULT} — We’ll confirm the price. We’ll check availability." in answer["text"]
    assert not policy["passed"]


def test_polite_stock_followup_in_another_customer_language():
    labels = {
        "unit": "par pièce",
        "stock": "En stock",
        "listed": "Disponible chez les fournisseurs",
        "no_price": "Nous confirmerons le prix",
        "check": "Nous vérifierons la disponibilité",
        "intro": "Voici les prix et disponibilités vérifiés.",
        "clarify": "Veuillez préciser toute autre référence à vérifier.",
    }

    def handle(req):
        return stores(MOBIS, 0, 0) if req.url.path.endswith("/stores") else mobis(MOBIS, "4000")

    answer, _, _ = draft(
        [MOBIS],
        handle,
        request="price_and_stock",
        language="fr",
        text="Voici les prix unitaires demandés.",
        extra_answer={"stock_labels": labels},
    )
    assert (
        f"{MOBIS} · HYUNDAI — 8.00 USD par pièce · Nous vérifierons la disponibilité"
        in answer["text"]
    )


def test_manager_reads_exact_followup_next_to_each_sku_without_retranslating_amounts():
    lines = (
        f"{MOBIS} · KIA — 123.45 USD per piece · We’ll check availability\n"
        f"{RENAULT} · Renault — 22.22 USD per piece · In stock\n"
        "AAA001 — We’ll confirm the price. We’ll check availability."
    )
    ru = asyncio.run(translate_text(lines, "en", "ru"))
    assert ru == (
        f"{MOBIS} · KIA — 123.45 USD за штуку · Мы уточним наличие\n"
        f"{RENAULT} · Renault — 22.22 USD за штуку · В наличии\n"
        "AAA001 — Мы уточним цену. Мы уточним наличие."
    )
    assert asyncio.run(translate_text(ru, "ru", "en")) == lines


@pytest.mark.parametrize("recovers", [True, False])
def test_retry_epc_error_without_reporting_the_part_absent(recovers):
    attempts = []

    def handle(req):
        if req.url.host == "mobis":
            return stores(RENAULT, 0, 0) if req.url.path.endswith("/stores") else mobis(RENAULT)
        attempts.append(req)
        if len(attempts) == 6 and recovers:
            return httpx.Response(
                200, json={"items": [{"part_number": RENAULT, "price": 750, "stock": 17}]}
            )
        return httpx.Response(
            502, json={"error": {"code": "upstream_error", "part_number": RENAULT}}
        )

    answer, calls, policy = draft(
        [RENAULT],
        handle,
        request="price_and_stock",
        text=f"{RENAULT} is currently unavailable and not listed.",
    )
    result = calls[0]["result"]
    assert result["attempts"] == len(attempts) == 6
    assert "not listed" not in answer["text"] and "unavailable" not in answer["text"]
    assert policy["passed"] == recovers
    if recovers:
        assert (
            "1.50 USD per piece · In stock" in answer["text"]
            and "(17)" not in answer["text"]
            and "error_code" not in result
        )
    else:
        assert "We’ll check availability" in answer["text"]
        assert result["error_code"] == "upstream_error"


def test_definitive_renault_miss_is_not_retried():
    def handle(req):
        return httpx.Response(
            404, json={"error": {"code": "part_not_found", "part_number": "X.101"}}
        )

    _, calls, _ = draft(["X.101"], handle, request="price_and_stock")
    assert calls[0]["result"]["attempts"] == 1
    assert calls[0]["result"]["status"] == "not_found"


def test_normalize_requested_skus_before_both_lookups_and_retry_only_failed_operations():
    attempts = {"price": 0, "stores": 0}

    def handle(req):
        assert req.url.host == "mobis"
        _, part, action = req.url.path.rsplit("/", 2)
        assert part == MOBIS
        attempts[action] += 1
        if action == "price":
            if attempts[action] < 6:
                return httpx.Response(503)
            return mobis(part, "1000")
        return stores(part, 1, 0)

    answer, calls, policy = draft(
        [MOBIS, "58310-G7A30", "58310 G7A30"],
        handle,
        request="price_and_stock",
        messages=[{"role": "in", "text": "Цена на 58310 - G7A30?"}],
        language="ru",
    )
    assert attempts == {"price": 6, "stores": 1}
    assert len(calls) == 1 and policy["passed"]
    assert f"{MOBIS} · HYUNDAI — 2.00 USD за штуку" in answer["text"]
    assert (
        requested_numbers(
            ["AB1234", "345"],
            [{"role": "in", "text": "AB12"}, {"role": "in", "text": "34 и 12-345"}],
        )
        == []
    )


def test_stores_recovers_from_incomplete_mobis_response():
    attempts = 0

    def handle(req):
        nonlocal attempts
        if req.url.path.endswith("/stores"):
            attempts += 1
            return (
                stores(MOBIS, 0, 1)
                if attempts == 6
                else httpx.Response(200, json={"part_number": MOBIS, "brands": []})
            )
        return mobis(MOBIS, kia="500")

    _, calls, policy = draft([MOBIS], handle, request="price_and_stock")
    assert attempts == 6 and policy["passed"]
    assert calls[0]["result"]["availability"] == {"KIA": 1, "HYUNDAI": 0}
