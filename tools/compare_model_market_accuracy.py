from __future__ import annotations

import argparse
import json 
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "weatherbot_v3.db"
DEFAULT_OUTPUT = ROOT / "audits" / f"model-vs-market-{date.today().isoformat()}" / "README.md"
CHECKPOINTS = {
    "D+0": (0, time(8, 0)),
    "D+1": (1, time(20, 0)),
    "D+2": (2, time(20, 0)),
}
MAX_SNAPSHOT_AGE = timedelta(hours=6)
MAX_BOOK_AGE_SECONDS = 600.0
MIN_COMPLETE_ASK_SUM = 0.95
EPSILON = 1e-15


@dataclass(frozen=True)
class Truth:
    city_key: str
    target_date: str
    actual_c: float
    provider: str
    station_id: str


@dataclass
class Snapshot:
    city_key: str
    target_date: str
    issued_at: datetime
    local_issued_at: datetime
    strategy_name: str
    event_slug: str
    buckets: list[dict[str, Any]]
    model_probs: list[float]
    market_probs: list[float]
    ask_sum: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only comparison of persisted WeatherBot model and market probabilities."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def finite_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return number


def normalized_city_key(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def station_rows(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM stations"):
        payload = dict(row)
        rows[str(payload["city_key"])] = payload
    return rows


def authoritative_truths(
    conn: sqlite3.Connection,
    stations: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], Truth]:
    wu_by_station_date = {
        (str(row["icao"] or "").upper(), str(row["date_local"])): float(row["high_c"])
        for row in conn.execute(
            "SELECT icao,date_local,high_c FROM truth_wunderground_daily WHERE high_c IS NOT NULL"
        )
    }
    hko_by_date = {
        str(row["date_local"]): float(row["high_c"])
        for row in conn.execute(
            "SELECT date_local,high_c FROM truth_hko_daily WHERE high_c IS NOT NULL"
        )
    }
    result: dict[tuple[str, str], Truth] = {}
    for city_key, station in stations.items():
        is_hong_kong = normalized_city_key(city_key) in {"hongkong", "hk"}
        if is_hong_kong:
            for target_date, high_c in hko_by_date.items():
                result[(city_key, target_date)] = Truth(
                    city_key=city_key,
                    target_date=target_date,
                    actual_c=high_c,
                    provider="hong_kong_observatory_daily_extract",
                    station_id="HKO",
                )
            continue
        station_ids = [
            station.get("settlement_station_id"),
            station.get("icao_id"),
            station.get("station_id"),
        ]
        station_id = next((str(item).upper() for item in station_ids if item), "")
        if not station_id:
            continue
        for (icao, target_date), high_c in wu_by_station_date.items():
            if icao != station_id:
                continue
            result[(city_key, target_date)] = Truth(
                city_key=city_key,
                target_date=target_date,
                actual_c=high_c,
                provider="wunderground_daily",
                station_id=station_id,
            )
    return result


def strict_bucket_events(
    conn: sqlite3.Connection,
) -> tuple[
    dict[tuple[str, str], list[tuple[str, set[str]]]],
    dict[str, dict[str, Any]],
]:
    events: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    buckets: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        """
        SELECT *
        FROM market_buckets
        WHERE strict_match_status='matched'
        """
    ):
        payload = dict(row)
        bucket_key = str(payload.get("bucket_key") or "")
        city_key = str(payload.get("city") or "")
        target_date = str(payload.get("target_date") or "")
        event_slug = str(payload.get("event_slug") or "")
        if not bucket_key or not city_key or not target_date or not event_slug:
            continue
        buckets[bucket_key] = payload
        events[(city_key, target_date, event_slug)].add(bucket_key)
    by_city_date: dict[tuple[str, str], list[tuple[str, set[str]]]] = defaultdict(list)
    for (city_key, target_date, event_slug), keys in events.items():
        by_city_date[(city_key, target_date)].append((event_slug, keys))
    return by_city_date, buckets


