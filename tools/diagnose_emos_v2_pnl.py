from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import benchmark_resolution_ladder as ladder
import compare_model_market_accuracy as base
import diagnose_model_recalibration as diagnostic


DEFAULT_DB = ROOT / "data" / "weatherbot_v3.db"
DEFAULT_OUTPUT = (
    ROOT
    / "audits"
    / f"emos-v2-pnl-{date.today().isoformat()}"
    / "README.md"
)
SUBSETS = {
    "all6": ("v3", "ecmwf", "gfs", "icon", "gem", "jma"),
    "core4": ("v3", "ecmwf", "gfs", "icon"),
    "v3_icon": ("v3", "icon"),
    "v3": ("v3",),
}
EDGE_THRESHOLDS = (0.05, 0.08, 0.10, 0.15, 0.20)
BOOTSTRAP_DRAWS = 5000
MIN_TRAINING_ROWS = 10
MIN_SIGMA_C = 0.5


@dataclass(frozen=True)
class MemberSpread:
    available_at: datetime
    spread_c: float
    member_count: int
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only EMOS-v2, conditional advantage, and unit P&L diagnosis."
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


def sample_std(values: Iterable[float]) -> float:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    if len(rows) < 2:
        return 0.0
    center = fmean(rows)
    return math.sqrt(
        sum((value - center) ** 2 for value in rows) / (len(rows) - 1)
    )


def lead_group(value: Any) -> str:
    text = str(value or "")
    if text.startswith("D+0"):
        return "D+0"
    if text.startswith("D+1"):
        return "D+1"
    return text


def raw_asks(sample: dict[str, Any]) -> list[float]:
    ask_sum = float(sample.get("ask_sum") or 0.0)
    return [float(value) * ask_sum for value in sample["market_probs"]]


def attach_ask_sums(
    samples: list[dict[str, Any]],
    snapshots: list[base.Snapshot],
    stations: dict[str, dict[str, Any]],
) -> None:
    selected, _ = base.select_checkpoint_samples(snapshots, stations)
    lookup = {
        (lead, snapshot.city_key, snapshot.target_date, snapshot.issued_at):
        snapshot.ask_sum
        for lead, snapshot in selected
    }
    for sample in samples:
        key = (
            str(sample["lead"]),
            str(sample["city_key"]),
            str(sample["target_date"]),
            sample["issued_at"],
        )
        sample["ask_sum"] = float(lookup[key])


def metric_bundle(
    samples: list[dict[str, Any]],
    probability_key: str,
) -> dict[str, Any]:
    rows = [row for row in samples if row.get(probability_key) is not None]
    model = base.summarize(rows, probability_key)
    market = base.summarize(rows, "market_probs")
    model_decomp = diagnostic.brier_decomposition(rows, probability_key)
    market_decomp = diagnostic.brier_decomposition(rows, "market_probs")
    return {
        "rows": rows,
        "model": model,
        "market": market,
        "model_decomp": model_decomp,
        "market_decomp": market_decomp,
        "vs_market": (
            float(model["brier"]) - float(market["brier"])
            if model["brier"] is not None and market["brier"] is not None
            else None
        ),
    }


def parse_available_at(row: sqlite3.Row) -> datetime | None:
    for key in ("available_at", "retrieved_at", "run_at", "created_at"):
        value = str(row[key] or "").strip()
        if not value:
            continue
        try:
            return base.parse_timestamp(value)
        except (TypeError, ValueError):
            continue
    return None


