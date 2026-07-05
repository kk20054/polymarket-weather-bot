from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
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


def _json_list(text: Any) -> list[str]:
    if not text:
        return []
    try:
        value = json.loads(str(text))
    except Exception:
        return [str(text)]
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        reasons = value.get("reasons") or value.get("gate_reasons") or value.values()
        if isinstance(reasons, list):
            return [str(item) for item in reasons]
    return [str(value)]


def build_report() -> str:
    conn = sqlite3.connect(load_config().v3_db_path)
    conn.row_factory = sqlite3.Row
    try:
        latest100 = [dict(row) for row in conn.execute(
            "SELECT * FROM signal_decisions ORDER BY COALESCE(issued_at, updated_at) DESC, id DESC LIMIT 100"
        ).fetchall()]
        all_rows = [dict(row) for row in conn.execute("SELECT strategy_name, paper_allowed, live_allowed FROM signal_decisions").fetchall()]
    finally:
        conn.close()

    reason_counts = Counter()
    reason_samples: dict[str, list[str]] = {}
    for row in latest100:
        reasons = _json_list(row.get("gate_reasons_json")) or _json_list(row.get("reasons"))
        if not reasons and row.get("blocked_reason_primary"):
            reasons = [str(row.get("blocked_reason_primary"))]
        for reason in reasons:
            reason_counts[reason] += 1
            samples = reason_samples.setdefault(reason, [])
            decision_id = str(row.get("decision_id") or row.get("id") or "")
            if decision_id and len(samples) < 3:
                samples.append(decision_id)

    paper_live_samples = [
        [
            row.get("decision_id"),
            row.get("city_key"),
            row.get("target_date"),
            row.get("strategy_name"),
            row.get("model_probability"),
            row.get("market_ask"),
            row.get("edge"),
            row.get("blocked_reason_primary"),
        ]
        for row in latest100
        if int(row.get("paper_allowed") or 0) == 1 and int(row.get("live_allowed") or 0) == 0
    ]

    strategy_counts = Counter(str(row.get("strategy_name") or "unknown") for row in all_rows)
    strategy_latest = Counter(str(row.get("strategy_name") or "unknown") for row in latest100)
    strategy_rows = []
    for name in sorted(set(strategy_counts) | {"single_bucket_ev", "ladder_grid", "tail_buying"}):
        strategy_rows.append([name, strategy_latest.get(name, 0), strategy_counts.get(name, 0)])

    p0 = []
    inverted = [
        row for row in latest100
        if int(row.get("live_allowed") or 0) == 1 and int(row.get("paper_allowed") or 0) == 0
    ]
    if inverted:
        p0.append(f"{len(inverted)} latest decisions have live_allowed=true while paper_allowed=false")
    blockers = p0 or [f"paper_allowed/live_blocked samples in latest 100: {len(paper_live_samples)}"]

    return f"""# Layer 6 Gate Verification

Generated: {_utc_now().isoformat()}
Rows considered: latest 100 signal_decisions plus all-time strategy counts.

## gate_reasons Top10

{_table(["reason", "rows", "sample_decision_ids"], [[k, v, ", ".join(reason_samples.get(k, []))] for k, v in reason_counts.most_common(10)]) if reason_counts else "No gate reasons found."}

## paper_allowed=true And live_allowed=false Samples

{_table(["decision_id", "city", "target_date", "strategy", "model_p", "ask", "edge", "primary_block"], paper_live_samples[:25]) if paper_live_samples else "No latest paper-allowed/live-blocked samples."}

## Strategy Counts

{_table(["strategy", "latest_100_rows", "all_time_rows"], strategy_rows)}

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
    path = out_dir / "layer6_gates.md"
    path.write_text(build_report(), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
