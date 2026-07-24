from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weatherbot_v3.bias import train_bias_table
from weatherbot_v3.config import load_config
from weatherbot_v3.deb import bucket_probabilities, build_daily_max_prediction, probability_mu_for_prediction
from weatherbot_v3.paper_settlement import bucket_contains_celsius
from weatherbot_v3.orderbook_replay import (
    executable_ask_shares,
    select_orderbook_as_of,
    snapshot_from_row,
)
from weatherbot_v3.registry import get_city_profile
from weatherbot_v3.strategies import CoreModalStrategy
from weatherbot_v3.strategy_profiles import core_modal_v1_parameters


CHECKPOINTS = {
    "d1_20": (-1, 20),
    "d0_08": (0, 8),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Leakage-safe replay of the dynamic core-modal paper strategy."
    )
    parser.add_argument("--cities", nargs="+", default=["shanghai", "chicago", "tokyo"])
    parser.add_argument("--start", required=True, help="First settlement date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Last settlement date, YYYY-MM-DD")
    parser.add_argument("--checkpoints", nargs="+", choices=sorted(CHECKPOINTS), default=list(CHECKPOINTS))
    parser.add_argument("--db", default=str(ROOT / "data" / "weatherbot_v3.db"))
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).resolve()
    cities = list(dict.fromkeys(str(value).strip().lower() for value in args.cities if str(value).strip()))
    dates = list(_date_range(args.start, args.end))
    rows: list[dict[str, Any]] = []
    bias_audits: list[dict[str, Any]] = []
    for target_date in dates:
        bias = train_bias_table(
            cities=cities,
            days=90,
            path=db_path,
            as_of_date_exclusive=target_date,
            persist=False,
        )
        bias_audits.append({
            "target_date": target_date,
            "row_count": int(bias.get("row_count") or 0),
            "runtime_eligible_rows": int(bias.get("runtime_eligible_rows") or 0),
        })
        for city in cities:
            for checkpoint in args.checkpoints:
                rows.append(
                    replay_case(
                        city,
                        target_date,
                        checkpoint,
                        bias_rows=list(bias.get("rows") or []),
                        db_path=db_path,
                    )
                )

    payload = {
        "version": "core-modal-leakage-safe-replay-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "cities": cities,
        "date_range": {"start": args.start, "end": args.end},
        "checkpoints": list(args.checkpoints),
        "contracts": {
            "forecast": "Only forecast runs available by the checkpoint are eligible.",
            "calibration": "Bias/MAE uses truth dates strictly before each target date.",
            "market": "Latest persisted orderbook at or before the checkpoint; quote age <= 300s.",
            "fill": "BUY YES at best ask, full fill only when displayed ask depth and orderMinSize permit.",
            "truth": "WU daily, or HKO Daily Extract for Hong Kong; IEM approximation is not scored.",
            "fees": "Polymarket fee assumed zero for current weather markets; no optimistic mid-price fill.",
        },
        "bias_audits": bias_audits,
        "summary": summarize(rows),
        "rows": rows,
    }
    output = Path(args.output) if args.output else ROOT / "audits" / f"core-modal-backtest-{date.today().isoformat()}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = output.with_suffix(".md")
    markdown.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({"output": str(output), "markdown": str(markdown), **payload["summary"]}, ensure_ascii=False))
    return 0


