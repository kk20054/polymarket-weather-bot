from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import compare_model_market_accuracy as base
import diagnose_emos_v2_pnl as previous
import diagnose_model_recalibration as diagnostic


DEFAULT_DB = ROOT / "data" / "weatherbot_v3.db"
DEFAULT_OUTPUT = (
    ROOT
    / "audits"
    / f"v3-pnl-power-clv-deb-fix-{date.today().isoformat()}"
    / "README.md"
)
ASK_BINS = ("all", "<0.05", "0.05-0.10", "0.10-0.20", "0.20-0.40", ">0.40")
MODEL_SOURCES = (
    ("E4d V3-only", "e4_v3_probs"),
    ("E1 B8 EMOS", "b8_probs"),
    ("E6 selector", "e6_probs"),
    ("B4 production", "model_probs"),
)
TARGET_ROIS = (0.05, 0.10, 0.20, 0.50)
Z_ONE_SIDED_ALPHA_05 = 1.6448536269514722
Z_POWER_80 = 0.8416212335729143


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only V3 P&L, condition, power and CLV feasibility audit."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def fmt_number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    number = float(value)
    if not math.isfinite(number):
        return "--"
    return f"{number:.{digits}f}"


def fmt_percent(value: Any, digits: int = 2) -> str:
    if value is None:
        return "--"
    number = float(value)
    if not math.isfinite(number):
        return "--"
    return f"{number * 100:.{digits}f}%"


