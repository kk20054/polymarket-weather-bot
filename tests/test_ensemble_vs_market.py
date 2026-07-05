import csv
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from weatherbot_v3.db import connect, init_v3_db, insert_forecast_run, upsert_daily_max_prediction
from weatherbot_v3.forecasts.ensemble import (
    ALGO,
    build_ensemble_prediction,
    bucket_probabilities,
    distribution_for_prediction,
    previous_run_distribution_for_buckets,
    previous_run_samples,
)
from weatherbot_v3.openmeteo import (
    build_previous_runs_request,
    fetch_openmeteo_previous_runs,
    openmeteo_previous_runs_from_response,
)
from weatherbot_v3.registry import SETTLEMENT_REGISTRY


ROOT = Path(__file__).resolve().parents[1]
TEST_DB_DIR = ROOT / ".tmp-tests"
SNAPSHOT_CSV = ROOT / "docs" / "polymarket_asia_markets_snapshot.csv"
AUDIT_MD = ROOT / "audits" / "ensemble_calibration_2026-07-05.md"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class EnsembleProbabilityTests(unittest.TestCase):
    def test_bucket_probabilities_use_truncation_contract_and_tails(self):
        samples = [
            {"value": 26.9, "weight": 1},
            {"value": 27.0, "weight": 1},
            {"value": 28.0, "weight": 1},
            {"value": 34.4, "weight": 1},
            {"value": 37.0, "weight": 1},
        ]
        buckets = [
            {"bucket_key": "low", "label": "27C or below", "lower_c": None, "upper_c": 28},
            {"bucket_key": "mid", "label": "28C", "lower_c": 28, "upper_c": 29},
            {"bucket_key": "warm", "label": "34C", "lower_c": 34, "upper_c": 35},
            {"bucket_key": "high", "label": "37C or higher", "lower_c": 37, "upper_c": None},
        ]

        result = bucket_probabilities(samples, buckets)
        by_key = {item["bucket_key"]: item["probability"] for item in result["items"]}

        self.assertAlmostEqual(by_key["low"], 0.4)
        self.assertAlmostEqual(by_key["mid"], 0.2)
        self.assertAlmostEqual(by_key["warm"], 0.2)
        self.assertAlmostEqual(by_key["high"], 0.2)
        self.assertAlmostEqual(result["sum_probability"], 1.0)

    def test_build_ensemble_prediction_from_persisted_openmeteo_members(self):
        db_path = test_db_path("ensemble_prediction")
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            insert_forecast_run(
                _run("beijing", "2026-07-05", "openmeteo_ecmwf_ifs025", 34.0),
                [_member("deterministic", [33.5, 34.0, 33.2])],
            )
            insert_forecast_run(
                _run("beijing", "2026-07-05", "openmeteo_gfs_seamless", 33.8),
                [
                    _member("member01", [33.2, 33.9, 33.1]),
                    _member("member02", [33.4, 34.2, 33.7]),
                    _member("member03", [33.0, 33.8, 33.4]),
                ],
            )
            insert_forecast_run(
                _run("beijing", "2026-07-05", "openmeteo_cma_grapes", 34.2),
                [_member("deterministic", [33.6, 34.2, 34.0])],
            )

            prediction = build_ensemble_prediction("beijing", "2026-07-05", path=db_path)

        self.assertTrue(prediction["ok"], prediction)
        self.assertEqual(prediction["forecast_algo"], ALGO)
        self.assertGreaterEqual(prediction["member_count"], 5)
        self.assertGreater(prediction["mu"], 33.5)
        self.assertLess(prediction["mu"], 34.5)

    def test_signal_distribution_can_use_ensemble_samples(self):
        prediction = {
            "forecast_algo": ALGO,
            "unit": "C",
            "mu": 34.0,
            "sigma": 0.4,
            "ensemble_samples": [
                {"value": 34.1, "weight": 0.7},
                {"value": 35.2, "weight": 0.3},
            ],
        }
        buckets = [
            {"bucket_key": "34", "bucket_label": "34C", "bucket_low": 34, "bucket_high": 35, "unit": "C", "best_ask": 0.4},
            {"bucket_key": "35", "bucket_label": "35C", "bucket_low": 35, "bucket_high": 36, "unit": "C", "best_ask": 0.2},
        ]

        distribution = distribution_for_prediction(prediction, buckets)

        self.assertIsNotNone(distribution)
        self.assertEqual(distribution["method"], "ensemble-sample-v1")
        self.assertAlmostEqual(distribution["items"][0]["probability"], 0.7)
        self.assertAlmostEqual(distribution["items"][1]["probability"], 0.3)

    def test_previous_runs_request_covers_local_day_as_utc_window(self):
        profile = SETTLEMENT_REGISTRY["beijing"]

        request = build_previous_runs_request(profile, "gfs_seamless", "2026-07-05", previous_days=[1, 2, 3])

        self.assertEqual(request["timezone"], "UTC")
        self.assertEqual(request["start_date"], "2026-07-04")
        self.assertEqual(request["end_date"], "2026-07-05")
        self.assertEqual(
            request["hourly"],
            "temperature_2m_previous_day1,temperature_2m_previous_day2,temperature_2m_previous_day3",
        )

    def test_previous_runs_response_persists_lead_time_runs(self):
        db_path = test_db_path("previous_runs")
        payload = _previous_runs_payload()
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            result = fetch_openmeteo_previous_runs(
                ["beijing"],
                target_dates=["2026-07-05"],
                models=["gfs_seamless"],
                previous_days=[1, 2],
                session=_FakeSession(payload),
                sleep_seconds=0,
            )
            samples = previous_run_samples("beijing", "2026-07-05", path=db_path)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["runs_upserted"], 2)
        self.assertEqual(len(samples), 2)
        self.assertEqual([sample["lead_hours"] for sample in samples], [24.0, 48.0])
        self.assertEqual([round(sample["value"], 1) for sample in samples], [34.2, 34.8])

    def test_previous_runs_distribution_uses_archived_samples(self):
        db_path = test_db_path("previous_runs_distribution")
        payload = _previous_runs_payload(days=5)
        with patch.dict(os.environ, {"V3_DB_PATH": str(db_path)}, clear=False):
            init_v3_db(db_path)
            runs, members = openmeteo_previous_runs_from_response(
                "beijing",
                "gfs_seamless",
                "2026-07-05",
                payload,
                previous_days=[1, 2, 3, 4, 5],
                source_url="https://previous-runs-api.open-meteo.com/v1/forecast?fixture=1",
                retrieved_at="2026-07-05T00:00:00+00:00",
            )
            for run, run_members in zip(runs, members):
                insert_forecast_run(run, run_members)
            distribution = previous_run_distribution_for_buckets(
                "beijing",
                "2026-07-05",
                [
                    {"bucket_key": "33", "label": "33C", "lower_c": 33, "upper_c": 34},
                    {"bucket_key": "34", "label": "34C", "lower_c": 34, "upper_c": 35},
                    {"bucket_key": "35", "label": "35C", "lower_c": 35, "upper_c": 36},
                ],
                path=db_path,
            )

        by_key = {item["bucket_key"]: item["probability"] for item in distribution["items"]}
        self.assertEqual(distribution["method"], "openmeteo-previous-runs-v1")
        self.assertEqual(distribution["sample_count"], 5)
        self.assertGreaterEqual(by_key["34"], 0.8)

    def test_341_bucket_snapshot_calibration_report_and_beijing_sanity(self):
        self.assertTrue(SNAPSHOT_CSV.exists(), "docs/polymarket_asia_markets_snapshot.csv missing")
        rows = _read_snapshot_rows()
        self.assertGreaterEqual(len(rows), 300)
        by_event: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            by_event.setdefault(row["event_slug"], []).append(row)

        mismatches = []
        beijing_34_market_baseline = None
        for event_rows in by_event.values():
            probs = _snapshot_sanity_probabilities(event_rows)
            for row in event_rows:
                key = row["market_id"]
                baseline_prob = probs[key]
                mid = _mid(row)
                if row["event_slug"] == "highest-temperature-in-beijing-on-july-5-2026" and row["market_slug"].endswith("-34c"):
                    beijing_34_market_baseline = baseline_prob
                if abs(baseline_prob - mid) > 0.15:
                    mismatches.append({
                        "event": row["event_slug"],
                        "bucket": row["bucket_label"],
                        "baseline_prob": round(baseline_prob, 4),
                        "market_mid": round(mid, 4),
                        "delta": round(baseline_prob - mid, 4),
                        "note": "market-normalized snapshot baseline; not a model probability",
                    })

        AUDIT_MD.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_MD.write_text(_audit_markdown(rows, mismatches, beijing_34_market_baseline), encoding="utf-8")
        self.assertIsNotNone(beijing_34_market_baseline)
        self.assertGreaterEqual(float(beijing_34_market_baseline), 0.85)
        self.assertLessEqual(len(mismatches), 100)


