from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .db import log_data_fetch
from .registry import SETTLEMENT_REGISTRY, get_city_profile
from .stations import apply_market_probe_result, get_station, sync_station_registry


GAMMA_EVENT_BY_SLUG_URL = "https://gamma-api.polymarket.com/events/slug/{slug}"
PROBE_VERSION = "polymarket-market-probe-v1"
USER_AGENT = "WeatherBot/settlement-probe (local research)"


@dataclass(frozen=True)
class ProbeCandidate:
    city_key: str
    slug: str
    target_date: str
    url: str


def probe_polymarket_markets(
    city_keys: list[str],
    *,
    days_ahead: int = 3,
    apply: bool = True,
    today: date | None = None,
    fetch_json: Callable[[str], dict[str, Any]] | None = None,
    path: Path | None = None,
    sleep_seconds: float = 0.15,
) -> dict[str, Any]:
    sync_station_registry(path)
    unique_cities = _unique_city_keys(city_keys)
    start_date = today or datetime.now(timezone.utc).date()
    results = []
    for city_key in unique_cities:
        started = time.perf_counter()
        result = _probe_city(
            city_key,
            start_date=start_date,
            days_ahead=days_ahead,
            fetch_json=fetch_json or fetch_gamma_json,
            path=path,
        )
        if apply:
            result["write_result"] = apply_market_probe_result(result, path=path)
        duration_ms = round((time.perf_counter() - started) * 1000)
        status = "OK" if result.get("active_market") else "WARN"
        log_data_fetch(
            source="polymarket_gamma",
            stage="settlement_rule_probe",
            status=status,
            city=city_key,
            target_date=str(result.get("target_date") or ""),
            duration_ms=duration_ms,
            message=result.get("status") or ("verified" if result.get("active_market") else "no_active_market"),
            details=result,
            log_key=f"{PROBE_VERSION}:{city_key}:{result.get('event_slug') or 'no-active'}",
            path=path,
        )
        results.append(result)
        if sleep_seconds:
            time.sleep(max(0.0, float(sleep_seconds)))
    return {
        "ok": all(row.get("ok") for row in results),
        "probe_version": PROBE_VERSION,
        "applied": bool(apply),
        "requested": len(unique_cities),
        "active": sum(1 for row in results if row.get("active_market")),
        "no_active_market": sum(1 for row in results if row.get("status") == "no_active_market"),
        "mismatches": sum(1 for row in results if row.get("settlement_mismatch")),
        "results": results,
    }


def parse_settlement_rule_text(
    *,
    city_key: str,
    description: str,
    source_url: str = "",
    event_slug: str = "",
) -> dict[str, Any]:
    profile = get_city_profile(city_key)
    text = _clean_text(description)
    source_url = str(source_url or "").strip()
    station_id = _station_from_url(source_url)
    station_name = ""
    source = ""
    if not station_id:
        station_id = _station_from_text(text)
    if "Hong Kong Observatory" in text:
        station_id = station_id or "HKO"
        station_name = "Hong Kong Observatory"
        source = "hong_kong_observatory"
    if not station_name:
        station_name = _station_name_from_text(text)
    if not source:
        if "Wunderground" in text or "wunderground.com" in source_url:
            source = "wunderground"
        elif "China Meteorological" in text or "中国气象" in text:
            source = "china_meteorological_administration"
        elif "Japan Meteorological" in text:
            source = "japan_meteorological_agency"
        else:
            source = "unknown"
    unit = ""
    if re.search(r"degrees\s+Fahrenheit", text, re.I):
        unit = "F"
    elif re.search(r"degrees\s+Celsius|deg\.\s*C", text, re.I):
        unit = "C"
    time_basis = "local_day" if re.search(r"all times on this day|specified date|daily max", text, re.I) else "unknown"
    return {
        "city_key": city_key,
        "event_slug": event_slug,
        "settlement_rule_text": text,
        "settlement_station_id": station_id.upper(),
        "settlement_station_name": station_name,
        "primary_settlement_source": source,
        "settlement_unit": unit or (profile.unit if profile else ""),
        "settlement_timezone": profile.timezone if profile else "",
        "settlement_time_basis": time_basis,
        "source_url": source_url,
        "parse_version": PROBE_VERSION,
    }


def fetch_gamma_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def market_probe_summary_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in results:
        rows.append({
            "city": item.get("city_key") or "",
            "active_market": "yes" if item.get("active_market") else "no_active_market",
            "settlement_station": item.get("settlement_station_id") or "--",
            "source": item.get("primary_settlement_source") or "--",
            "unit": item.get("settlement_unit") or "--",
            "timezone": item.get("settlement_timezone") or "--",
            "status": item.get("verification_status") or item.get("status") or "--",
        })
    return rows


