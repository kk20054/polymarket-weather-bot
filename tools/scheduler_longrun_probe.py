from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weatherbot_v3.config import load_config


DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def fetch_json(url: str, *, method: str = "GET", timeout: float = 20.0) -> dict[str, Any]:
    req = request.Request(url, method=method)
    with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - local operator tool
        return json.loads(response.read().decode("utf-8"))


def run_probe(
    *,
    base_url: str = DEFAULT_BASE_URL,
    duration_minutes: float = 60.0,
    sample_seconds: float = 300.0,
    output_dir: Path,
    start_scheduler: bool = False,
    stop_scheduler: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    samples_jsonl = output_dir / "samples.jsonl"
    if samples_jsonl.exists():
        samples_jsonl.unlink()
    samples: list[dict[str, Any]] = []
    started_at = utc_now()
    start_payload: dict[str, Any] | None = None
    stop_payload: dict[str, Any] | None = None
    if start_scheduler:
        start_payload = fetch_json(f"{base_url.rstrip('/')}/api/scheduler/start", method="POST")
    deadline = time.monotonic() + max(0.0, duration_minutes) * 60.0
    try:
        while True:
            sample = collect_sample(base_url)
            samples.append(sample)
            append_jsonl(samples_jsonl, sample)
            write_json(output_dir / "samples.json", samples)
            if time.monotonic() >= deadline:
                break
            time.sleep(max(1.0, sample_seconds))
    finally:
        if stop_scheduler:
            stop_payload = fetch_json(f"{base_url.rstrip('/')}/api/scheduler/stop", method="POST")
    finished_at = utc_now()
    report = build_report(
        started_at=started_at,
        finished_at=finished_at,
        base_url=base_url,
        samples=samples,
        start_payload=start_payload,
        stop_payload=stop_payload,
    )
    write_json(output_dir / "summary.json", report)
    markdown = render_markdown(report)
    (output_dir / "summary.md").write_text(markdown, encoding="utf-8")
    (output_dir / "README.md").write_text(markdown, encoding="utf-8")
    return report


def collect_sample(base_url: str) -> dict[str, Any]:
    root = base_url.rstrip("/")
    timestamp = utc_now()
    scheduler = fetch_json(f"{root}/api/scheduler/status")
    dashboard = fetch_json(f"{root}/api/dashboard")
    recommendations = (dashboard.get("recommendations") or {}) if isinstance(dashboard, dict) else {}
    city_series = dashboard.get("city_series") or [] if isinstance(dashboard, dict) else []
    freshness = db_freshness_snapshot()
    skipped = recommendations.get("skipped") or recommendations.get("skip_reasons") or {}
    if isinstance(skipped, dict) and skipped:
        skip_reason_top1 = max(skipped.items(), key=lambda item: int(item[1] or 0))[0]
    else:
        skip_reason_top1 = recommendations.get("empty_reason") or None
    return {
        "sampled_at": timestamp,
        "scheduler": compact_scheduler(scheduler),
        "recommendations": {
            "count": recommendations.get("count"),
            "empty_reason": recommendations.get("empty_reason"),
            "scheduler_running": recommendations.get("scheduler_running"),
            "skip_reason_top1": skip_reason_top1,
            "skipped": skipped,
        },
        "metar_age_median_seconds": freshness.get("metar_age_median_seconds"),
        "metar_age_p95_seconds": freshness.get("metar_age_p95_seconds"),
        "forecast_age_median_seconds": freshness.get("forecast_age_median_seconds"),
        "forecast_age_p95_seconds": freshness.get("forecast_age_p95_seconds"),
        "recommendation_count": recommendations.get("count") or 0,
        "skip_reason_top1": skip_reason_top1,
        "freshness_by_city": freshness.get("by_city"),
        "city_ages": city_age_snapshot(city_series),
    }


def db_freshness_snapshot() -> dict[str, Any]:
    cfg = load_config()
    now = datetime.now(timezone.utc)
    by_city: list[dict[str, Any]] = []
    metar_ages: list[float] = []
    forecast_ages: list[float] = []
    try:
        with sqlite3.connect(cfg.v3_db_path) as conn:
            conn.row_factory = sqlite3.Row
            stations = conn.execute(
                """
                SELECT city_key, station_id
                FROM stations
                WHERE COALESCE(enabled, 0) = 1
                ORDER BY city_key
                """
            ).fetchall()
            for station in stations:
                city = station["city_key"]
                metar_at = conn.execute(
                    "SELECT MAX(report_time) FROM metar_reports WHERE city = ?",
                    (city,),
                ).fetchone()[0]
                forecast_at = conn.execute(
                    "SELECT MAX(COALESCE(retrieved_at, run_at, created_at)) FROM forecast_runs WHERE city = ?",
                    (city,),
                ).fetchone()[0]
                metar_age = age_seconds(now, metar_at)
                forecast_age = age_seconds(now, forecast_at)
                if metar_age is not None:
                    metar_ages.append(metar_age)
                if forecast_age is not None:
                    forecast_ages.append(forecast_age)
                by_city.append(
                    {
                        "city": city,
                        "station_id": station["station_id"],
                        "latest_metar_at": metar_at,
                        "metar_age_seconds": metar_age,
                        "latest_forecast_at": forecast_at,
                        "forecast_age_seconds": forecast_age,
                    }
                )
    except Exception as exc:
        return {"error": str(exc), "by_city": []}
    return {
        "metar_age_median_seconds": percentile(metar_ages, 50),
        "metar_age_p95_seconds": percentile(metar_ages, 95),
        "forecast_age_median_seconds": percentile(forecast_ages, 50),
        "forecast_age_p95_seconds": percentile(forecast_ages, 95),
        "by_city": by_city,
    }


def age_seconds(now: datetime, value: Any) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.fromisoformat(text.replace(" ", "T"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds())


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * (p / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def compact_scheduler(payload: dict[str, Any]) -> dict[str, Any]:
    pollers = payload.get("pollers") or {}
    return {
        "running": payload.get("running"),
        "message": payload.get("message"),
        "pollers": {
            key: {
                "last_run_at": row.get("last_run_at"),
                "age_seconds": row.get("age_seconds"),
                "last_duration_ms": row.get("last_duration_ms"),
                "fails_last_hour": row.get("fails_last_hour"),
                "next_run_at": row.get("next_run_at"),
                "last_status": row.get("last_status"),
                "last_message": row.get("last_message"),
            }
            for key, row in pollers.items()
            if isinstance(row, dict)
        },
    }


def city_age_snapshot(city_series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for city in city_series:
        rows.append({
            "city_key": city.get("city_key"),
            "station_id": city.get("station_id"),
            "latest_timestamp": city.get("latest_timestamp"),
            "last_refreshed_at": city.get("last_refreshed_at"),
            "hourly_count": city.get("hourly_count"),
            "forecast_count": city.get("forecast_count"),
            "history_count": city.get("history_count"),
        })
    return rows


def build_report(
    *,
    started_at: str,
    finished_at: str,
    base_url: str,
    samples: list[dict[str, Any]],
    start_payload: dict[str, Any] | None,
    stop_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    poller_failures: dict[str, int] = {}
    latest_pollers = (samples[-1].get("scheduler") or {}).get("pollers") if samples else {}
    forecast_runs = [
        sample.get("scheduler", {}).get("pollers", {}).get("forecast_poller", {}).get("last_run_at")
        for sample in samples
    ]
    forecast_cycles = len({value for value in forecast_runs if value})
    recommendation_curve = [
        {
            "sampled_at": sample.get("sampled_at"),
            "recommendation_count": sample.get("recommendation_count"),
        }
        for sample in samples
    ]
    skip_counts = Counter(
        sample.get("skip_reason_top1")
        for sample in samples
        if sample.get("skip_reason_top1")
    )
    for sample in samples:
        for key, row in ((sample.get("scheduler") or {}).get("pollers") or {}).items():
            poller_failures[key] = max(poller_failures.get(key, 0), int(row.get("fails_last_hour") or 0))
    metar_median_values = [
        sample.get("metar_age_median_seconds")
        for sample in samples
        if sample.get("metar_age_median_seconds") is not None
    ]
    metar_p95_values = [
        sample.get("metar_age_p95_seconds")
        for sample in samples
        if sample.get("metar_age_p95_seconds") is not None
    ]
    forecast_median_values = [
        sample.get("forecast_age_median_seconds")
        for sample in samples
        if sample.get("forecast_age_median_seconds") is not None
    ]
    return {
        "probe_version": "scheduler-longrun-probe-v2",
        "base_url": base_url,
        "started_at": started_at,
        "finished_at": finished_at,
        "samples": len(samples),
        "start_payload": start_payload,
        "stop_payload": stop_payload,
        "latest_pollers": latest_pollers,
        "forecast_poller_full_cycles_observed": forecast_cycles,
        "metar_age_median_seconds": percentile(metar_median_values, 50),
        "metar_age_p95_seconds": percentile(metar_p95_values, 95),
        "forecast_age_median_seconds": percentile(forecast_median_values, 50),
        "max_fails_last_hour": poller_failures,
        "recommendation_counts": [sample.get("recommendation_count") for sample in samples],
        "recommendation_curve": recommendation_curve,
        "skip_reason_top1_counts": dict(skip_counts),
        "metar_stale_or_missing_repeated": skip_counts.get("metar_stale_or_missing", 0) > 1,
        "metar_stale_or_missing_root_cause": infer_metar_stale_root_cause(samples),
        "notes": [
            "This is an audit-only sampler. It does not import PolyWX or market data.",
            "Use --start-scheduler and --stop-scheduler when you want a self-contained run.",
            "Age metrics are computed from the local v3 SQLite DB for enabled stations.",
        ],
    }


def infer_metar_stale_root_cause(samples: list[dict[str, Any]]) -> str:
    stale_samples = [sample for sample in samples if sample.get("skip_reason_top1") == "metar_stale_or_missing"]
    if not stale_samples:
        return "metar_stale_or_missing was not the top skip reason in sampled dashboard payloads."
    latest = stale_samples[-1]
    ages = [
        row.get("metar_age_seconds")
        for row in latest.get("freshness_by_city") or []
        if row.get("metar_age_seconds") is not None
    ]
    if not ages:
        return "No latest METAR timestamps were present for enabled stations."
    if min(ages) > 30 * 60:
        return "Latest METAR rows for enabled stations were older than the 30 minute recommendation gate."
    return "At least one enabled station had fresh METAR; inspect target_date/lead-time gates for D+1/D+2 decisions."


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Scheduler Longrun Probe",
        "",
        f"- Base URL: `{report['base_url']}`",
        f"- Started: `{report['started_at']}`",
        f"- Finished: `{report['finished_at']}`",
        f"- Samples: `{report['samples']}`",
        "",
        "## Latest Pollers",
        "",
        "| Poller | Last Run | Age(s) | Duration(ms) | Fail/hr | Status |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for key, row in (report.get("latest_pollers") or {}).items():
        lines.append(
            f"| {key} | {row.get('last_run_at') or '--'} | {row.get('age_seconds') or '--'} | "
            f"{row.get('last_duration_ms') or '--'} | {row.get('fails_last_hour') or 0} | {row.get('last_status') or '--'} |"
        )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in report.get("notes") or [])
    lines.extend(
        [
            "",
            "## Required Answers",
            "",
            f"- Forecast poller full cycles observed: `{report.get('forecast_poller_full_cycles_observed')}`",
            f"- METAR age median seconds: `{report.get('metar_age_median_seconds')}`",
            f"- METAR age P95 seconds: `{report.get('metar_age_p95_seconds')}`",
            f"- Forecast age median seconds: `{report.get('forecast_age_median_seconds')}`",
            f"- Recommendation count curve: `{json.dumps(report.get('recommendation_curve'), ensure_ascii=False)}`",
            f"- Top skip reason counts: `{json.dumps(report.get('skip_reason_top1_counts'), ensure_ascii=False)}`",
            f"- metar_stale_or_missing repeated: `{report.get('metar_stale_or_missing_repeated')}`",
            f"- metar_stale_or_missing root cause: {report.get('metar_stale_or_missing_root_cause')}",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample WeatherBot scheduler freshness over time.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--duration-minutes", type=float, default=60.0)
    parser.add_argument("--sample-seconds", type=float, default=300.0)
    parser.add_argument("--output-dir", default=f"audits/scheduler-longrun-{datetime.now().date().isoformat()}")
    parser.add_argument("--start-scheduler", action="store_true")
    parser.add_argument("--stop-scheduler", action="store_true")
    args = parser.parse_args()
    report = run_probe(
        base_url=args.base_url,
        duration_minutes=args.duration_minutes,
        sample_seconds=args.sample_seconds,
        output_dir=Path(args.output_dir),
        start_scheduler=args.start_scheduler,
        stop_scheduler=args.stop_scheduler,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
