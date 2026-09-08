from tests import ensure_test_environment

ensure_test_environment()

import json
import math
import sqlite3
import unittest
from unittest.mock import patch

from weatherbot_v3.deb import bucket_bounds_in_prediction_unit, bucket_excluded_by_observed_floor, bucket_probabilities
from weatherbot_v3.forecasts.ensemble import distribution_for_prediction
from weatherbot_v3.paper_settlement import bucket_contains_celsius, truth_outcome_for_order
from weatherbot_v3.settlement_temperature import NOAA_ROUNDING_RULE, is_noaa_primary, reported_temperature


SOURCE = "https://www.weather.gov/wrh/timeseries?site=ZBAA"


def bucket(low, high, direction="exact", unit="C"):
    return {
        "bucket_key": f"{low}:{high}", "bucket_low": low, "bucket_high": high,
        "bucket_direction": direction, "unit": unit,
        "raw_json": json.dumps({"resolutionSource": SOURCE}),
    }


class NoaaBucketContractTests(unittest.TestCase):
    def test_primary_source_not_fallback_mention_selects_rule(self):
        self.assertTrue(is_noaa_primary(bucket(29, 29)))
        self.assertFalse(is_noaa_primary({
            "resolutionSource": "https://www.wunderground.com/history/daily/ZBAA",
            "description": f"Fallback: {SOURCE}",
        }))
        self.assertFalse(is_noaa_primary({"resolutionSource": "https://weather.gov.example/wrh/timeseries"}))
        self.assertTrue(is_noaa_primary({"raw_json": json.dumps({
            "description": f'The resolution source for this market will be information from NOAA, available here: {SOURCE}\n\nIf NOAA data is unavailable, use Weather Underground.',
            "source_url": "https://gamma-api.polymarket.com/events/slug/example",
        })}))
        self.assertFalse(is_noaa_primary({"description": f'If primary data is unavailable, use NOAA: {SOURCE}'}))
        self.assertEqual(bucket_bounds_in_prediction_unit({"bucket_low": 29, "bucket_high": 29, "unit": "C"}, "C"), (29, 30))

    def test_noaa_nearest_degree_including_negative_and_half_ties(self):
        for actual, expected in ((28.49, 28), (28.5, 29), (28.79, 29), (-2.5, -2), (-2.51, -3)):
            with self.subTest(actual=actual):
                row = bucket(expected, expected)
                self.assertEqual(reported_temperature(actual, "C", row), expected)
                self.assertTrue(bucket_contains_celsius(actual, row))
        self.assertFalse(bucket_contains_celsius(28.5, bucket(28, 28)))

    def test_noaa_celsius_tails_and_ranges_partition_without_gaps(self):
        rows = [bucket(-999, 27, "or_below"), bucket(28, 29, "range"), bucket(30, 999, "or_above")]
        self.assertEqual([bucket_bounds_in_prediction_unit(row, "C") for row in rows], [(-math.inf, 27.5), (27.5, 29.5), (29.5, math.inf)])
        result = bucket_probabilities(28.8, 1.0, rows)
        self.assertAlmostEqual(result["unconditioned_raw_sum_probability"], 1)
        self.assertAlmostEqual(result["sum_probability"], 1)
        self.assertTrue(all(item["probability"] > 0 for item in result["items"]))
        self.assertTrue(all(item["settlement_rounding_rule"] == NOAA_ROUNDING_RULE for item in result["items"]))
        json.dumps(result, allow_nan=False)

    def test_noaa_fahrenheit_ranges_convert_shared_edges_to_celsius(self):
        rows = [bucket(-999, 79, "or_below", "F"), bucket(80, 81, "range", "F"), bucket(82, 999, "or_above", "F")]
        edges = [bucket_bounds_in_prediction_unit(row, "C") for row in rows]
        self.assertEqual(edges[0][1], edges[1][0])
        self.assertEqual(edges[1][1], edges[2][0])
        self.assertAlmostEqual(edges[1][0], (79.5 - 32) * 5 / 9)
        self.assertAlmostEqual(bucket_probabilities(27, 1, rows)["unconditioned_raw_sum_probability"], 1)

    def test_gaussian_and_members_assign_28_point_79_to_29_not_28(self):
        rows = [bucket(28, 28), bucket(29, 29)]
        gaussian = bucket_probabilities(28.79, 1.0, rows)
        members = distribution_for_prediction({"unit": "C", "ensemble_samples": [{"value": 28.79, "weight": 1}]}, rows)
        self.assertGreater(gaussian["items"][1]["probability"], gaussian["items"][0]["probability"])
        self.assertEqual(members["items"][1]["probability"], 1)
        self.assertEqual(members["items"][0]["probability"], 0)

    def test_observed_floor_uses_same_noaa_rule_as_probabilities(self):
        rows = [bucket(28, 28), bucket(29, 29), bucket(30, 999, "or_above")]
        result = bucket_probabilities(28.8, 1.0, rows, observed_floor=28.6)
        self.assertEqual(result["observed_floor_settlement_value"], 29)
        self.assertTrue(result["items"][0]["observed_floor_excluded"])
        self.assertFalse(result["items"][1]["observed_floor_excluded"])
        self.assertTrue(bucket_excluded_by_observed_floor(rows[0], prediction_unit="C", observed_floor=28.6))
        self.assertAlmostEqual(result["sum_probability"], 1)

    def test_noaa_order_does_not_promote_wu_to_exact_temperature(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE polymarket_markets (market_id, bucket_lower_c, bucket_upper_c, is_tail, bucket_label, raw_json)")
        conn.execute("INSERT INTO polymarket_markets VALUES (?, ?, ?, ?, ?, ?)", ("market", 28, 29, 0, "28C", json.dumps({"resolutionSource": SOURCE})))
        conn.execute("CREATE TABLE truth_wunderground_daily (icao, date_local, high_c, source_url)")
        conn.execute("INSERT INTO truth_wunderground_daily VALUES ('ZBAA', '2026-09-07', 28.8, 'https://www.wunderground.com')")
        with patch("weatherbot_v3.paper_settlement.connect", return_value=conn):
            result = truth_outcome_for_order({"city_key": "beijing", "target_date": "2026-09-07", "market_id": "market"})
        self.assertFalse(result["available"])
        self.assertFalse(result["exact"])
        self.assertEqual(result["reason"], "noaa_settlement_truth_unavailable")


if __name__ == "__main__":
    unittest.main()
