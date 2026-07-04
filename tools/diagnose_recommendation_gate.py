from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weatherbot_v3.config import load_config
from weatherbot_v3.db import connect, init_v3_db


DEFAULT_CITIES = ["chicago", "tokyo", "atlanta", "nyc", "dallas", "shanghai", "hong-kong"]
METAR_FRESH_SECONDS = 30 * 60
FORECAST_FRESH_SECONDS = 90 * 60


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_seconds(value: Any, now: datetime) -> float | None:
    parsed = _parse_iso(value)
    if not parsed:
        return None
    return max(0.0, (now - parsed).total_seconds())


def _json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(str(value))
        except Exception:
            raw = [value]
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [str(item).strip() for item in raw if str(item or "").strip()]


def _reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    primary = str(row.get("blocked_reason_primary") or "").strip()
    if primary:
        reasons.append(primary)
    for reason in _json_list(row.get("gate_reasons_json")):
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _paper_or_spread_watch(row: dict[str, Any]) -> bool:
    if bool(row.get("paper_allowed")):
        return True
    ignored = {"live_trading_disabled", "paper_gate_not_passed", "insufficient_bias_samples"}
    blocking = {reason for reason in _reasons(row) if reason not in ignored}
    return bool(blocking) and blocking <= {"spread_too_wide"}


def _local_today(station: dict[str, Any], now: datetime) -> str:
    tz_name = str(station.get("settlement_timezone") or station.get("timezone") or "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    return now.astimezone(tz).date().isoformat()


def _latest_by_city(rows: list[dict[str, Any]], city_key: str, time_key: str) -> dict[str, Any] | None:
    city_rows = [row for row in rows if str(row.get("city") or row.get("city_key") or "").lower() == city_key]
    if not city_rows:
        return None
    return max(city_rows, key=lambda row: str(row.get(time_key) or ""))


def _latest_forecast_age(conn, city: str, target_date: str, now: datetime) -> tuple[str | None, float | None]:
    row = conn.execute(
        """
        SELECT COALESCE(retrieved_at, run_at, created_at) AS ts, source
        FROM forecast_runs
        WHERE city = ? AND target_date = ?
        ORDER BY COALESCE(retrieved_at, run_at, created_at) DESC, id DESC
        LIMIT 1
        """,
        (city, target_date),
    ).fetchone()
    if not row:
        return None, None
    ts = row["ts"]
    return ts, _age_seconds(ts, now)


def _decision_class(target_date: str, local_today: str) -> str:
    if not target_date:
        return "unknown"
    return "today_observation" if target_date <= local_today else "forecast_lead"


def diagnose(cities: list[str], *, path: Path | None = None) -> dict[str, Any]:
    init_v3_db(path)
    now = datetime.now(timezone.utc)
    dashboard_today = date.today().isoformat()
    result: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "dashboard_today": dashboard_today,
        "metar_fresh_seconds": METAR_FRESH_SECONDS,
        "forecast_fresh_seconds": FORECAST_FRESH_SECONDS,
        "cities": [],
        "summary": {},
    }
    horizon_mismatch_candidates = 0
    stale_collector_candidates = 0
    filter_counter: Counter[str] = Counter()
    stored_gate_counter: Counter[str] = Counter()

    with connect(path) as conn:
        station_rows = {
            str(row["city_key"]): dict(row)
            for row in conn.execute("SELECT * FROM stations").fetchall()
        }
        metar_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT city, station_id, report_time, temperature, raw_text
                FROM metar_reports
                WHERE city IS NOT NULL AND TRIM(city) != ''
                ORDER BY report_time DESC, id DESC
                LIMIT 1000
                """
            ).fetchall()
        ]
        decision_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    sd.*,
                    mb.strict_match_status AS market_strict_match_status,
                    mb.event_url AS bucket_event_url
                FROM signal_decisions sd
                LEFT JOIN market_buckets mb ON mb.bucket_key = sd.bucket_key
                WHERE sd.city_key IN ({placeholders})
                  AND sd.target_date >= ?
                ORDER BY sd.city_key, sd.target_date, sd.issued_at DESC, sd.edge DESC, sd.id DESC
                """.format(placeholders=",".join("?" for _ in cities)),
                tuple(cities) + (dashboard_today,),
            ).fetchall()
        ] if cities else []

        latest_issued: dict[tuple[str, str], str] = {}
        for row in decision_rows:
            key = (str(row.get("city_key") or ""), str(row.get("target_date") or ""))
            if not key[0] or not key[1]:
                continue
            latest_issued.setdefault(key, str(row.get("issued_at") or ""))

        latest_rows = [
            row
            for row in decision_rows
            if str(row.get("issued_at") or "") == latest_issued.get((str(row.get("city_key") or ""), str(row.get("target_date") or "")))
        ]

        by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in latest_rows:
            by_city[str(row.get("city_key") or "")].append(row)

        for city in cities:
            station = station_rows.get(city, {})
            local_today = _local_today(station, now)
            metar = _latest_by_city(metar_rows, city, "report_time")
            metar_ts = metar.get("report_time") if metar else None
            metar_age = _age_seconds(metar_ts, now)
            city_filter_counter: Counter[str] = Counter()
            city_stored_counter: Counter[str] = Counter()
            decisions_out: list[dict[str, Any]] = []

            for row in by_city.get(city, []):
                target_date = str(row.get("target_date") or "")
                decision_type = _decision_class(target_date, local_today)
                forecast_ts, forecast_age = _latest_forecast_age(conn, city, target_date, now)
                station_verified = bool(str(station.get("settlement_rule_verified_at") or "").strip())
                strict_match = str(row.get("market_strict_match_status") or "") == "matched"
                paper_or_spread = _paper_or_spread_watch(row)
                metar_fresh = metar_age is not None and metar_age < METAR_FRESH_SECONDS
                forecast_fresh = forecast_age is not None and forecast_age < FORECAST_FRESH_SECONDS
                stored = _reasons(row)
                city_stored_counter.update(stored)
                stored_gate_counter.update(stored)

                filters = []
                if decision_type == "today_observation" and not metar_fresh:
                    filters.append("metar_stale_or_missing")
                if decision_type == "forecast_lead" and not forecast_fresh:
                    filters.append("forecast_stale_or_missing")
                if not station_verified:
                    filters.append("settlement_unverified")
                if not strict_match:
                    filters.append("bucket_not_strict_match")
                if not paper_or_spread:
                    filters.append("paper_gate_blocked")
                city_filter_counter.update(filters)
                filter_counter.update(filters)

                if decision_type == "forecast_lead" and not metar_fresh and forecast_fresh and station_verified and strict_match and paper_or_spread:
                    horizon_mismatch_candidates += 1
                if decision_type == "today_observation" and not metar_fresh and station_verified and strict_match and paper_or_spread:
                    stale_collector_candidates += 1

                decisions_out.append({
                    "target_date": target_date,
                    "issued_at": row.get("issued_at"),
                    "decision_type": decision_type,
                    "forecast_time": forecast_ts,
                    "forecast_age_seconds": forecast_age,
                    "forecast_fresh": forecast_fresh,
                    "metar_fresh": metar_fresh,
                    "paper_allowed": bool(row.get("paper_allowed")),
                    "edge": row.get("edge"),
                    "stored_gate_reasons": stored[:5],
                    "recommendation_filter_reasons": filters,
                })

            result["cities"].append({
                "city_key": city,
                "station_id": station.get("station_id"),
                "settlement_rule_verified_at": station.get("settlement_rule_verified_at"),
                "local_today": local_today,
                "latest_metar_report_time": metar_ts,
                "latest_metar_age_seconds": metar_age,
                "latest_metar_fresh": metar_age is not None and metar_age < METAR_FRESH_SECONDS,
                "stored_gate_reasons_top3": city_stored_counter.most_common(3),
                "recommendation_filter_reasons_top3": city_filter_counter.most_common(3),
                "decision_count": len(decisions_out),
                "decisions": sorted(
                    decisions_out,
                    key=lambda item: (item["target_date"], item.get("issued_at") or "", item.get("edge") or 0),
                    reverse=True,
                )[:6],
            })

    verdict = "horizon_gate_mismatch" if horizon_mismatch_candidates > stale_collector_candidates else "collector_stale_or_missing"
    result["summary"] = {
        "verdict": verdict,
        "horizon_mismatch_candidates": horizon_mismatch_candidates,
        "stale_collector_candidates": stale_collector_candidates,
        "stored_gate_reasons_top3": stored_gate_counter.most_common(3),
        "recommendation_filter_reasons_top3": filter_counter.most_common(3),
    }
    return result


