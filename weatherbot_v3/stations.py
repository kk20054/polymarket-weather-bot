from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .db import connect, dump_json, init_v3_db, utc_now
from .registry import REGISTRY_VERSION, SETTLEMENT_REGISTRY, CitySettlementProfile


STATION_SYNC_VERSION = "stations-registry-sync-v1"
DEFAULT_ENABLED_CITY_KEYS = {
    "chicago",
    "tokyo",
    "atlanta",
    "nyc",
    "dallas",
    "shanghai",
    "hong-kong",
    "beijing",
    "wuhan",
    "qingdao",
    "shenzhen",
    "taipei",
    "singapore",
    "seoul",
}


def station_row_from_profile(profile: CitySettlementProfile) -> dict[str, Any]:
    station_id = str(profile.station_id or "").upper()
    provider_ids = {
        "icao": station_id,
        "metar": station_id,
        "aviationweather": station_id,
        "visual_crossing": station_id,
    }
    networks = ["METAR", "AviationWeather", "Visual Crossing station mode"]
    if station_id.startswith("K"):
        provider_ids["nws"] = station_id
        networks.append("NWS station observations")
    settlement_station_id = station_id
    settlement_station_name = profile.station_name
    primary_settlement_source = profile.expected_resolution_provider
    if profile.city == "hong-kong":
        settlement_station_id = "HKO"
        settlement_station_name = "Hong Kong Observatory"
        primary_settlement_source = "hong_kong_observatory"
        networks.append("HKO Daily Extract")
    confidence = 0.75 if profile.verification_status == "provisional" else 0.9
    settlement_rule_text = (
        f"WeatherBot registry maps {profile.city_name} to {profile.station_name} "
        f"({station_id}) for highest-temperature evidence. Each Polymarket "
        "contract still requires rule/source verification before live use."
    )
    raw = {
        **profile.to_dict(),
        "sync_version": STATION_SYNC_VERSION,
        "provider_station_ids": provider_ids,
        "nearby_observation_networks": networks,
        "settlement_rule_text": settlement_rule_text,
        "primary_settlement_source": primary_settlement_source,
        "confidence": confidence,
    }
    return {
        "city_key": profile.city,
        "city_name": profile.city_name,
        "station_id": station_id,
        "icao_id": station_id if len(station_id) == 4 else "",
        "wmo_id": "",
        "provider_station_ids_json": dump_json(provider_ids),
        "station_name": profile.station_name,
        "timezone": profile.timezone,
        "unit": profile.unit,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "region": profile.region,
        "expected_metric": profile.expected_metric,
        "settlement_rule_text": settlement_rule_text,
        "settlement_station_id": settlement_station_id,
        "settlement_station_name": settlement_station_name,
        "settlement_timezone": profile.timezone,
        "settlement_unit": profile.unit,
        "settlement_time_basis": "local_day",
        "settlement_rule_verified_at": "",
        "primary_settlement_source": primary_settlement_source,
        "nearby_observation_networks_json": dump_json(networks),
        "confidence": confidence,
        "verification_status": profile.verification_status,
        "enabled": 1 if profile.city in DEFAULT_ENABLED_CITY_KEYS else 0,
        "tier": 1 if profile.city in DEFAULT_ENABLED_CITY_KEYS else 9,
        "registry_version": profile.registry_version or REGISTRY_VERSION,
        "raw_json": dump_json(raw),
    }


