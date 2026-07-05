from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weatherbot_v3.config import load_config


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt_keep_zone(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "--"
    return str(value).replace("\n", " ").replace("|", "\\|")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(load_config().v3_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def build_report() -> str:
    now = _utc_now()
    since = now - timedelta(days=7)
    since_iso = since.isoformat()
    with _connect() as conn:
        runs = [dict(row) for row in conn.execute(
            "SELECT city, source, run_at, retrieved_at, target_date FROM forecast_runs WHERE COALESCE(retrieved_at, run_at, created_at) >= ?",
            (since_iso,),
        ).fetchall()]
        consensus = [dict(row) for row in conn.execute(
            "SELECT city, target_date, local_hour, valid_time, forecast_temp, forecast_source FROM hourly_consensus WHERE target_date >= ?",
            (since.date().isoformat(),),
        ).fetchall()]
        timezones = {
            row["city_key"]: row["timezone"]
            for row in conn.execute("SELECT city_key, timezone FROM stations").fetchall()
        }

    city_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        city_runs[str(row.get("city") or "")].append(row)
    run_rows = []
    for city, items in sorted(city_runs.items()):
        ages = []
        sources = Counter()
        for row in items:
            dt = _parse_dt(row.get("retrieved_at") or row.get("run_at"))
            if dt:
                ages.append((now - dt).total_seconds() / 3600)
            sources[str(row.get("source") or "unknown")] += 1
        run_rows.append([
            city,
            len(items),
            f"{min(ages):.1f}" if ages else "--",
            ", ".join(f"{k}:{v}" for k, v in sources.most_common(8)),
        ])
    stale_rows = [
        row for row in run_rows
        if row[2] == "--" or float(row[2]) > 24
    ]

    coverage_counter = Counter((row["city"], row["target_date"]) for row in consensus)
    coverage_rows = []
    for (city, target_date), count in sorted(coverage_counter.items()):
        coverage_rows.append([city, target_date, 24, count, f"{count / 24:.0%}" if count <= 24 else f"{count}/24"])
    incomplete_rows = [
        row for row in coverage_rows
        if int(row[3]) < 24
    ][:10]

    misaligned = []
    for row in consensus:
        local_hour = str(row.get("local_hour") or "")
        valid_time = str(row.get("valid_time") or "")
        if local_hour and valid_time:
            valid_dt = _parse_dt_keep_zone(valid_time)
            if valid_dt:
                city_tz = timezones.get(str(row.get("city") or ""))
                if city_tz:
                    try:
                        compare_dt = valid_dt.astimezone(ZoneInfo(city_tz))
                    except Exception:
                        compare_dt = valid_dt
                else:
                    compare_dt = valid_dt
                if compare_dt.strftime("%H:00") != local_hour[-5:]:
                    misaligned.append([row.get("city"), row.get("target_date"), local_hour, valid_time, row.get("forecast_source")])
        if row.get("forecast_temp") is not None and not row.get("forecast_source"):
            misaligned.append([row.get("city"), row.get("target_date"), local_hour, valid_time, "forecast_temp_without_source"])

    blockers = []
    if stale_rows:
        blockers.append(f"{len(stale_rows)} cities have no forecast run newer than 24h")
    incomplete = [row for row in coverage_rows if int(row[3]) < 24]
    if incomplete:
        blockers.append(f"{len(incomplete)} city/date consensus groups have fewer than 24 rows")
    if misaligned:
        blockers.append(f"{len(misaligned)} forecast/consensus timestamp alignment suspects")
    if not blockers:
        blockers.append("No Layer 3-4 blocker found")

    return f"""# Layer 3-4 Forecast And Hourly Consensus Verification

Generated: {now.isoformat()}
Window: last 7 days since {since_iso}

## Forecast Runs By City

{_table(["city", "run_count", "latest_age_hours", "source_distribution"], run_rows) if run_rows else "No forecast runs found in the last 7 days."}

## Stale Forecast Cities

{_table(["city", "run_count", "latest_age_hours", "source_distribution"], stale_rows) if stale_rows else "No stale forecast cities found."}

## Hourly Consensus Coverage

{_table(["city", "target_date", "expected_rows", "actual_rows", "coverage"], coverage_rows) if coverage_rows else "No hourly consensus rows found in the last 7 days."}

## Under-24 Hourly Consensus Groups

{_table(["city", "target_date", "expected_rows", "actual_rows", "coverage"], incomplete_rows) if incomplete_rows else "No under-24 consensus groups found."}

## Timestamp Alignment Suspects

{_table(["city", "target_date", "local_hour", "valid_time", "reason/source"], misaligned[:100]) if misaligned else "No timestamp alignment suspects found."}

## Layer Conclusion

- Can enter next layer: {"yes" if not stale_rows and not misaligned else "no"}
- Blockers:
{chr(10).join("  - " + item for item in blockers)}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "audits" / f"layer-verify-{_utc_now().date().isoformat()}"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "layer3_4_forecast.md"
    path.write_text(build_report(), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
