from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from weatherbot_v3.storage_maintenance import (
    archive_redundant_blobs,
    restore_blob_archive,
    storage_audit,
)


class StorageMaintenanceTests(unittest.TestCase):
    def test_archives_before_clearing_and_can_restore_duplicate_blobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "weatherbot.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript("""
                    CREATE TABLE forecast_members (
                        id INTEGER PRIMARY KEY,
                        raw_json TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE orderbooks (
                        id INTEGER PRIMARY KEY,
                        raw_json TEXT,
                        created_at TEXT NOT NULL
                    );
                """)
                conn.execute(
                    "INSERT INTO forecast_members VALUES (1, ?, '2026-01-01T00:00:00+00:00')",
                    ('{"hourly":[1,2]}',),
                )
                conn.execute(
                    "INSERT INTO orderbooks VALUES (1, ?, '2026-01-01T00:00:00+00:00')",
                    ('{"bids":[],"asks":[]}',),
                )
                conn.execute(
                    "INSERT INTO orderbooks VALUES (2, ?, '2999-01-01T00:00:00+00:00')",
                    ('{"current":true}',),
                )
                conn.commit()
            finally:
                conn.close()

            audit = storage_audit(db_path=db_path, before_days=1)
            self.assertEqual(audit["tables"]["forecast_members"]["eligible_rows"], 1)
            self.assertEqual(audit["tables"]["orderbooks"]["eligible_rows"], 1)

            archived = archive_redundant_blobs(
                db_path=db_path,
                archive_root=root / "archive",
                before_days=1,
                batch_size=100,
                apply=True,
            )
            manifest_path = Path(archived["manifest"])
            self.assertTrue(manifest_path.exists())
            conn = sqlite3.connect(db_path)
            try:
                self.assertIsNone(conn.execute("SELECT raw_json FROM forecast_members WHERE id=1").fetchone()[0])
                self.assertIsNone(conn.execute("SELECT raw_json FROM orderbooks WHERE id=1").fetchone()[0])
                self.assertEqual(conn.execute("SELECT raw_json FROM orderbooks WHERE id=2").fetchone()[0], '{"current":true}')
            finally:
                conn.close()

            restored = restore_blob_archive(db_path=db_path, manifest_path=manifest_path)
            self.assertEqual(restored["restored_rows"], 2)
            conn = sqlite3.connect(db_path)
            try:
                self.assertEqual(conn.execute("SELECT raw_json FROM forecast_members WHERE id=1").fetchone()[0], '{"hourly":[1,2]}')
                self.assertEqual(conn.execute("SELECT raw_json FROM orderbooks WHERE id=1").fetchone()[0], '{"bids":[],"asks":[]}')
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
