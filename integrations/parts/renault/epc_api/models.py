import re
from datetime import date
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

def normalize_part_number(value):
    return re.sub(r"[\s\-‐‑‒–—−]+", "", value).upper() if isinstance(value, str) else value


PartNumber = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$",
    ),
    BeforeValidator(normalize_part_number),
]


class PartsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_numbers: list[PartNumber] = Field(min_length=1, max_length=100)


class Compatibility(BaseModel):
    part_number: str
    adopted: date | None


class Part(BaseModel):
    part_number: str
    english_name: str
    weight: float | None = Field(description="Масса в граммах, как в EPC.")
    volume: float | None = Field(description="Объём в см³, как в EPC.")
    compatibility: list[Compatibility]
    price: float | None = Field(description="Цена в KRW с НДС, как в EPC.")
    stock: int | None
    cars: list[str]


class PartsResponse(BaseModel):
    items: list[Part]


class ErrorDetail(BaseModel):
    code: str
    message: str
    part_number: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
