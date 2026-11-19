from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.settings import pricing_expression
from app.services.parts_pricing import SaleFormula

# Only /api/* and /health reach the CRM API through Caddy.
router = APIRouter(prefix="/internal/parts-pricing", include_in_schema=False)
Won = Annotated[int | None, Query(ge=0, le=10**18)]


@router.get("/mobis")
def mobis_sale_prices(
    hyundai: Won = None, kia: Won = None, db: Session = Depends(get_db)
) -> dict[str, str | None]:
    formula = SaleFormula.parse(pricing_expression(db))
    return {"sale_usd_HYUNDAI": formula.sale_usd(hyundai), "sale_usd_KIA": formula.sale_usd(kia)}
