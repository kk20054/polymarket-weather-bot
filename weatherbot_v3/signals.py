from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .db import (
    connect,
    init_v3_db,
    list_daily_max_predictions,
    list_signal_decisions,
    upsert_signal_decision_record,
)
from .deb import bucket_probabilities
from .forecasts.ensemble import distribution_for_prediction as ensemble_distribution_for_prediction
from .stations import get_station
from .strategies import LadderGridStrategy, SingleBucketEVStrategy, TailBuyingStrategy
from .strategy_profiles import ensure_default_strategy_profile, profile_snapshot


DECISION_VERSION = "signal-decision-v3"
SINGLE_BUCKET_ID_VERSION = "signal-decision-v3"
MIN_EDGE = 0.05
MAX_SPREAD_BPS = 500.0
STALE_BOOK_SECONDS = 300.0
MIN_BIAS_SAMPLE_DAYS = 7
LOW_PRICE_TAIL_ASK = 0.05


def build_signal_decisions(
    city_key: str,
    target_date: str,
    *,
    issued_at_hour: str | None = None,
    dry_run: bool = False,
    limit: int = 200,
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    city = str(city_key or "").strip().lower()
    date = str(target_date or "").strip()
    if not city or not date:
        return {"ok": False, "reasons": ["city_or_target_date_missing"], "decisions": []}

    predictions = list_daily_max_predictions(city_key=city, target_date=date, limit=10, path=path)
    prediction = _select_prediction(predictions, issued_at_hour)
    if not prediction:
        return {"ok": False, "city_key": city, "target_date": date, "reasons": ["missing_daily_max_prediction"], "decisions": []}
    buckets = _list_market_buckets(city, date, limit=limit, path=path)
    if not buckets:
        return {
            "ok": False,
            "city_key": city,
            "target_date": date,
            "prediction": prediction,
            "reasons": ["missing_market_buckets"],
            "decisions": [],
        }

    distribution = ensemble_distribution_for_prediction(prediction, buckets)
    if not distribution:
        distribution = bucket_probabilities(
            float(prediction["mu"]),
            float(prediction["sigma"]),
            buckets,
            unit=str(prediction.get("unit") or "C"),
            sigma_floor=_optional_float(prediction.get("sigma_floor")),
            normalize=True,
        )
    probabilities = {
        str(item.get("bucket_key") or ""): item
        for item in distribution.get("items") or []
        if item.get("bucket_key")
    }
    evidence = _evidence_links(city, date, prediction, buckets, path)
    station_live_reasons = _station_live_gate_reasons(city, path)
    cfg = load_config()
    profile = ensure_default_strategy_profile("signal_generation", path=path)
    profile_parameters = profile["parameters"]
    decision_policy = profile_parameters["decision_policy"]
    sizing_policy = profile_parameters["sizing"]
    strategy_parameters = profile_parameters["strategies"]
    context = {
        "decision_version": DECISION_VERSION,
        "single_bucket_id_version": SINGLE_BUCKET_ID_VERSION,
        "distribution": distribution,
        "evidence": evidence,
        "station_live_reasons": station_live_reasons,
        "forecast_algo": prediction.get("forecast_algo") or prediction.get("method") or prediction.get("deb_version"),
        "max_spread_bps": decision_policy.get("max_spread_bps", MAX_SPREAD_BPS),
        "stale_book_seconds": decision_policy.get("stale_book_seconds", STALE_BOOK_SECONDS),
        "min_bias_sample_days": decision_policy.get("min_bias_sample_days", MIN_BIAS_SAMPLE_DAYS),
        "low_price_tail_ask": decision_policy.get("low_price_tail_ask", 0.05),
        "bankroll": getattr(cfg, "bankroll_usd", getattr(cfg, "max_bet", 0.0)),
        "kelly_multiplier": sizing_policy.get("kelly_multiplier", getattr(cfg, "kelly_multiplier", 0.15)),
        "bankroll_fraction_cap": sizing_policy.get("max_bankroll_fraction_per_trade", 0.05),
        "max_per_trade_usd": getattr(cfg, "max_per_trade_usd", getattr(cfg, "max_bet", 0.0)),
        "independent_settlement_days": _independent_settlement_days(city, path, prediction),
        "strategy_revision_id": profile["revision_id"],
        "strategy_params_hash": profile["content_sha256"],
        "strategy_params_snapshot": profile_snapshot(profile),
    }
    strategy_builders = (
        ("single_bucket_ev", SingleBucketEVStrategy),
        ("ladder_grid", LadderGridStrategy),
        ("tail_buying", TailBuyingStrategy),
    )
    strategies = [
        builder(strategy_parameters[name])
        for name, builder in strategy_builders
        if strategy_parameters.get(name, {}).get("enabled", True)
    ]
    decisions: list[dict[str, Any]] = []
    for strategy in strategies:
        decisions.extend(strategy.evaluate_many(buckets, probabilities, prediction, context))
    stored = 0
    if not dry_run:
        for decision in decisions:
            upsert_signal_decision_record(decision, path=path)
            stored += 1
    status_counts: dict[str, int] = {}
    paper_counts: dict[str, int] = {}
    live_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}
    for decision in decisions:
        status_counts[decision["gate_status"]] = status_counts.get(decision["gate_status"], 0) + 1
        paper_counts[decision["paper_decision"]] = paper_counts.get(decision["paper_decision"], 0) + 1
        live_counts[decision["live_decision"]] = live_counts.get(decision["live_decision"], 0) + 1
        strategy = str(decision.get("strategy_name") or "single_bucket_ev")
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
    return {
        "ok": True,
        "dry_run": dry_run,
        "city_key": city,
        "target_date": date,
        "prediction_id": prediction.get("id"),
        "deb_version": prediction.get("deb_version") or prediction.get("method"),
        "forecast_algo": prediction.get("forecast_algo") or prediction.get("method") or prediction.get("deb_version"),
        "bucket_count": len(buckets),
        "decision_count": len(decisions),
        "stored": stored,
        "status_counts": status_counts,
        "paper_counts": paper_counts,
        "live_counts": live_counts,
        "strategy_counts": strategy_counts,
        "strategy_revision_id": profile["revision_id"],
        "strategy_params_hash": profile["content_sha256"],
        "model_distribution": _distribution_summary(distribution),
        "decisions": decisions,
    }


