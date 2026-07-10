from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .db import connect, dump_json, init_v3_db, log_data_fetch, utc_now
from .market_buckets import highest_temperature_event_slug, polymarket_city_slug
from .polymarket import quote_from_market_payload
from .registry import get_city_profile
from .stations import list_stations, sync_station_registry


GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
CLOB_BASE_URL = "https://clob.polymarket.com"
PARSER_VERSION = "polymarket-gamma-structured-v1"
ASIAN_CITY_KEYS = (
    "shanghai",
    "beijing",
    "hong-kong",
    "tokyo",
    "seoul",
    "taipei",
    "wuhan",
    "qingdao",
    "shenzhen",
    "singapore",
)


class GammaClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        if hasattr(self.session, "trust_env"):
            self.session.trust_env = False
        self.session.headers.update({
            "User-Agent": "WeatherBot/Gamma-structured (local research)",
            "Accept": "application/json",
        })

    def event_by_slug(self, slug: str) -> dict[str, Any]:
        url = f"{GAMMA_BASE_URL}/events/slug/{slug}"
        response = self.session.get(url, timeout=(5, 15))
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            payload["source_url"] = url
            return payload
        return {}

    def orderbook(self, token_id: str) -> dict[str, Any]:
        response = self.session.get(f"{CLOB_BASE_URL}/book", params={"token_id": token_id}, timeout=(5, 15))
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            payload["source_url"] = str(getattr(response, "url", "") or "")
            return payload
        return {}

    def orderbooks(self, token_ids: list[str]) -> dict[str, dict[str, Any]]:
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
                    item["source_url"] = f"{CLOB_BASE_URL}/books"
                    books[token_id] = item
        return books


def sync_asian_weather_markets(
    *,
    cities: list[str] | None = None,
    target_dates: list[str] | None = None,
    days: int = 3,
    fetch_orderbooks: bool = True,
    dry_run: bool = False,
    session: requests.Session | None = None,
    path: Path | None = None,
    sleep_seconds: float = 0.05,
) -> dict[str, Any]:
    init_v3_db(path)
    sync_station_registry(path)
    client = GammaClient(session=session)
    selected = _selected_station_rows(cities, path=path)
    dates = target_dates or [(date.today() + timedelta(days=offset)).isoformat() for offset in range(max(1, int(days or 3)))]
    events_seen = 0
    events_stored = 0
    markets_stored = 0
    orderbooks_stored = 0
    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    pending_events: list[dict[str, Any]] = []
    pending_markets: list[dict[str, Any]] = []
    pending_orderbooks: list[tuple[dict[str, Any], str]] = []
    started_at = utc_now()
    started_perf = time.perf_counter()
    for station in selected:
        city_key = str(station.get("city_key") or station.get("city") or "")
        city_name = str(station.get("city_name") or city_key)
        city_slug = polymarket_city_slug(city_key, city_name)
        for target in dates:
            slug = highest_temperature_event_slug(city_slug, target)
            try:
                event = client.event_by_slug(slug)
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) == 404:
                    continue
                failures.append({"city": city_key, "target_date": target, "slug": slug, "error": str(exc)})
                continue
            except Exception as exc:
                failures.append({"city": city_key, "target_date": target, "slug": slug, "error": str(exc)})
                continue
            if not _event_active(event):
                continue
            events_seen += 1
            event_row, market_rows = event_to_rows(event, station, target)
            if not dry_run:
                pending_events.append(event_row)
                for row in market_rows:
                    pending_markets.append(row)
                    token_id = str(row.get("outcome_yes_token_id") or "")
                    if fetch_orderbooks and token_id:
                        pending_orderbooks.append((row, token_id))
            results.append({
                "city": city_key,
                "target_date": target,
                "event_id": event_row["event_id"],
                "slug": event_row["slug"],
                "markets": len(market_rows),
                "resolution_station": event_row.get("resolution_station"),
            })
            if sleep_seconds:
                time.sleep(max(0.0, float(sleep_seconds)))
    if not dry_run and (pending_events or pending_markets):
        init_v3_db(path)
        with connect(path) as conn:
            for event_row in pending_events:
                upsert_polymarket_event(event_row, path=path, _connection=conn)
            for market_row in pending_markets:
                upsert_polymarket_market(market_row, path=path, _connection=conn)
        events_stored = len(pending_events)
        markets_stored = len(pending_markets)
    if fetch_orderbooks and pending_orderbooks and not dry_run:
        try:
            books = client.orderbooks([token_id for _, token_id in pending_orderbooks])
            orderbook_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for market_row, token_id in pending_orderbooks:
                book = books.get(token_id)
                if not book:
                    failures.append({
                        "city": market_row.get("city"),
                        "market_id": market_row.get("market_id"),
                        "token_id": token_id,
                        "error": "clob_batch_book_missing",
                    })
                    continue
                orderbook_rows.append((market_row, book))
            if orderbook_rows:
                init_v3_db(path)
                with connect(path) as conn:
                    for market_row, book in orderbook_rows:
                        upsert_polymarket_orderbook(market_row, book, path=path, _connection=conn)
                orderbooks_stored = len(orderbook_rows)
        except Exception as exc:
            failures.append({
                "stage": "clob_batch_books",
                "token_count": len(pending_orderbooks),
                "error": str(exc),
            })
    duration_ms = round((time.perf_counter() - started_perf) * 1000, 2)
    log_data_fetch(
        source="polymarket_gamma",
        stage="structured_market_sync",
        status="OK" if events_seen and not failures else ("WARN" if events_seen else "ERROR"),
        duration_ms=duration_ms,
        message="Polymarket Gamma structured market sync completed",
        details={
            "cities": [row.get("city_key") for row in selected],
            "target_dates": dates,
            "events_seen": events_seen,
            "events_stored": events_stored,
            "markets_stored": markets_stored,
            "orderbooks_stored": orderbooks_stored,
            "failures": failures[:20],
            "dry_run": dry_run,
        },
        started_at=started_at,
        finished_at=utc_now(),
    )
    return {
        "ok": events_seen > 0 and not failures,
        "parser_version": PARSER_VERSION,
        "dry_run": dry_run,
        "events_seen": events_seen,
        "events_stored": events_stored,
        "markets_stored": markets_stored,
        "orderbooks_stored": orderbooks_stored,
        "failures": failures,
        "results": results,
        "duration_ms": duration_ms,
    }


