from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import connect, init_v3_db
from .forecasts.ensemble import model_family
from .registry import SETTLEMENT_REGISTRY


DEFAULT_BIAS_TABLE = DATA_DIR / "bias_table.json"
DEFAULT_CITY_MODELS = {
    "shanghai": ("ecmwf", "gfs", "cma"),
    "beijing": ("ecmwf", "gfs", "cma"),
    "wuhan": ("ecmwf", "gfs", "cma"),
    "qingdao": ("ecmwf", "gfs", "cma"),
    "shenzhen": ("ecmwf", "gfs", "cma"),
    "hong-kong": ("ecmwf", "gfs", "cma"),
    "tokyo": ("ecmwf", "gfs", "jma"),
    "seoul": ("ecmwf", "gfs", "jma"),
    "taipei": ("ecmwf", "gfs", "jma"),
    "singapore": ("ecmwf", "gfs", "icon"),
}


def train_bias_table(
    *,
    cities: list[str] | None = None,
    days: int = 90,
    path: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    selected = [str(city).strip().lower() for city in (cities or DEFAULT_CITY_MODELS.keys()) if str(city).strip()]
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    for city in selected:
        profile = SETTLEMENT_REGISTRY.get(city)
        if not profile:
            continue
        truth_by_date = _truth_by_date(profile.station_id, city, path)
        for family in DEFAULT_CITY_MODELS.get(city, ("ecmwf", "gfs", "icon")):
            residuals = _residuals_for_family(city, family, truth_by_date, days, path)
            additive_bias = statistics.median(residuals) if residuals else 0.0
            rows.append({
                "city": city,
                "icao": profile.station_id,
                "model": family,
                "additive_bias_c": round(float(additive_bias), 4),
                "sample_count": len(residuals),
                "lookback_days": int(days),
                "last_trained_at": now,
                "bias_definition": "forecast_high_c_minus_truth_high_c",
            })
    payload = {
        "generated_at": now,
        "rows": rows,
        "row_count": len(rows),
        "source": "weatherbot_v3.bias.train_bias_table",
    }
    destination = output_path or DEFAULT_BIAS_TABLE
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _truth_by_date(station_id: str, city: str, path: Path | None) -> dict[str, float]:
    station = str(station_id or "").upper()
    truth: dict[str, float] = {}
    with connect(path) as conn:
        for row in conn.execute(
            """
            SELECT date_local, high_c
            FROM truth_iem_daily
            WHERE UPPER(icao) = ? AND high_c IS NOT NULL
            ORDER BY date_local DESC
            """,
            (station,),
        ).fetchall():
            truth[str(row["date_local"])] = float(row["high_c"])
        if city == "hong-kong":
            for row in conn.execute(
                """
                SELECT date_local, high_c
                FROM truth_hko_daily
                WHERE high_c IS NOT NULL
                ORDER BY date_local DESC
                """
            ).fetchall():
                truth[str(row["date_local"])] = float(row["high_c"])
    return truth


def _residuals_for_family(city: str, family: str, truth_by_date: dict[str, float], days: int, path: Path | None) -> list[float]:
    if not truth_by_date:
        return []
    target_dates = list(sorted(truth_by_date.keys(), reverse=True))[: max(1, int(days or 90))]
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT target_date, source, model, unit, mean_high
                FROM forecast_runs
                WHERE city = ?
                  AND target_date IN ({})
                  AND mean_high IS NOT NULL
                  AND COALESCE(training_eligible, 0) = 1
                  AND COALESCE(parse_status, 'parsed') = 'parsed'
                ORDER BY retrieved_at DESC, id DESC
                """.format(",".join("?" for _ in target_dates)),
                (city, *target_dates),
            ).fetchall()
        ]
    seen: set[tuple[str, str]] = set()
    residuals: list[float] = []
    for row in rows:
        row_family = model_family(row.get("source") or row.get("model") or "")
        target_date = str(row.get("target_date") or "")
        key = (target_date, row_family)
        if row_family != family or key in seen or target_date not in truth_by_date:
            continue
        forecast_c = _to_c(float(row["mean_high"]), str(row.get("unit") or "C"))
        residuals.append(forecast_c - truth_by_date[target_date])
        seen.add(key)
    return residuals


def _to_c(value: float, unit: str) -> float:
    return (value - 32.0) * 5.0 / 9.0 if str(unit or "C").upper() == "F" else value

