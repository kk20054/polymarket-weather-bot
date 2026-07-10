from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .cli import run_china_weather_fetch, run_daily_max_build, run_gamma_structured_sync, run_hourly_consensus_build, run_market_buckets_sync, run_model_timing_reprice, run_openmeteo_fetch, run_pws_fetch, run_signal_decisions_build, run_weathercom_fetch
from .db import log_data_fetch, utc_now
from .metar import fetch_recent_hours
from .source_health import build_source_health_matrix, compact_source_health
from .stations import enabled_station_rows, sync_station_registry


MAX_CITY_CONCURRENCY = int(os.getenv("WEATHERBOT_SCHEDULER_CITY_CONCURRENCY", "2") or "2")
METAR_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_METAR_SECONDS", "300") or "300")
FORECAST_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_FORECAST_SECONDS", "3600") or "3600")
DERIVE_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_DERIVE_SECONDS", "900") or "900")
CHINA_LIVE_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_CHINA_LIVE_SECONDS", "300") or "300")
GAMMA_ORDERBOOK_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_GAMMA_ORDERBOOK_SECONDS", "300") or "300")
MODEL_TIMING_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_MODEL_TIMING_SECONDS", "60") or "60")
METAR_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_METAR_CITY_TIMEOUT", "120") or "120")
FORECAST_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_FORECAST_CITY_TIMEOUT", "240") or "240")
DERIVE_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_DERIVE_CITY_TIMEOUT", "300") or "300")
CHINA_LIVE_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_CHINA_LIVE_CITY_TIMEOUT", "60") or "60")
PWS_OPTIONAL_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_PWS_TIMEOUT", "30") or "30")
MODEL_TIMING_WINDOWS_UTC = ((7, 1), (19, 1), (5, 1), (17, 1))


