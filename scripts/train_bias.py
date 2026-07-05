from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weatherbot_v3.bias import train_bias_table


def main() -> None:
    parser = argparse.ArgumentParser(description="Train WeatherBot additive model bias table from local truth + forecast data.")
    parser.add_argument("--city", action="append", dest="cities", help="City key to include. Repeat for multiple cities.")
    parser.add_argument("--days", type=int, default=90, help="Lookback days to inspect.")
    parser.add_argument("--output", default="", help="Output JSON path. Defaults to data/bias_table.json.")
    args = parser.parse_args()
    payload = train_bias_table(
        cities=args.cities,
        days=args.days,
        output_path=Path(args.output) if args.output else None,
    )
    print(f"bias rows: {payload['row_count']}")
    print(f"generated_at: {payload['generated_at']}")


if __name__ == "__main__":
    main()
