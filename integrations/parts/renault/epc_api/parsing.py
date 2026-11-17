"""Strict conversion of catalog tables into the response from the specification.

Missing cells must be explicit blanks/null; a missing column is a format error.
Ext.NET data is decoded as data only, without eval or a JavaScript runtime.
"""

import html
import math
import re
from datetime import datetime

import json5
from bs4 import BeautifulSoup

from epc_api.errors import ParseError
from epc_api.models import Compatibility, Part


def clean(value) -> str:
    if value is None:
        return ""
    return " ".join(html.unescape(str(value)).split())


def number(value, *, integer=False) -> int | float | None:
    value = clean(value)
    if value in {"", "-", "—", "N/A"}:
        return None
    # EPC uses comma thousands separators and a dot decimal separator (e.g. .4).
    if not re.fullmatch(r"(?=.*\d)\+?(?:\d{1,3}(?:,\d{3})+|\d+|)(?:\.\d+)?", value):
        raise ParseError("Некорректное числовое значение в EPC.")
    result = float(value.replace(",", ""))
    if not math.isfinite(result) or result < 0 or (integer and not result.is_integer()):
        raise ParseError("Некорректное числовое значение в EPC.")
    return int(result) if integer else result


def adopted_date(value):
    value = clean(value)
    if value in {"", "-", "—"}:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ParseError("Некорректная дата Adopted в EPC.")


def json_path(value, path: str):
    """Dot paths, e.g. result.data or result.tables.0.rows. No expressions."""
    try:
        for key in path.split(".") if path else []:
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        raise ParseError("В ответе EPC отсутствует поле из профиля каталога.") from None


def table_rows(source: str, selector: str) -> list[dict]:
    table = BeautifulSoup(source, "html.parser").select_one(selector)
    if table is None:
        raise ParseError("В HTML EPC отсутствует таблица из профиля каталога.")
    rows = table.select("tr")
    if not rows:
        raise ParseError("В таблице EPC отсутствует строка заголовков.")
    headers = [clean(cell.get_text(" ", strip=True)) for cell in rows[0].select("th,td")]
    if not headers or len(set(headers)) != len(headers):
        raise ParseError("Неоднозначные заголовки таблицы EPC.")
    result = []
    for row in rows[1:]:
        cells = row.select("td")
        if not cells:
            continue
        if len(cells) != len(headers):
            raise ParseError("Число ячеек не совпадает с заголовками таблицы EPC.")
        result.append(
            dict(zip(headers, (clean(c.get_text(" ", strip=True)) for c in cells), strict=True))
        )
    return result


def balanced_array(script: str, start: int) -> str:
    depth, quote, escaped = 0, None, False
    for i in range(start, len(script)):
        char = script[i]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return script[start : i + 1]
    raise ParseError("Незавершённый массив данных Ext.NET.")


def ext_store_rows(source: str, store: str) -> list[dict]:
    # DirectEvent responses use a JS object literal {script:"App.Store.loadData([...]);"}.
    try:
        envelope = json5.loads(source)
    except ValueError:
        envelope = None
    script = envelope.get("script", "") if isinstance(envelope, dict) else source
    if not isinstance(script, str):
        raise ParseError("В ответе Ext.NET отсутствует текстовый script.")
    pattern = re.escape(store) + r"(?:\.(?:loadData|setData)\(|\.proxy\.data\s*=)\s*(\[)"
    matches = list(re.finditer(pattern, script))
    if not matches:
        raise ParseError("В ответе EPC отсутствует ожидаемый Ext.NET store.")
    try:
        return json5.loads(balanced_array(script, matches[-1].start(1)))
    except ValueError:
        raise ParseError("Невозможно прочитать массив Ext.NET как данные.") from None


def extract_rows(source: str, kind: str, selector: str) -> list[dict | list]:
    if kind == "html":
        value = table_rows(source, selector)
    elif kind == "ext_store":
        value = ext_store_rows(source, selector)
    else:
        try:
            value = json_path(json5.loads(source), selector)
        except ValueError:
            raise ParseError("Ответ EPC не является JSON.") from None
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(row, (dict, list)) for row in value):
        raise ParseError("Ожидался массив записей EPC.")
    return value


def build_part(
    part_number: str, general: list[dict], history: list[dict], stock: list[dict], cars: list[dict]
) -> Part:
    """Input rows have canonical keys, mapped explicitly by the catalog profile."""
    try:
        matching = [row for row in general if clean(row["part_number"]) == part_number]
        if len(matching) != 1:
            raise ParseError("Карточка EPC содержит другой номер детали или неоднозначные данные.")
        row = matching[0]
        name = clean(row["english_name"])
        if not name:
            raise ParseError("В карточке EPC отсутствует английское название детали.")
        compatibility = []
        for item in history:
            old_number = clean(item["part_number"])
            if not old_number:
                raise ParseError("В Part History отсутствует номер детали.")
            if old_number != part_number:
                compatibility.append(
                    Compatibility(part_number=old_number, adopted=adopted_date(item["adopted"]))
                )
        quantities = [number(item["stock"], integer=True) for item in stock]
        # Unknown availability at any center must not be presented as a known total.
        total = sum(quantities) if quantities and all(q is not None for q in quantities) else None
        models = [clean(item["model"]) for item in cars]
        if any(not model for model in models):
            raise ParseError("В Applied Catalog Section отсутствует название модели.")
        return Part(
            part_number=part_number,
            english_name=name,
            weight=number(row["weight"]),
            volume=number(row["volume"]),
            compatibility=compatibility,
            price=number(row["price"]),
            stock=total,
            cars=models,
        )
    except (KeyError, TypeError):
        raise ParseError("В таблице EPC отсутствует обязательный столбец.") from None
