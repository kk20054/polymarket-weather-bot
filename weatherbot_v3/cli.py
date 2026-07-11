from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .db import bulk_settlement_contract_verification, connect, dashboard_summary, init_v3_db, list_settlement_contracts, set_settlement_contract_verification
from .migration import audit_market_files, migrate_legacy_signals, repair_truth_temporal_mismatches, sync_settlement_contracts
from .model_dataset import build_model_dataset_audit, is_settlement_pending
from .notifier import FeishuNotifier
from .qualification import build_data_readiness, persist_data_readiness
from .source_health import build_source_health_matrix
from .stations import list_stations, set_station_enabled, sync_station_registry
from .validation import build_production_validation_report


ORDERBOOK_TERMINAL_SIGNAL_STATUSES = (
    "closed",
    "settled",
    "resolved",
    "expired",
    "lost",
    "won",
    "cancelled",
    "canceled",
)


def default_orderbook_start_date(now_utc: datetime | None = None) -> str:
    now = now_utc or datetime.now(timezone.utc)
    return (now.date() - timedelta(days=1)).isoformat()


def select_orderbook_backfill_markets(
    conn,
    *,
    limit: int,
    start_date: str,
    end_date: str = "",
):
    bounded_limit = max(1, min(int(limit or 50), 500))
    placeholders = ",".join("?" for _ in ORDERBOOK_TERMINAL_SIGNAL_STATUSES)
    params = [
        start_date,
        end_date,
        end_date,
        *ORDERBOOK_TERMINAL_SIGNAL_STATUSES,
        bounded_limit,
    ]
    return conn.execute(
        f"""
        SELECT
            market_id,
            MAX(id) AS latest_id,
            MAX(target_date) AS latest_target_date,
            COUNT(*) AS signal_count
        FROM signals
        WHERE market_id IS NOT NULL
          AND market_id != ''
          AND target_date IS NOT NULL
          AND target_date != ''
          AND target_date >= ?
          AND (? = '' OR target_date <= ?)
          AND LOWER(COALESCE(status, '')) NOT IN ({placeholders})
        GROUP BY market_id
        ORDER BY latest_target_date DESC, latest_id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


def readiness_stage(readiness: dict, key: str) -> dict | None:
    return next((stage for stage in readiness.get("stages", []) if stage.get("key") == key), None)


def print_current_state() -> None:
    state_path = Path(__file__).resolve().parents[1] / "docs" / "CURRENT_STATE.md"
    print(state_path.read_text(encoding="utf-8"))


def run_forecast_backfill(cities_arg: str = "", days_arg: int = 4) -> dict:
    from bot_v2 import LOCATIONS, take_forecast_snapshot, target_dates_for_city

    requested = {item.strip() for item in cities_arg.split(",") if item.strip()}
    cities = [city for city in LOCATIONS if not requested or city in requested]
    unknown = sorted(requested - set(LOCATIONS))
    days = max(1, min(int(days_arg or 4), 7))
    results = []
    for city in cities:
        dates = target_dates_for_city(city, days)
        try:
            snapshots = take_forecast_snapshot(city, dates)
            results.append({
                "city": city,
                "dates": dates,
                "stored_dates": sum(1 for value in snapshots.values() if value.get("best") is not None),
                "ok": True,
            })
        except Exception as exc:
            results.append({"city": city, "dates": dates, "stored_dates": 0, "ok": False, "error": str(exc)})
        time.sleep(0.2)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    return {
        "cities": len(cities),
        "unknown_cities": unknown,
        "days": days,
        "ok": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
        "results": results,
        "forecast_stage": readiness_stage(readiness, "forecast_runs"),
    }


def run_hourly_consensus_build(
    cities_arg: str = "",
    target_date: str = "",
    *,
    days_arg: int | None = None,
    limit_cities: int = 5,
    force_rebuild: bool = False,
) -> dict:
    from .hourly import build_hourly_consensus

    cities = [item.strip() for item in cities_arg.split(",") if item.strip()]
    if not cities and limit_cities:
        cities = _default_layer_city_keys(limit_cities)
    if not target_date and days_arg:
        targets = _target_dates_from_db(cities, days_arg, forecast_only=False)
        target_map: dict[str, set[str]] = {}
        for city, date_value in targets:
            target_map.setdefault(city, set()).add(date_value)
        batch = build_hourly_consensus(cities or None, target_dates_by_city=target_map)
        payload = {
            "ok": bool(batch.get("ok")),
            "source": "forecast_members+metar_reports+mesonet_observations",
            "cities": cities,
            "target_date": "",
            "target_pairs": len(targets),
            "forecast_points": int(batch.get("forecast_points") or 0),
            "observation_points": int(batch.get("observation_points") or 0),
            "rows_built": int(batch.get("rows_built") or 0),
            "rows_upserted": int(batch.get("rows_upserted") or 0),
            "result": batch,
        }
    else:
        payload = build_hourly_consensus(cities or None, target_date=target_date or None)
    payload["force_rebuild"] = bool(force_rebuild)
    payload["days_requested"] = days_arg
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    payload["hourly_consensus_stage"] = next(
        (stage for stage in readiness.get("stages", []) if stage.get("key") == "hourly_consensus"),
        None,
    )
    return payload


def run_daily_max_build(
    cities_arg: str = "",
    target_date: str = "",
    *,
    days_arg: int | None = None,
    dry_run: bool = False,
    limit_cities: int = 5,
) -> dict:
    from .deb import build_daily_max_predictions

    cities = [item.strip() for item in cities_arg.split(",") if item.strip()]
    if not cities and limit_cities:
        cities = _default_layer_city_keys(limit_cities)
    results = []
    if not target_date and days_arg:
        targets = _target_dates_from_db(cities, days_arg, forecast_only=True)
        for city, date_value in targets:
            results.append(build_daily_max_predictions(
                city=city,
                target_date=date_value,
                limit=1,
                dry_run=dry_run,
            ))
    else:
        for city in cities or [None]:
            results.append(build_daily_max_predictions(
                city=city,
                target_date=target_date or None,
                limit=max(1, int(days_arg or 50)),
                dry_run=dry_run,
            ))
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    return {
        "ok": all(item.get("ok") for item in results),
        "dry_run": dry_run,
        "cities": cities,
        "target_date": target_date or "",
        "days_requested": days_arg,
        "requested": sum(int(item.get("requested") or 0) for item in results),
        "stored": sum(int(item.get("stored") or 0) for item in results),
        "failed": sum(int(item.get("failed") or 0) for item in results),
        "results": results,
    }


def run_model_timing_reprice(
    cities_arg: str = "",
    *,
    days_arg: int = 2,
    dry_run: bool = False,
) -> dict:
    from .forecasts.ensemble import build_model_reprice_events

    cities = [item.strip() for item in cities_arg.split(",") if item.strip()]
    payload = build_model_reprice_events(cities=cities or None, days=max(1, int(days_arg or 2)), dry_run=dry_run)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    payload["signal_decisions_stage"] = readiness_stage(readiness, "signal_decisions")
    return payload


def run_metar_backfill(
    cities_arg: str = "",
    *,
    days_arg: int = 30,
    dry_run: bool = False,
    probe_stations: bool = False,
    all_cities: bool = False,
    limit_cities: int = 5,
    output_path: str = "",
) -> dict:
    from .metar import backfill_iem_asos_metars, probe_iem_stations

    cities = _cities_from_arg(cities_arg)
    if all_cities:
        cities = []
        limit_cities = 10_000
    if probe_stations:
        return probe_iem_stations(
            cities or None,
            limit_cities=limit_cities,
            output_path=output_path or None,
        )
    payload = backfill_iem_asos_metars(
        cities or None,
        days=days_arg,
        dry_run=dry_run,
        limit_cities=limit_cities,
        probe_report_path=output_path or None,
    )
    if payload.get("ok"):
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["observations_stage"] = readiness_stage(readiness, "observations")
    return payload


def run_openmeteo_fetch(
    cities_arg: str = "",
    *,
    ensemble: bool = False,
    dry_run: bool = False,
    all_cities: bool = False,
    limit_cities: int = 5,
    forecast_days: int = 7,
) -> dict:
    from .openmeteo import fetch_openmeteo_forecasts

    cities = _cities_from_arg(cities_arg)
    if all_cities:
        cities = []
        limit_cities = 10_000
    payload = fetch_openmeteo_forecasts(
        cities or None,
        ensemble=ensemble,
        dry_run=dry_run,
        limit_cities=limit_cities,
        forecast_days=forecast_days,
    )
    if not dry_run and payload.get("runs_upserted", 0) > 0:
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["forecast_stage"] = readiness_stage(readiness, "forecast_runs")
    return payload


def run_weathercom_fetch(
    cities_arg: str = "",
    *,
    dry_run: bool = False,
    all_cities: bool = False,
    limit_cities: int = 5,
    forecast_days: int = 3,
) -> dict:
    from .weathercom import fetch_weathercom_forecasts

    cities = _cities_from_arg(cities_arg)
    if all_cities:
        cities = []
        limit_cities = 10_000
    payload = fetch_weathercom_forecasts(
        cities or None,
        dry_run=dry_run,
        limit_cities=limit_cities,
        forecast_days=forecast_days,
    )
    if not dry_run and payload.get("runs_upserted", 0) > 0:
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["forecast_stage"] = readiness_stage(readiness, "forecast_runs")
    return payload


def run_openmeteo_previous_runs(
    cities_arg: str = "",
    *,
    target_date: str = "",
    start_date: str = "",
    end_date: str = "",
    days_arg: int | None = None,
    previous_days_arg: str = "",
    models_arg: str = "",
    dry_run: bool = False,
    all_cities: bool = False,
    limit_cities: int = 5,
) -> dict:
    from .openmeteo import fetch_openmeteo_previous_runs

    cities = _cities_from_arg(cities_arg)
    if all_cities:
        cities = []
        limit_cities = 10_000
    target_dates = _cli_date_window(
        target_date=target_date,
        start_date=start_date,
        end_date=end_date,
        days=days_arg or 1,
    )
    previous_days = [
        int(item)
        for item in str(previous_days_arg or "1,2,3").split(",")
        if str(item).strip().isdigit()
    ]
    models = [item.strip() for item in str(models_arg or "").split(",") if item.strip()]
    payload = fetch_openmeteo_previous_runs(
        cities or None,
        target_dates=target_dates,
        models=models or None,
        previous_days=previous_days or None,
        dry_run=dry_run,
        limit_cities=limit_cities,
    )
    if not dry_run and payload.get("runs_upserted", 0) > 0:
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["forecast_stage"] = readiness_stage(readiness, "forecast_runs")
    return payload


def run_china_weather_fetch(
    cities_arg: str = "",
    *,
    dry_run: bool = False,
) -> dict:
    from .china_weather import fetch_china_weather

    cities = _cities_from_arg(cities_arg)
    payload = fetch_china_weather(cities or None, dry_run=dry_run)
    if payload.get("ok") and not dry_run:
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["observations_stage"] = readiness_stage(readiness, "observations")
    return payload


def run_pws_fetch(
    cities_arg: str = "",
    *,
    dry_run: bool = False,
    all_cities: bool = False,
    limit_cities: int = 5,
    station_limit: int = 5,
) -> dict:
    from .pws import fetch_wunderground_pws

    cities = _cities_from_arg(cities_arg)
    if all_cities:
        cities = []
        limit_cities = 10_000
    payload = fetch_wunderground_pws(
        cities or None,
        dry_run=dry_run,
        limit_cities=limit_cities,
        station_limit=station_limit,
    )
    if payload.get("ok") and not dry_run:
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["observations_stage"] = readiness_stage(readiness, "observations")
    return payload


def run_history_backfill(
    cities_arg: str = "",
    *,
    days_arg: int = 30,
    start_date: str = "",
    end_date: str = "",
    dry_run: bool = False,
    all_cities: bool = False,
    limit_cities: int = 7,
) -> dict:
    from .history import fetch_open_meteo_historical_backfill

    cities = _cities_from_arg(cities_arg)
    if all_cities:
        cities = []
        limit_cities = 10_000
    payload = fetch_open_meteo_historical_backfill(
        cities or None,
        days=days_arg,
        start_date=start_date,
        end_date=end_date,
        dry_run=dry_run,
        limit_cities=limit_cities,
    )
    if payload.get("ok") and not dry_run:
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["observations_stage"] = readiness_stage(readiness, "observations")
    return payload


def run_orderbook_backfill(limit_arg: int = 50, start_date_arg: str = "", end_date_arg: str = "") -> dict:
    from .polymarket import PolymarketDataClient

    limit = max(1, min(int(limit_arg or 50), 500))
    start_date = start_date_arg or default_orderbook_start_date()
    end_date = end_date_arg or ""
    with connect() as conn:
        rows = select_orderbook_backfill_markets(
            conn,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
        )
    client = PolymarketDataClient()
    results = []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        market_id = str(row["market_id"])
        target_date = str(row["latest_target_date"] or "")
        signal_count = int(row["signal_count"] or 0)
        try:
            quote = client.quote(market_id)
            ok = _orderbook_quote_usable(quote)
            reason = _orderbook_quote_reason(quote) if not ok else "fresh_clob_depth_available"
            reason_counts[reason] += 1
            results.append({
                "market_id": market_id,
                "target_date": target_date,
                "signal_count": signal_count,
                "ok": ok,
                "reason": reason,
                "source": quote.book_source,
                "best_bid": quote.best_bid,
                "best_ask": quote.best_ask,
                "spread": quote.spread,
                "bid_levels": len(quote.bids),
                "ask_levels": len(quote.asks),
                "age_seconds": quote.quote_age_seconds,
            })
        except Exception as exc:
            results.append({
                "market_id": market_id,
                "target_date": target_date,
                "signal_count": signal_count,
                "ok": False,
                "reason": "quote_fetch_error",
                "error": str(exc),
            })
            reason_counts["quote_fetch_error"] += 1
        time.sleep(0.05)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    return {
        "selection_mode": "current_or_future_signal_markets",
        "start_date": start_date,
        "end_date": end_date or None,
        "requested": len(rows),
        "ok": sum(1 for row in results if row["ok"]),
        "failed": sum(1 for row in results if not row["ok"]),
        "reason_counts": dict(reason_counts),
        "results": results,
        "orderbook_stage": readiness_stage(readiness, "orderbooks"),
    }


def run_market_buckets_sync(
    limit_arg: int = 200,
    *,
    cities_arg: str = "",
    days_arg: int | None = None,
    target_date: str = "",
    active_weather: bool = False,
    dry_run: bool = False,
    limit_cities: int = 5,
    fetch_orderbooks: bool = True,
) -> dict:
    if active_weather:
        from .market_buckets import sync_active_weather_market_buckets

        cities = _cities_from_arg(cities_arg)
        target_dates = [target_date] if target_date else None
        result = sync_active_weather_market_buckets(
            cities=cities or None,
            target_dates=target_dates,
            days=days_arg or 3,
            limit_cities=limit_cities,
            limit=limit_arg,
            dry_run=dry_run,
            fetch_orderbooks=fetch_orderbooks,
        )
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        result["market_buckets_stage"] = readiness_stage(readiness, "market_buckets")
        return result

    from .market_buckets import ingest_market_buckets

    limit = max(1, min(int(limit_arg or 200), 1000))
    payloads = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT raw_json
            FROM markets
            WHERE raw_json IS NOT NULL AND raw_json != ''
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            parsed = _json_object(row["raw_json"])
            if parsed:
                payloads.append(parsed)
        if len(payloads) < limit:
            signal_rows = conn.execute(
                """
                SELECT raw_json
                FROM signals
                WHERE raw_json IS NOT NULL AND raw_json != ''
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (limit - len(payloads),),
            ).fetchall()
            for row in signal_rows:
                parsed = _json_object(row["raw_json"])
                market_payload = parsed.get("market") if isinstance(parsed.get("market"), dict) else parsed
                if market_payload:
                    payloads.append(market_payload)
    result = ingest_market_buckets(payloads[:limit])
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    result["market_buckets_stage"] = readiness_stage(readiness, "market_buckets")
    return result


