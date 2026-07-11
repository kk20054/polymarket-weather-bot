from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weatherbot_v3.bias import train_bias_table
from weatherbot_v3.config import ROOT, load_config
from weatherbot_v3.deb import build_daily_max_prediction
from weatherbot_v3.hourly import hourly_consensus_summary
from weatherbot_v3.registry import get_city_profile


DEFAULT_BENCHMARK_DIR = ROOT / "audits" / "polywx-benchmark-2026-07-05"
DEFAULT_COMPACT = ROOT / "audits" / "polywx-source-alignment-2026-07-07" / "polywx-firecrawl-compact.json"
DISCLAIMER = "Audit-only replay. PolyWX values are never written to WeatherBot runtime or trading tables."


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare saved PolyWX evidence with leakage-safe WeatherBot replay output.")
    parser.add_argument("--output-dir", default=f"audits/polywx-replay-{datetime.now().date().isoformat()}")
    parser.add_argument("--db", default="")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db) if args.db else load_config().v3_db_path

    cases = load_cases(DEFAULT_BENCHMARK_DIR, DEFAULT_COMPACT)
    results = [replay_case(case, db_path=db_path) for case in cases]
    payload = {
        "version": "polywx-leakage-safe-replay-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": DISCLAIMER,
        "db_path": str(db_path),
        "results": results,
    }
    (output_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "README.md").write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "output_dir": str(output_dir),
        "cases": len(results),
        "results": [compact_result(row) for row in results],
    }, ensure_ascii=False, indent=2))


