from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .config import DATA_DIR


HISTORY_CACHE_PATH = DATA_DIR / "weather_history_cache.json"


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
    source_url = "https://archive-api.open-meteo.com/v1/archive"
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
