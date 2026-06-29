from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .db import connect, init_v3_db
from .forecast_archive import TEMPERATURE_KEYS


def forecast_hourly_points(
    targets: dict[str, set[str]] | None = None,
    db_path: Path | None = None,
    max_sources_per_target: int = 4,
) -> dict[str, list[dict[str, Any]]]:
    """Aggregate archived forecast member hourly data for dashboard use.

    The archive can contain repeated runs for the same city/date. For the
    dashboard we keep only the latest run for each source/provider/model so
    old snapshots do not masquerade as independent hourly evidence.
    """
    init_v3_db(db_path)
    normalized_targets = {
        str(city or "").strip().lower(): {str(date) for date in dates if date}
        for city, dates in (targets or {}).items()
        if city
    }
    with connect(db_path) as conn:
        where = ["COALESCE(run_type, 'forecast') = 'forecast'"]
        params: list[Any] = []
        if normalized_targets:
            cities = sorted(normalized_targets)
            dates = sorted({date for dates_for_city in normalized_targets.values() for date in dates_for_city})
            where.append(f"city IN ({','.join('?' for _ in cities)})")
            where.append(f"target_date IN ({','.join('?' for _ in dates)})")
            params.extend(cities)
            params.extend(dates)
        run_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM forecast_runs
                WHERE {' AND '.join(where)}
                ORDER BY city, target_date, COALESCE(retrieved_at, run_at, created_at) DESC, id DESC
                """,
                params,
            ).fetchall()
        ]
        selected_runs: list[dict[str, Any]] = []
        source_counts: dict[tuple[str, str], int] = defaultdict(int)
        seen_source_keys: set[tuple[str, str, str, str, str]] = set()
        for run in run_rows:
            city = str(run.get("city") or "").strip().lower()
            target_date = str(run.get("target_date") or "")
            if not city or not target_date:
                continue
            if normalized_targets and target_date not in normalized_targets.get(city, set()):
                continue
            target_key = (city, target_date)
            if source_counts[target_key] >= max_sources_per_target:
                continue
            source_key = (
                city,
                target_date,
                str(run.get("source") or ""),
                str(run.get("provider") or ""),
                str(run.get("model") or ""),
            )
            if source_key in seen_source_keys:
                continue
            seen_source_keys.add(source_key)
            source_counts[target_key] += 1
            selected_runs.append(run)

        if not selected_runs:
            return {}

        run_ids = [int(run["id"]) for run in selected_runs]
        placeholders = ",".join("?" for _ in run_ids)
        member_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM forecast_members
                WHERE run_id IN ({placeholders})
                ORDER BY run_id, member_id
                """,
                run_ids,
            ).fetchall()
        ]

    runs_by_id = {int(run["id"]): run for run in selected_runs}
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for member in member_rows:
        run = runs_by_id.get(int(member.get("run_id") or 0))
        if not run:
            continue
        hourly = _loads(member.get("hourly_json"), [])
        if not isinstance(hourly, list):
            continue
        source = _source_label(run)
        for item in hourly:
            if not isinstance(item, dict):
                continue
            valid_at = str(item.get("valid_at") or item.get("time") or item.get("timestamp") or "").strip()
            if not valid_at:
                continue
            temp = _temperature_value(item)
            if temp is None:
                continue
            unit = str(run.get("unit") or "")
            city = str(run.get("city") or "").strip().lower()
            target_date = str(run.get("target_date") or "")
            key = (city, target_date, valid_at)
            bucket = buckets.setdefault(
                key,
                {
                    "timestamp": valid_at,
                    "target_date": target_date,
                    "city": city,
                    "values": [],
                    "humidity_values": [],
                    "sources": set(),
                    "source_values": defaultdict(list),
                    "unit": unit,
                    "horizon": run.get("horizon") or "",
                },
            )
            bucket["values"].append(float(temp))
            bucket["sources"].add(source)
            bucket["source_values"][source].append(float(temp))
            humidity = _float(item.get("relative_humidity_2m") or item.get("humidity") or item.get("rh"))
            if humidity is not None:
                bucket["humidity_values"].append(humidity)

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (city, _target_date, _valid_at), bucket in buckets.items():
        values = bucket["values"]
        if not values:
            continue
        source_values = bucket["source_values"]
        source_parts = sorted(str(source) for source in bucket["sources"] if source)
        point = {
            "timestamp": bucket["timestamp"],
            "target_date": bucket["target_date"],
            "horizon": bucket["horizon"],
            "best": _mean(values),
            "ensemble_mean": _mean(values),
            "ensemble_std": _std(values),
            "humidity": _mean(bucket["humidity_values"]),
            "source": " + ".join(source_parts) if source_parts else "forecast_archive",
            "member_count": len(values),
            "ecmwf": _mean(_matching_source_values(source_values, "ecmwf")),
            "hrrr": _mean(_matching_source_values(source_values, "hrrr", "gfs")),
            "archive": True,
        }
        by_city[city].append(point)

    return {
        city: sorted(points, key=lambda point: str(point.get("timestamp") or ""))
        for city, points in by_city.items()
    }


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _temperature_value(item: dict[str, Any]) -> float | None:
    for key in TEMPERATURE_KEYS:
        value = _float(item.get(key))
        if value is not None:
            return value
    return None


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _mean(values: list[float]) -> float | None:
    valid = [value for value in values if math.isfinite(value)]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _std(values: list[float]) -> float | None:
    valid = [value for value in values if math.isfinite(value)]
    if len(valid) <= 1:
        return 0.0 if valid else None
    avg = sum(valid) / len(valid)
    return math.sqrt(sum((value - avg) ** 2 for value in valid) / len(valid))


def _source_label(run: dict[str, Any]) -> str:
    return str(run.get("source") or run.get("provider") or run.get("model") or "forecast_archive")


def _matching_source_values(source_values: dict[str, list[float]], *needles: str) -> list[float]:
    values: list[float] = []
    lowered_needles = tuple(needle.lower() for needle in needles)
    for source, source_items in source_values.items():
        lower = str(source).lower()
        if any(needle in lower for needle in lowered_needles):
            values.extend(source_items)
    return values
