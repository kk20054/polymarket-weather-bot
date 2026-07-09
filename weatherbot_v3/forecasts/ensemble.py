from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import DATA_DIR
from ..db import connect, init_v3_db, upsert_model_reprice_event, utc_now
from ..deb import sigma_with_floor
from ..env_utils import env_value
from ..registry import CitySettlementProfile, get_city_profile


ALGO = "ensemble_v1"
POLYWX_ALIGNED_ALGO = "polywx_aligned_deb_v1"
BIAS_TABLE_PATH = DATA_DIR / "bias_table.json"
MIN_FAMILIES_FOR_ENSEMBLE = 2
MIN_MEMBER_COUNT_FOR_SINGLE_FAMILY = 5
SIGMA_FLOOR_C = 0.5
BIAS_MIN_SAMPLE_COUNT = 20

REGION_MODEL_WEIGHTS = {
    "us": {"gfs": 0.40, "ecmwf": 0.50, "hrrr": 0.10},
    "china_hk": {"gfs": 0.20, "ecmwf": 0.50, "cma": 0.30},
    "japan_korea_taipei": {"gfs": 0.20, "ecmwf": 0.40, "jma": 0.40},
    "singapore": {"gfs": 0.40, "ecmwf": 0.60},
    "global": {"gfs": 0.30, "ecmwf": 0.45, "icon": 0.25},
}
POLYWX_ALIGNED_MODEL_WEIGHTS = {
    "weathercom_v3": 0.484,
    "gfs": 0.152,
    "ecmwf": 0.104,
    "icon": 0.095,
    "gem": 0.093,
    "jma": 0.073,
    "cma": 0.073,
    "hrrr": 0.073,
    "nbm": 0.073,
}


def fetch_ensemble(
    icao: str,
    target_date: str,
    models: list[str] | tuple[str, ...] | None = None,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read already-persisted forecast members as member/hour/temp_c rows.

    This intentionally does not call Open-Meteo. The collector layer owns network
    fetches; this layer consumes the persisted Layer 3 archive.
    """
    station = str(icao or "").strip().upper()
    wanted_models = {str(model).strip().lower() for model in (models or []) if str(model).strip()}
    init_v3_db(path)
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fr.id AS run_id, fr.city, fr.target_date, fr.source, fr.model,
                       fr.unit, fr.station_id, fr.retrieved_at, fm.member_id,
                       fm.high_temp, fm.hourly_json
                FROM forecast_runs fr
                JOIN forecast_members fm ON fm.run_id = fr.id
                WHERE UPPER(COALESCE(fr.station_id, '')) = ?
                  AND fr.target_date = ?
                  AND COALESCE(fr.training_eligible, 0) = 1
                  AND COALESCE(fr.parse_status, 'parsed') = 'parsed'
                ORDER BY fr.retrieved_at DESC, fr.id DESC, fm.member_id
                """,
                (station, str(target_date)),
            ).fetchall()
        ]
    latest_by_source: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "").lower()
        model = str(row.get("model") or "").lower()
        if wanted_models and model not in wanted_models and source not in wanted_models:
            continue
        latest_by_source.setdefault(source, int(row.get("run_id") or 0))

    points: list[dict[str, Any]] = []
    for row in rows:
        source = str(row.get("source") or "").lower()
        run_id = int(row.get("run_id") or 0)
        if latest_by_source.get(source) != run_id:
            continue
        unit = str(row.get("unit") or "C").upper()
        hourly = _loads(row.get("hourly_json"), [])
        member = str(row.get("member_id") or "deterministic")
        for point in hourly:
            if not isinstance(point, dict):
                continue
            temp = _first_number(point.get("temperature_2m_c"), point.get("temperature_c"))
            if temp is None:
                temp = _first_number(point.get("temperature_2m"), point.get("temp"))
                if temp is not None:
                    temp = convert_temperature(temp, unit, "C")
            valid_at = str(point.get("valid_at") or point.get("hour") or "")
            if temp is None or not valid_at:
                continue
            points.append({
                "member": f"{source}:{member}",
                "source": source,
                "model": row.get("model") or source.replace("openmeteo_", ""),
                "hour": valid_at,
                "temp_c": round(float(temp), 3),
            })
    return points