def build_signal_decisions_for_targets(
    targets: list[tuple[str, str]],
    *,
    dry_run: bool = False,
    limit: int = 200,
    path: Path | None = None,
) -> dict[str, Any]:
    results = [
        build_signal_decisions(city, date, dry_run=dry_run, limit=limit, path=path)
        for city, date in targets
    ]
    return {
        "ok": all(result.get("ok") for result in results),
        "dry_run": dry_run,
        "requested": len(targets),
        "stored": sum(int(result.get("stored") or 0) for result in results),
        "decision_count": sum(int(result.get("decision_count") or 0) for result in results),
        "failed": sum(1 for result in results if not result.get("ok")),
        "results": results,
    }


def signal_decisions_summary(
    city_key: str | None = None,
    target_date: str | None = None,
    *,
    limit: int = 100,
    path: Path | None = None,
) -> dict[str, Any]:
    rows = list_signal_decisions(city_key=city_key, target_date=target_date, limit=limit, path=path)
    bucket_keys = [str(row.get("bucket_key") or "") for row in rows if row.get("bucket_key")]
    event_url_by_bucket: dict[str, str] = {}
    if bucket_keys:
        placeholders = ",".join("?" for _ in bucket_keys)
        with connect(path) as conn:
            event_url_by_bucket = {
                str(row["bucket_key"]): str(row["event_url"] or "")
                for row in conn.execute(
                    f"SELECT bucket_key, event_url FROM market_buckets WHERE bucket_key IN ({placeholders})",
                    bucket_keys,
                ).fetchall()
            }
    status_counts: dict[str, int] = {}
    paper_counts: dict[str, int] = {}
    live_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for row in rows:
        evidence_links = dict(row.get("evidence_links") or {})
        if not evidence_links.get("event_url"):
            evidence_links["event_url"] = event_url_by_bucket.get(str(row.get("bucket_key") or ""), "")
        row["evidence_links"] = evidence_links
        status_counts[str(row.get("gate_status") or "")] = status_counts.get(str(row.get("gate_status") or ""), 0) + 1
        paper_counts[str(row.get("paper_decision") or "")] = paper_counts.get(str(row.get("paper_decision") or ""), 0) + 1
        live_counts[str(row.get("live_decision") or "")] = live_counts.get(str(row.get("live_decision") or ""), 0) + 1
        strategy = str(row.get("strategy_name") or "single_bucket_ev")
        strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
        for reason in row.get("gate_reasons") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
    return {
        "ok": True,
        "city_key": city_key or "",
        "target_date": target_date or "",
        "count": len(rows),
        "status_counts": status_counts,
        "paper_counts": paper_counts,
        "live_counts": live_counts,
        "strategy_counts": strategy_counts,
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "decisions": rows,
    }


def _station_live_gate_reasons(city_key: str, path: Path | None = None) -> list[str]:
    row = get_station(city_key, path)
    if not row:
        return ["station_row_missing"]
    reasons: list[str] = []
    if not str(row.get("settlement_rule_text") or "").strip():
        reasons.append("settlement_rule_text_missing")
    if str(row.get("verification_status") or "") != "verified":
        reasons.append("settlement_rule_unverified")
    settlement_station = str(row.get("settlement_station_id") or row.get("station_id") or "").upper()
    observation_station = str(row.get("station_id") or "").upper()
    if settlement_station and observation_station and settlement_station != observation_station:
        reasons.append("settlement_mismatch")
    return _unique(reasons)


