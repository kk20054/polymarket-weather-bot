from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from .config import DATA_DIR
from .db import log_data_fetch, upsert_mesonet_observation, utc_now
from .registry import SETTLEMENT_REGISTRY, CitySettlementProfile
from .stations import list_stations


HISTORY_CACHE_PATH = DATA_DIR / "weather_history_cache.json"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_HISTORY_PARSER_VERSION = "openmeteo-historical-v1"
OPEN_METEO_HISTORY_NETWORK = "open_meteo_historical"
OPEN_METEO_HISTORY_HOURLY_FIELDS = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "surface_pressure",
    "pressure_msl",
    "precipitation",
    "weather_code",
)
OPEN_METEO_HISTORY_DAILY_FIELDS = ("temperature_2m_max",)


@dataclass(frozen=True)
class HistoricalWeatherPoint:
    city: str
    city_name: str
    station_id: str
    target_date: str
    unit: str
    actual_high: float | None
    humidity_mean: float | None
    provider: str
    source_confidence: float
    calibration_tier: str
    source_url: str
    fetched_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_history_cache(path: Path = HISTORY_CACHE_PATH) -> dict[str, list[dict[str, Any]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}
    except Exception:
        pass
    return {}


def save_history_cache(cache: dict[str, list[dict[str, Any]]], path: Path = HISTORY_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_history_points(points: list[dict[str, Any]], path: Path = HISTORY_CACHE_PATH) -> dict[str, list[dict[str, Any]]]:
    cache = load_history_cache(path)
    for point in points:
        city = str(point.get("city") or "")
        target_date = str(point.get("target_date") or "")
        if not city or not target_date:
            continue
        rows = cache.setdefault(city, [])
        keyed = {str(row.get("target_date")): row for row in rows}
        keyed[target_date] = point
        cache[city] = sorted(keyed.values(), key=lambda row: str(row.get("target_date") or ""))[-365:]
    save_history_cache(cache, path)
    return cache


def market_history_points(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for market in markets:
        actual = market.get("actual_temp")
        city = str(market.get("city") or "")
        target_date = str(market.get("date") or "")
        if actual is None or not city or not target_date:
            continue
        try:
            actual_high = float(actual)
        except Exception:
            continue
        provider = str(market.get("actual_provider") or "market_actual")
        eligible = bool(market.get("actual_calibration_eligible"))
        confidence = float(market.get("actual_confidence") or (0.82 if eligible else 0.45))
        points.append(HistoricalWeatherPoint(
            city=city,
            city_name=str(market.get("city_name") or city),
            station_id=str(market.get("actual_station") or market.get("station") or ""),
            target_date=target_date,
            unit=str(market.get("unit") or "F"),
            actual_high=round(actual_high, 1),
            humidity_mean=None,
            provider=provider,
            source_confidence=confidence,
            calibration_tier="live_truth" if eligible else "research_truth",
            source_url=str(market.get("actual_source_url") or market.get("event_url") or ""),
        ).to_dict())
    return points


def fetch_open_meteo_history(
    city_slug: str,
    loc: dict[str, Any],
    tz_name: str,
    days: int = 30,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Fetch research-grade historical high temps and humidity.

    This is intentionally a backfill helper rather than a dashboard hot path.
    Open-Meteo archive is useful for research calibration and charts, but it is
    not treated as high-confidence live settlement truth.
    """
    unit = str(loc.get("unit") or "F")
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=max(1, days) - 1)
    source_url = OPEN_METEO_ARCHIVE_URL
    session = session or requests.Session()
    session.trust_env = False
    response = session.get(
        source_url,
        params={
            "latitude": loc.get("lat"),
            "longitude": loc.get("lon"),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "temperature_2m_max",
            "hourly": "relative_humidity_2m",
            "temperature_unit": temp_unit,
            "timezone": tz_name,
        },
        timeout=(5, 20),
    )
    response.raise_for_status()
    data = response.json()
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    humidity_by_date = _humidity_mean_by_date(data.get("hourly") or {})
    fetched_at = datetime.utcnow().isoformat() + "Z"
    rows: list[dict[str, Any]] = []
    for idx, day in enumerate(dates):
        high = highs[idx] if idx < len(highs) else None
        rows.append(HistoricalWeatherPoint(
            city=city_slug,
            city_name=str(loc.get("name") or city_slug),
            station_id=str(loc.get("station") or ""),
            target_date=str(day),
            unit=unit,
            actual_high=round(float(high), 1) if high is not None else None,
            humidity_mean=humidity_by_date.get(str(day)),
            provider="open_meteo_archive",
            source_confidence=0.45,
            calibration_tier="research_truth",
            source_url=source_url,
            fetched_at=fetched_at,
        ).to_dict())
    return rows


def fetch_open_meteo_historical_backfill(
    cities: list[str] | None = None,
    *,
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
    dry_run: bool = False,
    limit_cities: int = 0,
    session: requests.Session | None = None,
    history_cache_path: Path = HISTORY_CACHE_PATH,
) -> dict[str, Any]:
    """Backfill display-only historical hourly weather into mesonet_observations.

    Open-Meteo Archive is useful for PolyWX-style history density and research
    comparisons. It is not settlement truth and does not unlock live trading.
    """
    profiles = _select_history_profiles(cities, limit_cities=limit_cities)
    client = session or requests.Session()
    client.trust_env = False
    fetched_at = utc_now()
    results: list[dict[str, Any]] = []
    total_hourly = 0
    total_daily = 0
    failures = 0
    history_points: list[dict[str, Any]] = []
    for profile in profiles:
        params = open_meteo_history_request_params(
            profile,
            days=days,
            start_date=start_date,
            end_date=end_date,
        )
        source_url = _preview_url(OPEN_METEO_ARCHIVE_URL, params)
        started = utc_now()
        started_perf = datetime.now(timezone.utc)
        try:
            if dry_run:
                rows: list[dict[str, Any]] = []
                daily_points: list[dict[str, Any]] = []
                status = "dry_run"
            else:
                response = client.get(
                    OPEN_METEO_ARCHIVE_URL,
                    params=params,
                    headers={"User-Agent": _open_meteo_history_user_agent()},
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                source_url = str(getattr(response, "url", "") or source_url)
                rows = open_meteo_historical_rows_from_response(
                    profile,
                    payload,
                    source_url=source_url,
                    fetched_at=fetched_at,
                )
                daily_points = open_meteo_historical_daily_points_from_response(
                    profile,
                    payload,
                    source_url=source_url,
                    fetched_at=fetched_at,
                )
                for row in rows:
                    upsert_mesonet_observation(row)
                if daily_points:
                    history_points.extend(daily_points)
                status = "parsed"
            duration_ms = _duration_ms(started_perf)
            total_hourly += len(rows)
            total_daily += len(daily_points)
            result = {
                "ok": True,
                "city": profile.city,
                "station_id": profile.station_id,
                "status": status,
                "source_url": source_url,
                "hourly_rows": len(rows),
                "daily_rows": len(daily_points),
                "duration_ms": duration_ms,
            }
            log_data_fetch(
                source=OPEN_METEO_HISTORY_NETWORK,
                stage="historical_backfill",
                status="OK",
                duration_ms=duration_ms,
                city=profile.city,
                message=f"Open-Meteo historical backfill {status} for {profile.city}",
                details=result,
                started_at=started,
                finished_at=utc_now(),
            )
            results.append(result)
        except Exception as exc:
            failures += 1
            duration_ms = _duration_ms(started_perf)
            result = {
                "ok": False,
                "city": profile.city,
                "station_id": profile.station_id,
                "source_url": source_url,
                "error": str(exc),
                "duration_ms": duration_ms,
            }
            log_data_fetch(
                source=OPEN_METEO_HISTORY_NETWORK,
                stage="historical_backfill",
                status="ERROR",
                duration_ms=duration_ms,
                city=profile.city,
                message=f"Open-Meteo historical backfill failed for {profile.city}",
                details=result,
                started_at=started,
                finished_at=utc_now(),
            )
            results.append(result)
    if history_points and not dry_run:
        merge_history_points(history_points, path=history_cache_path)
    return {
        "ok": failures == 0,
        "source": OPEN_METEO_HISTORY_NETWORK,
        "parser_version": OPEN_METEO_HISTORY_PARSER_VERSION,
        "dry_run": dry_run,
        "cities": len(profiles),
        "hourly_rows_upserted": 0 if dry_run else total_hourly,
        "daily_history_points": 0 if dry_run else total_daily,
        "failed": failures,
        "quality_flags": ["display_only", "research_truth", "not_settlement_truth"],
        "results": results,
    }


def open_meteo_history_request_params(
    profile: CitySettlementProfile,
    *,
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    end = _parse_date(end_date) or (date.today() - timedelta(days=1))
    start = _parse_date(start_date) or (end - timedelta(days=max(1, int(days or 30)) - 1))
    unit = str(profile.unit or "C").upper()
    return {
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(OPEN_METEO_HISTORY_DAILY_FIELDS),
        "hourly": ",".join(OPEN_METEO_HISTORY_HOURLY_FIELDS),
        "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
        "timezone": profile.timezone,
        "wind_speed_unit": "kmh",
    }


def open_meteo_historical_rows_from_response(
    profile: CitySettlementProfile,
    payload: dict[str, Any],
    *,
    source_url: str,
    fetched_at: str,
) -> list[dict[str, Any]]:
    hourly = payload.get("hourly") if isinstance(payload, dict) else {}
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time") or []
    rows: list[dict[str, Any]] = []
    for index, raw_time in enumerate(times):
        observed_at = _local_openmeteo_time_to_utc(raw_time, profile.timezone)
        if not observed_at:
            continue
        row_payload = {
            "provider": OPEN_METEO_HISTORY_NETWORK,
            "parser_version": OPEN_METEO_HISTORY_PARSER_VERSION,
            "profile_unit": profile.unit,
            "time": raw_time,
            "temperature_2m": _at(hourly, "temperature_2m", index),
            "relative_humidity_2m": _at(hourly, "relative_humidity_2m", index),
            "dew_point_2m": _at(hourly, "dew_point_2m", index),
            "cloud_cover": _at(hourly, "cloud_cover", index),
            "wind_speed_10m": _at(hourly, "wind_speed_10m", index),
            "wind_direction_10m": _at(hourly, "wind_direction_10m", index),
            "wind_gusts_10m": _at(hourly, "wind_gusts_10m", index),
            "surface_pressure": _at(hourly, "surface_pressure", index),
            "pressure_msl": _at(hourly, "pressure_msl", index),
            "precipitation": _at(hourly, "precipitation", index),
            "weather_code": _at(hourly, "weather_code", index),
        }
        temperature = _as_float(row_payload["temperature_2m"])
        warnings: list[str] = []
        if temperature is None:
            warnings.append("missing_temperature_2m")
        raw_response = json.dumps(row_payload, ensure_ascii=False, sort_keys=True)
        rows.append({
            "observation_key": _stable_key(OPEN_METEO_HISTORY_NETWORK, profile.city, profile.station_id, observed_at),
            "city": profile.city,
            "city_name": profile.city_name,
            "station_id": profile.station_id,
            "station_name": f"{profile.station_name} / Open-Meteo Archive grid",
            "network": OPEN_METEO_HISTORY_NETWORK,
            "observed_at": observed_at,
            "temperature": temperature,
            "humidity": _as_float(row_payload["relative_humidity_2m"]),
            "dew_point": _as_float(row_payload["dew_point_2m"]),
            "wind_direction": _as_float(row_payload["wind_direction_10m"]),
            "wind_speed": _as_float(row_payload["wind_speed_10m"]),
            "wind_gust": _as_float(row_payload["wind_gusts_10m"]),
            "pressure": _as_float(row_payload["pressure_msl"]) or _as_float(row_payload["surface_pressure"]),
            "precipitation": _as_float(row_payload["precipitation"]),
            "source_url": source_url,
            "raw_response": raw_response,
            "raw_response_hash": hashlib.sha256(raw_response.encode("utf-8")).hexdigest(),
            "parser_version": OPEN_METEO_HISTORY_PARSER_VERSION,
            "parse_status": "partial" if warnings else "parsed",
            "parse_warnings": warnings,
            "raw_unit": profile.unit,
            "quality_flags": ["display_only", "research_truth", "not_settlement_truth", "open_meteo_archive_grid"],
            "fetched_at": fetched_at,
            "raw_json": row_payload,
        })
    return rows


def open_meteo_historical_daily_points_from_response(
    profile: CitySettlementProfile,
    payload: dict[str, Any],
    *,
    source_url: str,
    fetched_at: str,
) -> list[dict[str, Any]]:
    daily = payload.get("daily") if isinstance(payload, dict) else {}
    hourly = payload.get("hourly") if isinstance(payload, dict) else {}
    if not isinstance(daily, dict):
        return []
    days = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    humidity_by_day = _humidity_mean_by_date(hourly if isinstance(hourly, dict) else {})
    points: list[dict[str, Any]] = []
    for index, target_day in enumerate(days):
        high = _as_float(highs[index] if index < len(highs) else None)
        points.append(HistoricalWeatherPoint(
            city=profile.city,
            city_name=profile.city_name,
            station_id=profile.station_id,
            target_date=str(target_day),
            unit=profile.unit,
            actual_high=round(high, 1) if high is not None else None,
            humidity_mean=humidity_by_day.get(str(target_day)),
            provider=OPEN_METEO_HISTORY_NETWORK,
            source_confidence=0.45,
            calibration_tier="research_truth",
            source_url=source_url,
            fetched_at=fetched_at,
        ).to_dict())
    return points


def _humidity_mean_by_date(hourly: dict[str, Any]) -> dict[str, float]:
    times = hourly.get("time") or []
    values = hourly.get("relative_humidity_2m") or []
    buckets: dict[str, list[float]] = {}
    for ts, value in zip(times, values):
        if value is None:
            continue
        try:
            day = datetime.fromisoformat(str(ts)).date().isoformat()
            buckets.setdefault(day, []).append(float(value))
        except Exception:
            continue
    return {
        day: round(sum(items) / len(items), 1)
        for day, items in buckets.items()
        if items
    }


def _select_history_profiles(cities: list[str] | None, *, limit_cities: int) -> list[CitySettlementProfile]:
    requested = [str(city or "").strip().lower() for city in (cities or []) if str(city or "").strip()]
    if requested:
        return [SETTLEMENT_REGISTRY[city] for city in requested if city in SETTLEMENT_REGISTRY]
    enabled = [
        str(row.get("city_key") or "").strip().lower()
        for row in list_stations()
        if bool(row.get("enabled"))
    ]
    profiles = [SETTLEMENT_REGISTRY[city] for city in enabled if city in SETTLEMENT_REGISTRY]
    limit = int(limit_cities or 0)
    return profiles if limit <= 0 else profiles[:limit]


def _local_openmeteo_time_to_utc(value: Any, timezone_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(timezone.utc).isoformat()


def _parse_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(str(value or "")).date()
    except Exception:
        return None


def _at(series: dict[str, Any], key: str, index: int) -> Any:
    values = series.get(key) or []
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _stable_key(*parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _preview_url(url: str, params: dict[str, Any]) -> str:
    return f"{url}?{urlencode(params, doseq=True)}"


def _open_meteo_history_user_agent() -> str:
    return "WeatherBot/3.5 (historical-backfill; contact: local-operator@example.com)"


def _duration_ms(started: datetime) -> int:
    return int(max(0.0, (datetime.now(timezone.utc) - started).total_seconds() * 1000))
