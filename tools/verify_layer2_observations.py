from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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


def _age_bucket(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes <= 30:
        return "<=30m"
    if minutes <= 60:
        return "31-60m"
    if minutes <= 180:
        return "61-180m"
    if minutes <= 360:
        return "181-360m"
    return ">360m"


def _grade(row_count: int, missing_hours: int, latest_age_min: float | None) -> str:
    if row_count >= 18 and missing_hours <= 6 and latest_age_min is not None and latest_age_min <= 90:
        return "A"
    if row_count >= 8 and missing_hours <= 14 and latest_age_min is not None and latest_age_min <= 360:
        return "B"
    return "C"


def _hour_floor(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def _missing_hour_intervals(start: datetime, end: datetime, present_hours: set[str]) -> list[str]:
    missing: list[datetime] = []
    cursor = _hour_floor(end - timedelta(hours=23))
    final = _hour_floor(end)
    while cursor <= final:
        key = cursor.strftime("%Y-%m-%dT%H")
        if key not in present_hours:
            missing.append(cursor)
        cursor += timedelta(hours=1)
    if not missing:
        return []

    intervals: list[str] = []
    block_start = missing[0]
    previous = missing[0]
    for item in missing[1:]:
        if item - previous == timedelta(hours=1):
            previous = item
            continue
        intervals.append(f"{block_start.strftime('%m-%d %H:00')}..{previous.strftime('%H:00')}")
        block_start = previous = item
    intervals.append(f"{block_start.strftime('%m-%d %H:00')}..{previous.strftime('%H:00')}")
    return intervals


def _looks_like_fake_metar(raw_json: str | None, network: str) -> bool:
    if network.lower() in {"metar", "iem_asos", "asos_metar"}:
        return True
    if not raw_json:
        return False
    try:
        raw = json.loads(raw_json)
    except Exception:
        return False
    flags = json.dumps(raw, sort_keys=True).lower()
    return "metar" in flags and network.lower() not in {"china_live", "wunderground_pws", "open_meteo_historical"}


def build_report() -> str:
    now = _utc_now()
    since = now - timedelta(hours=24)
    since_iso = since.isoformat()

    with _connect() as conn:
        cities = [row["city_key"] for row in conn.execute("SELECT city_key FROM stations ORDER BY city_key").fetchall()]
        metar = [dict(row) for row in conn.execute(
            "SELECT city, station_id, report_time, parse_status FROM metar_reports WHERE report_time >= ?",
            (since_iso,),
        ).fetchall()]
        mesonet = [dict(row) for row in conn.execute(
            "SELECT city, station_id, network, observed_at, raw_json, parse_status FROM mesonet_observations WHERE observed_at >= ?",
            (since_iso,),
        ).fetchall()]

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metar:
        by_city[str(row.get("city") or "")].append(row)

    metar_rows: list[list[Any]] = []
    missing_interval_rows: list[list[Any]] = []
    health_rows: list[list[Any]] = []
    p0: list[str] = []
    for city in cities:
        rows = by_city.get(city, [])
        ages: list[float] = []
        hours = set()
        parse_status = Counter()
        for row in rows:
            dt = _parse_dt(row.get("report_time"))
            if dt:
                ages.append((now - dt).total_seconds() / 60)
                hours.add(dt.strftime("%Y-%m-%dT%H"))
            parse_status[str(row.get("parse_status") or "unknown")] += 1
        latest_age = min(ages) if ages else None
        age_dist = Counter(_age_bucket(age) for age in ages)
        missing_intervals = _missing_hour_intervals(since, now, hours)
        missing_hours = max(0, 24 - len(hours))
        grade = _grade(len(rows), missing_hours, latest_age)
        metar_rows.append([
            city,
            len(rows),
            f"{latest_age:.1f}" if latest_age is not None else "--",
            missing_hours,
            ", ".join(f"{k}:{v}" for k, v in sorted(age_dist.items())) or "--",
            ", ".join(f"{k}:{v}" for k, v in sorted(parse_status.items())) or "--",
        ])
        missing_interval_rows.append([city, missing_hours, "; ".join(missing_intervals) or "--"])
        health_rows.append([city, grade, len(rows), missing_hours, f"{latest_age:.1f}" if latest_age is not None else "--"])
        if grade == "C":
            p0.append(f"{city}: Layer 2 health C in last 24h")

    mesonet_counter = Counter((str(row.get("city") or ""), str(row.get("network") or "")) for row in mesonet)
    mesonet_rows = [[city, network, count] for (city, network), count in sorted(mesonet_counter.items())]
    fake = [row for row in mesonet if _looks_like_fake_metar(row.get("raw_json"), str(row.get("network") or ""))]
    fake_rows = [[row.get("city"), row.get("station_id"), row.get("network"), row.get("observed_at")] for row in fake[:20]]

    can_continue = "yes" if not fake and any(row[1] in {"A", "B"} for row in health_rows) else "no"
    blockers = []
    if fake:
        blockers.append(f"{len(fake)} mesonet rows appear to masquerade as METAR")
    blockers.extend(p0[:10])
    if not blockers:
        blockers.append("No P0 blocker; C-grade cities need data refresh before strategy reliance")

    return f"""# Layer 2 Observations Verification

Generated: {now.isoformat()}
Window: last 24h since {since_iso}

## METAR Health By City

{_table(["city", "rows", "latest_age_min", "missing_hours", "age_distribution", "parse_status"], metar_rows)}

## Missing Hour Intervals By City

{_table(["city", "missing_hours", "missing_hour_intervals_utc"], missing_interval_rows)}

## Mesonet By Network

{_table(["city", "network", "rows"], mesonet_rows) if mesonet_rows else "No mesonet rows in the last 24h."}

## METAR Masquerade Check

- Suspect rows: {len(fake)}

{_table(["city", "station", "network", "observed_at"], fake_rows) if fake_rows else "No mesonet rows appear to masquerade as METAR."}

## Health Score

{_table(["city", "score", "rows", "missing_hours", "latest_age_min"], health_rows)}

## P0 Findings

{chr(10).join("- " + item for item in ([f"{len(fake)} fake METAR suspect rows"] if fake else [])) if fake else "None."}

## Layer Conclusion

- Can enter next layer: {can_continue}
- Blockers:
{chr(10).join("  - " + item for item in blockers)}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "audits" / f"layer-verify-{_utc_now().date().isoformat()}"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "layer2_observations.md"
    path.write_text(build_report(), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
