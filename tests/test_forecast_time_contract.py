from __future__ import annotations

from tests import ensure_test_environment

ensure_test_environment()

import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.db import (
    connect,
    init_v3_db,
    insert_forecast_run,
    list_daily_max_predictions,
    upsert_daily_max_prediction,
)
from weatherbot_v3.deb import build_daily_max_prediction
from weatherbot_v3.forecast_time import assess_forecast_run
from weatherbot_v3.migrations import (
    FORECAST_AVAILABILITY_MIGRATION,
    FORECAST_SNAPSHOT_UNIQUE_MIGRATION,
    PREDICTION_SOURCE_CONTRACT_MIGRATION,
    run_schema_migrations,
)


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


def valid_online_run(*, retrieved_at: str, raw_hash: str = "hash-a") -> dict:
    return {
        "city": "shanghai",
        "target_date": "2026-07-15",
        "source": "openmeteo_gfs_seamless",
        "provider": "open-meteo",
        "model": "gfs_seamless",
        "model_version": "test",
        "run_type": "forecast",
        "run_at": "2026-07-14T00:00:00+00:00",
        "retrieved_at": retrieved_at,
        "valid_at": "2026-07-15T06:00:00+00:00",
        "horizon": "d1",
        "timezone": "Asia/Shanghai",
        "station_id": "ZSPD",
        "unit": "C",
        "mean_high": 34.0,
        "member_count": 1,
        "raw_response_hash": raw_hash,
        "parse_status": "parsed",
        "training_eligible": True,
    }


