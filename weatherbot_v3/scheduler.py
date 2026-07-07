from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .cli import run_china_weather_fetch, run_daily_max_build, run_gamma_structured_sync, run_hourly_consensus_build, run_market_buckets_sync, run_model_timing_reprice, run_openmeteo_fetch, run_pws_fetch, run_signal_decisions_build
from .db import log_data_fetch, utc_now
from .metar import fetch_recent_hours
from .stations import enabled_station_rows, sync_station_registry


MAX_CITY_CONCURRENCY = int(os.getenv("WEATHERBOT_SCHEDULER_CITY_CONCURRENCY", "2") or "2")
METAR_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_METAR_SECONDS", "300") or "300")
FORECAST_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_FORECAST_SECONDS", "3600") or "3600")
DERIVE_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_DERIVE_SECONDS", "900") or "900")
CHINA_LIVE_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_CHINA_LIVE_SECONDS", "300") or "300")
GAMMA_ORDERBOOK_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_GAMMA_ORDERBOOK_SECONDS", "300") or "300")
MODEL_TIMING_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_MODEL_TIMING_SECONDS", "60") or "60")
MODEL_TIMING_WINDOWS_UTC = ((7, 1), (19, 1), (5, 1), (17, 1))


@dataclass
class PollerState:
    key: str
    label: str
    interval_seconds: int
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
        self.pollers: dict[str, PollerState] = {
            "metar_poller": PollerState("metar_poller", "METAR", METAR_INTERVAL_SECONDS),
            "forecast_poller": PollerState("forecast_poller", "Forecast", FORECAST_INTERVAL_SECONDS),
            "derive_poller": PollerState("derive_poller", "Historical", DERIVE_INTERVAL_SECONDS),
            "china_live_poller": PollerState("china_live_poller", "China Live", CHINA_LIVE_INTERVAL_SECONDS),
            "gamma_orderbook_poller": PollerState("gamma_orderbook_poller", "Orderbook", GAMMA_ORDERBOOK_INTERVAL_SECONDS),
            "model_timing_poller": PollerState("model_timing_poller", "Model Timing", MODEL_TIMING_INTERVAL_SECONDS),
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
        return {
            "ok": True,
            "scheduler_version": "weatherbot-scheduler-v1",
            "running": self.running,
            "started_at": self.started_at,
            "message": message,
            "city_concurrency": self.city_concurrency,
            "pollers": {key: state.status() for key, state in self.pollers.items()},
        }

    async def _poll_loop(self, poller_key: str) -> None:
        while not self.stop_event.is_set():
            state = self.pollers[poller_key]
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
            metar = await asyncio.to_thread(fetch_recent_hours, city, hours=24.0)
            optional_warnings = []
            try:
                pws = await asyncio.to_thread(
                    run_pws_fetch,
                    city,
                    dry_run=False,
                    all_cities=False,
                    limit_cities=1,
                    station_limit=5,
                )
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
        )

    async def _run_forecast_poller(self) -> dict[str, Any]:
        rows = _enabled_rows()
        return await _run_city_batch(
            rows,
            self.city_concurrency,
            lambda row: asyncio.to_thread(
                run_openmeteo_fetch,
                str(row.get("city_key") or row.get("city")),
                ensemble=False,
                limit_cities=1,
                forecast_days=7,
            ),
        )

    async def _run_derive_poller(self) -> dict[str, Any]:
        rows = _enabled_rows()

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city") or "")
            target_dates = _target_dates_for_station(row)
            date_results = []
            for target_date in target_dates:
                hourly = await asyncio.to_thread(run_hourly_consensus_build, city, target_date, limit_cities=1)
                daily = await asyncio.to_thread(run_daily_max_build, city, target_date, limit_cities=1)
                buckets = await asyncio.to_thread(
                    run_market_buckets_sync,
                    120,
                    cities_arg=city,
                    target_date=target_date,
                    active_weather=True,
                    limit_cities=1,
                    fetch_orderbooks=True,
                )
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
                "target_dates": target_dates,
                "dates": date_results,
            }

        return await _run_city_batch(rows, self.city_concurrency, run_city)

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
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(max(1, int(concurrency or 2)))

    async def guarded(row: dict[str, Any]) -> dict[str, Any]:
        city = str(row.get("city_key") or row.get("city") or "")
        async with semaphore:
            started = utc_now()
            started_perf = time.perf_counter()
            try:
                payload = await city_fn(row)
                ok = _payload_ok(payload)
                finished = utc_now()
                log_data_fetch(
                    source="scheduler",
                    stage="city_refresh",
                    status="OK" if ok else "WARN",
                    duration_ms=round((time.perf_counter() - started_perf) * 1000),
                    city=city,
                    message=f"Scheduler city refresh {'completed' if ok else 'finished with warnings'} for {city}",
                    details=payload,
                    started_at=started,
                    finished_at=finished,
                )
                return {"city": city, "ok": ok, "payload": payload}
            except Exception as exc:
                finished = utc_now()
                error = {"city": city, "ok": False, "error": str(exc)}
                log_data_fetch(
                    source="scheduler",
                    stage="city_refresh",
                    status="ERROR",
                    duration_ms=round((time.perf_counter() - started_perf) * 1000),
                    city=city,
                    message=f"Scheduler city refresh failed for {city}",
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
