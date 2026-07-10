from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import connect, dump_json, init_v3_db, utc_now


DELTA_AUDIT_VERSION = "truth-delta-audit-v2"


def upsert_truth_delta_audit(
    *,
    icao: str,
    date_local: str,
    city: str = "",
    wu_high_c: float | None = None,
    iem_high_c: float | None = None,
    hko_high_c: float | None = None,
    polymarket_resolved_bucket: str = "",
    resolved_at: str = "",
    notes: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    station = str(icao or "").upper()
    target = str(date_local or "")
    delta_wu_minus_iem = None
    if wu_high_c is not None and iem_high_c is not None:
        delta_wu_minus_iem = round(float(wu_high_c) - float(iem_high_c), 3)
    delta_hko_minus_iem = None
    if hko_high_c is not None and iem_high_c is not None:
        delta_hko_minus_iem = round(float(hko_high_c) - float(iem_high_c), 3)
    now = utc_now()
    audit_key = f"truth_delta:{station}:{target}"
    raw = {
        "version": DELTA_AUDIT_VERSION,
        "icao": station,
        "city": city,
        "date_local": target,
        "wu_high_c": wu_high_c,
        "iem_high_c": iem_high_c,
        "hko_high_c": hko_high_c,
        "polymarket_resolved_bucket": polymarket_resolved_bucket,
        "delta_wu_minus_iem": delta_wu_minus_iem,
        "delta_hko_minus_iem": delta_hko_minus_iem,
        "notes": notes,
    }
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO truth_delta_audit (
                audit_key, icao, city, date_local, wu_high_c, iem_high_c,
                hko_high_c, polymarket_resolved_bucket, delta_wu_minus_iem,
                delta_hko_minus_iem, resolved_at, notes, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(audit_key) DO UPDATE SET
                city=excluded.city,
                wu_high_c=excluded.wu_high_c,
                iem_high_c=excluded.iem_high_c,
                hko_high_c=excluded.hko_high_c,
                polymarket_resolved_bucket=excluded.polymarket_resolved_bucket,
                delta_wu_minus_iem=excluded.delta_wu_minus_iem,
                delta_hko_minus_iem=excluded.delta_hko_minus_iem,
                resolved_at=excluded.resolved_at,
                notes=excluded.notes,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                audit_key,
                station,
                str(city or ""),
                target,
                wu_high_c,
                iem_high_c,
                hko_high_c,
                polymarket_resolved_bucket,
                delta_wu_minus_iem,
                delta_hko_minus_iem,
                resolved_at,
                notes,
                dump_json(raw),
                now,
                now,
            ),
        )
    return {
        "ok": True,
        "audit_key": audit_key,
        "delta_wu_minus_iem": delta_wu_minus_iem,
        "delta_hko_minus_iem": delta_hko_minus_iem,
    }


def rebuild_truth_delta_from_tables(*, path: Path | None = None, limit: int = 500) -> dict[str, Any]:
    init_v3_db(path)
    rows_written = 0
    with connect(path) as conn:
        iem_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM truth_iem_daily
                ORDER BY date_local DESC, icao
                LIMIT ?
                """,
                (max(1, int(limit or 500)),),
            ).fetchall()
        ]
        for iem in iem_rows:
            icao = str(iem.get("icao") or "").upper()
            date_local = str(iem.get("date_local") or "")
            station = conn.execute(
                """
                SELECT city_key, settlement_station_id
                FROM stations
                WHERE UPPER(station_id) = ?
                LIMIT 1
                """,
                (icao,),
            ).fetchone()
            city = str(station["city_key"] or "") if station else ""
            settlement_station = str(station["settlement_station_id"] or "").upper() if station else ""
            wu = conn.execute(
                "SELECT * FROM truth_wunderground_daily WHERE icao = ? AND date_local = ?",
                (icao, date_local),
            ).fetchone()
            hko = conn.execute(
                "SELECT * FROM truth_hko_daily WHERE date_local = ?",
                (date_local,),
            ).fetchone() if settlement_station == "HKO" else None
            upsert_truth_delta_audit(
                icao=icao,
                city=city,
                date_local=date_local,
                wu_high_c=float(wu["high_c"]) if wu and wu["high_c"] is not None else None,
                iem_high_c=float(iem["high_c"]) if iem.get("high_c") is not None else None,
                hko_high_c=float(hko["high_c"]) if hko and hko["high_c"] is not None else None,
                notes="auto_rebuilt_from_truth_tables",
                path=path,
            )
            rows_written += 1
    return {"ok": True, "version": DELTA_AUDIT_VERSION, "rows_written": rows_written}