def run_signal_decisions_build(
    cities_arg: str = "",
    target_date: str = "",
    *,
    days_arg: int | None = None,
    dry_run: bool = False,
    limit_cities: int = 5,
    limit: int = 200,
) -> dict:
    from .signals import build_signal_decisions_for_targets

    cities = _cities_from_arg(cities_arg)
    if not cities and limit_cities:
        cities = _default_layer_city_keys(limit_cities)
    if target_date:
        targets = [(city, target_date) for city in cities]
    else:
        targets = _signal_decision_targets_from_db(cities, days_arg or 7)
    payload = build_signal_decisions_for_targets(targets, dry_run=dry_run, limit=limit)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    payload["signal_decisions_stage"] = readiness_stage(readiness, "signal_decisions")
    return payload


def run_paper_execute(
    *,
    decision_id: str = "",
    cities_arg: str = "",
    target_date: str = "",
    limit: int = 20,
    amount: float | None = None,
    apply: bool = False,
) -> dict:
    from .paper import execute_paper_decision, execute_paper_decisions

    dry_run = not bool(apply)
    if decision_id:
        payload = execute_paper_decision(decision_id, amount=amount, dry_run=dry_run)
    else:
        cities = _cities_from_arg(cities_arg)
        if not cities or not target_date:
            return {
                "ok": False,
                "status": "blocked",
                "reason": "decision_id_or_city_target_date_required",
                "dry_run": dry_run,
            }
        payload = execute_paper_decisions(
            city_key=cities[0],
            target_date=target_date,
            limit=limit,
            amount=amount,
            dry_run=dry_run,
        )
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    payload["paper_execution_stage"] = readiness_stage(readiness, "paper_execution")
    return payload


