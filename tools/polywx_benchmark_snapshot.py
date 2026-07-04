from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


POLYWX_BASE_URL = "https://www.polywx.xyz/"
DISCLAIMER = (
    "Audit-only PolyWX benchmark. Do not import these values into WeatherBot "
    "truth, mesonet_observations, hourly_consensus, forecasts, or trading tables."
)


def snapshot_city_date(
    city: str,
    target_date: str,
    *,
    output_dir: Path,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    client = session or requests.Session()
    url = f"{POLYWX_BASE_URL}?city={city}&date={target_date}"
    response = client.get(url, headers={"User-Agent": "WeatherBot/benchmark-snapshot"}, timeout=30)
    response.raise_for_status()
    html = response.text
    (output_dir / f"{city}-{target_date}.html").write_text(html, encoding="utf-8")
    api_urls = discover_api_urls(html, base_url=response.url or url)
    api_payloads: list[dict[str, Any]] = []
    for index, api_url in enumerate(api_urls[:25]):
        try:
            api_response = client.get(api_url, headers={"User-Agent": "WeatherBot/benchmark-snapshot"}, timeout=30)
            content_type = api_response.headers.get("content-type", "")
            text = api_response.text
            payload: Any
            if "json" in content_type or text.strip().startswith(("{", "[")):
                payload = api_response.json()
                (output_dir / f"{city}-{target_date}-api-{index:02d}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                payload = {"text_preview": text[:1000]}
            api_payloads.append({"url": api_url, "ok": True, "payload": payload})
        except Exception as exc:
            api_payloads.append({"url": api_url, "ok": False, "error": str(exc)})
    summary = {
        "benchmark_version": "polywx-benchmark-snapshot-v1",
        "disclaimer": DISCLAIMER,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "city": city,
        "target_date": target_date,
        "page_url": url,
        "api_url_count": len(api_urls),
        "api_urls": api_urls,
        "extracted": extract_polywx_summary(html, api_payloads),
    }
    (output_dir / f"{city}-{target_date}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def discover_api_urls(html: str, *, base_url: str) -> list[str]:
    candidates: set[str] = set()
    for match in re.findall(r"""["']([^"']*(?:/api/|api\.|forecast|metar|historical|deb)[^"']*)["']""", html, flags=re.I):
        url = match.replace("\\u0026", "&")
        if url.startswith("data:") or len(url) > 500:
            continue
        candidates.add(urljoin(base_url, url))
    return sorted(candidates)


def extract_polywx_summary(html: str, api_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", html)
    labels = {
        "has_daily_max_prediction": "Daily Max Prediction" in text or "DEB" in text,
        "has_probability_buckets": "Probability buckets" in text or "Gaussian" in text,
        "has_hourly_temperature": "Hourly Temperature" in text,
        "has_metar": "METAR" in text,
        "has_historical": "Historical" in text,
    }
    json_shapes = []
    for row in api_payloads:
        payload = row.get("payload")
        if isinstance(payload, dict):
            json_shapes.append({
                "url": row.get("url"),
                "keys": sorted(str(key) for key in payload.keys())[:50],
            })
        elif isinstance(payload, list):
            json_shapes.append({
                "url": row.get("url"),
                "list_length": len(payload),
                "first_keys": sorted(str(key) for key in payload[0].keys())[:50] if payload and isinstance(payload[0], dict) else [],
            })
    return {
        **labels,
        "api_payload_shapes": json_shapes,
        "note": "Field-level comparison should be done from saved JSON/HTML in audits only.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture one-time PolyWX benchmark evidence under audits/.")
    parser.add_argument("--city", action="append", required=True, help="PolyWX city key, e.g. chicago-kord")
    parser.add_argument("--date", required=True, help="Target date YYYY-MM-DD")
    parser.add_argument("--output-dir", default=f"audits/polywx-benchmark-{datetime.now().date().isoformat()}")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    summaries = [
        snapshot_city_date(city, args.date, output_dir=output_dir)
        for city in args.city
    ]
    manifest = {
        "benchmark_version": "polywx-benchmark-snapshot-v1",
        "disclaimer": DISCLAIMER,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "summaries": summaries,
    }
    (output_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