def daily_max_distribution(rows: list[dict[str, Any]], tz: str) -> list[dict[str, Any]]:
    zone = _zone(tz)
    highs: dict[str, dict[str, Any]] = {}
    for row in rows:
        member = str(row.get("member") or "")
        if not member:
            continue
        parsed = _parse_time(str(row.get("hour") or row.get("valid_at") or ""))
        temp_c = _first_number(row.get("temp_c"), row.get("temperature_c"), row.get("temperature_2m_c"))
        if parsed is None or temp_c is None:
            continue
        local = parsed.astimezone(zone)
        value = float(temp_c)
        current = highs.get(member)
        if current is None or value > float(current.get("daily_max_c") or -999):
            highs[member] = {
                "member": member,
                "daily_max_c": value,
                "peak_hour_local": local.strftime("%H:00"),
                "source": row.get("source") or "",
                "model": row.get("model") or "",
            }
    return list(highs.values())


def bucket_probabilities(samples: list[Any], bucket_edges: list[dict[str, Any]]) -> dict[str, Any]:
    weighted = _weighted_samples(samples)
    total_weight = sum(weight for _, weight in weighted)
    items: list[dict[str, Any]] = []
    if total_weight <= 0:
        return {"ok": False, "method": "ensemble-sample-v1", "items": [], "sum_probability": 0.0, "notes": ["empty_samples"]}
    for bucket in bucket_edges:
        label = str(bucket.get("label") or bucket.get("bucket_label") or bucket.get("bucket_key") or "")
        low = _first_number(bucket.get("lower_c"), bucket.get("bucket_lower_c"), bucket.get("bucket_low"))
        high = _first_number(bucket.get("upper_c"), bucket.get("bucket_upper_c"), bucket.get("bucket_high"))
        mass = 0.0
        for value, weight in weighted:
            if _sample_in_bucket(value, low, high):
                mass += weight
        probability = mass / total_weight
        items.append({
            "bucket_key": bucket.get("bucket_key") or label,
            "bucket_label": label,
            "bucket_low": low,
            "bucket_high": high,
            "probability": probability,
            "probability_raw": probability,
        })
    total = sum(float(item["probability"]) for item in items)
    if items and abs(total - 1.0) > 1e-6 and total > 0:
        for item in items:
            item["probability"] = float(item["probability"]) / total
            item["probability_raw"] = item["probability"]
        total = 1.0
    return {
        "ok": bool(items),
        "method": "ensemble-sample-v1",
        "items": items,
        "sum_probability": total,
        "normalized": bool(items),
        "notes": [],
    }


