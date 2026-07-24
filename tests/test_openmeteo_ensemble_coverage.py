from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from weatherbot_v3.openmeteo import openmeteo_runs_from_response


class OpenMeteoEnsembleCoverageTests(unittest.TestCase):
    @staticmethod
    def _payload(hours: int) -> dict:
        start = datetime(2026, 7, 1, 5, tzinfo=timezone.utc)
        times = [
            (start + timedelta(hours=offset)).strftime("%Y-%m-%dT%H:%M")
            for offset in range(hours)
        ]
        temperatures = [24.0 + min(offset, 10) * 0.2 for offset in range(hours)]
        return {
            "hourly": {
                "time": times,
                "temperature_2m": temperatures,
                "temperature_2m_member01": [value + 0.2 for value in temperatures],
                "temperature_2m_member02": [value - 0.2 for value in temperatures],
            }
        }

    def test_complete_local_day_remains_training_eligible(self):
        runs, _members = openmeteo_runs_from_response(
            "chicago",
            "gfs_seamless",
            self._payload(24),
            retrieved_at="2026-06-30T23:00:00Z",
            endpoint_kind="ensemble",
        )

        run = next(row for row in runs if row["target_date"] == "2026-07-01")
        self.assertEqual(run["parse_status"], "parsed")
        self.assertTrue(run["training_eligible"])
        self.assertEqual(run["meta"]["minimum_member_hour_count"], 24)

    def test_partial_local_day_is_persisted_but_not_training_eligible(self):
        runs, members = openmeteo_runs_from_response(
            "chicago",
            "gfs_seamless",
            self._payload(8),
            retrieved_at="2026-06-30T23:00:00Z",
            endpoint_kind="ensemble",
        )

        run = next(row for row in runs if row["target_date"] == "2026-07-01")
        self.assertEqual(run["parse_status"], "partial")
        self.assertFalse(run["training_eligible"])
        self.assertEqual(run["ineligibility_reason"], "incomplete_ensemble_local_day")
        self.assertIn("incomplete_local_day_coverage", run["quality_flags"])
        self.assertTrue(any("8/24_hours" in warning for warning in run["parse_warnings"]))
        self.assertEqual(len(members[runs.index(run)]), 3)


if __name__ == "__main__":
    unittest.main()