def replay_case(
    city: str,
    target_date: str,
    checkpoint: str,
    *,
    bias_rows: list[dict[str, Any]],
    db_path: Path,
) -> dict[str, Any]:
    profile = get_city_profile(city)
    if profile is None:
        return _failed(city, target_date, checkpoint, "city_profile_missing")
    decision_time = _checkpoint_utc(target_date, profile.timezone, checkpoint)
    truth = _exact_truth(city, profile.station_id, target_date, db_path)
    if truth is None:
        return _failed(city, target_date, checkpoint, "exact_truth_missing", decision_time)

    prediction = build_daily_max_prediction(
        city,
        target_date,
        issued_at=decision_time,
        path=db_path,
        bias_table=bias_rows,
    )
    if not prediction.get("ok"):
        return _failed(
            city,
            target_date,
            checkpoint,
            "prediction_failed",
            decision_time,
            details=list(prediction.get("reasons") or []),
        )

    buckets = _market_buckets_with_books(city, target_date, decision_time, db_path)
    if not buckets:
        return _failed(city, target_date, checkpoint, "market_buckets_missing", decision_time)
    probability_mu, probability_basis = probability_mu_for_prediction(prediction)
    distribution = bucket_probabilities(
        probability_mu,
        float(prediction["sigma"]),
        buckets,
        unit=str(prediction.get("unit") or "C"),
        sigma_floor=_number(prediction.get("sigma_floor")),
        observed_floor=_number(prediction.get("observed_floor")),
        normalize=True,
    )
    distribution["probability_mu_basis"] = probability_basis
    probabilities = {
        str(item.get("bucket_key") or ""): item
        for item in distribution.get("items") or []
        if item.get("bucket_key")
    }

    params = core_modal_v1_parameters()
    strategy_params = params["strategies"]["core_modal_v1"]
    settlement_days = _authoritative_days_before(city, profile.station_id, target_date, db_path)
    context = {
        "decision_time": decision_time,
        "decision_version": "core-modal-leakage-safe-replay-v1",
        "distribution": distribution,
        "evidence": {},
        "station_live_reasons": [],
        "max_spread_bps": params["decision_policy"]["max_spread_bps"],
        "stale_book_seconds": params["decision_policy"]["stale_book_seconds"],
        "min_bias_sample_days": params["decision_policy"]["min_bias_sample_days"],
        "low_price_tail_ask": params["decision_policy"]["low_price_tail_ask"],
        "bankroll": 40.0,
        "kelly_multiplier": params["sizing"]["kelly_multiplier"],
        "bankroll_fraction_cap": params["sizing"]["max_bankroll_fraction_per_trade"],
        "max_per_trade_usd": 2.0,
        "independent_settlement_days": settlement_days,
        "independent_settlement_basis": truth["provider"],
        "independent_settlement_authoritative": True,
        "strategy_revision_id": "historical-replay",
    }
    decisions = CoreModalStrategy(strategy_params).evaluate_many(buckets, probabilities, prediction, context)
    if not decisions:
        return _failed(city, target_date, checkpoint, "strategy_returned_no_decision", decision_time)
    decision = decisions[0]
    execution_reasons = _executor_reasons(decision)
    executable = bool(decision.get("paper_allowed")) and not execution_reasons
    chosen = next((row for row in buckets if row.get("bucket_key") == decision.get("bucket_key")), {})
    outcome_yes = bucket_contains_celsius(float(truth["actual_c"]), chosen) if chosen else False
    amount = float(decision.get("position_size_usd") or 0.0) if executable else 0.0
    ask = float(decision.get("market_ask") or 0.0)
    shares = amount / ask if amount > 0 and ask > 0 else 0.0
    pnl = shares * (1.0 if outcome_yes else 0.0) - amount
    all_outcomes = {
        str(bucket.get("bucket_key") or ""): 1.0 if bucket_contains_celsius(float(truth["actual_c"]), bucket) else 0.0
        for bucket in buckets
    }
    brier = sum(
        (float(item.get("probability") or 0.0) - all_outcomes.get(str(item.get("bucket_key") or ""), 0.0)) ** 2
        for item in distribution.get("items") or []
    )
    ranked = sorted(
        distribution.get("items") or [],
        key=lambda item: -float(item.get("probability") or 0.0),
    )
    actual_key = next((key for key, value in all_outcomes.items() if value == 1.0), "")
    return {
        "ok": True,
        "city": city,
        "target_date": target_date,
        "checkpoint": checkpoint,
        "decision_time": decision_time,
        "truth": truth,
        "prediction": {
            "mu": prediction.get("mu"),
            "sigma": prediction.get("sigma"),
            "unit": prediction.get("unit"),
            "model_families": [
                component.get("family")
                for component in prediction.get("components") or []
                if float(component.get("weight") or 0.0) > 0
            ],
            "warnings": list(prediction.get("build_warnings") or []),
        },
        "calibration": {
            "actual_bucket_key": actual_key,
            "top1_correct": bool(ranked and ranked[0].get("bucket_key") == actual_key),
            "top2_correct": actual_key in {str(item.get("bucket_key") or "") for item in ranked[:2]},
            "multiclass_brier": round(brier, 8),
        },
        "decision": {
            "bucket_key": decision.get("bucket_key"),
            "model_rank": (decision.get("core_modal") or {}).get("model_rank"),
            "model_probability": decision.get("model_probability"),
            "market_ask": decision.get("market_ask"),
            "market_bid": decision.get("market_bid"),
            "effective_edge": ((decision.get("core_modal") or {}).get("effective_edge")),
            "position_size_usd": decision.get("position_size_usd"),
            "paper_allowed": bool(decision.get("paper_allowed")),
            "gate_reasons": list(decision.get("gate_reasons") or []),
            "executor_reasons": execution_reasons,
            "executable": executable,
            "outcome_yes": bool(outcome_yes),
            "pnl": round(pnl, 4),
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row.get("ok")]
    trades = [row for row in valid if row["decision"]["executable"]]
    failures = Counter(str(row.get("reason") or "") for row in rows if not row.get("ok"))
    gates = Counter(
        reason
        for row in valid
        for reason in row["decision"].get("gate_reasons") or []
    )
    by_checkpoint: dict[str, dict[str, Any]] = {}
    for checkpoint in sorted({str(row.get("checkpoint") or "") for row in rows}):
        subset = [row for row in valid if row.get("checkpoint") == checkpoint]
        subset_trades = [row for row in subset if row["decision"]["executable"]]
        by_checkpoint[checkpoint] = _metric_block(subset, subset_trades)
    by_city: dict[str, dict[str, Any]] = {}
    for city in sorted({str(row.get("city") or "") for row in rows}):
        subset = [row for row in valid if row.get("city") == city]
        subset_trades = [row for row in subset if row["decision"]["executable"]]
        by_city[city] = _metric_block(subset, subset_trades)
    return {
        **_metric_block(valid, trades),
        "requested_cases": len(rows),
        "valid_cases": len(valid),
        "failed_cases": len(rows) - len(valid),
        "failure_reasons": dict(failures.most_common()),
        "gate_reasons_top10": dict(gates.most_common(10)),
        "by_checkpoint": by_checkpoint,
        "by_city": by_city,
    }


def _metric_block(cases: list[dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = sum(float(row["decision"]["pnl"]) for row in trades)
    cost = sum(float(row["decision"].get("position_size_usd") or 0.0) for row in trades)
    wins = sum(1 for row in trades if row["decision"]["outcome_yes"])
    return {
        "cases": len(cases),
        "top1_accuracy": round(sum(1 for row in cases if row["calibration"]["top1_correct"]) / len(cases), 6) if cases else None,
        "top2_accuracy": round(sum(1 for row in cases if row["calibration"]["top2_correct"]) / len(cases), 6) if cases else None,
        "mean_multiclass_brier": round(fmean(row["calibration"]["multiclass_brier"] for row in cases), 8) if cases else None,
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins / len(trades), 6) if trades else None,
        "cost_usd": round(cost, 4),
        "pnl_usd": round(pnl, 4),
        "roi": round(pnl / cost, 6) if cost else None,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Dynamic Core-Modal Historical Replay",
        "",
        "This is a leakage-safe research replay, not proof of future profitability.",
        "",
        "## Summary",
        "",
        f"- Cases: {summary['valid_cases']} / {summary['requested_cases']}",
        f"- Top-1 accuracy: {_pct(summary['top1_accuracy'])}",
        f"- Top-2 accuracy: {_pct(summary['top2_accuracy'])}",
        f"- Mean multiclass Brier: {summary['mean_multiclass_brier']}",
        f"- Executable trades: {summary['trades']}",
        f"- Win rate: {_pct(summary['win_rate'])}",
        f"- PnL / ROI: ${summary['pnl_usd']:.2f} / {_pct(summary['roi'])}",
        "",
        "## Checkpoints",
        "",
        "| checkpoint | cases | top1 | top2 | trades | W-L | PnL | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, item in summary["by_checkpoint"].items():
        lines.append(
            f"| {key} | {item['cases']} | {_pct(item['top1_accuracy'])} | {_pct(item['top2_accuracy'])} | "
            f"{item['trades']} | {item['wins']}-{item['losses']} | ${item['pnl_usd']:.2f} | {_pct(item['roi'])} |"
        )
    lines.extend(["", "## Gate Reasons", ""])
    for reason, count in summary["gate_reasons_top10"].items():
        lines.append(f"- `{reason}`: {count}")
    lines.extend(["", "## Contract", ""])
    for key, value in payload["contracts"].items():
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines) + "\n"


def _market_buckets_with_books(city: str, target_date: str, cutoff: str, db_path: Path) -> list[dict[str, Any]]:
    with _readonly(db_path) as conn:
        buckets = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM market_buckets WHERE city=? AND target_date=? ORDER BY bucket_low, bucket_high, id",
                (city, target_date),
            ).fetchall()
        ]
        for bucket in buckets:
            book = select_orderbook_as_of(
                conn,
                decision_time=cutoff,
                yes_token_id=str(bucket.get("yes_token_id") or ""),
                market_id=str(bucket.get("market_id") or ""),
            )
            if book is None:
                bucket.update({
                    "best_bid": None,
                    "best_ask": None,
                    "spread": None,
                    "quote_timestamp": "",
                    "bid_depth": None,
                    "ask_depth": None,
                    "best_bid_size": None,
                    "best_ask_size": None,
                    "bids": [],
                    "asks": [],
                    "orderbook_snapshot_key": "",
                    "order_min_size": None,
                    "tick_size": None,
                    "enable_order_book": False,
                })
                continue
            row = dict(book)
            snapshot = snapshot_from_row(row)
            bucket.update({
                "best_bid": row.get("best_bid"),
                "best_ask": row.get("best_ask"),
                "spread": row.get("spread"),
                "volume": row.get("volume"),
                "order_min_size": row.get("order_min_size") if _number(row.get("order_min_size")) else None,
                "tick_size": row.get("tick_size") if _number(row.get("tick_size")) else None,
                "enable_order_book": bool(row.get("enable_order_book")),
                "quote_timestamp": snapshot.get("quote_timestamp"),
                "bid_depth": row.get("bid_depth"),
                "ask_depth": row.get("ask_depth"),
                "best_bid_size": snapshot.get("best_bid_size"),
                "best_ask_size": snapshot.get("best_ask_size"),
                "bids": snapshot.get("bids") or [],
                "asks": snapshot.get("asks") or [],
                "depth_basis": snapshot.get("depth_basis"),
                "orderbook_snapshot_key": row.get("snapshot_key"),
                "orderbook_source": row.get("snapshot_type") or "stored_orderbook",
            })
    for bucket in buckets:
        bucket["enable_order_book"] = bool(bucket.get("enable_order_book"))
        bucket["neg_risk"] = bool(bucket.get("neg_risk"))
    return buckets


