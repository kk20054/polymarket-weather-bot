from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import init_v3_db, upsert_signal


LEGACY_DB = DATA_DIR / "weatherbot.db"


def migrate_legacy_signals(limit: int = 1000) -> dict[str, int]:
    init_v3_db()
    if not LEGACY_DB.exists():
        return {"imported": 0, "skipped": 0}
    imported = 0
    skipped = 0
    conn = sqlite3.connect(LEGACY_DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    for row in rows:
        payload = dict(row)
        raw = payload.get("raw_json")
        if raw:
            try:
                payload.update(json.loads(raw))
            except Exception:
                pass
        try:
            upsert_signal(payload, int(row["id"]))
            imported += 1
        except Exception:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


def audit_market_files() -> dict[str, Any]:
    markets_dir = DATA_DIR / "markets"
    result = {"market_files": 0, "with_positions": 0, "resolved": 0, "open": 0}
    if not markets_dir.exists():
        return result
    for path in markets_dir.glob("*.json"):
        result["market_files"] += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("position"):
            result["with_positions"] += 1
        if data.get("status") == "resolved":
            result["resolved"] += 1
        elif data.get("status") == "open":
            result["open"] += 1
    return result