def _json_object(raw: str) -> dict:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _cities_from_arg(cities_arg: str = "") -> list[str]:
    return [item.strip() for item in str(cities_arg or "").split(",") if item.strip()]


def _cli_date_window(
    *,
    target_date: str = "",
    start_date: str = "",
    end_date: str = "",
    days: int = 1,
) -> list[str]:
    if target_date:
        return [date.fromisoformat(target_date).isoformat()]
    if start_date or end_date:
        start = date.fromisoformat(start_date or end_date)
        end = date.fromisoformat(end_date or start_date)
        if end < start:
            start, end = end, start
        rows = []
        cursor = start
        while cursor <= end:
            rows.append(cursor.isoformat())
            cursor += timedelta(days=1)
        return rows
    count = max(1, min(int(days or 1), 90))
    today = date.today()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(count)]


def _default_layer_city_keys(limit_cities: int = 5) -> list[str]:
    preferred = ["chicago", "tokyo", "atlanta", "nyc", "dallas"]
    limit = max(1, int(limit_cities or len(preferred)))
    return preferred[:limit]


def _target_dates_from_db(cities: list[str], days: int, *, forecast_only: bool = False) -> list[tuple[str, str]]:
    selected_cities = cities or _default_layer_city_keys(5)
    per_city_limit = max(1, int(days or 1))
    targets: list[tuple[str, str]] = []
    with connect() as conn:
        for city in selected_cities:
            date_rows = []
            if forecast_only:
                date_rows = conn.execute(
                    """
                    SELECT DISTINCT target_date
                    FROM forecast_runs
                    WHERE city = ? AND target_date IS NOT NULL AND target_date != ''
                    ORDER BY target_date DESC
                    LIMIT ?
                    """,
                    (city, per_city_limit),
                ).fetchall()
            else:
                date_rows = conn.execute(
                    """
                    SELECT target_date FROM (
                        SELECT DISTINCT target_date
                        FROM forecast_runs
                        WHERE city = ? AND target_date IS NOT NULL AND target_date != ''
                        UNION
                        SELECT DISTINCT date(report_time) AS target_date
                        FROM metar_reports
                        WHERE city = ? AND report_time IS NOT NULL AND report_time != ''
                        UNION
                        SELECT DISTINCT date(observed_at) AS target_date
                        FROM mesonet_observations
                        WHERE city = ? AND observed_at IS NOT NULL AND observed_at != ''
                    )
                    WHERE target_date IS NOT NULL AND target_date != ''
                    ORDER BY target_date DESC
                    LIMIT ?
                    """,
                    (city, city, city, per_city_limit),
                ).fetchall()
            targets.extend((city, str(row["target_date"])) for row in date_rows if row["target_date"])
    return targets


