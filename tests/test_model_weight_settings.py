from __future__ import annotations

import unittest
from unittest.mock import patch

from weatherbot_v3.model_weight_settings import (
    manual_model_weights,
    model_weight_policy,
    update_model_weight_settings,
)


class ModelWeightSettingsTests(unittest.TestCase):
    def test_dynamic_is_default(self):
        with patch("weatherbot_v3.model_weight_settings.env_value", return_value="dynamic"):
            self.assertEqual(model_weight_policy(), "dynamic")

    def test_manual_weights_are_normalized_and_persisted(self):
        stored: dict[str, str] = {}

        def fake_setter(name: str, value: str) -> None:
            stored[name] = value

        def fake_reader(name: str, default: str = "") -> str:
            return stored.get(name, default)

        with patch("weatherbot_v3.model_weight_settings.set_env_value", side_effect=fake_setter) as setter:
            with patch("weatherbot_v3.model_weight_settings.env_value", side_effect=fake_reader):
                result = update_model_weight_settings(
                    "manual",
                    {"weathercom_v3": 60, "gfs": 40, "ecmwf": 0, "icon": 0, "gem": 0, "jma": 0},
                )
        self.assertEqual(result["mode"], "manual")
        self.assertAlmostEqual(result["weights"]["weathercom_v3"], 0.6)
        self.assertAlmostEqual(result["weights"]["gfs"], 0.4)
        self.assertTrue(setter.called)

    def test_default_manual_weights_sum_to_one(self):
        with patch("weatherbot_v3.model_weight_settings.env_value", return_value=""):
            self.assertAlmostEqual(sum(manual_model_weights().values()), 1.0, places=7)


if __name__ == "__main__":
    unittest.main()