def event_to_rows(event: dict[str, Any], station: dict[str, Any], target_date: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    markets = [market for market in (event.get("markets") or []) if isinstance(market, dict)]
    active_markets = [market for market in markets if market.get("closed") is not True and market.get("active") is not False]
    city = str(station.get("city_key") or station.get("city") or "")
    unit = str(station.get("settlement_unit") or station.get("unit") or "C")
    source_url = str(event.get("resolutionSource") or "")
    first_market = active_markets[0] if active_markets else {}
    if not source_url:
        source_url = str(first_market.get("resolutionSource") or "")
    resolution_station = _station_from_source_url(source_url) or str(station.get("settlement_station_id") or station.get("station_id") or "")
    resolution_source = _resolution_source(source_url, first_market.get("description") or event.get("description") or "")
    market_rows = []
    bucket_defs = []
    for market in active_markets:
        boundary = parse_celsius_bucket_boundary(market)
        quote = quote_from_market_payload(market, default_order_min_size=0.0, default_tick_size=0.0)
        tokens = _json_list(market.get("clobTokenIds"))
        row = {
            "market_id": str(market.get("id") or ""),
            "event_id": str(event.get("id") or ""),
            "event_slug": str(event.get("slug") or ""),
            "market_slug": str(market.get("slug") or ""),
            "city": city,
            "target_date": target_date,
            "bucket_label": boundary["label"],
            "bucket_lower_c": boundary["lower_c"],
            "bucket_upper_c": boundary["upper_c"],
            "is_tail": bool(boundary["is_tail"]),
            "outcome_yes_token_id": str(quote.yes_token_id or (tokens[0] if tokens else "")),
            "outcome_no_token_id": str(tokens[1] if len(tokens) > 1 else ""),
            "order_min_size": quote.order_min_size or _float(market.get("orderMinSize")),
            "tick_size": quote.tick_size or _float(market.get("orderPriceMinTickSize")),
            "enable_order_book": bool(market.get("enableOrderBook", True)),
            "raw_json": market,
        }
        market_rows.append(row)
        bucket_defs.append(boundary)
    event_row = {
        "event_id": str(event.get("id") or ""),
        "slug": str(event.get("slug") or ""),
        "city": city,
        "target_date": target_date,
        "resolution_station": resolution_station.upper(),
        "resolution_source": resolution_source,
        "resolution_source_url": source_url,
        "settlement_unit": unit,
        "volume_24h": _float(event.get("volume24hr") or event.get("volume24h") or event.get("volume")),
        "open_interest": _float(event.get("openInterest") or event.get("open_interest")),
        "buckets_json": bucket_defs,
        "raw_json": event,
    }
    return event_row, market_rows


def parse_celsius_bucket_boundary(market: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(market, str):
        text = market
        slug = market
    else:
        text = " ".join(str(market.get(key) or "") for key in ("question", "slug", "conditionId"))
        slug = str(market.get("slug") or "")
    clean = _normalize_degree_text(text)
    unit = "C" if re.search(r"\bc\b|celsius", clean) or re.search(r"\d+c\b", slug.lower()) else ""
    degree_c = r"(?:\s*[^a-z0-9-]*\s*c)?"
    degree_c_required = r"\s*[^a-z0-9-]*\s*c"
    slug_tail_below = re.search(r"-(\d+)c(?:or)?(?:below|lower)(?:$|-)", slug.lower())
    if slug_tail_below:
        value = int(slug_tail_below.group(1))
        return {"label": f"{value}C or below", "lower_c": None, "upper_c": value + 1, "is_tail": True, "unit": "C"}
    slug_tail_above = re.search(r"-(\d+)c(?:or)?(?:above|higher)(?:$|-)", slug.lower())
    if slug_tail_above:
        value = int(slug_tail_above.group(1))
        return {"label": f"{value}C or above", "lower_c": value, "upper_c": None, "is_tail": True, "unit": "C"}
    below = re.search(rf"(-?\d+){degree_c}\s*(?:or\s+below|below|orbelow|or\s+lower|lower|orlower)", clean)
    if below:
        value = int(below.group(1))
        return {"label": f"{value}C or below", "lower_c": None, "upper_c": value + 1, "is_tail": True, "unit": unit or "C"}
    above = re.search(rf"(-?\d+){degree_c}\s*(?:or\s+above|above|orabove|or\s+higher|higher|orhigher)", clean)
    if above:
        value = int(above.group(1))
        return {"label": f"{value}C or above", "lower_c": value, "upper_c": None, "is_tail": True, "unit": unit or "C"}
    slug_exact = re.search(r"-(\d+)c(?:$|-)", slug.lower())
    if slug_exact:
        value = int(slug_exact.group(1))
        return {"label": f"{value}C", "lower_c": value, "upper_c": value + 1, "is_tail": False, "unit": "C"}
    exact = re.search(rf"\bbe\s+(-?\d+){degree_c_required}\b", clean)
    if exact:
        value = int(exact.group(1))
        return {"label": f"{value}C", "lower_c": value, "upper_c": value + 1, "is_tail": False, "unit": unit or "C"}
    return {"label": "", "lower_c": None, "upper_c": None, "is_tail": False, "unit": unit}


def upsert_polymarket_event(
    row: dict[str, Any],
    *,
    path: Path | None = None,
    _connection=None,
) -> dict[str, Any]:
    if _connection is None:
        init_v3_db(path)
    now = utc_now()
    connection_context = connect(path) if _connection is None else nullcontext(_connection)
    with connection_context as conn:
        conn.execute(
            """
            INSERT INTO polymarket_events (
                event_id, slug, city, target_date, resolution_station,
                resolution_source, resolution_source_url, settlement_unit,
                volume_24h, open_interest, buckets_json, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                slug=excluded.slug,
                city=excluded.city,
                target_date=excluded.target_date,
                resolution_station=excluded.resolution_station,
                resolution_source=excluded.resolution_source,
                resolution_source_url=excluded.resolution_source_url,
                settlement_unit=excluded.settlement_unit,
                volume_24h=excluded.volume_24h,
                open_interest=excluded.open_interest,
                buckets_json=excluded.buckets_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                str(row.get("event_id") or ""),
                str(row.get("slug") or ""),
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("resolution_station") or ""),
                str(row.get("resolution_source") or ""),
                str(row.get("resolution_source_url") or ""),
                str(row.get("settlement_unit") or ""),
                _float(row.get("volume_24h")),
                _float(row.get("open_interest")),
                dump_json(row.get("buckets_json") or []),
                dump_json(row.get("raw_json") or row),
                now,
                now,
            ),
        )
    return {"ok": True, "event_id": row.get("event_id")}


def upsert_polymarket_market(
    row: dict[str, Any],
    *,
    path: Path | None = None,
    _connection=None,
) -> dict[str, Any]:
    if _connection is None:
        init_v3_db(path)
    now = utc_now()
    connection_context = connect(path) if _connection is None else nullcontext(_connection)
    with connection_context as conn:
        conn.execute(
            """
            INSERT INTO polymarket_markets (
                market_id, event_id, event_slug, market_slug, city, target_date,
                bucket_label, bucket_lower_c, bucket_upper_c, is_tail,
                outcome_yes_token_id, outcome_no_token_id, order_min_size,
                tick_size, enable_order_book, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market_id) DO UPDATE SET
                event_id=excluded.event_id,
                event_slug=excluded.event_slug,
                market_slug=excluded.market_slug,
                city=excluded.city,
                target_date=excluded.target_date,
                bucket_label=excluded.bucket_label,
                bucket_lower_c=excluded.bucket_lower_c,
                bucket_upper_c=excluded.bucket_upper_c,
                is_tail=excluded.is_tail,
                outcome_yes_token_id=excluded.outcome_yes_token_id,
                outcome_no_token_id=excluded.outcome_no_token_id,
                order_min_size=excluded.order_min_size,
                tick_size=excluded.tick_size,
                enable_order_book=excluded.enable_order_book,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                str(row.get("market_id") or ""),
                str(row.get("event_id") or ""),
                str(row.get("event_slug") or ""),
                str(row.get("market_slug") or ""),
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("bucket_label") or ""),
                _float(row.get("bucket_lower_c")),
                _float(row.get("bucket_upper_c")),
                1 if row.get("is_tail") else 0,
                str(row.get("outcome_yes_token_id") or ""),
                str(row.get("outcome_no_token_id") or ""),
                _float(row.get("order_min_size")),
                _float(row.get("tick_size")),
                1 if row.get("enable_order_book", True) else 0,
                dump_json(row.get("raw_json") or row),
                now,
                now,
            ),
        )
    return {"ok": True, "market_id": row.get("market_id")}