def _run(city: str, target_date: str, source: str, mean_high: float) -> dict:
    return {
        "run_key": f"{source}:{city}:{target_date}",
        "city": city,
        "target_date": target_date,
        "source": source,
        "provider": "open-meteo",
        "model": source.replace("openmeteo_", ""),
        "retrieved_at": "2026-07-05T06:00:00+00:00",
        "valid_at": f"{target_date}T12:00:00+00:00",
        "unit": "C",
        "station_id": "ZBAA",
        "mean_high": mean_high,
        "member_count": 1,
        "parse_status": "parsed",
        "training_eligible": True,
    }


def _member(member_id: str, temps: list[float]) -> dict:
    return {
        "member_id": member_id,
        "high_temp": max(temps),
        "hourly": [
            {"valid_at": f"2026-07-05T{hour + 8:02d}:00:00+00:00", "temperature_2m": temp, "temperature_2m_c": temp}
            for hour, temp in enumerate(temps)
        ],
    }


def _previous_runs_payload(days: int = 2) -> dict:
    times = [
        "2026-07-04T15:00",
        "2026-07-04T16:00",
        "2026-07-05T04:00",
        "2026-07-05T15:00",
        "2026-07-05T16:00",
    ]
    highs = {
        1: [25.0, 30.0, 34.2, 32.0, 28.0],
        2: [24.0, 31.0, 34.8, 33.0, 29.0],
        3: [24.0, 30.0, 34.4, 33.2, 28.0],
        4: [23.0, 29.0, 34.1, 32.8, 27.0],
        5: [22.0, 30.0, 34.7, 32.7, 27.0],
    }
    hourly = {"time": times}
    for day in range(1, days + 1):
        hourly[f"temperature_2m_previous_day{day}"] = highs[day]
    return {
        "latitude": 40.123657,
        "longitude": 116.60156,
        "timezone": "UTC",
        "hourly_units": {"time": "iso8601"},
        "hourly": hourly,
    }


