from __future__ import annotations

from tests import ensure_test_environment

ensure_test_environment()

import json
import tempfile
import unittest
from pathlib import Path

from weatherbot_v3.bias import _truth_by_date
from weatherbot_v3.db import connect, init_v3_db


class BiasTruthSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="weatherbot-bias-truth-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "truth.db"
        init_v3_db(self.path)

    def add_truth(self, provider="wu", target_date="2026-07-01", updated_at="2026-07-02T06:00:00Z"):
        table = {"wu": "truth_wunderground_daily", "iem": "truth_iem_daily"}[provider]
        with connect(self.path) as conn:
            conn.execute(
                f"""
                INSERT INTO {table} (truth_key, icao, date_local, high_c, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"{provider}:ZBAA:{target_date}", "ZBAA", target_date,
                 30.0 if provider == "wu" else 29.0, updated_at, updated_at),
            )

    def add_event(self, source="unknown", *, city="beijing", target_date="2026-07-01", source_url="", raw=None):
        with connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO polymarket_events (
                    event_id, city, target_date, resolution_source, resolution_source_url,
                    raw_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"{city}:{target_date}", city, target_date, source, source_url, json.dumps(raw or {}),
                 "2026-07-01T00:00:00Z", "2026-07-01T00:00:00Z"),
            )

    def truths(self, before_date="2026-07-10"):
        return _truth_by_date("ZBAA", "beijing", self.path, before_date=before_date)

    def test_noaa_primary_excludes_wu_and_iem(self):
        self.add_truth("wu")
        self.add_truth("iem")
        self.add_event("noaa", source_url="https://www.weather.gov/wrh/timeseries?site=zbaa")
        self.assertEqual(self.truths(), {})

    def test_noaa_primary_does_not_fall_back_to_iem(self):
        self.add_truth("iem")
        self.add_event("noaa", source_url="https://www.weather.gov/wrh/timeseries?site=zbaa")
        self.assertEqual(self.truths(), {})

    def test_noaa_primary_in_raw_event_excludes_wu(self):
        self.add_truth()
        self.add_event(raw={
            "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=zbaa",
            "description": "Weather Underground is a conditional fallback.",
        })
        self.assertEqual(self.truths(), {})

    def test_dated_primary_url_precedes_source_label(self):
        self.add_truth()
        self.add_event("wunderground", source_url="https://www.weather.gov/wrh/timeseries?site=zbaa")
        self.assertEqual(self.truths(), {})

    def test_wu_rule_keeps_wu_priority_and_exactness(self):
        self.add_truth("wu")
        self.add_truth("iem")
        self.add_event("wunderground", raw={
            "resolutionSource": "https://www.wunderground.com/history/daily/cn/beijing/ZBAA",
            "description": "NOAA is mentioned only as a fallback.",
        })
        truth = self.truths()["2026-07-01"]
        self.assertEqual(truth["high_c"], 30.0)
        self.assertEqual(truth["basis"], "wunderground_daily")
        self.assertTrue(truth["exact"])

    def test_unknown_rule_keeps_legacy_truth(self):
        self.add_truth()
        self.add_event()
        self.assertEqual(self.truths()["2026-07-01"]["basis"], "wunderground_daily")

    def test_source_label_without_primary_url_keeps_unknown_rule_compatibility(self):
        self.add_truth()
        self.add_event("noaa")
        self.assertEqual(self.truths()["2026-07-01"]["basis"], "wunderground_daily")

    def test_undated_iem_remains_an_approximation(self):
        self.add_truth("iem")
        truth = self.truths()["2026-07-01"]
        self.assertEqual(truth["basis"], "iem_asos_approximation")
        self.assertFalse(truth["exact"])

    def test_noaa_rule_is_scoped_to_city_and_date(self):
        self.add_truth()
        self.add_event("noaa", city="wuhan", source_url="https://www.weather.gov/wrh/timeseries?site=zhhh")
        self.add_event("noaa", target_date="2026-07-02", source_url="https://www.weather.gov/wrh/timeseries?site=zbaa")
        self.assertEqual(self.truths()["2026-07-01"]["basis"], "wunderground_daily")

    def test_current_station_metadata_does_not_rewrite_historical_ownership(self):
        self.add_truth()
        with connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO stations (
                    city_key, city_name, station_id, station_name, timezone, unit,
                    primary_settlement_source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("beijing", "Beijing", "ZBAA", "Beijing Capital", "Asia/Shanghai", "C", "noaa",
                 "2026-09-08T00:00:00Z"),
            )
        self.assertTrue(self.truths()["2026-07-01"]["exact"])

    def test_wu_rule_cannot_admit_truth_backfilled_after_replay_cutoff(self):
        self.add_truth(updated_at="2026-07-11T00:00:00Z")
        self.add_event("wunderground")
        self.assertEqual(self.truths(), {})

    def test_target_date_cutoff_is_unchanged(self):
        self.add_truth()
        self.add_event("wunderground")
        self.assertEqual(self.truths(before_date="2026-07-01"), {})


if __name__ == "__main__":
    unittest.main()