def upsert_polymarket_orderbook(
    market_row: dict[str, Any],
    book: dict[str, Any],
    *,
    path: Path | None = None,
    _connection=None,
) -> dict[str, Any]:
    if _connection is None:
        init_v3_db(path)
    bids = _levels(book.get("bids"))
    asks = _levels(book.get("asks"))
    best_bid = max((level["price"] for level in bids), default=None)
    best_ask = min((level["price"] for level in asks), default=None)
    ts = datetime.now(timezone.utc).isoformat()
    raw_hash = _hash_json(book)
    snapshot_key = f"pm_orderbook:{market_row.get('market_id')}:{market_row.get('outcome_yes_token_id')}:{raw_hash}"
    connection_context = connect(path) if _connection is None else nullcontext(_connection)
    with connection_context as conn:
        conn.execute(
            """
            INSERT INTO polymarket_orderbook (
                snapshot_key, market_id, event_id, token_id, ts, best_bid, best_ask,
                spread, volume_24h, bid_depth, ask_depth, source_url,
                raw_response_hash, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_key) DO NOTHING
            """,
            (
                snapshot_key,
                str(market_row.get("market_id") or ""),
                str(market_row.get("event_id") or ""),
                str(market_row.get("outcome_yes_token_id") or ""),
                ts,
                best_bid,
                best_ask,
                round(best_ask - best_bid, 6) if best_bid is not None and best_ask is not None else None,
                _float(market_row.get("volume_24h")),
                round(sum(level["size"] for level in bids), 6) if bids else None,
                round(sum(level["size"] for level in asks), 6) if asks else None,
                str(book.get("source_url") or ""),
                raw_hash,
                dump_json(book),
                utc_now(),
            ),
        )
    return {"ok": True, "snapshot_key": snapshot_key}