@dataclass
class PollerState:
    key: str
    label: str
    interval_seconds: int
    initial_delay_seconds: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    running: bool = False
    last_run_at: str | None = None
    last_started_at: str | None = None
    last_duration_ms: float | None = None
    next_run_at: str | None = None
    last_status: str = "idle"
    last_message: str = ""
    last_result: dict[str, Any] = field(default_factory=dict)
    run_count: int = 0
    consecutive_failures: int = 0
    failure_times: list[datetime] = field(default_factory=list)

    def fails_last_hour(self, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        cutoff = current - timedelta(hours=1)
        self.failure_times = [item for item in self.failure_times if item >= cutoff]
        return len(self.failure_times)

    def next_delay(self) -> int:
        multiplier = min(2 ** max(0, self.consecutive_failures), 4)
        return max(1, int(self.interval_seconds * multiplier))

    def status(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        age_seconds = None
        if self.last_run_at:
            parsed = _parse_time(self.last_run_at)
            if parsed:
                age_seconds = max(0.0, (now - parsed).total_seconds())
        return {
            "key": self.key,
            "label": self.label,
            "interval_seconds": self.interval_seconds,
            "initial_delay_seconds": self.initial_delay_seconds,
            "running": self.running or self.lock.locked(),
            "last_run_at": self.last_run_at,
            "last_started_at": self.last_started_at,
            "age_seconds": age_seconds,
            "last_duration_ms": self.last_duration_ms,
            "fails_last_hour": self.fails_last_hour(now),
            "next_run_at": self.next_run_at,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "last_result": self.last_result,
            "run_count": self.run_count,
            "consecutive_failures": self.consecutive_failures,
        }


class WeatherBotScheduler:
    def __init__(self, *, city_concurrency: int = MAX_CITY_CONCURRENCY):
        self.city_concurrency = max(1, int(city_concurrency or 2))
        self.started_at: str | None = None
        self.stop_event = asyncio.Event()
        self._source_health_cache: dict[str, Any] | None = None
        self._source_health_cache_at = 0.0
        self.pollers: dict[str, PollerState] = {
            "metar_poller": PollerState("metar_poller", "METAR", METAR_INTERVAL_SECONDS, 0),
            "china_live_poller": PollerState("china_live_poller", "China Live", CHINA_LIVE_INTERVAL_SECONDS, 5),
            "forecast_poller": PollerState("forecast_poller", "Forecast", FORECAST_INTERVAL_SECONDS, 15),
            "gamma_orderbook_poller": PollerState("gamma_orderbook_poller", "Orderbook", GAMMA_ORDERBOOK_INTERVAL_SECONDS, 45),
            "derive_poller": PollerState("derive_poller", "Historical", DERIVE_INTERVAL_SECONDS, 420),
            "model_timing_poller": PollerState("model_timing_poller", "Model Timing", MODEL_TIMING_INTERVAL_SECONDS, 0),
        }

    async def start(self) -> dict[str, Any]:
        if self.running:
            return self.status(message="scheduler already running")
        self.stop_event = asyncio.Event()
        self.started_at = utc_now()
        for key, state in self.pollers.items():
            state.task = asyncio.create_task(self._poll_loop(key), name=f"weatherbot-{key}")
        return self.status(message="scheduler started")

    async def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        tasks = [state.task for state in self.pollers.values() if state.task and not state.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for state in self.pollers.values():
            state.task = None
            state.running = False
            state.next_run_at = None
        return self.status(message="scheduler stopped")

    @property
    def running(self) -> bool:
        return any(state.task is not None and not state.task.done() for state in self.pollers.values())

    async def run_once(self, poller_key: str) -> dict[str, Any]:
        state = self.pollers[poller_key]
        if state.lock.locked():
            return {
                "ok": True,
                "skipped": True,
                "reason": "already_running",
                "poller": poller_key,
            }
        async with state.lock:
            state.running = True
            state.last_started_at = utc_now()
            started_perf = time.perf_counter()
            try:
                if poller_key == "metar_poller":
                    result = await self._run_metar_poller()
                elif poller_key == "forecast_poller":
                    result = await self._run_forecast_poller()
                elif poller_key == "derive_poller":
                    result = await self._run_derive_poller()
                elif poller_key == "china_live_poller":
                    result = await self._run_china_live_poller()
                elif poller_key == "gamma_orderbook_poller":
                    result = await self._run_gamma_orderbook_poller()
                elif poller_key == "model_timing_poller":
                    result = await self._run_model_timing_poller()
                else:
                    raise KeyError(poller_key)
            except Exception as exc:
                result = {"ok": False, "poller": poller_key, "error": str(exc), "results": []}
            duration_ms = round((time.perf_counter() - started_perf) * 1000)
            ok = bool(result.get("ok"))
            now = datetime.now(timezone.utc)
            state.running = False
            state.last_run_at = now.isoformat()
            state.last_duration_ms = duration_ms
            state.last_status = "OK" if ok else "WARN"
            state.last_message = _poller_message(poller_key, result)
            state.last_result = _compact_result(result)
            state.run_count += 1
            if ok:
                state.consecutive_failures = 0
            else:
                state.consecutive_failures += 1
                state.failure_times.append(now)
            delay = state.next_delay()
            state.next_run_at = (now + timedelta(seconds=delay)).isoformat()
            log_data_fetch(
                source="scheduler",
                stage=poller_key,
                status="OK" if ok else "WARN",
                duration_ms=duration_ms,
                message=state.last_message,
                details={
                    "poller": poller_key,
                    "interval_seconds": state.interval_seconds,
                    "next_run_at": state.next_run_at,
                    **_compact_result(result),
                },
                started_at=state.last_started_at or "",
                finished_at=state.last_run_at or "",
            )
            return {
                **result,
                "poller": poller_key,
                "duration_ms": duration_ms,
                "next_run_at": state.next_run_at,
            }

    def status(self, *, message: str = "") -> dict[str, Any]:
        payload = {
            "ok": True,
            "scheduler_version": "weatherbot-scheduler-v1",
            "running": self.running,
            "started_at": self.started_at,
            "message": message,
            "city_concurrency": self.city_concurrency,
            "pollers": {key: state.status() for key, state in self.pollers.items()},
        }
        try:
            now = time.monotonic()
            if self._source_health_cache is None or now - self._source_health_cache_at >= 30.0:
                self._source_health_cache = compact_source_health(build_source_health_matrix())
                self._source_health_cache_at = now
            payload["source_health"] = self._source_health_cache
        except Exception as exc:
            payload["source_health"] = {
                "overall_status": "unknown",
                "required_blockers": ["source_health_unavailable"],
                "error": str(exc),
            }
        return payload

    async def _poll_loop(self, poller_key: str) -> None:
        state = self.pollers[poller_key]
        if state.run_count == 0 and state.initial_delay_seconds > 0:
            state.next_run_at = (
                datetime.now(timezone.utc) + timedelta(seconds=state.initial_delay_seconds)
            ).isoformat()
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=state.initial_delay_seconds,
                )
                return
            except asyncio.TimeoutError:
                pass
        while not self.stop_event.is_set():
            await self.run_once(poller_key)
            delay = state.next_delay()
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def _run_metar_poller(self) -> dict[str, Any]:
        rows = _enabled_rows()

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city"))
            try:
                metar = await asyncio.wait_for(
                    asyncio.to_thread(fetch_recent_hours, city, hours=24.0),
                    timeout=METAR_CITY_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "city": city,
                    "station_id": row.get("station_id"),
                    "error": f"metar_timeout_{METAR_CITY_TIMEOUT_SECONDS}s",
                }
            optional_warnings = []
            try:
                pws = await asyncio.wait_for(
                    asyncio.to_thread(
                        run_pws_fetch,
                        city,
                        dry_run=False,
                        all_cities=False,
                        limit_cities=1,
                        station_limit=5,
                    ),
                    timeout=PWS_OPTIONAL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                pws = {"ok": False, "optional": True, "error": "pws_timeout"}
                optional_warnings.append("pws_timeout")
            except Exception as exc:
                pws = {"ok": False, "optional": True, "error": str(exc)}
                optional_warnings.append("pws_failed")
            return {
                "ok": _payload_ok(metar),
                "city": city,
                "station_id": row.get("station_id"),
                "metar": metar,
                "pws": pws,
                "optional_warnings": optional_warnings,
            }

        return await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="metar_poller",
            timeout_seconds=METAR_CITY_TIMEOUT_SECONDS + PWS_OPTIONAL_TIMEOUT_SECONDS + 10,
        )

    async def _run_forecast_poller(self) -> dict[str, Any]:
        rows = _enabled_rows()

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city"))
            openmeteo = await asyncio.wait_for(
                asyncio.to_thread(
                    run_openmeteo_fetch,
                    city,
                    ensemble=False,
                    limit_cities=1,
                    forecast_days=7,
                ),
                timeout=max(30, FORECAST_CITY_TIMEOUT_SECONDS - 50),
            )
            weathercom = await asyncio.wait_for(
                asyncio.to_thread(
                    run_weathercom_fetch,
                    city,
                    dry_run=False,
                    limit_cities=1,
                    forecast_days=3,
                ),
                timeout=40,
            )
            return {
                "ok": _payload_ok(openmeteo),
                "city": city,
                "station_id": row.get("station_id"),
                "openmeteo": openmeteo,
                "weathercom": weathercom,
                "optional_warnings": [] if _payload_ok(weathercom) else ["weathercom_v3_unavailable"],
            }

        return await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="forecast_poller",
            timeout_seconds=FORECAST_CITY_TIMEOUT_SECONDS,
        )

    async def _run_derive_poller(self) -> dict[str, Any]:
        rows = _enabled_rows()
        targets_by_city = {
            str(row.get("city_key") or row.get("city") or ""): _target_dates_for_station(row)
            for row in rows
        }
        market_results: dict[tuple[str, str], dict[str, Any]] = {}
        target_dates = sorted({target for targets in targets_by_city.values() for target in targets})
        for target_date in target_dates:
            target_cities = [city for city, targets in targets_by_city.items() if target_date in targets]
            market_batch = await asyncio.to_thread(
                run_market_buckets_sync,
                1000,
                cities_arg=",".join(target_cities),
                target_date=target_date,
                active_weather=True,
                limit_cities=len(target_cities),
                fetch_orderbooks=True,
            )
            for item in market_batch.get("results") or []:
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("city") or ""), str(item.get("target_date") or target_date))
                market_results[key] = item

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city") or "")
            city_target_dates = targets_by_city.get(city) or []
            date_results = []
            for target_date in city_target_dates:
                hourly = await asyncio.to_thread(run_hourly_consensus_build, city, target_date, limit_cities=1)
                daily = await asyncio.to_thread(run_daily_max_build, city, target_date, limit_cities=1)
                buckets = market_results.get((city, target_date), {"ok": False, "stored": 0, "status": "missing"})
                decisions = await asyncio.to_thread(run_signal_decisions_build, city, target_date, limit_cities=1, limit=120)
                date_results.append({
                    "target_date": target_date,
                    "hourly_rows": hourly.get("rows_upserted") or hourly.get("rows_built") or 0,
                    "daily_stored": daily.get("stored") or daily.get("stored_count") or 0,
                    "market_buckets": buckets.get("stored") or buckets.get("buckets") or 0,
                    "signal_decisions": decisions.get("stored") or decisions.get("decisions") or 0,
                    "ok": all(_payload_ok(item) for item in (hourly, daily, buckets, decisions)),
                })
            return {
                "ok": all(item.get("ok") for item in date_results),
                "city": city,
                "station_id": row.get("station_id"),
                "target_dates": city_target_dates,
                "dates": date_results,
            }

        return await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="derive_poller",
            timeout_seconds=DERIVE_CITY_TIMEOUT_SECONDS,
        )

    async def _run_china_live_poller(self) -> dict[str, Any]:
        rows = [
            row for row in _enabled_rows()
            if str(row.get("city_key") or row.get("city") or "") in {"shanghai", "hong-kong"}
        ]
        return await _run_city_batch(
            rows,
            self.city_concurrency,
            lambda row: asyncio.to_thread(
                run_china_weather_fetch,
                str(row.get("city_key") or row.get("city")),
            ),
            poller_key="china_live_poller",
            timeout_seconds=CHINA_LIVE_CITY_TIMEOUT_SECONDS,
        )

    async def _run_gamma_orderbook_poller(self) -> dict[str, Any]:
        return await asyncio.to_thread(
            run_gamma_structured_sync,
            "",
            days=3,
            dry_run=False,
            fetch_orderbooks=True,
        )

    async def _run_model_timing_poller(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if (now.hour, now.minute) not in MODEL_TIMING_WINDOWS_UTC:
            return {
                "ok": True,
                "skipped": True,
                "reason": "outside_model_timing_window",
                "windows_utc": [f"{hour:02d}:{minute:02d}" for hour, minute in MODEL_TIMING_WINDOWS_UTC],
            }
        return await asyncio.to_thread(run_model_timing_reprice, "", days_arg=2, dry_run=False)


async def _run_city_batch(
    rows: list[dict[str, Any]],
    concurrency: int,
    city_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    poller_key: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(max(1, int(concurrency or 2)))

    async def guarded(row: dict[str, Any]) -> dict[str, Any]:
        city = str(row.get("city_key") or row.get("city") or "")
        async with semaphore:
            started = utc_now()
            started_perf = time.perf_counter()
            try:
                payload = await asyncio.wait_for(
                    city_fn(row),
                    timeout=max(1, int(timeout_seconds)),
                )
                ok = _payload_ok(payload)
                finished = utc_now()
                details = {**payload, "poller": poller_key}
                log_data_fetch(
                    source="scheduler",
                    stage="city_refresh",
                    status="OK" if ok else "WARN",
                    duration_ms=round((time.perf_counter() - started_perf) * 1000),
                    city=city,
                    message=f"{poller_key} city refresh {'completed' if ok else 'finished with warnings'} for {city}",
                    details=details,
                    started_at=started,
                    finished_at=finished,
                )
                return {"city": city, "ok": ok, "payload": payload}
            except asyncio.TimeoutError:
                finished = utc_now()
                error = {
                    "city": city,
                    "ok": False,
                    "poller": poller_key,
                    "error": f"city_timeout_{int(timeout_seconds)}s",
                }
                log_data_fetch(
                    source="scheduler",
                    stage="city_refresh",
                    status="ERROR",
                    duration_ms=round((time.perf_counter() - started_perf) * 1000),
                    city=city,
                    message=f"{poller_key} city refresh timed out for {city}",
                    details=error,
                    started_at=started,
                    finished_at=finished,
                )
                return error
            except Exception as exc:
                finished = utc_now()
                error = {"city": city, "ok": False, "poller": poller_key, "error": str(exc)}
                log_data_fetch(
                    source="scheduler",
                    stage="city_refresh",
                    status="ERROR",
                    duration_ms=round((time.perf_counter() - started_perf) * 1000),
                    city=city,
                    message=f"{poller_key} city refresh failed for {city}",
                    details=error,
                    started_at=started,
                    finished_at=finished,
                )
                return error

    results = await asyncio.gather(*(guarded(row) for row in rows), return_exceptions=False)
    failures = [row for row in results if not row.get("ok")]
    return {
        "ok": not failures,
        "cities": len(rows),
        "ok_cities": len(rows) - len(failures),
        "failed_cities": len(failures),
        "results": results,
    }


def _enabled_rows() -> list[dict[str, Any]]:
    rows = enabled_station_rows()
    if not rows:
        sync_station_registry()
        rows = enabled_station_rows()
    return rows


def _target_dates_for_station(row: dict[str, Any]) -> list[str]:
    timezone_name = str(row.get("timezone") or "UTC")
    try:
        local_today = datetime.now(ZoneInfo(timezone_name)).date()
    except Exception:
        local_today = datetime.now(timezone.utc).date()
    return [local_today.isoformat(), (local_today + timedelta(days=1)).isoformat()]


def _payload_ok(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("ok") is False:
        return False
    if int(payload.get("failed") or payload.get("failed_cities") or 0) > 0:
        return False
    failures = payload.get("failures")
    return not failures


def _compact_result(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    city_results = []
    for result in results:
        if not isinstance(result, dict):
            continue
        payload_result = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        station_id = payload_result.get("station_id")
        if not station_id:
            stations = payload_result.get("stations")
            if isinstance(stations, list) and stations:
                station_id = stations[0]
        city_results.append({
            "city": result.get("city") or payload_result.get("city"),
            "station_id": station_id,
            "ok": bool(result.get("ok")),
            "error": result.get("error") or payload_result.get("error"),
            "reports_upserted": payload_result.get("reports_upserted"),
            "reports_fetched": payload_result.get("reports_fetched"),
            "rows_upserted": payload_result.get("rows_upserted"),
            "failed": payload_result.get("failed") or payload_result.get("failed_cities"),
        })
    return {
        "ok": bool(payload.get("ok")),
        "cities": int(payload.get("cities") or len(results) or 0),
        "ok_cities": int(payload.get("ok_cities") or 0),
        "failed_cities": int(payload.get("failed_cities") or payload.get("failed") or 0),
        "result_count": len(results),
        "city_results": city_results,
    }


def _poller_message(poller_key: str, result: dict[str, Any]) -> str:
    if poller_key == "gamma_orderbook_poller":
        return (
            f"{poller_key} completed: {int(result.get('events_stored') or 0)} events, "
            f"{int(result.get('orderbooks_stored') or 0)} orderbooks"
            if _payload_ok(result)
            else f"{poller_key} completed with {len(result.get('failures') or [])} failures"
        )
    summary = _compact_result(result)
    return (
        f"{poller_key} completed: {summary['ok_cities']}/{summary['cities']} cities ok"
        if summary["ok"]
        else f"{poller_key} completed with {summary['failed_cities']} failed cities"
    )


def _parse_time(value: str) -> datetime | None:
    try:
        text = str(value or "")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


_SCHEDULER: WeatherBotScheduler | None = None


def get_scheduler() -> WeatherBotScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = WeatherBotScheduler()
    return _SCHEDULER
