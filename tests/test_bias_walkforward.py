from __future__ import annotations

import unittest

from weatherbot_v3.bias import _calibration_summary, _walk_forward_errors
from weatherbot_v3.forecasts.ensemble import _bias_for, _mae_for, _predictive_error_for


def record(day: int, residual: float, **extra) -> dict:
    return {
        "target_date": f"2026-07-{day:02d}",
        "forecast_as_of": f"2026-07-{day - 1:02d}T18:00:00Z",
        "truth_available_at": f"2026-07-{day + 1:02d}T06:00:00Z",
        "residual_c": residual,
        **extra,
    }


class BiasWalkForwardTests(unittest.TestCase):
    def test_first_pair_is_raw_predictive_error_not_fitted_zero(self):
        summary = _calibration_summary([record(2, 4.0)])
        self.assertEqual(summary["mae_c"], 0.0)
        self.assertEqual(summary["walk_forward_mae_7d_c"], 4.0)
        self.assertEqual(summary["predictive_error_sample_count"], 1)
        self.assertEqual(_mae_for([{"icao": "KORD", "model": "ecmwf", **summary}], "KORD", "ecmwf"), 4.0)
        self.assertEqual(_predictive_error_for(
            [{"icao": "KORD", "model": "ecmwf", **summary}], "KORD", "ecmwf",
        )["predictive_error_sample_count"], 1)

    def test_training_and_runtime_apply_identical_shrinkage_and_caps(self):
        for residual, prior_count in ((4.0, 1), (20.0, 3), (-20.0, 3)):
            with self.subTest(residual=residual, prior_count=prior_count):
                records = [record(day, residual) for day in range(2, 2 + prior_count)]
                records.append(record(10, residual))
                scored = _walk_forward_errors(records)
                runtime_bias, _ = _bias_for(
                    [{"icao": "KORD", "model": "ecmwf", "sample_count": prior_count,
                      "additive_bias_c": residual}], "KORD", "ecmwf",
                )
                self.assertEqual(scored[-1]["prior_sample_count"], prior_count)
                self.assertEqual(scored[-1]["prior_bias_c"], runtime_bias)
                self.assertEqual(scored[-1]["corrected_error_c"], residual - runtime_bias)
        self.assertEqual(_walk_forward_errors([record(2, 4.0), record(4, 4.0)])[-1]["prior_bias_c"], 0.3636)

    def test_prior_truth_must_be_known_at_forecast_time(self):
        records = [
            record(2, 2.0),
            record(3, 100.0, truth_available_at="2026-07-20T00:00:00Z"),
            record(4, 100.0, truth_available_at=None),
            record(5, 100.0, truth_available_at="2026-07-06T18:00:00Z"),
            record(7, 4.0),
        ]
        last = _walk_forward_errors(records)[-1]
        self.assertEqual(last["prior_sample_count"], 1)
        self.assertEqual(last["prior_bias_c"], 0.1818)
        self.assertEqual(_calibration_summary([record(2, 4.0, forecast_as_of=None)])["predictive_error_sample_count"], 0)

    def test_legacy_and_unversioned_lead_scores_are_not_predictive_evidence(self):
        legacy = {"sample_count": 20, "additive_bias_c": 4.0,
                  "walk_forward_mae_7d_c": 0.0, "mae_7d_c": 0.0}
        row = {"icao": "KORD", "model": "ecmwf", **legacy}
        self.assertIsNone(_mae_for([row], "KORD", "ecmwf"))
        metadata = _predictive_error_for([row], "KORD", "ecmwf")
        self.assertEqual(metadata["predictive_error_basis"], "legacy_unverified")
        self.assertEqual(metadata["predictive_error_sample_count"], 0)
        self.assertIsNone(metadata["predictive_error_c"])
        valid = _calibration_summary([record(2, 4.0)])
        row.update(valid)
        row["lead_calibrations"] = {"d+1": legacy}
        self.assertIsNone(_mae_for([row], "KORD", "ecmwf", lead_bucket="d+1"))
        self.assertEqual(_mae_for([row], "KORD", "ecmwf"), 4.0)


if __name__ == "__main__":
    unittest.main()