def load_gfs_member_spreads(
    conn: sqlite3.Connection,
    relevant_pairs: set[tuple[str, str]],
) -> tuple[dict[tuple[str, str], list[MemberSpread]], dict[str, int]]:
    run_meta: dict[int, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT id,city,target_date,source,unit,member_count,
               available_at,retrieved_at,run_at,created_at,
               training_eligible,parse_status
        FROM forecast_runs
        WHERE source IN ('gfs_ensemble','openmeteo_ensemble_gfs_seamless')
          AND training_eligible=1
          AND member_count>=30
          AND (parse_status IS NULL OR parse_status!='failed')
        """
    ):
        pair = (str(row["city"] or ""), str(row["target_date"] or ""))
        available_at = parse_available_at(row)
        if pair not in relevant_pairs or available_at is None:
            continue
        run_meta[int(row["id"])] = {
            "pair": pair,
            "available_at": available_at,
            "unit": str(row["unit"] or "C").upper(),
            "source": str(row["source"] or ""),
        }

    highs: dict[int, list[float]] = defaultdict(list)
    run_ids = sorted(run_meta)
    for start in range(0, len(run_ids), 400):
        chunk = run_ids[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "SELECT run_id,high_temp FROM forecast_members "
            f"WHERE run_id IN ({placeholders}) AND high_temp IS NOT NULL"
        )
        for row in conn.execute(query, chunk):
            run_id = int(row["run_id"])
            value = as_float(row["high_temp"])
            if value is None:
                continue
            if run_meta[run_id]["unit"] == "F":
                value = (value - 32.0) * 5.0 / 9.0
            highs[run_id].append(float(value))

    result: dict[tuple[str, str], list[MemberSpread]] = defaultdict(list)
    source_counts: Counter[str] = Counter()
    for run_id, meta in run_meta.items():
        values = highs.get(run_id, [])
        if len(values) < 30:
            continue
        result[meta["pair"]].append(
            MemberSpread(
                available_at=meta["available_at"],
                spread_c=sample_std(values),
                member_count=len(values),
                source=meta["source"],
            )
        )
        source_counts[meta["source"]] += 1
    for rows in result.values():
        rows.sort(key=lambda item: item.available_at)
    return result, {
        "eligible_runs": len(run_meta),
        "usable_runs": sum(source_counts.values()),
        **dict(source_counts),
    }


def latest_member_spread(
    lookup: dict[tuple[str, str], list[MemberSpread]],
    sample: dict[str, Any],
) -> MemberSpread | None:
    rows = lookup.get((sample["city_key"], sample["target_date"]), [])
    if not rows:
        return None
    timestamps = [row.available_at for row in rows]
    index = bisect.bisect_right(timestamps, sample["issued_at"])
    return rows[index - 1] if index > 0 else None


def load_peak_hours(
    conn: sqlite3.Connection,
    prediction_ids: Iterable[int],
) -> dict[int, float]:
    ids = sorted({int(value) for value in prediction_ids})
    result: dict[int, float] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT id,peak_hour FROM daily_max_predictions "
            f"WHERE id IN ({placeholders})",
            chunk,
        ):
            text = str(row["peak_hour"] or "").strip()
            if not text:
                continue
            try:
                hour_text, minute_text = (text.split(":") + ["0"])[:2]
                result[int(row["id"])] = int(hour_text) + int(minute_text) / 60.0
            except (TypeError, ValueError):
                continue
    return result


def enrich_samples(
    conn: sqlite3.Connection,
    samples: list[dict[str, Any]],
    member_lookup: dict[tuple[str, str], list[MemberSpread]],
    observations: dict[tuple[str, str], list[diagnostic.Observation]],
) -> Counter[str]:
    components = ladder.load_components(
        conn,
        (int(sample["prediction_id"]) for sample in samples),
    )
    peak_hours = load_peak_hours(
        conn,
        (int(sample["prediction_id"]) for sample in samples),
    )
    counters: Counter[str] = Counter()
    for sample in samples:
        sample["components"] = components.get(int(sample["prediction_id"]), {})
        sample["family_raw_mu"] = {
            family: component.raw_mu_c
            for family, component in sample["components"].items()
        }
        sample["ask_values"] = raw_asks(sample)
        spread = latest_member_spread(member_lookup, sample)
        sample["real_gfs_spread_c"] = spread.spread_c if spread else None
        sample["real_gfs_member_count"] = spread.member_count if spread else 0
        if spread:
            counters["real_member_spread"] += 1
            counters[f"real_member_source:{spread.source}"] += 1
        else:
            counters["family_spread_fallback"] += 1

        rows = observations.get((sample["city_key"], sample["target_date"]), [])
        timestamps = [row.observed_at for row in rows]
        stop = bisect.bisect_left(timestamps, sample["issued_at"])
        eligible = rows[:stop]
        sample["observed_floor_c"] = (
            max(row.temperature_c for row in eligible) if eligible else None
        )
        peak_hour = peak_hours.get(int(sample["prediction_id"]))
        sample["forecast_peak_hour"] = peak_hour
        if peak_hour is None:
            sample["hours_to_peak"] = None
        else:
            issued_hour = (
                sample["local_issued_at"].hour
                + sample["local_issued_at"].minute / 60.0
            )
            sample["hours_to_peak"] = max(0.0, min(24.0, peak_hour - issued_hour))
    return counters


def subset_raw(
    sample: dict[str, Any],
    subset: tuple[str, ...],
    *,
    use_real_spread: bool,
) -> tuple[float, float] | None:
    values = [
        float(sample["family_raw_mu"][family])
        for family in subset
        if family in sample["family_raw_mu"]
    ]
    minimum = 1 if len(subset) == 1 else 2
    if len(values) < minimum:
        return None
    mean_value = fmean(values)
    family_spread = sample_std(values)
    real_spread = sample.get("real_gfs_spread_c")
    if (
        use_real_spread
        and "gfs" in subset
        and real_spread is not None
        and float(real_spread) > 0
    ):
        return mean_value, float(real_spread)
    return mean_value, family_spread


def fit_emos_fields(
    prior: list[dict[str, Any]],
    mean_key: str,
    spread_key: str,
) -> tuple[float, float, float, float, int]:
    rows = [
        row
        for row in prior
        if row.get(mean_key) is not None and row.get(spread_key) is not None
    ]
    if len(rows) < MIN_TRAINING_ROWS:
        return 0.0, 1.0, ladder.DEFAULT_SIGMA_C, 0.0, len(rows)
    means = [float(row[mean_key]) for row in rows]
    spreads = [float(row[spread_key]) for row in rows]
    actual = [float(row["actual_c"]) for row in rows]
    a_value, b_value = ladder.ols(means, actual)
    fitted = [a_value + b_value * value for value in means]
    c_value, d_value = ladder.fit_nonnegative_sigma(
        actual,
        fitted,
        spreads,
    )
    return a_value, b_value, c_value, d_value, len(rows)


def solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    size = len(vector)
    augmented = [
        [float(value) for value in matrix[row]] + [float(vector[row])]
        for row in range(size)
    ]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-10:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                left - factor * right
                for left, right in zip(augmented[row], augmented[column])
            ]
    return [augmented[row][-1] for row in range(size)]


def ridge_prediction(
    training: list[dict[str, Any]],
    sample: dict[str, Any],
    mean_key: str,
) -> tuple[float | None, int]:
    rows = [
        row
        for row in training
        if lead_group(row["lead"]) == "D+0"
        and row.get(mean_key) is not None
        and row.get("observed_floor_c") is not None
        and row.get("hours_to_peak") is not None
    ]
    if len(rows) < 12:
        return None, len(rows)
    raw_features = [
        [
            float(row[mean_key]),
            float(row["observed_floor_c"]) - float(row[mean_key]),
            float(row["hours_to_peak"]),
        ]
        for row in rows
    ]
    target = [float(row["actual_c"]) for row in rows]
    centers = [fmean(values) for values in zip(*raw_features)]
    scales = [
        max(sample_std(values), 1e-6)
        for values in zip(*raw_features)
    ]
    design = [
        [1.0]
        + [
            (value - centers[index]) / scales[index]
            for index, value in enumerate(features)
        ]
        for features in raw_features
    ]
    width = len(design[0])
    matrix = [[0.0 for _ in range(width)] for _ in range(width)]
    vector = [0.0 for _ in range(width)]
    for x_values, y_value in zip(design, target):
        for left in range(width):
            vector[left] += x_values[left] * y_value
            for right in range(width):
                matrix[left][right] += x_values[left] * x_values[right]
    for index in range(1, width):
        matrix[index][index] += 1.0
    coefficients = solve_linear(matrix, vector)
    if coefficients is None:
        return None, len(rows)
    current = [
        float(sample[mean_key]),
        float(sample["observed_floor_c"]) - float(sample[mean_key]),
        float(sample["hours_to_peak"]),
    ]
    standardized = [1.0] + [
        (value - centers[index]) / scales[index]
        for index, value in enumerate(current)
    ]
    return (
        sum(value * coefficient for value, coefficient in zip(standardized, coefficients)),
        len(rows),
    )


def apply_variant(
    samples: list[dict[str, Any]],
    *,
    key: str,
    subset: tuple[str, ...],
    use_real_spread: bool,
    lead_stratified: bool,
    d0_information: bool,
) -> None:
    mean_key = f"_{key}_raw_mean"
    spread_key = f"_{key}_raw_spread"
    for sample in samples:
        raw = subset_raw(sample, subset, use_real_spread=use_real_spread)
        sample[mean_key] = raw[0] if raw else None
        sample[spread_key] = raw[1] if raw else None

    processed: list[dict[str, Any]] = []
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
        if sample.get(mean_key) is None:
            processed.append(sample)
            continue
        prior = ladder.strict_prior(processed, sample)
        if lead_stratified:
            prior = [
                row
                for row in prior
                if lead_group(row["lead"]) == lead_group(sample["lead"])
            ]
        a_value, b_value, c_value, d_value, train_n = fit_emos_fields(
            prior,
            mean_key,
            spread_key,
        )
        mu_c = a_value + b_value * float(sample[mean_key])
        info_train_n = 0
        if (
            d0_information
            and lead_group(sample["lead"]) == "D+0"
            and sample.get("observed_floor_c") is not None
            and sample.get("hours_to_peak") is not None
        ):
            information_mu, info_train_n = ridge_prediction(
                prior,
                sample,
                mean_key,
            )
            if information_mu is not None:
                mu_c = information_mu
        sigma_c = max(
            MIN_SIGMA_C,
            c_value + d_value * float(sample[spread_key]),
        )
        probabilities = diagnostic.gaussian_bucket_probs(
            mu_c,
            sigma_c,
            sample["buckets"],
        )
        if (
            d0_information
            and lead_group(sample["lead"]) == "D+0"
            and sample.get("observed_floor_c") is not None
        ):
            probabilities = base.counterfactual_probs(
                float(sample["observed_floor_c"]),
                sample["buckets"],
                probabilities,
            )
        sample[key] = probabilities
        sample[f"{key}_mu_c"] = mu_c
        sample[f"{key}_sigma_c"] = sigma_c
        sample[f"{key}_effective_spread_c"] = float(sample[spread_key])
        sample[f"{key}_train_n"] = train_n
        sample[f"{key}_info_train_n"] = info_train_n
        processed.append(sample)


def apply_e6(samples: list[dict[str, Any]]) -> Counter[str]:
    candidate_keys: dict[str, str] = {}
    for name, subset in SUBSETS.items():
        candidate_key = f"_e6_candidate_{name}"
        candidate_keys[name] = candidate_key
        apply_variant(
            samples,
            key=candidate_key,
            subset=subset,
            use_real_spread=True,
            lead_stratified=True,
            d0_information=True,
        )

    selections: Counter[str] = Counter()
    processed: list[dict[str, Any]] = []
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
        prior = [
            row
            for row in ladder.strict_prior(processed, sample)
            if lead_group(row["lead"]) == lead_group(sample["lead"])
        ]
        scored: list[tuple[float, str, int]] = []
        for name, candidate_key in candidate_keys.items():
            if sample.get(candidate_key) is None:
                continue
            history = [row for row in prior if row.get(candidate_key) is not None]
            if len(history) < MIN_TRAINING_ROWS:
                continue
            score = float(base.summarize(history, candidate_key)["brier"])
            scored.append((score, name, len(history)))
        if scored:
            _, selected_name, selection_n = min(scored)
        else:
            available = [
                name
                for name in ("core4", "all6", "v3_icon", "v3")
                if sample.get(candidate_keys[name]) is not None
            ]
            if not available:
                processed.append(sample)
                continue
            selected_name = available[0]
            selection_n = 0
        candidate_key = candidate_keys[selected_name]
        sample["e6_probs"] = list(sample[candidate_key])
        for suffix in (
            "mu_c",
            "sigma_c",
            "effective_spread_c",
            "train_n",
            "info_train_n",
        ):
            sample[f"e6_{suffix}"] = sample.get(f"{candidate_key}_{suffix}")
        sample["e6_subset"] = selected_name
        sample["e6_selection_n"] = selection_n
        selections[selected_name] += 1
        processed.append(sample)
    return selections


def prepare_variants(
    samples: list[dict[str, Any]],
    truths: dict[tuple[str, str], base.Truth],
) -> dict[str, Any]:
    ladder.apply_ladder(samples, truths)
    apply_variant(
        samples,
        key="e2_probs",
        subset=SUBSETS["all6"],
        use_real_spread=True,
        lead_stratified=False,
        d0_information=False,
    )
    apply_variant(
        samples,
        key="e3_probs",
        subset=SUBSETS["all6"],
        use_real_spread=True,
        lead_stratified=True,
        d0_information=False,
    )
    for name, subset in SUBSETS.items():
        apply_variant(
            samples,
            key=f"e4_{name}_probs",
            subset=subset,
            use_real_spread=False,
            lead_stratified=False,
            d0_information=False,
        )
    apply_variant(
        samples,
        key="e5_probs",
        subset=SUBSETS["all6"],
        use_real_spread=False,
        lead_stratified=False,
        d0_information=True,
    )
    selections = apply_e6(samples)
    return {"e6_selections": selections}


def custom_checkpoint_samples(
    snapshots: list[base.Snapshot],
    shared: dict[str, Any],
    checkpoint_hour: int,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    previous = base.CHECKPOINTS
    base.CHECKPOINTS = {
        f"D+0@{checkpoint_hour:02d}": (0, time(checkpoint_hour, 0))
    }
    try:
        samples, exclusions = diagnostic.materialize_samples(
            snapshots,
            shared["stations"],
            shared["truths"],
            shared["maturity"],
            shared["predictions"],
        )
        attach_ask_sums(samples, snapshots, shared["stations"])
        return samples, exclusions
    finally:
        base.CHECKPOINTS = previous


def quartile_labels(
    samples: list[dict[str, Any]],
    value_fn: Any,
) -> tuple[dict[int, str], list[float]]:
    values = sorted(float(value_fn(sample)) for sample in samples)
    if not values:
        return {}, []
    cuts = [
        float(diagnostic.percentile(values, probability) or 0.0)
        for probability in (0.25, 0.50, 0.75)
    ]
    labels: dict[int, str] = {}
    for sample in samples:
        value = float(value_fn(sample))
        if value <= cuts[0]:
            label = "Q1"
        elif value <= cuts[1]:
            label = "Q2"
        elif value <= cuts[2]:
            label = "Q3"
        else:
            label = "Q4"
        labels[id(sample)] = label
    return labels, cuts


def condition_rows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    e6_rows = [row for row in samples if row.get("e6_probs") is not None]
    spread_rows = [
        row for row in e6_rows if row.get("e6_effective_spread_c") is not None
    ]
    spread_labels, spread_cuts = quartile_labels(
        spread_rows,
        lambda row: row["e6_effective_spread_c"],
    )

    def disagreement(row: dict[str, Any]) -> float:
        top_index = max(
            range(len(row["e6_probs"])),
            key=lambda index: row["e6_probs"][index],
        )
        return abs(
            float(row["e6_probs"][top_index]) - float(row["ask_values"][top_index])
        )

    disagreement_labels, disagreement_cuts = quartile_labels(
        e6_rows,
        disagreement,
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in e6_rows:
        if id(row) in spread_labels:
            groups[("ensemble spread", spread_labels[id(row)])].append(row)
        groups[("lead", lead_group(row["lead"]))].append(row)
        groups[("model-market disagreement", disagreement_labels[id(row)])].append(row)
        market_top_ask = max(float(value) for value in row["ask_values"])
        if market_top_ask < 0.30:
            confidence = "<0.30"
        elif market_top_ask <= 0.50:
            confidence = "0.30-0.50"
        else:
            confidence = ">0.50"
        groups[("market top ask", confidence)].append(row)
        maturity = int(row.get("maturity_count") or 0)
        if maturity < 5:
            band = "<5"
        elif maturity <= 10:
            band = "5-10"
        else:
            band = ">10"
        groups[("prior paired days", band)].append(row)

    result: list[dict[str, Any]] = []
    for (dimension, cell), rows in sorted(groups.items()):
        model = base.summarize(rows, "e6_probs")
        market = base.summarize(rows, "market_probs")
        result.append(
            {
                "dimension": dimension,
                "cell": cell,
                "rows": rows,
                "n": len(rows),
                "model_brier": float(model["brier"]),
                "market_brier": float(market["brier"]),
                "difference": float(model["brier"]) - float(market["brier"]),
                "model_top1": float(model["top1"]),
                "market_top1": float(market["top1"]),
                "cuts": (
                    spread_cuts
                    if dimension == "ensemble spread"
                    else disagreement_cuts
                    if dimension == "model-market disagreement"
                    else []
                ),
            }
        )
    return result


def agreement_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    triggered: list[tuple[dict[str, Any], int, int]] = []
    for sample in samples:
        counts: Counter[int] = Counter()
        for value in sample.get("family_raw_mu", {}).values():
            matches = [
                index
                for index, bucket in enumerate(sample["buckets"])
                if base.bucket_contains(float(value), bucket)
            ]
            if len(matches) == 1:
                counts[matches[0]] += 1
        if not counts:
            continue
        bucket_index, count = counts.most_common(1)[0]
        if count >= 3:
            triggered.append((sample, bucket_index, count))
    asks = [
        float(sample["ask_values"][bucket_index])
        for sample, bucket_index, _ in triggered
    ]
    outcomes = [
        1.0 if int(sample["actual_index"]) == bucket_index else 0.0
        for sample, bucket_index, _ in triggered
    ]
    hit_rate = fmean(outcomes) if outcomes else None
    return {
        "n": len(triggered),
        "hit_rate": hit_rate,
        "median_ask": median(asks) if asks else None,
        "mean_ask": fmean(asks) if asks else None,
        "hit_minus_median_ask": (
            hit_rate - median(asks) if asks and hit_rate is not None else None
        ),
        "mean_unit_profit": (
            fmean(outcome - ask for outcome, ask in zip(outcomes, asks))
            if asks
            else None
        ),
        "mean_model_count": (
            fmean(count for _, _, count in triggered) if triggered else None
        ),
    }


def ask_bin(value: float) -> str:
    if value < 0.05:
        return "<0.05"
    if value < 0.10:
        return "0.05-0.10"
    if value < 0.20:
        return "0.10-0.20"
    if value <= 0.40:
        return "0.20-0.40"
    return ">0.40"


def make_trades(
    samples: list[dict[str, Any]],
    probability_key: str,
    edge_threshold: float,
    *,
    ask_filter: str | None = None,
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for sample in samples:
        probabilities = sample.get(probability_key)
        if probabilities is None:
            continue
        for index, (probability, ask) in enumerate(
            zip(probabilities, sample["ask_values"])
        ):
            ask_value = float(ask)
            if ask_filter is not None and ask_bin(ask_value) != ask_filter:
                continue
            edge = float(probability) - ask_value
            if edge + 1e-12 < edge_threshold:
                continue
            payout = 1.0 if int(sample["actual_index"]) == index else 0.0
            trades.append(
                {
                    "cluster": (
                        sample["city_key"],
                        sample["target_date"],
                        str(sample["lead"]),
                    ),
                    "cost": ask_value,
                    "payout": payout,
                    "profit": payout - ask_value,
                    "edge": edge,
                }
            )
    return trades


def bootstrap_roi(
    trades: list[dict[str, Any]],
    *,
    seed: int,
) -> tuple[float | None, float | None]:
    by_cluster: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_cluster[trade["cluster"]].append(trade)
    clusters = list(by_cluster)
    if not clusters:
        return None, None
    randomizer = random.Random(seed)
    rois: list[float] = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = [randomizer.choice(clusters) for _ in clusters]
        cost = sum(
            trade["cost"]
            for cluster in sampled
            for trade in by_cluster[cluster]
        )
        profit = sum(
            trade["profit"]
            for cluster in sampled
            for trade in by_cluster[cluster]
        )
        if cost > 0:
            rois.append(profit / cost)
    return (
        diagnostic.percentile(rois, 0.025),
        diagnostic.percentile(rois, 0.975),
    )


def pnl_row(
    samples: list[dict[str, Any]],
    probability_key: str,
    edge_threshold: float,
    *,
    ask_filter: str | None = None,
    seed: int = 20260726,
) -> dict[str, Any]:
    trades = make_trades(
        samples,
        probability_key,
        edge_threshold,
        ask_filter=ask_filter,
    )
    cost = sum(float(row["cost"]) for row in trades)
    payout = sum(float(row["payout"]) for row in trades)
    low, high = bootstrap_roi(trades, seed=seed)
    return {
        "threshold": edge_threshold,
        "ask_bin": ask_filter or "all",
        "trades": len(trades),
        "hit_rate": (
            fmean(float(row["payout"]) for row in trades) if trades else None
        ),
        "cost": cost,
        "payout": payout,
        "roi": (payout - cost) / cost if cost > 0 else None,
        "ci_low": low,
        "ci_high": high,
    }


def format_number(value: Any, digits: int = 4) -> str:
    if value is None or not math.isfinite(float(value)):
        return "--"
    return f"{float(value):.{digits}f}"


def format_percent(value: Any, digits: int = 2) -> str:
    if value is None or not math.isfinite(float(value)):
        return "--"
    return f"{float(value) * 100:.{digits}f}%"


def variant_table(
    samples: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    definitions = [
        ("E1", "B8 EMOS baseline", "b8_probs"),
        ("E2", "real GFS member spread", "e2_probs"),
        ("E3", "lead-stratified EMOS", "e3_probs"),
        ("E4a", "all six", "e4_all6_probs"),
        ("E4b", "without GEM/JMA", "e4_core4_probs"),
        ("E4c", "V3 + ICON", "e4_v3_icon_probs"),
        ("E4d", "V3 only", "e4_v3_probs"),
        ("E5", "D+0 intraday information", "e5_probs"),
        ("E6", "walk-forward expert combination", "e6_probs"),
    ]
    rows: list[dict[str, Any]] = []
    bundles: dict[str, dict[str, Any]] = {}
    for code, label, key in definitions:
        bundle = metric_bundle(samples, key)
        bundles[code] = bundle
        rows.append(
            {
                "code": code,
                "label": label,
                "key": key,
                **bundle,
            }
        )
    return rows, bundles


def render_report(
    *,
    db_path: Path,
    samples: list[dict[str, Any]],
    variant_rows: list[dict[str, Any]],
    variant_bundles: dict[str, dict[str, Any]],
    member_stats: dict[str, int],
    enrichment_counts: Counter[str],
    e6_selections: Counter[str],
    checkpoint_rows: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    agreement: dict[str, Any],
    pnl_e6: list[dict[str, Any]],
    pnl_e6_bins: list[dict[str, Any]],
    pnl_b4: list[dict[str, Any]],
    best_condition: dict[str, Any] | None,
    pnl_condition: list[dict[str, Any]],
) -> str:
    e1 = variant_bundles["E1"]
    e2 = variant_bundles["E2"]
    e4_rows = [row for row in variant_rows if row["code"].startswith("E4")]
    best_e4 = min(
        e4_rows,
        key=lambda row: float(row["model"]["brier"])
        if row["model"]["brier"] is not None
        else math.inf,
    )
    e6 = variant_bundles["E6"]
    e6_resolution = float(e6["model_decomp"]["resolution"])
    e6_market_resolution = float(e6["market_decomp"]["resolution"])
    real_member_n = int(enrichment_counts.get("real_member_spread", 0))
    e2_resolution_lift = (
        float(e2["model_decomp"]["resolution"])
        - float(e1["model_decomp"]["resolution"])
    )
    qualifying_conditions = [
        row
        for row in conditions
        if row["n"] >= 25 and row["difference"] < 0
    ]
    positive_pnl = [
        row
        for row in pnl_e6 + pnl_e6_bins + pnl_condition
        if row["ci_low"] is not None and float(row["ci_low"]) > 0
    ]
    checkpoint_08 = next(
        (row for row in checkpoint_rows if row["hour"] == 8),
        None,
    )
    checkpoint_14 = next(
        (row for row in checkpoint_rows if row["hour"] == 14),
        None,
    )
    model_resolution_gain = (
        float(checkpoint_14["model_resolution"])
        - float(checkpoint_08["model_resolution"])
        if checkpoint_08 and checkpoint_14
        else None
    )
    market_resolution_gain = (
        float(checkpoint_14["market_resolution"])
        - float(checkpoint_08["market_resolution"])
        if checkpoint_08 and checkpoint_14
        else None
    )
    improves_faster = (
        model_resolution_gain is not None
        and market_resolution_gain is not None
        and model_resolution_gain > market_resolution_gain
    )
    decision = (
        "PRODUCTIONIZE_CANDIDATE"
        if positive_pnl
        else "STOP_TRADING_MODEL_NO_PROVEN_EDGE"
    )

    lines = [
        "# EMOS v2 与 P&L 最终只读诊断",
        "",
        f"- 生成日期：`{date.today().isoformat()}`",
        f"- 只读数据库：`{db_path}`",
        f"- 基础宽口径样本：`N={len(samples)}`",
        "- 无泄漏边界：每个评估样本的拟合记录均要求 "
        "`training.target_date < evaluation.local_issued_date` 且 "
        "`training.target_date < evaluation.target_date`；真实成员与实况还要求 "
        "`available_at/observed_at < issued_at`。",
        "- P&L 是必要条件测试：每个合格桶按 ask 买 1 份并持有到结算，"
        "未计滑点、深度、费用；95% CI 按 city/date/lead 聚类 bootstrap 5,000 次。",
        f"- 最终决策：**{decision}**",
        "",
        "## A. EMOS v2 阶梯",
        "",
        "| 变体 | 说明 | N | Brier | Log loss | Top-1 | Top-2 | Reliability | Resolution | vs 同批市场 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in variant_rows:
        metrics = row["model"]
        decomp = row["model_decomp"]
        lines.append(
            f"| {row['code']} | {row['label']} | {metrics['n']} | "
            f"{format_number(metrics['brier'])} | "
            f"{format_number(metrics['log_loss'])} | "
            f"{format_percent(metrics['top1'])} | "
            f"{format_percent(metrics['top2'])} | "
            f"{format_number(decomp['reliability'])} | "
            f"{format_number(decomp['resolution'])} | "
            f"{format_number(row['vs_market'], 4)} |"
        )
    lines += [
        "",
        "### A 部分判定",
        "",
        f"- **A1**：完整 31 成员 spread 在 `{real_member_n}/{len(samples)}` "
        f"个样本可用；E2 相对 E1 的 resolution 变化为 "
        f"`{e2_resolution_lift:+.4f}`。可用 run 统计：`{member_stats}`。",
        f"- **A2**：E4 最佳子集为 **{best_e4['label']}**，Brier "
        f"`{format_number(best_e4['model']['brier'])}`；剔除 GEM/JMA "
        f"{'优于' if float(variant_bundles['E4b']['model']['brier']) < float(variant_bundles['E4a']['model']['brier']) else '未优于'}"
        "全六模型。",
        f"- **A3**：E6 resolution `{e6_resolution:.4f}`，同批市场 "
        f"`{e6_market_resolution:.4f}`，差 `{e6_market_resolution - e6_resolution:+.4f}`；"
        f"相对指定市场参考 `0.2173` 还差 `{0.2173 - e6_resolution:+.4f}`。",
        f"- E6 严格走查模型选择分布：`{dict(e6_selections)}`。没有用当前样本真值"
        "选择子集；训练不足时预注册回退顺序为 core4 → all6 → V3+ICON → V3。",
        "",
        "## B. D+0 截面时刻敏感性",
        "",
        "| 本地时刻 | N | E6 Brier | E6 resolution | 市场 Brier | 市场 resolution | 实况 floor 覆盖 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in checkpoint_rows:
        lines.append(
            f"| {row['hour']:02d}:00 | {row['n']} | "
            f"{format_number(row['model_brier'])} | "
            f"{format_number(row['model_resolution'])} | "
            f"{format_number(row['market_brier'])} | "
            f"{format_number(row['market_resolution'])} | "
            f"{format_percent(row['floor_share'])} |"
        )
    first_exceed = next(
        (
            row
            for row in checkpoint_rows
            if row["model_resolution"] > row["market_resolution"]
        ),
        None,
    )
    lines += [
        "",
        f"- **B1**：08:00→14:00 模型 resolution 变化 "
        f"`{format_number(model_resolution_gain)}`；"
        + (
            f"首次超过同批市场发生在 `{first_exceed['hour']:02d}:00`。"
            if first_exceed
            else "四个截面均未超过同批市场。"
        ),
        f"- **B2**：同期市场 resolution 变化 "
        f"`{format_number(market_resolution_gain)}`；模型"
        f"{'提升更快' if improves_faster else '未比市场提升更快'}。"
        "市场 Brier 已在上表逐时同批对照。",
        "",
        "## C. 条件性优势",
        "",
        "| 维度 | 分箱 | N | 模型 Brier | 市场 Brier | 差值 | 模型 Top-1 | 市场 Top-1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in conditions:
        lines.append(
            f"| {row['dimension']} | {row['cell']} | {row['n']} | "
            f"{row['model_brier']:.4f} | {row['market_brier']:.4f} | "
            f"{row['difference']:+.4f} | {format_percent(row['model_top1'])} | "
            f"{format_percent(row['market_top1'])} |"
        )
    lines += [
        "",
        "### C 部分判定",
        "",
    ]
    if qualifying_conditions:
        lines.append("- **C1**：存在以下 `N>=25` 且模型 Brier 低于市场的格子：")
        for row in qualifying_conditions:
            lines.append(
                f"  - `{row['dimension']} / {row['cell']}`：N={row['n']}，"
                f"差 `{row['difference']:+.4f}`。"
            )
    else:
        lines.append("- **C1**：不存在 `N>=25` 且模型 Brier 低于市场的格子。")
    lines += [
        f"- **C2**：>=3 个模型落同一桶触发 `{agreement['n']}` 次；"
        f"实际命中率 `{format_percent(agreement['hit_rate'])}`，该桶 ask 中位数 "
        f"`{format_percent(agreement['median_ask'])}`，命中率减 ask 中位数 "
        f"`{format_percent(agreement['hit_minus_median_ask'])}`；按每笔真实 ask "
        f"计算的平均单位利润 `{format_number(agreement['mean_unit_profit'])}`。",
        "",
        "## D. 直接 P&L 回测",
        "",
        "### D1. E6 全 ask",
        "",
        "| edge 阈值 | 交易数 | 命中率 | 总投入 | 总回报 | ROI | ROI 95% CI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pnl_e6:
        lines.append(
            f"| {row['threshold']:.2f} | {row['trades']} | "
            f"{format_percent(row['hit_rate'])} | {row['cost']:.4f} | "
            f"{row['payout']:.4f} | {format_percent(row['roi'])} | "
            f"[{format_percent(row['ci_low'])}, {format_percent(row['ci_high'])}] |"
        )
    lines += [
        "",
        "### D2. E6 按 ask 分箱",
        "",
        "| edge | ask 区间 | 交易数 | 命中率 | 投入 | 回报 | ROI | 95% CI |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pnl_e6_bins:
        lines.append(
            f"| {row['threshold']:.2f} | {row['ask_bin']} | {row['trades']} | "
            f"{format_percent(row['hit_rate'])} | {row['cost']:.4f} | "
            f"{row['payout']:.4f} | {format_percent(row['roi'])} | "
            f"[{format_percent(row['ci_low'])}, {format_percent(row['ci_high'])}] |"
        )
    lines += [
        "",
        "### D3. 最佳条件子集",
        "",
    ]
    if best_condition is None:
        lines.append("没有 `N>=25` 且模型 Brier 优于市场的条件子集，因此无可回测子集。")
    else:
        lines += [
            f"子集：`{best_condition['dimension']} / {best_condition['cell']}`，"
            f"N={best_condition['n']}，Brier 差 `{best_condition['difference']:+.4f}`。",
            "",
            "| edge | 交易数 | 命中率 | 投入 | 回报 | ROI | 95% CI |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for row in pnl_condition:
            lines.append(
                f"| {row['threshold']:.2f} | {row['trades']} | "
                f"{format_percent(row['hit_rate'])} | {row['cost']:.4f} | "
                f"{row['payout']:.4f} | {format_percent(row['roi'])} | "
                f"[{format_percent(row['ci_low'])}, {format_percent(row['ci_high'])}] |"
            )
    lines += [
        "",
        "### D4. 当前生产概率 B4 对照",
        "",
        "| edge 阈值 | 交易数 | 命中率 | 总投入 | 总回报 | ROI | ROI 95% CI |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pnl_b4:
        lines.append(
            f"| {row['threshold']:.2f} | {row['trades']} | "
            f"{format_percent(row['hit_rate'])} | {row['cost']:.4f} | "
            f"{row['payout']:.4f} | {format_percent(row['roi'])} | "
            f"[{format_percent(row['ci_low'])}, {format_percent(row['ci_high'])}] |"
        )
    cheap_rows = [
        row
        for row in pnl_e6_bins
        if row["ask_bin"] in {"<0.05", "0.05-0.10"}
    ]
    cheap_08 = [
        row for row in cheap_rows if abs(row["threshold"] - 0.08) < 1e-9
    ]
    lines += [
        "",
        "### D 部分判定",
        "",
        f"- **D1**："
        + (
            f"存在 bootstrap 下界 > 0 的配置，共 `{len(positive_pnl)}` 个；"
            + ", ".join(
                f"edge={row['threshold']:.2f}, ask={row['ask_bin']}"
                for row in positive_pnl
            )
            + "。"
            if positive_pnl
            else "不存在 bootstrap 下界 > 0 的配置。"
        ),
        "- **D2**：当前被生产闸门排除的 ask<0.10 在 edge=0.08 时："
        + (
            "; ".join(
                f"{row['ask_bin']} ROI={format_percent(row['roi'])}, "
                f"CI=[{format_percent(row['ci_low'])},{format_percent(row['ci_high'])}]"
                for row in cheap_08
            )
            if cheap_08
            else "没有触发交易。"
        ),
        "- **D3**：最佳条件子集的 P&L 已在 D3 表列出；若没有满足条件的子集，"
        "不以更小样本制造 alpha。",
        "- **D4**：当前生产概率 B4 的同规则 ROI 已在 D4 表列出，可直接量化 "
        "E6 相对现状的 P&L 变化。",
        "",
        "## 必答四句话",
        "",
        f"1. E6 最佳无泄漏组合的 Brier 为 "
        f"`{format_number(e6['model']['brier'])}`、resolution 为 "
        f"`{e6_resolution:.4f}`；同批市场 Brier "
        f"`{format_number(e6['market']['brier'])}`、resolution "
        f"`{e6_market_resolution:.4f}`，Brier 还差 "
        f"`{float(e6['model']['brier']) - float(e6['market']['brier']):+.4f}`。",
        f"2. D+0 推迟到峰值前，模型"
        f"{'比市场提升更快' if improves_faster else '没有比市场提升更快'}；"
        f"模型 resolution 变化 `{format_number(model_resolution_gain)}`，"
        f"市场变化 `{format_number(market_resolution_gain)}`。",
        "3. "
        + (
            "存在 N>=25 且模型 Brier 低于市场的条件子集："
            + "、".join(
                f"{row['dimension']}/{row['cell']} (N={row['n']})"
                for row in qualifying_conditions
            )
            + "。"
            if qualifying_conditions
            else "不存在 N>=25 且模型 Brier 低于市场的条件子集。"
        ),
        "4. "
        + (
            "P&L 回测存在 bootstrap 下界为正的配置："
            + "、".join(
                f"edge {row['threshold']:.2f} / ask {row['ask_bin']}"
                for row in positive_pnl
            )
            + "。"
            if positive_pnl
            else "P&L 回测不存在 bootstrap 下界为正的配置。"
        ),
        "",
        "## 生产化或止损决定",
        "",
    ]
    if decision == "PRODUCTIONIZE_CANDIDATE":
        lines += [
            "**结论：仅允许进入受控 paper 生产候选，不解锁 live。**",
            "",
            "理由：至少一个预先定义阈值/ask 或条件子集的聚类 bootstrap 下界为正；"
            "但该回测未计成交量、滑点与费用，仍只证明必要条件。",
        ]
    else:
        lines += [
            "**结论：停止把当前模型当作交易 alpha 继续生产化。**",
            "",
            "理由：EMOS v2、真实成员 spread、提前量分层、模型子集和可部署日内信息"
            "均已纳入，但没有配置的 bootstrap ROI 下界为正。系统可以继续作为天气/市场"
            "研究看板与数据采集器，但不应再以“再修一个 gate”推动自动交易。",
        ]
    lines += [
        "",
        "## 待办观察（本轮不修）",
        "",
        "- 当前生产 DEB 的过度自信（既有结论：log loss 4.2627）仍是生产缺陷；"
        "本轮没有修改。",
        "- P&L 未建模盘口深度、滑点、费用与成交失败，因此任何正结果都不能直接外推实盘。",
        "- 条件分箱和 E4 多模型子集属于多重比较；报告只把 N>=25 且 bootstrap "
        "下界为正视为继续验证依据。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    conn = base.open_read_only(args.db)
    try:
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
        snapshots, snapshot_exclusions = diagnostic.valid_snapshots(
            groups,
            truths,
            stations,
            bucket_events,
            bucket_lookup,
            relaxed=True,
        )
        samples, sample_exclusions = diagnostic.materialize_samples(
            snapshots,
            stations,
            truths,
            maturity,
            predictions,
        )
        attach_ask_sums(samples, snapshots, stations)
        relevant_pairs = {
            (sample["city_key"], sample["target_date"]) for sample in samples
        }
        member_lookup, member_stats = load_gfs_member_spreads(
            conn,
            relevant_pairs,
        )
        observations = diagnostic.load_observations(conn, stations)
        enrichment_counts = enrich_samples(
            conn,
            samples,
            member_lookup,
            observations,
        )
        variant_meta = prepare_variants(samples, truths)
        variant_rows, variant_bundles = variant_table(samples)

        checkpoint_rows: list[dict[str, Any]] = []
        for hour in (8, 10, 12, 14):
            checkpoint_samples, _ = custom_checkpoint_samples(
                snapshots,
                shared,
                hour,
            )
            enrich_samples(
                conn,
                checkpoint_samples,
                member_lookup,
                observations,
            )
            apply_e6(checkpoint_samples)
            bundle = metric_bundle(checkpoint_samples, "e6_probs")
            rows = bundle["rows"]
            checkpoint_rows.append(
                {
                    "hour": hour,
                    "n": len(rows),
                    "model_brier": bundle["model"]["brier"],
                    "model_resolution": bundle["model_decomp"]["resolution"],
                    "market_brier": bundle["market"]["brier"],
                    "market_resolution": bundle["market_decomp"]["resolution"],
                    "floor_share": (
                        sum(
                            row.get("observed_floor_c") is not None
                            for row in rows
                        )
                        / len(rows)
                        if rows
                        else 0.0
                    ),
                }
            )

        conditions = condition_rows(samples)
        qualifying = [
            row
            for row in conditions
            if row["n"] >= 25 and row["difference"] < 0
        ]
        best_condition = (
            min(qualifying, key=lambda row: row["difference"])
            if qualifying
            else None
        )
        agreement = agreement_stats(samples)
        e6_rows = variant_bundles["E6"]["rows"]
        pnl_e6 = [
            pnl_row(e6_rows, "e6_probs", threshold, seed=20260726 + index)
            for index, threshold in enumerate(EDGE_THRESHOLDS)
        ]
        pnl_e6_bins = [
            pnl_row(
                e6_rows,
                "e6_probs",
                threshold,
                ask_filter=bin_name,
                seed=20260726 + threshold_index * 10 + bin_index,
            )
            for threshold_index, threshold in enumerate(EDGE_THRESHOLDS)
            for bin_index, bin_name in enumerate(
                ("<0.05", "0.05-0.10", "0.10-0.20", "0.20-0.40", ">0.40")
            )
        ]
        pnl_b4 = [
            pnl_row(e6_rows, "model_probs", threshold, seed=20260826 + index)
            for index, threshold in enumerate(EDGE_THRESHOLDS)
        ]
        pnl_condition = (
            [
                pnl_row(
                    best_condition["rows"],
                    "e6_probs",
                    threshold,
                    seed=20260926 + index,
                )
                for index, threshold in enumerate(EDGE_THRESHOLDS)
            ]
            if best_condition
            else []
        )
        report = render_report(
            db_path=args.db.resolve(),
            samples=samples,
            variant_rows=variant_rows,
            variant_bundles=variant_bundles,
            member_stats=member_stats,
            enrichment_counts=enrichment_counts,
            e6_selections=variant_meta["e6_selections"],
            checkpoint_rows=checkpoint_rows,
            conditions=conditions,
            agreement=agreement,
            pnl_e6=pnl_e6,
            pnl_e6_bins=pnl_e6_bins,
            pnl_b4=pnl_b4,
            best_condition=best_condition,
            pnl_condition=pnl_condition,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "samples": len(samples),
                    "snapshot_exclusions": dict(snapshot_exclusions),
                    "sample_exclusions": dict(sample_exclusions),
                    "member_stats": member_stats,
                    "enrichment": dict(enrichment_counts),
                    "e6_selections": dict(variant_meta["e6_selections"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