def _signal_decision_targets_from_db(cities: list[str], days: int) -> list[tuple[str, str]]:
    selected_cities = cities or _default_layer_city_keys(5)
    per_city_limit = max(1, int(days or 1))
    targets: list[tuple[str, str]] = []
    with connect() as conn:
        for city in selected_cities:
            rows = conn.execute(
                """
                SELECT DISTINCT d.city_key, d.target_date
                FROM daily_max_predictions d
                INNER JOIN market_buckets m
                    ON m.city = d.city_key
                   AND m.target_date = d.target_date
                WHERE d.city_key = ?
                  AND d.target_date IS NOT NULL
                  AND d.target_date != ''
                ORDER BY d.target_date ASC
                LIMIT ?
                """,
                (city, per_city_limit),
            ).fetchall()
            targets.extend((str(row["city_key"]), str(row["target_date"])) for row in rows)
    if targets:
        return targets
    return _target_dates_from_db(selected_cities, days, forecast_only=True)


def _orderbook_quote_usable(quote) -> bool:
    return (
        quote.book_source == "clob"
        and len(quote.bids) > 0
        and len(quote.asks) > 0
        and quote.best_bid > 0
        and quote.best_ask > 0
    )


def _orderbook_quote_reason(quote) -> str:
    if quote.book_source != "clob":
        return "no_clob_orderbook"
    if not quote.bids and not quote.asks:
        return "empty_clob_depth"
    if not quote.bids:
        return "missing_bid_depth"
    if not quote.asks:
        return "missing_ask_depth"
    if quote.best_bid <= 0 or quote.best_ask <= 0:
        return "invalid_best_bid_ask"
    return "unknown_orderbook_blocker"


