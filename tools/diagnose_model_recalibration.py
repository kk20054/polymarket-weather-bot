from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean, median, stdev
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import compare_model_market_accuracy as base
from weatherbot_v3.deb import bucket_probabilities, normal_cdf


DEFAULT_DB = ROOT / "data" / "weatherbot_v3.db"
DEFAULT_OUTPUT = (
    ROOT
    / "audits"
    / f"model-error-recalibration-{date.today().isoformat()}"
    / "README.md"
)
RELAXED_BOOK_AGE_SECONDS = 6.0 * 60.0 * 60.0
MARKET_BENCHMARK_BRIER = 0.6869
DECOMPOSITION_BINS = 10
SCALE_EPSILON = 0.10


@dataclass(frozen=True)
class PredictionRecord:
    prediction_id: int
    city_key: str
    target_date: str
    issued_at: datetime
    mu_c: float
    model_mu_c: float
    sigma_c: float
    unit: str


@dataclass(frozen=True)
class Observation:
    observed_at: datetime
    temperature_c: float
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only diagnosis of WeatherBot error structure and leakage-free "
            "walk-forward recalibration."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, probability)) * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def celsius_value(value: Any, unit: str) -> float | None:
    number = as_float(value)
    if number is None:
        return None
    if str(unit or "C").upper() == "F":
        return (number - 32.0) * 5.0 / 9.0
    return number


def celsius_sigma(value: Any, unit: str) -> float | None:
    number = as_float(value)
    if number is None or number <= 0:
        return None
    if str(unit or "C").upper() == "F":
        return number * 5.0 / 9.0
    return number


def load_predictions(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str], list[PredictionRecord]]:
    result: dict[tuple[str, str], list[PredictionRecord]] = defaultdict(list)
    query = """
        SELECT id,city_key,target_date,issued_at,mu,sigma,unit,raw_json
        FROM daily_max_predictions
        WHERE mu IS NOT NULL
          AND sigma IS NOT NULL
          AND (validity_status IS NULL OR validity_status != 'invalid')
        ORDER BY city_key,target_date,issued_at,id
    """
    for row in conn.execute(query):
        payload = dict(row)
        unit = str(payload.get("unit") or "C").upper()
        mu_c = celsius_value(payload.get("mu"), unit)
        sigma_c = celsius_sigma(payload.get("sigma"), unit)
        if mu_c is None or sigma_c is None:
            continue
        raw = base.safe_json(payload.get("raw_json"))
        model_mu_c = celsius_value(raw.get("model_mu"), unit)
        if model_mu_c is None:
            model_mu_c = mu_c
        result[(str(payload["city_key"]), str(payload["target_date"]))].append(
            PredictionRecord(
                prediction_id=int(payload["id"]),
                city_key=str(payload["city_key"]),
                target_date=str(payload["target_date"]),
                issued_at=base.parse_timestamp(str(payload["issued_at"])),
                mu_c=mu_c,
                model_mu_c=model_mu_c,
                sigma_c=sigma_c,
                unit=unit,
            )
        )
    return result


def prediction_for_snapshot(
    snapshot: base.Snapshot,
    prediction_rows: dict[tuple[str, str], list[PredictionRecord]],
) -> PredictionRecord | None:
    rows = prediction_rows.get((snapshot.city_key, snapshot.target_date), [])
    if not rows:
        return None
    timestamps = [row.issued_at for row in rows]
    index = bisect.bisect_right(timestamps, snapshot.issued_at)
    if index <= 0:
        return None
    return rows[index - 1]


def make_relaxed_snapshot(
    key: tuple[str, str, str, str],
    rows: list[dict[str, Any]],
    station: dict[str, Any],
    expected_events: list[tuple[str, set[str]]],
    bucket_lookup: dict[str, dict[str, Any]],
) -> tuple[base.Snapshot | None, str]:
    relaxed_rows: list[dict[str, Any]] = []
    for row in rows:
        copied = dict(row)
        age = as_float(copied.get("book_age_seconds"))
        if age is None:
            copied["book_age_seconds"] = 0.0
        relaxed_rows.append(copied)
    previous_limit = base.MAX_BOOK_AGE_SECONDS
    base.MAX_BOOK_AGE_SECONDS = RELAXED_BOOK_AGE_SECONDS
    try:
        return base.make_snapshot(
            key,
            relaxed_rows,
            station,
            expected_events,
            bucket_lookup,
        )
    finally:
        base.MAX_BOOK_AGE_SECONDS = previous_limit


def valid_snapshots(
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]],
    truths: dict[tuple[str, str], base.Truth],
    stations: dict[str, dict[str, Any]],
    bucket_events: dict[tuple[str, str], list[tuple[str, set[str]]]],
    bucket_lookup: dict[str, dict[str, Any]],
    *,
    relaxed: bool,
) -> tuple[list[base.Snapshot], Counter[str]]:
    snapshots: list[base.Snapshot] = []
    exclusions: Counter[str] = Counter()
    builder = make_relaxed_snapshot if relaxed else base.make_snapshot
    for key, rows in groups.items():
        city_key, target_date, _, _ = key
        if (city_key, target_date) not in truths:
            exclusions["no_authoritative_truth"] += 1
            continue
        station = stations.get(city_key)
        if not station:
            exclusions["station_missing"] += 1
            continue
        expected_events = bucket_events.get((city_key, target_date), [])
        if not expected_events:
            exclusions["no_strict_matched_event"] += 1
            continue
        snapshot, reason = builder(
            key,
            rows,
            station,
            expected_events,
            bucket_lookup,
        )
        if snapshot is None:
            exclusions[reason] += 1
            continue
        snapshots.append(snapshot)
    return snapshots, exclusions


