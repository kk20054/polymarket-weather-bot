from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request


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
    samples: list[dict[str, Any]] = []
    started_at = utc_now()
    start_payload: dict[str, Any] | None = None
    stop_payload: dict[str, Any] | None = None
    if start_scheduler:
        start_payload = fetch_json(f"{base_url.rstrip('/')}/api/scheduler/start", method="POST")
    deadline = time.monotonic() + max(0.0, duration_minutes) * 60.0
    while True:
        sample = collect_sample(base_url)
        samples.append(sample)
        write_json(output_dir / "samples.json", samples)
        if time.monotonic() >= deadline:
            break
        time.sleep(max(1.0, sample_seconds))
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
    (output_dir / "README.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def collect_sample(base_url: str) -> dict[str, Any]:
    root = base_url.rstrip("/")
    timestamp = utc_now()
    scheduler = fetch_json(f"{root}/api/scheduler/status")
    dashboard = fetch_json(f"{root}/api/dashboard")
    recommendations = (dashboard.get("recommendations") or {}) if isinstance(dashboard, dict) else {}
    city_series = dashboard.get("city_series") or [] if isinstance(dashboard, dict) else []
    return {
        "sampled_at": timestamp,
        "scheduler": compact_scheduler(scheduler),
        "recommendations": {
            "count": recommendations.get("count"),
            "empty_reason": recommendations.get("empty_reason"),
            "scheduler_running": recommendations.get("scheduler_running"),
        },
        "city_ages": city_age_snapshot(city_series),
    }


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
    for sample in samples:
        for key, row in ((sample.get("scheduler") or {}).get("pollers") or {}).items():
            poller_failures[key] = max(poller_failures.get(key, 0), int(row.get("fails_last_hour") or 0))
    return {
        "probe_version": "scheduler-longrun-probe-v1",
        "base_url": base_url,
        "started_at": started_at,
        "finished_at": finished_at,
        "samples": len(samples),
        "start_payload": start_payload,
        "stop_payload": stop_payload,
        "latest_pollers": latest_pollers,
        "max_fails_last_hour": poller_failures,
        "recommendation_counts": [sample.get("recommendations", {}).get("count") for sample in samples],
        "notes": [
            "This is an audit-only sampler. It does not import PolyWX or market data.",
            "Use --start-scheduler and --stop-scheduler when you want a self-contained run.",
        ],
    }


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
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
