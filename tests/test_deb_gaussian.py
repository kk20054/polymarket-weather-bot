import json
import math
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from weatherbot_v3.db import (
    connect,
    init_v3_db,
    insert_forecast_run,
    upsert_metar_report,
    upsert_signal_decision,
)
from weatherbot_v3.deb import (
    build_and_store_daily_max_prediction,
    bucket_probabilities,
)


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class DebGaussianTests(unittest.TestCase):
    def test_celsius_bucket_integral_uses_truncated_integer_interval(self):
        result = bucket_probabilities(
            25.0,
            1.0,
            [{"bucket_key": "mid", "bucket_low": 24.0, "bucket_high": 26.0, "unit": "C"}],
            unit="C",
            normalize=False,
        )

        probability = result["items"][0]["probability"]
        self.assertAlmostEqual(probability, 0.818595, places=5)
        self.assertEqual(result["items"][0]["bucket_low"], 24.0)
        self.assertEqual(result["items"][0]["bucket_high"], 27.0)

    def test_open_tail_buckets_normalize_to_one(self):
        result = bucket_probabilities(
            25.0,
            2.0,
            [
                {"bucket_key": "low", "bucket_high": 20.0, "bucket_direction": "or_below", "unit": "C"},
                {"bucket_key": "mid", "bucket_low": 20.0, "bucket_high": 30.0, "unit": "C"},
                {"bucket_key": "high", "bucket_low": 30.0, "bucket_direction": "or_above", "unit": "C"},
            ],
            unit="C",
            normalize=True,
        )

        self.assertTrue(result["normalized"])
        self.assertAlmostEqual(result["sum_probability"], 1.0, places=6)

    def test_sigma_floor_prevents_nan(self):
        result = bucket_probabilities(
            25.0,
            0.0,
            [{"bucket_key": "mid", "bucket_low": 24.0, "bucket_high": 26.0, "unit": "C"}],
            unit="C",
            sigma_floor=0.5,
            normalize=False,
        )

        item = result["items"][0]
        self.assertTrue(result["sigma_floor_applied"])
        self.assertTrue(math.isfinite(result["sigma"]))
        self.assertTrue(math.isfinite(item["probability"]))

    def test_daily_max_prediction_applies_observed_temperature_floor(self):
        path = test_db_path("deb_observed_floor")
        with patch("weatherbot_v3.db.load_config", return_value=SimpleNamespace(v3_db_path=path)):
            init_v3_db(path)
            insert_forecast_run(
                {
                    "run_key": "ecmwf:paris:2026-07-02",
                    "city": "paris",
                    "target_date": "2026-07-02",
                    "source": "ecmwf",
                    "unit": "C",
                    "mean_high": 25.0,
                    "std_high": 0.0,
                    "member_count": 1,
                    "retrieved_at": "2026-07-02T06:00:00Z",
                },
                [{"member_id": "m0", "high_temp": 25.0}],
            )
            upsert_metar_report({
                "report_key": "lfpb-20260702-1200",
                "city": "paris",
                "station_id": "LFPB",
                "report_time": "2026-07-02T12:00:00",
                "temperature": 28.0,
                "raw_text": "METAR LFPB 021200Z AUTO 00000KT CAVOK 28/16 Q1015",
            })

            prediction = build_and_store_daily_max_prediction("paris", "2026-07-02", path=path)

            self.assertTrue(prediction["ok"])
            self.assertTrue(prediction["mu_observed_floor_applied"])
            self.assertGreaterEqual(prediction["mu"], 28.0)
            self.assertGreaterEqual(prediction["sigma"], prediction["sigma_floor"])

    def test_signal_decision_probability_skeleton_fields_are_persisted(self):
        path = test_db_path("deb_signal_decision_fields")
        with patch("weatherbot_v3.db.load_config", return_value=SimpleNamespace(v3_db_path=path)):
            init_v3_db(path)
            upsert_signal_decision(
                77,
                {
                    "market_id": "market-77",
                    "action": "observe",
                    "paper_allowed": False,
                    "live_allowed": False,
                    "model_distribution": {"mu": 25.0, "sigma": 1.0, "unit": "C"},
                    "model_bucket_probs": {"items": [{"bucket_key": "mid", "probability": 0.68}]},
                    "market_bucket_probs": [{"bucket_key": "mid", "market_probability": 0.5}],
                    "edge_by_bucket": {"mid": {"edge": 0.18}},
                    "gate_reasons": ["layer_6_not_connected_to_execution"],
                },
                path=path,
            )

            with connect(path) as conn:
                row = conn.execute(
                    """
                    SELECT model_distribution_json, model_bucket_probs_json,
                           market_bucket_probs_json, edge_by_bucket_json, gate_reasons_json
                    FROM signal_decisions
                    WHERE signal_id = 77
                    """
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(json.loads(row["model_distribution_json"])["mu"], 25.0)
            self.assertEqual(json.loads(row["model_bucket_probs_json"])["items"][0]["bucket_key"], "mid")
            self.assertEqual(json.loads(row["market_bucket_probs_json"])[0]["market_probability"], 0.5)
            self.assertEqual(json.loads(row["edge_by_bucket_json"])["mid"]["edge"], 0.18)
            self.assertEqual(json.loads(row["gate_reasons_json"])[0], "layer_6_not_connected_to_execution")


if __name__ == "__main__":
    unittest.main()
