from __future__ import annotations

import json
import sqlite3
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .db import connect, init_v3_db, upsert_market_rules, upsert_settlement_contracts, upsert_signal, upsert_truth_observation
from .registry import get_city_profile
from .truth import _parse_time, infer_settlement_rule, settlement_contract_from_rule


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


def sync_settlement_contracts() -> dict[str, Any]:
    markets_dir = DATA_DIR / "markets"
    rules = []
    contracts: dict[str, dict[str, Any]] = {}
    files = 0
    if not markets_dir.exists():
        return {"market_files": 0, "market_rules": 0, "settlement_contracts": 0}
    for path in markets_dir.glob("*.json"):
        try:
            market = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        files += 1
        outcomes = market.get("all_outcomes") or []
        if not outcomes:
            outcomes = [{"market_id": "", "question": market.get("question") or ""}]
        for outcome in outcomes:
            payload = {
                **market,
                "market_id": outcome.get("market_id"),
                "question": outcome.get("question") or market.get("question") or "",
                "event_url": outcome.get("event_url") or market.get("event_url") or "",
            }
            try:
                rule = infer_settlement_rule(payload).to_dict()
            except Exception:
                continue
            rules.append(rule)
            contract = settlement_contract_from_rule(rule)
            if contract.get("event_slug"):
                contracts[str(contract["event_slug"])] = contract
    upsert_market_rules(rules, prune_missing=True)
    upsert_settlement_contracts(list(contracts.values()))
    with connect() as conn:
        persisted = conn.execute(
            """
            SELECT
                COUNT(*) AS settlement_contracts,
                SUM(CASE WHEN auto_verified_at IS NOT NULL AND auto_verified_at != '' THEN 1 ELSE 0 END) AS auto_verified_contracts,
                SUM(CASE WHEN manual_verified_at IS NOT NULL AND manual_verified_at != '' THEN 1 ELSE 0 END) AS manual_verified_contracts
            FROM settlement_contracts
            """
        ).fetchone()
    return {
        "market_files": files,
        "market_rules": len(rules),
        "parsed_settlement_contracts": len(contracts),
        "settlement_contracts": int(persisted["settlement_contracts"] or 0),
        "auto_verified_contracts": int(persisted["auto_verified_contracts"] or 0),
        "manual_verified_contracts": int(persisted["manual_verified_contracts"] or 0),
    }


def repair_truth_temporal_mismatches() -> dict[str, Any]:
    init_v3_db()
    with connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM truth_observations
                WHERE actual_temp IS NOT NULL
                  AND provider IN ('nws_station', 'aviationweather_station')
                """
            ).fetchall()
        ]
    checked = 0
    invalidated = 0
    examples = []
    for row in rows:
        checked += 1
        try:
            raw = json.loads(row.get("raw_json") or "{}")
        except Exception:
            raw = {}
        observed_at = _parse_time(raw.get("observed_at"))
        profile = get_city_profile(str(row.get("city") or ""))
        if not observed_at or not profile:
            continue
        observed_local_date = observed_at.astimezone(ZoneInfo(profile.timezone)).strftime("%Y-%m-%d")
        target_date = str(row.get("target_date") or "")
        if observed_local_date == target_date:
            continue
        corrected = {
            **raw,
            "city": row.get("city"),
            "city_name": row.get("city_name"),
            "target_date": target_date,
            "station_id": row.get("station_id"),
            "station_name": row.get("station_name"),
            "unit": row.get("unit"),
            "actual_temp": None,
            "provider": row.get("provider"),
            "source_url": row.get("source_url"),
            "observation_count": row.get("observation_count"),
            "source_confidence": 0.0,
            "calibration_eligible": False,
            "reason_if_ineligible": f"observation_date_mismatch:{observed_local_date}",
            "is_preliminary": False,
            "is_final": False,
            "quality_flags": list(dict.fromkeys([
                *(raw.get("quality_flags") or []),
                "temporal_mismatch_invalidated",
            ])),
        }
        upsert_truth_observation(corrected)
        invalidated += 1
        examples.append({
            "city": row.get("city"),
            "target_date": target_date,
            "observed_local_date": observed_local_date,
            "provider": row.get("provider"),
        })
    return {"checked": checked, "invalidated": invalidated, "examples": examples[:20]}
