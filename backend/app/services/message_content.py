"""Normalize supported contact/coordinate text; never execute provider markup."""

import math
import re
from urllib.parse import parse_qs, urlsplit


def rich_content(kind: str, body: str) -> dict:
    if kind == "geo":
        candidate = body.strip()
        try:
            url = urlsplit(candidate)
            if url.scheme in ("http", "https"):
                query = parse_qs(url.query)
                candidate = (query.get("q") or query.get("query") or query.get("ll") or [""])[0]
            match = re.fullmatch(
                r"(?:geo:)?\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;]\s*(-?\d{1,3}(?:\.\d+)?)\s*", candidate
            )
            if match:
                lat, lon = map(float, match.groups())
                if (
                    math.isfinite(lat)
                    and math.isfinite(lon)
                    and -90 <= lat <= 90
                    and -180 <= lon <= 180
                ):
                    return {
                        "latitude": lat,
                        "longitude": lon,
                        "map_url": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
                    }
        except ValueError:
            pass
    if kind == "vcard":
        name, phones = "", []
        for line in re.sub(r"\r?\n[ \t]", "", body[:100000]).splitlines():
            key, sep, value = line.partition(":")
            key = key.upper().split(";", 1)[0]
            if sep and key == "FN":
                name = value[:200]
            if sep and key == "TEL":
                phone = re.sub(r"[^+0-9]", "", value.removeprefix("tel:"))[:30]
                if phone:
                    phones.append(phone)
        if name or phones:
            return {"name": name, "phones": list(dict.fromkeys(phones))[:20]}
    return {}
