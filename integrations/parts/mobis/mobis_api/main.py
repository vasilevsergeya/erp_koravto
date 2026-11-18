from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .client import MobisClient, MobisClientError, MobisUnavailableError
from .config import Settings, load_settings
from .models import BrandStores, PriceResponse, Store, StoresResponse
from .parser import MobisBlockedError


settings = load_settings()
security = HTTPBasic()


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = MobisClient(settings)
    app.state.mobis_client = client
    app.state.pricing_url = os.getenv("ERP_MOBIS_PRICING_URL")
    async with httpx.AsyncClient(timeout=3, trust_env=False) as pricing_client:
        app.state.pricing_client = pricing_client
        try:
            yield
        finally:
            await client.aclose()


app = FastAPI(title="Mobis Python API", version="0.2.0", lifespan=lifespan)


def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    username_ok = secrets.compare_digest(credentials.username, settings.auth_username)
    password_ok = secrets.compare_digest(credentials.password, settings.auth_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def get_client(request: Request) -> MobisClient:
    return request.app.state.mobis_client


def normalize_part_number(
    part_number: str = Path(..., min_length=1, pattern=r"^[A-Za-z0-9-]+$")
) -> str:
    return part_number.strip().upper()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get(
    "/api/v1/parts/{part_number}/price",
    response_model=PriceResponse,
    response_model_by_alias=True,
    dependencies=[Depends(verify_basic_auth)],
)
async def part_price(
    request: Request,
    part_number: str = Depends(normalize_part_number),
    client: MobisClient = Depends(get_client),
    brand: Literal["HYUNDAI", "KIA"] | None = Query(default=None),
) -> PriceResponse:
    try:
        prices = await client.get_prices(part_number, brand)
    except MobisUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "5"}) from exc
    except MobisBlockedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MobisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    first = next((price for price in prices.values() if price), None)
    hyundai = prices["HYUNDAI"].price if prices.get("HYUNDAI") else None
    kia = prices["KIA"].price if prices.get("KIA") else None
    sale_prices = {}
    params = {k: v for k, v in (("hyundai", hyundai), ("kia", kia)) if v is not None}
    if params and request.app.state.pricing_url:
        try:
            response = await request.app.state.pricing_client.get(
                request.app.state.pricing_url, params=params
            )
            response.raise_for_status()
            payload = response.json()
            sale_prices = {key: payload[key] for key in ("sale_usd_HYUNDAI", "sale_usd_KIA")}
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise HTTPException(503, "Sale pricing unavailable", headers={"Retry-After": "5"}) from None
    return PriceResponse(
        part_number=part_number,
        korean_name=first.korean_name if first else None,
        english_name=first.english_name if first else None,
        price_HYUNDAI=hyundai,
        price_KIA=kia,
        **sale_prices,
    )


@app.get(
    "/api/v1/parts/{part_number}/stores",
    response_model=StoresResponse,
    dependencies=[Depends(verify_basic_auth)],
)
async def part_stores(
    part_number: str = Depends(normalize_part_number),
    client: MobisClient = Depends(get_client),
    brand: Literal["HYUNDAI", "KIA"] | None = Query(default=None),
) -> StoresResponse:
    try:
        stores_by_brand = await client.get_stores(part_number, brand)
    except MobisUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "5"}) from exc
    except MobisBlockedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MobisClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return StoresResponse(
        part_number=part_number,
        brands=[
            BrandStores(
                brand=brand,
                stores=[
                    Store(
                        store_type=store.store_type,
                        name=store.name,
                        address=store.address,
                        phone=store.phone,
                    )
                    for store in stores_by_brand[brand]
                ],
            )
            for brand in stores_by_brand
        ],
    )
