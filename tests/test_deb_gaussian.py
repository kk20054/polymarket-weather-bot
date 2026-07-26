from tests import ensure_test_environment

ensure_test_environment()

import json
import math
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from weatherbot_v3.db import (
    connect,
    init_v3_db,
    insert_forecast_run,
    list_daily_max_predictions,
    upsert_metar_report,
    upsert_signal_decision,
)
from weatherbot_v3.deb import (
    _time_decay_factor,
    build_and_store_daily_max_prediction,
    bucket_probabilities,
    probability_mu_for_prediction,
)


TEST_DB_DIR = Path(__file__).resolve().parents[1] / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class DebGaussianTests(unittest.TestCase):
    def test_intraday_sigma_decay_uses_prediction_as_of_not_wall_clock(self):
        self.assertEqual(
            _time_decay_factor(
                "2026-07-18",
                "Asia/Shanghai",
                True,
                as_of="2026-07-18T09:00:00+00:00",
            ),
            0.5,
        )
        self.assertEqual(
            _time_decay_factor(
                "2026-07-19",
                "Asia/Shanghai",
                False,
                as_of="2026-07-18T09:00:00+00:00",
            ),
            1.0,
        )

    def test_probability_distribution_does_not_apply_observed_floor_twice(self):
        probability_mu, basis = probability_mu_for_prediction({
            "mu": 36.5,
            "effective_mu": 36.5,
            "model_mu": 35.7,
            "observed_floor": 37.0,
            "mu_observed_floor_applied": True,
        })

        self.assertAlmostEqual(probability_mu, 35.7)
        self.assertEqual(basis, "model_mu_conditioned_on_observed_floor")

    def test_probability_distribution_uses_effective_mu_without_floor_adjustment(self):
        probability_mu, basis = probability_mu_for_prediction({
            "mu": 25.4,
            "model_mu": 25.4,
            "observed_floor": None,
            "mu_observed_floor_applied": False,
        })

        self.assertAlmostEqual(probability_mu, 25.4)
        self.assertEqual(basis, "effective_mu")

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
        self.assertIsNone(result["items"][0]["bucket_low"])
        self.assertIsNone(result["items"][-1]["bucket_high"])
        json.dumps(result, allow_nan=False)

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

    def test_observed_celsius_max_marks_impossible_buckets_with_numerical_floor(self):
        result = bucket_probabilities(
            38.5,
            1.7,
            [
                {"bucket_key": "37", "bucket_low": 37.0, "bucket_high": 37.0, "unit": "C"},
                {"bucket_key": "38", "bucket_low": 38.0, "bucket_high": 38.0, "unit": "C"},
                {"bucket_key": "39", "bucket_low": 39.0, "bucket_high": 39.0, "unit": "C"},
                {"bucket_key": "40+", "bucket_low": 40.0, "bucket_direction": "or_above", "unit": "C"},
            ],
            unit="C",
            observed_floor=39.0,
        )

        probabilities = {item["bucket_key"]: item["probability"] for item in result["items"]}
        excluded = {item["bucket_key"]: item["observed_floor_excluded"] for item in result["items"]}
        self.assertGreater(probabilities["37"], 0.0)
        self.assertGreater(probabilities["38"], 0.0)
        self.assertLess(probabilities["37"], 1e-8)
        self.assertLess(probabilities["38"], 1e-8)
        self.assertTrue(excluded["37"])
        self.assertTrue(excluded["38"])
        self.assertGreater(probabilities["39"], 0.0)
        self.assertGreater(probabilities["40+"], 0.0)
        self.assertAlmostEqual(result["sum_probability"], 1.0, places=6)
        self.assertTrue(result["observed_floor_applied_to_distribution"])
        self.assertEqual(result["observed_floor_excluded_bucket_count"], 2)
        self.assertIn("conditioned_on_observed_daily_max", result["notes"])

    def test_observed_fahrenheit_max_keeps_inclusive_range_bucket(self):
        result = bucket_probabilities(
            81.0,
            2.0,
            [
                {"bucket_key": "78-79", "bucket_low": 78.0, "bucket_high": 79.0, "unit": "F"},
                {"bucket_key": "80-81", "bucket_low": 80.0, "bucket_high": 81.0, "unit": "F"},
                {"bucket_key": "82+", "bucket_low": 82.0, "bucket_direction": "or_above", "unit": "F"},
            ],
            unit="F",
            observed_floor=81.0,
        )

        probabilities = {item["bucket_key"]: item["probability"] for item in result["items"]}
        excluded = {item["bucket_key"]: item["observed_floor_excluded"] for item in result["items"]}
        self.assertGreater(probabilities["78-79"], 0.0)
        self.assertLess(probabilities["78-79"], 1e-8)
        self.assertTrue(excluded["78-79"])
        self.assertGreater(probabilities["80-81"], 0.0)
        self.assertGreater(probabilities["82+"], 0.0)
        self.assertAlmostEqual(result["sum_probability"], 1.0, places=6)

    def test_daily_max_prediction_applies_observed_temperature_floor(self):
        path = test_db_path("deb_observed_floor")
        with patch("weatherbot_v3.db.load_config", return_value=SimpleNamespace(v3_db_path=path)), patch.dict(
            os.environ, {"WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false"}, clear=False
        ):
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
                    "valid_at": "2026-07-02T12:00:00Z",
                    "horizon": "d0",
                    "timezone": "Europe/Paris",
                    "parse_status": "parsed",
                    "training_eligible": True,
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

            prediction = build_and_store_daily_max_prediction(
                "paris", "2026-07-02", issued_at="2026-07-02T12:30:00Z", path=path
            )

            self.assertTrue(prediction["ok"])
            self.assertTrue(prediction["mu_observed_floor_applied"])
            self.assertAlmostEqual(prediction["model_mu"], 25.0, places=6)
            self.assertEqual(prediction["effective_mu"], prediction["mu"])
            self.assertGreaterEqual(prediction["mu"], 27.5)
            self.assertGreaterEqual(prediction["sigma"], prediction["sigma_floor"])
            stored = list_daily_max_predictions(city_key="paris", target_date="2026-07-02", path=path)[0]
            self.assertAlmostEqual(stored["model_mu"], 25.0, places=6)
            self.assertEqual(stored["effective_mu"], stored["mu"])
            self.assertEqual(stored["mu_basis"], "observed_floor_adjusted")

    def test_tokyo_observed_floor_keeps_deb_above_intraday_max_minus_half_c(self):
        path = test_db_path("deb_tokyo_observed_floor")
        with patch("weatherbot_v3.db.load_config", return_value=SimpleNamespace(v3_db_path=path)), patch.dict(
            os.environ, {"WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false"}, clear=False
        ):
            init_v3_db(path)
            insert_forecast_run(
                {
                    "run_key": "jma:tokyo:2026-07-05",
                    "city": "tokyo",
                    "target_date": "2026-07-05",
                    "source": "openmeteo_jma_seamless",
                    "unit": "C",
                    "mean_high": 24.0,
                    "std_high": 0.0,
                    "member_count": 1,
                    "retrieved_at": "2026-07-05T00:00:00Z",
                    "valid_at": "2026-07-05T06:00:00Z",
                    "horizon": "d0",
                    "timezone": "Asia/Tokyo",
                    "parse_status": "parsed",
                    "training_eligible": True,
                },
                [{"member_id": "m0", "high_temp": 24.0}],
            )
            upsert_metar_report({
                "report_key": "rjtt-20260705-0014",
                "city": "tokyo",
                "station_id": "RJTT",
                "report_time": "2026-07-05T00:14:00Z",
                "temperature": 26.0,
                "parser_version": "iem-asos-csv-v1",
                "raw_text": "RJTT 050014Z 18008KT 9999 FEW020 26/21 Q1009",
            })

            prediction = build_and_store_daily_max_prediction(
                "tokyo", "2026-07-05", issued_at="2026-07-05T01:00:00Z", path=path
            )

            self.assertTrue(prediction["ok"])
            self.assertTrue(prediction["mu_observed_floor_applied"])
            self.assertGreaterEqual(prediction["mu"], 25.5)

    def test_chicago_fahrenheit_forecast_is_not_double_converted(self):
        path = test_db_path("deb_chicago_unit_regression")
        with patch("weatherbot_v3.db.load_config", return_value=SimpleNamespace(v3_db_path=path)), patch.dict(
            os.environ, {"WEATHERBOT_ENSEMBLE_DEB_ENABLED": "false"}, clear=False
        ):
            init_v3_db(path)
            insert_forecast_run(
                {
                    "run_key": "openmeteo:chicago:2026-07-04:ecmwf",
                    "city": "chicago",
                    "target_date": "2026-07-04",
                    "source": "openmeteo_ecmwf_ifs025",
                    "unit": "F",
                    "mean_high": 94.9,
                    "std_high": 0.0,
                    "member_count": 1,
                    "retrieved_at": "2026-07-04T06:00:00Z",
                    "valid_at": "2026-07-04T20:00:00Z",
                    "horizon": "d0",
                    "timezone": "America/Chicago",
                    "parse_status": "parsed",
                    "training_eligible": True,
                },
                [{"member_id": "m0", "high_temp": 94.9}],
            )

            prediction = build_and_store_daily_max_prediction(
                "chicago", "2026-07-04", issued_at="2026-07-04T20:30:00Z", path=path
            )

            self.assertTrue(prediction["ok"])
            self.assertEqual(prediction["unit"], "F")
            self.assertLess(prediction["mu"], 96.0)
            self.assertGreater(prediction["mu"], 93.0)

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