def load_cases(benchmark_dir: Path, compact_path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for target_date in ("2026-07-02", "2026-07-04"):
        prefix = benchmark_dir / f"chicago-kord-{target_date}"
        prediction = _read_json(prefix.with_name(f"{prefix.name}-prediction.json"), {})
        cases.append({
            "city": "chicago",
            "city_slug": "chicago-kord",
            "target_date": target_date,
            "captured_at": str(prediction.get("generated_at") or ""),
            "forecast_unit": "F",
            "forecast": _read_json(prefix.with_name(f"{prefix.name}-forecast.json"), []),
            "prediction": prediction,
            "peak_marker": _read_json(prefix.with_name(f"{prefix.name}-peak-marker.json"), {}),
            "evidence": "saved_polywx_api_json",
        })

    compact = _read_json(compact_path, {})
    page = ((compact.get("pages") or {}).get("shanghai:2026-07-06") or {})
    cases.append({
        "city": "shanghai",
        "city_slug": "shanghai-zspd",
        "target_date": "2026-07-06",
        "captured_at": str(compact.get("captured_at") or ""),
        "forecast_unit": "C",
        "forecast": [
            {"hour": f"{hour:02d}:00", "temperature_c": temp, "cloud_cover_pct": cloud}
            for hour, (temp, cloud) in enumerate(zip(page.get("forecast") or [], page.get("cloud_pct") or []))
        ],
        "prediction": page.get("daily_max_prediction") or {},
        "peak_marker": {},
        "evidence": "saved_firecrawl_compact",
    })
    return cases


def replay_case(case: dict[str, Any], *, db_path: Path) -> dict[str, Any]:
    city = str(case["city"])
    target_date = str(case["target_date"])
    profile = get_city_profile(city)
    if profile is None:
        return {**case, "ok": False, "error": "unknown_city"}
    issued_at = str(case.get("captured_at") or "") or f"{target_date}T23:59:59+00:00"
    bias_payload = train_bias_table(
        cities=[city],
        days=90,
        path=db_path,
        as_of_date_exclusive=target_date,
        persist=False,
    )
    prediction = build_daily_max_prediction(
        city,
        target_date,
        issued_at=issued_at,
        path=db_path,
        bias_table=list(bias_payload.get("rows") or []),
    )
    hourly = hourly_consensus_summary(city, target_date, db_path=db_path)
    local_by_hour = {str(row.get("local_hour") or ""): row for row in hourly.get("points") or []}
    polywx_rows = normalize_polywx_forecast(case.get("forecast") or [])
    series_rows = []
    for source_row in polywx_rows:
        hour = str(source_row.get("hour") or "")
        local = local_by_hour.get(hour) or {}
        polywx_temp = convert_temperature(
            source_row.get("temperature_c"),
            str(case.get("forecast_unit") or "C"),
            profile.unit,
        )
        local_temp = _number(local.get("best"))
        polywx_cloud = _number(source_row.get("cloud_cover_pct"))
        local_cloud = _number(local.get("forecast_cloud_cover"))
        series_rows.append({
            "hour": hour,
            "polywx_forecast": polywx_temp,
            "weatherbot_forecast": local_temp,
            "forecast_delta": _delta(local_temp, polywx_temp),
            "polywx_cloud_pct": polywx_cloud,
            "weatherbot_cloud_pct": local_cloud,
            "cloud_delta_pct": _delta(local_cloud, polywx_cloud),
        })

    polywx_prediction = case.get("prediction") or {}
    polywx_mu = convert_temperature(_number(polywx_prediction.get("mu")), "C", profile.unit)
    polywx_sigma = convert_delta(_number(polywx_prediction.get("sigma")), "C", profile.unit)
    local_mu = _number(prediction.get("mu")) if prediction.get("ok") else None
    local_sigma = _number(prediction.get("sigma")) if prediction.get("ok") else None
    polywx_peak = str((case.get("peak_marker") or {}).get("localTime") or "")[:5]
    local_peak = str(prediction.get("peak_hour") or "")[:5] if prediction.get("ok") else ""
    return {
        "ok": bool(prediction.get("ok")),
        "city": city,
        "city_slug": case.get("city_slug"),
        "target_date": target_date,
        "unit": profile.unit,
        "captured_at": issued_at,
        "evidence": case.get("evidence"),
        "hourly_rows": len(series_rows),
        "forecast_pairs": _paired_count(series_rows, "weatherbot_forecast", "polywx_forecast"),
        "forecast_mae": _mae(series_rows, "forecast_delta"),
        "cloud_pairs": _paired_count(series_rows, "weatherbot_cloud_pct", "polywx_cloud_pct"),
        "cloud_mae_pct": _mae(series_rows, "cloud_delta_pct"),
        "polywx_mu": polywx_mu,
        "weatherbot_mu": local_mu,
        "mu_delta": _delta(local_mu, polywx_mu),
        "polywx_sigma": polywx_sigma,
        "weatherbot_sigma": local_sigma,
        "sigma_delta": _delta(local_sigma, polywx_sigma),
        "polywx_peak_hour": polywx_peak,
        "weatherbot_peak_hour": local_peak,
        "peak_match": None if not polywx_peak else polywx_peak == local_peak,
        "weatherbot_method": prediction.get("method") or prediction.get("algo") or "",
        "weatherbot_observed_floor": _number(prediction.get("observed_floor")),
        "weatherbot_mu_floor_applied": bool(prediction.get("mu_observed_floor_applied")),
        "weatherbot_model_weights": prediction.get("model_weights") or {},
        "weatherbot_bias_rows": sum(1 for row in bias_payload.get("rows") or [] if row.get("runtime_eligible")),
        "weatherbot_bias_samples": {
            str(row.get("model")): int(row.get("sample_count") or 0)
            for row in bias_payload.get("rows") or []
        },
        "weatherbot_warnings": prediction.get("build_warnings") or prediction.get("reasons") or [],
        "series": series_rows,
        "root_causes": {
            "forecast": "data_source: PolyWX saved forecast feed versus WeatherBot hourly consensus/Open-Meteo archive",
            "cloud": "data_source: PolyWX saved cloud feed versus WeatherBot model cloud_cover",
            "deb": "computation+model_input: independently weighted ensembles and walk-forward bias",
            "peak": "computation+observation_source: mixed curve and available observation cadence",
        },
        "polywx_schema_warning": (
            "PolyWX US forecast payload labels temperature as temperature_c but stores city display unit F; "
            "pressure_hpa similarly contains inHg-scale values. Replay uses the city unit contract, not field-name inference."
            if str(case.get("forecast_unit") or "C") == "F" else ""
        ),
    }


def normalize_polywx_forecast(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, row in enumerate(rows):
        hour = str(row.get("hour") or f"{index:02d}:00")
        if hour.isdigit():
            hour = f"{int(hour):02d}:00"
        elif len(hour) >= 2 and ":" not in hour:
            hour = f"{hour[:2]}:00"
        normalized.append({
            "hour": hour[:5],
            "temperature_c": _number(row.get("temperature_c")),
            "cloud_cover_pct": _number(row.get("cloud_cover_pct")),
        })
    return normalized


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# PolyWX Leakage-Safe Replay Benchmark",
        "",
        DISCLAIMER,
        "",
        "| City | Date | Forecast MAE | Cloud MAE pp | PolyWX mu | WeatherBot mu | PolyWX sigma | WeatherBot sigma | Peak |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("results") or []:
        unit = str(row.get("unit") or "")
        lines.append(
            f"| {row.get('city')} | {row.get('target_date')} | {_fmt(row.get('forecast_mae'))} {unit} | "
            f"{_fmt(row.get('cloud_mae_pct'))} | {_fmt(row.get('polywx_mu'))} | {_fmt(row.get('weatherbot_mu'))} | "
            f"{_fmt(row.get('polywx_sigma'))} | {_fmt(row.get('weatherbot_sigma'))} | "
            f"{row.get('polywx_peak_hour') or '--'} / {row.get('weatherbot_peak_hour') or '--'} |"
        )
    lines.extend(["", "## Field-Level Findings", ""])
    for row in payload.get("results") or []:
        lines.extend([
            f"### {row.get('city')} {row.get('target_date')}",
            "",
            f"- Forecast: {row.get('forecast_pairs')}/24 pairs, MAE {_fmt(row.get('forecast_mae'))} {row.get('unit')}; root cause `{row['root_causes']['forecast']}`.",
            f"- Cloud: {row.get('cloud_pairs')}/24 pairs, MAE {_fmt(row.get('cloud_mae_pct'))} percentage points; root cause `{row['root_causes']['cloud']}`.",
            f"- DEB: mu delta {_fmt(row.get('mu_delta'))} {row.get('unit')}, sigma delta {_fmt(row.get('sigma_delta'))} {row.get('unit')}; root cause `{row['root_causes']['deb']}`.",
            f"- DEB floor: observed {_fmt(row.get('weatherbot_observed_floor'))} {row.get('unit')}, applied={str(bool(row.get('weatherbot_mu_floor_applied'))).lower()}; model weights `{json.dumps(row.get('weatherbot_model_weights') or {}, sort_keys=True)}`.",
            f"- Peak: PolyWX {row.get('polywx_peak_hour') or '--'}, WeatherBot {row.get('weatherbot_peak_hour') or '--'}; root cause `{row['root_causes']['peak']}`.",
            f"- Walk-forward eligible bias rows: {row.get('weatherbot_bias_rows')}; samples: `{json.dumps(row.get('weatherbot_bias_samples') or {}, sort_keys=True)}`.",
            f"- Schema warning: {row.get('polywx_schema_warning') or 'none'}",
            "",
        ])
    lines.extend([
        "## Interpretation",
        "",
        "This report measures parity, not profitability. A large difference is not automatically a WeatherBot bug; it is actionable only after its source, freshness, and computation contract are identified. The replay excludes truth on and after each target date from bias training and does not overwrite `data/bias_table.json`.",
    ])
    return "\n".join(lines) + "\n"


def compact_result(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in (
        "city", "target_date", "forecast_mae", "cloud_mae_pct", "mu_delta", "sigma_delta",
        "polywx_peak_hour", "weatherbot_peak_hour", "weatherbot_bias_rows",
    )}


def convert_temperature(value: float | None, source_unit: str, target_unit: str) -> float | None:
    if value is None:
        return None
    if source_unit == target_unit:
        return value
    if source_unit == "C" and target_unit == "F":
        return value * 9.0 / 5.0 + 32.0
    if source_unit == "F" and target_unit == "C":
        return (value - 32.0) * 5.0 / 9.0
    return value


def convert_delta(value: float | None, source_unit: str, target_unit: str) -> float | None:
    if value is None or source_unit == target_unit:
        return value
    return value * 9.0 / 5.0 if source_unit == "C" and target_unit == "F" else value * 5.0 / 9.0


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _number(value: Any) -> float | None:
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    except Exception:
        return None


def _delta(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _paired_count(rows: list[dict[str, Any]], left: str, right: str) -> int:
    return sum(1 for row in rows if row.get(left) is not None and row.get(right) is not None)


def _mae(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [abs(float(row[field])) for row in rows if row.get(field) is not None]
    return sum(values) / len(values) if values else None


def _fmt(value: Any) -> str:
    number = _number(value)
    return "--" if number is None else f"{number:.2f}"


if __name__ == "__main__":
    main()
