from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .config import load_config
from .registry import REGISTRY_VERSION, get_city_profile


@dataclass(frozen=True)
class SettlementRule:
    market_id: str
    event_slug: str
    market_slug: str
    question: str
    city: str
    city_name: str
    station_id: str
    station_name: str
    timezone: str
    unit: str
    bucket_low: float | None
    bucket_high: float | None
    metric: str
    resolution_source_text: str
    source_url: str
    truth_confidence: float
    confidence_reason: str
    contract_id: str
    target_local_date: str
    bucket_boundary: str
    rounding_rule: str
    truth_provider_priority: list[str]
    rule_version: str
    registry_version: str
    parsed_at: str
    manual_verified_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TruthObservation:
    city: str
    city_name: str
    target_date: str
    station_id: str
    station_name: str
    unit: str
    actual_temp: float | None
    provider: str
    source_url: str
    observation_count: int
    source_confidence: float
    calibration_eligible: bool
    reason_if_ineligible: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def market_fields(self) -> dict[str, Any]:
        return {
            "actual_temp": self.actual_temp,
            "actual_provider": self.provider,
            "actual_station": self.station_id,
            "actual_observation_count": self.observation_count,
            "actual_confidence": self.source_confidence,
            "actual_calibration_eligible": self.calibration_eligible,
            "actual_reason_if_ineligible": self.reason_if_ineligible,
            "actual_source_url": self.source_url,
        }


def infer_settlement_rule(
    market: dict[str, Any],
    locations: dict[str, dict[str, Any]] | None = None,
    timezones: dict[str, str] | None = None,
) -> SettlementRule:
    locations = locations or {}
    timezones = timezones or {}
    city = str(market.get("city") or market.get("city_key") or "")
    profile = get_city_profile(city)
    loc = locations.get(city, {})
    event_url = str(market.get("event_url") or "")
    event_slug = _slug_from_url(event_url)
    question = str(market.get("question") or "")
    bucket_low, bucket_high = _parse_temp_range(question)
    if bucket_low is None and market.get("position"):
        pos = market.get("position") or {}
        bucket_low = _float_or_none(pos.get("bucket_low"))
        bucket_high = _float_or_none(pos.get("bucket_high"))

    rule_text = " ".join(
        str(market.get(key) or "")
        for key in (
            "question",
            "description",
            "resolutionSource",
            "rules",
            "resolution_rules",
            "resolvedBy",
        )
    ).strip()
    source_url = _extract_url(rule_text) or event_url
    station_id = str(market.get("station") or loc.get("station") or (profile.station_id if profile else "")).upper()
    has_wunderground = "wunderground" in rule_text.lower()
    confidence = 0.35
    reason = "missing_station_mapping"
    if station_id:
        confidence = 0.72
        reason = "airport_station_mapping"
    if has_wunderground and station_id:
        confidence = 0.82
        reason = "rule_mentions_wunderground_station_mapped"
    return SettlementRule(
        market_id=str((market.get("position") or {}).get("market_id") or market.get("market_id") or ""),
        event_slug=event_slug,
        market_slug=str(market.get("market_slug") or ""),
        question=question,
        city=city,
        city_name=str(market.get("city_name") or loc.get("name") or city),
        station_id=station_id,
        station_name=str((profile.station_name if profile else "") or loc.get("name") or market.get("city_name") or city),
        timezone=str(timezones.get(city) or market.get("timezone") or (profile.timezone if profile else "UTC")),
        unit=str(market.get("unit") or loc.get("unit") or (profile.unit if profile else "F")),
        bucket_low=bucket_low,
        bucket_high=bucket_high,
        metric="highest_temperature",
        resolution_source_text=rule_text,
        source_url=source_url,
        truth_confidence=round(confidence, 3),
        confidence_reason=reason,
        contract_id=f"{event_slug}:{str((market.get('position') or {}).get('market_id') or market.get('market_id') or '')}",
        target_local_date=str(market.get("date") or market.get("target_date") or ""),
        bucket_boundary="inclusive",
        rounding_rule="source_reported_daily_high",
        truth_provider_priority=[
            "polymarket_resolved",
            "official_station",
            "visual_crossing_station",
            "open_meteo_archive",
        ],
        rule_version="settlement-rule-v1",
        registry_version=REGISTRY_VERSION,
        parsed_at=datetime.now(timezone.utc).isoformat(),
        manual_verified_at=None,
    )