def run_truth_backfill(
    cities_arg: str = "",
    limit_arg: int = 50,
    start_date_arg: str = "",
    end_date_arg: str = "",
) -> dict:
    from .db import upsert_truth_observation
    from .registry import SETTLEMENT_REGISTRY
    from .truth import get_actual_observation

    repair = repair_truth_temporal_mismatches()
    requested = {item.strip() for item in cities_arg.split(",") if item.strip()}
    profiles = {
        city: profile
        for city, profile in SETTLEMENT_REGISTRY.items()
        if not requested or city in requested
    }
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT city, target_local_date
            FROM settlement_contracts
            WHERE target_local_date IS NOT NULL AND target_local_date != ''
            ORDER BY target_local_date DESC, city
            """
        ).fetchall()
    locations = {
        city: {
            "lat": profile.latitude,
            "lon": profile.longitude,
            "name": profile.city_name,
            "station": profile.station_id,
            "unit": profile.unit,
            "region": profile.region,
        }
        for city, profile in profiles.items()
    }
    timezones = {city: profile.timezone for city, profile in profiles.items()}
    candidates = []
    skipped_pending = 0
    skipped_unknown_city = 0
    for row in rows:
        city = str(row["city"] or "")
        target_date = str(row["target_local_date"] or "")
        if city not in profiles:
            skipped_unknown_city += 1
            continue
        if start_date_arg and target_date < start_date_arg:
            continue
        if end_date_arg and target_date > end_date_arg:
            continue
        if is_settlement_pending(target_date, profiles[city].timezone):
            skipped_pending += 1
            continue
        candidates.append((city, target_date))
    candidates = candidates[: max(1, min(int(limit_arg or 50), 500))]
    results = []
    for city, target_date in candidates:
        try:
            observation = get_actual_observation(city, target_date, locations, timezones)
            upsert_truth_observation(observation.to_dict())
            results.append({
                "city": city,
                "target_date": target_date,
                "provider": observation.provider,
                "actual_temp": observation.actual_temp,
                "eligible": observation.calibration_eligible,
                "is_final": observation.is_final,
                "ok": observation.actual_temp is not None,
            })
        except Exception as exc:
            results.append({"city": city, "target_date": target_date, "ok": False, "error": str(exc)})
        time.sleep(0.05)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    providers = Counter(row.get("provider") or "error" for row in results)
    return {
        "requested": len(candidates),
        "skipped_pending_settlement": skipped_pending,
        "skipped_unknown_city": skipped_unknown_city,
        "ok": sum(1 for row in results if row["ok"]),
        "eligible": sum(1 for row in results if row.get("eligible")),
        "providers": dict(providers),
        "temporal_repair": repair,
        "results": results,
        "truth_stage": next(
            (stage for stage in readiness["stages"] if stage["key"] == "truth"),
            None,
        ),
    }


def run_iem_asos_truth_fetch(
    cities_arg: str = "",
    *,
    target_date: str = "",
    start_date: str = "",
    end_date: str = "",
    days: int = 1,
    all_cities: bool = False,
    limit_cities: int = 10,
    dry_run: bool = False,
) -> dict:
    from .truth.iem_asos import fetch_iem_asos_range

    sync_station_registry()
    requested = _cities_from_arg(cities_arg)
    rows = list_stations()
    if requested:
        wanted = {item.lower() for item in requested}
        rows = [row for row in rows if str(row.get("city_key") or "").lower() in wanted or str(row.get("station_id") or "").lower() in wanted]
    elif not all_cities:
        rows = [row for row in rows if row.get("enabled")][: max(1, int(limit_cities or 10))]
    targets = sorted(set(_cli_date_window(target_date=target_date, start_date=start_date, end_date=end_date, days=days)))
    results = []
    for row in rows:
        # IEM is an observation-side approximation used for deltas. It must
        # follow the physical reporting station, not a non-airport settlement
        # authority such as Hong Kong Observatory.
        station = str(row.get("station_id") or "").upper()
        tz = str(row.get("timezone") or row.get("settlement_timezone") or "UTC")
        if not station:
            continue
        station_results = fetch_iem_asos_range(
            station,
            targets[0],
            targets[-1],
            tz,
            persist=not dry_run,
        )
        results.extend(_compact_iem_truth_result(result) for result in station_results)
    return {
        "ok": all(item.get("ok") for item in results) if results else False,
        "source": "iem_asos",
        "stage": "truth_iem_daily",
        "dry_run": dry_run,
        "cities": [row.get("city_key") for row in rows],
        "target_dates": targets,
        "stored": 0 if dry_run else sum(1 for item in results if item.get("ok")),
        "results": results,
    }


def run_hko_truth_fetch(
    *,
    target_date: str = "",
    start_date: str = "",
    end_date: str = "",
    days: int = 1,
    dry_run: bool = False,
) -> dict:
    from .truth.hko import fetch_hko_daily_extract_many

    targets = _cli_date_window(target_date=target_date, start_date=start_date, end_date=end_date, days=days)
    results = [
        _compact_hko_truth_result(result)
        for result in fetch_hko_daily_extract_many(targets, persist=not dry_run)
    ]
    return {
        "ok": all(item.get("ok") for item in results) if results else False,
        "source": "hko",
        "stage": "truth_hko_daily",
        "dry_run": dry_run,
        "target_dates": targets,
        "stored": 0 if dry_run else sum(1 for item in results if item.get("ok")),
        "results": results,
    }


def _compact_iem_truth_result(result: dict) -> dict:
    return {
        "ok": bool(result.get("ok")),
        "icao": result.get("icao"),
        "date_local": result.get("date_local"),
        "timezone": result.get("timezone"),
        "high_c": result.get("high_c"),
        "low_c": result.get("low_c"),
        "high_time_local": result.get("high_time_local"),
        "low_time_local": result.get("low_time_local"),
        "obs_count": result.get("obs_count"),
        "settlement_truth_type": result.get("settlement_truth_type"),
        "reason": result.get("reason"),
        "duration_ms": result.get("duration_ms"),
    }


def _compact_hko_truth_result(result: dict) -> dict:
    return {
        "ok": bool(result.get("ok")),
        "date_local": result.get("date_local"),
        "high_c": result.get("high_c"),
        "low_c": result.get("low_c"),
        "mean_c": result.get("mean_c"),
        "settlement_truth_type": result.get("settlement_truth_type"),
        "reason": result.get("reason"),
        "duration_ms": result.get("duration_ms"),
    }


def run_wunderground_truth_fetch(
    cities_arg: str = "",
    *,
    target_date: str = "",
    start_date: str = "",
    end_date: str = "",
    days: int = 1,
    all_cities: bool = False,
    limit_cities: int = 10,
    dry_run: bool = False,
    force_rebuild: bool = False,
) -> dict:
    from .truth.wunderground import fetch_wunderground_daily_result, persist_wunderground_daily, persist_wunderground_hourly

    sync_station_registry()
    requested = _cities_from_arg(cities_arg)
    rows = list_stations()
    if requested:
        wanted = {item.lower() for item in requested}
        rows = [row for row in rows if str(row.get("city_key") or "").lower() in wanted or str(row.get("station_id") or "").lower() in wanted]
    elif not all_cities:
        rows = [row for row in rows if row.get("enabled")][: max(1, int(limit_cities or 10))]
    targets = _cli_date_window(target_date=target_date, start_date=start_date, end_date=end_date, days=days)
    results = []
    hourly_rows_upserted = 0
    for row in rows:
        station = str(row.get("settlement_station_id") or row.get("station_id") or "").upper()
        if station == "HKO":
            continue
        timezone_name = str(row.get("settlement_timezone") or row.get("timezone") or "UTC")
        for target in targets:
            if not force_rebuild:
                cached = _existing_wunderground_daily(station, target)
                if cached:
                    results.append(cached)
                    continue
            result = fetch_wunderground_daily_result(station, target, timezone_name=timezone_name)
            if result.get("ok") and not dry_run:
                persist_wunderground_daily(result)
                hourly_result = result.get("hourly_result")
                if isinstance(hourly_result, dict) and hourly_result.get("ok"):
                    persisted_hourly = persist_wunderground_hourly(hourly_result)
                    hourly_rows_upserted += int(persisted_hourly.get("rows_upserted") or 0)
            results.append(_compact_wunderground_result(result))
    return {
        "ok": all(item.get("ok") for item in results) if results else False,
        "source": "wunderground",
        "stage": "truth_wunderground_daily",
        "dry_run": dry_run,
        "force_rebuild": force_rebuild,
        "target_dates": targets,
        "stored": 0 if dry_run else sum(1 for item in results if item.get("ok")),
        "hourly_rows_upserted": hourly_rows_upserted,
        "skipped": sum(1 for item in results if not item.get("ok")),
        "results": results,
    }


def run_wunderground_hourly_fetch(
    cities_arg: str = "",
    *,
    target_date: str = "",
    start_date: str = "",
    end_date: str = "",
    days: int = 1,
    all_cities: bool = False,
    limit_cities: int = 10,
    dry_run: bool = False,
) -> dict:
    from .truth.wunderground import fetch_wunderground_hourly_result, persist_wunderground_hourly

    sync_station_registry()
    requested = _cities_from_arg(cities_arg)
    rows = list_stations()
    if requested:
        wanted = {item.lower() for item in requested}
        rows = [row for row in rows if str(row.get("city_key") or "").lower() in wanted or str(row.get("station_id") or "").lower() in wanted]
    elif not all_cities:
        rows = [row for row in rows if row.get("enabled")][: max(1, int(limit_cities or 10))]
    targets = _cli_date_window(target_date=target_date, start_date=start_date, end_date=end_date, days=days)
    results = []
    rows_upserted = 0
    for row in rows:
        station = str(row.get("settlement_station_id") or row.get("station_id") or "").upper()
        if station == "HKO":
            continue
        timezone_name = str(row.get("settlement_timezone") or row.get("timezone") or "UTC")
        for target in targets:
            result = fetch_wunderground_hourly_result(station, target, timezone_name=timezone_name)
            if result.get("ok") and not dry_run:
                persisted = persist_wunderground_hourly(result)
                rows_upserted += int(persisted.get("rows_upserted") or 0)
                result["rows_upserted"] = persisted.get("rows_upserted")
            results.append(_compact_wunderground_result(result))
    return {
        "ok": all(item.get("ok") for item in results) if results else False,
        "source": "wunderground",
        "stage": "truth_wunderground_hourly",
        "dry_run": dry_run,
        "target_dates": targets,
        "stored": 0 if dry_run else sum(1 for item in results if item.get("ok")),
        "rows_upserted": rows_upserted,
        "skipped": sum(1 for item in results if not item.get("ok")),
        "results": results,
    }


def _compact_wunderground_result(result: dict) -> dict:
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    hourly_result = result.get("hourly_result") if isinstance(result.get("hourly_result"), dict) else None
    return {
        "ok": bool(result.get("ok")),
        "icao": result.get("icao"),
        "date_local": result.get("date_local"),
        "timezone": result.get("timezone"),
        "high_c": result.get("high_c"),
        "low_c": result.get("low_c"),
        "row_count": result.get("row_count") if result.get("row_count") is not None else (len(rows) if rows else None),
        "hourly_row_count": result.get("hourly_row_count") or (hourly_result.get("row_count") if hourly_result else None),
        "method": result.get("method"),
        "settlement_truth_type": result.get("settlement_truth_type"),
        "source_url": result.get("source_url"),
        "skip_reasons": result.get("skip_reasons") or [],
        "duration_ms": result.get("duration_ms"),
        "rows_upserted": result.get("rows_upserted"),
    }


def _existing_wunderground_daily(station: str, target_date: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT icao, date_local, timezone, high_c, low_c, method,
                   settlement_truth_type, source_url, parser_version, updated_at
            FROM truth_wunderground_daily
            WHERE UPPER(icao) = UPPER(?) AND date_local = ?
              AND high_c IS NOT NULL
            """,
            (station, target_date),
        ).fetchone()
    if not row:
        return None
    return {
        "ok": True,
        "cached": True,
        "icao": row["icao"],
        "date_local": row["date_local"],
        "timezone": row["timezone"],
        "high_c": row["high_c"],
        "low_c": row["low_c"],
        "row_count": None,
        "hourly_row_count": None,
        "method": row["method"],
        "settlement_truth_type": row["settlement_truth_type"],
        "source_url": row["source_url"],
        "skip_reasons": [],
        "duration_ms": 0,
        "rows_upserted": 0,
        "updated_at": row["updated_at"],
    }


def run_truth_delta_build(limit: int = 500) -> dict:
    from .truth.delta import rebuild_truth_delta_from_tables

    return rebuild_truth_delta_from_tables(limit=limit)