def _executor_reasons(decision: dict[str, Any]) -> list[str]:
    if not decision.get("paper_allowed"):
        return []
    cfg = load_config()
    reasons: list[str] = []
    ask = _number(decision.get("market_ask"))
    spread = _number((decision.get("orderbook_snapshot") or {}).get("spread"))
    snapshot = decision.get("orderbook_snapshot") or {}
    size = float(decision.get("position_size_usd") or 0.0)
    shares = size / ask if ask and ask > 0 else 0.0
    asks = snapshot.get("asks") or []
    depth = (
        executable_ask_shares(asks, ask)
        if asks and ask is not None
        else _number(snapshot.get("best_ask_size"))
    )
    minimum = _number(decision.get("order_min_size"))
    if ask is None or ask < cfg.min_price or ask > cfg.max_price:
        reasons.append("executor_price_outside_limits")
    if spread is None or spread > cfg.max_slippage + 1e-12:
        reasons.append("executor_spread_above_max_slippage")
    if depth is None or depth + 1e-9 < shares:
        reasons.append("executor_insufficient_ask_depth")
    if minimum is None or shares + 1e-9 < minimum:
        reasons.append("executor_below_order_min_size")
    return reasons


def _exact_truth(city: str, station_id: str, target_date: str, db_path: Path) -> dict[str, Any] | None:
    with _readonly(db_path) as conn:
        if city == "hong-kong":
            row = conn.execute(
                "SELECT high_c,source_url FROM truth_hko_daily WHERE date_local=? AND high_c IS NOT NULL",
                (target_date,),
            ).fetchone()
            if row:
                return {"actual_c": float(row["high_c"]), "provider": "hong_kong_observatory_daily_extract", "station": "HKO"}
        row = conn.execute(
            "SELECT high_c,source_url FROM truth_wunderground_daily WHERE UPPER(icao)=? AND date_local=? AND high_c IS NOT NULL",
            (str(station_id or "").upper(), target_date),
        ).fetchone()
        if row:
            return {"actual_c": float(row["high_c"]), "provider": "wunderground_daily", "station": station_id}
    return None


