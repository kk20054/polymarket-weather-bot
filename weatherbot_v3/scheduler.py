from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .bias import DEFAULT_BIAS_TABLE, train_bias_table
from .cli import run_daily_max_build, run_gamma_structured_sync, run_hourly_consensus_build, run_market_buckets_sync, run_model_timing_reprice, run_openmeteo_fetch, run_pws_fetch, run_signal_decisions_build, run_weathercom_fetch, run_wunderground_daily_rollup, run_wunderground_hourly_fetch
from .clv import snapshot_candidate_preclose_quotes
from .china_weather import fetch_china_weather_city, supported_china_live_cities
from .db import connect, log_data_fetch, utc_now
from .env_utils import env_value
from .market_buckets import refresh_cached_market_bucket_orderbooks
from .metar import fetch_recent_hours
from .paper_settlement import settle_open_paper_orders
from .paper_exit import evaluate_open_paper_exits
from .paper_validation import run_paper_validation_tick
from .source_health import compact_source_health
from .stations import enabled_station_rows, list_stations, sync_station_registry


MAX_CITY_CONCURRENCY = int(os.getenv("WEATHERBOT_SCHEDULER_CITY_CONCURRENCY", "4") or "4")
MAX_POLLER_CONCURRENCY = int(os.getenv("WEATHERBOT_SCHEDULER_POLLER_CONCURRENCY", "3") or "3")
MAX_CRITICAL_POLLER_CONCURRENCY = int(os.getenv("WEATHERBOT_SCHEDULER_CRITICAL_POLLER_CONCURRENCY", "3") or "3")
MAX_BACKGROUND_POLLER_CONCURRENCY = int(os.getenv("WEATHERBOT_SCHEDULER_BACKGROUND_POLLER_CONCURRENCY", "1") or "1")
METAR_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_METAR_SECONDS", "300") or "300")
FORECAST_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_FORECAST_SECONDS", "600") or "600")
NWP_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_NWP_SECONDS", "3600") or "3600")
NWP_ENSEMBLE_EVERY_N_RUNS = max(1, int(os.getenv("WEATHERBOT_SCHEDULER_ENSEMBLE_EVERY_N_RUNS", "6") or "6"))
NWP_ENSEMBLE_MAX_AGE_SECONDS = max(
    NWP_INTERVAL_SECONDS,
    int(
        os.getenv(
            "WEATHERBOT_SCHEDULER_ENSEMBLE_MAX_AGE_SECONDS",
            str(NWP_INTERVAL_SECONDS * NWP_ENSEMBLE_EVERY_N_RUNS),
        )
        or str(NWP_INTERVAL_SECONDS * NWP_ENSEMBLE_EVERY_N_RUNS)
    ),
)
NWP_ENSEMBLE_MODELS = tuple(
    item.strip()
    for item in os.getenv("WEATHERBOT_SCHEDULER_ENSEMBLE_MODELS", "gfs_seamless").split(",")
    if item.strip()
)
NWP_ENSEMBLE_FORECAST_DAYS = max(1, min(7, int(os.getenv("WEATHERBOT_SCHEDULER_ENSEMBLE_FORECAST_DAYS", "3") or "3")))
HISTORICAL_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_HISTORICAL_SECONDS", "600") or "600")
BIAS_RETRAIN_MAX_AGE_HOURS = float(os.getenv("WEATHERBOT_BIAS_RETRAIN_MAX_AGE_HOURS", "20") or "20")
BASELINE_REFRESH_MULTIPLIER = int(os.getenv("WEATHERBOT_SCHEDULER_BASELINE_MULTIPLIER", "3") or "3")
METAR_BACKGROUND_MULTIPLIER = int(os.getenv("WEATHERBOT_SCHEDULER_METAR_BACKGROUND_MULTIPLIER", "12") or "12")
NWP_BACKGROUND_MULTIPLIER = int(os.getenv("WEATHERBOT_SCHEDULER_NWP_BACKGROUND_MULTIPLIER", "6") or "6")
HISTORICAL_BACKGROUND_MULTIPLIER = int(os.getenv("WEATHERBOT_SCHEDULER_HISTORICAL_BACKGROUND_MULTIPLIER", "6") or "6")
PWS_BACKGROUND_MULTIPLIER = int(os.getenv("WEATHERBOT_SCHEDULER_PWS_BACKGROUND_MULTIPLIER", "6") or "6")
GAMMA_DISCOVERY_MULTIPLIER = int(os.getenv("WEATHERBOT_SCHEDULER_GAMMA_DISCOVERY_MULTIPLIER", "6") or "6")
PWS_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_PWS_SECONDS", "600") or "600")
DERIVE_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_DERIVE_SECONDS", "900") or "900")
CHINA_LIVE_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_CHINA_LIVE_SECONDS", "60") or "60")
GAMMA_ORDERBOOK_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_GAMMA_ORDERBOOK_SECONDS", "300") or "300")
GAMMA_DISCOVERY_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_GAMMA_DISCOVERY_SECONDS", "3600") or "3600")
MODEL_TIMING_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_MODEL_TIMING_SECONDS", "60") or "60")
PAPER_SETTLEMENT_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_PAPER_SETTLEMENT_SECONDS", "900") or "900")
PAPER_EXECUTION_INTERVAL_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_PAPER_EXECUTION_SECONDS", "300") or "300")
METAR_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_METAR_CITY_TIMEOUT", "120") or "120")
FORECAST_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_FORECAST_CITY_TIMEOUT", "240") or "240")
DERIVE_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_DERIVE_CITY_TIMEOUT", "300") or "300")
CHINA_LIVE_CITY_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_CHINA_LIVE_CITY_TIMEOUT", "15") or "15")
PWS_OPTIONAL_TIMEOUT_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_PWS_TIMEOUT", "30") or "30")
PWS_AUTH_COOLDOWN_SECONDS = int(os.getenv("WEATHERBOT_SCHEDULER_PWS_AUTH_COOLDOWN", "3600") or "3600")
MODEL_TIMING_WINDOWS_UTC = ((7, 1), (19, 1), (5, 1), (17, 1))