def decision_groups(conn: sqlite3.Connection) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    query = """
        SELECT city_key,target_date,issued_at,strategy_name,bucket_key,
               model_probability,market_ask,book_age_seconds,
               orderbook_snapshot_json
        FROM signal_decisions
        WHERE strategy_name='single_bucket_ev' OR strategy_name IS NULL
        ORDER BY city_key,target_date,issued_at,id
    """
    for row in conn.execute(query):
        payload = dict(row)
        key = (
            str(payload.get("city_key") or ""),
            str(payload.get("target_date") or ""),
            str(payload.get("issued_at") or ""),
            str(payload.get("strategy_name") or "legacy_single_bucket_ev"),
        )
        groups[key].append(payload)
    return groups


def make_snapshot(
    key: tuple[str, str, str, str],
    rows: list[dict[str, Any]],
    station: dict[str, Any],
    expected_events: list[tuple[str, set[str]]],
    bucket_lookup: dict[str, dict[str, Any]],
) -> tuple[Snapshot | None, str]:
    city_key, target_date, issued_at_text, strategy_name = key
    row_keys = [str(row.get("bucket_key") or "") for row in rows]
    if len(row_keys) != len(set(row_keys)):
        return None, "duplicate_bucket_rows"
    event_match = next(
        ((event_slug, keys) for event_slug, keys in expected_events if set(row_keys) == keys),
        None,
    )
    if event_match is None:
        return None, "incomplete_strict_bucket_set"
    model_probs: list[float] = []
    asks: list[float] = []
    ordered_buckets: list[dict[str, Any]] = []
    for row in rows:
        model_probability = finite_probability(row.get("model_probability"))
        market_ask = finite_probability(row.get("market_ask"))
        if model_probability is None:
            return None, "invalid_model_probability"
        if market_ask is None or not 0.0 < market_ask < 1.0:
            return None, "invalid_or_missing_ask"
        book = safe_json(row.get("orderbook_snapshot_json"))
        if str(book.get("source") or "").lower() != "clob":
            return None, "non_clob_ask"
        try:
            book_age = float(row.get("book_age_seconds"))
        except (TypeError, ValueError):
            return None, "missing_book_age"
        if not math.isfinite(book_age) or book_age < 0 or book_age > MAX_BOOK_AGE_SECONDS:
            return None, "stale_book"
        bucket = bucket_lookup.get(str(row.get("bucket_key") or ""))
        if not bucket:
            return None, "bucket_metadata_missing"
        model_probs.append(model_probability)
        asks.append(market_ask)
        ordered_buckets.append(bucket)
    model_sum = sum(model_probs)
    if abs(model_sum - 1.0) > 1e-6:
        return None, "model_distribution_not_normalized"
    ask_sum = sum(asks)
    if ask_sum < MIN_COMPLETE_ASK_SUM:
        return None, "unusable_complete_ask_sum"
    timezone_name = str(
        station.get("settlement_timezone")
        or station.get("timezone")
        or "UTC"
    )
    try:
        city_timezone = ZoneInfo(timezone_name)
    except Exception:
        return None, "invalid_city_timezone"
    issued_at = parse_timestamp(issued_at_text)
    return (
        Snapshot(
            city_key=city_key,
            target_date=target_date,
            issued_at=issued_at,
            local_issued_at=issued_at.astimezone(city_timezone),
            strategy_name=strategy_name,
            event_slug=event_match[0],
            buckets=ordered_buckets,
            model_probs=model_probs,
            market_probs=[ask / ask_sum for ask in asks],
            ask_sum=ask_sum,
        ),
        "",
    )


def select_checkpoint_samples(
    snapshots: Iterable[Snapshot],
    stations: dict[str, dict[str, Any]],
) -> tuple[list[tuple[str, Snapshot]], Counter[str]]:
    candidates: dict[tuple[str, str, str], list[tuple[timedelta, Snapshot]]] = defaultdict(list)
    rejected: Counter[str] = Counter()
    for snapshot in snapshots:
        station = stations[snapshot.city_key]
        timezone_name = str(
            station.get("settlement_timezone")
            or station.get("timezone")
            or "UTC"
        )
        city_timezone = ZoneInfo(timezone_name)
        target = date.fromisoformat(snapshot.target_date)
        for lead_name, (lead_days, checkpoint_time) in CHECKPOINTS.items():
            checkpoint_date = target - timedelta(days=lead_days)
            checkpoint = datetime.combine(
                checkpoint_date,
                checkpoint_time,
                tzinfo=city_timezone,
            )
            if snapshot.local_issued_at.date() != checkpoint_date:
                continue
            age = checkpoint - snapshot.local_issued_at
            if timedelta(0) <= age <= MAX_SNAPSHOT_AGE:
                candidates[(snapshot.city_key, snapshot.target_date, lead_name)].append(
                    (age, snapshot)
                )
    selected: list[tuple[str, Snapshot]] = []
    for (city_key, target_date, lead_name), rows in candidates.items():
        rows.sort(key=lambda item: (item[0], -item[1].issued_at.timestamp()))
        selected.append((lead_name, rows[0][1]))
    selected.sort(key=lambda item: (item[1].target_date, item[1].city_key, item[0]))
    return selected, rejected