def _selected_station_rows(cities: list[str] | None, *, path: Path | None = None) -> list[dict[str, Any]]:
    rows = list_stations(path)
    requested = {str(city or "").strip().lower() for city in (cities or []) if str(city or "").strip()}
    if not requested:
        requested = set(ASIAN_CITY_KEYS)
    return [
        row for row in rows
        if str(row.get("city_key") or row.get("city") or "").lower() in requested
    ]


def _event_active(event: dict[str, Any]) -> bool:
    if not event or event.get("closed") is True or event.get("active") is False:
        return False
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    return any(market.get("closed") is not True and market.get("active") is not False for market in markets if isinstance(market, dict))


def _normalize_degree_text(text: str) -> str:
    return (
        str(text or "")
        .replace("\u00c2", "")
        .replace("\u00b0", "")
        .replace("\u2103", "C")
        .replace("\u2109", "F")
        .replace("℃", "C")
        .replace("°", "")
        .lower()
    )


def _station_from_source_url(url: str) -> str:
    for part in reversed([piece for piece in str(url or "").split("/") if piece]):
        text = part.upper()
        if re.fullmatch(r"(K[A-Z]{3}|Z[A-Z]{3}|R[A-Z]{3}|V[A-Z]{3}|W[A-Z]{3}|E[A-Z]{3}|L[A-Z]{3}|S[A-Z]{3})", text):
            return text
    return ""


def _resolution_source(url: str, text: str) -> str:
    combined = f"{url} {text}".lower()
    if "wunderground" in combined:
        return "wunderground"
    if "hong kong observatory" in combined or "weather.gov.hk" in combined:
        return "hong_kong_observatory"
    return "unknown"


def _json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(str(raw or "[]"))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _levels(raw: Any) -> list[dict[str, float]]:
    rows = raw if isinstance(raw, list) else []
    parsed = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        price = _float(item.get("price"))
        size = _float(item.get("size"))
        if price is not None and size is not None:
            parsed.append({"price": price, "size": size})
    return parsed


def _float(value: Any) -> float | None:
    try:
        if value in (None, "", "null"):
            return None
        return float(value)
    except Exception:
        return None


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
