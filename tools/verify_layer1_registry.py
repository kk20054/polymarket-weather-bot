from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weatherbot_v3.config import load_config
from weatherbot_v3.registry import SETTLEMENT_REGISTRY


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _probe_advice(verified_at: Any, status: str) -> str:
    if status == "verified" and verified_at:
        parsed = _parse_dt(verified_at)
        if parsed:
            age_days = (_utc_now() - parsed).total_seconds() / 86400
            if age_days <= 7:
                return "no immediate probe; recheck in 7 days"
            return "refresh probe within next maintenance window"
    if status == "settlement_mismatch":
        return "manual review before next live-gate change"
    if status == "no_active_market":
        return "retry polymarket-market-probe when a new daily event is listed"
    return "run polymarket-market-probe in next verification round"


def build_report() -> str:
    with _connect() as conn:
        station_rows = {
            row["city_key"]: dict(row)
            for row in conn.execute("SELECT * FROM stations ORDER BY city_key").fetchall()
        }

    rows: list[list[Any]] = []
    diff_rows: list[list[Any]] = []
    unverified_rows: list[list[Any]] = []
    p0: list[str] = []

    for city, profile in sorted(SETTLEMENT_REGISTRY.items()):
        db = station_rows.get(city, {})
        status = str(db.get("verification_status") or profile.verification_status or "provisional")
        verified_at = db.get("settlement_rule_verified_at") or ""
        rows.append(
            [
                city,
                profile.station_id,
                profile.timezone,
                profile.unit,
                db.get("settlement_station_id") or db.get("station_id"),
                db.get("settlement_timezone") or db.get("timezone"),
                db.get("settlement_unit") or db.get("unit"),
                status,
                verified_at,
            ]
        )
        comparisons = [
            ("station_id", profile.station_id, db.get("station_id")),
            ("settlement_station_id", profile.station_id, db.get("settlement_station_id") or db.get("station_id")),
            ("timezone", profile.timezone, db.get("timezone")),
            ("settlement_timezone", profile.timezone, db.get("settlement_timezone") or db.get("timezone")),
            ("unit", profile.unit, db.get("unit")),
            ("settlement_unit", profile.unit, db.get("settlement_unit") or db.get("unit")),
        ]
        for field, registry_value, db_value in comparisons:
            if str(registry_value or "").strip() != str(db_value or "").strip():
                diff_rows.append([city, field, registry_value, db_value])
        if status != "verified" or not verified_at:
            unverified_rows.append([city, status, verified_at, _probe_advice(verified_at, status)])
        if not db:
            p0.append(f"{city}: registry city missing from stations table")

    missing_in_registry = sorted(set(station_rows) - set(SETTLEMENT_REGISTRY))
    for city in missing_in_registry:
        diff_rows.append([city, "extra_station_row", "--", "present in stations only"])

    can_continue = "yes" if not p0 else "no"
    blockers = p0 or [f"{len(unverified_rows)} cities are not fully verified; live gates must remain blocked for them"]

    return f"""# Layer 1 Registry Verification

Generated: {_utc_now().isoformat()}

## Registry vs Stations

{_table(["city", "registry station", "registry tz", "registry unit", "db settlement station", "db settlement tz", "db settlement unit", "status", "verified_at"], rows)}

## Diff

{_table(["city", "field", "registry", "stations table"], diff_rows) if diff_rows else "No registry/table diffs found."}

## Unverified Cities And Probe Advice

{_table(["city", "status", "verified_at", "next probe advice"], unverified_rows) if unverified_rows else "All registry cities are verified in the stations table."}

## P0 Findings

{chr(10).join("- " + item for item in p0) if p0 else "None."}

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
    (out_dir / "layer1_registry.md").write_text(build_report(), encoding="utf-8")
    print(out_dir / "layer1_registry.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