def resolved_temperature(actual_c: float, unit: str) -> int:
    if str(unit or "C").upper() == "F":
        return int(round(actual_c * 9.0 / 5.0 + 32.0))
    return math.floor(actual_c + 1e-9)


def bucket_contains(actual_c: float, bucket: dict[str, Any]) -> bool:
    unit = str(bucket.get("unit") or "C").upper()
    resolved = resolved_temperature(actual_c, unit)
    direction = str(bucket.get("bucket_direction") or "exact").lower()
    low = float(bucket["bucket_low"]) if bucket.get("bucket_low") is not None else None
    high = float(bucket["bucket_high"]) if bucket.get("bucket_high") is not None else None
    if direction in {"or_below", "below", "at_or_below"}:
        bound = high if high is not None else low
        return bound is not None and resolved <= bound
    if direction in {"or_above", "above", "at_or_above"}:
        bound = low if low is not None else high
        return bound is not None and resolved >= bound
    if direction in {"range", "between"}:
        return low is not None and high is not None and low <= resolved <= high
    bound = low if low is not None else high
    return bound is not None and resolved == bound


def bucket_survives_observed_floor(actual_c: float, bucket: dict[str, Any]) -> bool:
    unit = str(bucket.get("unit") or "C").upper()
    observed_floor = resolved_temperature(actual_c, unit)
    direction = str(bucket.get("bucket_direction") or "exact").lower()
    if direction in {"or_above", "above", "at_or_above"}:
        return True
    low = float(bucket["bucket_low"]) if bucket.get("bucket_low") is not None else None
    high = float(bucket["bucket_high"]) if bucket.get("bucket_high") is not None else None
    maximum = high if high is not None else low
    return maximum is not None and maximum >= observed_floor


def counterfactual_probs(
    actual_c: float,
    buckets: list[dict[str, Any]],
    model_probs: list[float],
) -> list[float]:
    retained = [
        probability if bucket_survives_observed_floor(actual_c, bucket) else 0.0
        for bucket, probability in zip(buckets, model_probs)
    ]
    total = sum(retained)
    if total <= 0:
        return list(model_probs)
    return [probability / total for probability in retained]


def metric_row(probabilities: list[float], actual_index: int) -> dict[str, float]:
    brier = sum(
        (probability - (1.0 if index == actual_index else 0.0)) ** 2
        for index, probability in enumerate(probabilities)
    )
    ranked = sorted(
        range(len(probabilities)),
        key=lambda index: (-probabilities[index], index),
    )
    true_probability = probabilities[actual_index]
    return {
        "brier": brier,
        "log_loss": -math.log(max(true_probability, EPSILON)),
        "top1": 1.0 if ranked and ranked[0] == actual_index else 0.0,
        "top2": 1.0 if actual_index in ranked[:2] else 0.0,
        "true_probability": true_probability,
    }


def summarize(samples: list[dict[str, Any]], probability_key: str) -> dict[str, float | int | None]:
    if not samples:
        return {
            "n": 0,
            "brier": None,
            "log_loss": None,
            "top1": None,
            "top2": None,
            "true_probability": None,
        }
    rows = [
        metric_row(sample[probability_key], int(sample["actual_index"]))
        for sample in samples
    ]
    return {
        "n": len(rows),
        "brier": fmean(row["brier"] for row in rows),
        "log_loss": fmean(row["log_loss"] for row in rows),
        "top1": fmean(row["top1"] for row in rows),
        "top2": fmean(row["top2"] for row in rows),
        "true_probability": fmean(row["true_probability"] for row in rows),
    }