def sync_station_registry(
    path: Path | None = None,
    profiles: Iterable[CitySettlementProfile] | None = None,
) -> dict[str, Any]:
    init_v3_db(path)
    rows = [station_row_from_profile(profile) for profile in (profiles or SETTLEMENT_REGISTRY.values())]
    now = utc_now()
    with connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO stations (
                city_key, city_name, station_id, icao_id, wmo_id,
                provider_station_ids_json, station_name, timezone, unit,
                latitude, longitude, region, expected_metric,
                settlement_rule_text, settlement_station_id, settlement_station_name,
                settlement_timezone, settlement_unit, settlement_time_basis,
                settlement_rule_verified_at, primary_settlement_source,
                nearby_observation_networks_json, confidence,
                verification_status, enabled, tier, registry_version, raw_json, updated_at
            ) VALUES (
                :city_key, :city_name, :station_id, :icao_id, :wmo_id,
                :provider_station_ids_json, :station_name, :timezone, :unit,
                :latitude, :longitude, :region, :expected_metric,
                :settlement_rule_text, :settlement_station_id, :settlement_station_name,
                :settlement_timezone, :settlement_unit, :settlement_time_basis,
                :settlement_rule_verified_at, :primary_settlement_source,
                :nearby_observation_networks_json, :confidence,
                :verification_status, :enabled, :tier, :registry_version, :raw_json, :updated_at
            )
            ON CONFLICT(city_key) DO UPDATE SET
                city_name=excluded.city_name,
                station_id=excluded.station_id,
                icao_id=excluded.icao_id,
                wmo_id=excluded.wmo_id,
                provider_station_ids_json=excluded.provider_station_ids_json,
                station_name=excluded.station_name,
                timezone=excluded.timezone,
                unit=excluded.unit,
                latitude=excluded.latitude,
                longitude=excluded.longitude,
                region=excluded.region,
                expected_metric=excluded.expected_metric,
                settlement_rule_text=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.settlement_rule_text
                    ELSE excluded.settlement_rule_text
                END,
                settlement_station_id=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.settlement_station_id
                    ELSE excluded.settlement_station_id
                END,
                settlement_station_name=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.settlement_station_name
                    ELSE excluded.settlement_station_name
                END,
                settlement_timezone=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.settlement_timezone
                    ELSE excluded.settlement_timezone
                END,
                settlement_unit=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.settlement_unit
                    ELSE excluded.settlement_unit
                END,
                settlement_time_basis=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.settlement_time_basis
                    ELSE excluded.settlement_time_basis
                END,
                settlement_rule_verified_at=COALESCE(NULLIF(stations.settlement_rule_verified_at, ''), excluded.settlement_rule_verified_at),
                primary_settlement_source=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.primary_settlement_source
                    ELSE excluded.primary_settlement_source
                END,
                nearby_observation_networks_json=excluded.nearby_observation_networks_json,
                confidence=excluded.confidence,
                verification_status=CASE
                    WHEN COALESCE(stations.verification_status, '') IN ('verified', 'settlement_mismatch', 'no_active_market') THEN stations.verification_status
                    ELSE excluded.verification_status
                END,
                enabled=COALESCE(stations.enabled, excluded.enabled),
                tier=COALESCE(stations.tier, excluded.tier),
                registry_version=excluded.registry_version,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            [{**row, "updated_at": now} for row in rows],
        )
        enabled_count = int(conn.execute("SELECT COUNT(*) FROM stations WHERE COALESCE(enabled, 0) = 1").fetchone()[0])
        if enabled_count == 0:
            placeholders = ",".join("?" for _ in DEFAULT_ENABLED_CITY_KEYS)
            conn.execute(
                f"""
                UPDATE stations
                SET enabled = 1,
                    tier = 1,
                    updated_at = ?
                WHERE city_key IN ({placeholders})
                """,
                [now, *sorted(DEFAULT_ENABLED_CITY_KEYS)],
            )
        count = int(conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0])
    return {
        "ok": True,
        "sync_version": STATION_SYNC_VERSION,
        "registry_version": REGISTRY_VERSION,
        "synced": len(rows),
        "total": count,
        "updated_at": now,
    }


def list_stations(path: Path | None = None, region: str = "", city: str = "") -> list[dict[str, Any]]:
    init_v3_db(path)
    where: list[str] = []
    params: list[Any] = []
    if region:
        where.append("region = ?")
        params.append(region)
    if city:
        where.append("(city_key = ? OR station_id = ?)")
        params.extend([city, str(city).upper()])
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect(path) as conn:
        rows = [
            _decode_station_row(dict(row))
            for row in conn.execute(
                f"""
                SELECT *
                FROM stations
                {clause}
                ORDER BY region, city_name
                """,
                tuple(params),
            ).fetchall()
        ]
    return rows


def get_station(city_key: str, path: Path | None = None) -> dict[str, Any] | None:
    rows = list_stations(path, city=city_key)
    return rows[0] if rows else None


def set_station_enabled(city_key: str, enabled: bool, *, path: Path | None = None, tier: int | None = None) -> dict[str, Any]:
    init_v3_db(path)
    sync_station_registry(path)
    key = str(city_key or "").strip().lower()
    if not key:
        return {"ok": False, "reason": "missing_city_key", "city_key": city_key}
    now = utc_now()
    with connect(path) as conn:
        found = conn.execute(
            "SELECT city_key FROM stations WHERE city_key = ? OR station_id = ?",
            (key, key.upper()),
        ).fetchone()
        if not found:
            return {"ok": False, "reason": "station_not_found", "city_key": city_key}
        actual_key = str(found["city_key"])
        if tier is None:
            conn.execute(
                "UPDATE stations SET enabled = ?, updated_at = ? WHERE city_key = ?",
                (1 if enabled else 0, now, actual_key),
            )
        else:
            conn.execute(
                "UPDATE stations SET enabled = ?, tier = ?, updated_at = ? WHERE city_key = ?",
                (1 if enabled else 0, max(1, int(tier)), now, actual_key),
            )
    row = get_station(actual_key, path)
    return {
        "ok": True,
        "city_key": actual_key,
        "enabled": bool(row.get("enabled")) if row else bool(enabled),
        "tier": int(row.get("tier") or 9) if row else int(tier or 9),
        "station": row,
    }


