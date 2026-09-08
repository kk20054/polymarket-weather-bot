from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import urlsplit


NOAA_ROUNDING_RULE = "noaa-wrh-nearest-integer-v1"


def primary_resolution_source(market: dict[str, Any]) -> str:
    """Read the named primary source, never a fallback URL in description text."""
    source = market.get("resolutionSource") or market.get("resolution_source_url") or market.get("resolution_source")
    if source and urlsplit(str(source)).hostname:
        return str(source)
    rule = market.get("settlement_rule")
    if isinstance(rule, dict) and rule.get("source_url"):
        return str(rule["source_url"])
    raw = market.get("raw_json")
    if isinstance(raw, str) and raw:
        raw = json.loads(raw)
    if isinstance(raw, dict):
        source = primary_resolution_source(raw)
        if source:
            return source
    # Some Gamma markets omit resolutionSource. Only an explicit primary-source
    # paragraph is eligible; a later conditional fallback is not a declaration.
    declaration = re.search(
        r"(?:^|\n)\s*The (?:primary )?resolution source for this market will be[^\n]+",
        str(market.get("description") or ""),
        re.IGNORECASE,
    )
    if declaration:
        url = re.search(r"https?://[^\s<>]+", declaration.group(0), re.IGNORECASE)
        if url:
            return url.group(0).rstrip(".,;:)\"]'")
    return str(market.get("source_url") or "")


def is_noaa_primary(market: dict[str, Any]) -> bool:
    source = urlsplit(primary_resolution_source(market).strip())
    return (
        source.hostname in {"weather.gov", "www.weather.gov"}
        and source.path.rstrip("/").lower() == "/wrh/timeseries"
    )


def settlement_rounding_rule(market: dict[str, Any], unit: str) -> str:
    if is_noaa_primary(market):
        return NOAA_ROUNDING_RULE
    return "legacy-celsius-floor" if unit.upper() == "C" else "legacy-fahrenheit-nearest"


def reported_temperature(value: float, unit: str, market: dict[str, Any]) -> float:
    # NOAA WRH obs.js displays Math.round(air_temp_set_1) in both unit modes.
    # Math.round ties toward +infinity, unlike Python's ties-to-even round.
    if settlement_rounding_rule(market, unit) == "legacy-celsius-floor":
        return float(math.floor(value + 1e-9))
    return float(math.floor(value + 0.5))