@dataclass
class PollerState:
    key: str
    label: str
    interval_seconds: int
    initial_delay_seconds: int = 0
    slot_group: str = "source"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task | None = None
    running: bool = False
    waiting_for_slot: bool = False
    last_run_at: str | None = None
    last_started_at: str | None = None
    last_duration_ms: float | None = None
    last_queue_wait_ms: float | None = None
    next_run_at: str | None = None
    last_status: str = "idle"
    last_message: str = ""
    last_log_error: str = ""
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
            "slot_group": self.slot_group,
            "interval_seconds": self.interval_seconds,
            "initial_delay_seconds": self.initial_delay_seconds,
            "running": self.running or self.lock.locked(),
            "waiting_for_slot": self.waiting_for_slot,
            "last_run_at": self.last_run_at,
            "last_started_at": self.last_started_at,
            "age_seconds": age_seconds,
            "last_duration_ms": self.last_duration_ms,
            "last_queue_wait_ms": self.last_queue_wait_ms,
            "fails_last_hour": self.fails_last_hour(now),
            "next_run_at": self.next_run_at,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "last_log_error": self.last_log_error,
            "last_result": self.last_result,
            "run_count": self.run_count,
            "consecutive_failures": self.consecutive_failures,
        }


class WeatherBotScheduler:
    def __init__(
        self,
        *,
        city_concurrency: int = MAX_CITY_CONCURRENCY,
        poller_concurrency: int = MAX_POLLER_CONCURRENCY,
        critical_poller_concurrency: int = MAX_CRITICAL_POLLER_CONCURRENCY,
        background_poller_concurrency: int = MAX_BACKGROUND_POLLER_CONCURRENCY,
    ):
        self.city_concurrency = max(1, int(city_concurrency or 2))
        self.poller_concurrency = max(1, int(poller_concurrency or 3))
        self.critical_poller_concurrency = max(1, int(critical_poller_concurrency or 3))
        self.background_poller_concurrency = max(1, int(background_poller_concurrency or 1))
        self._poller_slots = {
            "critical": asyncio.Semaphore(self.critical_poller_concurrency),
            "source": asyncio.Semaphore(self.poller_concurrency),
            "background": asyncio.Semaphore(self.background_poller_concurrency),
        }
        self._active_pollers: set[str] = set()
        self.started_at: str | None = None
        self.stop_event = asyncio.Event()
        self._pws_auth_disabled_until = 0.0
        self._source_health_cache: dict[str, Any] | None = None
        self._source_health_cache_at = 0.0
        self.pollers: dict[str, PollerState] = {
            "metar_poller": PollerState("metar_poller", "METAR", METAR_INTERVAL_SECONDS, 0, "critical"),
            "china_live_poller": PollerState("china_live_poller", "China Live", CHINA_LIVE_INTERVAL_SECONDS, 5, "critical"),
            "forecast_poller": PollerState("forecast_poller", "Forecast", FORECAST_INTERVAL_SECONDS, 15),
            "nwp_poller": PollerState("nwp_poller", "NWP", NWP_INTERVAL_SECONDS, 30, "background"),
            "historical_poller": PollerState("historical_poller", "Historical", HISTORICAL_INTERVAL_SECONDS, 90),
            "pws_poller": PollerState("pws_poller", "PWS", PWS_INTERVAL_SECONDS, 120),
            "gamma_orderbook_poller": PollerState("gamma_orderbook_poller", "Orderbook", GAMMA_ORDERBOOK_INTERVAL_SECONDS, 45, "critical"),
            "gamma_discovery_poller": PollerState("gamma_discovery_poller", "Market Discovery", GAMMA_DISCOVERY_INTERVAL_SECONDS, 180, "background"),
            "derive_poller": PollerState("derive_poller", "Derived", DERIVE_INTERVAL_SECONDS, 420, "background"),
            "paper_settlement_poller": PollerState("paper_settlement_poller", "Paper Settlement", PAPER_SETTLEMENT_INTERVAL_SECONDS, 600),
            "paper_execution_poller": PollerState("paper_execution_poller", "Paper Validation", PAPER_EXECUTION_INTERVAL_SECONDS, 720),
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
            state.waiting_for_slot = False
            state.next_run_at = None
        return self.status(message="scheduler stopped")

    @property
    def running(self) -> bool:
        return any(state.task is not None and not state.task.done() for state in self.pollers.values())

    def update_source_health_cache(self, matrix: dict[str, Any]) -> None:
        self._source_health_cache = compact_source_health(matrix)
        self._source_health_cache_at = time.monotonic()

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
            queued_perf = time.perf_counter()
            state.waiting_for_slot = True
            try:
                slot = self._poller_slots.get(state.slot_group, self._poller_slots["source"])
                async with slot:
                    state.waiting_for_slot = False
                    state.last_queue_wait_ms = round((time.perf_counter() - queued_perf) * 1000)
                    self._active_pollers.add(poller_key)
                    return await self._run_once_in_slot(poller_key, state)
            finally:
                state.waiting_for_slot = False
                self._active_pollers.discard(poller_key)

    async def _run_once_in_slot(self, poller_key: str, state: PollerState) -> dict[str, Any]:
        state.running = True
        state.last_started_at = utc_now()
        started_perf = time.perf_counter()
        try:
            try:
                if poller_key == "metar_poller":
                    result = await self._run_metar_poller()
                elif poller_key == "forecast_poller":
                    result = await self._run_forecast_poller()
                elif poller_key == "nwp_poller":
                    result = await self._run_nwp_poller()
                elif poller_key == "historical_poller":
                    result = await self._run_historical_poller()
                elif poller_key == "pws_poller":
                    result = await self._run_pws_poller()
                elif poller_key == "derive_poller":
                    result = await self._run_derive_poller()
                elif poller_key == "china_live_poller":
                    result = await self._run_china_live_poller()
                elif poller_key == "gamma_orderbook_poller":
                    result = await self._run_gamma_orderbook_poller()
                elif poller_key == "gamma_discovery_poller":
                    result = await self._run_gamma_discovery_poller()
                elif poller_key == "model_timing_poller":
                    result = await self._run_model_timing_poller()
                elif poller_key == "paper_settlement_poller":
                    result = await self._run_paper_settlement_poller()
                elif poller_key == "paper_execution_poller":
                    result = await self._run_paper_execution_poller()
                else:
                    raise KeyError(poller_key)
            except Exception as exc:
                result = {"ok": False, "poller": poller_key, "error": str(exc), "results": []}
            duration_ms = round((time.perf_counter() - started_perf) * 1000)
            ok = bool(result.get("ok"))
            now = datetime.now(timezone.utc)
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
            state.last_log_error = await asyncio.to_thread(
                _safe_log_data_fetch,
                source="scheduler",
                stage=poller_key,
                status="OK" if ok else "WARN",
                duration_ms=duration_ms,
                message=state.last_message,
                details={
                    "poller": poller_key,
                    "interval_seconds": state.interval_seconds,
                    "queue_wait_ms": state.last_queue_wait_ms,
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
                "queue_wait_ms": state.last_queue_wait_ms,
                "next_run_at": state.next_run_at,
            }
        finally:
            state.running = False

    def status(self, *, message: str = "") -> dict[str, Any]:
        payload = {
            "ok": True,
            "scheduler_version": "weatherbot-scheduler-v1",
            "running": self.running,
            "started_at": self.started_at,
            "message": message,
            "city_concurrency": self.city_concurrency,
            "poller_concurrency": self.poller_concurrency,
            "critical_poller_concurrency": self.critical_poller_concurrency,
            "background_poller_concurrency": self.background_poller_concurrency,
            "poller_groups": {
                key: state.slot_group for key, state in self.pollers.items()
            },
            "active_pollers": sorted(self._active_pollers),
            "waiting_pollers": sorted(key for key, state in self.pollers.items() if state.waiting_for_slot),
            "pollers": {key: state.status() for key, state in self.pollers.items()},
        }
        # Status is called on the FastAPI event loop and must remain an
        # in-memory read. A full source-health scan touches a multi-GB SQLite
        # database and belongs on /api/source-health's worker thread.
        payload["source_health"] = self._source_health_cache or {
            "overall_status": "warming",
            "required_blockers": [],
        }
        return payload

    async def _poll_loop(self, poller_key: str) -> None:
        state = self.pollers[poller_key]
        if state.initial_delay_seconds > 0:
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
            cycle_started = time.monotonic()
            await self.run_once(poller_key)
            delay = _remaining_cycle_delay(state.next_delay(), time.monotonic() - cycle_started)
            state.next_run_at = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            ).isoformat()
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                continue

    async def _run_metar_poller(self) -> dict[str, Any]:
        run_count = self.pollers["metar_poller"].run_count
        all_rows = await asyncio.to_thread(
            _collection_rows,
            include_background=_background_due(run_count, METAR_BACKGROUND_MULTIPLIER),
        )
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows, cadence = _tiered_refresh_rows(
            all_rows,
            run_count=run_count,
            baseline_multiplier=METAR_BACKGROUND_MULTIPLIER,
            active_city_keys=active_city_keys or None,
        )

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
            return {
                "ok": _payload_ok(metar),
                "city": city,
                "station_id": row.get("station_id"),
                "metar": metar,
            }

        result = await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="metar_poller",
            timeout_seconds=METAR_CITY_TIMEOUT_SECONDS + 10,
        )
        return {**result, **cadence}

    async def _run_forecast_poller(self) -> dict[str, Any]:
        run_count = self.pollers["forecast_poller"].run_count
        all_rows = await asyncio.to_thread(
            _collection_rows,
            include_background=_background_due(run_count, BASELINE_REFRESH_MULTIPLIER),
        )
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows, cadence = _tiered_refresh_rows(
            all_rows,
            run_count=run_count,
            active_city_keys=active_city_keys or None,
        )

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city"))
            weathercom = await asyncio.to_thread(
                run_weathercom_fetch,
                city,
                dry_run=False,
                limit_cities=1,
                forecast_days=3,
                refresh_readiness=False,
            )
            return {
                "ok": _payload_ok(weathercom),
                "city": city,
                "station_id": row.get("station_id"),
                "weathercom": weathercom,
            }

        result = await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="forecast_poller",
            timeout_seconds=FORECAST_CITY_TIMEOUT_SECONDS,
        )
        return {**result, **cadence}

    async def _run_nwp_poller(self) -> dict[str, Any]:
        run_count = self.pollers["nwp_poller"].run_count
        all_rows = await asyncio.to_thread(
            _collection_rows,
            include_background=_background_due(run_count, NWP_BACKGROUND_MULTIPLIER),
        )
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows, cadence = _tiered_refresh_rows(
            all_rows,
            run_count=run_count,
            baseline_multiplier=NWP_BACKGROUND_MULTIPLIER,
            active_city_keys=active_city_keys or None,
        )
        ensemble_due_by_city = await asyncio.to_thread(_ensemble_due_by_city, rows)
        ensemble_due_cities = sorted(city for city, due in ensemble_due_by_city.items() if due)

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city"))
            ensemble_due = bool(ensemble_due_by_city.get(city, True))
            openmeteo = await asyncio.wait_for(
                asyncio.to_thread(
                    run_openmeteo_fetch,
                    city,
                    ensemble=False,
                    limit_cities=1,
                    forecast_days=7,
                    refresh_readiness=False,
                ),
                timeout=FORECAST_CITY_TIMEOUT_SECONDS,
            )
            ensemble = None
            if ensemble_due:
                ensemble = await asyncio.wait_for(
                    asyncio.to_thread(
                        run_openmeteo_fetch,
                        city,
                        ensemble=True,
                        models_arg=",".join(NWP_ENSEMBLE_MODELS),
                        limit_cities=1,
                        forecast_days=NWP_ENSEMBLE_FORECAST_DAYS,
                        refresh_readiness=False,
                    ),
                    timeout=FORECAST_CITY_TIMEOUT_SECONDS,
                )
            return {
                "ok": _payload_ok(openmeteo) and (not ensemble_due or _payload_ok(ensemble or {})),
                "city": city,
                "station_id": row.get("station_id"),
                "openmeteo": openmeteo,
                "ensemble": ensemble,
                "ensemble_due": ensemble_due,
            }

        result = await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="nwp_poller",
            timeout_seconds=FORECAST_CITY_TIMEOUT_SECONDS,
        )
        return {
            **result,
            **cadence,
            "ensemble_due": bool(ensemble_due_cities),
            "ensemble_due_cities": ensemble_due_cities,
            "ensemble_every_n_runs": NWP_ENSEMBLE_EVERY_N_RUNS,
            "ensemble_max_age_seconds": NWP_ENSEMBLE_MAX_AGE_SECONDS,
            "ensemble_models": list(NWP_ENSEMBLE_MODELS),
            "ensemble_forecast_days": NWP_ENSEMBLE_FORECAST_DAYS,
        }

    async def _run_historical_poller(self) -> dict[str, Any]:
        # Hong Kong keeps HKO as settlement truth, but VHHH WU history remains
        # useful display evidence and must not disappear from the operator UI.
        run_count = self.pollers["historical_poller"].run_count
        all_rows = list(await asyncio.to_thread(
            _collection_rows,
            include_background=_background_due(run_count, HISTORICAL_BACKGROUND_MULTIPLIER),
        ))
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows, cadence = _tiered_refresh_rows(
            all_rows,
            run_count=run_count,
            baseline_multiplier=HISTORICAL_BACKGROUND_MULTIPLIER,
            active_city_keys=active_city_keys or None,
        )

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city"))
            target_date = _local_today(row)
            previous_date = (datetime.fromisoformat(target_date).date() - timedelta(days=1)).isoformat()
            payload = await asyncio.to_thread(
                run_wunderground_hourly_fetch,
                city,
                target_date=target_date,
                limit_cities=1,
                dry_run=False,
                sync_registry=False,
            )
            daily_rollup = await asyncio.to_thread(
                run_wunderground_daily_rollup,
                city,
                target_date=previous_date,
                limit_cities=1,
                dry_run=False,
            )
            hourly_ok = _payload_ok(payload)
            daily_truth_ok = _payload_ok(daily_rollup)
            return {
                # The Historical badge describes the current-day display feed.
                # Previous-day truth completeness remains separately auditable.
                "ok": hourly_ok,
                "city": city,
                "station_id": row.get("station_id"),
                "target_date": target_date,
                "historical": payload,
                "daily_truth_rollup": daily_rollup,
                "daily_truth_ok": daily_truth_ok,
                "daily_truth_failed": 0 if daily_truth_ok else 1,
            }

        result = await _run_city_batch(
            rows,
            # WU fetches write both source audit rows and hourly observations.
            # SQLite has one writer, so concurrent city workers only add lock
            # waits and can misclassify a successful HTTP response as failed.
            1,
            run_city,
            poller_key="historical_poller",
            timeout_seconds=FORECAST_CITY_TIMEOUT_SECONDS,
        )
        daily_truth_failures = sum(
            int((item.get("payload") or {}).get("daily_truth_failed") or 0)
            for item in result.get("results") or []
            if isinstance(item, dict)
        )
        calibration_refresh: dict[str, Any] = {
            "status": "not_due",
            "output_path": str(DEFAULT_BIAS_TABLE),
        }
        if _bias_refresh_due():
            try:
                trained = await asyncio.to_thread(train_bias_table)
                rows_trained = list(trained.get("rows") or [])
                latest_sample_date = max(
                    (
                        str(sample_date)
                        for row in rows_trained
                        for sample_date in (row.get("sample_dates") or [])
                        if sample_date
                    ),
                    default="",
                )
                calibration_refresh = {
                    "status": "updated",
                    "generated_at": str(trained.get("generated_at") or ""),
                    "row_count": int(trained.get("row_count") or len(rows_trained)),
                    "city_count": int(trained.get("city_count") or 0),
                    "runtime_eligible_rows": int(trained.get("runtime_eligible_rows") or 0),
                    "latest_sample_date": latest_sample_date,
                    "output_path": str(DEFAULT_BIAS_TABLE),
                }
            except Exception as exc:
                calibration_refresh = {
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "output_path": str(DEFAULT_BIAS_TABLE),
                }
        return {
            **result,
            **cadence,
            "daily_truth_failed_cities": daily_truth_failures,
            "calibration_refresh": calibration_refresh,
        }

    async def _run_pws_poller(self) -> dict[str, Any]:
        run_count = self.pollers["pws_poller"].run_count
        all_rows = await asyncio.to_thread(
            _collection_rows,
            include_background=_background_due(run_count, PWS_BACKGROUND_MULTIPLIER),
        )
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows, cadence = _tiered_refresh_rows(
            all_rows,
            run_count=run_count,
            baseline_multiplier=PWS_BACKGROUND_MULTIPLIER,
            active_city_keys=active_city_keys or None,
        )
        if not env_value("WUNDERGROUND_API_KEY"):
            return {
                "ok": True,
                "cities": len(rows),
                "ok_cities": 0,
                "failed_cities": 0,
                "result_count": 0,
                "city_results": [],
                "skipped": True,
                "reason": "missing_wunderground_api_key",
                **cadence,
            }
        cooldown_remaining = max(0, round(self._pws_auth_disabled_until - time.monotonic()))
        if cooldown_remaining:
            return {
                "ok": True,
                "cities": len(rows),
                "ok_cities": 0,
                "failed_cities": 0,
                "result_count": 0,
                "city_results": [],
                "skipped": True,
                "reason": "pws_auth_cooldown",
                "retry_after_seconds": cooldown_remaining,
                **cadence,
            }

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city"))
            if self._pws_auth_disabled_until > time.monotonic():
                return {
                    "ok": True,
                    "city": city,
                    "station_id": row.get("station_id"),
                    "skipped": True,
                    "reason": "pws_auth_cooldown",
                }
            payload = await asyncio.wait_for(
                asyncio.to_thread(
                    run_pws_fetch,
                    city,
                    dry_run=False,
                    all_cities=False,
                    limit_cities=1,
                    station_limit=5,
                    refresh_readiness=False,
                ),
                timeout=PWS_OPTIONAL_TIMEOUT_SECONDS,
            )
            if _pws_auth_failure(payload):
                self._pws_auth_disabled_until = time.monotonic() + PWS_AUTH_COOLDOWN_SECONDS
            return {
                "ok": _payload_ok(payload),
                "city": city,
                "station_id": row.get("station_id"),
                "pws": payload,
            }

        result = await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="pws_poller",
            timeout_seconds=PWS_OPTIONAL_TIMEOUT_SECONDS + 10,
        )
        return {**result, **cadence}

    async def _run_derive_poller(self) -> dict[str, Any]:
        all_rows = await asyncio.to_thread(_collection_rows)
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows = (
            [
                row for row in all_rows
                if str(row.get("city_key") or row.get("city") or "") in active_city_keys
            ]
            if active_city_keys
            else all_rows
        )
        targets_by_city = {
            str(row.get("city_key") or row.get("city") or ""): _target_dates_for_station(row)
            for row in rows
        }

        async def run_city(row: dict[str, Any]) -> dict[str, Any]:
            city = str(row.get("city_key") or row.get("city") or "")
            city_target_dates = targets_by_city.get(city) or []
            date_results = []
            for target_date in city_target_dates:
                hourly = await asyncio.to_thread(
                    run_hourly_consensus_build,
                    city,
                    target_date,
                    limit_cities=1,
                    refresh_readiness=False,
                )
                daily = await asyncio.to_thread(
                    run_daily_max_build,
                    city,
                    target_date,
                    limit_cities=1,
                    refresh_readiness=False,
                )
                decisions = await asyncio.to_thread(
                    run_signal_decisions_build,
                    city,
                    target_date,
                    limit_cities=1,
                    limit=120,
                    refresh_readiness=False,
                )
                decision_results = decisions.get("results") if isinstance(decisions.get("results"), list) else []
                bucket_count = int(decision_results[0].get("bucket_count") or 0) if decision_results else 0
                date_results.append({
                    "target_date": target_date,
                    "hourly_rows": hourly.get("rows_upserted") or hourly.get("rows_built") or 0,
                    "daily_stored": daily.get("stored") or daily.get("stored_count") or 0,
                    "market_buckets": bucket_count,
                    "signal_decisions": decisions.get("stored") or decisions.get("decisions") or 0,
                    "ok": bucket_count > 0 and all(_payload_ok(item) for item in (hourly, daily, decisions)),
                })
            return {
                "ok": all(item.get("ok") for item in date_results),
                "city": city,
                "station_id": row.get("station_id"),
                "target_dates": city_target_dates,
                "dates": date_results,
            }

        result = await _run_city_batch(
            rows,
            self.city_concurrency,
            run_city,
            poller_key="derive_poller",
            timeout_seconds=DERIVE_CITY_TIMEOUT_SECONDS,
        )
        # A full production-readiness audit is intentionally operator-driven.
        # It scans cross-layer history and must not run every 15 minutes on the
        # collector hot path. Source and decision gates are persisted by their
        # own builders; `data-readiness` remains available as an explicit CLI.
        result["readiness_refreshed"] = False
        result["readiness_reason"] = "explicit_audit_only"
        return result

    async def _run_china_live_poller(self) -> dict[str, Any]:
        supported = set(supported_china_live_cities())
        rows = [
            row for row in await asyncio.to_thread(_collection_rows, include_background=True)
            if str(row.get("city_key") or row.get("city") or "") in supported
        ]
        return await _run_city_batch(
            rows,
            # HTTP latency dominates this poller. Database initialization is
            # process-idempotent and each observation UPSERT is a short WAL
            # transaction, so three bounded workers keep nine sources inside
            # the minute cadence without creating an unbounded write burst.
            min(3, self.city_concurrency),
            lambda row: asyncio.to_thread(
                fetch_china_weather_city,
                str(row.get("city_key") or row.get("city")),
            ),
            poller_key="china_live_poller",
            timeout_seconds=CHINA_LIVE_CITY_TIMEOUT_SECONDS,
        )

    async def _run_gamma_orderbook_poller(self) -> dict[str, Any]:
        all_rows = await asyncio.to_thread(_collection_rows, include_background=True)
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows = [
            row for row in all_rows
            if not active_city_keys
            or str(row.get("city_key") or row.get("city") or "") in active_city_keys
        ]
        targets_by_city = {
            str(row.get("city_key") or row.get("city") or ""): _target_dates_for_station(row)
            for row in rows
        }
        result = await asyncio.to_thread(
            refresh_cached_market_bucket_orderbooks,
            targets_by_city=targets_by_city,
            limit=10_000,
            dry_run=False,
        )
        result["candidate_preclose_quotes"] = await asyncio.to_thread(
            snapshot_candidate_preclose_quotes
        )
        result["active_market_cities"] = len(targets_by_city)
        return result

    async def _run_gamma_discovery_poller(self) -> dict[str, Any]:
        run_count = self.pollers["gamma_discovery_poller"].run_count
        full_discovery = _background_due(run_count, GAMMA_DISCOVERY_MULTIPLIER)
        all_rows = await asyncio.to_thread(_collection_rows, include_background=full_discovery)
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        discovery_cities = (
            [str(row.get("city_key") or row.get("city") or "") for row in all_rows]
            if full_discovery
            else sorted(active_city_keys) or [str(row.get("city_key") or row.get("city") or "") for row in all_rows]
        )
        structured = await asyncio.to_thread(
            run_gamma_structured_sync,
            ",".join(discovery_cities),
            days=3,
            dry_run=False,
            fetch_orderbooks=True,
        )
        active_city_keys = await asyncio.to_thread(_active_market_city_keys)
        rows = (
            [
                row for row in all_rows
                if str(row.get("city_key") or row.get("city") or "") in active_city_keys
            ]
            if active_city_keys
            else all_rows
        )
        targets_by_city = {
            str(row.get("city_key") or row.get("city") or ""): _target_dates_for_station(row)
            for row in rows
        }
        active_batches: list[dict[str, Any]] = []
        for target_date in sorted({target for targets in targets_by_city.values() for target in targets}):
            target_cities = [city for city, targets in targets_by_city.items() if target_date in targets]
            payload = await asyncio.to_thread(
                run_market_buckets_sync,
                1000,
                cities_arg=",".join(target_cities),
                target_date=target_date,
                active_weather=True,
                limit_cities=len(target_cities),
                fetch_orderbooks=True,
                refresh_readiness=False,
            )
            active_batches.append({
                "ok": _payload_ok(payload),
                "target_date": target_date,
                "cities": target_cities,
                "stored": int(payload.get("stored") or 0),
                "orderbook_ok": int(payload.get("orderbook_ok") or 0),
                "failed": int(payload.get("failed") or payload.get("events_failed") or 0),
            })
        structured_failures = list(structured.get("failures") or [])
        book_gaps = [
            failure
            for failure in structured_failures
            if str(failure.get("error") or "") == "clob_batch_book_missing"
        ]
        failures = [failure for failure in structured_failures if failure not in book_gaps]
        failures.extend(
            {
                "stage": "active_market_buckets",
                "target_date": batch["target_date"],
                "failed": batch["failed"],
            }
            for batch in active_batches
            if not batch["ok"]
        )
        return {
            "ok": not failures and all(batch["ok"] for batch in active_batches),
            "events_stored": int(structured.get("events_stored") or 0),
            "markets_stored": int(structured.get("markets_stored") or 0),
            "orderbooks_stored": int(structured.get("orderbooks_stored") or 0),
            "market_buckets_stored": sum(batch["stored"] for batch in active_batches),
            "active_orderbooks": sum(batch["orderbook_ok"] for batch in active_batches),
            "active_batches": active_batches,
            "book_gaps": book_gaps,
            "failures": failures,
            "discovery_scope": "all_registered" if full_discovery else "active_markets",
            "discovery_cities": len(discovery_cities),
        }

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

    async def _run_paper_settlement_poller(self) -> dict[str, Any]:
        settlement = await asyncio.to_thread(
            settle_open_paper_orders,
            limit=1000,
            refresh_gamma=True,
            apply=True,
        )
        exits = await asyncio.to_thread(
            evaluate_open_paper_exits,
            limit=1000,
            apply=True,
        )
        return {
            **settlement,
            "ok": bool(settlement.get("ok", True)) and bool(exits.get("ok", True)),
            "paper_exits": exits,
            "exited_now": int(exits.get("exited_now") or 0),
        }

    async def _run_paper_execution_poller(self) -> dict[str, Any]:
        return await asyncio.to_thread(run_paper_validation_tick, apply=True)


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
                compact_payload = _compact_city_payload(payload)
                details = {**compact_payload, "poller": poller_key}
                log_error = await asyncio.to_thread(
                    _safe_log_data_fetch,
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
                return {"city": city, "ok": ok, "payload": compact_payload, "log_error": log_error}
            except asyncio.TimeoutError:
                finished = utc_now()
                error = {
                    "city": city,
                    "ok": False,
                    "poller": poller_key,
                    "error": f"city_timeout_{int(timeout_seconds)}s",
                }
                log_error = await asyncio.to_thread(
                    _safe_log_data_fetch,
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
                return {**error, "log_error": log_error}
            except Exception as exc:
                finished = utc_now()
                error = {"city": city, "ok": False, "poller": poller_key, "error": str(exc)}
                log_error = await asyncio.to_thread(
                    _safe_log_data_fetch,
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
                return {**error, "log_error": log_error}

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


def _collection_rows(*, include_background: bool = False) -> list[dict[str, Any]]:
    """Return active watchlist rows, optionally including every visible city."""
    if not include_background:
        return _enabled_rows()
    rows = [row for row in list_stations() if row.get("display_enabled", True)]
    if not rows:
        sync_station_registry()
        rows = [row for row in list_stations() if row.get("display_enabled", True)]
    rows.sort(key=lambda row: (0 if row.get("enabled") else 1, int(row.get("tier") or 9), str(row.get("city_name") or "")))
    return rows


def _background_due(run_count: int, multiplier: int) -> bool:
    """Defer full-registry work until a completed fast cycle exists."""
    count = max(0, int(run_count or 0))
    every = max(1, int(multiplier or 1))
    return count > 0 and count % every == 0


def _active_market_city_keys() -> set[str]:
    """Read current structured events; stale historical probes must not drive work."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT city
                FROM polymarket_events
                WHERE target_date >= ?
                  AND COALESCE(json_extract(raw_json, '$.active'), 1) = 1
                  AND COALESCE(json_extract(raw_json, '$.closed'), 0) = 0
                """,
                (today,),
            ).fetchall()
        return {str(row[0] or "") for row in rows if str(row[0] or "")}
    except Exception:
        return set()


def _safe_log_data_fetch(**kwargs: Any) -> str:
    """Audit logging must never turn a completed collector run into a crash."""
    try:
        log_data_fetch(**kwargs)
        return ""
    except Exception as exc:
        return str(exc)


def _remaining_cycle_delay(interval_seconds: int | float, elapsed_seconds: int | float) -> float:
    """Coalesce missed ticks instead of immediately chasing an overdue cycle."""
    interval = max(1.0, float(interval_seconds))
    elapsed = max(0.0, float(elapsed_seconds))
    return interval - elapsed if elapsed < interval else interval


def _tiered_refresh_rows(
    rows: list[dict[str, Any]],
    *,
    run_count: int,
    baseline_multiplier: int = BASELINE_REFRESH_MULTIPLIER,
    active_city_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Refresh active markets every cycle and the full watchlist every N cycles."""
    multiplier = max(1, int(baseline_multiplier or 1))
    full_refresh = int(run_count or 0) % multiplier == 0
    active_rows = [row for row in rows if _station_has_active_market(row, active_city_keys=active_city_keys)]
    selected = list(rows) if full_refresh else active_rows
    selected_keys = {
        str(row.get("city_key") or row.get("city") or "")
        for row in selected
    }
    deferred = [
        str(row.get("city_key") or row.get("city") or "")
        for row in rows
        if str(row.get("city_key") or row.get("city") or "") not in selected_keys
    ]
    return selected, {
        "refresh_scope": "full_watchlist" if full_refresh else "active_markets",
        "enabled_cities": len(rows),
        "active_market_cities": len(active_rows),
        "deferred_cities": deferred,
        "baseline_every_cycles": multiplier,
    }


def _station_has_active_market(row: dict[str, Any], *, active_city_keys: set[str] | None = None) -> bool:
    city_key = str(row.get("city_key") or row.get("city") or "")
    if active_city_keys is not None:
        return city_key in active_city_keys
    raw = row.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        return False
    probe = raw.get("latest_market_probe")
    if not isinstance(probe, dict):
        return False
    return bool(probe.get("active_market")) or str(probe.get("status") or "") == "active_market"


def _target_dates_for_station(row: dict[str, Any]) -> list[str]:
    timezone_name = str(row.get("timezone") or "UTC")
    try:
        local_today = datetime.now(ZoneInfo(timezone_name)).date()
    except Exception:
        local_today = datetime.now(timezone.utc).date()
    return [local_today.isoformat(), (local_today + timedelta(days=1)).isoformat()]


def _local_today(row: dict[str, Any]) -> str:
    timezone_name = str(row.get("settlement_timezone") or row.get("timezone") or "UTC")
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = timezone.utc
    return datetime.now(timezone.utc).astimezone(zone).date().isoformat()


def _payload_ok(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("ok") is False:
        return False
    if int(payload.get("failed") or payload.get("failed_cities") or 0) > 0:
        return False
    failures = payload.get("failures")
    return not failures


def _compact_city_payload(payload: Any) -> dict[str, Any]:
    """Keep scheduler state/logs bounded; full collector payloads already live in SQLite."""
    if not isinstance(payload, dict):
        return {"ok": False, "error": "collector_payload_not_object"}
    compact: dict[str, Any] = {}
    nested = next(
        (
            payload[key]
            for key in ("metar", "weathercom", "openmeteo", "historical", "pws")
            if isinstance(payload.get(key), dict)
        ),
        {},
    )
    for key in (
        "ok",
        "city",
        "station_id",
        "target_date",
        "status",
        "skipped",
        "reason",
        "error",
        "message",
        "provider",
        "observed_at",
        "fallback",
        "reports_fetched",
        "reports_upserted",
        "rows_fetched",
        "rows_upserted",
        "rows_inserted",
        "rows_updated",
        "source_observation_new",
        "source_observation_changed",
        "source_unchanged",
        "rows_built",
        "stored",
        "stored_count",
        "failed",
        "failed_cities",
        "daily_truth_ok",
        "daily_truth_failed",
        "events_stored",
        "markets_stored",
        "orderbooks_stored",
    ):
        if key in payload:
            compact[key] = payload[key]
        elif key in nested:
            compact[key] = nested[key]
    stations = payload.get("stations")
    if isinstance(stations, list):
        compact["stations"] = [str(value) for value in stations[:5]]
    results = payload.get("results")
    if isinstance(results, list):
        compact["result_count"] = len(results)
        compact["result_errors"] = [
            str(row.get("error") or row.get("reason") or "")
            for row in results
            if isinstance(row, dict) and (row.get("error") or row.get("reason"))
        ][:5]
    dates = payload.get("dates")
    if isinstance(dates, list):
        compact["dates"] = [
            {
                key: row.get(key)
                for key in ("target_date", "ok", "hourly_rows", "daily_stored", "market_buckets", "signal_decisions")
                if key in row
            }
            for row in dates[:3]
            if isinstance(row, dict)
        ]
    return compact


def _pws_auth_failure(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    try:
        text = json.dumps(payload, ensure_ascii=False).lower()
    except Exception:
        text = str(payload).lower()
    return "401" in text or "unauthorized" in text or "forbidden" in text


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
            "rows_inserted": payload_result.get("rows_inserted"),
            "rows_updated": payload_result.get("rows_updated"),
            "source_observation_new": payload_result.get("source_observation_new"),
            "source_observation_changed": payload_result.get("source_observation_changed"),
            "source_unchanged": payload_result.get("source_unchanged"),
            "failed": payload_result.get("failed") or payload_result.get("failed_cities"),
            "daily_truth_ok": payload_result.get("daily_truth_ok"),
            "daily_truth_failed": payload_result.get("daily_truth_failed"),
        })
    compact = {
        "ok": bool(payload.get("ok")),
        "cities": int(payload.get("cities") or len(results) or 0),
        "ok_cities": int(payload.get("ok_cities") or 0),
        "failed_cities": int(payload.get("failed_cities") or payload.get("failed") or 0),
        "result_count": len(results),
        "city_results": city_results,
    }
    for key in (
        "refresh_scope",
        "enabled_cities",
        "active_market_cities",
        "deferred_cities",
        "baseline_every_cycles",
        "daily_truth_failed_cities",
        "calibration_refresh",
        "cached_buckets",
        "tokens_requested",
        "quotes_refreshed",
        "quotes_missing",
        "quote_timestamp_min",
        "quote_timestamp_max",
        "events_stored",
        "markets_stored",
        "orderbooks_stored",
        "market_buckets_stored",
        "active_orderbooks",
        "discovery_scope",
        "discovery_cities",
    ):
        if key in payload:
            compact[key] = payload[key]
    return compact


def _poller_message(poller_key: str, result: dict[str, Any]) -> str:
    if poller_key == "paper_settlement_poller":
        return (
            f"{poller_key} completed: {int(result.get('resolved_now') or 0)} resolved, "
            f"{int(result.get('provisional_now') or 0)} provisional, "
            f"{int(result.get('pending_now') or 0)} pending, "
            f"{int(result.get('exited_now') or 0)} model exits"
        )
    if poller_key == "paper_execution_poller":
        return (
            f"{poller_key} {result.get('status') or 'completed'}: "
            f"{int(result.get('executed') or 0)} executed from "
            f"{int(result.get('candidate_count') or 0)} candidates"
        )
    if poller_key == "gamma_orderbook_poller":
        return (
            f"{poller_key} completed: {int(result.get('quotes_refreshed') or 0)} fresh books, "
            f"{int(result.get('quotes_missing') or 0)} missing"
            if _payload_ok(result)
            else (
                f"{poller_key} completed with {int(result.get('quotes_missing') or 0)} missing books"
                if result.get("quotes_refreshed")
                else f"{poller_key} failed: {result.get('error') or result.get('reason') or 'no fresh books'}"
            )
        )
    if poller_key == "gamma_discovery_poller":
        return (
            f"{poller_key} completed: {int(result.get('events_stored') or 0)} events, "
            f"{int(result.get('market_buckets_stored') or 0)} buckets"
            if _payload_ok(result)
            else f"{poller_key} completed with {len(result.get('failures') or [])} failures"
        )
    if poller_key == "china_live_poller":
        summary = _compact_result(result)
        new_points = sum(int(row.get("rows_inserted") or 0) for row in summary["city_results"])
        unchanged = sum(1 for row in summary["city_results"] if row.get("source_unchanged") is True)
        return (
            f"{poller_key} completed: {new_points} new source points, {unchanged} unchanged, "
            f"{summary['failed_cities']} failed"
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


def _ensemble_due_by_city(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    max_age_seconds: int = NWP_ENSEMBLE_MAX_AGE_SECONDS,
    path: Path | None = None,
) -> dict[str, bool]:
    """Use persisted ensemble age so service restarts do not reset cadence."""

    cities = sorted({
        str(row.get("city_key") or row.get("city") or "").strip()
        for row in rows
        if str(row.get("city_key") or row.get("city") or "").strip()
    })
    if not cities:
        return {}
    placeholders = ",".join("?" for _city in cities)
    with connect(path) as conn:
        latest_rows = conn.execute(
            f"""
            SELECT city, MAX(COALESCE(available_at, retrieved_at)) AS latest_at
            FROM forecast_runs
            WHERE city IN ({placeholders})
              AND source LIKE 'openmeteo_ensemble_%'
              AND COALESCE(parse_status, 'parsed') = 'parsed'
            GROUP BY city
            """,
            tuple(cities),
        ).fetchall()
    latest_by_city = {
        str(row["city"]): _parse_time(str(row["latest_at"] or ""))
        for row in latest_rows
    }
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    bounded_age = max(NWP_INTERVAL_SECONDS, int(max_age_seconds or NWP_ENSEMBLE_MAX_AGE_SECONDS))
    due: dict[str, bool] = {}
    for city in cities:
        latest = latest_by_city.get(city)
        due[city] = latest is None or (current - latest.astimezone(timezone.utc)).total_seconds() >= bounded_age
    return due


def _bias_refresh_due(
    *,
    output_path: Path = DEFAULT_BIAS_TABLE,
    now: datetime | None = None,
    max_age_hours: float = BIAS_RETRAIN_MAX_AGE_HOURS,
) -> bool:
    """Refresh the leakage-free calibration artifact roughly once per day."""
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        generated_at = _parse_time(str(payload.get("generated_at") or ""))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return True
    if generated_at is None:
        return True
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (current - generated_at.astimezone(timezone.utc)).total_seconds() >= max(1.0, max_age_hours) * 3600


_SCHEDULER: WeatherBotScheduler | None = None


def get_scheduler() -> WeatherBotScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = WeatherBotScheduler()
    return _SCHEDULER