def run_gamma_structured_sync(
    cities_arg: str = "",
    *,
    days: int = 3,
    target_date: str = "",
    dry_run: bool = False,
    fetch_orderbooks: bool = True,
) -> dict:
    from .polymarket_gamma import sync_asian_weather_markets

    targets = [target_date] if target_date else None
    return sync_asian_weather_markets(
        cities=_cities_from_arg(cities_arg) or None,
        target_dates=targets,
        days=days,
        fetch_orderbooks=fetch_orderbooks,
        dry_run=dry_run,
    )


def run_polymarket_market_probe(cities_arg: str = "", *, apply: bool = True, days_ahead: int = 3) -> dict:
    from .polymarket_probe import probe_polymarket_markets

    cities = _cities_from_arg(cities_arg)
    if not cities:
        raise SystemExit("--city or --cities is required")
    payload = probe_polymarket_markets(cities, days_ahead=days_ahead, apply=apply)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    payload["stations_stage"] = readiness_stage(readiness, "stations")
    return payload


def run_legacy_signal_scan() -> dict:
    from bot_v2 import scan_and_update

    new_pos, closed, resolved = scan_and_update()
    migrated = migrate_legacy_signals()
    return {
        "ok": True,
        "new_positions": new_pos,
        "closed_positions": closed,
        "resolved_positions": resolved,
        "migrated_signals": migrated,
    }


def _stage_payload_ok(payload: object) -> bool:
    if not isinstance(payload, dict):
        return True
    ok_value = payload.get("ok")
    if isinstance(ok_value, bool):
        return ok_value
    if "failed" in payload:
        try:
            return int(payload.get("failed") or 0) == 0
        except Exception:
            return False
    failed_stages = payload.get("failed_stages")
    if isinstance(failed_stages, list):
        return len(failed_stages) == 0
    if "ok" in payload:
        return bool(ok_value)
    return True


def _stage_result(name: str, fn) -> dict:
    started = time.perf_counter()
    try:
        payload = fn()
        ok = _stage_payload_ok(payload)
        return {"name": name, "ok": ok, "elapsed_ms": round((time.perf_counter() - started) * 1000), "payload": payload}
    except Exception as exc:
        return {"name": name, "ok": False, "elapsed_ms": round((time.perf_counter() - started) * 1000), "error": str(exc)}


def _run_recent_metar_refresh(cities_arg: str, hours: float) -> dict:
    from .metar import refresh_metar_reports

    cities = _cities_from_arg(cities_arg)
    bounded_hours = max(1.0, min(float(hours or 24.0), 96.0))
    payload = refresh_metar_reports(cities or None, hours=bounded_hours)
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    payload["observations_stage"] = readiness_stage(readiness, "observations")
    payload["hours_requested"] = bounded_hours
    return payload