def _write_audit(payload: dict[str, Any], audit_dir: Path) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "diagnosis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Recommendation Gate Diagnosis",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- verdict: `{payload['summary']['verdict']}`",
        f"- horizon_mismatch_candidates: `{payload['summary']['horizon_mismatch_candidates']}`",
        f"- stale_collector_candidates: `{payload['summary']['stale_collector_candidates']}`",
        f"- recommendation_filter_top3: `{payload['summary']['recommendation_filter_reasons_top3']}`",
        "",
        "| city | latest METAR | age min | fresh | decisions | filter top3 |",
        "| --- | --- | ---: | --- | ---: | --- |",
    ]
    for city in payload["cities"]:
        age = city.get("latest_metar_age_seconds")
        age_min = "" if age is None else f"{age / 60.0:.1f}"
        lines.append(
            "| {city_key} | {metar} | {age_min} | {fresh} | {count} | {top3} |".format(
                city_key=city["city_key"],
                metar=city.get("latest_metar_report_time") or "",
                age_min=age_min,
                fresh="yes" if city.get("latest_metar_fresh") else "no",
                count=city.get("decision_count", 0),
                top3=city.get("recommendation_filter_reasons_top3"),
            )
        )
    (audit_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose why WeatherBot recommendations are empty.")
    parser.add_argument("--city", action="append", dest="cities", help="City key to inspect. Repeatable.")
    parser.add_argument("--db-path", default="", help="Override V3 database path.")
    parser.add_argument("--audit-dir", default="", help="Directory for diagnosis.json and SUMMARY.md.")
    parser.add_argument("--no-audit", action="store_true", help="Print only; do not write audits.")
    args = parser.parse_args()

    cfg = load_config()
    db_path = Path(args.db_path) if args.db_path else cfg.v3_db_path
    cities = [str(city).strip().lower() for city in (args.cities or DEFAULT_CITIES) if str(city).strip()]
    payload = diagnose(cities, path=db_path)
    if not args.no_audit:
        audit_dir = Path(args.audit_dir) if args.audit_dir else ROOT / "audits" / f"recommendation-gate-diagnosis-{date.today().isoformat()}"
        _write_audit(payload, audit_dir)
        payload["audit_dir"] = str(audit_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
