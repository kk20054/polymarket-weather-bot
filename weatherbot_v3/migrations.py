from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .forecast_time import assess_forecast_run


CANONICAL_WEATHER_KEYS_MIGRATION = "20260713_01_canonical_weather_keys"
FORECAST_AVAILABILITY_MIGRATION = "20260714_01_forecast_availability_contract"
PREDICTION_SOURCE_CONTRACT_MIGRATION = "20260714_02_prediction_source_contract"


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
    _run_forecast_availability_migration(conn)
    _run_prediction_source_contract_migration(conn)


def _run_forecast_availability_migration(conn: sqlite3.Connection) -> None:
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
        (FORECAST_AVAILABILITY_MIGRATION,),
    ).fetchone()
    if applied is None:
        now = _utc_now()
        conn.execute(
            """
            UPDATE forecast_runs
            SET snapshot_key = COALESCE(NULLIF(snapshot_key, ''), 'legacy:' || id),
                availability_basis = CASE
                    WHEN COALESCE(NULLIF(availability_basis, ''), '') <> '' THEN availability_basis
                    WHEN json_valid(raw_json) AND COALESCE(json_extract(raw_json, '$.archive_imported'), 0) = 1
                        THEN 'archive_run_at'
                    ELSE 'retrieved_at'
                END,
                available_at = CASE
                    WHEN COALESCE(NULLIF(available_at, ''), '') <> '' THEN available_at
                    WHEN json_valid(raw_json) AND COALESCE(json_extract(raw_json, '$.archive_imported'), 0) = 1
                        THEN NULLIF(run_at, '')
                    ELSE NULLIF(retrieved_at, '')
                END
            """
        )
        quarantined = 0
        candidate_rows = conn.execute(
            "SELECT * FROM forecast_runs WHERE COALESCE(training_eligible, 0) = 1"
        ).fetchall()
        for candidate in candidate_rows:
            run = dict(candidate)
            assessment = assess_forecast_run(
                run,
                target_date=str(run.get("target_date") or ""),
                timezone_name=str(run.get("timezone") or ""),
                require_training=True,
                respect_training_flag=False,
            )
            if assessment["ok"]:
                continue
            reason = str(assessment.get("reason") or "forecast_time_contract_failed")
            conn.execute(
                """
                UPDATE forecast_runs
                SET training_eligible = 0,
                    ineligibility_reason = ?,
                    quarantined_at = ?,
                    quarantine_reason = ?
                WHERE id = ?
                """,
                (reason, now, reason, int(run["id"])),
            )
            quarantined += 1
        conn.execute(
            """
            UPDATE daily_max_predictions
            SET validity_status = 'valid',
                invalidated_at = NULL,
                invalidation_reason = NULL
            WHERE validity_status IS NULL OR TRIM(validity_status) = ''
            """
        )
        invalidated = conn.execute(
            """
            UPDATE daily_max_predictions AS d
            SET validity_status = 'invalid',
                invalidated_at = ?,
                invalidation_reason = 'forecast_source_not_available_at_issued_at'
            WHERE CASE
                    WHEN json_valid(d.source_run_ids_json)
                        THEN json_array_length(d.source_run_ids_json) = 0
                    ELSE 1
                  END
               OR EXISTS (
                SELECT 1
                FROM json_each(
                    CASE
                        WHEN json_valid(d.source_run_ids_json) THEN d.source_run_ids_json
                        ELSE '[]'
                    END
                ) AS source_id
                LEFT JOIN forecast_runs AS fr ON fr.id = CAST(source_id.value AS INTEGER)
                WHERE fr.id IS NULL
                   OR fr.quarantined_at IS NOT NULL
                   OR COALESCE(fr.training_eligible, 0) <> 1
                   OR fr.available_at IS NULL
                   OR TRIM(fr.available_at) = ''
                   OR julianday(fr.available_at) > julianday(d.issued_at)
                   OR COALESCE(fr.city, '') <> COALESCE(d.city_key, '')
                   OR COALESCE(fr.target_date, '') <> COALESCE(d.target_date, '')
            )
            """,
            (now,),
        ).rowcount
        conn.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at, details_json) VALUES (?, ?, ?)",
            (
                FORECAST_AVAILABILITY_MIGRATION,
                now,
                json.dumps(
                    {
                        "forecast_runs_quarantined": max(0, int(quarantined or 0)),
                        "daily_max_predictions_invalidated": max(0, int(invalidated or 0)),
                        "history_preserved": True,
                        "availability_rule": "online=retrieved_at;trusted_archive=run_at",
                    },
                    sort_keys=True,
                ),
            ),
        )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_runs_snapshot_key "
        "ON forecast_runs(snapshot_key) WHERE snapshot_key IS NOT NULL AND snapshot_key <> ''"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_forecast_runs_city_date_available "
        "ON forecast_runs(city, target_date, available_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_max_predictions_validity "
        "ON daily_max_predictions(city_key, target_date, validity_status, issued_at DESC, id DESC)"
    )


def _run_prediction_source_contract_migration(conn: sqlite3.Connection) -> None:
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
        (PREDICTION_SOURCE_CONTRACT_MIGRATION,),
    ).fetchone()
    if applied is not None:
        return
    now = _utc_now()
    invalidated = conn.execute(
        """
        UPDATE daily_max_predictions AS d
        SET validity_status = 'invalid',
            invalidated_at = ?,
            invalidation_reason = 'forecast_source_ineligible_or_unavailable'
        WHERE COALESCE(d.validity_status, 'valid') = 'valid'
          AND (
            CASE
                WHEN json_valid(d.source_run_ids_json)
                    THEN json_array_length(d.source_run_ids_json) = 0
                ELSE 1
            END
            OR EXISTS (
                SELECT 1
                FROM json_each(
                    CASE
                        WHEN json_valid(d.source_run_ids_json) THEN d.source_run_ids_json
                        ELSE '[]'
                    END
                ) AS source_id
                LEFT JOIN forecast_runs AS fr ON fr.id = CAST(source_id.value AS INTEGER)
                WHERE fr.id IS NULL
                   OR COALESCE(fr.training_eligible, 0) <> 1
                   OR fr.quarantined_at IS NOT NULL
                   OR fr.available_at IS NULL
                   OR TRIM(fr.available_at) = ''
                   OR julianday(fr.available_at) > julianday(d.issued_at)
                   OR COALESCE(fr.city, '') <> COALESCE(d.city_key, '')
                   OR COALESCE(fr.target_date, '') <> COALESCE(d.target_date, '')
            )
          )
        """,
        (now,),
    ).rowcount
    conn.execute(
        "INSERT INTO schema_migrations (migration_id, applied_at, details_json) VALUES (?, ?, ?)",
        (
            PREDICTION_SOURCE_CONTRACT_MIGRATION,
            now,
            json.dumps(
                {
                    "daily_max_predictions_invalidated": max(0, int(invalidated or 0)),
                    "history_preserved": True,
                    "source_rule": "training_eligible+not_quarantined+available_by_issued_at+same_city_date",
                },
                sort_keys=True,
            ),
        ),
    )