def build_ensemble_prediction(
    city_key: str,
    target_date: str,
    *,
    issued_at: str | None = None,
    path: Path | None = None,
    bias_table: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile = get_city_profile(city_key)
    if not profile:
        return {"ok": False, "city_key": city_key, "target_date": target_date, "reasons": ["unknown_city"]}
    init_v3_db(path)
    algo = _deb_algo()
    rows = _latest_forecast_members(profile, target_date, path)
    components = _components_from_rows(profile, rows, bias_table if bias_table is not None else load_bias_table(), path, target_date=target_date)
    usable = [component for component in components if component["member_count"] > 0 and component["family"] in region_model_weights(profile)]
    usable_families = {component["family"] for component in usable}
    total_members = sum(int(component["member_count"]) for component in usable)
    if len(usable_families) < MIN_FAMILIES_FOR_ENSEMBLE and total_members < MIN_MEMBER_COUNT_FOR_SINGLE_FAMILY:
        return {
            "ok": False,
            "city_key": city_key,
            "target_date": target_date,
            "reasons": ["insufficient_ensemble_sources"],
            "families": sorted(usable_families),
            "member_count": total_members,
        }

    weighted = _weighted_member_highs(profile, usable)
    if not weighted:
        return {"ok": False, "city_key": city_key, "target_date": target_date, "reasons": ["empty_weighted_samples"]}
    values = [value for value, _weight, _meta in weighted]
    weights = [weight for _value, weight, _meta in weighted]
    mu = _weighted_mean(values, weights)
    sigma_c = _weighted_std(values, weights)
    sigma_floor = convert_temperature_delta(SIGMA_FLOOR_C, "C", profile.unit)
    sigma = sigma_with_floor(sigma_c if profile.unit == "C" else convert_temperature_delta(sigma_c, "C", profile.unit), sigma_floor)
    issued = issued_at or utc_now()
    samples_unit = [
        {
            "value": round(convert_temperature(value, "C", profile.unit), 4),
            "weight": round(weight, 8),
            **meta,
        }
        for value, weight, meta in weighted
    ]
    peak = _weighted_peak_hour(usable)
    return {
        "ok": True,
        "city_key": profile.city,
        "target_date": target_date,
        "issued_at": issued,
        "mu": round(convert_temperature(mu, "C", profile.unit), 4),
        "sigma": round(sigma, 4),
        "unit": profile.unit,
        "method": algo,
        "deb_version": algo,
        "forecast_algo": algo,
        "algo": algo,
        "model_weights": {component["source"]: component["weight"] for component in usable},
        "member_count": len(samples_unit),
        "components": usable,
        "source_run_ids": sorted({component["run_id"] for component in usable}),
        "member_daily_highs": {
            component["source"]: [
                round(convert_temperature(value, "C", profile.unit), 4)
                for value in component["adjusted_daily_highs_c"]
            ]
            for component in usable
        },
        "sigma_from_spread": _weighted_spread(values),
        "sigma_from_history": sigma_c,
        "bias_correction": 0.0,
        "bias_sample_count": min((int(component.get("bias_sample_count") or 0) for component in usable), default=0),
        "observed_floor": None,
        "sigma_floor": sigma_floor,
        "time_decay_factor": 1.0,
        "mu_observed_floor_applied": False,
        "peak_hour": peak.get("peak_hour") or "",
        "peak_temp": convert_temperature(peak["peak_temp_c"], "C", profile.unit) if peak.get("peak_temp_c") is not None else None,
        "peak_source": "ensemble_weighted",
        "ensemble_samples": samples_unit,
        "ensemble_sample_weights": [row["weight"] for row in samples_unit],
        "build_warnings": _source_warnings(usable, algo),
    }


def distribution_for_prediction(prediction: dict[str, Any], buckets: list[dict[str, Any]]) -> dict[str, Any] | None:
    samples = prediction.get("ensemble_samples") or []
    unit = str(prediction.get("unit") or "C").upper()
    if not samples:
        return None
    converted: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        value = _first_number(sample.get("value"), sample.get("daily_max"), sample.get("daily_max_c"))
        if value is None:
            continue
        converted.append({"value": convert_temperature(float(value), unit, "C"), "weight": _first_number(sample.get("weight")) or 1.0})
    bucket_edges = []
    for bucket in buckets:
        low = _first_number(bucket.get("bucket_low"), bucket.get("bucket_lower_c"))
        high = _first_number(bucket.get("bucket_high"), bucket.get("bucket_upper_c"))
        bucket_unit = str(bucket.get("unit") or unit).upper()
        bucket_edges.append({
            "bucket_key": bucket.get("bucket_key") or "",
            "bucket_label": bucket.get("bucket_label") or "",
            "lower_c": convert_temperature(float(low), bucket_unit, "C") if low is not None else None,
            "upper_c": convert_temperature(float(high), bucket_unit, "C") if high is not None else None,
        })
    result = bucket_probabilities(converted, bucket_edges)
    for item, bucket in zip(result.get("items") or [], buckets):
        item["market_id"] = bucket.get("market_id") or ""
        item["yes_token_id"] = bucket.get("yes_token_id") or ""
        item["bucket_direction"] = bucket.get("bucket_direction") or ""
        item["bucket_unit"] = unit
        market_probability = _market_probability(bucket)
        item["market_probability"] = market_probability
        item["best_bid"] = _first_number(bucket.get("best_bid"))
        item["best_ask"] = _first_number(bucket.get("best_ask"))
        item["price"] = _first_number(bucket.get("price"))
        item["edge"] = None if market_probability is None else float(item.get("probability") or 0.0) - market_probability
    ranked = sorted(result.get("items") or [], key=lambda row: float(row.get("probability") or 0.0), reverse=True)
    result.update({
        "mu": prediction.get("mu"),
        "sigma": prediction.get("sigma"),
        "unit": unit,
        "method": "ensemble-sample-v1",
        "top_model": ranked[:5],
    })
    return result


def previous_run_samples(
    city_key: str,
    target_date: str,
    *,
    path: Path | None = None,
    models: list[str] | tuple[str, ...] | None = None,
    max_lead_days: int = 7,
) -> list[dict[str, Any]]:
    """Return archived Previous Runs daily-high samples for walk-forward checks."""
    wanted_models = {str(model).strip().lower() for model in (models or []) if str(model).strip()}
    max_lead = max(1, min(int(max_lead_days or 7), 7))
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fr.id AS run_id, fr.city, fr.target_date, fr.source, fr.model,
                       fr.unit, fr.lead_hours, fr.run_at, fr.retrieved_at,
                       fm.member_id, fm.high_temp
                FROM forecast_runs fr
                JOIN forecast_members fm ON fm.run_id = fr.id
                WHERE fr.city = ?
                  AND fr.target_date = ?
                  AND COALESCE(fr.training_eligible, 0) = 1
                  AND COALESCE(fr.parse_status, 'parsed') = 'parsed'
                  AND fr.source LIKE 'openmeteo_previous_%'
                ORDER BY fr.lead_hours ASC, fr.model, fm.member_id
                """,
                (str(city_key), str(target_date)),
            ).fetchall()
        ]
    samples: list[dict[str, Any]] = []
    for row in rows:
        model = str(row.get("model") or "").lower()
        source = str(row.get("source") or "").lower()
        if wanted_models and model not in wanted_models and source not in wanted_models:
            continue
        lead_hours = _first_number(row.get("lead_hours")) or 0.0
        if lead_hours > max_lead * 24:
            continue
        value = _first_number(row.get("high_temp"))
        if value is None:
            continue
        samples.append({
            "value": float(value),
            "weight": 1.0,
            "source": row.get("source"),
            "model": row.get("model"),
            "member": row.get("member_id"),
            "lead_hours": lead_hours,
            "run_at": row.get("run_at"),
            "retrieved_at": row.get("retrieved_at"),
        })
    return samples


def previous_run_distribution_for_buckets(
    city_key: str,
    target_date: str,
    buckets: list[dict[str, Any]],
    *,
    path: Path | None = None,
    models: list[str] | tuple[str, ...] | None = None,
    max_lead_days: int = 7,
) -> dict[str, Any]:
    samples = previous_run_samples(
        city_key,
        target_date,
        path=path,
        models=models,
        max_lead_days=max_lead_days,
    )
    result = bucket_probabilities(samples, buckets)
    result["method"] = "openmeteo-previous-runs-v1"
    result["sample_count"] = len(samples)
    return result


def build_model_reprice_events(
    *,
    cities: list[str] | None = None,
    days: int = 2,
    dry_run: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    city_filter = {str(city).strip().lower() for city in (cities or []) if str(city).strip()}
    triggered_at = utc_now()
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT pm.city, pm.target_date, pm.market_id, pm.bucket_label,
                       pm.bucket_lower_c, pm.bucket_upper_c, po.best_bid, po.best_ask
                FROM polymarket_markets pm
                LEFT JOIN (
                    SELECT market_id, MAX(ts) AS latest_ts
                    FROM polymarket_orderbook
                    GROUP BY market_id
                ) latest ON latest.market_id = pm.market_id
                LEFT JOIN polymarket_orderbook po
                  ON po.market_id = pm.market_id AND po.ts = latest.latest_ts
                ORDER BY pm.city, pm.target_date, pm.bucket_lower_c
                """
            ).fetchall()
        ]
        previous_rows = {
            (row["city_key"], row["target_date"], row["market_id"]): row
            for row in conn.execute(
                """
                SELECT city_key, target_date, market_id, model_prob
                FROM model_reprice_events
                WHERE model_source = ?
                ORDER BY triggered_at DESC, id DESC
                """,
                (ALGO,),
            ).fetchall()
        }
    today = datetime.now(timezone.utc).date()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        city = str(row.get("city") or "").lower()
        if city_filter and city not in city_filter:
            continue
        try:
            target = datetime.fromisoformat(str(row.get("target_date"))).date()
        except Exception:
            continue
        if target < today or (target - today).days >= max(1, int(days or 2)):
            continue
        grouped.setdefault((city, str(row.get("target_date"))), []).append(row)

    events: list[dict[str, Any]] = []
    for (city, target_date), bucket_rows in grouped.items():
        prediction = build_ensemble_prediction(city, target_date, issued_at=triggered_at, path=path)
        if not prediction.get("ok"):
            continue
        distribution = distribution_for_prediction(prediction, [
            {
                "bucket_key": str(row.get("market_id") or row.get("bucket_label") or ""),
                "bucket_label": row.get("bucket_label") or "",
                "bucket_low": row.get("bucket_lower_c"),
                "bucket_high": row.get("bucket_upper_c"),
                "unit": "C",
                "best_bid": row.get("best_bid"),
                "best_ask": row.get("best_ask"),
            }
            for row in bucket_rows
        ])
        if not distribution:
            continue
        by_key = {str(item.get("bucket_key") or ""): item for item in distribution.get("items") or []}
        for row in bucket_rows:
            market_id = str(row.get("market_id") or "")
            item = by_key.get(market_id)
            if not item:
                continue
            bid = _first_number(row.get("best_bid"))
            ask = _first_number(row.get("best_ask"))
            market_mid = (bid + ask) / 2 if bid is not None and ask is not None else None
            model_prob = float(item.get("probability") or 0.0)
            previous = previous_rows.get((city, target_date, market_id))
            previous_prob = _first_number(previous["model_prob"]) if previous else None
            delta = None if previous_prob is None else model_prob - previous_prob
            edge = None if market_mid is None else model_prob - market_mid
            event = {
                "event_key": f"{ALGO}:{city}:{target_date}:{market_id}:{triggered_at[:16]}",
                "city_key": city,
                "target_date": target_date,
                "market_id": market_id,
                "bucket_key": market_id,
                "triggered_at": triggered_at,
                "model_source": ALGO,
                "previous_model_prob": previous_prob,
                "model_prob": model_prob,
                "delta_prob": delta,
                "market_mid": market_mid,
                "edge": edge,
                "alpha_candidate": delta is not None and edge is not None and abs(delta) > 0.05 and abs(edge) > 0.08,
            }
            if not dry_run:
                event["id"] = upsert_model_reprice_event(event, path=path)
            events.append(event)
    return {"ok": True, "dry_run": dry_run, "stored": 0 if dry_run else len(events), "events": events}


