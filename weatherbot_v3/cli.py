from __future__ import annotations

import argparse
import json
import sys
import time

from .db import bulk_settlement_contract_verification, connect, dashboard_summary, init_v3_db, list_settlement_contracts, set_settlement_contract_verification
from .migration import audit_market_files, migrate_legacy_signals, repair_truth_temporal_mismatches, sync_settlement_contracts
from .model_dataset import build_model_dataset_audit, is_settlement_pending
from .notifier import FeishuNotifier
from .qualification import build_data_readiness, persist_data_readiness


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="WeatherBot v3 utilities")
    parser.add_argument(
        "command",
        choices=[
            "init-db",
            "migrate",
            "summary",
            "notify-daily",
            "data-readiness",
            "model-dataset-audit",
            "forecast-backfill",
            "forecast-archive-import",
            "forecast-archive-manifest",
            "orderbook-backfill",
            "contracts-sync",
            "contracts-list",
            "contracts-verify",
            "contracts-bulk-verify",
            "truth-backfill",
            "truth-audit",
        ],
    )
    parser.add_argument("--cities", default="", help="Comma-separated city keys; empty means all cities")
    parser.add_argument("--days", type=int, default=4, help="Local forecast days to persist (1-7)")
    parser.add_argument("--limit", type=int, default=50, help="Maximum recent signal markets to refresh")
    parser.add_argument("--start-date", default="", help="Inclusive local target date filter")
    parser.add_argument("--end-date", default="", help="Inclusive local target date filter")
    parser.add_argument(
        "--status",
        default="unverified",
        help="Contract status filter: all, unverified, verified, auto, mature-auto, future-auto, manual-required, source-missing, low-confidence",
    )
    parser.add_argument("--contract-id", default="", help="Settlement contract id or event slug")
    parser.add_argument("--reviewer", default="local-operator", help="Manual verifier name")
    parser.add_argument("--note", default="", help="Manual verification note")
    parser.add_argument("--archive-path", default="", help="Historical forecast archive JSON/JSONL path")
    parser.add_argument("--output-path", default="", help="Output path for generated JSONL/manifest files")
    parser.add_argument("--sources", default="ecmwf,gfs_ensemble", help="Comma-separated forecast archive sources")
    parser.add_argument("--unverify", action="store_true", help="Clear manual verification instead of setting it")
    parser.add_argument("--apply", action="store_true", help="Apply a bulk write; without it bulk commands are dry-run")
    parser.add_argument("--mature-only", action="store_true", help="Only act on contracts whose local settlement day has ended")
    args = parser.parse_args()

    if args.command == "init-db":
        init_v3_db()
        print("v3 database initialized")
    elif args.command == "migrate":
        init_v3_db()
        payload = {"signals": migrate_legacy_signals(), "markets": audit_market_files()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "summary":
        print(json.dumps(dashboard_summary(), ensure_ascii=False, indent=2))
    elif args.command == "notify-daily":
        summary = dashboard_summary()
        sent = FeishuNotifier().daily_summary(summary)
        print(json.dumps({"sent": sent, "summary": summary}, ensure_ascii=False, indent=2))
    elif args.command == "data-readiness":
        payload = build_data_readiness()
        persist_data_readiness(payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "model-dataset-audit":
        payload = build_model_dataset_audit(min_samples=args.limit)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "forecast-backfill":
        from bot_v2 import LOCATIONS, take_forecast_snapshot, target_dates_for_city

        requested = {item.strip() for item in args.cities.split(",") if item.strip()}
        cities = [city for city in LOCATIONS if not requested or city in requested]
        unknown = sorted(requested - set(LOCATIONS))
        days = max(1, min(int(args.days or 4), 7))
        results = []
        for city in cities:
            dates = target_dates_for_city(city, days)
            try:
                snapshots = take_forecast_snapshot(city, dates)
                results.append({
                    "city": city,
                    "dates": dates,
                    "stored_dates": sum(1 for value in snapshots.values() if value.get("best") is not None),
                    "ok": True,
                })
            except Exception as exc:
                results.append({"city": city, "dates": dates, "stored_dates": 0, "ok": False, "error": str(exc)})
            time.sleep(0.2)
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            "cities": len(cities),
            "unknown_cities": unknown,
            "days": days,
            "ok": sum(1 for row in results if row["ok"]),
            "failed": sum(1 for row in results if not row["ok"]),
            "results": results,
            "forecast_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "forecast_runs"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "forecast-archive-import":
        if not args.archive_path:
            raise SystemExit("--archive-path is required")
        from .forecast_archive import import_forecast_archive

        payload = import_forecast_archive(args.archive_path, apply=args.apply)
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        payload["forecast_stage"] = next(
            (stage for stage in readiness["stages"] if stage["key"] == "forecast_runs"),
            None,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "forecast-archive-manifest":
        from .forecast_archive import build_forecast_archive_manifest, write_forecast_archive_manifest

        sources = [source.strip() for source in args.sources.split(",") if source.strip()]
        audit = build_model_dataset_audit(min_samples=args.limit)
        manifest = build_forecast_archive_manifest(audit, sources=sources)
        payload = {
            key: value
            for key, value in manifest.items()
            if key != "jsonl"
        }
        if args.output_path:
            write_forecast_archive_manifest(manifest, args.output_path)
            payload["output_path"] = args.output_path
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "orderbook-backfill":
        from .polymarket import PolymarketDataClient

        limit = max(1, min(int(args.limit or 50), 500))
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT market_id, MAX(id) latest_id
                FROM signals
                WHERE market_id IS NOT NULL AND market_id != ''
                GROUP BY market_id
                ORDER BY latest_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        client = PolymarketDataClient()
        results = []
        for row in rows:
            market_id = str(row["market_id"])
            try:
                quote = client.quote(market_id)
                results.append({
                    "market_id": market_id,
                    "ok": quote.book_source == "clob",
                    "source": quote.book_source,
                    "best_bid": quote.best_bid,
                    "best_ask": quote.best_ask,
                    "spread": quote.spread,
                    "bid_levels": len(quote.bids),
                    "ask_levels": len(quote.asks),
                    "age_seconds": quote.quote_age_seconds,
                })
            except Exception as exc:
                results.append({"market_id": market_id, "ok": False, "error": str(exc)})
            time.sleep(0.05)
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            "requested": len(rows),
            "ok": sum(1 for row in results if row["ok"]),
            "failed": sum(1 for row in results if not row["ok"]),
            "results": results,
            "orderbook_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "orderbooks"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "contracts-sync":
        payload = sync_settlement_contracts()
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            **payload,
            "contract_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "settlement_contracts"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "contracts-list":
        payload = list_settlement_contracts(status=args.status, limit=args.limit)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "contracts-verify":
        if not args.contract_id:
            raise SystemExit("--contract-id is required")
        if not args.unverify and not str(args.note or "").strip():
            raise SystemExit("--note is required when manually verifying a contract")
        contract = set_settlement_contract_verification(
            args.contract_id,
            verified=not args.unverify,
            reviewer=args.reviewer,
            note=args.note,
        )
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            "ok": True,
            "contract": contract,
            "contract_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "settlement_contracts"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "contracts-bulk-verify":
        contract_ids = [item.strip() for item in args.contract_id.split(",") if item.strip()]
        result = bulk_settlement_contract_verification(
            contract_ids=contract_ids or None,
            limit=args.limit,
            reviewer=args.reviewer,
            note=args.note or "bulk review from CLI",
            require_auto_verified=True,
            mature_only=args.mature_only,
            apply=args.apply,
        )
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            **result,
            "contract_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "settlement_contracts"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "truth-backfill":
        from collections import Counter

        from .db import upsert_truth_observation
        from .registry import SETTLEMENT_REGISTRY
        from .truth import get_actual_observation

        repair = repair_truth_temporal_mismatches()
        requested = {item.strip() for item in args.cities.split(",") if item.strip()}
        profiles = {
            city: profile
            for city, profile in SETTLEMENT_REGISTRY.items()
            if not requested or city in requested
        }
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT city, target_local_date
                FROM settlement_contracts
                WHERE target_local_date IS NOT NULL AND target_local_date != ''
                ORDER BY target_local_date DESC, city
                """
            ).fetchall()
        locations = {
            city: {
                "lat": profile.latitude,
                "lon": profile.longitude,
                "name": profile.city_name,
                "station": profile.station_id,
                "unit": profile.unit,
                "region": profile.region,
            }
            for city, profile in profiles.items()
        }
        timezones = {city: profile.timezone for city, profile in profiles.items()}
        candidates = []
        skipped_pending = 0
        skipped_unknown_city = 0
        for row in rows:
            city = str(row["city"] or "")
            target_date = str(row["target_local_date"] or "")
            if city not in profiles:
                skipped_unknown_city += 1
                continue
            if args.start_date and target_date < args.start_date:
                continue
            if args.end_date and target_date > args.end_date:
                continue
            if is_settlement_pending(target_date, profiles[city].timezone):
                skipped_pending += 1
                continue
            candidates.append((city, target_date))
        candidates = candidates[: max(1, min(int(args.limit or 50), 500))]
        results = []
        for city, target_date in candidates:
            try:
                observation = get_actual_observation(city, target_date, locations, timezones)
                upsert_truth_observation(observation.to_dict())
                results.append({
                    "city": city,
                    "target_date": target_date,
                    "provider": observation.provider,
                    "actual_temp": observation.actual_temp,
                    "eligible": observation.calibration_eligible,
                    "is_final": observation.is_final,
                    "ok": observation.actual_temp is not None,
                })
            except Exception as exc:
                results.append({"city": city, "target_date": target_date, "ok": False, "error": str(exc)})
            time.sleep(0.05)
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        providers = Counter(row.get("provider") or "error" for row in results)
        print(json.dumps({
            "requested": len(candidates),
            "skipped_pending_settlement": skipped_pending,
            "skipped_unknown_city": skipped_unknown_city,
            "ok": sum(1 for row in results if row["ok"]),
            "eligible": sum(1 for row in results if row.get("eligible")),
            "providers": dict(providers),
            "temporal_repair": repair,
            "results": results,
            "truth_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "truth"),
                None,
            ),
        }, ensure_ascii=False, indent=2))
    elif args.command == "truth-audit":
        repair = repair_truth_temporal_mismatches()
        readiness = build_data_readiness()
        persist_data_readiness(readiness)
        print(json.dumps({
            **repair,
            "truth_stage": next(
                (stage for stage in readiness["stages"] if stage["key"] == "truth"),
                None,
            ),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
