"""HTTP workflow verified against the authenticated Renault Korea EPC catalog."""

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from epc_api.config import ConfigurationError
from epc_api.errors import CatalogNotConfigured, ParseError, PartNotFound
from epc_api.http import EPCSession
from epc_api.parsing import build_part, clean, extract_rows, json_path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestSpec(StrictModel):
    url: str = Field(min_length=1)
    method: Literal["GET", "POST"] = "GET"
    params: dict[str, str] = Field(default_factory=dict)
    # For Ext.NET: first GET the page to obtain fresh viewstate, then POST a DirectMethod.
    direct_method: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)


class RowsSpec(StrictModel):
    kind: Literal["json", "html", "ext_store"]
    selector: str
    columns: dict[str, str] = Field(default_factory=dict)

    def read(self, source: str) -> list[dict]:
        rows = extract_rows(source, self.kind, self.selector)
        if not self.columns:
            return rows
        return [{key: json_path(row, path) for key, path in self.columns.items()} for row in rows]


class SourceSpec(RowsSpec):
    response: str


class DetailStep(RequestSpec):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    capture: RowsSpec | None = None


class ResultSpec(StrictModel):
    general: SourceSpec
    history: SourceSpec
    stock: SourceSpec
    cars: SourceSpec


class CatalogProfile(StrictModel):
    search: RequestSpec
    search_rows: RowsSpec
    details: list[DetailStep] = Field(min_length=1, max_length=10)
    result: ResultSpec


def load_profile(path: Path | None) -> CatalogProfile | None:
    if path is None:
        path = Path(__file__).parent / "profiles" / "renault.json"
    try:
        profile = CatalogProfile.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))
        names = ["search", *(step.name for step in profile.details)]
        if len(names) != len(set(names)) or any(
            source.response not in names
            for source in (
                profile.result.general,
                profile.result.history,
                profile.result.stock,
                profile.result.cars,
            )
        ):
            raise ValueError("Invalid response references")
        for spec in (profile.search, *profile.details):
            if spec.direct_method and spec.method != "POST":
                raise ValueError("DirectMethod requires POST")
        return profile
    except (OSError, ValueError, ValidationError):
        raise ConfigurationError(f"Некорректный профиль каталога: {path.name}.") from None


def render(template: str, context: dict, *, url=False) -> str:
    def replace(match):
        value = json_path(context, match.group(1))
        if not isinstance(value, (str, int, float)):
            raise ParseError("Некорректное значение параметра запроса EPC.")
        return quote(str(value), safe="") if url else str(value)

    return re.sub(r"\{([A-Za-z0-9_.-]+)\}", replace, template)


class Catalog:
    def __init__(self, session: EPCSession, profile: CatalogProfile | None):
        self.session = session
        self.profile = profile

    def fetch(self, spec: RequestSpec, context: dict) -> str:
        url = render(spec.url, context, url=True)
        params = {key: render(value, context) for key, value in spec.params.items()}
        fields = {key: render(value, context) for key, value in spec.fields.items()}
        if spec.direct_method:
            page = self.session.protected_request("GET", url)
            response = self.session.direct(page, spec.direct_method, params, fields)
            self.session.check_protected_response(response)
        else:
            kwargs = {"params" if spec.method == "GET" else "data": params | fields}
            response = self.session.protected_request(spec.method, url, **kwargs)
        return response.text

    def lookup(self, part_number: str):
        profile = self.profile
        if profile is None:
            raise CatalogNotConfigured()
        context = {"part_number": part_number}
        responses = {"search": self.fetch(profile.search, context)}
        rows = profile.search_rows.read(responses["search"])
        if any(
            not isinstance(row, dict) or not ("PARTNO" in row or "part_number" in row)
            for row in rows
        ):
            raise ParseError("В выдаче EPC отсутствует номер детали.")
        rows = [
            row for row in rows if clean(row.get("part_number", row.get("PARTNO"))) == part_number
        ]
        if not rows:
            raise PartNotFound()
        # The specification allows any result row; preserve the first row's identifiers.
        context["row"] = rows[0]
        for step in profile.details:
            responses[step.name] = self.fetch(step, context)
            if step.capture:
                candidates = step.capture.read(responses[step.name])
                matches = [
                    row
                    for row in candidates
                    if isinstance(row, dict)
                    and clean(row.get("part_number", row.get("PARTNO"))) == part_number
                ]
                if len(matches) != 1:
                    raise ParseError("В карточке EPC не найдена однозначная запись детали.")
                context[step.name] = matches[0]
        data = {
            name: source.read(responses[source.response])
            for name, source in (
                ("general", profile.result.general),
                ("history", profile.result.history),
                ("stock", profile.result.stock),
                ("cars", profile.result.cars),
            )
        }
        return build_part(part_number, **data)