def generalized_conditions(
    samples: list[dict[str, Any]],
    probability_key: str,
) -> list[dict[str, Any]]:
    rows = [row for row in samples if row.get(probability_key) is not None]
    spread_rows = [
        row
        for row in rows
        if row.get("e4_all6_probs") is not None
        and row.get("e4_all6_effective_spread_c") is not None
    ]
    spread_labels, spread_cuts = previous.quartile_labels(
        spread_rows,
        lambda row: row["e4_all6_effective_spread_c"],
    )

    def disagreement(row: dict[str, Any]) -> float:
        probabilities = row[probability_key]
        top_index = max(
            range(len(probabilities)),
            key=lambda index: probabilities[index],
        )
        return abs(
            float(probabilities[top_index]) - float(row["ask_values"][top_index])
        )

    disagreement_labels, disagreement_cuts = previous.quartile_labels(
        rows,
        disagreement,
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if id(row) in spread_labels:
            groups[("ensemble spread", spread_labels[id(row)])].append(row)
        groups[("lead", previous.lead_group(row["lead"]))].append(row)
        groups[
            ("model-market disagreement", disagreement_labels[id(row)])
        ].append(row)
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
            maturity_band = "<5"
        elif maturity <= 10:
            maturity_band = "5-10"
        else:
            maturity_band = ">10"
        groups[("prior paired days", maturity_band)].append(row)

    output: list[dict[str, Any]] = []
    for (dimension, cell), subset in sorted(groups.items()):
        model = base.summarize(subset, probability_key)
        market = base.summarize(subset, "market_probs")
        output.append(
            {
                "dimension": dimension,
                "cell": cell,
                "rows": subset,
                "n": len(subset),
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
    return output


def pnl_rows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seed = 202607260
    for source_index, (source_name, probability_key) in enumerate(MODEL_SOURCES):
        eligible = [
            sample for sample in samples if sample.get(probability_key) is not None
        ]
        for threshold_index, threshold in enumerate(previous.EDGE_THRESHOLDS):
            for bin_index, ask_band in enumerate(ASK_BINS):
                row = previous.pnl_row(
                    eligible,
                    probability_key,
                    threshold,
                    ask_filter=None if ask_band == "all" else ask_band,
                    seed=seed
                    + source_index * 100
                    + threshold_index * 10
                    + bin_index,
                )
                row["source"] = source_name
                row["probability_key"] = probability_key
                output.append(row)
    return output


def selected_trade_rows(
    samples: Iterable[dict[str, Any]],
    probability_key: str,
    *,
    edge_threshold: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for sample in samples:
        probabilities = sample.get(probability_key)
        if probabilities is None:
            continue
        for index, (probability, ask, bucket) in enumerate(
            zip(probabilities, sample["ask_values"], sample["buckets"])
        ):
            probability_value = float(probability)
            ask_value = float(ask)
            if probability_value - ask_value + 1e-12 < edge_threshold:
                continue
            output.append(
                {
                    "city_key": str(sample["city_key"]),
                    "target_date": str(sample["target_date"]),
                    "lead": str(sample["lead"]),
                    "issued_at": sample["issued_at"],
                    "bucket_index": index,
                    "bucket_key": str(bucket.get("bucket_key") or ""),
                    "token_id": str(
                        bucket.get("yes_token_id")
                        or bucket.get("outcome_yes_token_id")
                        or bucket.get("token_id")
                        or ""
                    ),
                    "ask": ask_value,
                    "probability": probability_value,
                    "edge": probability_value - ask_value,
                    "ask_bin": previous.ask_bin(ask_value),
                }
            )
    return output


def calendar_span_days(trades: list[dict[str, Any]]) -> int:
    dates = sorted(
        date.fromisoformat(str(row["target_date"])) for row in trades
    )
    if not dates:
        return 0
    return (dates[-1] - dates[0]).days + 1


def power_rows(
    samples: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trades = selected_trade_rows(
        samples,
        "e4_v3_probs",
        edge_threshold=0.08,
    )
    total_span_days = calendar_span_days(trades)
    output: list[dict[str, Any]] = []
    z_total_sq = (Z_ONE_SIDED_ALPHA_05 + Z_POWER_80) ** 2
    for ask_band in ASK_BINS[1:]:
        band_trades = [row for row in trades if row["ask_bin"] == ask_band]
        daily_rate = (
            len(band_trades) / total_span_days if total_span_days > 0 else 0.0
        )
        for target_roi in TARGET_ROIS:
            variances: list[float] = []
            feasible_asks: list[float] = []
            for row in band_trades:
                ask = float(row["ask"])
                probability = ask * (1.0 + target_roi)
                if not 0.0 < probability < 1.0:
                    continue
                variances.append(probability * (1.0 - probability) / (ask * ask))
                feasible_asks.append(ask)
            per_trade_variance = fmean(variances) if variances else None
            required = (
                math.ceil(z_total_sq * per_trade_variance / (target_roi**2))
                if per_trade_variance is not None
                else None
            )
            output.append(
                {
                    "target_roi": target_roi,
                    "ask_bin": ask_band,
                    "observed_candidates": len(band_trades),
                    "mean_ask": fmean(feasible_asks) if feasible_asks else None,
                    "variance": per_trade_variance,
                    "required_trades": required,
                    "daily_rate": daily_rate,
                    "days": (
                        required / daily_rate
                        if required is not None and daily_rate > 0
                        else None
                    ),
                }
            )
    return output, {
        "edge_threshold": 0.08,
        "calendar_span_days": total_span_days,
        "all_candidates": len(trades),
        "all_daily_rate": len(trades) / total_span_days if total_span_days else 0.0,
    }


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def clv_feasibility(
    conn: sqlite3.Connection,
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    selected = [
        row
        for row in selected_trade_rows(
            samples,
            "e4_v3_probs",
            edge_threshold=0.08,
        )
        if row["ask_bin"] == "0.05-0.10"
    ]
    orderbook_columns = table_columns(conn, "polymarket_orderbook")
    event_columns = table_columns(conn, "polymarket_events")
    signal_columns = table_columns(conn, "signal_decisions")
    token_ids = sorted({row["token_id"] for row in selected if row["token_id"]})
    snapshot_counts: dict[str, dict[str, Any]] = {}
    if token_ids and {"token_id", "ts", "best_ask"}.issubset(orderbook_columns):
        placeholders = ",".join("?" for _ in token_ids)
        query = f"""
            SELECT token_id,
                   COUNT(*) AS rows,
                   MIN(ts) AS first_ts,
                   MAX(ts) AS last_ts,
                   SUM(CASE WHEN best_ask IS NOT NULL THEN 1 ELSE 0 END) AS ask_rows
            FROM polymarket_orderbook
            WHERE token_id IN ({placeholders})
            GROUP BY token_id
        """
        for row in conn.execute(query, token_ids):
            snapshot_counts[str(row["token_id"])] = dict(row)
    with_any_snapshot = sum(
        1 for row in selected if row["token_id"] in snapshot_counts
    )
    with_ask_snapshot = sum(
        1
        for row in selected
        if int(snapshot_counts.get(row["token_id"], {}).get("ask_rows") or 0) > 0
    )

    close_columns = {
        "end_date",
        "end_at",
        "close_time",
        "closed_at",
        "market_close_at",
    }
    event_has_close = bool(event_columns & close_columns)
    orderbook_has_role = bool(
        orderbook_columns
        & {"snapshot_role", "snapshot_type", "is_preclose", "market_status"}
    )
    signal_has_decision_ask = "decision_time_ask" in signal_columns
    signal_has_quote_age = "quote_age_at_decision_seconds" in signal_columns
    reconstructable = (
        event_has_close
        and orderbook_has_role
        and signal_has_decision_ask
        and signal_has_quote_age
    )
    reasons: list[str] = []
    if not event_has_close:
        reasons.append("market close timestamp is not persisted")
    if not orderbook_has_role:
        reasons.append("orderbook rows are not labeled as pre-close/final")
    if not signal_has_decision_ask:
        reasons.append("signal_decisions.decision_time_ask is missing")
    if not signal_has_quote_age:
        reasons.append("signal_decisions.quote_age_at_decision_seconds is missing")
    return {
        "reconstructable": reconstructable,
        "selected_trades": len(selected),
        "unique_tokens": len(token_ids),
        "with_any_snapshot": with_any_snapshot,
        "with_ask_snapshot": with_ask_snapshot,
        "event_columns": sorted(event_columns),
        "orderbook_columns": sorted(orderbook_columns),
        "signal_columns": sorted(signal_columns),
        "reasons": reasons,
        "clv_n": 0,
        "clv_mean": None,
        "clv_std": None,
        "clv_t": None,
    }


def render_report(
    *,
    db_path: Path,
    samples: list[dict[str, Any]],
    pnl: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    power: list[dict[str, Any]],
    power_meta: dict[str, Any],
    clv: dict[str, Any],
    snapshot_exclusions: Counter[str],
    sample_exclusions: Counter[str],
    selections: Counter[str],
) -> str:
    v3_qualifying = [
        row for row in conditions if row["n"] >= 25 and row["difference"] < 0
    ]
    v3_low = next(
        row
        for row in pnl
        if row["source"] == "E4d V3-only"
        and abs(row["threshold"] - 0.08) < 1e-12
        and row["ask_bin"] == "0.05-0.10"
    )
    e6_low = next(
        row
        for row in pnl
        if row["source"] == "E6 selector"
        and abs(row["threshold"] - 0.08) < 1e-12
        and row["ask_bin"] == "0.05-0.10"
    )
    power_target = next(
        row
        for row in power
        if row["ask_bin"] == "0.05-0.10"
        and abs(row["target_roi"] - 0.20) < 1e-12
    )

    lines = [
        "# V3-only P&L、功效与 CLV 基础设施审计",
        "",
        f"- 数据库：`{db_path}`",
        f"- 走查样本：{len(samples)}；E6 选择分布：`{dict(selections)}`",
        f"- 快照排除：`{dict(snapshot_exclusions)}`",
        f"- 样本排除：`{dict(sample_exclusions)}`",
        "- 所有概率拟合沿用上一轮严格走查：训练 target_date 必须早于评估样本 issued date。",
        "",
        "## 先承认上一轮三个缺陷",
        "",
        "1. 上一轮全部 P&L 和条件分箱误用了 `e6_probs`，没有回测 Brier 最低的 `E4d V3-only`。",
        "2. E6 小样本选择器过拟合，频繁选择较差的 core4，组合结果反而劣于 V3-only。",
        "3. 低价二元合约单笔 ROI 方差巨大，几百笔的 bootstrap 下界不为正是功效不足，不是模型无效的充分证据。",
        "",
        "## A1 四组概率源 P&L",
        "",
        "| 概率源 | edge | ask 区间 | 交易数 | 命中率 | ROI | 95% CI |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pnl:
        lines.append(
            "| {source} | {edge:.0%} | {ask_bin} | {trades} | {hit} | {roi} | [{low}, {high}] |".format(
                source=row["source"],
                edge=row["threshold"],
                ask_bin=row["ask_bin"],
                trades=row["trades"],
                hit=fmt_percent(row["hit_rate"]),
                roi=fmt_percent(row["roi"]),
                low=fmt_percent(row["ci_low"]),
                high=fmt_percent(row["ci_high"]),
            )
        )
    lines += [
        "",
        "### A1 必答",
        "",
        (
            f"- 在统一的 `edge>=8%` 口径下，V3-only 的 5-10c 桶共有 "
            f"{v3_low['trades']} 笔，ROI {fmt_percent(v3_low['roi'])}，"
            f"95% CI [{fmt_percent(v3_low['ci_low'])}, {fmt_percent(v3_low['ci_high'])}]。"
        ),
        (
            f"- E6 同格 ROI 为 {fmt_percent(e6_low['roi'])}；"
            f"V3-only 相对差值为 "
            f"{fmt_percent((v3_low['roi'] or 0.0) - (e6_low['roi'] or 0.0))}。"
        ),
        "",
        "## A2 V3-only 条件分箱",
        "",
        "| 维度 | 分箱 | N | V3 Brier | 市场 Brier | 差值 | V3 Top-1 | 市场 Top-1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in conditions:
        lines.append(
            "| {dimension} | {cell} | {n} | {model:.4f} | {market:.4f} | {difference:+.4f} | {model_top1} | {market_top1} |".format(
                dimension=row["dimension"],
                cell=row["cell"],
                n=row["n"],
                model=row["model_brier"],
                market=row["market_brier"],
                difference=row["difference"],
                model_top1=fmt_percent(row["model_top1"]),
                market_top1=fmt_percent(row["market_top1"]),
            )
        )
    lines += [
        "",
        "### A2 必答",
        "",
    ]
    if v3_qualifying:
        lines.append(
            "- 存在 N>=25 且 V3-only Brier 低于市场的分箱："
            + "；".join(
                f"{row['dimension']}={row['cell']} (N={row['n']}, 差 {row['difference']:+.4f})"
                for row in v3_qualifying
            )
            + "。"
        )
    else:
        lines.append("- 不存在 N>=25 且 V3-only Brier 低于市场的条件分箱。")
    lines += [
        "",
        "## A3 功效分析",
        "",
        (
            f"- 候选定义：V3-only、edge>=8%；样本覆盖 {power_meta['calendar_span_days']} 个日历日，"
            f"共 {power_meta['all_candidates']} 个候选，平均 {power_meta['all_daily_rate']:.3f} 个/日。"
        ),
        "- 公式：单侧 alpha=5%、power=80%，二元单位合约 ROI 方差为 `p(1-p)/ask^2`，其中 `p=ask*(1+目标ROI)`。",
        "- 这是忽略同城/同日相关性的乐观下界；真实所需样本只会更多。",
        "",
        "| 目标 ROI | ask 区间 | 观测候选 | 平均 ask | 每笔 ROI 方差 | 80% 功效所需交易数 | 当前速率/日 | 所需天数 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in power:
        lines.append(
            "| {roi:.0%} | {ask_bin} | {observed} | {ask} | {variance} | {required} | {rate} | {days} |".format(
                roi=row["target_roi"],
                ask_bin=row["ask_bin"],
                observed=row["observed_candidates"],
                ask=fmt_number(row["mean_ask"], 4),
                variance=fmt_number(row["variance"], 3),
                required=row["required_trades"] or "--",
                rate=fmt_number(row["daily_rate"], 3),
                days=fmt_number(row["days"], 0),
            )
        )
    lines += [
        "",
        "### A3 必答",
        "",
        (
            f"- 在 5-10c 桶检测 20% ROI 至少需要 {power_target['required_trades']} 笔；"
            f"按当前 {power_target['daily_rate']:.3f} 笔/日约需 "
            f"{fmt_number(power_target['days'], 0)} 天。"
        ),
        (
            "- 该周期已达到“数年”量级，历史二元 P&L 作为主要模型迭代指标不可行；"
            "前瞻阶段必须使用方差更低的 CLV/概率评分，同时保留真实 P&L 为最终结果。"
            if (power_target["days"] or 0) > 730
            else "- 该周期未超过两年，但仍应优先用 CLV/概率评分提高迭代效率。"
        ),
        "",
        "## A4 CLV 可行性",
        "",
        f"- V3-only 5-10c、edge>=8% 候选：{clv['selected_trades']} 笔，{clv['unique_tokens']} 个 token。",
        f"- 有任意 orderbook 快照：{clv['with_any_snapshot']}；有 ask 快照：{clv['with_ask_snapshot']}。",
    ]
    if clv["reconstructable"]:
        lines += [
            f"- 可重建 CLV：N={clv['clv_n']}，均值={fmt_number(clv['clv_mean'])}，"
            f"标准差={fmt_number(clv['clv_std'])}，t={fmt_number(clv['clv_t'])}。"
        ]
    else:
        lines += [
            "- **现有快照不能严谨重建 CLV。**",
            "- 缺口：" + "；".join(clv["reasons"]) + "。",
            "- `polymarket_orderbook` 的最后一行不能自动视为收盘前最后报价；在没有市场 close timestamp 和 pre-close 标签时这样做会产生前视/口径错误。",
        ]
    lines += [
        "",
        "## B 部分生产修复",
        "",
        "_本节由同一轮 B1-B4 实施完成后补充；A 部分数据在生产改动前已一次性只读计算。_",
        "",
        "## 四句结论",
        "",
        (
            f"1. V3-only 在 5-10c、edge>=8% 的 ROI 为 {fmt_percent(v3_low['roi'])}，"
            f"95% CI [{fmt_percent(v3_low['ci_low'])}, {fmt_percent(v3_low['ci_high'])}]，"
            f"相对 E6 同格差 {fmt_percent((v3_low['roi'] or 0.0) - (e6_low['roi'] or 0.0))}。"
        ),
        (
            "2. 用 V3-only 重测后，"
            + (
                "存在 N>=25 且模型优于市场的条件子集。"
                if v3_qualifying
                else "不存在 N>=25 且模型优于市场的条件子集。"
            )
        ),
        (
            f"3. 检测 20% ROI 需要 {power_target['required_trades']} 笔、约 "
            f"{fmt_number(power_target['days'], 0)} 天；"
            "若达到数年量级，历史 P&L 路线不适合作为主要迭代反馈。"
        ),
        (
            "4. 现有快照"
            + (
                f"可重建 CLV；5-10c 平均 CLV 为 {fmt_number(clv['clv_mean'])}。"
                if clv["reconstructable"]
                else "不能严谨重建 CLV，缺少决策报价年龄、市场收盘时间和明确的 pre-close 最终报价标记。"
            )
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    # The prior diagnostic used 5,000 draws for a handful of rows. This audit
    # evaluates 120 cells; 2,000 clustered draws keeps the interval stable
    # without repeating more than a hundred million Python-level operations.
    previous.BOOTSTRAP_DRAWS = 2_000
    conn = base.open_read_only(args.db)
    try:
        stations = base.station_rows(conn)
        truths = base.authoritative_truths(conn, stations)
        bucket_events, bucket_lookup = base.strict_bucket_events(conn)
        groups = base.decision_groups(conn)
        predictions = diagnostic.load_predictions(conn)
        maturity = base.maturity_counts(truths, base.prediction_dates(conn))
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
        previous.attach_ask_sums(samples, snapshots, stations)
        relevant_pairs = {
            (sample["city_key"], sample["target_date"]) for sample in samples
        }
        member_lookup, _ = previous.load_gfs_member_spreads(conn, relevant_pairs)
        observations = diagnostic.load_observations(conn, stations)
        previous.enrich_samples(conn, samples, member_lookup, observations)
        variant_meta = previous.prepare_variants(samples, truths)

        pnl = pnl_rows(samples)
        conditions = generalized_conditions(samples, "e4_v3_probs")
        power, power_meta = power_rows(samples)
        clv = clv_feasibility(conn, samples)
        report = render_report(
            db_path=args.db.resolve(),
            samples=samples,
            pnl=pnl,
            conditions=conditions,
            power=power,
            power_meta=power_meta,
            clv=clv,
            snapshot_exclusions=snapshot_exclusions,
            sample_exclusions=sample_exclusions,
            selections=variant_meta["e6_selections"],
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "samples": len(samples),
                    "v3_samples": sum(
                        sample.get("e4_v3_probs") is not None for sample in samples
                    ),
                    "snapshot_exclusions": dict(snapshot_exclusions),
                    "sample_exclusions": dict(sample_exclusions),
                    "e6_selections": dict(variant_meta["e6_selections"]),
                    "clv_reconstructable": clv["reconstructable"],
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
