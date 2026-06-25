from __future__ import annotations

import argparse
import json
import time

from .db import connect, dashboard_summary, init_v3_db
from .migration import audit_market_files, migrate_legacy_signals
from .notifier import FeishuNotifier
from .qualification import build_data_readiness, persist_data_readiness


def main() -> None:
    parser = argparse.ArgumentParser(description="WeatherBot v3 utilities")
    parser.add_argument(
        "command",
        choices=[
            "init-db",
            "migrate",
            "summary",
            "notify-daily",
            "data-readiness",
            "forecast-backfill",
            "orderbook-backfill",
        ],
    )
    parser.add_argument("--cities", default="", help="Comma-separated city keys; empty means all cities")
    parser.add_argument("--days", type=int, default=4, help="Local forecast days to persist (1-7)")
    parser.add_argument("--limit", type=int, default=50, help="Maximum recent signal markets to refresh")
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


if __name__ == "__main__":
    main()
