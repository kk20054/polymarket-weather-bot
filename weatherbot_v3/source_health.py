from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import load_config
from .db import connect, connect_readonly, utc_now
from .env_utils import env_value


SOURCE_HEALTH_VERSION = "source-health-v2"


def build_source_health_matrix(
    path: Path | None = None,
    *,
    now_utc: datetime | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Return an auditable health matrix for source and derived-data layers."""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cfg = load_config()
    connection_factory = connect_readonly if read_only else connect
    with connection_factory(path) as conn:
        enabled_rows = conn.execute(
            """
            SELECT city_key, station_id, settlement_station_id,
                   primary_settlement_source, settlement_rule_verified_at,
                   verification_status
            FROM stations
            WHERE COALESCE(enabled, 0) = 1
            ORDER BY city_key
            """
        ).fetchall()
        enabled = [str(row["city_key"]) for row in enabled_rows]
        enabled_set = set(enabled)

        matrix = [
            _settlement_contract_source(enabled_rows),
            _freshness_source(
                conn,
                key="metar",
                label="AWC METAR",
                role="observation_peak_floor",
                table="metar_reports",
                city_column="city",
                timestamp_column="report_time",
                freshness_column="updated_at",
                where="COALESCE(parse_status, '') != 'failed'",
                expected_cities=enabled,
                expected_interval_seconds=300,
                # A station can legitimately publish only one routine report per
                # hour. Keep the platform health threshold aligned with the D+0
                # decision gate; individual stale cities are still rejected by
                # signal generation.
                stale_after_seconds=1800,
                required=True,
                stages=("metar_poller", "refresh_metar_reports"),
                now=now,
            ),
            _freshness_source(
                conn,
                key="forecast_openmeteo",
                label="Open-Meteo multi-model",
                role="forecast_model_input",
                table="forecast_runs",
                city_column="city",
                timestamp_column="retrieved_at",
                where="source LIKE 'openmeteo_%' AND source NOT LIKE 'openmeteo_previous_%' AND COALESCE(parse_status, '') != 'failed'",
                expected_cities=enabled,
                expected_interval_seconds=3600,
                stale_after_seconds=int(max(60.0, cfg.forecast_max_age_minutes) * 60),
                required=True,
                stages=("nwp_poller", "refresh_forecast_runs"),
                now=now,
                latest_order_by="target_date DESC, retrieved_at DESC",
                recent_date_column="target_date",
                recent_date_cutoff=(now - timedelta(days=2)).date().isoformat(),
            ),
            _freshness_source(
                conn,
                key="forecast_weathercom_v3",
                label="Weather.com v3",
                role="polywx_aligned_forecast_component",
                table="forecast_runs",
                city_column="city",
                timestamp_column="retrieved_at",
                where="source = 'weathercom_v3_forecast' AND COALESCE(parse_status, '') != 'failed'",
                expected_cities=enabled,
                expected_interval_seconds=1800,
                stale_after_seconds=int(max(60.0, cfg.forecast_max_age_minutes) * 60),
                required=bool(cfg.weather_com_forecast_enabled) or str(cfg.deb_weight_mode).lower() in {
                    "polywx", "polywx_aligned", "polywx_aligned_deb_v1"
                },
                stages=("forecast_poller", "refresh_forecast_runs"),
                now=now,
                latest_order_by="target_date DESC, retrieved_at DESC",
                recent_date_column="target_date",
                recent_date_cutoff=(now - timedelta(days=2)).date().isoformat(),
            ),
            _freshness_source(
                conn,
                key="china_live",
                label="China/HKO live",
                role="display_and_short_term_observation",
                table="mesonet_observations",
                city_column="city",
                timestamp_column="observed_at",
                freshness_column="COALESCE(fetched_at, updated_at)",
                where="network = 'china_live' AND COALESCE(parse_status, '') != 'failed'",
                expected_cities=[city for city in ("shanghai", "hong-kong") if city in enabled_set],
                expected_interval_seconds=60,
                stale_after_seconds=900,
                required=False,
                stages=("china_live_poller", "china_weather_fetch"),
                now=now,
            ),
            _freshness_source(
                conn,
                key="wunderground_pws",
                label="Wunderground PWS",
                role="display_and_peak_lock_only",
                table="mesonet_observations",
                city_column="city",
                timestamp_column="observed_at",
                freshness_column="COALESCE(fetched_at, updated_at)",
                where="network = 'wunderground_pws' AND COALESCE(parse_status, '') != 'failed'",
                expected_cities=enabled,
                expected_interval_seconds=600,
                stale_after_seconds=900,
                required=False,
                stages=("pws_poller", "pws_fetch"),
                now=now,
            ),
        ]

        wu_expected = [
            str(row["city_key"])
            for row in enabled_rows
            if str(row["settlement_station_id"] or row["station_id"] or "").upper() != "HKO"
        ]
        matrix.append(
            _daily_truth_source(
                conn,
                key="truth_wunderground_daily",
                label="WU/weather.com daily truth",
                role="settlement_truth",
                table="truth_wunderground_daily",
                station_column="icao",
                expected_station_rows=enabled_rows,
                expected_cities=wu_expected,
                target_days=max(30, int(cfg.min_independent_settlement_days)),
                stages=("truth_wunderground_daily",),
                now=now,
            )
        )
        matrix.append(
            _daily_truth_source(
                conn,
                key="truth_wunderground_hourly",
                label="WU/weather.com hourly history",
                role="historical_line_and_daily_truth_input",
                table="truth_wunderground_hourly",
                station_column="icao",
                expected_station_rows=enabled_rows,
                expected_cities=wu_expected,
                target_days=max(30, int(cfg.min_independent_settlement_days)),
                stages=("historical_poller", "truth_wunderground_hourly"),
                now=now,
            )
        )
        matrix.append(
            _daily_truth_source(
                conn,
                key="truth_iem_daily",
                label="IEM ASOS daily approximation",
                role="truth_fallback_and_delta_baseline",
                table="truth_iem_daily",
                station_column="icao",
                expected_station_rows=enabled_rows,
                expected_cities=wu_expected,
                target_days=max(30, int(cfg.min_independent_settlement_days)),
                stages=("truth_iem_daily", "iem_asos_fetch"),
                now=now,
                required=False,
            )
        )
        matrix.append(_hko_truth_source(conn, enabled_set, max(30, int(cfg.min_independent_settlement_days)), now))
        matrix.extend([
            _orderbook_source(conn, enabled, now, int(cfg.orderbook_max_age_minutes * 60)),
            _freshness_source(
                conn,
                key="hourly_consensus",
                label="Hourly consensus",
                role="derived_weather_evidence",
                table="hourly_consensus",
                city_column="city",
                timestamp_column="updated_at",
                where="COALESCE(build_status, '') != 'failed'",
                expected_cities=enabled,
                expected_interval_seconds=900,
                stale_after_seconds=2700,
                required=True,
                stages=("derive_poller",),
                now=now,
                latest_order_by="target_date DESC, local_hour DESC, updated_at DESC",
                recent_date_column="target_date",
                recent_date_cutoff=(now - timedelta(days=2)).date().isoformat(),
            ),
            _freshness_source(
                conn,
                key="signal_decisions",
                label="Signal decisions",
                role="derived_trading_decision",
                table="signal_decisions",
                city_column="city_key",
                timestamp_column="issued_at",
                freshness_column="updated_at",
                where="decision_id IS NOT NULL",
                expected_cities=enabled,
                expected_interval_seconds=900,
                stale_after_seconds=2700,
                required=True,
                stages=("derive_poller",),
                now=now,
                latest_order_by="target_date DESC, updated_at DESC",
                recent_date_column="target_date",
                recent_date_cutoff=(now - timedelta(days=2)).date().isoformat(),
            ),
        ])

    city_matrix = _build_city_matrix(enabled_rows, matrix)
    required_bad = [row for row in matrix if row["required"] and row["status"] != "healthy"]
    optional_bad = [row for row in matrix if not row["required"] and row["status"] != "healthy"]
    overall = "healthy" if not required_bad else "blocked"
    return {
        "ok": not required_bad,
        "version": SOURCE_HEALTH_VERSION,
        "generated_at": now.isoformat(),
        "overall_status": overall,
        "config": {
            "deb_weight_mode": cfg.deb_weight_mode,
            "weather_com_forecast_enabled": cfg.weather_com_forecast_enabled,
            "weather_com_configured": bool(env_value("WEATHER_COM_API_KEY") or env_value("WUNDERGROUND_API_KEY")),
            "pws_peak_lock_enabled": cfg.pws_peak_lock_enabled,
            "wunderground_pws_configured": bool(env_value("WUNDERGROUND_API_KEY")),
            "live_trading": cfg.live_trading,
        },
        "enabled_cities": enabled,
        "source_keys": [row["key"] for row in matrix],
        "city_matrix": city_matrix,
        "summary": {
            "sources": len(matrix),
            "healthy": sum(1 for row in matrix if row["status"] == "healthy"),
            "degraded": sum(1 for row in matrix if row["status"] == "degraded"),
            "stale": sum(1 for row in matrix if row["status"] == "stale"),
            "missing": sum(1 for row in matrix if row["status"] == "missing"),
            "required_blockers": len(required_bad),
            "optional_gaps": len(optional_bad),
        },
        "required_blockers": [row["key"] for row in required_bad],
        "sources": matrix,
    }


def _settlement_contract_source(station_rows) -> dict[str, Any]:
    expected = sorted(str(row["city_key"]) for row in station_rows)
    verified = sorted(
        str(row["city_key"])
        for row in station_rows
        if str(row["settlement_rule_verified_at"] or "").strip()
    )
    status_rows = {
        str(row["city_key"]): str(row["verification_status"] or "provisional")
        for row in station_rows
    }
    live_verified = sorted(city for city, status in status_rows.items() if status == "verified")
    mismatches = sorted(city for city, status in status_rows.items() if status == "settlement_mismatch")
    reasons: list[str] = []
    if len(verified) < len(expected):
        reasons.append("settlement_rule_unverified")
    if mismatches:
        reasons.append("settlement_station_mismatch")
    status = "healthy" if len(live_verified) == len(expected) else "degraded"
    return {
        "key": "settlement_contracts",
        "label": "Polymarket settlement contracts",
        "role": "station_and_resolution_gate",
        "required": True,
        "status": status,
        "reasons": reasons,
        "latest_at": None,
        "age_seconds": None,
        "sample_count": len(verified),
        "expected_cities": expected,
        "covered_cities": verified,
        "live_verified_cities": live_verified,
        "mismatch_cities": mismatches,
        "missing_cities": sorted(set(expected) - set(verified)),
        "coverage_pct": _coverage_pct(len(verified), len(expected)),
        "verification_status_by_city": status_rows,
        "errors_last_hour": 0,
    }


def compact_source_health(matrix: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": matrix.get("version"),
        "generated_at": matrix.get("generated_at"),
        "overall_status": matrix.get("overall_status"),
        "summary": matrix.get("summary") or {},
        "required_blockers": matrix.get("required_blockers") or [],
    }


def _freshness_source(
    conn,
    *,
    key: str,
    label: str,
    role: str,
    table: str,
    city_column: str,
    timestamp_column: str,
    where: str,
    expected_cities: Iterable[str],
    expected_interval_seconds: int,
    stale_after_seconds: int,
    required: bool,
    stages: tuple[str, ...],
    now: datetime,
    freshness_column: str | None = None,
    latest_order_by: str | None = None,
    recent_date_column: str | None = None,
    recent_date_cutoff: str | None = None,
) -> dict[str, Any]:
    expected = sorted(set(str(city) for city in expected_cities if city))
    health_column = freshness_column or timestamp_column
    order_by = latest_order_by or f"{timestamp_column} DESC"
    recent_sql = f" AND {recent_date_column} >= ?" if recent_date_column and recent_date_cutoff else ""
    by_city: dict[str, dict[str, Any]] = {}
    if expected:
        expected_values = ",".join("(?)" for _ in expected)
        recent_args: tuple[Any, ...] = (recent_date_cutoff,) if recent_sql else ()
        rows = conn.execute(
            f"""
            WITH expected(city) AS (VALUES {expected_values})
            SELECT source.{city_column} city,
                   source.{timestamp_column} latest_data_at,
                   {health_column} latest_at
            FROM expected
            JOIN {table} source
              ON source.id = (
                  SELECT id
                  FROM {table}
                  WHERE {city_column} = expected.city
                    AND ({where})
                    {recent_sql}
                  ORDER BY {order_by}, id DESC
                  LIMIT 1
              )
            """,
            (*expected, *recent_args),
        ).fetchall()
        by_city = {
            str(row["city"]): {**dict(row), "sample_count": 1}
            for row in rows
        }
    covered = sorted(by_city)
    latest = _latest_time_value(by_city[city]["latest_at"] for city in covered)
    latest_data = _latest_time_value(by_city[city]["latest_data_at"] for city in covered)
    sample_count = sum(int(by_city[city]["sample_count"] or 0) for city in covered)
    age_by_city = {
        city: _age_seconds(by_city[city]["latest_at"], now)
        for city in covered
    }
    data_age_by_city = {
        city: _age_seconds(by_city[city]["latest_data_at"], now)
        for city in covered
    }
    age_seconds = max((age for age in age_by_city.values() if age is not None), default=None)
    data_age_seconds = max((age for age in data_age_by_city.values() if age is not None), default=None)
    coverage_pct = _coverage_pct(len(covered), len(expected))
    errors = _errors_last_hour(conn, stages, now)
    status, reasons = _freshness_status(sample_count, age_seconds, stale_after_seconds, coverage_pct, errors)
    return {
        "key": key,
        "label": label,
        "role": role,
        "required": bool(required),
        "status": status,
        "reasons": reasons,
        "expected_interval_seconds": expected_interval_seconds,
        "stale_after_seconds": stale_after_seconds,
        "latest_at": latest or None,
        "age_seconds": age_seconds,
        "latest_data_at": latest_data or None,
        "data_age_seconds": data_age_seconds,
        "age_seconds_by_city": age_by_city,
        "data_age_seconds_by_city": data_age_by_city,
        "sample_count_by_city": {
            city: 1
            for city in covered
        },
        "sample_count_basis": "latest_state_presence",
        "stale_cities": sorted(
            city for city, age in age_by_city.items()
            if age is None or age > stale_after_seconds
        ),
        "sample_count": sample_count,
        "expected_cities": expected,
        "covered_cities": covered,
        "missing_cities": sorted(set(expected) - set(covered)),
        "coverage_pct": coverage_pct,
        "errors_last_hour": errors,
    }


def _daily_truth_source(
    conn,
    *,
    key: str,
    label: str,
    role: str,
    table: str,
    station_column: str,
    expected_station_rows,
    expected_cities: list[str],
    target_days: int,
    stages: tuple[str, ...],
    now: datetime,
    required: bool = True,
) -> dict[str, Any]:
    city_station = {
        str(row["city_key"]): str(row["settlement_station_id"] or row["station_id"] or "").upper()
        for row in expected_station_rows
        if str(row["city_key"]) in set(expected_cities)
    }
    station_city = {station: city for city, station in city_station.items() if station}
    rows = conn.execute(
        f"SELECT {station_column} station, COUNT(DISTINCT date_local) days, MAX(date_local) latest_date FROM {table} GROUP BY {station_column}"
    ).fetchall()
    by_city: dict[str, dict[str, Any]] = {}
    for row in rows:
        city = station_city.get(str(row["station"] or "").upper())
        if city:
            by_city[city] = dict(row)
    covered = sorted(by_city)
    days_by_city = {city: int(by_city.get(city, {}).get("days") or 0) for city in sorted(expected_cities)}
    minimum_days = min(days_by_city.values(), default=0)
    latest_date = max((str(row.get("latest_date") or "") for row in by_city.values()), default="")
    reasons: list[str] = []
    if not covered:
        status = "missing"
        reasons.append("no_truth_rows")
    elif len(covered) < len(expected_cities) or minimum_days < target_days:
        status = "degraded"
        if len(covered) < len(expected_cities):
            reasons.append("city_coverage_incomplete")
        if minimum_days < target_days:
            reasons.append("history_days_below_target")
    else:
        status = "healthy"
    errors = _errors_last_hour(conn, stages, now)
    if errors:
        reasons.append("recent_fetch_errors")
        if status == "healthy":
            status = "degraded"
    return {
        "key": key,
        "label": label,
        "role": role,
        "required": bool(required),
        "status": status,
        "reasons": reasons,
        "latest_at": latest_date or None,
        "age_seconds": None,
        "sample_count": sum(days_by_city.values()),
        "target_history_days": target_days,
        "minimum_history_days": minimum_days,
        "history_days_by_city": days_by_city,
        "expected_cities": sorted(expected_cities),
        "covered_cities": covered,
        "missing_cities": sorted(set(expected_cities) - set(covered)),
        "coverage_pct": _coverage_pct(len(covered), len(expected_cities)),
        "errors_last_hour": errors,
    }


def _hko_truth_source(conn, enabled_cities: set[str], target_days: int, now: datetime) -> dict[str, Any]:
    expected = ["hong-kong"] if "hong-kong" in enabled_cities else []
    row = conn.execute(
        "SELECT COUNT(DISTINCT date_local) days, MAX(date_local) latest_date FROM truth_hko_daily WHERE high_c IS NOT NULL"
    ).fetchone()
    days = int(row["days"] or 0)
    status = "healthy" if not expected or days >= target_days else ("missing" if days == 0 else "degraded")
    reasons = [] if status == "healthy" else ["history_days_below_target"]
    errors = _errors_last_hour(conn, ("truth_hko_daily",), now)
    if errors:
        reasons.append("recent_fetch_errors")
    return {
        "key": "truth_hko_daily",
        "label": "HKO Daily Extract",
        "role": "hong_kong_settlement_truth",
        "required": bool(expected),
        "status": status,
        "reasons": reasons,
        "latest_at": row["latest_date"] or None,
        "age_seconds": None,
        "sample_count": days,
        "target_history_days": target_days,
        "minimum_history_days": days,
        "history_days_by_city": {"hong-kong": days} if expected else {},
        "expected_cities": expected,
        "covered_cities": expected if days else [],
        "missing_cities": [] if days or not expected else expected,
        "coverage_pct": 100.0 if not expected or days else 0.0,
        "errors_last_hour": errors,
    }


def _orderbook_source(conn, enabled_cities: list[str], now: datetime, stale_after_seconds: int) -> dict[str, Any]:
    expected = sorted({
        str(row["city"])
        for row in conn.execute("SELECT DISTINCT city FROM market_buckets WHERE city IS NOT NULL AND city != ''")
        if str(row["city"]) in set(enabled_cities)
    })
    return _freshness_source(
        conn,
        key="polymarket_orderbook",
        label="Polymarket CLOB orderbook",
        role="market_price_and_execution_constraints",
        table="market_buckets",
        city_column="city",
        timestamp_column="quote_timestamp",
        freshness_column="updated_at",
        where="best_ask IS NOT NULL AND best_bid IS NOT NULL AND strict_match_status = 'matched'",
        expected_cities=expected,
        expected_interval_seconds=300,
        stale_after_seconds=max(60, stale_after_seconds),
        required=True,
        stages=("gamma_orderbook_poller", "structured_market_sync", "refresh_market_buckets"),
        now=now,
    )


def _freshness_status(
    sample_count: int,
    age_seconds: float | None,
    stale_after_seconds: int,
    coverage_pct: float,
    errors_last_hour: int,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if sample_count <= 0:
        return "missing", ["no_rows"]
    if age_seconds is None:
        reasons.append("latest_timestamp_unparseable")
    elif age_seconds > stale_after_seconds:
        reasons.append("source_stale")
    if coverage_pct < 100.0:
        reasons.append("city_coverage_incomplete")
    if errors_last_hour:
        reasons.append("recent_fetch_errors")
    if "source_stale" in reasons or "latest_timestamp_unparseable" in reasons:
        return "stale", reasons
    if reasons:
        return "degraded", reasons
    return "healthy", []


def _errors_last_hour(conn, stages: tuple[str, ...], now: datetime) -> int:
    if not stages:
        return 0
    placeholders = ",".join("?" for _ in stages)
    cutoff = (now - timedelta(hours=1)).isoformat()
    return int(conn.execute(
        f"""
        SELECT COUNT(*) FROM data_fetch_logs
        WHERE stage IN ({placeholders})
          AND created_at >= ?
          AND UPPER(COALESCE(status, '')) NOT IN ('OK', 'SUCCESS')
        """,
        (*stages, cutoff),
    ).fetchone()[0] or 0)


def _age_seconds(value: Any, now: datetime) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, round((now - parsed).total_seconds(), 3))


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        numeric = float(text)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coverage_pct(covered: int, expected: int) -> float:
    if expected <= 0:
        return 100.0
    return round(covered / expected * 100.0, 1)


def _latest_time_value(values: Iterable[Any]) -> str:
    parsed: list[tuple[datetime, str]] = []
    for value in values:
        timestamp = _parse_time(value)
        if timestamp is not None:
            parsed.append((timestamp, str(value)))
    return max(parsed, default=(datetime.min.replace(tzinfo=timezone.utc), ""))[1]


def _build_city_matrix(station_rows, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand source summaries into an operator-friendly city/source matrix."""
    result: list[dict[str, Any]] = []
    for station in station_rows:
        city = str(station["city_key"])
        verification_status = str(station["verification_status"] or "provisional")
        cells: dict[str, dict[str, Any]] = {}
        for source in sources:
            key = str(source["key"])
            expected = set(str(value) for value in source.get("expected_cities") or [])
            if city not in expected:
                cells[key] = {"status": "not_applicable", "reason": "city_not_in_source_scope"}
                continue
            if key == "settlement_contracts":
                status = "healthy" if verification_status == "verified" else (
                    "degraded" if verification_status == "settlement_mismatch" else "missing"
                )
                cells[key] = {
                    "status": status,
                    "verification_status": verification_status,
                    "verified_at": str(station["settlement_rule_verified_at"] or ""),
                }
                continue
            if "history_days_by_city" in source:
                days = int((source.get("history_days_by_city") or {}).get(city) or 0)
                target = int(source.get("target_history_days") or 0)
                cells[key] = {
                    "status": "healthy" if days >= target else ("degraded" if days else "missing"),
                    "history_days": days,
                    "target_history_days": target,
                }
                continue
            age = (source.get("age_seconds_by_city") or {}).get(city)
            data_age = (source.get("data_age_seconds_by_city") or {}).get(city)
            samples = int((source.get("sample_count_by_city") or {}).get(city) or 0)
            stale_after = int(source.get("stale_after_seconds") or 0)
            if samples <= 0:
                status = "missing"
            elif age is None or (stale_after and float(age) > stale_after):
                status = "stale"
            else:
                status = "healthy"
            cells[key] = {
                "status": status,
                "age_seconds": age,
                "data_age_seconds": data_age,
                "sample_count": samples,
                "stale_after_seconds": stale_after,
            }
        result.append({
            "city_key": city,
            "station_id": str(station["station_id"] or ""),
            "settlement_station_id": str(station["settlement_station_id"] or ""),
            "verification_status": verification_status,
            "settlement_rule_verified_at": str(station["settlement_rule_verified_at"] or ""),
            "live_gate_eligible": verification_status == "verified",
            "paper_only": verification_status == "settlement_mismatch",
            "sources": cells,
        })
    return result