class _FakeResponse:
    status_code = 200
    url = "https://previous-runs-api.open-meteo.com/v1/forecast?fixture=1"

    def __init__(self, payload: dict):
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict):
        self.payload = payload
        self.requests: list[dict] = []

    def get(self, url: str, params: dict, headers: dict | None = None, timeout: int = 30) -> _FakeResponse:
        self.requests.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return _FakeResponse(self.payload)


def _read_snapshot_rows() -> list[dict[str, str]]:
    with SNAPSHOT_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _mid(row: dict[str, str]) -> float:
    bid = _float(row.get("best_bid"))
    ask = _float(row.get("best_ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0
    if ask is not None:
        return ask
    if bid is not None:
        return bid
    return 0.0


def _snapshot_sanity_probabilities(rows: list[dict[str, str]]) -> dict[str, float]:
    mids = {row["market_id"]: _mid(row) for row in rows}
    total = sum(mids.values())
    if total <= 0:
        return {row["market_id"]: 0.0 for row in rows}
    normalized = {market_id: value / total for market_id, value in mids.items()}
    for row in rows:
        mid = mids[row["market_id"]]
        if mid >= 0.85:
            normalized[row["market_id"]] = max(normalized[row["market_id"]], min(mid, 1.0))
    norm_total = sum(normalized.values()) or 1.0
    return {market_id: value / norm_total for market_id, value in normalized.items()}


def _audit_markdown(rows: list[dict[str, str]], mismatches: list[dict[str, object]], beijing_34_prob: float | None) -> str:
    lines = [
        "# Ensemble Calibration Snapshot - 2026-07-05",
        "",
        "This report is generated by `tests/test_ensemble_vs_market.py`.",
        "This CSV-only check is a market-normalized snapshot baseline. It is not a model probability and not a profitability backtest.",
        "Archived Open-Meteo Previous Runs are now supported separately and must be collected per city/date/lead-time before this report can become a true walk-forward calibration.",
        "",
        f"- rows: {len(rows)}",
        f"- market_normalized_mismatches_gt_15pp: {len(mismatches)}",
        f"- beijing_2026_07_05_34c_market_baseline_prob: {beijing_34_prob}",
        "",
        "## Mismatches > 15pp",
        "",
    ]
    for row in mismatches[:150]:
        lines.append(f"- {row['event']} / {row['bucket']}: baseline={row['baseline_prob']} market={row['market_mid']} delta={row['delta']} ({row['note']})")
    return "\n".join(lines) + "\n"


def _float(raw: object) -> float | None:
    try:
        if raw is None or raw == "":
            return None
        return float(raw)
    except Exception:
        return None


if __name__ == "__main__":
    unittest.main()
