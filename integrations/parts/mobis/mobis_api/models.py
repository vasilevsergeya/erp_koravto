from __future__ import annotations

from pydantic import BaseModel, Field


class PriceResponse(BaseModel):
    part_number: str
    korean_name: str | None = None
    english_name: str | None = None
    price_HYUNDAI: str | None = Field(default=None, alias="price_HYUNDAI")
    price_KIA: str | None = None
    sale_usd_HYUNDAI: str | None = None
    sale_usd_KIA: str | None = None

    model_config = {"populate_by_name": True}


class Store(BaseModel):
    store_type: str | None = None
    name: str | None = None
    address: str | None = None
    phone: str | None = None


class BrandStores(BaseModel):
    brand: str
    stores: list[Store]


class StoresResponse(BaseModel):
    part_number: str
    brands: list[BrandStores]
