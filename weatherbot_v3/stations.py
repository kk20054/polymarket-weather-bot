from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .db import connect, dump_json, init_v3_db, utc_now
from .registry import REGISTRY_VERSION, SETTLEMENT_REGISTRY, CitySettlementProfile


STATION_SYNC_VERSION = "stations-registry-sync-v1"


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
        "primary_settlement_source": profile.expected_resolution_provider,
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
        "primary_settlement_source": profile.expected_resolution_provider,
        "nearby_observation_networks_json": dump_json(networks),
        "confidence": confidence,
        "verification_status": profile.verification_status,
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
                settlement_rule_text, primary_settlement_source,
                nearby_observation_networks_json, confidence,
                verification_status, registry_version, raw_json, updated_at
            ) VALUES (
                :city_key, :city_name, :station_id, :icao_id, :wmo_id,
                :provider_station_ids_json, :station_name, :timezone, :unit,
                :latitude, :longitude, :region, :expected_metric,
                :settlement_rule_text, :primary_settlement_source,
                :nearby_observation_networks_json, :confidence,
                :verification_status, :registry_version, :raw_json, :updated_at
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
                settlement_rule_text=excluded.settlement_rule_text,
                primary_settlement_source=excluded.primary_settlement_source,
                nearby_observation_networks_json=excluded.nearby_observation_networks_json,
                confidence=excluded.confidence,
                verification_status=excluded.verification_status,
                registry_version=excluded.registry_version,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            [{**row, "updated_at": now} for row in rows],
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
    return row
