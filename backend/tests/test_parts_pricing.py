import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("PUBLIC_URL", "https://crm-test.invalid")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SECRET_KEY", "01" * 32)
os.environ.setdefault("MASTER_ENCRYPTION_KEY", "02" * 32)

from app.api.parts_pricing import mobis_sale_prices
from app.services.parts_pricing import DEFAULT_FORMULA, SaleFormula


def test_sale_prices_match_spreadsheet():
    won = [100, *range(2000, 22001, 1000)]
    expected = (
        "6.21 7.50 8.25 9.04 9.88 10.76 11.66 12.60 13.57 14.56 15.58 "
        "16.61 17.67 18.74 19.82 20.92 22.03 23.16 24.29 25.43 26.58 27.74"
    ).split()
    formula = SaleFormula.parse(DEFAULT_FORMULA)
    assert [formula.sale_usd(w) for w in won] == expected


def test_next_request_uses_the_saved_formula_for_both_brands():
    row = SimpleNamespace(value={"value": DEFAULT_FORMULA})
    db = SimpleNamespace(get=lambda *_: row)
    assert mobis_sale_prices(2000, None, db) == {"sale_usd_HYUNDAI": "7.50", "sale_usd_KIA": None}
    row.value["value"] = "0,0025*W+0*exp(-W/1000)"
    assert mobis_sale_prices(100, 2, db) == {"sale_usd_HYUNDAI": "0.25", "sale_usd_KIA": "0.01"}
    assert mobis_sale_prices(0, None, db) == {"sale_usd_HYUNDAI": None, "sale_usd_KIA": None}


@pytest.mark.parametrize("bad", ["W + 1", "0*W+6*EXP(-W/100)", "1*W+6*EXP(-W/0)", "NaN"])
def test_invalid_formula_is_rejected(bad):
    with pytest.raises(ValueError):
        SaleFormula.parse(bad)