def load_bias_table(path: Path | None = None) -> list[dict[str, Any]]:
    bias_path = path or BIAS_TABLE_PATH
    try:
        value = json.loads(Path(bias_path).read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value = value.get("rows") or value.get("biases") or []
        return value if isinstance(value, list) else []
    except Exception:
        return []


def region_model_weights(profile: CitySettlementProfile) -> dict[str, float]:
    if _deb_algo() == POLYWX_ALIGNED_ALGO:
        return POLYWX_ALIGNED_MODEL_WEIGHTS
    if profile.region == "us":
        return REGION_MODEL_WEIGHTS["us"]
    if profile.city in {"shanghai", "beijing", "wuhan", "qingdao", "shenzhen", "hong-kong"}:
        return REGION_MODEL_WEIGHTS["china_hk"]
    if profile.city in {"tokyo", "seoul", "taipei"}:
        return REGION_MODEL_WEIGHTS["japan_korea_taipei"]
    if profile.city == "singapore":
        return REGION_MODEL_WEIGHTS["singapore"]
    return REGION_MODEL_WEIGHTS["global"]


def model_family(name: str) -> str:
    raw = str(name or "").lower()
    if "weathercom" in raw or "weather.com" in raw:
        return "weathercom_v3"
    if "hrrr" in raw:
        return "hrrr"
    if "nbm" in raw:
        return "nbm"
    if "ecmwf" in raw or "ifs" in raw or "aifs" in raw:
        return "ecmwf"
    if "gfs" in raw:
        return "gfs"
    if "grapes" in raw or "cma" in raw:
        return "cma"
    if "jma" in raw:
        return "jma"
    if "icon" in raw:
        return "icon"
    if "gem" in raw:
        return "gem"
    return raw.replace("openmeteo_ensemble_", "").replace("openmeteo_", "")


def convert_temperature_delta(value: float, source_unit: str, target_unit: str) -> float:
    source = str(source_unit or "C").upper()
    target = str(target_unit or "C").upper()
    if source == target:
        return float(value)
    if source == "C" and target == "F":
        return float(value) * 9.0 / 5.0
    if source == "F" and target == "C":
        return float(value) * 5.0 / 9.0
    return float(value)


def convert_temperature(value: float, source_unit: str, target_unit: str) -> float:
    source = str(source_unit or "C").upper()
    target = str(target_unit or "C").upper()
    numeric = float(value)
    if source == target:
        return numeric
    if source == "F" and target == "C":
        return (numeric - 32.0) * 5.0 / 9.0
    if source == "C" and target == "F":
        return numeric * 9.0 / 5.0 + 32.0
    return numeric


def _latest_forecast_members(profile: CitySettlementProfile, target_date: str, path: Path | None) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fr.id AS run_id, fr.city, fr.target_date, fr.source, fr.provider,
                       fr.model, fr.retrieved_at, fr.valid_at, fr.unit, fr.mean_high,
                       fr.std_high, fr.member_count, fm.member_id, fm.high_temp,
                       fm.hourly_json
                FROM forecast_runs fr
                JOIN forecast_members fm ON fm.run_id = fr.id
                WHERE fr.city = ?
                  AND fr.target_date = ?
                  AND COALESCE(fr.training_eligible, 0) = 1
                  AND COALESCE(fr.parse_status, 'parsed') = 'parsed'
                  AND (fr.source LIKE 'openmeteo_%' OR fr.source = 'weathercom_v3_forecast')
                ORDER BY fr.retrieved_at DESC, fr.id DESC, fm.member_id
                """,
                (profile.city, target_date),
            ).fetchall()
        ]
    latest_by_source: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "")
        latest_by_source.setdefault(source, int(row.get("run_id") or 0))
    return [row for row in rows if latest_by_source.get(str(row.get("source") or "")) == int(row.get("run_id") or 0)]


def _components_from_rows(
    profile: CitySettlementProfile,
    rows: list[dict[str, Any]],
    bias_table: list[dict[str, Any]],
    path: Path | None,
    *,
    target_date: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        family = model_family(row.get("source") or row.get("model") or "")
        grouped.setdefault(family, []).append(row)
    weights = region_model_weights(profile)
    components: list[dict[str, Any]] = []
    for family, group in grouped.items():
        if family not in weights:
            continue
        best_source = _best_source_group(group)
        if not best_source:
            continue
        first = best_source[0]
        unit = str(first.get("unit") or profile.unit or "C").upper()
        bias_c, sample_count = _bias_for(bias_table, profile.station_id, family)
        bias_unit = convert_temperature_delta(bias_c, "C", unit)
        highs_unit = [_first_number(row.get("high_temp")) for row in best_source]
        highs_c = [
            convert_temperature(float(value), unit, "C")
            for value in highs_unit
            if value is not None and math.isfinite(float(value))
        ]
        adjusted_c = [
            convert_temperature(float(value) - bias_unit, unit, "C")
            for value in highs_unit
            if value is not None and math.isfinite(float(value))
        ]
        if not adjusted_c:
            continue
        peak_hour = _peak_hour_from_members(best_source, unit, bias_unit, profile.timezone)
        components.append({
            "source": str(first.get("source") or ""),
            "family": family,
            "role": _component_role(family),
            "run_id": int(first.get("run_id") or 0),
            "model": str(first.get("model") or ""),
            "weight_raw": float(weights.get(family) or 0.0),
            "weight": 0.0,
            "weight_prior": float(weights.get(family) or 0.0),
            "weight_after_mae": 0.0,
            "member_count": len(adjusted_c),
            "raw_daily_highs_c": [round(value, 4) for value in highs_c],
            "adjusted_daily_highs_c": [round(value, 4) for value in adjusted_c],
            "model_daily_high_c": round(sum(adjusted_c) / len(adjusted_c), 4),
            "bias_correction_c": round(bias_c, 4),
            "bias_sample_count": int(sample_count),
            "mae_7d": _mae_for(bias_table, profile.station_id, family),
            "truth_basis": _truth_basis(profile, target_date, path),
            "retrieved_at": str(first.get("retrieved_at") or ""),
            "peak_hour": peak_hour.get("peak_hour") or "",
            "peak_temp_c": peak_hour.get("peak_temp_c"),
        })
    if _deb_algo() == POLYWX_ALIGNED_ALGO:
        _apply_mae_adjusted_weights(components)
        return components
    raw_sum = sum(component["weight_raw"] for component in components) or 1.0
    for component in components:
        component["weight"] = component["weight_raw"] / raw_sum
        component["weight_after_mae"] = component["weight"]
    return components


def _best_source_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_source.setdefault(str(row.get("source") or ""), []).append(row)
    return max(
        by_source.values(),
        key=lambda group: (len(group), str(group[0].get("retrieved_at") or ""), int(group[0].get("run_id") or 0)),
        default=[],
    )


def _weighted_member_highs(profile: CitySettlementProfile, components: list[dict[str, Any]]) -> list[tuple[float, float, dict[str, Any]]]:
    weighted: list[tuple[float, float, dict[str, Any]]] = []
    for component in components:
        highs = component.get("adjusted_daily_highs_c") or []
        count = len(highs) or 1
        member_weight = float(component.get("weight") or 0.0) / count
        for index, value in enumerate(highs):
            weighted.append((float(value), member_weight, {
                "source": component.get("source"),
                "family": component.get("family"),
                "member": f"{component.get('source')}:{index}",
            }))
    return weighted


def _weighted_peak_hour(components: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        component for component in components
        if component.get("peak_hour") and component.get("peak_temp_c") is not None
    ]
    if not candidates:
        return {}
    best = max(candidates, key=lambda row: (float(row.get("peak_temp_c") or -999), str(row.get("peak_hour") or "")))
    return {"peak_hour": best.get("peak_hour"), "peak_temp_c": best.get("peak_temp_c")}


def _peak_hour_from_members(rows: list[dict[str, Any]], unit: str, bias_unit: float, tz: str) -> dict[str, Any]:
    zone = _zone(tz)
    best: dict[str, Any] = {}
    for row in rows:
        hourly = _loads(row.get("hourly_json"), [])
        for point in hourly:
            if not isinstance(point, dict):
                continue
            temp = _first_number(point.get("temperature_2m"), point.get("temp"))
            valid = _parse_time(str(point.get("valid_at") or ""))
            if temp is None or valid is None:
                continue
            temp_c = convert_temperature(float(temp) - bias_unit, unit, "C")
            local_hour = valid.astimezone(zone).strftime("%H:00")
            if not best or temp_c > float(best.get("peak_temp_c") or -999) or (
                abs(temp_c - float(best.get("peak_temp_c") or -999)) < 1e-9 and local_hour > str(best.get("peak_hour") or "")
            ):
                best = {"peak_hour": local_hour, "peak_temp_c": temp_c}
    return best


def _bias_for(bias_table: list[dict[str, Any]], station_id: str, family: str) -> tuple[float, int]:
    station = str(station_id or "").upper()
    fam = str(family or "").lower()
    for row in bias_table:
        if str(row.get("icao") or row.get("station_id") or "").upper() == station and str(row.get("model") or "").lower() == fam:
            sample_count = int(row.get("sample_count") or 0)
            if sample_count < BIAS_MIN_SAMPLE_COUNT:
                return 0.0, sample_count
            return float(row.get("additive_bias_c") or 0.0), sample_count
    return 0.0, 0


def _deb_algo() -> str:
    mode = env_value("DEB_WEIGHT_MODE", "ensemble").strip().lower()
    return POLYWX_ALIGNED_ALGO if mode in {"polywx", "polywx_aligned", "polywx_aligned_deb_v1"} else ALGO


def _component_role(family: str) -> str:
    if family == "weathercom_v3":
        return "weather.com/WU-style v3 forecast"
    if family in {"gfs", "ecmwf", "icon", "gem", "jma", "cma", "hrrr", "nbm"}:
        return "NWP forecast"
    return "forecast"


def _mae_for(bias_table: list[dict[str, Any]], station_id: str, family: str) -> float | None:
    station = str(station_id or "").upper()
    fam = str(family or "").lower()
    for row in bias_table:
        if str(row.get("icao") or row.get("station_id") or "").upper() != station:
            continue
        if str(row.get("model") or "").lower() != fam:
            continue
        for key in ("mae_c", "mae", "rmse_c", "rmse"):
            value = _first_number(row.get(key))
            if value is not None:
                return round(float(value), 4)
        bias = _first_number(row.get("additive_bias_c"))
        if bias is not None:
            return round(abs(float(bias)), 4)
    return None


def _truth_basis(profile: CitySettlementProfile, target_date: str, path: Path | None) -> str:
    with connect(path) as conn:
        wu = conn.execute(
            "SELECT COUNT(*) FROM truth_wunderground_daily WHERE UPPER(icao) = ? AND date_local <= ? AND high_c IS NOT NULL",
            (str(profile.station_id or "").upper(), target_date),
        ).fetchone()[0]
        if int(wu or 0) > 0:
            return "wunderground_daily"
        if profile.city == "hong-kong":
            hko = conn.execute(
                "SELECT COUNT(*) FROM truth_hko_daily WHERE date_local <= ? AND high_c IS NOT NULL",
                (target_date,),
            ).fetchone()[0]
            if int(hko or 0) > 0:
                return "hong_kong_observatory_daily_extract"
        iem = conn.execute(
            "SELECT COUNT(*) FROM truth_iem_daily WHERE UPPER(icao) = ? AND date_local <= ? AND high_c IS NOT NULL",
            (str(profile.station_id or "").upper(), target_date),
        ).fetchone()[0]
        if int(iem or 0) > 0:
            return "iem_asos_approximation"
    return "none"


def _apply_mae_adjusted_weights(components: list[dict[str, Any]]) -> None:
    scored: list[tuple[dict[str, Any], float]] = []
    for component in components:
        prior = max(0.0, float(component.get("weight_prior") or component.get("weight_raw") or 0.0))
        mae = _first_number(component.get("mae_7d"))
        quality = 1.0 if mae is None else 1.0 / max(float(mae), 0.05)
        scored.append((component, prior * quality))
    total = sum(score for _component, score in scored) or 1.0
    for component, score in scored:
        weight = score / total
        component["weight_raw"] = score
        component["weight"] = weight
        component["weight_after_mae"] = weight


def _source_warnings(components: list[dict[str, Any]], algo: str) -> list[str]:
    warnings: list[str] = []
    if algo == POLYWX_ALIGNED_ALGO and not any(component.get("family") == "weathercom_v3" for component in components):
        warnings.append("missing_weathercom_v3")
    if not any(component.get("truth_basis") in {"wunderground_daily", "hong_kong_observatory_daily_extract"} for component in components):
        warnings.append("truth_basis_uses_approximation_or_none")
    return warnings


def _weighted_samples(samples: list[Any]) -> list[tuple[float, float]]:
    weighted: list[tuple[float, float]] = []
    for sample in samples:
        if isinstance(sample, dict):
            value = _first_number(sample.get("value"), sample.get("daily_max_c"), sample.get("daily_max"))
            weight = _first_number(sample.get("weight")) or 1.0
        else:
            value = _first_number(sample)
            weight = 1.0
        if value is None or not math.isfinite(float(value)):
            continue
        weighted.append((float(value), max(0.0, float(weight))))
    return weighted


def _sample_in_bucket(value: float, low: float | None, high: float | None) -> bool:
    if low is None and high is None:
        return False
    if low is None:
        return value < float(high)
    if high is None:
        return value >= float(low)
    return value >= float(low) and value < float(high)


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total = sum(weights) or 1.0
    return sum(value * weight for value, weight in zip(values, weights)) / total


def _weighted_std(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    mean = _weighted_mean(values, weights)
    total = sum(weights) or 1.0
    variance = sum(weight * (value - mean) ** 2 for value, weight in zip(values, weights)) / total
    return math.sqrt(max(0.0, variance))


def _weighted_spread(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def _market_probability(bucket: dict[str, Any]) -> float | None:
    bid = _first_number(bucket.get("best_bid"))
    ask = _first_number(bucket.get("best_ask"))
    price = _first_number(bucket.get("price"))
    if ask is not None:
        return ask
    if price is not None:
        return price
    if bid is not None:
        return bid
    return None


def _parse_time(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        value = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _loads(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return default
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _first_number(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None or value == "":
                continue
            parsed = float(value)
            if math.isfinite(parsed):
                return parsed
        except Exception:
            continue
    return None
