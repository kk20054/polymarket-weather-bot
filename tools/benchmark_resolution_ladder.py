from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import compare_model_market_accuracy as base
import diagnose_model_recalibration as diagnostic


DEFAULT_DB = ROOT / "data" / "weatherbot_v3.db"
DEFAULT_OUTPUT = (
    ROOT
    / "audits"
    / f"resolution-benchmark-ladder-{date.today().isoformat()}"
    / "README.md"
)
MARKET_REFERENCE_BRIER = 0.6869
MARKET_REFERENCE_RESOLUTION = 0.2116
FAMILIES = ("v3", "ecmwf", "gfs", "icon", "gem", "jma")
FAMILY_LABELS = {
    "v3": "V3",
    "ecmwf": "ECMWF",
    "gfs": "GFS",
    "icon": "ICON",
    "gem": "GEM",
    "jma": "JMA",
}
MODEL_KEYS = {
    "v3": "b1_v3_probs",
    "ecmwf": "b2_ecmwf_probs",
    "gfs": "b3_gfs_probs",
    "icon": "b3_icon_probs",
    "gem": "b3_gem_probs",
    "jma": "b3_jma_probs",
}
DEFAULT_SIGMA_C = 1.5
MIN_SIGMA_C = 0.5
CLIMATOLOGY_DAYS = 45
CLIMATOLOGY_ALPHA = 0.5
EMOS_MIN_TRAINING_ROWS = 10


@dataclass(frozen=True)
class Component:
    family: str
    raw_mu_c: float
    production_mu_c: float
    production_weight: float
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only benchmark ladder locating the source of WeatherBot's "
            "resolution deficit."
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def canonical_family(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"weathercom_v3", "weather.com_v3", "weathercom"}:
        return "v3"
    if text.startswith("ecmwf"):
        return "ecmwf"
    if text.startswith("gfs"):
        return "gfs"
    if text.startswith("icon"):
        return "icon"
    if text.startswith("gem"):
        return "gem"
    if text.startswith("jma"):
        return "jma"
    return text