def get_actual_observation(
    city_slug: str,
    date_str: str,
    locations: dict[str, dict[str, Any]],
    timezones: dict[str, str],
    session: requests.Session | None = None,
) -> TruthObservation:
    cfg = load_config()
    loc = locations[city_slug]
    unit = str(loc.get("unit") or "F")
    station = str(loc.get("station") or "").upper()
    city_name = str(loc.get("name") or city_slug)
    tz_name = str(timezones.get(city_slug) or "UTC")
    session = session or _session()

    attempts: list[TruthObservation] = []
    if station.startswith("K"):
        attempts.append(_from_nws_station(session, city_slug, city_name, date_str, station, unit, tz_name))
    if station:
        attempts.append(_from_aviationweather_station(session, city_slug, city_name, date_str, station, unit, tz_name))
    if cfg.visual_crossing_key:
        attempts.append(_from_visual_crossing(session, city_slug, city_name, date_str, station, unit, cfg.visual_crossing_key))
    attempts.append(_from_open_meteo(session, city_slug, city_name, date_str, loc, unit, tz_name, cfg.open_meteo_actual_allowed_for_paper))

    for obs in attempts:
        if obs.actual_temp is not None:
            return obs
    return TruthObservation(
        city=city_slug,
        city_name=city_name,
        target_date=date_str,
        station_id=station,
        station_name=city_name,
        unit=unit,
        actual_temp=None,
        provider="none",
        source_url="",
        observation_count=0,
        source_confidence=0.0,
        calibration_eligible=False,
        reason_if_ineligible="no_truth_provider_returned_temperature",
        raw={"attempts": [obs.to_dict() for obs in attempts]},
    )


def provider_is_live_calibration_eligible(provider: str, confidence: float) -> bool:
    cfg = load_config()
    if provider in {"nws_station", "visual_crossing_station", "aviationweather_station"}:
        return confidence >= 0.7
    if provider == "open_meteo_archive":
        return bool(cfg.open_meteo_actual_allowed_for_live)
    return False


def _from_nws_station(
    session: requests.Session,
    city: str,
    city_name: str,
    date_str: str,
    station: str,
    unit: str,
    tz_name: str,
) -> TruthObservation:
    source_url = f"https://api.weather.gov/stations/{station}/observations"
    try:
        start_utc, end_utc = _local_day_window(date_str, tz_name)
        response = session.get(
            source_url,
            params={"start": start_utc.isoformat().replace("+00:00", "Z"), "end": end_utc.isoformat().replace("+00:00", "Z")},
            headers={"User-Agent": "weatherbot-v4 truth calibration"},
            timeout=(5, 15),
        )
        response.raise_for_status()
        data = response.json()
        values_c = []
        for item in data.get("features", []):
            value = (((item or {}).get("properties") or {}).get("temperature") or {}).get("value")
            if value is not None:
                values_c.append(float(value))
        if values_c:
            temp_c = max(values_c)
            actual = temp_c * 9.0 / 5.0 + 32.0 if unit == "F" else temp_c
            return TruthObservation(city, city_name, date_str, station, city_name, unit, round(actual, 1), "nws_station", source_url, len(values_c), 0.9, True, "", {"max_c": temp_c})
        return _empty_obs(city, city_name, date_str, station, unit, "nws_station", source_url, "nws_no_temperature_observations")
    except Exception as exc:
        return _empty_obs(city, city_name, date_str, station, unit, "nws_station", source_url, f"nws_error:{exc}")


def _from_aviationweather_station(
    session: requests.Session,
    city: str,
    city_name: str,
    date_str: str,
    station: str,
    unit: str,
    tz_name: str,
) -> TruthObservation:
    source_url = "https://aviationweather.gov/api/data/metar"
    try:
        start_utc, end_utc = _local_day_window(date_str, tz_name)
        data = session.get(
            source_url,
            params={"ids": station, "format": "json", "taf": "false", "hours": 48},
            timeout=(5, 12),
        ).json()
        values_c = []
        for item in data if isinstance(data, list) else []:
            obs_time = item.get("obsTime") or item.get("reportTime")
            parsed = _parse_time(obs_time)
            if parsed and not (start_utc <= parsed < end_utc):
                continue
            value = item.get("temp")
            if value is not None:
                values_c.append(float(value))
        if values_c:
            temp_c = max(values_c)
            actual = temp_c * 9.0 / 5.0 + 32.0 if unit == "F" else temp_c
            return TruthObservation(city, city_name, date_str, station, city_name, unit, round(actual, 1), "aviationweather_station", source_url, len(values_c), 0.74, True, "", {"max_c": temp_c})
        return _empty_obs(city, city_name, date_str, station, unit, "aviationweather_station", source_url, "aviationweather_no_temperature_observations")
    except Exception as exc:
        return _empty_obs(city, city_name, date_str, station, unit, "aviationweather_station", source_url, f"aviationweather_error:{exc}")


