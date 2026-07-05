from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


POLYWX_BASE_URL = "https://www.polywx.xyz/"
POLYWX_API_BASE_URL = "https://api.weather.polywx.xyz"
POLYWX_API_ENDPOINTS = (
    "forecast",
    "metar",
    "historical",
    "pws",
    "peak-marker",
    "prediction",
)
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
    headers = polywx_headers(url)
    page_error = ""
    try:
        response = client.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        html = response.text
        (output_dir / f"{city}-{target_date}.html").write_text(html, encoding="utf-8")
        api_urls = discover_api_urls(html, base_url=response.url or url)
    except Exception as exc:
        html = ""
        api_urls = []
        page_error = str(exc)
    api_payloads: list[dict[str, Any]] = []
    for index, api_url in enumerate(api_urls[:25]):
        try:
            api_response = client.get(api_url, headers=headers, timeout=30)
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
    endpoint_payloads = fetch_known_api_payloads(
        city,
        target_date,
        output_dir=output_dir,
        session=client,
        referer=url,
    )
    summary = {
        "benchmark_version": "polywx-benchmark-snapshot-v1",
        "disclaimer": DISCLAIMER,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "city": city,
        "target_date": target_date,
        "page_url": url,
        "page_error": page_error,
        "api_url_count": len(api_urls),
        "api_urls": api_urls,
        "known_api_payloads": endpoint_payloads,
        "extracted": extract_polywx_summary(html, api_payloads, endpoint_payloads),
    }
    (output_dir / f"{city}-{target_date}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def polywx_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126 Safari/537.36 WeatherBot/benchmark-snapshot"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Origin": "https://www.polywx.xyz",
        "Referer": referer,
    }


def fetch_known_api_payloads(
    city: str,
    target_date: str,
    *,
    output_dir: Path,
    session: requests.Session,
    referer: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    headers = polywx_headers(referer)
    for endpoint in POLYWX_API_ENDPOINTS:
        url = f"{POLYWX_API_BASE_URL}/api/{endpoint}?city={city}&date={target_date}"
        try:
            response = session.get(url, headers=headers, timeout=30)
            text = response.text
            record: dict[str, Any] = {
                "endpoint": endpoint,
                "url": url,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "ok": response.ok,
            }
            if response.ok and (text.strip().startswith(("{", "[")) or "json" in record["content_type"]):
                payload = response.json()
                (output_dir / f"{city}-{target_date}-{endpoint}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                record["shape"] = payload_shape(payload)
            else:
                record["error"] = text[:500] or response.reason
            payloads.append(record)
        except Exception as exc:
            payloads.append({
                "endpoint": endpoint,
                "url": url,
                "ok": False,
                "error": str(exc),
            })
    return payloads


def payload_shape(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return {
            "type": "list",
            "rows": len(payload),
            "first_keys": sorted(str(key) for key in payload[0].keys()) if payload and isinstance(payload[0], dict) else [],
        }
    if isinstance(payload, dict):
        return {"type": "dict", "keys": sorted(str(key) for key in payload.keys())}
    return {"type": type(payload).__name__}


def discover_api_urls(html: str, *, base_url: str) -> list[str]:
    candidates: set[str] = set()
    for match in re.findall(r"""["']([^"']*(?:/api/|api\.|forecast|metar|historical|deb)[^"']*)["']""", html, flags=re.I):
        url = match.replace("\\u0026", "&")
        if url.startswith("data:") or len(url) > 500:
            continue
        candidates.add(urljoin(base_url, url))
    return sorted(candidates)


def extract_polywx_summary(
    html: str,
    api_payloads: list[dict[str, Any]],
    endpoint_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        "known_api_payloads": endpoint_payloads or [],
        "note": "Field-level comparison should be done from saved JSON/HTML in audits only.",
    }


def date_range(start: str, end: str) -> list[str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if end_date < start_date:
        raise ValueError("--end must be >= --start")
    days = []
    current = start_date
    while current <= end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture one-time PolyWX benchmark evidence under audits/.")
    parser.add_argument("--city", action="append", required=True, help="PolyWX city key, e.g. chicago-kord")
    parser.add_argument("--date", help="Target date YYYY-MM-DD")
    parser.add_argument("--start", help="Start date YYYY-MM-DD for date range capture")
    parser.add_argument("--end", help="End date YYYY-MM-DD for date range capture")
    parser.add_argument("--output-dir", default=f"audits/polywx-benchmark-{datetime.now().date().isoformat()}")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    if args.start or args.end:
        if not args.start or not args.end:
            raise SystemExit("--start and --end must be provided together")
        dates = date_range(args.start, args.end)
    elif args.date:
        dates = [args.date]
    else:
        raise SystemExit("--date or --start/--end is required")
    summaries = []
    for city in args.city:
        for target_date in dates:
            summaries.append(snapshot_city_date(city, target_date, output_dir=output_dir))
            time.sleep(0.35)
    manifest = {
        "benchmark_version": "polywx-benchmark-snapshot-v1",
        "disclaimer": DISCLAIMER,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "summaries": summaries,
    }
    (output_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
