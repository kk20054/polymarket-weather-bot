from tests import ensure_test_environment

ensure_test_environment()

import unittest
from datetime import date, timedelta

from weatherbot_v3.db import connect, init_v3_db
from weatherbot_v3.signals import _independent_settlement_evidence
from weatherbot_v3.stations import sync_station_registry

from tests.test_v3_core import test_db_path


class SignalQualityContractTests(unittest.TestCase):
    def test_wunderground_daily_counts_as_authoritative_independent_truth(self):
        path = test_db_path("signal_quality_wu_truth")
        init_v3_db(path)
        sync_station_registry(path)
        with connect(path) as conn:
            conn.execute(
                "UPDATE stations SET primary_settlement_source='wunderground' WHERE city_key='chicago'"
            )
            for offset in range(20):
                target = (date(2026, 6, 1) + timedelta(days=offset)).isoformat()
                conn.execute(
                    """
                    INSERT INTO truth_wunderground_daily (
                        truth_key, icao, date_local, timezone, high_c,
                        settlement_truth_type, parser_version, created_at, updated_at
                    ) VALUES (?, 'KORD', ?, 'America/Chicago', 30.0,
                              'wunderground_daily', 'test-v1', ?, ?)
                    """,
                    (f"wu:KORD:{target}", target, target, target),
                )

        evidence = _independent_settlement_evidence("chicago", path, {"bias_sample_count": 0})

        self.assertEqual(evidence["days"], 20)
        self.assertEqual(evidence["basis"], "wunderground_daily")
        self.assertTrue(evidence["authoritative"])

    def test_iem_only_history_remains_non_authoritative(self):
        path = test_db_path("signal_quality_iem_truth")
        init_v3_db(path)
        sync_station_registry(path)
        with connect(path) as conn:
            for offset in range(20):
                target = (date(2026, 6, 1) + timedelta(days=offset)).isoformat()
                conn.execute(
                    """
                    INSERT INTO truth_iem_daily (
                        truth_key, icao, date_local, timezone, high_c,
                        settlement_truth_type, parser_version, created_at, updated_at
                    ) VALUES (?, 'KORD', ?, 'America/Chicago', 30.0,
                              'iem_asos_approximation', 'test-v1', ?, ?)
                    """,
                    (f"iem:KORD:{target}", target, target, target),
                )

        evidence = _independent_settlement_evidence("chicago", path, {"bias_sample_count": 0})

        self.assertEqual(evidence["days"], 20)
        self.assertEqual(evidence["basis"], "iem_asos_approximation")
        self.assertFalse(evidence["authoritative"])


if __name__ == "__main__":
    unittest.main()
