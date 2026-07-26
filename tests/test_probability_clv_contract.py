from tests import ensure_test_environment

ensure_test_environment()

import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from weatherbot_v3.db import (
    connect,
    init_v3_db,
    insert_orderbook,
    list_signal_decisions,
    upsert_signal_decision_record,
)
from weatherbot_v3.deb import MIN_BUCKET_PROBABILITY, bucket_probabilities
from weatherbot_v3.forecasts.ensemble import (
    POLYWX_ALIGNED_MODEL_WEIGHTS,
    _apply_mae_adjusted_weights,
    _bias_for,
)
from weatherbot_v3.orderbook_replay import select_orderbook_as_of
from weatherbot_v3.signals import d0_peak_decision_window


class ProbabilityClvContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "weatherbot.db"
        init_v3_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_clean_gaussian_distribution_is_positive_and_normalized(self):
        result = bucket_probabilities(
            25.0,
            0.5,
            [
                {"bucket_key": "24-or-below", "bucket_high": 24.0, "bucket_direction": "or_below", "unit": "C"},
                {"bucket_key": "25", "bucket_low": 25.0, "bucket_high": 25.0, "unit": "C"},
                {"bucket_key": "26-or-above", "bucket_low": 26.0, "bucket_direction": "or_above", "unit": "C"},
            ],
            unit="C",
            observed_floor=25.5,
            normalize=True,
        )

        probabilities = [float(item["probability"]) for item in result["items"]]
        self.assertAlmostEqual(sum(probabilities), 1.0, places=12)
        self.assertTrue(all(value >= MIN_BUCKET_PROBABILITY / 2 for value in probabilities))
        self.assertTrue(all(math.isfinite(value) for value in probabilities))

    def test_persisted_probabilities_match_clean_gaussian_reconstruction(self):
        result = bucket_probabilities(
            25.0,
            1.0,
            [
                {"bucket_key": "24-or-below", "bucket_high": 24.0, "bucket_direction": "or_below", "unit": "C"},
                {"bucket_key": "25", "bucket_low": 25.0, "bucket_high": 25.0, "unit": "C"},
                {"bucket_key": "26-or-above", "bucket_low": 26.0, "bucket_direction": "or_above", "unit": "C"},
            ],
            unit="C",
            normalize=True,
        )
        expected = {
            str(item["bucket_key"]): float(item["probability"])
            for item in result["items"]
        }
        for bucket_key, probability in expected.items():
            upsert_signal_decision_record(
                {
                    "decision_id": f"decision-{bucket_key}",
                    "city_key": "shanghai",
                    "market_id": "market-1",
                    "bucket_key": bucket_key,
                    "target_date": "2026-07-27",
                    "issued_at": "2026-07-26T04:00:00+00:00",
                    "model_probability": probability,
                    "market_ask": 0.2,
                    "model_distribution": result,
                    "model_bucket_probs": expected,
                    "gate_reasons": [],
                },
                path=self.db_path,
            )

        stored = {
            str(row["bucket_key"]): float(row["model_probability"])
            for row in list_signal_decisions(limit=20, path=self.db_path)
        }
        self.assertEqual(set(stored), set(expected))
        for bucket_key, probability in expected.items():
            self.assertAlmostEqual(stored[bucket_key], probability, places=12)

    def test_decision_time_quote_is_immutable_across_reprice_upsert(self):
        base = {
            "decision_id": "decision-quote",
            "city_key": "chicago",
            "market_id": "market-quote",
            "bucket_key": "80-81",
            "target_date": "2026-07-27",
            "issued_at": "2026-07-26T10:00:00+00:00",
            "model_probability": 0.3,
            "market_ask": 0.12,
            "decision_time_ask": 0.12,
            "quote_age_at_decision_seconds": 20.0,
            "gate_reasons": [],
        }
        upsert_signal_decision_record(base, path=self.db_path)
        upsert_signal_decision_record(
            {
                **base,
                "market_ask": 0.20,
                "decision_time_ask": 0.20,
                "quote_age_at_decision_seconds": 3.0,
            },
            path=self.db_path,
        )

        row = list_signal_decisions(limit=1, path=self.db_path)[0]
        self.assertAlmostEqual(float(row["market_ask"]), 0.20)
        self.assertAlmostEqual(float(row["decision_time_ask"]), 0.12)
        self.assertAlmostEqual(float(row["quote_age_at_decision_seconds"]), 20.0)

    def test_orderbook_replay_understands_epoch_milliseconds(self):
        timestamp_ms = int(datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        insert_orderbook(
            "market-epoch",
            {
                "snapshot_key": "epoch-book",
                "yes_token_id": "token-epoch",
                "bids": [{"price": 0.20, "size": 10}],
                "asks": [{"price": 0.22, "size": 10}],
                "timestamp": str(timestamp_ms),
                "snapshot_type": "clob",
            },
            path=self.db_path,
        )

        with connect(self.db_path) as conn:
            row = select_orderbook_as_of(
                conn,
                yes_token_id="token-epoch",
                market_id="market-epoch",
                decision_time="2026-07-26T10:01:00+00:00",
            )
        self.assertIsNotNone(row)
        self.assertAlmostEqual(float(row["best_ask"]), 0.22)

    def test_cold_start_uses_v3_and_keeps_gem_jma_diagnostic_only(self):
        self.assertEqual(POLYWX_ALIGNED_MODEL_WEIGHTS["gem"], 0.0)
        self.assertEqual(POLYWX_ALIGNED_MODEL_WEIGHTS["jma"], 0.0)
        components = [
            {
                "family": family,
                "weight_prior": prior,
                "bias_sample_count": 5,
                "effective_mae_c": 1.0,
            }
            for family, prior in POLYWX_ALIGNED_MODEL_WEIGHTS.items()
        ]
        _apply_mae_adjusted_weights(components)

        weights = {row["family"]: float(row.get("weight") or 0.0) for row in components}
        self.assertEqual(weights["weathercom_v3"], 1.0)
        self.assertTrue(all(weight == 0.0 for family, weight in weights.items() if family != "weathercom_v3"))

    def test_bias_prefers_station_lead_calibration(self):
        table = [{
            "icao": "KORD",
            "model": "weathercom_v3",
            "sample_count": 30,
            "additive_bias_c": 2.0,
            "lead_calibrations": {
                "d0": {"sample_count": 20, "additive_bias_c": 1.0},
                "d1": {"sample_count": 20, "additive_bias_c": -1.0},
            },
        }]
        d0_bias, d0_count = _bias_for(table, "KORD", "weathercom_v3", lead_bucket="d0")
        d1_bias, d1_count = _bias_for(table, "KORD", "weathercom_v3", lead_bucket="d1")

        self.assertEqual(d0_count, 20)
        self.assertEqual(d1_count, 20)
        self.assertGreater(d0_bias, 0)
        self.assertLess(d1_bias, 0)

    def test_d0_decision_window_is_two_to_three_hours_before_peak(self):
        prediction = {"target_date": "2026-07-26", "peak_hour": "15:00"}
        inside = d0_peak_decision_window(
            prediction,
            timezone_name="Asia/Shanghai",
            as_of="2026-07-26T04:30:00+00:00",
        )
        too_early = d0_peak_decision_window(
            prediction,
            timezone_name="Asia/Shanghai",
            as_of="2026-07-26T03:00:00+00:00",
        )
        next_day = d0_peak_decision_window(
            {"target_date": "2026-07-27", "peak_hour": "15:00"},
            timezone_name="Asia/Shanghai",
            as_of="2026-07-26T04:30:00+00:00",
        )
        cross_day = d0_peak_decision_window(
            {"target_date": "2026-07-27", "peak_hour": "00:00"},
            timezone_name="Asia/Shanghai",
            as_of="2026-07-26T13:30:00+00:00",
        )

        self.assertTrue(inside["enforced"])
        self.assertTrue(inside["ok"])
        self.assertLessEqual(float(inside["hours_before_peak"]), 3.0)
        self.assertGreaterEqual(float(inside["hours_before_peak"]), 2.0)
        self.assertFalse(too_early["ok"])
        self.assertFalse(next_day["enforced"])
        self.assertTrue(next_day["ok"])
        self.assertTrue(cross_day["enforced"])
        self.assertTrue(cross_day["ok"])
        self.assertEqual(cross_day["reason"], "cross_day_peak_window")


if __name__ == "__main__":
    unittest.main()