def prediction_dates(conn: sqlite3.Connection) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute("SELECT DISTINCT city_key,target_date FROM daily_max_predictions"):
        result[str(row["city_key"])].add(str(row["target_date"]))
    return result


def maturity_counts(
    truths: dict[tuple[str, str], Truth],
    predicted_dates: dict[str, set[str]],
) -> dict[tuple[str, str], int]:
    paired_by_city: dict[str, list[str]] = defaultdict(list)
    for city_key, target_date in truths:
        if target_date in predicted_dates.get(city_key, set()):
            paired_by_city[city_key].append(target_date)
    result: dict[tuple[str, str], int] = {}
    for city_key, dates in paired_by_city.items():
        ordered = sorted(set(dates))
        for index, target_date in enumerate(ordered):
            result[(city_key, target_date)] = index
    return result


def maturity_band(value: int) -> str:
    if value < 10:
        return "<10"
    if value < 20:
        return "10-19"
    return ">=20"


def format_number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    return f"{float(value):.{digits}f}"


def format_percent(value: Any) -> str:
    if value is None:
        return "--"
    return f"{float(value) * 100:.2f}%"


def metrics_table(overall: dict[str, dict[str, Any]]) -> list[str]:
    return [
        "| 指标 | A 模型 | B 市场 | C 模型+实况截断 |",
        "|---|---:|---:|---:|",
        f"| 多分类 Brier | {format_number(overall['A']['brier'])} | {format_number(overall['B']['brier'])} | {format_number(overall['C']['brier'])} |",
        f"| Log loss | {format_number(overall['A']['log_loss'])} | {format_number(overall['B']['log_loss'])} | {format_number(overall['C']['log_loss'])} |",
        f"| Top-1 命中率 | {format_percent(overall['A']['top1'])} | {format_percent(overall['B']['top1'])} | {format_percent(overall['C']['top1'])} |",
        f"| Top-2 命中率 | {format_percent(overall['A']['top2'])} | {format_percent(overall['B']['top2'])} | {format_percent(overall['C']['top2'])} |",
        f"| 结算桶的平均概率 | {format_percent(overall['A']['true_probability'])} | {format_percent(overall['B']['true_probability'])} | {format_percent(overall['C']['true_probability'])} |",
    ]