class ForecastTimeContractTests(unittest.TestCase):
    def test_upgraded_database_gets_full_snapshot_unique_index(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE metar_reports (
                    id INTEGER PRIMARY KEY, station_id TEXT, report_time TEXT,
                    parse_status TEXT, updated_at TEXT, created_at TEXT
                );
                CREATE TABLE hourly_consensus (
                    id INTEGER PRIMARY KEY, city TEXT, target_date TEXT, local_hour TEXT,
                    updated_at TEXT, created_at TEXT
                );
                CREATE TABLE forecast_runs (
                    id INTEGER PRIMARY KEY, snapshot_key TEXT, city TEXT, target_date TEXT,
                    timezone TEXT, availability_basis TEXT, raw_json TEXT, run_at TEXT,
                    retrieved_at TEXT, available_at TEXT, training_eligible INTEGER,
                    lead_hours REAL, ineligibility_reason TEXT, quarantined_at TEXT,
                    quarantine_reason TEXT
                );
                CREATE UNIQUE INDEX idx_forecast_runs_snapshot_key
                    ON forecast_runs(snapshot_key)
                    WHERE snapshot_key IS NOT NULL AND snapshot_key <> '';
                CREATE TABLE daily_max_predictions (
                    id INTEGER PRIMARY KEY, city_key TEXT, target_date TEXT, issued_at TEXT,
                    source_run_ids_json TEXT, validity_status TEXT, invalidated_at TEXT,
                    invalidation_reason TEXT
                );
                INSERT INTO forecast_runs (
                    id, snapshot_key, city, target_date, timezone, raw_json,
                    run_at, retrieved_at, available_at, availability_basis,
                    training_eligible, lead_hours
                ) VALUES (
                    1, 'snapshot-1', 'shanghai', '2026-07-15', 'Asia/Shanghai', '{}',
                    '2026-07-14T00:00:00+00:00', '2026-07-14T01:00:00+00:00',
                    '2026-07-14T01:00:00+00:00', 'retrieved_at', 1, 29
                );
                """
            )

            run_schema_migrations(conn)
            run_schema_migrations(conn)
            index_row = conn.execute(
                "SELECT partial FROM pragma_index_list('forecast_runs') WHERE name = ?",
                ("idx_forecast_runs_snapshot_key",),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO forecast_runs (id, snapshot_key, city, target_date)
                VALUES (2, 'snapshot-1', 'shanghai', '2026-07-15')
                ON CONFLICT(snapshot_key) DO NOTHING
                """
            )
            migration_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?",
                (FORECAST_SNAPSHOT_UNIQUE_MIGRATION,),
            ).fetchone()[0]
            row_count = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(index_row[0], 0)
        self.assertEqual(migration_count, 1)
        self.assertEqual(row_count, 1)

    def test_online_snapshot_is_not_available_before_actual_retrieval(self):
        run = valid_online_run(retrieved_at="2026-07-14T14:00:00+00:00")
        result = assess_forecast_run(
            run,
            as_of="2026-07-14T13:59:59+00:00",
            target_date="2026-07-15",
            timezone_name="Asia/Shanghai",
            require_training=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "forecast_not_available_as_of")
        self.assertEqual(result["availability_basis"], "retrieved_at")

    def test_trusted_archive_uses_model_run_time_not_import_time(self):
        run = {
            **valid_online_run(retrieved_at="2026-07-20T00:00:00+00:00"),
            "run_at": "2026-07-14T12:00:00+00:00",
            "available_at": "2026-07-14T12:00:00+00:00",
            "availability_basis": "archive_run_at",
            "quality_flags": ["trusted_forecast_archive"],
        }
        result = assess_forecast_run(
            run,
            as_of="2026-07-14T13:00:00+00:00",
            target_date="2026-07-15",
            timezone_name="Asia/Shanghai",
            require_training=True,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["availability_basis"], "archive_run_at")

    def test_insert_quarantines_negative_effective_lead(self):
        path = test_db_path("forecast_negative_lead")
        with patch.dict(os.environ, {"V3_DB_PATH": str(path)}, clear=False):
            run = valid_online_run(retrieved_at="2026-07-15T07:00:00+00:00")
            run["valid_at"] = "2026-07-15T06:00:00+00:00"
            insert_forecast_run(run, [{"member_id": "control", "high_temp": 34.0, "hourly": []}])
            with connect(path) as conn:
                row = dict(conn.execute("SELECT * FROM forecast_runs").fetchone())

        self.assertEqual(row["training_eligible"], 0)
        self.assertEqual(row["quarantine_reason"], "forecast_lead_negative")
        self.assertIsNotNone(row["quarantined_at"])

    def test_same_hour_snapshots_are_immutable_and_exact_replay_deduplicates(self):
        path = test_db_path("forecast_immutable_snapshots")
        members = [{"member_id": "control", "high_temp": 34.0, "hourly": []}]
        with patch.dict(os.environ, {"V3_DB_PATH": str(path)}, clear=False):
            first = valid_online_run(retrieved_at="2026-07-14T12:10:00+00:00", raw_hash="hash-a")
            second = valid_online_run(retrieved_at="2026-07-14T12:40:00+00:00", raw_hash="hash-b")
            first_id = insert_forecast_run(first, members)
            replay_id = insert_forecast_run(first, members)
            second_id = insert_forecast_run(second, members)
            with connect(path) as conn:
                rows = [dict(row) for row in conn.execute(
                    "SELECT id, snapshot_key, available_at FROM forecast_runs ORDER BY available_at"
                ).fetchall()]

        self.assertEqual(first_id, replay_id)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["snapshot_key"], rows[1]["snapshot_key"])

    def test_explicit_deb_cutoff_preserves_minutes(self):
        path = test_db_path("deb_exact_cutoff")
        with patch.dict(os.environ, {"V3_DB_PATH": str(path)}, clear=False):
            prediction = build_daily_max_prediction(
                "shanghai",
                "2026-07-15",
                issued_at="2026-07-14T12:34:56+00:00",
                path=path,
            )

        self.assertEqual(prediction["issued_at"], "2026-07-14T12:34:56+00:00")

    def test_migration_preserves_rows_quarantines_source_and_invalidates_prediction(self):
        path = test_db_path("forecast_migration")
        with patch.dict(os.environ, {"V3_DB_PATH": str(path)}, clear=False):
            init_v3_db(path)
            run_id = insert_forecast_run(
                valid_online_run(retrieved_at="2026-07-14T12:00:00+00:00"),
                [{"member_id": "control", "high_temp": 34.0, "hourly": []}],
            )
            upsert_daily_max_prediction({
                "city_key": "shanghai",
                "target_date": "2026-07-15",
                "issued_at": "2026-07-14T12:30:00+00:00",
                "mu": 34.0,
                "sigma": 1.0,
                "unit": "C",
                "source_run_ids": [run_id],
            }, path=path)
            with connect(path) as conn:
                conn.execute(
                    """
                    UPDATE forecast_runs
                    SET lead_hours = -1, available_at = NULL, availability_basis = NULL,
                        training_eligible = 1, ineligibility_reason = '',
                        quarantined_at = NULL, quarantine_reason = NULL
                    WHERE id = ?
                    """,
                    (run_id,),
                )
                conn.execute(
                    "UPDATE daily_max_predictions SET validity_status = NULL, invalidated_at = NULL, invalidation_reason = NULL"
                )
                conn.execute(
                    "DELETE FROM schema_migrations WHERE migration_id = ?",
                    (FORECAST_AVAILABILITY_MIGRATION,),
                )
                before_runs = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
                before_predictions = conn.execute("SELECT COUNT(*) FROM daily_max_predictions").fetchone()[0]
                run_schema_migrations(conn)
                run_schema_migrations(conn)
                source = dict(conn.execute("SELECT * FROM forecast_runs WHERE id = ?", (run_id,)).fetchone())
                prediction = dict(conn.execute("SELECT * FROM daily_max_predictions").fetchone())
                after_runs = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
                after_predictions = conn.execute("SELECT COUNT(*) FROM daily_max_predictions").fetchone()[0]

            self.assertEqual(before_runs, after_runs)
            self.assertEqual(before_predictions, after_predictions)
            self.assertEqual(source["training_eligible"], 0)
            self.assertEqual(source["quarantine_reason"], "forecast_lead_negative")
            self.assertEqual(prediction["validity_status"], "invalid")
            self.assertEqual(list_daily_max_predictions(path=path), [])

    def test_prediction_source_migration_rejects_preexisting_ineligible_source(self):
        path = test_db_path("prediction_source_migration")
        with patch.dict(os.environ, {"V3_DB_PATH": str(path)}, clear=False):
            init_v3_db(path)
            run_id = insert_forecast_run(
                {
                    **valid_online_run(retrieved_at="2026-07-14T12:00:00+00:00"),
                    "training_eligible": False,
                },
                [{"member_id": "control", "high_temp": 34.0, "hourly": []}],
            )
            prediction_id = upsert_daily_max_prediction({
                "city_key": "shanghai",
                "target_date": "2026-07-15",
                "issued_at": "2026-07-14T12:30:00+00:00",
                "mu": 34.0,
                "sigma": 1.0,
                "unit": "C",
                "model_weights": {"openmeteo_gfs_seamless": 1.0},
                "source_run_ids": [run_id],
            }, path=path)
            with connect(path) as conn:
                conn.execute(
                    "DELETE FROM schema_migrations WHERE migration_id = ?",
                    (PREDICTION_SOURCE_CONTRACT_MIGRATION,),
                )
                run_schema_migrations(conn)
                prediction = dict(conn.execute(
                    "SELECT * FROM daily_max_predictions WHERE id = ?",
                    (prediction_id,),
                ).fetchone())

        self.assertEqual(prediction["validity_status"], "invalid")
        self.assertEqual(
            prediction["invalidation_reason"],
            "forecast_source_ineligible_or_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