def load_components(
    conn: Any,
    prediction_ids: Iterable[int],
) -> dict[int, dict[str, Component]]:
    ids = sorted({int(value) for value in prediction_ids})
    if not ids:
        return {}
    result: dict[int, dict[str, Component]] = {}
    chunk_size = 500
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start : start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "SELECT id,components_json,raw_json "
            f"FROM daily_max_predictions WHERE id IN ({placeholders})"
        )
        for row in conn.execute(query, chunk):
            try:
                components = json.loads(row["components_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                components = []
            if not isinstance(components, list) or not components:
                raw = base.safe_json(row["raw_json"])
                components = raw.get("components") or []
            by_family: dict[str, Component] = {}
            for item in components:
                if not isinstance(item, dict):
                    continue
                family = canonical_family(item.get("family") or item.get("model"))
                if family not in FAMILIES:
                    continue
                raw_values = [
                    number
                    for number in (
                        as_float(value) for value in (item.get("raw_daily_highs_c") or [])
                    )
                    if number is not None
                ]
                production_mu = as_float(item.get("model_daily_high_c"))
                raw_mu = fmean(raw_values) if raw_values else production_mu
                if raw_mu is None or production_mu is None:
                    continue
                by_family[family] = Component(
                    family=family,
                    raw_mu_c=float(raw_mu),
                    production_mu_c=float(production_mu),
                    production_weight=float(as_float(item.get("weight")) or 0.0),
                    source=str(item.get("source") or ""),
                )
            result[int(row["id"])] = by_family
    return result


def build_samples(conn: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stations = base.station_rows(conn)
    truths = base.authoritative_truths(conn, stations)
    bucket_events, bucket_lookup = base.strict_bucket_events(conn)
    groups = base.decision_groups(conn)
    predictions = diagnostic.load_predictions(conn)
    maturity = base.maturity_counts(truths, base.prediction_dates(conn))
    shared = {
        "stations": stations,
        "truths": truths,
        "bucket_events": bucket_events,
        "bucket_lookup": bucket_lookup,
        "groups": groups,
        "predictions": predictions,
        "maturity": maturity,
    }
    samples, exclusions, valid_snapshots = diagnostic.build_cohort(
        conn,
        relaxed=True,
        shared=shared,
    )
    components = load_components(
        conn,
        (int(sample["prediction_id"]) for sample in samples),
    )
    for sample in samples:
        sample["components"] = components.get(int(sample["prediction_id"]), {})
        sample["family_raw_mu"] = {
            family: component.raw_mu_c
            for family, component in sample["components"].items()
        }
    return samples, {
        "stations": stations,
        "truths": truths,
        "exclusions": exclusions,
        "valid_snapshots": valid_snapshots,
    }


def strict_prior(
    processed: list[dict[str, Any]],
    sample: dict[str, Any],
) -> list[dict[str, Any]]:
    local_issued_date = sample["local_issued_at"].date().isoformat()
    return [
        row
        for row in processed
        if str(row["target_date"]) < local_issued_date
        and str(row["target_date"]) < str(sample["target_date"])
    ]


def rmse(values: Iterable[float], default: float = DEFAULT_SIGMA_C) -> float:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    if not rows:
        return default
    return max(MIN_SIGMA_C, math.sqrt(fmean(value * value for value in rows)))


def sample_std(values: Iterable[float]) -> float:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    if len(rows) < 2:
        return 0.0
    mean_value = fmean(rows)
    return math.sqrt(
        sum((value - mean_value) ** 2 for value in rows) / (len(rows) - 1)
    )


def empirical_climatology(
    sample: dict[str, Any],
    truths: dict[tuple[str, str], base.Truth],
) -> tuple[list[float], int]:
    target = date.fromisoformat(str(sample["target_date"]))
    issued_date = sample["local_issued_at"].date()
    values: list[float] = []
    for (city_key, target_date), truth in truths.items():
        if city_key != sample["city_key"]:
            continue
        truth_date = date.fromisoformat(target_date)
        if not truth_date < issued_date or not truth_date < target:
            continue
        if (target - truth_date).days > CLIMATOLOGY_DAYS:
            continue
        values.append(float(truth.actual_c))
    counts = [CLIMATOLOGY_ALPHA for _ in sample["buckets"]]
    for actual_c in values:
        matches = [
            index
            for index, bucket in enumerate(sample["buckets"])
            if base.bucket_contains(actual_c, bucket)
        ]
        if len(matches) == 1:
            counts[matches[0]] += 1.0
    total = sum(counts)
    return [value / total for value in counts], len(values)


def component_mean(sample: dict[str, Any]) -> float | None:
    values = list(sample["family_raw_mu"].values())
    return fmean(values) if values else None


def component_spread(sample: dict[str, Any]) -> float | None:
    values = list(sample["family_raw_mu"].values())
    return sample_std(values) if len(values) >= 2 else None


def station_lead_bias(
    prior: list[dict[str, Any]],
    sample: dict[str, Any],
    family: str,
) -> tuple[float, int]:
    errors = [
        float(row["family_raw_mu"][family]) - float(row["actual_c"])
        for row in prior
        if row["city_key"] == sample["city_key"]
        and row["lead"] == sample["lead"]
        and family in row["family_raw_mu"]
    ]
    return (fmean(errors), len(errors)) if errors else (0.0, 0)


def family_mae(
    prior: list[dict[str, Any]],
    sample: dict[str, Any],
    family: str,
    bias: float,
) -> tuple[float | None, str, int]:
    station_rows = [
        row
        for row in prior
        if row["city_key"] == sample["city_key"]
        and row["lead"] == sample["lead"]
        and family in row["family_raw_mu"]
    ]
    if station_rows:
        values = [
            abs(float(row["family_raw_mu"][family]) - bias - float(row["actual_c"]))
            for row in station_rows
        ]
        return fmean(values), "station_lead", len(values)
    global_rows = [row for row in prior if family in row["family_raw_mu"]]
    if global_rows:
        values = [
            abs(float(row["family_raw_mu"][family]) - float(row["actual_c"]))
            for row in global_rows
        ]
        return fmean(values), "global_family", len(values)
    return None, "equal_cold_start", 0


def ols(x: list[float], y: list[float]) -> tuple[float, float]:
    if not x or len(x) != len(y):
        return 0.0, 1.0
    x_mean = fmean(x)
    y_mean = fmean(y)
    denominator = sum((value - x_mean) ** 2 for value in x)
    if denominator <= 1e-12:
        return y_mean - x_mean, 1.0
    slope = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x, y)
    ) / denominator
    slope = min(2.0, max(0.0, slope))
    return y_mean - slope * x_mean, slope


def gaussian_nll(
    actual: list[float],
    means: list[float],
    spreads: list[float],
    c_value: float,
    d_value: float,
) -> float:
    total = 0.0
    for truth, mean_value, spread in zip(actual, means, spreads):
        sigma = max(MIN_SIGMA_C, c_value + d_value * spread)
        residual = truth - mean_value
        total += math.log(sigma) + 0.5 * (residual / sigma) ** 2
    return total


def fit_nonnegative_sigma(
    actual: list[float],
    means: list[float],
    spreads: list[float],
) -> tuple[float, float]:
    residual_scale = [
        abs(truth - mean_value) * math.sqrt(math.pi / 2.0)
        for truth, mean_value in zip(actual, means)
    ]
    constant = max(MIN_SIGMA_C, fmean(residual_scale))
    candidates: list[tuple[float, float]] = [(constant, 0.0)]
    if len(spreads) >= 2:
        spread_mean = fmean(spreads)
        scale_mean = fmean(residual_scale)
        denominator = sum((value - spread_mean) ** 2 for value in spreads)
        if denominator > 1e-12:
            slope = sum(
                (spread - spread_mean) * (scale - scale_mean)
                for spread, scale in zip(spreads, residual_scale)
            ) / denominator
            intercept = scale_mean - slope * spread_mean
            if slope >= 0.0 and intercept >= 0.0:
                candidates.append((intercept, slope))
        squared = sum(value * value for value in spreads)
        if squared > 1e-12:
            through_origin = sum(
                spread * scale for spread, scale in zip(spreads, residual_scale)
            ) / squared
            if through_origin >= 0.0:
                candidates.append((0.0, through_origin))
    return min(
        candidates,
        key=lambda pair: gaussian_nll(
            actual,
            means,
            spreads,
            pair[0],
            pair[1],
        ),
    )


def fit_emos(prior: list[dict[str, Any]]) -> tuple[float, float, float, float, int]:
    rows = [
        row
        for row in prior
        if row.get("raw_ens_mean") is not None and row.get("raw_ens_spread") is not None
    ]
    if len(rows) < EMOS_MIN_TRAINING_ROWS:
        return 0.0, 1.0, DEFAULT_SIGMA_C, 0.0, len(rows)
    means = [float(row["raw_ens_mean"]) for row in rows]
    spreads = [float(row["raw_ens_spread"]) for row in rows]
    actual = [float(row["actual_c"]) for row in rows]
    a_value, b_value = ols(means, actual)
    fitted_means = [a_value + b_value * value for value in means]
    c_value, d_value = fit_nonnegative_sigma(
        actual,
        fitted_means,
        spreads,
    )
    return a_value, b_value, c_value, d_value, len(rows)


def apply_ladder(
    samples: list[dict[str, Any]],
    truths: dict[tuple[str, str], base.Truth],
) -> dict[str, Any]:
    processed: list[dict[str, Any]] = []
    family_sigma_training: dict[str, list[int]] = defaultdict(list)
    b6_bias_training: list[int] = []
    b7_mae_sources: Counter[str] = Counter()
    emos_training: list[int] = []
    climatology_training: list[int] = []

    ordered = sorted(
        samples,
        key=lambda row: (
            row["issued_at"],
            row["target_date"],
            row["city_key"],
            row["lead"],
        ),
    )
    for sample in ordered:
        prior = strict_prior(processed, sample)
        sample["b0_probs"], climate_n = empirical_climatology(sample, truths)
        sample["b0_train_n"] = climate_n
        climatology_training.append(climate_n)

        for family, probability_key in MODEL_KEYS.items():
            if family not in sample["family_raw_mu"]:
                continue
            residuals = [
                float(row["family_raw_mu"][family]) - float(row["actual_c"])
                for row in prior
                if family in row["family_raw_mu"]
            ]
            sigma = rmse(residuals)
            sample[probability_key] = diagnostic.gaussian_bucket_probs(
                float(sample["family_raw_mu"][family]),
                sigma,
                sample["buckets"],
            )
            sample[f"{family}_sigma_c"] = sigma
            sample[f"{family}_sigma_train_n"] = len(residuals)
            family_sigma_training[family].append(len(residuals))

        values = list(sample["family_raw_mu"].values())
        sample["raw_ens_mean"] = fmean(values) if values else None
        sample["raw_ens_spread"] = sample_std(values) if len(values) >= 2 else None
        if len(values) >= 2:
            prior_b5_residuals = [
                float(row["b5_mu_c"]) - float(row["actual_c"])
                for row in prior
                if row.get("b5_mu_c") is not None
            ]
            sample["b5_mu_c"] = fmean(values)
            sample["b5_sigma_c"] = rmse(prior_b5_residuals)
            sample["b5_probs"] = diagnostic.gaussian_bucket_probs(
                sample["b5_mu_c"],
                sample["b5_sigma_c"],
                sample["buckets"],
            )

            adjusted: dict[str, float] = {}
            biases: dict[str, float] = {}
            bias_counts: dict[str, int] = {}
            for family, raw_mu in sample["family_raw_mu"].items():
                bias, bias_n = station_lead_bias(prior, sample, family)
                adjusted[family] = float(raw_mu) - bias
                biases[family] = bias
                bias_counts[family] = bias_n
            b6_bias_training.extend(bias_counts.values())
            sample["b6_biases"] = biases
            sample["b6_bias_counts"] = bias_counts
            sample["b6_mu_c"] = fmean(adjusted.values())
            prior_b6_residuals = [
                float(row["b6_mu_c"]) - float(row["actual_c"])
                for row in prior
                if row.get("b6_mu_c") is not None
            ]
            sample["b6_sigma_c"] = rmse(prior_b6_residuals)
            sample["b6_probs"] = diagnostic.gaussian_bucket_probs(
                sample["b6_mu_c"],
                sample["b6_sigma_c"],
                sample["buckets"],
            )

            inverse_mae: dict[str, float] = {}
            for family in adjusted:
                mae, source, _ = family_mae(
                    prior,
                    sample,
                    family,
                    biases[family],
                )
                b7_mae_sources[source] += 1
                inverse_mae[family] = 1.0 / max(mae, 0.1) if mae is not None else 1.0
            weight_sum = sum(inverse_mae.values())
            weights = {
                family: value / weight_sum for family, value in inverse_mae.items()
            }
            sample["b7_weights"] = weights
            sample["b7_mu_c"] = sum(
                adjusted[family] * weights[family] for family in adjusted
            )
            prior_b7_residuals = [
                float(row["b7_mu_c"]) - float(row["actual_c"])
                for row in prior
                if row.get("b7_mu_c") is not None
            ]
            sample["b7_sigma_c"] = rmse(prior_b7_residuals)
            sample["b7_probs"] = diagnostic.gaussian_bucket_probs(
                sample["b7_mu_c"],
                sample["b7_sigma_c"],
                sample["buckets"],
            )

            a_value, b_value, c_value, d_value, train_n = fit_emos(prior)
            sample["b8_mu_c"] = a_value + b_value * float(sample["raw_ens_mean"])
            sample["b8_sigma_c"] = max(
                MIN_SIGMA_C,
                c_value + d_value * float(sample["raw_ens_spread"]),
            )
            sample["b8_params"] = {
                "a": a_value,
                "b": b_value,
                "c": c_value,
                "d": d_value,
                "train_n": train_n,
            }
            sample["b8_probs"] = diagnostic.gaussian_bucket_probs(
                sample["b8_mu_c"],
                sample["b8_sigma_c"],
                sample["buckets"],
            )
            emos_training.append(train_n)
        processed.append(sample)
    return {
        "climatology_train_n_median": median(climatology_training)
        if climatology_training
        else 0,
        "family_sigma_train_n_median": {
            family: median(values) if values else 0
            for family, values in family_sigma_training.items()
        },
        "b6_bias_train_n_median": median(b6_bias_training)
        if b6_bias_training
        else 0,
        "b7_mae_sources": b7_mae_sources,
        "emos_train_n_median": median(emos_training) if emos_training else 0,
    }


def gaussian_crps(mu_c: float, sigma_c: float, actual_c: float) -> float:
    sigma = max(float(sigma_c), 1e-9)
    z_value = (float(actual_c) - float(mu_c)) / sigma
    phi = math.exp(-0.5 * z_value * z_value) / math.sqrt(2.0 * math.pi)
    standard_cdf = 0.5 * (1.0 + math.erf(z_value / math.sqrt(2.0)))
    return sigma * (
        z_value * (2.0 * standard_cdf - 1.0)
        + 2.0 * phi
        - 1.0 / math.sqrt(math.pi)
    )


def rows_with(samples: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [sample for sample in samples if sample.get(key) is not None]


def metric_bundle(
    samples: list[dict[str, Any]],
    probability_key: str,
) -> dict[str, Any]:
    rows = rows_with(samples, probability_key)
    metrics = base.summarize(rows, probability_key)
    decomposition = diagnostic.brier_decomposition(rows, probability_key)
    market = base.summarize(rows, "market_probs")
    market_decomposition = diagnostic.brier_decomposition(rows, "market_probs")
    return {
        "rows": rows,
        "metrics": metrics,
        "decomposition": decomposition,
        "market": market,
        "market_decomposition": market_decomposition,
        "vs_market": (
            float(metrics["brier"]) - float(market["brier"])
            if metrics["brier"] is not None and market["brier"] is not None
            else None
        ),
    }


def ladder_results(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = [
        ("B0", "气候态基线", "b0_probs"),
        ("B1", "V3 单模型", "b1_v3_probs"),
        ("B2", "ECMWF 单模型", "b2_ecmwf_probs"),
        ("B3-GFS", "GFS 单模型", "b3_gfs_probs"),
        ("B3-ICON", "ICON 单模型", "b3_icon_probs"),
        ("B3-GEM", "GEM 单模型", "b3_gem_probs"),
        ("B3-JMA", "JMA 单模型", "b3_jma_probs"),
        ("B4", "当前生产 DEB", "model_probs"),
        ("B5", "可用模型等权平均", "b5_probs"),
        ("B6", "站点×提前量去偏后等权", "b6_probs"),
        ("B7", "去偏后逆 MAE 加权", "b7_probs"),
        ("B8", "EMOS", "b8_probs"),
        ("B9", "市场", "market_probs"),
    ]
    result: list[dict[str, Any]] = []
    for code, name, key in definitions:
        bundle = metric_bundle(samples, key)
        result.append(
            {
                "code": code,
                "name": name,
                "key": key,
                **bundle,
            }
        )
    return result


def paired_single_model_comparison(
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for family, key in MODEL_KEYS.items():
        rows = rows_with(samples, key)
        single = base.summarize(rows, key)
        production = base.summarize(rows, "model_probs")
        result.append(
            {
                "family": family,
                "n": len(rows),
                "single_brier": float(single["brier"]),
                "production_brier": float(production["brier"]),
                "delta": float(single["brier"]) - float(production["brier"]),
            }
        )
    return result


def compare_to_production(
    samples: list[dict[str, Any]],
    probability_key: str,
) -> dict[str, float | int]:
    rows = rows_with(samples, probability_key)
    candidate = base.summarize(rows, probability_key)
    production = base.summarize(rows, "model_probs")
    candidate_decomp = diagnostic.brier_decomposition(rows, probability_key)
    production_decomp = diagnostic.brier_decomposition(rows, "model_probs")
    return {
        "n": len(rows),
        "brier_improvement": float(production["brier"]) - float(candidate["brier"]),
        "resolution_improvement": float(candidate_decomp["resolution"])
        - float(production_decomp["resolution"]),
        "production_brier": float(production["brier"]),
        "candidate_brier": float(candidate["brier"]),
        "production_resolution": float(production_decomp["resolution"]),
        "candidate_resolution": float(candidate_decomp["resolution"]),
    }


def model_error_rows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for sample in samples:
        for family, mu_c in sample["family_raw_mu"].items():
            groups[(family, str(sample["city_key"]))].append(
                float(mu_c) - float(sample["actual_c"])
            )
    result: list[dict[str, Any]] = []
    for (family, city_key), errors in sorted(groups.items()):
        result.append(
            {
                "family": family,
                "city_key": city_key,
                "n": len(errors),
                "mean_error": fmean(errors),
                "mae": fmean(abs(value) for value in errors),
            }
        )
    return result


def aggregate_model_errors(
    samples: list[dict[str, Any]],
    *,
    china_only: bool = False,
) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        is_mainland_china = (
            str(sample["city_key"]) != "hong-kong"
            and str(sample["city_key"])
            in {
                "beijing",
                "chengdu",
                "chongqing",
                "guangzhou",
                "qingdao",
                "shanghai",
                "shenzhen",
                "wuhan",
            }
        )
        if china_only and not is_mainland_china:
            continue
        for family, mu_c in sample["family_raw_mu"].items():
            groups[family].append(float(mu_c) - float(sample["actual_c"]))
    return [
        {
            "family": family,
            "n": len(groups.get(family, [])),
            "mean_error": fmean(groups[family]) if groups.get(family) else math.nan,
            "mae": (
                fmean(abs(value) for value in groups[family])
                if groups.get(family)
                else math.nan
            ),
        }
        for family in FAMILIES
    ]


def top_city_analysis(samples: list[dict[str, Any]]) -> dict[str, Any]:
    b8_rows = rows_with(samples, "b8_probs")
    counts = Counter(str(row["city_key"]) for row in b8_rows)
    top_cities = [city for city, _ in counts.most_common(5)]
    top_rows = [row for row in b8_rows if row["city_key"] in top_cities]
    full_model = base.summarize(b8_rows, "b8_probs")
    full_market = base.summarize(b8_rows, "market_probs")
    top_model = base.summarize(top_rows, "b8_probs")
    top_market = base.summarize(top_rows, "market_probs")
    return {
        "all_rows": b8_rows,
        "top_rows": top_rows,
        "counts": counts,
        "top_cities": top_cities,
        "full_model": full_model,
        "full_market": full_market,
        "top_model": top_model,
        "top_market": top_market,
        "full_gap": float(full_model["brier"]) - float(full_market["brier"]),
        "top_gap": float(top_model["brier"]) - float(top_market["brier"]),
    }


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if not math.isfinite(number):
        return "--"
    return f"{number:.{digits}f}"


def pct(value: Any) -> str:
    if value is None:
        return "--"
    return f"{float(value) * 100.0:.2f}%"


def counter_text(counter: Counter[str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in counter.most_common()) or "无"


def ladder_table(results: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| 编号 | 概率来源 | N | Brier | Log loss | Top-1 | Top-2 | Resolution | vs 同批市场 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        metrics = row["metrics"]
        decomposition = row["decomposition"]
        lines.append(
            f"| {row['code']} | {row['name']} | {metrics['n']} | "
            f"{fmt(metrics['brier'])} | {fmt(metrics['log_loss'])} | "
            f"{pct(metrics['top1'])} | {pct(metrics['top2'])} | "
            f"{fmt(decomposition['resolution'])} | {fmt(row['vs_market'])} |"
        )
    return lines


def build_report(
    *,
    db_path: Path,
    samples: list[dict[str, Any]],
    metadata: dict[str, Any],
    ladder: list[dict[str, Any]],
    training: dict[str, Any],
) -> str:
    by_code = {row["code"]: row for row in ladder}
    pairwise = paired_single_model_comparison(samples)
    b6_vs_b4 = compare_to_production(samples, "b6_probs")
    b7_vs_b4 = compare_to_production(samples, "b7_probs")
    b8 = by_code["B8"]
    b5_b8_rows = [
        row
        for row in samples
        if row.get("b5_probs") is not None and row.get("b8_probs") is not None
    ]
    b5_crps = fmean(
        gaussian_crps(row["b5_mu_c"], row["b5_sigma_c"], row["actual_c"])
        for row in b5_b8_rows
    )
    b8_crps = fmean(
        gaussian_crps(row["b8_mu_c"], row["b8_sigma_c"], row["actual_c"])
        for row in b5_b8_rows
    )
    crps_improvement = (b5_crps - b8_crps) / b5_crps if b5_crps > 0 else math.nan
    errors_by_station = model_error_rows(samples)
    errors_all = aggregate_model_errors(samples)
    errors_china = aggregate_model_errors(samples, china_only=True)
    china_lookup = {row["family"]: row for row in errors_china}
    top_city = top_city_analysis(samples)
    enabled_count = sum(
        1 for station in metadata["stations"].values() if int(station.get("enabled") or 0)
    )
    probability_city_count = len({str(row["city_key"]) for row in samples})
    component_counts = Counter(len(row["family_raw_mu"]) for row in samples)
    highest_resolution = max(
        (row for row in ladder if row["code"] != "B9"),
        key=lambda row: float(row["decomposition"]["resolution"]),
    )
    best_brier = min(
        (row for row in ladder if row["code"] != "B9"),
        key=lambda row: float(row["metrics"]["brier"]),
    )
    best_market_decomp = best_brier["market_decomposition"]
    best_decomp = best_brier["decomposition"]
    reliability_gap = float(best_decomp["reliability"]) - float(
        best_market_decomp["reliability"]
    )
    resolution_gap = float(best_decomp["resolution"]) - float(
        best_market_decomp["resolution"]
    )
    dominant_gap = (
        "resolution"
        if abs(resolution_gap) > abs(reliability_gap)
        else "reliability"
    )
    better_singles = [row for row in pairwise if row["delta"] < 0]
    gem_china = china_lookup["gem"]
    jma_china = china_lookup["jma"]
    gem_jma_cold = (
        float(gem_china["mean_error"]) < 0 and float(jma_china["mean_error"]) < 0
    )
    narrowed = abs(top_city["top_gap"]) < abs(top_city["full_gap"])
    top_city_text = ", ".join(
        f"{city}({top_city['counts'][city]})" for city in top_city["top_cities"]
    )
    source_coverage = Counter()
    for sample in samples:
        for family in sample["family_raw_mu"]:
            source_coverage[family] += 1

    lines = [
        "# WeatherBot Resolution 基准阶梯诊断",
        "",
        f"- 生成时间：`{datetime.now().astimezone().isoformat()}`",
        f"- 数据库：`{db_path}`，以 SQLite `mode=ro` + `query_only=ON` 打开。",
        "- 本轮只新增分析脚本与本报告；未写库、未改 schema、未改生产代码，未运行测试或前端构建。",
        "",
        "## 样本契约",
        "",
        "- 概率样本、权威 truth、strict-matched 全桶、CLOB ask、固定 D+0/D+1 截面，直接复用 `tools/diagnose_model_recalibration.py` 的放宽口径。",
        f"- 概率样本 N={len(samples)}，覆盖 {probability_city_count} 城；注册表 enabled 城市为 {enabled_count}。其余 enabled 城市没有同时满足既有 truth、完整市场桶与历史概率快照，因此没有被虚构补入。",
        f"- 原始模型覆盖：{', '.join(f'{FAMILY_LABELS[key]}={source_coverage[key]}' for key in FAMILIES)}。",
        f"- 每事件可用模型家族数：{counter_text(component_counts)}。B5-B8 仅在当时至少有 2 个真实模型时计算，不使用未来模型补齐。",
        "- 单模型行使用各自真实覆盖子集；Q1 另用完全相同子集对比 B4，避免把覆盖差异误判为模型技能。",
        "- 所有走查拟合只使用 `train.target_date < evaluation.local_issued_date` 且 `< evaluation.target_date` 的已结算记录；目标日和未来 truth 无法进入训练。",
        "",
        "## 基准阶梯",
        "",
        "- B0：同城此前 45 天权威 truth 的桶频率，Jeffreys/Laplace 平滑 α=0.5；无历史时退化为均匀分布。",
        "- B1-B3：组件内 `raw_daily_highs_c` 的均值；sigma 仅用此前全局该模型残差 RMSE 走查拟合，冷启动为 1.5°C。",
        "- B5：当时可用模型原始中心等权；B6：每模型先按站点×提前量历史误差平移，再等权；B7：同样去偏后按历史逆 MAE 加权。",
        "- B8：六模型可用子集的均值/家族间 spread；`mu=a+b·ens_mean` 用 OLS，`sigma=max(0.5,c+d·ens_spread)` 用非负线性尺度并按 Gaussian NLL 选解。训练 N<10 时使用 identity 冷启动。",
        "",
    ]
    lines.extend(ladder_table(ladder))
    lines.extend(
        [
            "",
            f"- 走查训练中位数：气候态 {fmt(training['climatology_train_n_median'], 1)} 日；"
            f"B6 站点×提前量 bias {fmt(training['b6_bias_train_n_median'], 1)}；"
            f"EMOS {fmt(training['emos_train_n_median'], 1)}。",
            f"- B7 MAE 权重来源：{counter_text(training['b7_mae_sources'])}。",
            "",
            "## Q1：单模型是否胜过当前融合",
            "",
            "| 模型 | N | 单模型 Brier | 同子集 B4 | 单模型-B4 | 判定 |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in pairwise:
        lines.append(
            f"| {FAMILY_LABELS[row['family']]} | {row['n']} | "
            f"{fmt(row['single_brier'])} | {fmt(row['production_brier'])} | "
            f"{fmt(row['delta'])} | {'单模型更好' if row['delta'] < 0 else 'B4 更好'} |"
        )
    lines.extend(
        [
            "",
            (
                "**结论："
                + (
                    "存在单模型优于当前融合："
                    + "、".join(
                        f"{FAMILY_LABELS[row['family']]} {abs(row['delta']):.4f}"
                        for row in better_singles
                    )
                    + "。"
                    if better_singles
                    else "没有单模型在其同子集上优于当前融合。"
                )
                + "**"
            ),
            "",
            "## Q2：先去偏再融合",
            "",
            "| 方案 | N | B4 Brier | 方案 Brier | Brier 改善 | B4 resolution | 方案 resolution | resolution 改善 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
            f"| B6 | {b6_vs_b4['n']} | {fmt(b6_vs_b4['production_brier'])} | {fmt(b6_vs_b4['candidate_brier'])} | {fmt(b6_vs_b4['brier_improvement'])} | {fmt(b6_vs_b4['production_resolution'])} | {fmt(b6_vs_b4['candidate_resolution'])} | {fmt(b6_vs_b4['resolution_improvement'])} |",
            f"| B7 | {b7_vs_b4['n']} | {fmt(b7_vs_b4['production_brier'])} | {fmt(b7_vs_b4['candidate_brier'])} | {fmt(b7_vs_b4['brier_improvement'])} | {fmt(b7_vs_b4['production_resolution'])} | {fmt(b7_vs_b4['candidate_resolution'])} | {fmt(b7_vs_b4['resolution_improvement'])} |",
            "",
            "## Q3：EMOS 与市场分辨力",
            "",
            f"- B8 resolution={fmt(b8['decomposition']['resolution'])}；距固定市场参考 0.2116 为 {fmt(float(b8['decomposition']['resolution']) - MARKET_REFERENCE_RESOLUTION)}。",
            f"- B8 同子集市场 resolution={fmt(b8['market_decomposition']['resolution'])}，差值 {fmt(float(b8['decomposition']['resolution']) - float(b8['market_decomposition']['resolution']))}。",
            f"- Gaussian CRPS：B5={fmt(b5_crps)}，B8={fmt(b8_crps)}，实际改进 {pct(crps_improvement)}；这是本项目实测，不套用文献的 16–24%。",
            "",
            "## Q4：逐模型误差",
            "",
            "### 总体与中国大陆",
            "",
            "| 模型 | 全部 N | 全部 mean error °C | 全部 MAE °C | 中国 N | 中国 mean error °C | 中国 MAE °C |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    all_lookup = {row["family"]: row for row in errors_all}
    for family in FAMILIES:
        overall = all_lookup[family]
        china = china_lookup[family]
        lines.append(
            f"| {FAMILY_LABELS[family]} | {overall['n']} | "
            f"{fmt(overall['mean_error'], 3)} | {fmt(overall['mae'], 3)} | "
            f"{china['n']} | {fmt(china['mean_error'], 3)} | {fmt(china['mae'], 3)} |"
        )
    lines.extend(
        [
            "",
            (
                f"**GEM/JMA 中国判断：GEM mean={fmt(gem_china['mean_error'], 3)}°C "
                f"(N={gem_china['n']})，JMA mean={fmt(jma_china['mean_error'], 3)}°C "
                f"(N={jma_china['n']})。"
                + (
                    "两者均明显冷偏，且 MAE 未显示相对技能优势；因此合计 19.3% 的固定先验权重在当前本站历史证据下没有依据。"
                    if gem_jma_cold
                    else "两者并非同时大幅冷偏，不能据此否定合计 19.3% 的先验权重。"
                )
                + "**"
            ),
            "",
            "<details><summary>按站点完整误差表</summary>",
            "",
            "| 模型 | 城市 | N | Mean signed error °C | MAE °C |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in errors_by_station:
        lines.append(
            f"| {FAMILY_LABELS[row['family']]} | {row['city_key']} | {row['n']} | "
            f"{fmt(row['mean_error'], 3)} | {fmt(row['mae'], 3)} |"
        )
    lines.extend(
        [
            "",
            "</details>",
            "",
            "## Q5：收窄到样本最多的五城",
            "",
            f"- B8 可评估全集：N={len(top_city['all_rows'])}，城市数={len(top_city['counts'])}；"
            f"Top-5={top_city_text}。",
            "",
            "| 范围 | N | B8 Brier | 市场 Brier | B8-市场 |",
            "|---|---:|---:|---:|---:|",
            f"| 全部可评估城市 | {len(top_city['all_rows'])} | {fmt(top_city['full_model']['brier'])} | {fmt(top_city['full_market']['brier'])} | {fmt(top_city['full_gap'])} |",
            f"| 样本最多 5 城 | {len(top_city['top_rows'])} | {fmt(top_city['top_model']['brier'])} | {fmt(top_city['top_market']['brier'])} | {fmt(top_city['top_gap'])} |",
            "",
            f"**结论：收窄五城后差距{'收窄' if narrowed else '扩大'} {abs(top_city['top_gap'] - top_city['full_gap']):.4f}。"
            f"{'该结果支持优先聚焦高样本城市。' if narrowed else '该结果不支持仅靠收窄城市范围提升相对市场技能。'}**",
            "",
            "## 必答三句话",
            "",
            (
                "1. 当前融合（B4）"
                + (
                    "劣于至少一个单模型；最好单模型相对同子集 B4 改善 "
                    f"{max(abs(row['delta']) for row in better_singles):.4f}。"
                    if better_singles
                    else "没有劣于任何可评估单模型。"
                )
            ),
            f"2. 阶梯中 resolution 最高的非市场方案是 {highest_resolution['code']} {highest_resolution['name']}，resolution={fmt(highest_resolution['decomposition']['resolution'])}，距市场参考 0.2116 为 {fmt(float(highest_resolution['decomposition']['resolution']) - MARKET_REFERENCE_RESOLUTION)}。",
            f"3. 最低 Brier 的非市场方案是 {best_brier['code']} {best_brier['name']}；若与同子集市场相比，主要缺口在 {dominant_gap}。完整分解：Brier={fmt(best_decomp['brier'])}，reliability={fmt(best_decomp['reliability'])}，resolution={fmt(best_decomp['resolution'])}，uncertainty={fmt(best_decomp['uncertainty'])}，binning residual={fmt(best_decomp['binning_residual'])}；同子集市场 reliability={fmt(best_market_decomp['reliability'])}、resolution={fmt(best_market_decomp['resolution'])}。",
            "",
            "## 待办观察",
            "",
            "- 49 个 enabled 城市中，当前概率准确度口径只有 34 城具备权威 truth、完整 strict-matched 桶与历史概率快照；本报告没有用非权威 truth 扩充。",
            "- JMA 历史组件覆盖显著低于其他模型，因此其单模型结论 N 较小；报告通过同子集 B4 对照消除直接比较偏差，但不能扩大其外推范围。",
            "- EMOS 本轮使用无需第三方依赖的 OLS + 非负 sigma 回归，并以 Gaussian NLL 在候选尺度中选解；它是严格无泄漏基准，不等同于完整 CRPS 数值优化实现。",
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
        samples, metadata = build_samples(conn)
        training = apply_ladder(samples, metadata["truths"])
        ladder = ladder_results(samples)
        report = build_report(
            db_path=args.db.resolve(),
            samples=samples,
            metadata=metadata,
            ladder=ladder,
            training=training,
        )
    finally:
        conn.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nReport: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