def _authoritative_days_before(city: str, station_id: str, target_date: str, db_path: Path) -> int:
    with _readonly(db_path) as conn:
        if city == "hong-kong":
            return int(conn.execute(
                "SELECT count(DISTINCT date_local) FROM truth_hko_daily WHERE date_local<? AND high_c IS NOT NULL",
                (target_date,),
            ).fetchone()[0])
        return int(conn.execute(
            "SELECT count(DISTINCT date_local) FROM truth_wunderground_daily WHERE UPPER(icao)=? AND date_local<? AND high_c IS NOT NULL",
            (str(station_id or "").upper(), target_date),
        ).fetchone()[0])


@contextmanager
def _readonly(path: Path):
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _checkpoint_utc(target_date: str, timezone_name: str, checkpoint: str) -> str:
    day_offset, hour = CHECKPOINTS[checkpoint]
    local_date = date.fromisoformat(target_date) + timedelta(days=day_offset)
    local = datetime.combine(local_date, time(hour=hour), ZoneInfo(timezone_name))
    return local.astimezone(timezone.utc).isoformat()


def _date_range(start: str, end: str):
    current = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    while current <= finish:
        yield current.isoformat()
        current += timedelta(days=1)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _failed(
    city: str,
    target_date: str,
    checkpoint: str,
    reason: str,
    decision_time: str = "",
    *,
    details: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "city": city,
        "target_date": target_date,
        "checkpoint": checkpoint,
        "decision_time": decision_time,
        "reason": reason,
        "details": details or [],
    }


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
