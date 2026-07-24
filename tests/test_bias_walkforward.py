from __future__ import annotations

import unittest

from weatherbot_v3.bias import _walk_forward_errors
from weatherbot_v3.forecasts.ensemble import _mae_for


class BiasWalkForwardTests(unittest.TestCase):
    def test_each_error_uses_only_earlier_target_dates(self):
        records = [
            {"target_date": "2026-07-03", "forecast_run_id": 3, "residual_c": 4.0},
            {"target_date": "2026-07-01", "forecast_run_id": 1, "residual_c": 2.0},
            {"target_date": "2026-07-02", "forecast_run_id": 2, "residual_c": 4.0},
        ]

        scored = _walk_forward_errors(records)

        self.assertEqual([row["target_date"] for row in scored], ["2026-07-02", "2026-07-03"])
        self.assertEqual(scored[0]["prior_sample_count"], 1)
        self.assertAlmostEqual(scored[0]["prior_bias_c"], 2.0)
        self.assertAlmostEqual(scored[0]["corrected_error_c"], 2.0)
        self.assertEqual(scored[1]["prior_sample_count"], 2)
        self.assertAlmostEqual(scored[1]["prior_bias_c"], 3.0)
        self.assertAlmostEqual(scored[1]["corrected_error_c"], 1.0)

    def test_dynamic_weight_prefers_walk_forward_metric(self):
        table = [{
            "icao": "KORD",
            "model": "ecmwf",
            "sample_count": 20,
            "walk_forward_mae_7d_c": 1.25,
            "mae_7d_c": 0.1,
        }]

        self.assertAlmostEqual(_mae_for(table, "KORD", "ecmwf"), 1.25)


if __name__ == "__main__":
    unittest.main()