def _probe_city(
    city_key: str,
    *,
    start_date: date,
    days_ahead: int,
    fetch_json: Callable[[str], dict[str, Any]],
    path: Path | None,
) -> dict[str, Any]:
    profile = get_city_profile(city_key)
    if not profile:
        return {"ok": False, "city_key": city_key, "status": "unknown_city", "active_market": False}
    attempted = []
    for candidate in _candidates(city_key, start_date, days_ahead):
        attempted.append(candidate.slug)
        try:
            event = fetch_json(candidate.url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            return {
                "ok": False,
                "city_key": city_key,
                "status": "gamma_error",
                "active_market": False,
                "error": f"http_{exc.code}",
                "attempted_slugs": attempted,
            }
        except Exception as exc:
            return {
                "ok": False,
                "city_key": city_key,
                "status": "gamma_error",
                "active_market": False,
                "error": str(exc),
                "attempted_slugs": attempted,
            }
        markets = event.get("markets") if isinstance(event.get("markets"), list) else []
        active = bool(event.get("active")) and not bool(event.get("closed")) and any(
            bool(market.get("active")) and not bool(market.get("closed"))
            for market in markets
        )
        if not active:
            continue
        market = next((m for m in markets if bool(m.get("active")) and not bool(m.get("closed"))), markets[0] if markets else {})
        description = str(market.get("description") or event.get("description") or "")
        source_url = str(market.get("resolutionSource") or event.get("resolutionSource") or "")
        parsed = parse_settlement_rule_text(
            city_key=city_key,
            description=description,
            source_url=source_url,
            event_slug=str(event.get("slug") or candidate.slug),
        )
        current = get_station(city_key, path) or {}
        current_station = str(current.get("station_id") or profile.station_id or "").upper()
        settlement_station = str(parsed.get("settlement_station_id") or "").upper()
        mismatch = bool(current_station and settlement_station and current_station != settlement_station)
        contract_complete = _parsed_contract_complete(parsed)
        return {
            "ok": True,
            "city_key": city_key,
            "city_name": profile.city_name,
            "status": "active_market",
            "active_market": True,
            "event_slug": str(event.get("slug") or candidate.slug),
            "event_title": str(event.get("title") or ""),
            "event_url": f"https://polymarket.com/event/{event.get('slug') or candidate.slug}",
            "gamma_url": candidate.url,
            "target_date": candidate.target_date,
            "current_station_id": current_station,
            "settlement_mismatch": mismatch,
            "verification_status": (
                "settlement_mismatch"
                if contract_complete and mismatch
                else "verified" if contract_complete else "unverified"
            ),
            **parsed,
            "market_count": len(markets),
            "market_id": str(market.get("id") or ""),
            "attempted_slugs": attempted,
        }
    return {
        "ok": True,
        "city_key": city_key,
        "city_name": profile.city_name,
        "status": "no_active_market",
        "active_market": False,
        "current_station_id": profile.station_id,
        "settlement_mismatch": False,
        "verification_status": "no_active_market",
        "settlement_timezone": profile.timezone,
        "settlement_unit": profile.unit,
        "attempted_slugs": attempted,
    }


def _candidates(city_key: str, start_date: date, days_ahead: int) -> list[ProbeCandidate]:
    candidates = []
    for offset in range(max(0, int(days_ahead)) + 1):
        target = start_date + timedelta(days=offset)
        slug = f"highest-temperature-in-{city_key}-on-{target.strftime('%B').lower()}-{target.day}-{target.year}"
        candidates.append(ProbeCandidate(
            city_key=city_key,
            slug=slug,
            target_date=target.isoformat(),
            url=GAMMA_EVENT_BY_SLUG_URL.format(slug=urllib.parse.quote(slug)),
        ))
    return candidates


def _unique_city_keys(city_keys: list[str]) -> list[str]:
    seen = []
    for raw in city_keys:
        key = str(raw or "").strip().lower()
        if key and key not in seen:
            seen.append(key)
    return seen


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _station_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    for part in reversed([item for item in parsed.path.upper().split("/") if item]):
        if _looks_like_station_id(part):
            return part
    return ""


def _station_from_text(text: str) -> str:
    if re.search(r"Hong\s+Kong\s+Observatory", text, re.I):
        return "HKO"
    for pattern in (
        r"\bstation\s+(?P<station>[A-Z]{4})\b",
        r"\b(?P<station>HKO)\b",
    ):
        match = re.search(pattern, text.upper())
        if match:
            return match.group("station")
    return ""


def _looks_like_station_id(value: str) -> bool:
    text = str(value or "").strip().upper()
    if text == "HKO":
        return True
    return bool(re.fullmatch(r"[A-Z]{4}", text))


def _parsed_contract_complete(parsed: dict[str, Any]) -> bool:
    source = str(parsed.get("primary_settlement_source") or "").strip().lower()
    time_basis = str(parsed.get("settlement_time_basis") or "").strip().lower()
    return all(
        str(parsed.get(field) or "").strip()
        for field in (
            "settlement_rule_text",
            "settlement_station_id",
            "settlement_timezone",
            "settlement_unit",
        )
    ) and source not in {"", "unknown"} and time_basis not in {"", "unknown"}


def _station_name_from_text(text: str) -> str:
    patterns = (
        r"recorded at the (?P<name>.+?) Station in degrees",
        r"recorded by the (?P<name>.+?) in degrees",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _clean_text(match.group("name"))
    return ""