def gaussian_bucket_probs(
    mu_c: float,
    sigma_c: float,
    buckets: list[dict[str, Any]],
) -> list[float]:
    distribution = bucket_probabilities(
        mu_c,
        sigma_c,
        buckets,
        unit="C",
        sigma_floor=1e-9,
        observed_floor=None,
        normalize=True,
    )
    items = list(distribution.get("items") or [])
    if len(items) != len(buckets):
        raise ValueError("gaussian_bucket_count_mismatch")
    probabilities = [float(item.get("probability") or 0.0) for item in items]
    total = sum(probabilities)
    if total <= 0:
        raise ValueError("gaussian_probability_mass_zero")
    return [value / total for value in probabilities]


def materialize_samples(
    snapshots: list[base.Snapshot],
    stations: dict[str, dict[str, Any]],
    truths: dict[tuple[str, str], base.Truth],
    maturity: dict[tuple[str, str], int],
    prediction_rows: dict[tuple[str, str], list[PredictionRecord]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    selected, checkpoint_rejections = base.select_checkpoint_samples(snapshots, stations)
    exclusions: Counter[str] = Counter(checkpoint_rejections)
    samples: list[dict[str, Any]] = []
    for lead, snapshot in selected:
        truth = truths[(snapshot.city_key, snapshot.target_date)]
        actual_matches = [
            index
            for index, bucket in enumerate(snapshot.buckets)
            if base.bucket_contains(truth.actual_c, bucket)
        ]
        if len(actual_matches) != 1:
            exclusions["truth_does_not_map_to_exactly_one_bucket"] += 1
            continue
        prediction = prediction_for_snapshot(snapshot, prediction_rows)
        if prediction is None:
            exclusions["daily_max_prediction_not_found_before_decision"] += 1
            continue
        try:
            gaussian_probs = gaussian_bucket_probs(
                prediction.mu_c,
                prediction.sigma_c,
                snapshot.buckets,
            )
        except ValueError as exc:
            exclusions[str(exc)] += 1
            continue
        maturity_count = maturity.get((snapshot.city_key, snapshot.target_date), 0)
        samples.append(
            {
                "city_key": snapshot.city_key,
                "target_date": snapshot.target_date,
                "lead": lead,
                "issued_at": snapshot.issued_at,
                "local_issued_at": snapshot.local_issued_at,
                "truth_provider": truth.provider,
                "actual_c": float(truth.actual_c),
                "actual_index": actual_matches[0],
                "buckets": snapshot.buckets,
                "model_probs": snapshot.model_probs,
                "market_probs": snapshot.market_probs,
                "gaussian_probs": gaussian_probs,
                "prediction_id": prediction.prediction_id,
                "prediction_issued_at": prediction.issued_at,
                "prediction_age_seconds": (
                    snapshot.issued_at - prediction.issued_at
                ).total_seconds(),
                "mu_c": prediction.mu_c,
                "model_mu_c": prediction.model_mu_c,
                "sigma_c": prediction.sigma_c,
                "maturity_count": maturity_count,
                "maturity_band": base.maturity_band(maturity_count),
            }
        )
    return samples, exclusions


def build_cohort(
    conn: sqlite3.Connection,
    *,
    relaxed: bool,
    shared: dict[str, Any],
) -> tuple[list[dict[str, Any]], Counter[str], int]:
    snapshots, exclusions = valid_snapshots(
        shared["groups"],
        shared["truths"],
        shared["stations"],
        shared["bucket_events"],
        shared["bucket_lookup"],
        relaxed=relaxed,
    )
    samples, sample_exclusions = materialize_samples(
        snapshots,
        shared["stations"],
        shared["truths"],
        shared["maturity"],
        shared["predictions"],
    )
    exclusions.update(sample_exclusions)
    return samples, exclusions, len(snapshots)


def observation_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        return base.parse_timestamp(text)
    except (TypeError, ValueError, OverflowError):
        return None


def metar_temperature_c(
    row: sqlite3.Row,
    stations: dict[str, dict[str, Any]],
) -> float | None:
    temperature = as_float(row["temperature"])
    if temperature is None:
        return None
    parser_version = str(row["parser_version"] or "")
    if parser_version.startswith("iem-asos"):
        return temperature
    station = stations.get(str(row["city"] or ""), {})
    unit = str(station.get("settlement_unit") or station.get("unit") or "C").upper()
    return celsius_value(temperature, unit)


def load_observations(
    conn: sqlite3.Connection,
    stations: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], list[Observation]]:
    result: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT city,report_time,temperature,parser_version,parse_status
        FROM metar_reports
        WHERE temperature IS NOT NULL
          AND (parse_status IS NULL OR parse_status != 'failed')
        """
    ):
        city_key = str(row["city"] or "")
        station = stations.get(city_key)
        if not station:
            continue
        observed_at = observation_timestamp(row["report_time"])
        temperature_c = metar_temperature_c(row, stations)
        if observed_at is None or temperature_c is None:
            continue
        try:
            timezone_name = str(
                station.get("settlement_timezone")
                or station.get("timezone")
                or "UTC"
            )
            local_date = observed_at.astimezone(base.ZoneInfo(timezone_name)).date().isoformat()
        except Exception:
            continue
        result[(city_key, local_date)].append(
            Observation(observed_at, temperature_c, "METAR")
        )

    for row in conn.execute(
        """
        SELECT city,network,observed_at,temperature,raw_unit,parse_status
        FROM mesonet_observations
        WHERE temperature IS NOT NULL
          AND network IN ('china_live','wunderground_pws')
          AND (parse_status IS NULL OR parse_status != 'failed')
        """
    ):
        city_key = str(row["city"] or "")
        station = stations.get(city_key)
        if not station:
            continue
        observed_at = observation_timestamp(row["observed_at"])
        temperature_c = celsius_value(row["temperature"], str(row["raw_unit"] or "C"))
        if observed_at is None or temperature_c is None:
            continue
        try:
            timezone_name = str(
                station.get("settlement_timezone")
                or station.get("timezone")
                or "UTC"
            )
            local_date = observed_at.astimezone(base.ZoneInfo(timezone_name)).date().isoformat()
        except Exception:
            continue
        source = (
            "China Live"
            if str(row["network"]) == "china_live"
            else "Wunderground PWS"
        )
        result[(city_key, local_date)].append(
            Observation(observed_at, temperature_c, source)
        )

    for rows in result.values():
        rows.sort(key=lambda item: item.observed_at)
    return result


def apply_deployable_floor(
    samples: list[dict[str, Any]],
    observations: dict[tuple[str, str], list[Observation]],
) -> Counter[str]:
    source_counts: Counter[str] = Counter()
    for sample in samples:
        rows = observations.get((sample["city_key"], sample["target_date"]), [])
        timestamps = [row.observed_at for row in rows]
        stop = bisect.bisect_left(timestamps, sample["issued_at"])
        eligible = rows[:stop]
        if not eligible:
            sample["observed_floor_c"] = None
            sample["observed_floor_source"] = None
            sample["cprime_probs"] = list(sample["model_probs"])
            source_counts["no_observation_before_decision"] += 1
            continue
        maximum = max(eligible, key=lambda item: (item.temperature_c, item.observed_at))
        sample["observed_floor_c"] = maximum.temperature_c
        sample["observed_floor_source"] = maximum.source
        sample["cprime_probs"] = base.counterfactual_probs(
            maximum.temperature_c,
            sample["buckets"],
            sample["model_probs"],
        )
        source_counts[maximum.source] += 1
    return source_counts


def descriptive_stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "std": None,
            "p10": None,
            "p90": None,
            "negative_share": None,
        }
    return {
        "n": len(values),
        "mean": fmean(values),
        "median": median(values),
        "std": stdev(values) if len(values) > 1 else 0.0,
        "p10": percentile(values, 0.10),
        "p90": percentile(values, 0.90),
        "negative_share": sum(value < 0 for value in values) / len(values),
    }


def signed_error_groups(
    samples: list[dict[str, Any]],
    group_key: str,
    *,
    minimum_n: int = 0,
) -> list[tuple[str, dict[str, float | int | None]]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        grouped[str(sample[group_key])].append(
            float(sample["model_mu_c"]) - float(sample["actual_c"])
        )
    return [
        (group, descriptive_stats(values))
        for group, values in sorted(grouped.items())
        if len(values) >= minimum_n
    ]


def sigma_diagnostics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    signed_errors = [
        float(sample["model_mu_c"]) - float(sample["actual_c"])
        for sample in samples
    ]
    mean_error = fmean(signed_errors) if signed_errors else 0.0
    raw_z = [
        (float(sample["actual_c"]) - float(sample["model_mu_c"]))
        / float(sample["sigma_c"])
        for sample in samples
    ]
    centered_z = [
        (
            float(sample["actual_c"])
            - (float(sample["model_mu_c"]) - mean_error)
        )
        / float(sample["sigma_c"])
        for sample in samples
    ]
    pits = [
        normal_cdf(
            float(sample["actual_c"]),
            float(sample["model_mu_c"]),
            float(sample["sigma_c"]),
            1e-9,
        )
        for sample in samples
    ]
    bins = [0] * 10
    for pit in pits:
        index = min(9, max(0, int(pit * 10.0)))
        bins[index] += 1
    return {
        "n": len(samples),
        "abs_z_mean": fmean(abs(value) for value in raw_z) if raw_z else None,
        "abs_z_median": median(abs(value) for value in raw_z) if raw_z else None,
        "abs_z_p90": percentile([abs(value) for value in raw_z], 0.90),
        "within_1sigma": (
            sum(abs(value) <= 1.0 for value in raw_z) / len(raw_z)
            if raw_z
            else None
        ),
        "within_2sigma": (
            sum(abs(value) <= 2.0 for value in raw_z) / len(raw_z)
            if raw_z
            else None
        ),
        "centered_within_1sigma": (
            sum(abs(value) <= 1.0 for value in centered_z) / len(centered_z)
            if centered_z
            else None
        ),
        "centered_within_2sigma": (
            sum(abs(value) <= 2.0 for value in centered_z) / len(centered_z)
            if centered_z
            else None
        ),
        "pit_mean": fmean(pits) if pits else None,
        "pit_median": median(pits) if pits else None,
        "pit_bins": bins,
    }


def brier_decomposition(
    samples: list[dict[str, Any]],
    probability_key: str,
) -> dict[str, float | int]:
    if not samples:
        return {
            "n": 0,
            "brier": math.nan,
            "reliability": math.nan,
            "resolution": math.nan,
            "uncertainty": math.nan,
            "binning_residual": math.nan,
        }
    bins: dict[int, list[tuple[float, float]]] = defaultdict(list)
    pair_count = 0
    for sample in samples:
        actual_index = int(sample["actual_index"])
        for index, probability in enumerate(sample[probability_key]):
            value = float(probability)
            bin_index = min(DECOMPOSITION_BINS - 1, max(0, int(value * DECOMPOSITION_BINS)))
            bins[bin_index].append((value, 1.0 if index == actual_index else 0.0))
            pair_count += 1
    n_events = len(samples)
    climatology = n_events / pair_count
    reliability = 0.0
    resolution = 0.0
    for rows in bins.values():
        forecast_mean = fmean(row[0] for row in rows)
        outcome_mean = fmean(row[1] for row in rows)
        event_weight = len(rows) / n_events
        reliability += event_weight * (forecast_mean - outcome_mean) ** 2
        resolution += event_weight * (outcome_mean - climatology) ** 2
    uncertainty = (pair_count / n_events) * climatology * (1.0 - climatology)
    brier = float(base.summarize(samples, probability_key)["brier"])
    reconstructed = reliability - resolution + uncertainty
    return {
        "n": n_events,
        "pair_count": pair_count,
        "brier": brier,
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
        "binning_residual": brier - reconstructed,
    }


def canonical_calibration_records(
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_city_date: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        by_city_date[(sample["city_key"], sample["target_date"])].append(sample)
    lead_rank = {"D+1": 0, "D+2": 1, "D+0": 2}
    records: list[dict[str, Any]] = []
    for rows in by_city_date.values():
        rows.sort(key=lambda row: (lead_rank.get(str(row["lead"]), 9), row["issued_at"]))
        records.append(rows[0])
    records.sort(key=lambda row: (row["target_date"], row["city_key"]))
    return records


def fitted_scale(residual_rows: list[tuple[float, float]]) -> float:
    if not residual_rows:
        return 1.0
    ratio_square_mean = fmean(
        (residual / max(sigma, 1e-9)) ** 2
        for residual, sigma in residual_rows
    )
    return max(math.sqrt(max(ratio_square_mean, 0.0)), SCALE_EPSILON)


def apply_walk_forward_recalibration(samples: list[dict[str, Any]]) -> dict[str, Any]:
    training_records = canonical_calibration_records(samples)
    training_sizes: list[int] = []
    city_training_sizes: list[int] = []
    scales_v3: list[float] = []
    scales_v4: list[float] = []
    for sample in sorted(samples, key=lambda row: row["issued_at"]):
        local_issued_date = sample["local_issued_at"].date().isoformat()
        prior = [
            row
            for row in training_records
            if row["target_date"] < local_issued_date
            and row["target_date"] < sample["target_date"]
        ]
        prior_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in prior:
            prior_by_city[str(row["city_key"])].append(row)
        global_errors = [
            float(row["mu_c"]) - float(row["actual_c"])
            for row in prior
        ]
        city_rows = prior_by_city.get(str(sample["city_key"]), [])
        city_errors = [
            float(row["mu_c"]) - float(row["actual_c"])
            for row in city_rows
        ]
        global_bias = fmean(global_errors) if global_errors else 0.0
        city_bias = fmean(city_errors) if city_errors else 0.0
        scale_v3 = fitted_scale(
            [
                (
                    float(row["actual_c"]) - float(row["mu_c"]),
                    float(row["sigma_c"]),
                )
                for row in prior
            ]
        )
        city_biases = {
            city_key: fmean(
                float(row["mu_c"]) - float(row["actual_c"])
                for row in rows
            )
            for city_key, rows in prior_by_city.items()
        }
        scale_v4 = fitted_scale(
            [
                (
                    float(row["actual_c"])
                    - (
                        float(row["mu_c"])
                        - city_biases.get(str(row["city_key"]), 0.0)
                    ),
                    float(row["sigma_c"]),
                )
                for row in prior
            ]
        )
        sample["v1_probs"] = gaussian_bucket_probs(
            float(sample["mu_c"]) - global_bias,
            float(sample["sigma_c"]),
            sample["buckets"],
        )
        sample["v2_probs"] = gaussian_bucket_probs(
            float(sample["mu_c"]) - city_bias,
            float(sample["sigma_c"]),
            sample["buckets"],
        )
        sample["v3_probs"] = gaussian_bucket_probs(
            float(sample["mu_c"]),
            float(sample["sigma_c"]) * scale_v3,
            sample["buckets"],
        )
        sample["v4_probs"] = gaussian_bucket_probs(
            float(sample["mu_c"]) - city_bias,
            float(sample["sigma_c"]) * scale_v4,
            sample["buckets"],
        )
        sample["wf_global_bias_c"] = global_bias
        sample["wf_city_bias_c"] = city_bias
        sample["wf_scale_v3"] = scale_v3
        sample["wf_scale_v4"] = scale_v4
        sample["wf_train_n"] = len(prior)
        sample["wf_city_train_n"] = len(city_rows)
        training_sizes.append(len(prior))
        city_training_sizes.append(len(city_rows))
        scales_v3.append(scale_v3)
        scales_v4.append(scale_v4)
    return {
        "training_record_count": len(training_records),
        "train_n_median": median(training_sizes) if training_sizes else 0,
        "city_train_n_median": median(city_training_sizes) if city_training_sizes else 0,
        "v2_corrected_n": sum(value > 0 for value in city_training_sizes),
        "scale_v3_median": median(scales_v3) if scales_v3 else 1.0,
        "scale_v4_median": median(scales_v4) if scales_v4 else 1.0,
    }


def brier_gap(samples: list[dict[str, Any]]) -> tuple[int, float | None, float | None, float | None]:
    if not samples:
        return 0, None, None, None
    model = float(base.summarize(samples, "model_probs")["brier"])
    market = float(base.summarize(samples, "market_probs")["brier"])
    return len(samples), model, market, model - market


def maturity_composition(samples: list[dict[str, Any]]) -> dict[str, Any]:
    bands = ("<10", "10-19", ">=20")
    full: dict[str, Any] = {}
    city_bands: dict[str, set[str]] = defaultdict(set)
    for sample in samples:
        city_bands[str(sample["city_key"])].add(str(sample["maturity_band"]))
    paired_cities = sorted(
        city
        for city, values in city_bands.items()
        if "<10" in values and "10-19" in values
    )
    for band in bands:
        subset = [row for row in samples if row["maturity_band"] == band]
        full[band] = {
            "gap": brier_gap(subset),
            "cities": Counter(str(row["city_key"]) for row in subset),
            "leads": Counter(str(row["lead"]) for row in subset),
        }
    paired: dict[str, Any] = {}
    for band in ("<10", "10-19"):
        subset = [
            row
            for row in samples
            if row["maturity_band"] == band and row["city_key"] in paired_cities
        ]
        paired[band] = {
            "gap": brier_gap(subset),
            "cities": Counter(str(row["city_key"]) for row in subset),
            "leads": Counter(str(row["lead"]) for row in subset),
        }
    per_city_changes: list[tuple[str, float, float, float]] = []
    for city in paired_cities:
        early = [
            row
            for row in samples
            if row["city_key"] == city and row["maturity_band"] == "<10"
        ]
        mature = [
            row
            for row in samples
            if row["city_key"] == city and row["maturity_band"] == "10-19"
        ]
        early_gap = brier_gap(early)[3]
        mature_gap = brier_gap(mature)[3]
        if early_gap is None or mature_gap is None:
            continue
        per_city_changes.append((city, early_gap, mature_gap, mature_gap - early_gap))
    return {
        "full": full,
        "paired_cities": paired_cities,
        "paired": paired,
        "per_city_changes": per_city_changes,
    }


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "--"
    return f"{number:.{digits}f}"


def pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return "--"
    return f"{float(value) * 100.0:.{digits}f}%"


def counter_text(counter: Counter[str]) -> str:
    if not counter:
        return "--"
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items()))


def signed_stats_table(
    rows: list[tuple[str, dict[str, float | int | None]]],
) -> list[str]:
    lines = [
        "| 分组 | N | mean °C | median °C | std °C | P10 °C | P90 °C | 偏冷占比 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, values in rows:
        lines.append(
            f"| {name} | {values['n']} | {fmt(values['mean'], 3)} | "
            f"{fmt(values['median'], 3)} | {fmt(values['std'], 3)} | "
            f"{fmt(values['p10'], 3)} | {fmt(values['p90'], 3)} | "
            f"{pct(values['negative_share'])} |"
        )
    return lines


def sensitivity_table(
    strict_samples: list[dict[str, Any]],
    relaxed_samples: list[dict[str, Any]],
) -> list[str]:
    lines = [
        "| 口径 | N | 城市数 | A 模型 Brier | B 市场 Brier | A-B | C′ Brier |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, rows in (("严格：age≤10m", strict_samples), ("放宽：age≤6h/缺失可纳入", relaxed_samples)):
        model = base.summarize(rows, "model_probs")
        market = base.summarize(rows, "market_probs")
        cprime = base.summarize(rows, "cprime_probs")
        gap = (
            float(model["brier"]) - float(market["brier"])
            if model["brier"] is not None and market["brier"] is not None
            else None
        )
        lines.append(
            f"| {name} | {len(rows)} | "
            f"{len({row['city_key'] for row in rows})} | "
            f"{fmt(model['brier'])} | {fmt(market['brier'])} | "
            f"{fmt(gap)} | {fmt(cprime['brier'])} |"
        )
    return lines


def recalibration_table(samples: list[dict[str, Any]]) -> list[str]:
    variants = [
        ("A 持久化模型", "model_probs"),
        ("B 市场", "market_probs"),
        ("V1 全局 bias", "v1_probs"),
        ("V2 城市 bias", "v2_probs"),
        ("V3 全局 sigma scale", "v3_probs"),
        ("V4 城市 bias + 全局 sigma", "v4_probs"),
    ]
    lines = [
        "| 变体 | N | Brier | Log loss | Top-1 | Top-2 | 与市场 0.6869 的差 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, key in variants:
        values = base.summarize(samples, key)
        difference = (
            float(values["brier"]) - MARKET_BENCHMARK_BRIER
            if values["brier"] is not None
            else None
        )
        lines.append(
            f"| {name} | {values['n']} | {fmt(values['brier'])} | "
            f"{fmt(values['log_loss'])} | {pct(values['top1'])} | "
            f"{pct(values['top2'])} | {fmt(difference)} |"
        )
    return lines


def lead_floor_table(samples: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 分组 | N | A 模型 Brier | C′ 决策时实况截断 Brier | 改善 | 有可用 floor |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    groups = [("总体", samples)]
    groups.extend(
        (lead, [row for row in samples if row["lead"] == lead])
        for lead in ("D+0", "D+1", "D+2")
    )
    for name, rows in groups:
        if not rows:
            continue
        model = base.summarize(rows, "model_probs")
        cprime = base.summarize(rows, "cprime_probs")
        improvement = float(model["brier"]) - float(cprime["brier"])
        with_floor = sum(row.get("observed_floor_c") is not None for row in rows)
        lines.append(
            f"| {name} | {len(rows)} | {fmt(model['brier'])} | "
            f"{fmt(cprime['brier'])} | {fmt(improvement)} | "
            f"{with_floor}/{len(rows)} |"
        )
    return lines


def maturity_rows(composition: dict[str, Any]) -> list[str]:
    lines = [
        "| 范围 | 成熟度 | N | 模型 Brier | 市场 Brier | 差值 A-B | lead 构成 | 城市构成 |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for scope, values in (("全样本", composition["full"]), ("共同城市", composition["paired"])):
        for band, payload in values.items():
            n, model, market, gap = payload["gap"]
            lines.append(
                f"| {scope} | {band} | {n} | {fmt(model)} | {fmt(market)} | "
                f"{fmt(gap)} | {counter_text(payload['leads'])} | "
                f"{counter_text(payload['cities'])} |"
            )
    return lines


def build_report(
    *,
    db_path: Path,
    strict_samples: list[dict[str, Any]],
    relaxed_samples: list[dict[str, Any]],
    strict_exclusions: Counter[str],
    relaxed_exclusions: Counter[str],
    strict_valid_snapshots: int,
    relaxed_valid_snapshots: int,
    observation_sources: Counter[str],
    walk_forward: dict[str, Any],
) -> str:
    signed_overall = descriptive_stats(
        [
            float(row["model_mu_c"]) - float(row["actual_c"])
            for row in relaxed_samples
        ]
    )
    signed_lead = signed_error_groups(relaxed_samples, "lead")
    signed_city = signed_error_groups(relaxed_samples, "city_key", minimum_n=5)
    signed_maturity = signed_error_groups(relaxed_samples, "maturity_band")
    sigma = sigma_diagnostics(relaxed_samples)
    model_decomp = brier_decomposition(relaxed_samples, "model_probs")
    market_decomp = brier_decomposition(relaxed_samples, "market_probs")
    composition = maturity_composition(relaxed_samples)

    variant_keys = {
        "V1 全局 bias": "v1_probs",
        "V2 城市 bias": "v2_probs",
        "V3 全局 sigma scale": "v3_probs",
        "V4 城市 bias + 全局 sigma": "v4_probs",
    }
    variant_briers = {
        name: float(base.summarize(relaxed_samples, key)["brier"])
        for name, key in variant_keys.items()
    }
    best_variant, best_brier = min(variant_briers.items(), key=lambda item: item[1])

    raw_coverage_1 = float(sigma["within_1sigma"])
    raw_coverage_2 = float(sigma["within_2sigma"])
    centered_coverage_1 = float(sigma["centered_within_1sigma"])
    centered_coverage_2 = float(sigma["centered_within_2sigma"])
    if centered_coverage_1 < 0.633 or centered_coverage_2 < 0.904:
        sigma_conclusion = "去除全局冷偏后覆盖率仍明显低于理论值，sigma 偏小。"
    elif centered_coverage_1 > 0.733 and centered_coverage_2 > 0.984:
        sigma_conclusion = "去除全局冷偏后覆盖率明显高于理论值，sigma 偏大。"
    else:
        sigma_conclusion = "去除全局冷偏后覆盖率接近理论值，sigma 尺度基本合适。"
    pit_skew = (
        "明显右偏（高 PIT 聚集，实际温度常高于模型中心）"
        if float(sigma["pit_mean"]) > 0.58
        else "明显左偏"
        if float(sigma["pit_mean"]) < 0.42
        else "未见强单侧偏斜"
    )

    reliability_gap = float(model_decomp["reliability"]) - float(market_decomp["reliability"])
    resolution_gap = float(model_decomp["resolution"]) - float(market_decomp["resolution"])
    if resolution_gap < 0:
        decomposition_conclusion = (
            "模型既有更差的校准（reliability 更高），分辨力也低于市场"
            "（resolution 更低）；这不是纯校准问题，后处理无法凭空补回市场信息。"
        )
    else:
        decomposition_conclusion = (
            "模型分辨力不低于市场，但 reliability 更差；主要是可后处理修复的校准问题。"
        )

    paired_early_gap = composition["paired"].get("<10", {}).get("gap", (0, None, None, None))[3]
    paired_mature_gap = composition["paired"].get("10-19", {}).get("gap", (0, None, None, None))[3]
    city_changes = composition["per_city_changes"]
    improved_cities = sum(change[3] < 0 for change in city_changes)
    if (
        paired_early_gap is not None
        and paired_mature_gap is not None
        and paired_mature_gap < paired_early_gap
        and improved_cities > len(city_changes) / 2
    ):
        maturity_conclusion = (
            "共同城市内差距仍收窄，且多数城市方向一致；收窄主要是实际校准改善，"
            "并非仅由城市构成造成。"
        )
    else:
        maturity_conclusion = (
            "控制共同城市后差距未稳定收窄；此前改善主要是城市/lead 构成差异，"
            "不能归因于成熟度。"
        )

    cprime_overall = base.summarize(relaxed_samples, "cprime_probs")
    cprime_d0 = base.summarize(
        [row for row in relaxed_samples if row["lead"] == "D+0"],
        "cprime_probs",
    )
    cprime_d1 = base.summarize(
        [row for row in relaxed_samples if row["lead"] == "D+1"],
        "cprime_probs",
    )

    lines = [
        "# WeatherBot 模型误差结构与无泄漏再校准诊断",
        "",
        f"- 生成时间：`{datetime.now(timezone.utc).isoformat()}`",
        f"- 数据库：`{db_path}`，以 SQLite `mode=ro` + `query_only=ON` 打开。",
        "- 本轮未写库、未改 schema、未改生产代码；只新增本脚本与本报告。",
        "",
        "## 样本与敏感性",
        "",
        "- 样本选择、固定 D+0/D+1/D+2 截面、strict-matched 全桶、CLOB ask、ask 归一化、truth 映射均直接复用 `tools/compare_model_market_accuracy.py`。",
        "- 严格口径维持 book age ≤10 分钟；放宽口径只把 book age 改为“缺失可纳入或 ≤6 小时”，其余条件不变。",
        "- 放宽口径用于概率准确度诊断，不代表这些历史盘口在当时可成交。",
        "",
    ]
    lines.extend(sensitivity_table(strict_samples, relaxed_samples))
    lines.extend(
        [
            "",
            f"- 严格口径有效快照组：{strict_valid_snapshots}；固定截面样本：{len(strict_samples)}。",
            f"- 放宽口径有效快照组：{relaxed_valid_snapshots}；固定截面样本：{len(relaxed_samples)}。",
            f"- 放宽样本城市分布：{counter_text(Counter(str(row['city_key']) for row in relaxed_samples))}",
            "",
            "<details><summary>严格/放宽排除原因</summary>",
            "",
            f"- 严格：{counter_text(strict_exclusions)}",
            f"- 放宽：{counter_text(relaxed_exclusions)}",
            "",
            "</details>",
            "",
            "## 1. 有符号误差：是否系统性偏冷",
            "",
            "`error = raw_json.model_mu - actual_c`；负数代表模型预测最高温低于最终 truth。",
            "",
        ]
    )
    lines.extend(signed_stats_table([("总体", signed_overall)]))
    lines.extend(["", "### 按 lead", ""])
    lines.extend(signed_stats_table(signed_lead))
    lines.extend(["", "### 按城市（N≥5）", ""])
    lines.extend(signed_stats_table(signed_city))
    lines.extend(["", "### 按成熟度", ""])
    lines.extend(signed_stats_table(signed_maturity))
    lines.extend(
        [
            "",
            f"**结论：平均有符号误差 {fmt(signed_overall['mean'], 3)}°C，"
            f"偏冷样本占比 {pct(signed_overall['negative_share'])}。"
            f"{'超过 70%，支持系统性偏冷。' if float(signed_overall['negative_share']) > 0.70 else '未超过 70%，不支持“多数样本系统性偏冷”这一强判断。'}**",
            "",
            "## 2. sigma 与 PIT",
            "",
            "| N | mean |z| | median |z| | P90 |z| | μ±1σ | μ±2σ | 去偏后 ±1σ | 去偏后 ±2σ |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {sigma['n']} | {fmt(sigma['abs_z_mean'], 3)} | {fmt(sigma['abs_z_median'], 3)} | "
            f"{fmt(sigma['abs_z_p90'], 3)} | {pct(raw_coverage_1)} | {pct(raw_coverage_2)} | "
            f"{pct(centered_coverage_1)} | {pct(centered_coverage_2)} |",
            "",
            "- 理论正态覆盖率：±1σ = 68.3%，±2σ = 95.4%。",
            f"- PIT mean={fmt(sigma['pit_mean'], 3)}，median={fmt(sigma['pit_median'], 3)}；{pit_skew}。",
            "",
            "| PIT 区间 | 0-.1 | .1-.2 | .2-.3 | .3-.4 | .4-.5 | .5-.6 | .6-.7 | .7-.8 | .8-.9 | .9-1.0 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            "| 计数 | " + " | ".join(str(value) for value in sigma["pit_bins"]) + " |",
            "",
            f"**结论：{sigma_conclusion} 原始 PIT {pit_skew}。**",
            "",
            "## 3. 多分类 Brier 分解",
            "",
            "- 方法：把每个事件的所有桶视为 one-vs-rest 概率对，按预测概率固定分成 10 档；各项按事件数加权，使总 Brier 保持 `sum_k(p_k-y_k)^2` 的尺度。",
            "- reliability 越低越好；resolution 越高越好；`binning residual` 是固定分箱近似与原始 Brier 的差，不被解释为技能。",
            "",
            "| 概率源 | N | Brier | Reliability | Resolution | Uncertainty | Binning residual |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| A 模型 | {model_decomp['n']} | {fmt(model_decomp['brier'])} | "
            f"{fmt(model_decomp['reliability'])} | {fmt(model_decomp['resolution'])} | "
            f"{fmt(model_decomp['uncertainty'])} | {fmt(model_decomp['binning_residual'])} |",
            f"| B 市场 | {market_decomp['n']} | {fmt(market_decomp['brier'])} | "
            f"{fmt(market_decomp['reliability'])} | {fmt(market_decomp['resolution'])} | "
            f"{fmt(market_decomp['uncertainty'])} | {fmt(market_decomp['binning_residual'])} |",
            "",
            f"- 模型−市场 reliability：{fmt(reliability_gap)}（正数更差）。",
            f"- 模型−市场 resolution：{fmt(resolution_gap)}（负数更差）。",
            f"**判定：{decomposition_conclusion}**",
            "",
            "## 4. 无泄漏走查再校准",
            "",
            "- 概率由与决策快照对应的 `daily_max_predictions.mu/sigma` 重新做 Gaussian CDF 桶积分。",
            "- 训练集按独立 city/date 去重，优先使用 D+1 截面；对每个评估样本，只允许 `train.target_date < evaluation.local_issued_date` 且 `< evaluation.target_date`。",
            "- 因而目标日、同日 D+0、未来 truth 都不可能进入拟合；无历史时使用 identity（bias=0、scale=1），不借用未来全局参数。",
            "- V1：此前全局平均 `(mu-actual)`；V2：此前同城市平均误差；V3：此前全局标准化残差的 MLE scale；V4：先按城市去偏，再以此前样本拟合全局 scale。",
            f"- 独立训练记录 {walk_forward['training_record_count']}；每个评估点此前全局训练 N 中位数 {walk_forward['train_n_median']}，"
            f"城市训练 N 中位数 {walk_forward['city_train_n_median']}；V2 实际有城市历史的评估点 {walk_forward['v2_corrected_n']}/{len(relaxed_samples)}。",
            f"- V3 scale 中位数 {fmt(walk_forward['scale_v3_median'], 3)}；V4 scale 中位数 {fmt(walk_forward['scale_v4_median'], 3)}。",
            "",
        ]
    )
    lines.extend(recalibration_table(relaxed_samples))
    lines.extend(
        [
            "",
            f"**最佳变体：{best_variant}，Brier={best_brier:.4f}；"
            f"{'低于' if best_brier < MARKET_BENCHMARK_BRIER else '未低于'}市场基准 0.6869，"
            f"差值 {best_brier - MARKET_BENCHMARK_BRIER:+.4f}。**",
            "",
            "## 5. 可部署实况截断 C′",
            "",
            "- C′ 仅使用 `observed_at/report_time < issued_at` 且本地日期等于目标日的 METAR、China Live、Wunderground PWS；Open-Meteo historical 与最终 truth 不进入。",
            f"- floor 来源：{counter_text(observation_sources)}。",
            "",
        ]
    )
    lines.extend(lead_floor_table(relaxed_samples))
    lines.extend(
        [
            "",
            f"**C′ 总体 Brier={fmt(cprime_overall['brier'])}；D+0={fmt(cprime_d0['brier'])}；"
            f"D+1={fmt(cprime_d1['brier'])}。上一轮 C 使用最终 truth，不可部署；"
            "C′ 才是决策时真实可实现的实况截断上限。**",
            "",
            "## 6. 成熟度趋势与构成控制",
            "",
        ]
    )
    lines.extend(maturity_rows(composition))
    lines.extend(
        [
            "",
            f"- 同时跨 `<10` 与 `10-19` 的城市：{', '.join(composition['paired_cities']) or '无'}。",
            f"- 共同城市逐城差值改善：{improved_cities}/{len(city_changes)}。",
            "",
            "| 城市 | <10 A-B | 10-19 A-B | 变化（负数=改善） |",
            "|---|---:|---:|---:|",
        ]
    )
    for city, early, mature, change in city_changes:
        lines.append(f"| {city} | {fmt(early)} | {fmt(mature)} | {fmt(change)} |")
    if not city_changes:
        lines.append("| -- | -- | -- | -- |")
    lines.extend(
        [
            "",
            f"**判定：{maturity_conclusion}**",
            "",
            "## 必答三句话",
            "",
            f"1. 模型{'是' if float(signed_overall['negative_share']) > 0.70 else '不是'}“超过 70% 样本系统性偏冷”："
            f"平均有符号误差 {fmt(signed_overall['mean'], 3)}°C，偏冷样本占比 {pct(signed_overall['negative_share'])}。",
            f"2. Brier 分解显示：{decomposition_conclusion}",
            f"3. 无泄漏再校准后最好的是 {best_variant}，Brier={best_brier:.4f}；"
            f"{'能' if best_brier < MARKET_BENCHMARK_BRIER else '不能'}低于市场 0.6869，"
            f"{'领先' if best_brier < MARKET_BENCHMARK_BRIER else '仍落后'} {abs(best_brier - MARKET_BENCHMARK_BRIER):.4f}。",
            "",
            "## 待办观察",
            "",
            "- 本报告只记录、不修复：放宽口径中的缺失 book age 说明历史报价时间契约不完整，不能据此认定可成交。",
            "- 本报告只记录、不修复：若 Gaussian 重建概率与持久化 A 存在差异，来源可能是当时已应用 observed floor 或不同 DEB 版本；本轮不改生产概率。",
            "- 本报告只记录、不修复：truth 表没有外部结算可用时间字段；走查以“训练目标日严格早于评估本地 issued date”作为保守无泄漏边界。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    conn = base.open_read_only(args.db)
    try:
        stations = base.station_rows(conn)
        truths = base.authoritative_truths(conn, stations)
        bucket_events, bucket_lookup = base.strict_bucket_events(conn)
        groups = base.decision_groups(conn)
        predictions = load_predictions(conn)
        maturity = base.maturity_counts(
            truths,
            base.prediction_dates(conn),
        )
        shared = {
            "stations": stations,
            "truths": truths,
            "bucket_events": bucket_events,
            "bucket_lookup": bucket_lookup,
            "groups": groups,
            "predictions": predictions,
            "maturity": maturity,
        }
        strict_samples, strict_exclusions, strict_valid_snapshots = build_cohort(
            conn,
            relaxed=False,
            shared=shared,
        )
        relaxed_samples, relaxed_exclusions, relaxed_valid_snapshots = build_cohort(
            conn,
            relaxed=True,
            shared=shared,
        )
        observations = load_observations(conn, stations)
        strict_observation_sources = apply_deployable_floor(strict_samples, observations)
        relaxed_observation_sources = apply_deployable_floor(relaxed_samples, observations)
        del strict_observation_sources
        walk_forward = apply_walk_forward_recalibration(relaxed_samples)
        report = build_report(
            db_path=args.db.resolve(),
            strict_samples=strict_samples,
            relaxed_samples=relaxed_samples,
            strict_exclusions=strict_exclusions,
            relaxed_exclusions=relaxed_exclusions,
            strict_valid_snapshots=strict_valid_snapshots,
            relaxed_valid_snapshots=relaxed_valid_snapshots,
            observation_sources=relaxed_observation_sources,
            walk_forward=walk_forward,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(report)
        print(f"\nReport: {args.output.resolve()}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
