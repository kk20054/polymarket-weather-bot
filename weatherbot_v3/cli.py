from __future__ import annotations

import argparse
import json

from .db import dashboard_summary, init_v3_db
from .migration import audit_market_files, migrate_legacy_signals
from .notifier import FeishuNotifier


def main() -> None:
    parser = argparse.ArgumentParser(description="WeatherBot v3 utilities")
    parser.add_argument("command", choices=["init-db", "migrate", "summary", "notify-daily"])
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


if __name__ == "__main__":
    main()

