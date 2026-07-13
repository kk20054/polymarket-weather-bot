from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


CANONICAL_WEATHER_KEYS_MIGRATION = "20260713_01_canonical_weather_keys"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _delete_ranked_duplicates(
    conn: sqlite3.Connection,
    *,
    table: str,
    partition_by: str,
    quality_order: str,
    where_clause: str,
) -> int:
    if not _table_exists(conn, table):
        return 0
    before = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    conn.execute(
        f"""
        DELETE FROM {table}
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY {partition_by}
                        ORDER BY {quality_order}
                    ) AS duplicate_rank
                FROM {table}
                WHERE {where_clause}
            ) ranked
            WHERE duplicate_rank > 1
        )
        """
    )
    after = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return before - after


def run_schema_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL,
            details_json TEXT NOT NULL
        )
        """
    )
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
        (CANONICAL_WEATHER_KEYS_MIGRATION,),
    ).fetchone()
    if applied is None:
        metar_deleted = _delete_ranked_duplicates(
            conn,
            table="metar_reports",
            partition_by="station_id, report_time",
            quality_order=(
                "CASE parse_status "
                "WHEN 'parsed' THEN 3 WHEN 'partial' THEN 2 WHEN 'failed' THEN 1 ELSE 0 END DESC, "
                "COALESCE(updated_at, created_at, '') DESC, id DESC"
            ),
            where_clause=(
                "station_id IS NOT NULL AND TRIM(station_id) <> '' "
                "AND report_time IS NOT NULL AND TRIM(report_time) <> ''"
            ),
        )
        consensus_deleted = _delete_ranked_duplicates(
            conn,
            table="hourly_consensus",
            partition_by="city, target_date, local_hour",
            quality_order="COALESCE(updated_at, created_at, '') DESC, id DESC",
            where_clause=(
                "city IS NOT NULL AND TRIM(city) <> '' "
                "AND target_date IS NOT NULL AND TRIM(target_date) <> '' "
                "AND local_hour IS NOT NULL AND TRIM(local_hour) <> ''"
            ),
        )
        conn.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at, details_json) VALUES (?, ?, ?)",
            (
                CANONICAL_WEATHER_KEYS_MIGRATION,
                _utc_now(),
                json.dumps(
                    {
                        "metar_rows_deleted": metar_deleted,
                        "hourly_consensus_rows_deleted": consensus_deleted,
                        "winner_rule": "parse_quality_then_updated_at_then_id",
                    },
                    sort_keys=True,
                ),
            ),
        )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_metar_reports_station_report_time_unique "
        "ON metar_reports(station_id, report_time)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_hourly_consensus_city_date_hour_unique "
        "ON hourly_consensus(city, target_date, local_hour)"
    )
