from __future__ import annotations

import unittest

from weatherbot_v3.forecasts.ensemble import (
    DYNAMIC_WEIGHT_MAX_SHARE,
    _apply_mae_adjusted_weights,
    _best_source_group,
    _mae_for,
)
from weatherbot_v3.strategies.core_modal import CoreModalStrategy


def component(family: str, *, prior: float, samples: int, mae: float | None) -> dict:
    return {
        "source": f"source_{family}",
        "family": family,
        "weight_prior": prior,
        "weight_raw": prior,
        "weight": 0.0,
        "weight_after_mae": 0.0,
        "bias_sample_count": samples,
        "mae_7d": mae,
        "member_count": 1,
        "adjusted_daily_highs_c": [30.0],
    }


class DynamicModelWeightTests(unittest.TestCase):
    def test_stale_ensemble_does_not_shadow_fresh_deterministic_family_source(self):
        rows = [
            *[
                {
                    "source": "openmeteo_ensemble_gfs_seamless",
                    "run_id": 10,
                    "available_at": "2026-07-20T00:00:00+00:00",
                }
                for _index in range(31)
            ],
            {
                "source": "openmeteo_gfs_seamless",
                "run_id": 20,
                "available_at": "2026-07-21T00:00:00+00:00",
            },
        ]

        selected = _best_source_group(rows)

        self.assertEqual({row["source"] for row in selected}, {"openmeteo_gfs_seamless"})

    def test_recent_ensemble_remains_preferred_over_deterministic_source(self):
        rows = [
            *[
                {
                    "source": "openmeteo_ensemble_gfs_seamless",
                    "run_id": 10,
                    "available_at": "2026-07-21T00:00:00+00:00",
                }
                for _index in range(31)
            ],
            {
                "source": "openmeteo_gfs_seamless",
                "run_id": 20,
                "available_at": "2026-07-21T06:00:00+00:00",
            },
        ]

        selected = _best_source_group(rows)

        self.assertEqual(
            {row["source"] for row in selected},
            {"openmeteo_ensemble_gfs_seamless"},
        )

    def test_unmatured_v3_is_the_cold_start_source_while_other_models_remain_diagnostic(self):
        rows = [
            component("weathercom_v3", prior=0.484, samples=7, mae=None),
            component("gfs", prior=0.152, samples=24, mae=0.9),
            component("ecmwf", prior=0.104, samples=24, mae=0.7),
            component("icon", prior=0.095, samples=24, mae=0.8),
            component("gem", prior=0.093, samples=24, mae=1.1),
            component("jma", prior=0.073, samples=24, mae=1.0),
        ]

        _apply_mae_adjusted_weights(rows)

        v3 = rows[0]
        self.assertEqual(v3["weight"], 1.0)
        self.assertEqual(v3["weight_status"], "cold_start_v3_only")
        self.assertAlmostEqual(sum(row["weight"] for row in rows), 1.0, places=9)
        self.assertTrue(all(row["weight"] == 0.0 for row in rows[1:]))
        self.assertTrue(all(row["weight_status"] == "diagnostic_only" for row in rows[1:]))

    def test_sparse_error_does_not_override_v3_cold_start_before_maturity(self):
        bias_rows = [{
            "icao": "ZSPD",
            "model": "jma",
            "sample_count": 1,
            "walk_forward_mae_7d_c": 0.5,
            "location_version": 1,
        }]
        mae = _mae_for(bias_rows, "ZSPD", "jma")
        rows = [
            component("weathercom_v3", prior=0.484, samples=1, mae=2.0),
            component("jma", prior=0.073, samples=1, mae=mae),
            component("gfs", prior=0.152, samples=0, mae=None),
        ]

        _apply_mae_adjusted_weights(rows)

        self.assertIsNone(mae)
        self.assertEqual(rows[0]["weight"], 1.0)
        self.assertEqual(rows[1]["weight_status"], "diagnostic_only")
        self.assertEqual(rows[1]["weight"], 0.0)
        self.assertAlmostEqual(sum(row["weight"] for row in rows), 1.0, places=9)

    def test_v3_enters_gradually_after_twenty_leakage_free_pairs(self):
        rows = [
            component("weathercom_v3", prior=0.484, samples=20, mae=0.6),
            component("gfs", prior=0.152, samples=40, mae=0.8),
            component("ecmwf", prior=0.104, samples=40, mae=0.7),
            component("icon", prior=0.095, samples=40, mae=0.9),
        ]

        _apply_mae_adjusted_weights(rows)

        self.assertGreater(rows[0]["weight"], 0.0)
        self.assertEqual(rows[0]["weight_status"], "active")
        self.assertAlmostEqual(rows[0]["sample_maturity"], 0.5)
        self.assertLessEqual(rows[0]["weight"], DYNAMIC_WEIGHT_MAX_SHARE + 1e-9)

    def test_core_quality_keeps_prior_only_models_but_excludes_them_from_calibrated_coverage(self):
        strategy = CoreModalStrategy()
        prediction = {
            "components": [
                {**component("weathercom_v3", prior=0.484, samples=7, mae=None), "weight": 0.3, "mae_imputed": True},
                {**component("gfs", prior=0.25, samples=24, mae=0.8), "weight": 0.35, "mae_imputed": False},
                {**component("ecmwf", prior=0.25, samples=24, mae=0.7), "weight": 0.35, "mae_imputed": False},
            ],
        }

        quality = strategy._prediction_quality(prediction, {})

        self.assertEqual(quality["families"], ["ecmwf", "gfs", "weathercom_v3"])
        self.assertEqual(quality["family_count"], 3)
        self.assertAlmostEqual(quality["calibration_coverage"], 0.7)


if __name__ == "__main__":
    unittest.main()