def brier_breakdown_table(
    samples: list[dict[str, Any]],
    group_key: str,
    ordered_groups: Iterable[str],
    minimum_n: int = 0,
) -> list[str]:
    lines = [
        "| 分组 | N | A 模型 Brier | B 市场 Brier | C 截断 Brier | A-B |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for group in ordered_groups:
        subset = [sample for sample in samples if str(sample[group_key]) == group]
        if len(subset) < minimum_n:
            continue
        model = summarize(subset, "model_probs")
        market = summarize(subset, "market_probs")
        counterfactual = summarize(subset, "counterfactual_probs")
        difference = (
            float(model["brier"]) - float(market["brier"])
            if model["brier"] is not None and market["brier"] is not None
            else None
        )
        lines.append(
            f"| {group} | {len(subset)} | {format_number(model['brier'])} | "
            f"{format_number(market['brier'])} | {format_number(counterfactual['brier'])} | "
            f"{format_number(difference)} |"
        )
    return lines


def stable_segments(samples: list[dict[str, Any]]) -> list[tuple[str, int, float, float]]:
    segments: list[tuple[str, list[dict[str, Any]]]] = []
    for lead in CHECKPOINTS:
        segments.append((lead, [sample for sample in samples if sample["lead"] == lead]))
    for city_key in sorted({str(sample["city_key"]) for sample in samples}):
        segments.append(
            (f"城市:{city_key}", [sample for sample in samples if sample["city_key"] == city_key])
        )
    for band in ("<10", "10-19", ">=20"):
        segments.append(
            (f"成熟度:{band}", [sample for sample in samples if sample["maturity_band"] == band])
        )
    winners: list[tuple[str, int, float, float]] = []
    for name, subset in segments:
        if len(subset) < 20:
            continue
        model = summarize(subset, "model_probs")
        market = summarize(subset, "market_probs")
        if float(model["brier"]) < float(market["brier"]):
            winners.append(
                (name, len(subset), float(model["brier"]), float(market["brier"]))
            )
    return winners


def build_report(
    samples: list[dict[str, Any]],
    truths: dict[tuple[str, str], Truth],
    bucket_events: dict[tuple[str, str], list[tuple[str, set[str]]]],
    group_counts: dict[str, int],
    exclusions: Counter[str],
) -> str:
    overall = {
        "A": summarize(samples, "model_probs"),
        "B": summarize(samples, "market_probs"),
        "C": summarize(samples, "counterfactual_probs"),
    }
    city_counts = Counter(str(sample["city_key"]) for sample in samples)
    provider_counts = Counter(str(sample["truth_provider"]) for sample in samples)
    winners = stable_segments(samples)
    model_brier = float(overall["A"]["brier"]) if overall["A"]["brier"] is not None else math.nan
    market_brier = float(overall["B"]["brier"]) if overall["B"]["brier"] is not None else math.nan
    counterfactual_brier = (
        float(overall["C"]["brier"]) if overall["C"]["brier"] is not None else math.nan
    )
    difference = model_brier - market_brier

    lines = [
        "# WeatherBot 模型 vs 市场同样本准确度",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat()}",
        f"- 数据库：`{DEFAULT_DB}`（SQLite URI `mode=ro` + `query_only=ON`）",
        f"- 最终样本：**N={len(samples)}**；每个 city/date/lead 最多 1 个样本。",
        f"- Truth：{', '.join(f'{key}={value}' for key, value in sorted(provider_counts.items())) or '无'}。",
        "",
        "## 口径",
        "",
        "- A：历史 `signal_decisions` 中 `single_bucket_ev`（含早期 legacy 同口径）在该时点持久化的完整 DEB 桶概率，未重算、未改权重。",
        "- B：同一决策快照内每个桶的 CLOB `market_ask`，全桶 ask 相加后归一化到 1。",
        "- C：在 A 上事后使用最终权威日最高温作为“已观测 floor”，删除已不可能的较低桶后重归一；这是带最终实况信息的反事实，不是可部署的无泄漏成绩。",
        "- 固定截面：D+0 当地 08:00、D+1 当地 20:00、D+2 当地 20:00；取截面前 6 小时内最近的完整快照。",
        "- 可用 ask：全桶 strict matched、每桶 ask 在 (0,1)、来源为 CLOB、book age <= 600 秒、全桶 ask 总和 >= 0.95。",
        "- °C 市场按 `floor(actual_c)` 结算桶；°F 市场先转 °F 后取整，range 桶按双端包含判断。",
        "- 多分类 Brier 为每个事件 `sum_k (p_k-y_k)^2`，与 README 采用的未除桶数定义一致；越低越好。",
        "",
        "## 样本漏斗",
        "",
        f"- 已有权威 truth 的 city/date：{len(truths)}",
        f"- 同时存在 strict-matched 桶事件的 city/date：{sum(1 for key in truths if key in bucket_events)}",
        f"- 扫描历史完整桶策略快照组：{group_counts.get('raw_groups', 0)}",
        f"- 通过桶、概率与盘口质量校验的快照组：{group_counts.get('valid_groups', 0)}",
        f"- 命中固定 D+0/D+1/D+2 截面的最终样本：{len(samples)}",
        "",
        "排除原因（快照组）：",
        "",
    ]
    if exclusions:
        lines.extend(f"- `{reason}`：{count}" for reason, count in exclusions.most_common())
    else:
        lines.append("- 无")

    lines.extend(
        [
            "",
            "## 城市分布",
            "",
            "| 城市 | N |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {city_key} | {count} |" for city_key, count in sorted(city_counts.items()))
    lines.extend(["", "## 总体对照", ""])
    lines.extend(metrics_table(overall))
    lines.extend(["", "## D+0 / D+1 / D+2", ""])
    lines.extend(brier_breakdown_table(samples, "lead", CHECKPOINTS.keys()))
    lines.extend(["", "## 城市（仅 N>=10）", ""])
    lines.extend(
        brier_breakdown_table(
            samples,
            "city_key",
            sorted(city_counts),
            minimum_n=10,
        )
    )
    lines.extend(["", "## 模型成熟度", ""])
    lines.extend(
        brier_breakdown_table(
            samples,
            "maturity_band",
            ("<10", "10-19", ">=20"),
        )
    )
    lines.extend(
        [
            "",
            "成熟度定义：该城市在目标日前，同时已有 `daily_max_predictions` 与权威 truth 的独立 city/date 对数。",
            "",
            "## 必须回答的三句话",
            "",
            f"1. 在同一批 N={len(samples)} 个样本上，"
            f"{'市场' if market_brier < model_brier else '模型'} Brier 更低："
            f"模型 {model_brier:.4f}，市场 {market_brier:.4f}，"
            f"模型减市场为 {difference:+.4f}。",
            f"2. 加上实况截断后，C 的 Brier 为 {counterfactual_brier:.4f}；"
            f"{'能够反超市场' if counterfactual_brier < market_brier else '仍然落后于市场'}"
            f"（市场 {market_brier:.4f}）。",
        ]
    )
    if winners:
        winner_text = "；".join(
            f"{name}（N={n}，模型 {model:.4f} < 市场 {market:.4f}）"
            for name, n, model, market in winners
        )
        lines.append(f"3. 存在 N>=20 且模型 Brier 低于市场的细分：{winner_text}。")
    else:
        lines.append("3. 不存在任何 N>=20 且模型 Brier 低于市场的城市、提前量或成熟度细分。")

    lines.extend(
        [
            "",
            "## 待办观察",
            "",
            "- 本报告没有修改任何生产代码。历史 `core-modal-leakage-safe-replay-v1` 的 README 113 例不是本报告样本：它只有 D+1/D+0，并且当时的审计没有持久化同批完整 ask 向量。",
            "- 旧回放调用的 `bucket_contains_celsius()` 未显式处理 `bucket_direction='range'`；这可能影响 README 里 °F 双温度桶的 outcome/Brier。此处仅记录，不修复。",
            "- `core_modal_v1` 每轮只持久化被选桶，无法重建完整多分类分布，因此不进入本报告；本报告使用仍保存全桶概率的 `single_bucket_ev` 历史快照。",
            "- 全桶 ask 总和低于 0.95 的结算后/残缺盘口被判定为不可用；否则把全桶 0.1¢ 残留价归一化会伪造“均匀市场概率”。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    conn = open_read_only(args.db)
    try:
        stations = station_rows(conn)
        truths = authoritative_truths(conn, stations)
        bucket_events, bucket_lookup = strict_bucket_events(conn)
        groups = decision_groups(conn)
        predicted_dates = prediction_dates(conn)
        maturity = maturity_counts(truths, predicted_dates)

        valid_snapshots: list[Snapshot] = []
        exclusions: Counter[str] = Counter()
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
            snapshot, reason = make_snapshot(
                key,
                rows,
                station,
                expected_events,
                bucket_lookup,
            )
            if snapshot is None:
                exclusions[reason] += 1
                continue
            valid_snapshots.append(snapshot)

        selected, checkpoint_rejections = select_checkpoint_samples(valid_snapshots, stations)
        exclusions.update(checkpoint_rejections)
        samples: list[dict[str, Any]] = []
        for lead, snapshot in selected:
            truth = truths[(snapshot.city_key, snapshot.target_date)]
            actual_matches = [
                index
                for index, bucket in enumerate(snapshot.buckets)
                if bucket_contains(truth.actual_c, bucket)
            ]
            if len(actual_matches) != 1:
                exclusions["truth_does_not_map_to_exactly_one_bucket"] += 1
                continue
            maturity_count = maturity.get((snapshot.city_key, snapshot.target_date), 0)
            samples.append(
                {
                    "city_key": snapshot.city_key,
                    "target_date": snapshot.target_date,
                    "lead": lead,
                    "issued_at": snapshot.issued_at.isoformat(),
                    "truth_provider": truth.provider,
                    "actual_c": truth.actual_c,
                    "actual_index": actual_matches[0],
                    "model_probs": snapshot.model_probs,
                    "market_probs": snapshot.market_probs,
                    "counterfactual_probs": counterfactual_probs(
                        truth.actual_c,
                        snapshot.buckets,
                        snapshot.model_probs,
                    ),
                    "maturity_count": maturity_count,
                    "maturity_band": maturity_band(maturity_count),
                }
            )

        report = build_report(
            samples,
            truths,
            bucket_events,
            {
                "raw_groups": len(groups),
                "valid_groups": len(valid_snapshots),
            },
            exclusions,
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
