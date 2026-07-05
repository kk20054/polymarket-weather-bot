from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weatherbot_v3.config import load_config


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "--"
    return str(value).replace("\n", " ").replace("|", "\\|")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return "\n".join(lines)


def build_report() -> str:
    conn = sqlite3.connect(load_config().v3_db_path)
    conn.row_factory = sqlite3.Row
    try:
        latest = conn.execute("SELECT MAX(updated_at) AS ts FROM market_buckets").fetchone()["ts"]
        rows = [dict(row) for row in conn.execute("SELECT * FROM market_buckets").fetchall()]
    finally:
        conn.close()

    status = Counter(str(row.get("strict_match_status") or "unknown") for row in rows)
    status_rows = [[k, v] for k, v in sorted(status.items())]
    unmatched = [row for row in rows if str(row.get("strict_match_status") or "") != "matched"]
    examples = [
        [row.get("city"), row.get("target_date"), row.get("bucket_label"), row.get("question"), row.get("strict_match_reasons")]
        for row in unmatched[:25]
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in unmatched:
        key = (str(row.get("city") or "--"), str(row.get("target_date") or "--"))
        if len(grouped[key]) < 5:
            grouped[key].append(row)
    grouped_rows = []
    for (city, target_date), items in sorted(grouped.items()):
        total = sum(1 for row in unmatched if str(row.get("city") or "--") == city and str(row.get("target_date") or "--") == target_date)
        examples_text = "; ".join(
            f"{item.get('bucket_label') or item.get('bucket_key')}: {item.get('strict_match_reasons') or 'unknown'}"
            for item in items
        )
        grouped_rows.append([city, target_date, total, examples_text])
    reason_counts = Counter()
    for row in unmatched:
        text = str(row.get("strict_match_reasons") or "unknown")
        for part in text.replace("[", "").replace("]", "").replace('"', "").split(","):
            part = part.strip() or "unknown"
            reason_counts[part] += 1
    missing_token = sum(1 for row in rows if not (row.get("token_id") or row.get("yes_token_id")))
    missing_tick = sum(1 for row in rows if row.get("tick_size") in (None, "", 0))
    missing_min = sum(1 for row in rows if row.get("order_min_size") in (None, "", 0))
    p0 = []
    if rows and missing_token == len(rows):
        p0.append("all latest market_buckets are missing token ids")
    if rows and status.get("matched", 0) == 0:
        p0.append("no latest market_buckets have strict_match_status=matched")
    blockers = p0 or [
        f"unmatched rows: {len(unmatched)}",
        f"missing token rows: {missing_token}",
        f"missing tick rows: {missing_tick}",
        f"missing orderMinSize rows: {missing_min}",
    ]

    return f"""# Layer 5 Market Buckets Verification

Generated: {_utc_now().isoformat()}
Latest updated_at considered: {_cell(latest)}
Rows considered: {len(rows)} current rows in `market_buckets`

## strict_match_status Distribution

{_table(["status", "rows"], status_rows) if status_rows else "No market bucket rows found."}

## Unmatched Reason Top10

{_table(["reason", "rows"], [[k, v] for k, v in reason_counts.most_common(10)]) if reason_counts else "No unmatched reasons."}

## Unmatched Examples

{_table(["city", "target_date", "bucket", "question", "reasons"], examples) if examples else "No unmatched examples."}

## Unmatched Examples By City/Date

{_table(["city", "target_date", "unmatched_rows", "examples_max_5"], grouped_rows) if grouped_rows else "No grouped unmatched examples."}

## Required Trading Metadata Gaps

{_table(["check", "rows"], [["empty token_id/yes_token_id", missing_token], ["missing tick_size", missing_tick], ["missing order_min_size", missing_min]])}

## P0 Findings

{chr(10).join("- " + item for item in p0) if p0 else "None."}

## Layer Conclusion

- Can enter next layer: {"yes" if not p0 else "no"}
- Blockers:
{chr(10).join("  - " + item for item in blockers)}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "audits" / f"layer-verify-{_utc_now().date().isoformat()}"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "layer5_market_buckets.md"
    path.write_text(build_report(), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