def apply_market_probe_result(result: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    """Persist an independently fetched Polymarket settlement-rule probe.

    ``station_id`` remains the observation collector station. The market rule's
    station/source are stored in settlement_* fields so mismatches can block
    live trading without breaking paper observation workflows.
    """
    init_v3_db(path)
    sync_station_registry(path)
    city_key = str(result.get("city_key") or result.get("city") or "").strip().lower()
    if not city_key:
        return {"ok": False, "reason": "missing_city_key", "city_key": city_key}
    now = utc_now()
    with connect(path) as conn:
        existing = conn.execute("SELECT * FROM stations WHERE city_key = ?", (city_key,)).fetchone()
        if not existing:
            return {"ok": False, "reason": "station_not_found", "city_key": city_key}
        current = dict(existing)
        active_market = bool(result.get("active_market"))
        settlement_station = str(result.get("settlement_station_id") or "").upper()
        current_station = str(current.get("station_id") or "").upper()
        status = "no_active_market"
        mismatch = False
        if active_market:
            if settlement_station and current_station and settlement_station != current_station:
                status = "settlement_mismatch"
                mismatch = True
            elif settlement_station:
                status = "verified"
            else:
                status = "unverified"
        source = str(result.get("primary_settlement_source") or result.get("source_institution") or "")
        rule_text = str(result.get("settlement_rule_text") or "")
        if not active_market:
            settlement_station = str(current.get("settlement_station_id") or current.get("station_id") or "").upper()
            source = source or "no_active_market"
            rule_text = rule_text or "no_active_market: no active Polymarket highest-temperature event found in probe window"
        confidence = 0.95 if status == "verified" else (0.55 if status == "settlement_mismatch" else 0.2)
        raw = {
            **_json_or_dict(current.get("raw_json")),
            "latest_market_probe": result,
            "settlement_mismatch": mismatch,
        }
        conn.execute(
            """
            UPDATE stations
            SET settlement_rule_text = ?,
                settlement_station_id = ?,
                settlement_station_name = ?,
                settlement_timezone = ?,
                settlement_unit = ?,
                settlement_time_basis = ?,
                settlement_rule_verified_at = ?,
                primary_settlement_source = ?,
                confidence = ?,
                verification_status = ?,
                raw_json = ?,
                updated_at = ?
            WHERE city_key = ?
            """,
            (
                rule_text,
                settlement_station,
                str(result.get("settlement_station_name") or current.get("settlement_station_name") or current.get("station_name") or ""),
                str(result.get("settlement_timezone") or current.get("timezone") or ""),
                str(result.get("settlement_unit") or current.get("unit") or ""),
                str(result.get("settlement_time_basis") or "local_day"),
                now if active_market else "",
                source,
                confidence,
                status,
                dump_json(raw),
                now,
                city_key,
            ),
        )
    row = get_station(city_key, path)
    return {
        "ok": True,
        "city_key": city_key,
        "verification_status": row.get("verification_status") if row else status,
        "settlement_mismatch": mismatch,
        "station": row,
    }


def enabled_station_rows(path: Path | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = [row for row in list_stations(path) if row.get("enabled")]
    rows.sort(key=lambda row: (int(row.get("tier") or 9), str(row.get("city_name") or "")))
    if limit is not None:
        return rows[: max(0, int(limit))]
    return rows


def _decode_station_row(row: dict[str, Any]) -> dict[str, Any]:
    for source_key, target_key in (
        ("provider_station_ids_json", "provider_station_ids"),
        ("nearby_observation_networks_json", "nearby_observation_networks"),
    ):
        try:
            row[target_key] = json.loads(row.get(source_key) or "{}")
        except Exception:
            row[target_key] = [] if target_key.endswith("networks") else {}
    row["city"] = row.get("city_key")
    row["enabled"] = bool(row.get("enabled"))
    try:
        row["tier"] = int(row.get("tier") or 9)
    except Exception:
        row["tier"] = 9
    return row


def _json_or_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}