def _independent_settlement_days(city_key: str, path: Path | None, prediction: dict[str, Any]) -> int:
    city = str(city_key or "").strip().lower()
    counts: list[int] = []
    try:
        with connect(path) as conn:
            counts.append(int(conn.execute(
                """
                SELECT COUNT(DISTINCT target_date)
                FROM truth_observations
                WHERE city = ?
                  AND COALESCE(calibration_eligible, 0) = 1
                  AND actual_temp IS NOT NULL
                """,
                (city,),
            ).fetchone()[0] or 0))
            counts.append(int(conn.execute(
                """
                SELECT COUNT(DISTINCT target_date)
                FROM settlements
                WHERE city = ?
                  AND actual_temp IS NOT NULL
                """,
                (city,),
            ).fetchone()[0] or 0))
    except Exception:
        pass
    counts.append(int(prediction.get("bias_sample_count") or 0))
    return max(counts or [0])


def _list_market_buckets(city: str, target_date: str, *, limit: int, path: Path | None) -> list[dict[str, Any]]:
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM market_buckets
                WHERE city = ? AND target_date = ?
                ORDER BY bucket_low, bucket_high, id
                LIMIT ?
                """,
                (city, target_date, max(1, min(int(limit or 200), 1000))),
            ).fetchall()
        ]
    for row in rows:
        row["neg_risk"] = bool(row.get("neg_risk"))
        row["enable_order_book"] = bool(row.get("enable_order_book"))
        row["strict_match_reasons"] = _loads(row.get("strict_match_reasons"), [])
    return rows


def _select_prediction(predictions: list[dict[str, Any]], issued_at_hour: str | None) -> dict[str, Any] | None:
    if not predictions:
        return None
    if not issued_at_hour:
        return predictions[0]
    target = _hour_key(issued_at_hour)
    for prediction in predictions:
        if _hour_key(prediction.get("issued_at")) == target:
            return prediction
    return predictions[0]


def _evidence_links(
    city: str,
    target_date: str,
    prediction: dict[str, Any],
    buckets: list[dict[str, Any]],
    path: Path | None,
) -> dict[str, Any]:
    with connect(path) as conn:
        hourly_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM hourly_consensus WHERE city = ? AND target_date = ? ORDER BY local_hour LIMIT 24",
                (city, target_date),
            ).fetchall()
        ]
        metar_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM metar_reports WHERE city = ? ORDER BY report_time DESC LIMIT 24",
                (city,),
            ).fetchall()
        ]
    return {
        "event_url": next((str(row.get("event_url") or "") for row in buckets if row.get("event_url")), ""),
        "daily_max_prediction_id": prediction.get("id"),
        "source_run_ids": prediction.get("source_run_ids") or [],
        "hourly_consensus_ids": hourly_ids,
        "metar_report_ids": metar_ids,
        "market_bucket_ids": [row.get("id") for row in buckets],
    }


def _decision_id(bucket: dict[str, Any], prediction: dict[str, Any]) -> str:
    key = "|".join([
        str(bucket.get("city") or prediction.get("city_key") or ""),
        str(bucket.get("target_date") or prediction.get("target_date") or ""),
        str(bucket.get("yes_token_id") or bucket.get("token_id") or ""),
        _hour_key(prediction.get("issued_at")),
        DECISION_VERSION,
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _distribution_summary(distribution: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": distribution.get("method"),
        "mu": distribution.get("mu"),
        "sigma": distribution.get("sigma"),
        "unit": distribution.get("unit"),
        "sum_probability": distribution.get("sum_probability"),
        "normalized": distribution.get("normalized"),
        "item_count": len(distribution.get("items") or []),
    }


def _spread_bps(spread: float | None, ask: float | None, bid: float | None) -> float | None:
    if spread is None and ask is not None and bid is not None:
        spread = ask - bid
    if spread is None or ask is None or ask <= 0:
        return None
    return max(0.0, float(spread) / float(ask) * 10_000.0)


def _book_age_seconds(value: Any) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hour_key(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return str(value or "")
    return parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()


def _is_low_price_tail(bucket: dict[str, Any], ask: float | None) -> bool:
    direction = str(bucket.get("bucket_direction") or "").lower()
    if direction not in {"or_above", "or_below", "above", "below", "under", "over", "at_or_above", "at_or_below"}:
        return False
    return ask is not None and ask < LOW_PRICE_TAIL_ASK


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return _loads(value, []) if value else []


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _unique(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