def _from_visual_crossing(
    session: requests.Session,
    city: str,
    city_name: str,
    date_str: str,
    station: str,
    unit: str,
    api_key: str,
) -> TruthObservation:
    vc_unit = "us" if unit == "F" else "metric"
    source_url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{station}/{date_str}/{date_str}"
    try:
        data = session.get(
            source_url,
            params={"unitGroup": vc_unit, "key": api_key, "include": "days", "elements": "tempmax"},
            timeout=(5, 12),
        ).json()
        days = data.get("days") or []
        if days and days[0].get("tempmax") is not None:
            return TruthObservation(city, city_name, date_str, station, city_name, unit, round(float(days[0]["tempmax"]), 1), "visual_crossing_station", source_url, 1, 0.82, True, "", {"day": days[0]})
        return _empty_obs(city, city_name, date_str, station, unit, "visual_crossing_station", source_url, "visual_crossing_no_tempmax")
    except Exception as exc:
        return _empty_obs(city, city_name, date_str, station, unit, "visual_crossing_station", source_url, f"visual_crossing_error:{exc}")


def _from_open_meteo(
    session: requests.Session,
    city: str,
    city_name: str,
    date_str: str,
    loc: dict[str, Any],
    unit: str,
    tz_name: str,
    paper_allowed: bool,
) -> TruthObservation:
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    source_url = "https://archive-api.open-meteo.com/v1/archive"
    try:
        data = session.get(
            source_url,
            params={
                "latitude": loc.get("lat"),
                "longitude": loc.get("lon"),
                "start_date": date_str,
                "end_date": date_str,
                "daily": "temperature_2m_max",
                "temperature_unit": temp_unit,
                "timezone": tz_name,
            },
            timeout=(5, 12),
        ).json()
        temps = data.get("daily", {}).get("temperature_2m_max", [])
        if temps and temps[0] is not None:
            return TruthObservation(
                city,
                city_name,
                date_str,
                str(loc.get("station") or "").upper(),
                city_name,
                unit,
                round(float(temps[0]), 1),
                "open_meteo_archive",
                source_url,
                1,
                0.45,
                bool(paper_allowed),
                "" if paper_allowed else "open_meteo_fallback_not_calibration_eligible",
                {"daily": data.get("daily", {})},
            )
        return _empty_obs(city, city_name, date_str, str(loc.get("station") or "").upper(), unit, "open_meteo_archive", source_url, "open_meteo_no_temperature")
    except Exception as exc:
        return _empty_obs(city, city_name, date_str, str(loc.get("station") or "").upper(), unit, "open_meteo_archive", source_url, f"open_meteo_error:{exc}")


def _empty_obs(city: str, city_name: str, date_str: str, station: str, unit: str, provider: str, source_url: str, reason: str) -> TruthObservation:
    return TruthObservation(city, city_name, date_str, station, city_name, unit, None, provider, source_url, 0, 0.0, False, reason, {})


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _local_day_window(date_str: str, tz_name: str) -> tuple[datetime, datetime]:
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    start_local = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    return url.rstrip("/").split("/")[-1]


def _extract_url(text: str) -> str:
    match = re.search(r"https?://[^\s)]+", text or "")
    return match.group(0) if match else ""


def _parse_temp_range(question: str) -> tuple[float | None, float | None]:
    if not question:
        return None, None
    num = r"(-?\d+(?:\.\d+)?)"
    if re.search(r"or below", question, re.IGNORECASE):
        match = re.search(num + r"\s*[°]?[FC]\s*or below", question, re.IGNORECASE)
        if match:
            return -999.0, float(match.group(1))
    if re.search(r"or higher", question, re.IGNORECASE):
        match = re.search(num + r"\s*[°]?[FC]\s*or higher", question, re.IGNORECASE)
        if match:
            return float(match.group(1)), 999.0
    match = re.search(r"between\s+" + num + r"\s*-\s*" + num + r"\s*[°]?[FC]", question, re.IGNORECASE)
    if match:
        return float(match.group(1)), float(match.group(2))
    match = re.search(r"be\s+" + num + r"\s*[°]?[FC]\s+on", question, re.IGNORECASE)
    if match:
        value = float(match.group(1))
        return value, value
    return None, None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None
