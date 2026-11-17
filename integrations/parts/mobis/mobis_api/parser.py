from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup, Tag


class MobisSourceError(RuntimeError):
    """The response is not authoritative part data."""


class MobisBlockedError(MobisSourceError):
    pass


class MobisMaintenanceError(MobisSourceError):
    pass


class MobisRateLimitedError(MobisSourceError):
    pass


class MobisSessionExpiredError(MobisSourceError):
    pass


class MobisInvalidResponseError(MobisSourceError):
    pass


@dataclass(frozen=True)
class ParsedPrice:
    part_number: str
    korean_name: str | None
    english_name: str | None
    price: str | None


@dataclass(frozen=True)
class ParsedStore:
    store_type: str | None
    name: str | None
    address: str | None
    phone: str | None


_WHITESPACE_RE = re.compile(r"\s+")
_PART_RE = re.compile(r"^(?=.*\d)[A-Z0-9-]{5,}$", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?(?:\d{2,4}[-.\s]\d{3,4}[-.\s]\d{4,6})")
_PAGE_RE = re.compile(r"PageLoad\((\d+)\)")


def clean_text(value: str | None) -> str:
    return _WHITESPACE_RE.sub(" ", value or "").strip()


def _cell_text(cell: Tag) -> str:
    for element in cell.select("script, style"):
        element.decompose()
    return clean_text(cell.get_text(" ", strip=True))


def _soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    active_flags = {
        str(key).lower()
        for field in soup.find_all("input")
        if str(field.get("value") or "").strip().upper() == "Y"
        for key in (field.get("id"), field.get("name")) if key
    }
    for keys, error, message in (
        (("maintenance", "maintaince"), MobisMaintenanceError, "Mobis is temporarily unavailable (maintenance=Y)"),
        (("ratelimited",), MobisRateLimitedError, "Mobis rate limit reached"),
        (("sessionexpired",), MobisSessionExpiredError, "Mobis session expired"),
        (("black",), MobisBlockedError, "Mobis marked this request as abnormal access (black=Y)"),
    ):
        if active_flags.intersection(keys):
            raise error(message)
    return soup


def validate_source_status(html: str) -> None:
    _soup(html)


def _explicit_empty(soup: BeautifulSoup) -> bool:
    # Mobis returns this marker for a completed search with no matches.
    # A missing table, a login page or an empty HTTP 200 is NOT proof of absence.
    return any(
        "없" in node.get_text() or re.search(r"no (?:data|results|matches)", node.get_text(), re.I)
        for node in soup.select(".nodata, .no-data")
    )


def _rows(soup: BeautifulSoup) -> Iterable[list[str]]:
    for row in soup.find_all("tr"):
        cells = [_cell_text(cell) for cell in row.find_all(["th", "td"])]
        cells = [cell for cell in cells if cell]
        if cells:
            yield cells

    for row in soup.select("ul.table-d li[role='row']"):
        cells = [_cell_text(cell) for cell in row.select("span.t-td")]
        cells = [cell for cell in cells if cell]
        if cells:
            yield cells


def _mobis_list_rows(soup: BeautifulSoup) -> Iterable[list[str]]:
    for row in soup.select("ul.table-d li[role='row']"):
        cells = [_cell_text(cell) for cell in row.select("span.t-td")]
        cells = [cell for cell in cells if cell]
        if cells:
            yield cells


def _looks_like_header(cells: list[str]) -> bool:
    joined = " ".join(cells)
    return any(token in joined for token in ("부품번호", "부품명", "가격", "판매점", "주소", "전화"))


def _normalize_part_number(part_number: str) -> str:
    return part_number.replace("-", "").strip().upper()


def _find_part_index(cells: list[str], part_number: str) -> int | None:
    wanted = _normalize_part_number(part_number)
    for index, cell in enumerate(cells):
        normalized = _normalize_part_number(cell)
        if normalized == wanted:
            return index
    return None


def _price_from_cells(cells: list[str]) -> str | None:
    candidates = []
    for cell in cells:
        value = cell.replace(",", "").replace("원", "").strip()
        if value.isdigit():
            candidates.append(value)
    return candidates[-1] if candidates else None


def _extract_phone(value: str) -> str | None:
    phone = _PHONE_RE.search(value)
    return phone.group(0) if phone else None


def parse_price(html: str, part_number: str) -> ParsedPrice | None:
    soup = _soup(html)
    wanted = _normalize_part_number(part_number)

    for cells in _mobis_list_rows(soup):
        if len(cells) < 4 or _normalize_part_number(cells[0]) != wanted:
            continue

        return ParsedPrice(
            part_number=cells[0],
            korean_name=cells[1] or None,
            english_name=cells[2] or None,
            price=_price_from_cells([cells[3]]),
        )

    for cells in _rows(soup):
        if _looks_like_header(cells):
            continue
        part_index = _find_part_index(cells, part_number)
        if part_index is None:
            continue

        after_part = cells[part_index + 1 :]
        korean_name = None
        english_name = None

        for cell in after_part:
            if korean_name is None and re.search(r"[가-힣]", cell):
                korean_name = cell
                continue
            if english_name is None and re.search(r"[A-Za-z]", cell) and not _PART_RE.match(cell):
                english_name = cell
                continue

        return ParsedPrice(
            part_number=cells[part_index],
            korean_name=korean_name,
            english_name=english_name,
            price=_price_from_cells(cells),
        )

    if _explicit_empty(soup) or any(
        len(cells) >= 4 and _PART_RE.match(cells[0])
        for cells in _mobis_list_rows(soup)
    ):
        return None
    raise MobisInvalidResponseError("Mobis price response has no recognized result or explicit no-data marker")


def parse_stores(html: str) -> list[ParsedStore]:
    soup = _soup(html)
    stores: list[ParsedStore] = []

    for cells in _mobis_list_rows(soup):
        if len(cells) >= 5:
            phone = _extract_phone(cells[4])
            if phone:
                stores.append(
                    ParsedStore(
                        store_type=cells[0],
                        name=cells[2],
                        address=cells[3],
                        phone=phone,
                    )
                )
                continue

        if len(cells) >= 4:
            phone = _extract_phone(cells[3])
            if phone:
                stores.append(
                    ParsedStore(
                        store_type=cells[0],
                        name=cells[1],
                        address=cells[2],
                        phone=phone,
                    )
                )

    if stores:
        return stores

    for cells in _rows(soup):
        if _looks_like_header(cells) or len(cells) < 3:
            continue

        phone_index = next((i for i, cell in enumerate(cells) if _extract_phone(cell)), None)
        if phone_index is None:
            continue

        phone_text = _extract_phone(cells[phone_index]) or cells[phone_index]
        before_phone = cells[:phone_index]

        if len(before_phone) >= 4:
            store_type = before_phone[0]
            name = before_phone[2]
            address = " ".join(before_phone[3:])
        elif len(before_phone) >= 3:
            store_type = before_phone[0]
            name = before_phone[1]
            address = " ".join(before_phone[2:])
        elif len(before_phone) == 2:
            store_type = None
            name = before_phone[0]
            address = before_phone[1]
        else:
            continue

        stores.append(
            ParsedStore(
                store_type=store_type,
                name=name,
                address=address,
                phone=phone_text,
            )
        )

    if stores or _explicit_empty(soup):
        return stores
    raise MobisInvalidResponseError("Mobis inventory response has no recognized result or explicit no-data marker")


def parse_max_page(html: str) -> int:
    soup = _soup(html)
    max_page = 1

    for link in soup.select("ul.indicator a"):
        text = clean_text(link.get_text(" ", strip=True))
        if text.isdigit():
            max_page = max(max_page, int(text))

        onclick = link.get("onclick") or ""
        match = _PAGE_RE.search(onclick)
        if match:
            max_page = max(max_page, int(match.group(1)))

    return max_page
