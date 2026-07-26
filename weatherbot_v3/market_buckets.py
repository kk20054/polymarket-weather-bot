from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests

from .db import (
    connect,
    init_v3_db,
    insert_orderbooks,
    log_data_fetch,
    mark_market_bucket_orderbook_fetch_state,
    upsert_market_buckets,
    utc_now,
)
from .polymarket import quote_from_market_payload
from .stations import list_stations


PARSER_VERSION = "market-buckets-v1"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
POLYMARKET_BASE_URL = "https://polymarket.com"
ACTIVE_SYNC_VERSION = "market-buckets-active-weather-v1"
CACHED_ORDERBOOK_REFRESH_VERSION = "market-buckets-cached-orderbooks-v1"
MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}
MONTH_NAMES = {
    "01": "january",
    "02": "february",
    "03": "march",
    "04": "april",
    "05": "may",
    "06": "june",
    "07": "july",
    "08": "august",
    "09": "september",
    "10": "october",
    "11": "november",
    "12": "december",
}


class PolymarketWeatherMarketClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        self.session.headers.update({
            "User-Agent": "WeatherBot/5.5 (+local research; no trading)",
            "Accept": "application/json",
        })

    def get_event_by_slug(self, slug: str) -> dict[str, Any]:
        url = f"{GAMMA_BASE_URL}/events/slug/{slug}"
        response = self.session.get(url, timeout=(5, 12))
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            data["source_url"] = url
            return data
        return {}

    def get_orderbook(self, token_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{CLOB_BASE_URL}/book",
            params={"token_id": token_id},
            timeout=(5, 12),
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return {}
        data["snapshot_type"] = "clob"
        data["source_url"] = response.url
        return data

    def get_orderbooks(self, token_ids: list[str]) -> dict[str, dict[str, Any]]:
        unique = list(dict.fromkeys(str(token_id) for token_id in token_ids if str(token_id)))
        books: dict[str, dict[str, Any]] = {}
        for start in range(0, len(unique), 500):
            chunk = unique[start:start + 500]
            response = self.session.post(
                f"{CLOB_BASE_URL}/books",
                json=[{"token_id": token_id} for token_id in chunk],
                timeout=(5, 30),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("clob_books_response_not_list")
            for item in payload:
                if not isinstance(item, dict):
                    continue
                token_id = str(item.get("asset_id") or item.get("token_id") or "")
                if token_id:
                    item["snapshot_type"] = "clob"
                    item["source_url"] = f"{CLOB_BASE_URL}/books"
                    books[token_id] = item
        return books


def refresh_cached_market_bucket_orderbooks(
    *,
    targets_by_city: dict[str, list[str]] | None = None,
    limit: int = 5000,
    dry_run: bool = False,
    path=None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Refresh CLOB books for already-discovered weather buckets in batches.

    Event discovery is intentionally excluded from this hot path. A full Gamma
    scan requires hundreds of sequential event requests, while CLOB accepts up
    to 500 token ids in one `/books` request. Keeping these jobs separate lets
    five-minute quotes remain fresh even while discovery or model derivation is
    still running.
    """
    started = time.perf_counter()
    init_v3_db(path)
    target_pairs = {
        (str(city), str(target_date))
        for city, target_dates in (targets_by_city or {}).items()
        for target_date in target_dates
        if city and target_date
    }
    bounded_limit = max(1, min(int(limit or 5000), 10_000))
    with connect(path) as conn:
        db_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM market_buckets
                WHERE COALESCE(yes_token_id, '') != ''
                ORDER BY target_date ASC, city ASC, id ASC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        ]
    if target_pairs:
        db_rows = [
            row for row in db_rows
            if (str(row.get("city") or ""), str(row.get("target_date") or "")) in target_pairs
        ]
    if not db_rows:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_cached_market_buckets",
            "refresh_version": CACHED_ORDERBOOK_REFRESH_VERSION,
            "cached_buckets": 0,
            "tokens_requested": 0,
            "quotes_refreshed": 0,
            "quotes_missing": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    token_ids = list(dict.fromkeys(str(row.get("yes_token_id") or "") for row in db_rows))
    client = PolymarketWeatherMarketClient(session=session)
    try:
        books = client.get_orderbooks(token_ids)
    except Exception as exc:
        state_updates = 0
        if not dry_run:
            state_updates = _sqlite_write_with_retry(
                mark_market_bucket_orderbook_fetch_state,
                token_ids,
                path=path,
                state="fetch_failed",
                http_status=_http_status_from_exception(exc),
                error=str(exc)[:1000],
            )
        return {
            "ok": False,
            "refresh_version": CACHED_ORDERBOOK_REFRESH_VERSION,
            "cached_buckets": len(db_rows),
            "tokens_requested": len(token_ids),
            "quotes_refreshed": 0,
            "quotes_missing": len(token_ids),
            "orderbook_state_updates": state_updates,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    refreshed_rows: list[dict[str, Any]] = []
    orderbook_rows: list[tuple[str, dict[str, Any]]] = []
    missing: list[dict[str, str]] = []
    quote_times: list[str] = []
    for row in db_rows:
        token_id = str(row.get("yes_token_id") or "")
        book = books.get(token_id)
        if not book:
            missing.append({
                "city": str(row.get("city") or ""),
                "target_date": str(row.get("target_date") or ""),
                "market_id": str(row.get("market_id") or ""),
                "token_id": token_id,
            })
            continue
        cached_payload = _cached_market_payload(row)
        merged = _merge_orderbook_payload(cached_payload, book)
        refreshed = market_bucket_from_payload(
            merged,
            city=str(row.get("city") or ""),
            city_name=str(row.get("city_name") or ""),
            target_date=str(row.get("target_date") or ""),
            station_id=str(row.get("station_id") or ""),
        )
        refreshed["bucket_key"] = str(row.get("bucket_key") or refreshed.get("bucket_key") or "")
        refreshed_rows.append(refreshed)
        orderbook_rows.append(
            (str(row.get("market_id") or ""), _orderbook_payload_for_db(merged, book))
        )
        quote_time = str(refreshed.get("quote_timestamp") or "")
        if quote_time:
            quote_times.append(quote_time)

    if not dry_run:
        # SQLite permits one writer. Short transactions let minute-level
        # observation collectors make progress while a thousand-token market
        # refresh is being persisted.
        write_batch_size = 100
        for start in range(0, len(refreshed_rows), write_batch_size):
            _sqlite_write_with_retry(
                upsert_market_buckets,
                refreshed_rows[start:start + write_batch_size],
                path=path,
            )
        for start in range(0, len(orderbook_rows), write_batch_size):
            _sqlite_write_with_retry(
                insert_orderbooks,
                orderbook_rows[start:start + write_batch_size],
                path=path,
            )
        missing_token_ids = [str(item.get("token_id") or "") for item in missing]
        if missing_token_ids:
            _sqlite_write_with_retry(
                mark_market_bucket_orderbook_fetch_state,
                missing_token_ids,
                path=path,
                state="fetch_failed",
                http_status=200,
                error="token_missing_from_clob_batch_response",
            )
    return {
        "ok": bool(refreshed_rows) and not missing,
        "refresh_version": CACHED_ORDERBOOK_REFRESH_VERSION,
        "dry_run": dry_run,
        "cached_buckets": len(db_rows),
        "tokens_requested": len(token_ids),
        "quotes_refreshed": len(refreshed_rows),
        "quotes_missing": len(missing),
        "missing": missing[:20],
        "quote_timestamp_min": min(quote_times) if quote_times else None,
        "quote_timestamp_max": max(quote_times) if quote_times else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def _sqlite_write_with_retry(write_fn, payload, *, path=None, attempts: int = 4, **kwargs):
    """Retry a short SQLite batch when another collector owns the writer lock."""
    for attempt in range(max(1, attempts)):
        try:
            return write_fn(payload, path=path, **kwargs)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= attempts - 1:
                raise
            time.sleep(0.2 * (2 ** attempt))


def _http_status_from_exception(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    try:
        return int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        return None


def _cached_market_payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    payload = dict(raw) if isinstance(raw, dict) else {}
    field_map = {
        "id": "market_id",
        "eventSlug": "event_slug",
        "event_url": "event_url",
        "conditionId": "condition_id",
        "question": "question",
        "city": "city",
        "city_name": "city_name",
        "target_date": "target_date",
        "station_id": "station_id",
        "yes_token_id": "yes_token_id",
        "no_token_id": "no_token_id",
        "orderMinSize": "order_min_size",
        "orderPriceMinTickSize": "tick_size",
        "enableOrderBook": "enable_order_book",
        "volume": "volume",
        "liquidity": "liquidity",
        "negRisk": "neg_risk",
        "source_url": "source_url",
    }
    for payload_key, row_key in field_map.items():
        if payload.get(payload_key) in (None, "") and row.get(row_key) not in (None, ""):
            payload[payload_key] = row.get(row_key)
    return payload


def sync_active_weather_market_buckets(
    *,
    cities: list[str] | None = None,
    target_dates: list[str] | None = None,
    days: int = 3,
    limit_cities: int = 0,
    limit: int = 200,
    fetch_orderbooks: bool = True,
    dry_run: bool = False,
    session: requests.Session | None = None,
    sleep_seconds: float = 0.05,
) -> dict[str, Any]:
    started_all = time.perf_counter()
    client = PolymarketWeatherMarketClient(session=session)
    station_rows = _selected_stations(cities or [], limit_cities=limit_cities)
    dates = target_dates or _date_window(days)
    bounded_limit = max(1, min(int(limit or 200), 1000))
    results: list[dict[str, Any]] = []
    buckets: list[dict[str, Any]] = []
    stored = 0
    orderbook_ok = 0
    orderbook_failed = 0
    markets_seen = 0
    missing_events = 0
    failed_events = 0
    pending_orderbooks: list[tuple[str, dict[str, Any]]] = []

    for station in station_rows:
        if markets_seen >= bounded_limit:
            break
        city_key = str(station.get("city_key") or station.get("city") or "")
        city_name = str(station.get("city_name") or city_key)
        station_id = str(station.get("station_id") or station.get("icao_id") or "")
        city_slug = polymarket_city_slug(city_key, city_name)
        for target_date in dates:
            if markets_seen >= bounded_limit:
                break
            event_slug = highest_temperature_event_slug(city_slug, target_date)
            event_started = time.perf_counter()
            gamma_url = f"{GAMMA_BASE_URL}/events/slug/{event_slug}"
            try:
                event = client.get_event_by_slug(event_slug)
            except requests.HTTPError as exc:
                status_code = getattr(exc.response, "status_code", None)
                missing = status_code == 404
                missing_events += 1 if missing else 0
                failed_events += 0 if missing else 1
                result = {
                    "city": city_key,
                    "target_date": target_date,
                    "event_slug": event_slug,
                    "ok": False,
                    "status": "missing" if missing else "failed",
                    "error": str(exc),
                }
                results.append(result)
                _log_market_bucket_fetch(
                    result,
                    duration_ms=(time.perf_counter() - event_started) * 1000,
                    status="WARN" if missing else "ERROR",
                )
                continue
            except Exception as exc:
                failed_events += 1
                result = {
                    "city": city_key,
                    "target_date": target_date,
                    "event_slug": event_slug,
                    "ok": False,
                    "status": "failed",
                    "error": str(exc),
                }
                results.append(result)
                _log_market_bucket_fetch(result, duration_ms=(time.perf_counter() - event_started) * 1000, status="ERROR")
                continue

            event_markets = _active_weather_markets_from_event(event)
            event_rows = []
            event_orderbook_errors = []
            event_payloads: list[dict[str, Any]] = []
            for market in event_markets:
                if markets_seen >= bounded_limit:
                    break
                markets_seen += 1
                payload = _enrich_market_payload(
                    market,
                    event=event,
                    city_key=city_key,
                    city_name=city_name,
                    station_id=station_id,
                    target_date=target_date,
                    gamma_url=gamma_url,
                )
                event_payloads.append(payload)

            book_map: dict[str, dict[str, Any]] = {}
            batch_error = ""
            token_ids = [str(payload.get("yes_token_id") or "") for payload in event_payloads if payload.get("yes_token_id")]
            if fetch_orderbooks and token_ids:
                try:
                    book_map = client.get_orderbooks(token_ids)
                except Exception as exc:
                    batch_error = str(exc)

            for payload in event_payloads:
                token_id = str(payload.get("yes_token_id") or "")
                if fetch_orderbooks and token_id:
                    book_payload = book_map.get(token_id)
                    if book_payload:
                        payload = _merge_orderbook_payload(payload, book_payload)
                        if not dry_run:
                            pending_orderbooks.append(
                                (str(payload.get("id") or ""), _orderbook_payload_for_db(payload, book_payload))
                            )
                        orderbook_ok += 1
                    else:
                        orderbook_failed += 1
                        event_orderbook_errors.append({
                            "market_id": str(payload.get("id") or ""),
                            "token_id": token_id,
                            "error": batch_error or "clob_batch_book_missing",
                        })
                        payload["orderbook_error"] = batch_error or "clob_batch_book_missing"
                row = market_bucket_from_payload(
                    payload,
                    city=city_key,
                    city_name=city_name,
                    target_date=target_date,
                    station_id=station_id,
                )
                event_rows.append(row)
                buckets.append(row)
            result = {
                "city": city_key,
                "city_name": city_name,
                "station_id": station_id,
                "target_date": target_date,
                "event_slug": event_slug,
                "event_url": f"{POLYMARKET_BASE_URL}/event/{event_slug}",
                "source_url": gamma_url,
                "ok": bool(event_rows),
                "status": "ok" if event_rows else "empty",
                "markets": len(event_markets),
                "stored": 0 if dry_run else len(event_rows),
                "matched": sum(1 for row in event_rows if row.get("strict_match_status") == "matched"),
                "blocked": sum(1 for row in event_rows if row.get("strict_match_status") != "matched"),
                "orderbook_errors": event_orderbook_errors[:5],
            }
            results.append(result)
            _log_market_bucket_fetch(
                result,
                duration_ms=(time.perf_counter() - event_started) * 1000,
                status="OK" if event_rows else "WARN",
            )
            if sleep_seconds:
                time.sleep(max(0.0, float(sleep_seconds)))

    if not dry_run:
        stored = len(upsert_market_buckets(buckets))
        insert_orderbooks(pending_orderbooks)

    return {
        "ok": failed_events == 0 and bool(buckets),
        "sync_version": ACTIVE_SYNC_VERSION,
        "dry_run": dry_run,
        "source": "polymarket_gamma+clob",
        "cities": [row.get("city_key") or row.get("city") for row in station_rows],
        "target_dates": dates,
        "requested_events": len(station_rows) * len(dates),
        "events_found": sum(1 for row in results if row.get("ok")),
        "events_missing": missing_events,
        "events_failed": failed_events,
        "markets_seen": markets_seen,
        "stored": stored,
        "matched": sum(1 for row in buckets if row.get("strict_match_status") == "matched"),
        "blocked": sum(1 for row in buckets if row.get("strict_match_status") != "matched"),
        "orderbook_ok": orderbook_ok,
        "orderbook_failed": orderbook_failed,
        "elapsed_ms": round((time.perf_counter() - started_all) * 1000),
        "results": results,
        "buckets": [_bucket_preview(row) for row in buckets[: min(len(buckets), 50)]],
    }


def ingest_market_buckets(
    payloads: list[dict[str, Any]] | dict[str, Any],
    *,
    city: str = "",
    city_name: str = "",
    target_date: str = "",
    station_id: str = "",
) -> dict[str, Any]:
    markets = _market_payloads(payloads)
    bucket_rows = []
    for market in markets:
        row = market_bucket_from_payload(
            market,
            city=city,
            city_name=city_name,
            target_date=target_date,
            station_id=station_id,
        )
        bucket_rows.append(row)
    upsert_market_buckets(bucket_rows)
    return {
        "ok": True,
        "parser_version": PARSER_VERSION,
        "requested": len(markets),
        "stored": len(bucket_rows),
        "matched": sum(1 for row in bucket_rows if row.get("strict_match_status") == "matched"),
        "blocked": sum(1 for row in bucket_rows if row.get("strict_match_status") != "matched"),
        "buckets": bucket_rows,
    }


def market_bucket_from_payload(
    payload: dict[str, Any],
    *,
    city: str = "",
    city_name: str = "",
    target_date: str = "",
    station_id: str = "",
) -> dict[str, Any]:
    question = str(payload.get("question") or payload.get("title") or "")
    parsed = parse_temperature_bucket(question)
    inferred_city = city or str(payload.get("city") or "")
    if not inferred_city:
        inferred_city = parse_city_from_question(question)
    inferred_date = target_date or str(payload.get("target_date") or payload.get("targetDate") or "")
    if not inferred_date:
        inferred_date = parse_date_from_question(question)

    quote = quote_from_market_payload(payload, default_order_min_size=0.0, default_tick_size=0.0)
    prices = _parse_list(payload.get("outcomePrices"))
    tokens = _parse_list(payload.get("clobTokenIds"))
    outcomes = _parse_list(payload.get("outcomes"))
    yes_token_id = str(payload.get("yes_token_id") or quote.yes_token_id or (tokens[0] if tokens else ""))
    no_token_id = str(payload.get("no_token_id") or (tokens[1] if len(tokens) > 1 else ""))
    outcome_name = _outcome_name(outcomes)
    has_clob_book = quote.book_source == "clob"
    orderbook_error = str(payload.get("orderbook_error") or "")
    if orderbook_error or quote.book_source == "gamma_fallback":
        orderbook_state = "fetch_failed"
        orderbook_checked_at = utc_now()
        orderbook_last_success_at = ""
        orderbook_http_status = payload.get("orderbook_http_status")
    elif has_clob_book:
        orderbook_checked_at = utc_now()
        orderbook_last_success_at = orderbook_checked_at
        orderbook_http_status = int(payload.get("orderbook_http_status") or 200)
        if quote.bids and quote.asks:
            orderbook_state = "two_sided"
        elif quote.bids or quote.asks:
            orderbook_state = "side_absent"
        else:
            orderbook_state = "book_absent"
    else:
        orderbook_state = ""
        orderbook_checked_at = ""
        orderbook_last_success_at = ""
        orderbook_http_status = None
    best_bid = quote.best_bid if quote.best_bid > 0 else (None if has_clob_book else _num(payload.get("bestBid")))
    best_ask = quote.best_ask if quote.best_ask > 0 else (None if has_clob_book else _num(payload.get("bestAsk")))
    price = best_ask or (_num(prices[0]) if prices else None)
    spread = quote.spread if quote.spread > 0 else (
        round(best_ask - best_bid, 6) if best_ask is not None and best_bid is not None else None
    )

    row = {
        "event_slug": str(payload.get("eventSlug") or payload.get("event_slug") or payload.get("event") or ""),
        "event_url": str(payload.get("event_url") or payload.get("eventUrl") or _event_url(payload)),
        "market_id": str(payload.get("id") or payload.get("market_id") or ""),
        "condition_id": str(payload.get("conditionId") or payload.get("condition_id") or ""),
        "question": question,
        "city": inferred_city,
        "city_name": city_name or str(payload.get("city_name") or inferred_city),
        "target_date": inferred_date,
        "station_id": station_id or str(payload.get("station_id") or ""),
        "unit": parsed.get("unit") or str(payload.get("unit") or ""),
        "bucket_label": parsed.get("label") or str(payload.get("bucket_label") or ""),
        "bucket_direction": parsed.get("direction") or "",
        "bucket_low": parsed.get("low"),
        "bucket_high": parsed.get("high"),
        "outcome_name": outcome_name,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "token_id": yes_token_id,
        "token_side": "YES",
        "outcome_index": 0,
        "price": price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "volume": _num(payload.get("volume")),
        "liquidity": _num(payload.get("liquidity")),
        "order_min_size": quote.order_min_size or _num(payload.get("orderMinSize")),
        "tick_size": quote.tick_size or _num(payload.get("orderPriceMinTickSize")),
        "neg_risk": _bool(payload.get("negRisk", payload.get("neg_risk", False))),
        "enable_order_book": _bool(payload.get("enableOrderBook", payload.get("enable_order_book", True))),
        "quote_timestamp": quote.quote_timestamp or str(payload.get("quote_timestamp") or payload.get("timestamp") or ""),
        "orderbook_snapshot_key": str(payload.get("snapshot_key") or ""),
        "orderbook_source": quote.book_source,
        "orderbook_state": orderbook_state,
        "orderbook_checked_at": orderbook_checked_at,
        "orderbook_last_success_at": orderbook_last_success_at,
        "orderbook_http_status": orderbook_http_status,
        "orderbook_error": orderbook_error,
        "bid_depth": round(sum(level["size"] for level in quote.bids), 6) if quote.bids else _num(payload.get("bid_depth")),
        "ask_depth": round(sum(level["size"] for level in quote.asks), 6) if quote.asks else _num(payload.get("ask_depth")),
        "source_url": str(payload.get("source_url") or ""),
        "raw_response_hash": str(payload.get("raw_response_hash") or ""),
        "parser_version": PARSER_VERSION,
        "raw_json": payload,
    }
    reasons = strict_match_reasons(row)
    row["strict_match_status"] = "matched" if not reasons else "blocked"
    row["strict_match_reasons"] = reasons
    return row


def parse_temperature_bucket(question: str) -> dict[str, Any]:
    text = _normalize_question(question)
    unit = parse_unit(text)
    between = re.search(
        r"between\s+(-?\d+(?:\.\d+)?)\s*(?:-|to|and)\s*(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])",
        text,
    )
    if between:
        low = float(between.group(1))
        high = float(between.group(2))
        unit = between.group(3).upper()
        return {
            "direction": "range",
            "low": min(low, high),
            "high": max(low, high),
            "unit": unit,
            "label": f"{min(low, high):g}-{max(low, high):g}{unit}",
        }
    lower = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])?\s*(?:or\s+)?(?:below|lower|under|or below)", text)
    if lower:
        value = float(lower.group(1))
        unit = (lower.group(2) or unit or "").upper()
        return {"direction": "or_below", "low": -999.0, "high": value, "unit": unit, "label": f"{value:g}{unit} or below"}
    upper = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])?\s*(?:or\s+)?(?:above|higher|over|or above)", text)
    if upper:
        value = float(upper.group(1))
        unit = (upper.group(2) or unit or "").upper()
        return {"direction": "or_above", "low": value, "high": 999.0, "unit": unit, "label": f"{value:g}{unit} or above"}
    exact = re.search(r"\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:°?\s*)?([cf])\b", text)
    if exact:
        value = float(exact.group(1))
        unit = exact.group(2).upper()
        return {"direction": "exact", "low": value, "high": value, "unit": unit, "label": f"{value:g}{unit}"}
    return {"direction": "", "low": None, "high": None, "unit": unit or "", "label": ""}


def parse_city_from_question(question: str) -> str:
    match = re.search(r"highest temperature in\s+(.+?)\s+be\b", question, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def parse_date_from_question(question: str) -> str:
    match = re.search(
        r"\bon\s+("
        + "|".join(MONTHS)
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    year = match.group(3) or str(datetime.utcnow().year)
    return f"{year}-{MONTHS[match.group(1).lower()]}-{int(match.group(2)):02d}"


def parse_unit(question: str) -> str:
    match = re.search(r"°?\s*([cf])\b", question, flags=re.IGNORECASE)
    return match.group(1).upper() if match else ""


def strict_match_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    required = {
        "city": "city_missing",
        "target_date": "target_date_missing",
        "unit": "unit_missing",
        "market_id": "market_id_missing",
        "yes_token_id": "yes_token_missing",
    }
    for field, reason in required.items():
        if not row.get(field):
            reasons.append(reason)
    if row.get("bucket_low") is None and row.get("bucket_high") is None:
        reasons.append("temperature_bucket_unparsed")
    if row.get("tick_size") in (None, 0):
        reasons.append("tick_size_missing")
    if row.get("order_min_size") in (None, 0):
        reasons.append("order_min_size_missing")
    if row.get("enable_order_book") is False:
        reasons.append("orderbook_disabled")
    if row.get("price") is None and row.get("best_ask") is None:
        reasons.append("quote_price_missing")
    best_bid = _num(row.get("best_bid"))
    best_ask = _num(row.get("best_ask"))
    if best_bid is not None and not (0 < best_bid < 1):
        reasons.append("invalid_best_bid")
    if best_ask is not None and not (0 < best_ask < 1):
        reasons.append("invalid_best_ask")
    if best_bid is not None and best_ask is not None and best_bid > best_ask:
        reasons.append("crossed_orderbook")
    return reasons


def _market_payloads(payloads: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payloads, list):
        return [item for item in payloads if isinstance(item, dict)]
    if not isinstance(payloads, dict):
        return []
    for key in ("markets", "data", "items"):
        value = payloads.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payloads]


def _normalize_question(question: str) -> str:
    return (
        str(question or "")
        .replace("\u00c2", "")
        .replace("\u00b0", "")
        .replace("\u2109", "F")
        .replace("\u2103", "C")
        .replace("–", "-")
        .replace("—", "-")
        .replace("℉", "F")
        .replace("℃", "C")
        .lower()
    )


def _parse_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if raw in (None, ""):
        return []
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _outcome_name(outcomes: list[Any]) -> str:
    if not outcomes:
        return "Yes"
    first = outcomes[0]
    if isinstance(first, dict):
        return str(first.get("name") or first.get("outcome") or "Yes")
    return str(first)


def _event_url(payload: dict[str, Any]) -> str:
    slug = payload.get("eventSlug") or payload.get("event_slug")
    return f"https://polymarket.com/event/{slug}" if slug else ""


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def polymarket_city_slug(city_key: str, city_name: str = "") -> str:
    key = _slugify(city_key)
    aliases = {
        "new-york": "nyc",
        "new-york-city": "nyc",
    }
    if key in aliases:
        return aliases[key]
    if key:
        return key
    name_key = _slugify(city_name)
    return aliases.get(name_key, name_key)


def highest_temperature_event_slug(city_slug: str, target_date: str) -> str:
    parsed = date.fromisoformat(str(target_date))
    month = MONTH_NAMES[f"{parsed.month:02d}"]
    return f"highest-temperature-in-{city_slug}-on-{month}-{parsed.day}-{parsed.year}"


def _date_window(days: int) -> list[str]:
    count = max(1, min(int(days or 3), 14))
    today = date.today()
    return [(today + timedelta(days=offset)).isoformat() for offset in range(count)]


def _selected_stations(cities: list[str], *, limit_cities: int) -> list[dict[str, Any]]:
    requested = {_slugify(city) for city in cities if city}
    stations = list_stations()
    if requested:
        rows = [
            row for row in stations
            if _slugify(str(row.get("city_key") or row.get("city") or "")) in requested
            or _slugify(str(row.get("city_name") or "")) in requested
        ]
    else:
        rows = [row for row in stations if bool(row.get("enabled"))]
    limit = int(limit_cities or 0)
    return rows if limit <= 0 else rows[: max(1, min(limit, 500))]


def _active_weather_markets_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    rows = []
    for market in markets:
        if not isinstance(market, dict):
            continue
        question = str(market.get("question") or "")
        if "highest temperature" not in question.lower():
            continue
        if market.get("closed") is True or market.get("active") is False:
            continue
        rows.append(market)
    return rows


def _enrich_market_payload(
    market: dict[str, Any],
    *,
    event: dict[str, Any],
    city_key: str,
    city_name: str,
    station_id: str,
    target_date: str,
    gamma_url: str,
) -> dict[str, Any]:
    event_slug = str(event.get("slug") or "")
    tokens = _parse_list(market.get("clobTokenIds"))
    payload = dict(market)
    payload.update({
        "eventSlug": event_slug,
        "event_url": f"{POLYMARKET_BASE_URL}/event/{event_slug}" if event_slug else "",
        "city": city_key,
        "city_name": city_name,
        "target_date": target_date,
        "station_id": station_id,
        "source_url": gamma_url,
        "yes_token_id": str(tokens[0]) if tokens else str(market.get("yes_token_id") or ""),
        "no_token_id": str(tokens[1]) if len(tokens) > 1 else str(market.get("no_token_id") or ""),
        "raw_response_hash": _hash_json(market),
    })
    return payload


def _merge_orderbook_payload(market_payload: dict[str, Any], book_payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(market_payload)
    merged.update(book_payload)
    yes_token_id = str(market_payload.get("yes_token_id") or book_payload.get("asset_id") or "")
    raw_hash = _hash_json({"market": market_payload, "book": book_payload})
    merged.update({
        "id": market_payload.get("id"),
        "conditionId": market_payload.get("conditionId"),
        "question": market_payload.get("question"),
        "outcomes": market_payload.get("outcomes"),
        "outcomePrices": market_payload.get("outcomePrices"),
        "clobTokenIds": market_payload.get("clobTokenIds"),
        "eventSlug": market_payload.get("eventSlug"),
        "event_url": market_payload.get("event_url"),
        "city": market_payload.get("city"),
        "city_name": market_payload.get("city_name"),
        "target_date": market_payload.get("target_date"),
        "station_id": market_payload.get("station_id"),
        "yes_token_id": yes_token_id,
        "no_token_id": market_payload.get("no_token_id"),
        "orderMinSize": market_payload.get("orderMinSize") or book_payload.get("min_order_size"),
        "orderPriceMinTickSize": market_payload.get("orderPriceMinTickSize") or book_payload.get("tick_size"),
        "enableOrderBook": market_payload.get("enableOrderBook", True),
        "volume": market_payload.get("volume"),
        "liquidity": market_payload.get("liquidity"),
        "negRisk": market_payload.get("negRisk", book_payload.get("neg_risk", False)),
        "snapshot_key": f"{yes_token_id}:{book_payload.get('hash') or raw_hash}",
        "source_url": market_payload.get("source_url"),
        "raw_response_hash": raw_hash,
    })
    return merged


def _orderbook_payload_for_db(market_payload: dict[str, Any], book_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(market_payload)
    payload["source_url"] = book_payload.get("source_url") or ""
    payload["snapshot_type"] = book_payload.get("snapshot_type") or "clob"
    payload["asset_id"] = book_payload.get("asset_id") or market_payload.get("yes_token_id")
    return payload


def _log_market_bucket_fetch(result: dict[str, Any], *, duration_ms: float, status: str) -> None:
    log_data_fetch(
        source="polymarket_gamma",
        stage="refresh_market_buckets",
        status=status,
        city=str(result.get("city") or ""),
        target_date=str(result.get("target_date") or ""),
        duration_ms=round(duration_ms, 2),
        message=str(result.get("status") or ""),
        details=result,
    )


def _bucket_preview(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_id": row.get("market_id"),
        "city": row.get("city"),
        "target_date": row.get("target_date"),
        "bucket_label": row.get("bucket_label"),
        "bucket_direction": row.get("bucket_direction"),
        "best_bid": row.get("best_bid"),
        "best_ask": row.get("best_ask"),
        "spread": row.get("spread"),
        "yes_token_id": row.get("yes_token_id"),
        "event_url": row.get("event_url"),
        "strict_match_status": row.get("strict_match_status"),
        "strict_match_reasons": row.get("strict_match_reasons"),
    }


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
