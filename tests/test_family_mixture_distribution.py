from __future__ import annotations

import unittest

from weatherbot_v3.forecasts.ensemble import (
    POLYWX_ALIGNED_ALGO,
    family_mixture_distribution_for_prediction,
)


class FamilyMixtureDistributionTests(unittest.TestCase):
    @staticmethod
    def _buckets() -> list[dict]:
        return [
            {
                "bucket_key": "low",
                "bucket_label": "29C or below",
                "bucket_high": 29,
                "bucket_direction": "or_below",
                "unit": "C",
            },
            {
                "bucket_key": "30",
                "bucket_label": "30C",
                "bucket_low": 30,
                "bucket_high": 30,
                "bucket_direction": "exact",
                "unit": "C",
            },
            {
                "bucket_key": "31",
                "bucket_label": "31C",
                "bucket_low": 31,
                "bucket_high": 31,
                "bucket_direction": "exact",
                "unit": "C",
            },
            {
                "bucket_key": "high",
                "bucket_label": "32C or above",
                "bucket_low": 32,
                "bucket_direction": "or_above",
                "unit": "C",
            },
        ]

    def test_real_members_are_mixed_by_family_weight(self):
        prediction = {
            "forecast_algo": POLYWX_ALIGNED_ALGO,
            "unit": "C",
            "mu": 31.0,
            "sigma": 1.0,
            "components": [
                {
                    "source": "openmeteo_ensemble_gfs_seamless",
                    "family": "gfs",
                    "weight": 0.4,
                    "adjusted_daily_highs_c": [30.1, 30.2, 30.3, 32.1, 32.2],
                    "model_daily_high_c": 31.0,
                    "effective_mae_c": 0.8,
                },
                {
                    "source": "weathercom_v3_forecast",
                    "family": "weathercom_v3",
                    "weight": 0.6,
                    "adjusted_daily_highs_c": [31.0],
                    "model_daily_high_c": 31.0,
                    "effective_mae_c": 0.5,
                },
            ],
        }

        distribution = family_mixture_distribution_for_prediction(
            prediction,
            self._buckets(),
        )

        self.assertEqual(distribution["method"], "family-mixture-v1")
        self.assertAlmostEqual(distribution["sum_probability"], 1.0)
        self.assertEqual(distribution["family_methods"]["gfs"], "empirical_members")
        self.assertEqual(
            distribution["family_methods"]["weathercom_v3"],
            "calibrated_gaussian_kernel",
        )
        gfs_mass = sum(
            row["family_contributions"].get("gfs", 0.0)
            for row in distribution["items"]
        )
        self.assertAlmostEqual(gfs_mass, 0.4, places=6)

    def test_deterministic_only_prediction_keeps_global_gaussian_fallback(self):
        prediction = {
            "forecast_algo": POLYWX_ALIGNED_ALGO,
            "components": [{
                "source": "weathercom_v3_forecast",
                "family": "weathercom_v3",
                "weight": 1.0,
                "adjusted_daily_highs_c": [31.0],
                "model_daily_high_c": 31.0,
                "effective_mae_c": 0.5,
            }],
        }
        self.assertIsNone(
            family_mixture_distribution_for_prediction(prediction, self._buckets())
        )


if __name__ == "__main__":
    unittest.main()