def run_production_refresh(
    *,
    cities: str = "",
    days: int = 4,
    limit: int = 50,
    start_date: str = "",
    end_date: str = "",
    scan_signals: bool = True,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    init_v3_db()
    bounded_days = max(1, min(int(days or 4), 7))
    bounded_limit = max(1, min(int(limit or 50), 500))
    target_date = (start_date or end_date or "").strip()
    recent_metar_hours = max(24.0, min(float(bounded_days) * 24.0, 96.0))
    stages = []

    def emit_progress() -> None:
        if not progress_callback:
            return
        failed = [stage for stage in stages if stage.get("ok") is False and not stage.get("running")]
        progress_callback({
            "refresh_version": "production-refresh-v2",
            "ok": not failed,
            "running": True,
            "failed_stages": [stage["name"] for stage in failed],
            "scan_signals": scan_signals,
            "target_date": target_date,
            "stages": list(stages),
        })

    def run_stage(name: str, fn) -> None:
        stages.append({"name": name, "ok": False, "running": True})
        emit_progress()
        stages[-1] = _stage_result(name, fn)
        emit_progress()

    run_stage("contracts_sync", sync_settlement_contracts)
    run_stage("forecast_backfill", lambda: run_forecast_backfill(cities, bounded_days))
    run_stage(
        "openmeteo_fetch",
        lambda: run_openmeteo_fetch(cities, forecast_days=min(bounded_days + 2, 7), limit_cities=5),
    )
    run_stage(
        "weathercom_fetch",
        lambda: run_weathercom_fetch(cities, forecast_days=min(bounded_days + 2, 7), limit_cities=5),
    )
    run_stage("metar_refresh", lambda: _run_recent_metar_refresh(cities, recent_metar_hours))
    run_stage(
        "hourly_consensus",
        lambda: run_hourly_consensus_build(cities, target_date=target_date, days_arg=None if target_date else bounded_days),
    )
    run_stage(
        "daily_max_build",
        lambda: run_daily_max_build(cities, target_date=target_date, days_arg=None if target_date else bounded_days),
    )
    run_stage(
        "market_buckets_sync",
        lambda: run_market_buckets_sync(
            bounded_limit,
            cities_arg=cities,
            days_arg=bounded_days,
            target_date=target_date,
            active_weather=True,
            limit_cities=5,
            fetch_orderbooks=True,
        ),
    )
    run_stage(
        "signal_decisions_build",
        lambda: run_signal_decisions_build(cities, target_date=target_date, days_arg=None if target_date else bounded_days, limit=bounded_limit),
    )
    if scan_signals:
        run_stage("signal_scan", run_legacy_signal_scan)
    else:
        stages.append({"name": "signal_scan", "ok": True, "skipped": True, "reason": "skip_signal_scan"})
        emit_progress()
        run_stage("signal_migration", migrate_legacy_signals)
    run_stage("orderbook_backfill", lambda: run_orderbook_backfill(bounded_limit, start_date or target_date, end_date or target_date))
    readiness = build_data_readiness()
    persist_data_readiness(readiness)
    failed = [stage for stage in stages if not stage.get("ok")]
    return {
        "refresh_version": "production-refresh-v2",
        "ok": not failed,
        "failed_stages": [stage["name"] for stage in failed],
        "scan_signals": scan_signals,
        "target_date": target_date,
        "stages": stages,
        "readiness": {
            "status": readiness.get("status"),
            "score": readiness.get("score"),
            "live_allowed": readiness.get("live_allowed"),
            "production_phase": readiness.get("production_phase"),
            "blocked_keys": (readiness.get("production_phase") or {}).get("blocked_keys", []),
            "next_actions": readiness.get("next_actions", [])[:5],
        },
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "state-print":
        print_current_state()
        return
    parser = argparse.ArgumentParser(description="WeatherBot v3 utilities")
    parser.add_argument(
        "command",
        choices=[
            "init-db",
            "migrate",
            "summary",
            "source-health",
            "state-print",
            "notify-daily",
            "production-refresh",
            "production-validation",
            "stations-sync",
            "stations-list",
            "stations-enable",
            "stations-disable",
            "data-readiness",
            "model-dataset-audit",
            "forecast-backfill",
            "openmeteo-fetch",
            "weathercom-fetch",
            "openmeteo-previous-runs",
            "china-weather-fetch",
            "pws-fetch",
            "history-backfill",
            "metar-refresh",
            "metar-backfill",
            "hourly-consensus-build",
            "daily-max-build",
            "market-buckets-sync",
            "polymarket-market-probe",
            "signal-decisions-build",
            "model-timing-reprice",
            "paper-execute",
            "forecast-archive-import",
            "forecast-archive-manifest",
            "orderbook-backfill",
            "contracts-sync",
            "contracts-list",
            "contracts-verify",
            "contracts-bulk-verify",
            "truth-backfill",
            "truth-audit",
            "iem-asos-fetch",
            "hko-truth-fetch",
            "wunderground-truth-fetch",
            "wunderground-hourly-fetch",
            "truth-delta-build",
            "gamma-structured-sync",
        ],
    )
    parser.add_argument("--cities", default="", help="Comma-separated city keys; empty means all cities")
    parser.add_argument("--city", action="append", default=[], help="Single city key; can be repeated and is merged with --cities")
    parser.add_argument("--days", type=int, default=None, help="Days for supported commands; forecast defaults to 4, METAR backfill defaults to 30")
    parser.add_argument("--recent-hours", type=float, default=24.0, help="Recent METAR hours for metar-refresh")
    parser.add_argument("--station-limit", type=int, default=5, help="Maximum PWS stations per city")
    parser.add_argument("--limit", type=int, default=50, help="Maximum current/future signal markets to refresh")
    parser.add_argument("--start-date", default="", help="Inclusive local target date filter")
    parser.add_argument("--target-date", default="", help="Single local target date for Layer 4 build commands")
    parser.add_argument("--end-date", default="", help="Inclusive local target date filter")
    parser.add_argument(
        "--status",
        default="unverified",
        help="Contract status filter: all, unverified, verified, auto, mature-auto, future-auto, manual-required, source-missing, low-confidence",
    )
    parser.add_argument("--contract-id", default="", help="Settlement contract id or event slug")
    parser.add_argument("--decision-id", default="", help="Layer 6 signal decision id")
    parser.add_argument("--amount", type=float, default=None, help="Paper/live order amount where supported")
    parser.add_argument("--reviewer", default="local-operator", help="Manual verifier name")
    parser.add_argument("--note", default="", help="Manual verification note")
    parser.add_argument("--archive-path", default="", help="Historical forecast archive JSON/JSONL path")
    parser.add_argument("--output-path", default="", help="Output path for generated JSONL/manifest files")
    parser.add_argument("--sources", default="ecmwf,gfs_ensemble", help="Comma-separated forecast archive sources")
    parser.add_argument("--unverify", action="store_true", help="Clear manual verification instead of setting it")
    parser.add_argument("--apply", action="store_true", help="Apply a bulk write; without it bulk commands are dry-run")
    parser.add_argument("--mature-only", action="store_true", help="Only act on contracts whose local settlement day has ended")
    parser.add_argument("--skip-signal-scan", action="store_true", help="Skip the legacy signal scan during production-refresh")
    parser.add_argument("--include-targets", action="store_true", help="Include full next-action target lists in production-validation output")
    parser.add_argument("--all-cities", action="store_true", help="Apply supported city commands to all station rows")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing rows where supported")
    parser.add_argument("--probe-stations", action="store_true", help="Probe IEM ASOS station= candidates before METAR backfill")
    parser.add_argument("--limit-cities", type=int, default=5, help="Maximum default cities for METAR probe/backfill")
    parser.add_argument("--ensemble", action="store_true", help="Fetch Open-Meteo ensemble endpoint where supported")
    parser.add_argument("--forecast-days", type=int, default=7, help="Forecast days for Open-Meteo fetch")
    parser.add_argument("--previous-days", default="1,2,3", help="Comma-separated Open-Meteo Previous Runs lead days, 1-7")
    parser.add_argument("--models", default="", help="Comma-separated model names for Open-Meteo Previous Runs")
    parser.add_argument("--force-rebuild", action="store_true", help="Force upsert/rebuild where supported")
    parser.add_argument("--active-weather", action="store_true", help="Sync active Polymarket weather events from Gamma/CLOB")
    parser.add_argument("--skip-orderbooks", action="store_true", help="Skip CLOB orderbook fetches for active weather market buckets")
    args = parser.parse_args()
    cities_arg = ",".join(item for item in [args.cities, *args.city] if item)

    if args.command == "init-db":
        init_v3_db()
        print("v3 database initialized")
    elif args.command == "migrate":
        init_v3_db()
        payload = {"signals": migrate_legacy_signals(), "markets": audit_market_files()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "summary":
        print(json.dumps(dashboard_summary(), ensure_ascii=False, indent=2))
    elif args.command == "source-health":
        print(json.dumps(build_source_health_matrix(), ensure_ascii=False, indent=2))
    elif args.command == "state-print":
        print_current_state()
    elif args.command == "notify-daily":
        summary = dashboard_summary()
        sent = FeishuNotifier().daily_summary(summary)
        print(json.dumps({"sent": sent, "summary": summary}, ensure_ascii=False, indent=2))
    elif args.command == "production-refresh":
        payload = run_production_refresh(
            cities=args.cities,
            days=args.days or 4,
            limit=args.limit,
            start_date=args.start_date,
            end_date=args.end_date,
            scan_signals=not args.skip_signal_scan,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "production-validation":
        payload = build_production_validation_report(include_action_targets=args.include_targets)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "stations-sync":
        payload = sync_station_registry()
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["stations_stage"] = next(
            (stage for stage in readiness["stages"] if stage["key"] == "stations"),
            None,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "stations-list":
        sync_station_registry()
        stations = list_stations()
        payload = {
            "stations": stations,
            "count": len(stations),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command in {"stations-enable", "stations-disable"}:
        cities = _cities_from_arg(cities_arg)
        if not cities:
            raise SystemExit("--city or --cities is required")
        enabled = args.command == "stations-enable"
        results = [set_station_enabled(city, enabled) for city in cities]
        print(json.dumps({
            "ok": all(row.get("ok") for row in results),
            "enabled": enabled,
            "results": results,
        }, ensure_ascii=False, indent=2))
    elif args.command == "data-readiness":
        payload = build_data_readiness()
        persist_data_readiness(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "model-dataset-audit":
        payload = build_model_dataset_audit(min_samples=args.limit)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "forecast-backfill":
        print(json.dumps(run_forecast_backfill(cities_arg, args.days or 4), ensure_ascii=False, indent=2))
    elif args.command == "openmeteo-fetch":
        print(json.dumps(
            run_openmeteo_fetch(
                cities_arg,
                ensemble=args.ensemble,
                dry_run=args.dry_run,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
                forecast_days=args.forecast_days,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "weathercom-fetch":
        print(json.dumps(
            run_weathercom_fetch(
                cities_arg,
                dry_run=args.dry_run,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
                forecast_days=args.forecast_days,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "openmeteo-previous-runs":
        print(json.dumps(
            run_openmeteo_previous_runs(
                cities_arg,
                target_date=args.target_date,
                start_date=args.start_date,
                end_date=args.end_date,
                days_arg=args.days,
                previous_days_arg=args.previous_days,
                models_arg=args.models,
                dry_run=args.dry_run,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "china-weather-fetch":
        print(json.dumps(
            run_china_weather_fetch(
                cities_arg,
                dry_run=args.dry_run,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "pws-fetch":
        print(json.dumps(
            run_pws_fetch(
                cities_arg,
                dry_run=args.dry_run,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
                station_limit=args.station_limit,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "history-backfill":
        print(json.dumps(
            run_history_backfill(
                cities_arg,
                days_arg=args.days or 30,
                start_date=args.start_date,
                end_date=args.end_date,
                dry_run=args.dry_run,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "metar-refresh":
        print(json.dumps(_run_recent_metar_refresh(cities_arg, args.recent_hours), ensure_ascii=False, indent=2))
    elif args.command == "metar-backfill":
        print(json.dumps(
            run_metar_backfill(
                cities_arg,
                days_arg=args.days or 30,
                dry_run=args.dry_run,
                probe_stations=args.probe_stations,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
                output_path=args.output_path,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "hourly-consensus-build":
        print(json.dumps(
            run_hourly_consensus_build(
                cities_arg,
                args.target_date or args.start_date,
                days_arg=args.days,
                limit_cities=args.limit_cities,
                force_rebuild=args.force_rebuild,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "daily-max-build":
        print(json.dumps(
            run_daily_max_build(
                cities_arg,
                args.target_date or args.start_date,
                days_arg=args.days,
                dry_run=args.dry_run,
                limit_cities=args.limit_cities,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "market-buckets-sync":
        print(json.dumps(
            run_market_buckets_sync(
                args.limit,
                cities_arg=cities_arg,
                days_arg=args.days,
                target_date=args.target_date or args.start_date,
                active_weather=args.active_weather,
                dry_run=args.dry_run,
                limit_cities=args.limit_cities,
                fetch_orderbooks=not args.skip_orderbooks,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "polymarket-market-probe":
        print(json.dumps(
            run_polymarket_market_probe(
                cities_arg,
                apply=not args.dry_run,
                days_ahead=args.days if args.days is not None else 3,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "signal-decisions-build":
        print(json.dumps(
            run_signal_decisions_build(
                cities_arg,
                args.target_date or args.start_date,
                days_arg=args.days,
                dry_run=args.dry_run,
                limit_cities=args.limit_cities,
                limit=args.limit,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "model-timing-reprice":
        print(json.dumps(
            run_model_timing_reprice(
                cities_arg,
                days_arg=args.days if args.days is not None else 2,
                dry_run=args.dry_run,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "paper-execute":
        print(json.dumps(
            run_paper_execute(
                decision_id=args.decision_id,
                cities_arg=cities_arg,
                target_date=args.target_date or args.start_date,
                limit=args.limit,
                amount=args.amount,
                apply=args.apply,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "forecast-archive-import":
        if not args.archive_path:
            raise SystemExit("--archive-path is required")
        from .forecast_archive import import_forecast_archive

        payload = import_forecast_archive(args.archive_path, apply=args.apply)
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["forecast_stage"] = next(
            (stage for stage in readiness["stages"] if stage["key"] == "forecast_runs"),
            None,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "forecast-archive-manifest":
        from .forecast_archive import build_forecast_archive_manifest, write_forecast_archive_manifest

        sources = [source.strip() for source in args.sources.split(",") if source.strip()]
        audit = build_model_dataset_audit(min_samples=args.limit)
        manifest = build_forecast_archive_manifest(audit, sources=sources)
        payload = {
            key: value
            for key, value in manifest.items()
            if key != "jsonl"
        }
        if args.output_path:
            write_forecast_archive_manifest(manifest, args.output_path)
            payload["output_path"] = args.output_path
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "orderbook-backfill":
        print(json.dumps(run_orderbook_backfill(args.limit, args.start_date, args.end_date), ensure_ascii=False, indent=2))
    elif args.command == "contracts-sync":
        payload = sync_settlement_contracts()
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            **payload,
            "contract_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "settlement_contracts"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "contracts-list":
        payload = list_settlement_contracts(status=args.status, limit=args.limit)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "contracts-verify":
        if not args.contract_id:
            raise SystemExit("--contract-id is required")
        if not args.unverify and not str(args.note or "").strip():
            raise SystemExit("--note is required when manually verifying a contract")
        contract = set_settlement_contract_verification(
            args.contract_id,
            verified=not args.unverify,
            reviewer=args.reviewer,
            note=args.note,
        )
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            "ok": True,
            "contract": contract,
            "contract_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "settlement_contracts"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "contracts-bulk-verify":
        contract_ids = [item.strip() for item in args.contract_id.split(",") if item.strip()]
        result = bulk_settlement_contract_verification(
            contract_ids=contract_ids or None,
            limit=args.limit,
            reviewer=args.reviewer,
            note=args.note or "bulk review from CLI",
            require_auto_verified=True,
            mature_only=args.mature_only,
            apply=args.apply,
        )
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            **result,
            "contract_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "settlement_contracts"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "truth-backfill":
        payload = run_truth_backfill(args.cities, args.limit, args.start_date, args.end_date)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "truth-audit":
        repair = repair_truth_temporal_mismatches()
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            **repair,
            "truth_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "truth"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "iem-asos-fetch":
        print(json.dumps(
            run_iem_asos_truth_fetch(
                cities_arg,
                target_date=args.target_date or "",
                start_date=args.start_date,
                end_date=args.end_date,
                days=args.days or 1,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
                dry_run=args.dry_run,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "hko-truth-fetch":
        print(json.dumps(
            run_hko_truth_fetch(
                target_date=args.target_date or "",
                start_date=args.start_date,
                end_date=args.end_date,
                days=args.days or 1,
                dry_run=args.dry_run,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "wunderground-truth-fetch":
        print(json.dumps(
            run_wunderground_truth_fetch(
                cities_arg,
                target_date=args.target_date or "",
                start_date=args.start_date,
                end_date=args.end_date,
                days=args.days or 1,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
                dry_run=args.dry_run,
                force_rebuild=args.force_rebuild,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "wunderground-hourly-fetch":
        print(json.dumps(
            run_wunderground_hourly_fetch(
                cities_arg,
                target_date=args.target_date or "",
                start_date=args.start_date,
                end_date=args.end_date,
                days=args.days or 1,
                all_cities=args.all_cities,
                limit_cities=args.limit_cities,
                dry_run=args.dry_run,
            ),
            ensure_ascii=False,
            indent=2,
        ))
    elif args.command == "truth-delta-build":
        print(json.dumps(run_truth_delta_build(args.limit), ensure_ascii=False, indent=2))
    elif args.command == "gamma-structured-sync":
        print(json.dumps(
            run_gamma_structured_sync(
                cities_arg,
                days=args.days or 3,
                target_date=args.target_date or args.start_date,
                dry_run=args.dry_run,
                fetch_orderbooks=not args.skip_orderbooks,
            ),
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
