from __future__ import annotations

import json
from contextlib import nullcontext
import hashlib
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR, load_config
from .forecast_time import persisted_prediction_cohort_status


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: Path | None = None) -> sqlite3.Connection:
    cfg = load_config()
    db_path = path or cfg.v3_db_path
    db_path.parent.mkdir(exist_ok=True)
    timeout_seconds = 30.0
    try:
        import os

        timeout_seconds = max(1.0, float(os.getenv("WEATHERBOT_SQLITE_TIMEOUT_SECONDS", "30")))
    except Exception:
        timeout_seconds = 30.0
    conn = sqlite3.connect(db_path, timeout=timeout_seconds, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError:
        pass
    return conn


def connect_readonly(path: Path | None = None) -> sqlite3.Connection:
    """Open the production database without permitting writes or WAL changes."""
    cfg = load_config()
    db_path = Path(path or cfg.v3_db_path).resolve()
    conn = sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro",
        uri=True,
        timeout=30.0,
        factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_v3_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS markets (
                market_id TEXT PRIMARY KEY,
                event_slug TEXT,
                event_url TEXT,
                question TEXT,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                bucket_label TEXT,
                yes_token_id TEXT,
                no_token_id TEXT,
                order_min_size REAL,
                tick_size REAL,
                enable_order_book INTEGER,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                city TEXT,
                target_date TEXT,
                source TEXT,
                model_probability REAL,
                ensemble_mean REAL,
                ensemble_std REAL,
                ensemble_members INTEGER,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orderbooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_key TEXT UNIQUE,
                market_id TEXT,
                yes_token_id TEXT,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                volume REAL,
                order_min_size REAL,
                tick_size REAL,
                enable_order_book INTEGER,
                snapshot_type TEXT,
                quote_timestamp TEXT,
                book_hash TEXT,
                bids_json TEXT,
                asks_json TEXT,
                bid_depth REAL,
                ask_depth REAL,
                source_url TEXT,
                raw_response_hash TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_signal_id INTEGER,
                signal_key TEXT UNIQUE,
                market_id TEXT,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                bucket_label TEXT,
                event_url TEXT,
                yes_token_id TEXT,
                model_probability REAL,
                market_probability REAL,
                probability_edge REAL,
                ev REAL,
                kelly REAL,
                suggested_size REAL,
                quality_score REAL,
                status TEXT DEFAULT 'candidate',
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stations (
                city_key TEXT PRIMARY KEY,
                city_name TEXT NOT NULL,
                station_id TEXT NOT NULL,
                icao_id TEXT,
                wmo_id TEXT,
                provider_station_ids_json TEXT,
                station_name TEXT NOT NULL,
                timezone TEXT NOT NULL,
                unit TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                region TEXT,
                expected_metric TEXT,
                settlement_rule_text TEXT,
                settlement_station_id TEXT,
                settlement_station_name TEXT,
                settlement_timezone TEXT,
                settlement_unit TEXT,
                settlement_time_basis TEXT,
                settlement_rule_verified_at TEXT,
                primary_settlement_source TEXT,
                nearby_observation_networks_json TEXT,
                confidence REAL,
                verification_status TEXT,
                display_enabled INTEGER DEFAULT 1,
                city_scope TEXT DEFAULT 'market_candidate',
                enabled INTEGER DEFAULT 0,
                tier INTEGER DEFAULT 9,
                registry_version TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                provider TEXT,
                model TEXT,
                approve INTEGER,
                confidence REAL,
                summary TEXT,
                reasons TEXT,
                vetoes TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_profile_revisions (
                revision_id TEXT PRIMARY KEY,
                profile_key TEXT NOT NULL,
                revision_no INTEGER NOT NULL,
                parent_revision_id TEXT,
                schema_version INTEGER NOT NULL,
                engine_version TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                strategy_names_json TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                validation_report_json TEXT NOT NULL,
                code_commit_sha TEXT,
                created_by TEXT NOT NULL,
                change_note TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(profile_key, revision_no),
                UNIQUE(profile_key, content_sha256)
            );

            CREATE TABLE IF NOT EXISTS strategy_profile_activation_events (
                activation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                revision_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT,
                signal_id INTEGER,
                idempotency_key TEXT UNIQUE,
                market_id TEXT,
                yes_token_id TEXT,
                bucket_key TEXT,
                strategy_name TEXT,
                ladder_group_id TEXT,
                strategy_revision_id TEXT,
                strategy_params_hash TEXT,
                strategy_params_snapshot_json TEXT,
                sizing_snapshot_json TEXT,
                execution_quote_json TEXT,
                cap_reasons_json TEXT,
                city_key TEXT,
                target_date TEXT,
                event_url TEXT,
                side TEXT,
                limit_price REAL,
                requested_amount REAL,
                amount REAL,
                shares REAL,
                filled_amount REAL,
                filled_shares REAL,
                unfilled_amount REAL,
                average_fill_price REAL,
                mark_price REAL,
                unrealized_pnl REAL,
                realized_pnl REAL,
                status TEXT,
                lifecycle_status TEXT,
                fill_status TEXT,
                order_version TEXT,
                model_probability REAL,
                market_probability REAL,
                edge REAL,
                gate_status TEXT,
                failure_reason TEXT,
                risk_reasons_json TEXT,
                orderbook_snapshot_json TEXT,
                evidence_links_json TEXT,
                raw_json TEXT,
                opened_at TEXT,
                closed_at TEXT,
                cohort_run_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_validation_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                stopped_at TEXT,
                bankroll_usd REAL NOT NULL,
                max_per_trade_usd REAL NOT NULL,
                daily_max_usd REAL NOT NULL,
                max_open_positions INTEGER NOT NULL,
                max_orders_per_day INTEGER NOT NULL,
                decision_max_age_minutes REAL NOT NULL,
                cities_json TEXT,
                strategies_json TEXT,
                strategy_revision_id TEXT,
                strategy_profile_snapshot_json TEXT,
                kelly_multiplier REAL,
                bankroll_fraction_cap REAL,
                execution_version TEXT NOT NULL,
                notes TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS live_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                idempotency_key TEXT UNIQUE,
                market_id TEXT,
                yes_token_id TEXT,
                side TEXT,
                limit_price REAL,
                amount REAL,
                shares REAL,
                status TEXT,
                dry_run INTEGER,
                clob_order_id TEXT,
                failure_reason TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT UNIQUE,
                order_id INTEGER,
                order_type TEXT,
                decision_id TEXT,
                market_id TEXT,
                yes_token_id TEXT,
                fill_status TEXT,
                price REAL,
                shares REAL,
                amount REAL,
                source TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                settlement_key TEXT UNIQUE,
                paper_order_id INTEGER,
                decision_id TEXT,
                market_id TEXT,
                yes_token_id TEXT,
                city_key TEXT,
                target_date TEXT,
                result TEXT,
                outcome_yes INTEGER,
                settlement_status TEXT,
                settlement_source TEXT,
                actual_temp REAL,
                actual_provider TEXT,
                actual_station TEXT,
                actual_confidence REAL,
                calibration_eligible INTEGER,
                payout REAL,
                pnl REAL,
                brier_score REAL,
                market_brier_score REAL,
                settled_at TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS market_rules (
                market_id TEXT PRIMARY KEY,
                event_slug TEXT,
                market_slug TEXT,
                question TEXT,
                city TEXT,
                city_name TEXT,
                station_id TEXT,
                station_name TEXT,
                timezone TEXT,
                unit TEXT,
                bucket_low REAL,
                bucket_high REAL,
                metric TEXT,
                resolution_source_text TEXT,
                source_url TEXT,
                truth_confidence REAL,
                confidence_reason TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                station_id TEXT,
                station_name TEXT,
                unit TEXT,
                actual_temp REAL,
                provider TEXT,
                source_url TEXT,
                observation_count INTEGER,
                source_confidence REAL,
                calibration_eligible INTEGER,
                reason_if_ineligible TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(city, target_date, station_id, provider)
            );

            CREATE TABLE IF NOT EXISTS truth_observation_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truth_key TEXT NOT NULL,
                truth_version TEXT NOT NULL,
                supersedes_truth_id INTEGER,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                station_id TEXT,
                station_name TEXT,
                unit TEXT,
                actual_temp REAL,
                provider TEXT,
                source_url TEXT,
                observation_count INTEGER,
                source_confidence REAL,
                calibration_eligible INTEGER,
                reason_if_ineligible TEXT,
                observed_at TEXT,
                retrieved_at TEXT,
                is_preliminary INTEGER,
                is_final INTEGER,
                quality_flags TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(truth_key, truth_version)
            );

            CREATE TABLE IF NOT EXISTS settlement_contracts (
                contract_id TEXT PRIMARY KEY,
                event_slug TEXT UNIQUE,
                city TEXT,
                city_name TEXT,
                target_local_date TEXT,
                station_id TEXT,
                station_name TEXT,
                timezone TEXT,
                unit TEXT,
                metric TEXT,
                rounding_rule TEXT,
                bucket_boundary TEXT,
                resolution_source_text TEXT,
                source_url TEXT,
                truth_provider_priority TEXT,
                rule_version TEXT,
                registry_version TEXT,
                parse_confidence REAL,
                confidence_reason TEXT,
                auto_verified_at TEXT,
                manual_verified_at TEXT,
                manual_verified_by TEXT,
                manual_verification_note TEXT,
                manual_verification_snapshot TEXT,
                verification_evidence TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecast_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_key TEXT UNIQUE,
                snapshot_key TEXT UNIQUE,
                city TEXT,
                target_date TEXT,
                source TEXT,
                provider TEXT,
                model TEXT,
                model_version TEXT,
                run_type TEXT,
                run_at TEXT,
                retrieved_at TEXT,
                available_at TEXT,
                availability_basis TEXT,
                valid_at TEXT,
                horizon TEXT,
                lead_hours REAL,
                latitude REAL,
                longitude REAL,
                station_id TEXT,
                timezone TEXT,
                unit TEXT,
                mean_high REAL,
                std_high REAL,
                member_count INTEGER,
                source_url TEXT,
                raw_response_hash TEXT,
                data_license TEXT,
                quality_flags TEXT,
                parser_version TEXT,
                parse_status TEXT,
                parse_warnings TEXT,
                source_unit TEXT,
                training_eligible INTEGER,
                ineligibility_reason TEXT,
                quarantined_at TEXT,
                quarantine_reason TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forecast_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                member_name TEXT,
                high_temp REAL,
                member_id TEXT,
                hourly_json TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, member_id)
            );

            CREATE TABLE IF NOT EXISTS metar_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_key TEXT UNIQUE,
                city TEXT,
                city_name TEXT,
                station_id TEXT,
                report_type TEXT,
                report_time TEXT,
                raw_text TEXT,
                temperature REAL,
                dew_point REAL,
                wind_direction REAL,
                wind_speed REAL,
                wind_gust REAL,
                visibility REAL,
                cloud_layers_json TEXT,
                altimeter REAL,
                pressure REAL,
                precipitation REAL,
                sea_level_pressure REAL,
                peak_wind_json TEXT,
                source_url TEXT,
                parser_version TEXT,
                parse_status TEXT,
                parse_warnings TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mesonet_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_key TEXT UNIQUE,
                city TEXT,
                city_name TEXT,
                station_id TEXT,
                station_name TEXT,
                network TEXT,
                observed_at TEXT,
                temperature REAL,
                humidity REAL,
                dew_point REAL,
                wind_direction REAL,
                wind_speed REAL,
                wind_gust REAL,
                pressure REAL,
                precipitation REAL,
                source_url TEXT,
                raw_response TEXT,
                raw_response_hash TEXT,
                parser_version TEXT,
                parse_status TEXT,
                parse_warnings TEXT,
                raw_unit TEXT,
                quality_flags TEXT,
                fetched_at TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hourly_consensus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                consensus_key TEXT UNIQUE,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                local_hour TEXT,
                valid_time TEXT,
                station_id TEXT,
                forecast_temp REAL,
                observed_temp REAL,
                observation_source TEXT,
                humidity REAL,
                cloud_cover REAL,
                precipitation REAL,
                wind_speed REAL,
                wind_direction REAL,
                pressure REAL,
                dew_point REAL,
                residual REAL,
                forecast_spread REAL,
                forecast_member_count INTEGER,
                consensus_method TEXT,
                source_count INTEGER,
                source_weights_json TEXT,
                forecast_source TEXT,
                forecast_sources_json TEXT,
                observation_sources_json TEXT,
                source_mix_json TEXT,
                consensus_version TEXT,
                build_status TEXT,
                build_warnings TEXT,
                peak_marker TEXT,
                taf_marker TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_max_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_key TEXT UNIQUE,
                city_key TEXT,
                target_date TEXT,
                issued_at TEXT,
                mu REAL,
                sigma REAL,
                unit TEXT,
                method TEXT,
                model_weights_json TEXT,
                member_count INTEGER,
                components_json TEXT,
                source_run_ids_json TEXT,
                member_daily_highs_json TEXT,
                sigma_from_spread REAL,
                sigma_from_history REAL,
                bias_correction REAL,
                bias_sample_count INTEGER,
                deb_version TEXT,
                observed_floor REAL,
                sigma_floor REAL,
                time_decay_factor REAL,
                mu_observed_floor_applied INTEGER,
                peak_hour TEXT,
                peak_temp REAL,
                peak_source TEXT,
                validity_status TEXT,
                invalidated_at TEXT,
                invalidation_reason TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_buckets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket_key TEXT UNIQUE,
                event_slug TEXT,
                event_url TEXT,
                market_id TEXT,
                condition_id TEXT,
                question TEXT,
                city TEXT,
                city_name TEXT,
                target_date TEXT,
                station_id TEXT,
                unit TEXT,
                bucket_label TEXT,
                bucket_direction TEXT,
                bucket_low REAL,
                bucket_high REAL,
                outcome_name TEXT,
                yes_token_id TEXT,
                no_token_id TEXT,
                token_id TEXT,
                token_side TEXT,
                outcome_index INTEGER,
                price REAL,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                volume REAL,
                liquidity REAL,
                order_min_size REAL,
                tick_size REAL,
                neg_risk INTEGER,
                enable_order_book INTEGER,
                quote_timestamp TEXT,
                orderbook_snapshot_key TEXT,
                orderbook_source TEXT,
                bid_depth REAL,
                ask_depth REAL,
                source_url TEXT,
                raw_response_hash TEXT,
                strict_match_status TEXT,
                strict_match_reasons TEXT,
                parser_version TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_iem_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truth_key TEXT UNIQUE,
                icao TEXT,
                date_local TEXT,
                timezone TEXT,
                high_c REAL,
                low_c REAL,
                high_time_local TEXT,
                low_time_local TEXT,
                obs_count INTEGER,
                source_url TEXT,
                settlement_truth_type TEXT,
                parser_version TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_iem_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_key TEXT UNIQUE,
                icao TEXT,
                date_local TEXT,
                timezone TEXT,
                observed_at_local TEXT,
                observed_at_utc TEXT,
                temp_c REAL,
                tmpf REAL,
                raw_text TEXT,
                source_url TEXT,
                parser_version TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_wunderground_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truth_key TEXT UNIQUE,
                icao TEXT,
                date_local TEXT,
                timezone TEXT,
                high_c REAL,
                low_c REAL,
                source_url TEXT,
                method TEXT,
                settlement_truth_type TEXT,
                skip_reasons_json TEXT,
                parser_version TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_wunderground_hourly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_key TEXT UNIQUE,
                icao TEXT,
                date_local TEXT,
                timezone TEXT,
                observed_at_local TEXT,
                observed_at_utc TEXT,
                temp_c REAL,
                dew_point_c REAL,
                heat_index_c REAL,
                humidity REAL,
                pressure_hpa REAL,
                visibility_km REAL,
                wind_direction REAL,
                wind_speed_kph REAL,
                wind_gust_kph REAL,
                cloud_cover_pct REAL,
                condition TEXT,
                source_url TEXT,
                method TEXT,
                settlement_truth_type TEXT,
                parser_version TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_hko_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                truth_key TEXT UNIQUE,
                date_local TEXT,
                high_c REAL,
                low_c REAL,
                mean_c REAL,
                source_url TEXT,
                settlement_truth_type TEXT,
                parser_version TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS truth_delta_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_key TEXT UNIQUE,
                icao TEXT,
                city TEXT,
                date_local TEXT,
                wu_high_c REAL,
                iem_high_c REAL,
                hko_high_c REAL,
                polymarket_resolved_bucket TEXT,
                delta_wu_minus_iem REAL,
                delta_hko_minus_iem REAL,
                resolved_at TEXT,
                notes TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS polymarket_events (
                event_id TEXT PRIMARY KEY,
                slug TEXT UNIQUE,
                city TEXT,
                target_date TEXT,
                resolution_station TEXT,
                resolution_source TEXT,
                resolution_source_url TEXT,
                settlement_unit TEXT,
                volume_24h REAL,
                open_interest REAL,
                buckets_json TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS polymarket_markets (
                market_id TEXT PRIMARY KEY,
                event_id TEXT,
                event_slug TEXT,
                market_slug TEXT,
                city TEXT,
                target_date TEXT,
                bucket_label TEXT,
                bucket_lower_c REAL,
                bucket_upper_c REAL,
                is_tail INTEGER,
                outcome_yes_token_id TEXT,
                outcome_no_token_id TEXT,
                order_min_size REAL,
                tick_size REAL,
                enable_order_book INTEGER,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS polymarket_orderbook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_key TEXT UNIQUE,
                market_id TEXT,
                event_id TEXT,
                token_id TEXT,
                ts TEXT,
                best_bid REAL,
                best_ask REAL,
                spread REAL,
                volume_24h REAL,
                bid_depth REAL,
                ask_depth REAL,
                source_url TEXT,
                raw_response_hash TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS data_fetch_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_key TEXT UNIQUE,
                source TEXT,
                stage TEXT,
                status TEXT,
                duration_ms REAL,
                city TEXT,
                target_date TEXT,
                message TEXT,
                details_json TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_distributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT,
                event_slug TEXT,
                signal_id INTEGER,
                sum_probability REAL,
                normalized INTEGER,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signal_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT,
                signal_id INTEGER UNIQUE,
                market_id TEXT,
                bucket_id INTEGER,
                bucket_key TEXT,
                city_key TEXT,
                target_date TEXT,
                issued_at TEXT,
                token_id TEXT,
                yes_token_id TEXT,
                bucket_direction TEXT,
                bucket_lower REAL,
                bucket_upper REAL,
                mu REAL,
                sigma REAL,
                deb_version TEXT,
                forecast_algo TEXT,
                model_probability REAL,
                market_ask REAL,
                market_bid REAL,
                market_mid REAL,
                market_implied_probability REAL,
                edge REAL,
                edge_percent REAL,
                strategy_name TEXT,
                kelly_fraction REAL,
                position_size_usd REAL,
                ladder_group_id TEXT,
                strategy_revision_id TEXT,
                strategy_params_hash TEXT,
                strategy_params_snapshot_json TEXT,
                sizing_bankroll_usd REAL,
                sizing_max_per_trade_usd REAL,
                kelly_multiplier REAL,
                bankroll_fraction_cap REAL,
                orderbook_snapshot_json TEXT,
                tick_size REAL,
                order_min_size REAL,
                neg_risk INTEGER,
                book_age_seconds REAL,
                spread_bps REAL,
                gate_status TEXT,
                paper_decision TEXT,
                live_decision TEXT,
                blocked_reason_primary TEXT,
                evidence_links_json TEXT,
                decision_version TEXT,
                action TEXT,
                live_allowed INTEGER,
                paper_allowed INTEGER,
                reasons TEXT,
                cautions TEXT,
                model_distribution_json TEXT,
                model_bucket_probs_json TEXT,
                market_bucket_probs_json TEXT,
                edge_by_bucket_json TEXT,
                gate_reasons_json TEXT,
                raw_json TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_reprice_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE,
                city_key TEXT,
                target_date TEXT,
                market_id TEXT,
                bucket_key TEXT,
                triggered_at TEXT,
                model_source TEXT,
                previous_model_prob REAL,
                model_prob REAL,
                delta_prob REAL,
                market_mid REAL,
                edge REAL,
                alpha_candidate INTEGER,
                raw_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                severity TEXT,
                message TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT,
                event_type TEXT,
                status TEXT,
                message TEXT,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS data_qualification_audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_version TEXT NOT NULL,
                status TEXT NOT NULL,
                score REAL NOT NULL,
                live_allowed INTEGER NOT NULL,
                raw_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        _ensure_columns(conn)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    ensure = {
        "settlements": {
            "actual_provider": "TEXT",
            "actual_station": "TEXT",
            "actual_confidence": "REAL",
            "calibration_eligible": "INTEGER",
        },
        "signals": {
            "decision_json": "TEXT",
        },
        "markets": {
            "station_id": "TEXT",
            "truth_confidence": "REAL",
        },
        "market_rules": {
            "exchange_market_id": "TEXT",
            "contract_id": "TEXT",
            "target_local_date": "TEXT",
            "bucket_boundary": "TEXT",
            "rounding_rule": "TEXT",
            "truth_provider_priority": "TEXT",
            "rule_version": "TEXT",
            "registry_version": "TEXT",
            "parsed_at": "TEXT",
            "manual_verified_at": "TEXT",
        },
        "settlement_contracts": {
            "manual_verified_by": "TEXT",
            "manual_verification_note": "TEXT",
            "manual_verification_snapshot": "TEXT",
        },
        "truth_observations": {
            "truth_version": "TEXT",
            "supersedes_truth_id": "INTEGER",
        },
        "forecast_runs": {
            "run_key": "TEXT",
            "snapshot_key": "TEXT",
            "provider": "TEXT",
            "model": "TEXT",
            "model_version": "TEXT",
            "run_type": "TEXT",
            "retrieved_at": "TEXT",
            "available_at": "TEXT",
            "availability_basis": "TEXT",
            "valid_at": "TEXT",
            "lead_hours": "REAL",
            "latitude": "REAL",
            "longitude": "REAL",
            "station_id": "TEXT",
            "timezone": "TEXT",
            "unit": "TEXT",
            "member_count": "INTEGER",
            "source_url": "TEXT",
            "raw_response_hash": "TEXT",
            "data_license": "TEXT",
            "quality_flags": "TEXT",
            "parser_version": "TEXT",
            "parse_status": "TEXT",
            "parse_warnings": "TEXT",
            "source_unit": "TEXT",
            "training_eligible": "INTEGER",
            "ineligibility_reason": "TEXT",
            "quarantined_at": "TEXT",
            "quarantine_reason": "TEXT",
        },
        "forecast_members": {
            "member_id": "TEXT",
            "hourly_json": "TEXT",
        },
        "hourly_consensus": {
            "precipitation": "REAL",
            "wind_speed": "REAL",
            "wind_direction": "REAL",
            "pressure": "REAL",
            "dew_point": "REAL",
            "forecast_spread": "REAL",
            "forecast_member_count": "INTEGER",
            "consensus_method": "TEXT",
            "forecast_source": "TEXT",
            "forecast_sources_json": "TEXT",
            "observation_sources_json": "TEXT",
            "source_mix_json": "TEXT",
            "consensus_version": "TEXT",
            "build_status": "TEXT",
            "build_warnings": "TEXT",
        },
        "daily_max_predictions": {
            "prediction_key": "TEXT",
            "city_key": "TEXT",
            "target_date": "TEXT",
            "issued_at": "TEXT",
            "mu": "REAL",
            "sigma": "REAL",
            "unit": "TEXT",
            "method": "TEXT",
            "model_weights_json": "TEXT",
            "member_count": "INTEGER",
            "components_json": "TEXT",
            "source_run_ids_json": "TEXT",
            "member_daily_highs_json": "TEXT",
            "sigma_from_spread": "REAL",
            "sigma_from_history": "REAL",
            "bias_correction": "REAL",
            "bias_sample_count": "INTEGER",
            "deb_version": "TEXT",
            "observed_floor": "REAL",
            "sigma_floor": "REAL",
            "time_decay_factor": "REAL",
            "mu_observed_floor_applied": "INTEGER",
            "peak_hour": "TEXT",
            "peak_temp": "REAL",
            "peak_source": "TEXT",
            "validity_status": "TEXT",
            "invalidated_at": "TEXT",
            "invalidation_reason": "TEXT",
        },
        "market_buckets": {
            "bucket_key": "TEXT",
            "event_slug": "TEXT",
            "event_url": "TEXT",
            "market_id": "TEXT",
            "condition_id": "TEXT",
            "question": "TEXT",
            "city": "TEXT",
            "city_name": "TEXT",
            "target_date": "TEXT",
            "station_id": "TEXT",
            "unit": "TEXT",
            "bucket_label": "TEXT",
            "bucket_direction": "TEXT",
            "bucket_low": "REAL",
            "bucket_high": "REAL",
            "outcome_name": "TEXT",
            "yes_token_id": "TEXT",
            "no_token_id": "TEXT",
            "token_id": "TEXT",
            "token_side": "TEXT",
            "outcome_index": "INTEGER",
            "price": "REAL",
            "best_bid": "REAL",
            "best_ask": "REAL",
            "spread": "REAL",
            "volume": "REAL",
            "liquidity": "REAL",
            "order_min_size": "REAL",
            "tick_size": "REAL",
            "neg_risk": "INTEGER",
            "enable_order_book": "INTEGER",
            "quote_timestamp": "TEXT",
            "orderbook_snapshot_key": "TEXT",
            "orderbook_source": "TEXT",
            "bid_depth": "REAL",
            "ask_depth": "REAL",
            "source_url": "TEXT",
            "raw_response_hash": "TEXT",
            "strict_match_status": "TEXT",
            "strict_match_reasons": "TEXT",
            "parser_version": "TEXT",
        },
        "orderbooks": {
            "snapshot_key": "TEXT",
            "snapshot_type": "TEXT",
            "quote_timestamp": "TEXT",
            "book_hash": "TEXT",
            "bids_json": "TEXT",
            "asks_json": "TEXT",
            "bid_depth": "REAL",
            "ask_depth": "REAL",
            "source_url": "TEXT",
            "raw_response_hash": "TEXT",
        },
        "signal_decisions": {
            "decision_id": "TEXT",
            "bucket_id": "INTEGER",
            "bucket_key": "TEXT",
            "city_key": "TEXT",
            "target_date": "TEXT",
            "issued_at": "TEXT",
            "token_id": "TEXT",
            "yes_token_id": "TEXT",
            "bucket_direction": "TEXT",
            "bucket_lower": "REAL",
            "bucket_upper": "REAL",
            "mu": "REAL",
            "sigma": "REAL",
            "deb_version": "TEXT",
            "forecast_algo": "TEXT",
            "model_probability": "REAL",
            "market_ask": "REAL",
            "market_bid": "REAL",
            "market_mid": "REAL",
            "market_implied_probability": "REAL",
            "edge": "REAL",
            "edge_percent": "REAL",
            "strategy_name": "TEXT",
            "kelly_fraction": "REAL",
            "position_size_usd": "REAL",
            "ladder_group_id": "TEXT",
            "strategy_revision_id": "TEXT",
            "strategy_params_hash": "TEXT",
            "strategy_params_snapshot_json": "TEXT",
            "sizing_bankroll_usd": "REAL",
            "sizing_max_per_trade_usd": "REAL",
            "kelly_multiplier": "REAL",
            "bankroll_fraction_cap": "REAL",
            "orderbook_snapshot_json": "TEXT",
            "tick_size": "REAL",
            "order_min_size": "REAL",
            "neg_risk": "INTEGER",
            "book_age_seconds": "REAL",
            "spread_bps": "REAL",
            "gate_status": "TEXT",
            "paper_decision": "TEXT",
            "live_decision": "TEXT",
            "blocked_reason_primary": "TEXT",
            "evidence_links_json": "TEXT",
            "decision_version": "TEXT",
            "model_distribution_json": "TEXT",
            "model_bucket_probs_json": "TEXT",
            "market_bucket_probs_json": "TEXT",
            "edge_by_bucket_json": "TEXT",
            "gate_reasons_json": "TEXT",
        },
        "paper_orders": {
            "decision_id": "TEXT",
            "bucket_key": "TEXT",
            "strategy_name": "TEXT",
            "ladder_group_id": "TEXT",
            "strategy_revision_id": "TEXT",
            "strategy_params_hash": "TEXT",
            "strategy_params_snapshot_json": "TEXT",
            "sizing_snapshot_json": "TEXT",
            "execution_quote_json": "TEXT",
            "cap_reasons_json": "TEXT",
            "city_key": "TEXT",
            "target_date": "TEXT",
            "event_url": "TEXT",
            "requested_amount": "REAL",
            "filled_amount": "REAL",
            "filled_shares": "REAL",
            "unfilled_amount": "REAL",
            "average_fill_price": "REAL",
            "mark_price": "REAL",
            "unrealized_pnl": "REAL",
            "realized_pnl": "REAL",
            "lifecycle_status": "TEXT",
            "fill_status": "TEXT",
            "order_version": "TEXT",
            "model_probability": "REAL",
            "market_probability": "REAL",
            "edge": "REAL",
            "gate_status": "TEXT",
            "risk_reasons_json": "TEXT",
            "orderbook_snapshot_json": "TEXT",
            "evidence_links_json": "TEXT",
            "opened_at": "TEXT",
            "closed_at": "TEXT",
            "cohort_run_id": "TEXT",
        },
        "paper_validation_runs": {
            "strategy_revision_id": "TEXT",
            "strategy_profile_snapshot_json": "TEXT",
            "kelly_multiplier": "REAL",
            "bankroll_fraction_cap": "REAL",
        },
        "fills": {
            "idempotency_key": "TEXT",
            "decision_id": "TEXT",
            "market_id": "TEXT",
            "yes_token_id": "TEXT",
            "fill_status": "TEXT",
            "source": "TEXT",
        },
        "settlements": {
            "settlement_key": "TEXT",
            "paper_order_id": "INTEGER",
            "decision_id": "TEXT",
            "yes_token_id": "TEXT",
            "city_key": "TEXT",
            "target_date": "TEXT",
            "outcome_yes": "INTEGER",
            "settlement_status": "TEXT",
            "settlement_source": "TEXT",
            "payout": "REAL",
            "brier_score": "REAL",
            "market_brier_score": "REAL",
            "settled_at": "TEXT",
            "updated_at": "TEXT",
        },
        "mesonet_observations": {
            "raw_response": "TEXT",
            "raw_response_hash": "TEXT",
            "parser_version": "TEXT",
            "parse_status": "TEXT",
            "parse_warnings": "TEXT",
            "raw_unit": "TEXT",
            "fetched_at": "TEXT",
        },
        "stations": {
            "icao_id": "TEXT",
            "wmo_id": "TEXT",
            "provider_station_ids_json": "TEXT",
            "expected_metric": "TEXT",
            "settlement_rule_text": "TEXT",
            "settlement_station_id": "TEXT",
            "settlement_station_name": "TEXT",
            "settlement_timezone": "TEXT",
            "settlement_unit": "TEXT",
            "settlement_time_basis": "TEXT",
            "settlement_rule_verified_at": "TEXT",
            "primary_settlement_source": "TEXT",
            "nearby_observation_networks_json": "TEXT",
            "confidence": "REAL",
            "verification_status": "TEXT",
            "display_enabled": "INTEGER DEFAULT 1",
            "city_scope": "TEXT DEFAULT 'market_candidate'",
            "enabled": "INTEGER DEFAULT 0",
            "tier": "INTEGER DEFAULT 9",
            "registry_version": "TEXT",
        },
        "truth_delta_audit": {
            "delta_hko_minus_iem": "REAL",
        },
    }
    for table, columns in ensure.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    from .migrations import run_schema_migrations

    run_schema_migrations(conn)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_runs_run_key ON forecast_runs(run_key)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_forecast_runs_city_date_source_retrieved "
        "ON forecast_runs(city, target_date, source, retrieved_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_forecast_runs_city_date_type_retrieved "
        "ON forecast_runs(city, target_date, run_type, retrieved_at DESC, id DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_members_run_member "
        "ON forecast_members(run_id, member_id)"
    )
    conn.execute(
        """
        UPDATE forecast_runs
        SET training_eligible = 0,
            ineligibility_reason = COALESCE(ineligibility_reason, 'legacy_run_before_training_gate')
        WHERE training_eligible IS NULL
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orderbooks_snapshot_key ON orderbooks(snapshot_key)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_settlements_key ON settlements(settlement_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_settlements_order ON settlements(paper_order_id, settlement_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stations_station_id ON stations(station_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stations_region ON stations(region)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metar_reports_city_time ON metar_reports(city, report_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_metar_reports_station_time ON metar_reports(station_id, report_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mesonet_observations_city_time ON mesonet_observations(city, observed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hourly_consensus_city_date ON hourly_consensus(city, target_date, local_hour)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_max_predictions_key "
        "ON daily_max_predictions(prediction_key)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_max_predictions_city_date_issued "
        "ON daily_max_predictions(city_key, target_date, issued_at)"
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_market_buckets_key ON market_buckets(bucket_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_buckets_city_date ON market_buckets(city, target_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_buckets_market ON market_buckets(market_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_buckets_token ON market_buckets(yes_token_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_truth_iem_daily_station_date ON truth_iem_daily(icao, date_local)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_truth_iem_hourly_station_date ON truth_iem_hourly(icao, date_local)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_truth_wu_daily_station_date ON truth_wunderground_daily(icao, date_local)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_truth_wu_hourly_key ON truth_wunderground_hourly(observation_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_truth_wu_hourly_station_date ON truth_wunderground_hourly(icao, date_local)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_truth_wu_hourly_observed ON truth_wunderground_hourly(observed_at_utc)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_truth_hko_daily_date ON truth_hko_daily(date_local)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_truth_delta_audit_station_date ON truth_delta_audit(icao, date_local)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_polymarket_events_city_date ON polymarket_events(city, target_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_polymarket_markets_event ON polymarket_markets(event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_polymarket_markets_city_date ON polymarket_markets(city, target_date)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_polymarket_orderbook_snapshot ON polymarket_orderbook(snapshot_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_polymarket_orderbook_market_ts ON polymarket_orderbook(market_id, ts)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_decisions_decision_id ON signal_decisions(decision_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_decisions_city_date ON signal_decisions(city_key, target_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_decisions_bucket ON signal_decisions(bucket_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_decisions_strategy ON signal_decisions(strategy_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_decisions_ladder_group ON signal_decisions(ladder_group_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_decisions_strategy_revision ON signal_decisions(strategy_revision_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_model_reprice_event_key ON model_reprice_events(event_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_reprice_city_date ON model_reprice_events(city_key, target_date)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_orders_idempotency ON paper_orders(idempotency_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_decision ON paper_orders(decision_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_city_date ON paper_orders(city_key, target_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_token_status ON paper_orders(yes_token_id, lifecycle_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_orders_cohort ON paper_orders(cohort_run_id, opened_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_validation_status ON paper_validation_runs(status, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_profile_revision_no ON strategy_profile_revisions(profile_key, revision_no DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_profile_activation_scope ON strategy_profile_activation_events(scope, activation_id DESC)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_idempotency ON fills(idempotency_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_type, order_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_data_fetch_logs_created ON data_fetch_logs(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_data_fetch_logs_source_status ON data_fetch_logs(source, status)")
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS strategy_profile_revisions_no_update
        BEFORE UPDATE ON strategy_profile_revisions
        BEGIN
            SELECT RAISE(ABORT, 'strategy_profile_revisions_are_immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS strategy_profile_revisions_no_delete
        BEFORE DELETE ON strategy_profile_revisions
        BEGIN
            SELECT RAISE(ABORT, 'strategy_profile_revisions_are_immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS strategy_profile_activation_no_update
        BEFORE UPDATE ON strategy_profile_activation_events
        BEGIN
            SELECT RAISE(ABORT, 'strategy_profile_activation_events_are_immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS strategy_profile_activation_no_delete
        BEFORE DELETE ON strategy_profile_activation_events
        BEGIN
            SELECT RAISE(ABORT, 'strategy_profile_activation_events_are_immutable');
        END;
        """
    )


def dump_json(payload: Any) -> str:
    return json.dumps(_json_safe({} if payload is None else payload), ensure_ascii=False, sort_keys=True, allow_nan=False)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _stable_key(*parts: Any) -> str:
    text = "|".join(str(part or "") for part in parts)
    if text.strip("|"):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return hashlib.sha256(utc_now().encode("utf-8")).hexdigest()[:32]


def _hash_text(text: Any) -> str:
    if text is None:
        return ""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:32]


def upsert_signal(signal: dict[str, Any], legacy_signal_id: int | None = None) -> int:
    init_v3_db()
    now = utc_now()
    market_id = str(signal.get("market_id") or "")
    signal_key = str(signal.get("signal_key") or f"{market_id}:{signal.get('created_at') or now}")
    raw = signal.get("raw_json")
    if isinstance(raw, str):
        try:
            raw_payload = json.loads(raw)
        except Exception:
            raw_payload = {"raw_json": raw}
    else:
        raw_payload = raw or signal
    probability = _num(signal.get("probability"), _num(signal.get("p"), 0.0))
    price = _num(signal.get("limit_price"), _num(signal.get("entry_price"), 0.0))
    edge = probability - price if probability and price else _num(signal.get("probability_edge"), 0.0)
    ev = _num(signal.get("ev"), 0.0)
    quality = round(max(0.0, min(1.0, (edge * 1.5) + min(max(ev, 0.0), 2.0) / 4.0)), 4)
    row = {
        "legacy_signal_id": legacy_signal_id,
        "signal_key": signal_key,
        "market_id": market_id,
        "city": signal.get("city") or raw_payload.get("city") or "",
        "city_name": signal.get("city_name") or "",
        "target_date": signal.get("date") or signal.get("target_date") or "",
        "bucket_label": signal.get("bucket_label") or "",
        "event_url": signal.get("event_url") or raw_payload.get("event_url") or "",
        "yes_token_id": signal.get("yes_token_id") or raw_payload.get("yes_token_id") or "",
        "model_probability": probability,
        "market_probability": price,
        "probability_edge": edge,
        "ev": ev,
        "kelly": _num(signal.get("kelly"), 0.0),
        "suggested_size": _num(signal.get("amount"), _num(signal.get("cost"), 0.0)),
        "quality_score": quality,
        "status": signal.get("status") or "candidate",
        "raw_json": dump_json(raw_payload),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO signals (
                legacy_signal_id, signal_key, market_id, city, city_name, target_date,
                bucket_label, event_url, yes_token_id, model_probability,
                market_probability, probability_edge, ev, kelly, suggested_size,
                quality_score, status, raw_json, created_at, updated_at
            ) VALUES (
                :legacy_signal_id, :signal_key, :market_id, :city, :city_name, :target_date,
                :bucket_label, :event_url, :yes_token_id, :model_probability,
                :market_probability, :probability_edge, :ev, :kelly, :suggested_size,
                :quality_score, :status, :raw_json, :created_at, :updated_at
            )
            ON CONFLICT(signal_key) DO UPDATE SET
                market_probability=excluded.market_probability,
                probability_edge=excluded.probability_edge,
                ev=excluded.ev,
                kelly=excluded.kelly,
                suggested_size=excluded.suggested_size,
                quality_score=excluded.quality_score,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            {**row, "created_at": now, "updated_at": now},
        )
        return int(conn.execute("SELECT id FROM signals WHERE signal_key = ?", (signal_key,)).fetchone()["id"])


def insert_orderbook(
    market_id: str,
    payload: dict[str, Any],
    *,
    path: Path | None = None,
    _connection=None,
) -> int:
    if _connection is None:
        init_v3_db(path)
    bids = _levels(payload.get("bids"))
    asks = _levels(payload.get("asks"))
    best_bid = max((level["price"] for level in bids), default=_num(payload.get("bestBid"), _num(payload.get("best_bid"), 0.0)))
    best_ask = min((level["price"] for level in asks), default=_num(payload.get("bestAsk"), _num(payload.get("best_ask"), 0.0)))
    spread = _num(payload.get("spread"), best_ask - best_bid if best_ask and best_bid else 0.0)
    raw_response_hash = str(payload.get("raw_response_hash") or _json_hash(payload))
    snapshot_key = str(
        payload.get("snapshot_key")
        or f"{payload.get('yes_token_id') or payload.get('asset_id') or market_id}:{payload.get('hash') or raw_response_hash}"
    )
    connection_context = connect(path) if _connection is None else nullcontext(_connection)
    with connection_context as conn:
        conn.execute(
            """
            INSERT INTO orderbooks (
                snapshot_key, market_id, yes_token_id, best_bid, best_ask, spread,
                volume, order_min_size, tick_size, enable_order_book, snapshot_type,
                quote_timestamp, book_hash, bids_json, asks_json, bid_depth,
                ask_depth, source_url, raw_response_hash, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_key) DO UPDATE SET
                best_bid=excluded.best_bid,
                best_ask=excluded.best_ask,
                spread=excluded.spread,
                bids_json=excluded.bids_json,
                asks_json=excluded.asks_json,
                bid_depth=excluded.bid_depth,
                ask_depth=excluded.ask_depth,
                quote_timestamp=excluded.quote_timestamp,
                raw_json=excluded.raw_json,
                created_at=excluded.created_at
            """,
            (
                snapshot_key,
                market_id,
                str(payload.get("yes_token_id") or payload.get("asset_id") or ""),
                best_bid,
                best_ask,
                spread,
                _num(payload.get("volume"), 0.0),
                _num(payload.get("orderMinSize"), _num(payload.get("order_min_size"), _num(payload.get("min_order_size"), 0.0))),
                _num(payload.get("orderPriceMinTickSize"), _num(payload.get("tick_size"), 0.0)),
                1 if payload.get("enableOrderBook", payload.get("enable_order_book", True)) else 0,
                str(payload.get("snapshot_type") or ("clob" if bids or asks else "gamma")),
                str(payload.get("quote_timestamp") or payload.get("timestamp") or ""),
                str(payload.get("hash") or ""),
                dump_json(bids),
                dump_json(asks),
                round(sum(level["size"] for level in bids), 6),
                round(sum(level["size"] for level in asks), 6),
                str(payload.get("source_url") or ""),
                raw_response_hash,
                dump_json(payload),
                utc_now(),
            ),
        )
        return int(conn.execute("SELECT id FROM orderbooks WHERE snapshot_key = ?", (snapshot_key,)).fetchone()["id"])


def insert_orderbooks(
    items: list[tuple[str, dict[str, Any]]],
    path: Path | None = None,
) -> list[int]:
    if not items:
        return []
    init_v3_db(path)
    with connect(path) as conn:
        return [
            insert_orderbook(market_id, payload, path=path, _connection=conn)
            for market_id, payload in items
        ]


def upsert_market_bucket(
    bucket: dict[str, Any],
    path: Path | None = None,
    *,
    _connection=None,
) -> int:
    if _connection is None:
        init_v3_db(path)
    now = utc_now()
    market_id = str(bucket.get("market_id") or "")
    yes_token_id = str(bucket.get("yes_token_id") or "")
    bucket_key = str(
        bucket.get("bucket_key")
        or _stable_key(
            "market_bucket",
            market_id,
            yes_token_id,
            bucket.get("outcome_name"),
            bucket.get("bucket_label"),
        )
    )
    row = {
        "bucket_key": bucket_key,
        "event_slug": str(bucket.get("event_slug") or ""),
        "event_url": str(bucket.get("event_url") or ""),
        "market_id": market_id,
        "condition_id": str(bucket.get("condition_id") or ""),
        "question": str(bucket.get("question") or ""),
        "city": str(bucket.get("city") or ""),
        "city_name": str(bucket.get("city_name") or ""),
        "target_date": str(bucket.get("target_date") or ""),
        "station_id": str(bucket.get("station_id") or ""),
        "unit": str(bucket.get("unit") or ""),
        "bucket_label": str(bucket.get("bucket_label") or ""),
        "bucket_direction": str(bucket.get("bucket_direction") or ""),
        "bucket_low": _nullable_num(bucket.get("bucket_low")),
        "bucket_high": _nullable_num(bucket.get("bucket_high")),
        "outcome_name": str(bucket.get("outcome_name") or ""),
        "yes_token_id": yes_token_id,
        "no_token_id": str(bucket.get("no_token_id") or ""),
        "token_id": str(bucket.get("token_id") or yes_token_id),
        "token_side": str(bucket.get("token_side") or "YES"),
        "outcome_index": int(bucket.get("outcome_index") or 0),
        "price": _nullable_num(bucket.get("price")),
        "best_bid": _nullable_num(bucket.get("best_bid")),
        "best_ask": _nullable_num(bucket.get("best_ask")),
        "spread": _nullable_num(bucket.get("spread")),
        "volume": _nullable_num(bucket.get("volume")),
        "liquidity": _nullable_num(bucket.get("liquidity")),
        "order_min_size": _nullable_num(bucket.get("order_min_size")),
        "tick_size": _nullable_num(bucket.get("tick_size")),
        "neg_risk": 1 if bucket.get("neg_risk") else 0,
        "enable_order_book": 1 if bucket.get("enable_order_book", True) else 0,
        "quote_timestamp": str(bucket.get("quote_timestamp") or ""),
        "orderbook_snapshot_key": str(bucket.get("orderbook_snapshot_key") or ""),
        "orderbook_source": str(bucket.get("orderbook_source") or ""),
        "bid_depth": _nullable_num(bucket.get("bid_depth")),
        "ask_depth": _nullable_num(bucket.get("ask_depth")),
        "source_url": str(bucket.get("source_url") or ""),
        "raw_response_hash": str(bucket.get("raw_response_hash") or _json_hash(bucket.get("raw_json") or bucket)),
        "strict_match_status": str(bucket.get("strict_match_status") or "blocked"),
        "strict_match_reasons": dump_json(bucket.get("strict_match_reasons", [])),
        "parser_version": str(bucket.get("parser_version") or "market-buckets-v1"),
        "raw_json": dump_json(bucket.get("raw_json") or bucket),
        "created_at": now,
        "updated_at": now,
    }
    connection_context = connect(path) if _connection is None else nullcontext(_connection)
    with connection_context as conn:
        conn.execute(
            """
            INSERT INTO market_buckets (
                bucket_key, event_slug, event_url, market_id, condition_id, question,
                city, city_name, target_date, station_id, unit, bucket_label,
                bucket_direction, bucket_low, bucket_high, outcome_name, yes_token_id,
                no_token_id, token_id, token_side, outcome_index, price, best_bid,
                best_ask, spread, volume, liquidity, order_min_size, tick_size,
                neg_risk, enable_order_book, quote_timestamp, orderbook_snapshot_key,
                orderbook_source, bid_depth, ask_depth, source_url, raw_response_hash,
                strict_match_status, strict_match_reasons, parser_version, raw_json,
                created_at, updated_at
            ) VALUES (
                :bucket_key, :event_slug, :event_url, :market_id, :condition_id, :question,
                :city, :city_name, :target_date, :station_id, :unit, :bucket_label,
                :bucket_direction, :bucket_low, :bucket_high, :outcome_name, :yes_token_id,
                :no_token_id, :token_id, :token_side, :outcome_index, :price, :best_bid,
                :best_ask, :spread, :volume, :liquidity, :order_min_size, :tick_size,
                :neg_risk, :enable_order_book, :quote_timestamp, :orderbook_snapshot_key,
                :orderbook_source, :bid_depth, :ask_depth, :source_url, :raw_response_hash,
                :strict_match_status, :strict_match_reasons, :parser_version, :raw_json,
                :created_at, :updated_at
            )
            ON CONFLICT(bucket_key) DO UPDATE SET
                event_slug=excluded.event_slug,
                event_url=excluded.event_url,
                condition_id=excluded.condition_id,
                question=excluded.question,
                city=excluded.city,
                city_name=excluded.city_name,
                target_date=excluded.target_date,
                station_id=excluded.station_id,
                unit=excluded.unit,
                bucket_label=excluded.bucket_label,
                bucket_direction=excluded.bucket_direction,
                bucket_low=excluded.bucket_low,
                bucket_high=excluded.bucket_high,
                outcome_name=excluded.outcome_name,
                yes_token_id=excluded.yes_token_id,
                no_token_id=excluded.no_token_id,
                token_id=excluded.token_id,
                token_side=excluded.token_side,
                outcome_index=excluded.outcome_index,
                price=excluded.price,
                best_bid=excluded.best_bid,
                best_ask=excluded.best_ask,
                spread=excluded.spread,
                volume=excluded.volume,
                liquidity=excluded.liquidity,
                order_min_size=excluded.order_min_size,
                tick_size=excluded.tick_size,
                neg_risk=excluded.neg_risk,
                enable_order_book=excluded.enable_order_book,
                quote_timestamp=excluded.quote_timestamp,
                orderbook_snapshot_key=excluded.orderbook_snapshot_key,
                orderbook_source=excluded.orderbook_source,
                bid_depth=excluded.bid_depth,
                ask_depth=excluded.ask_depth,
                source_url=excluded.source_url,
                raw_response_hash=excluded.raw_response_hash,
                strict_match_status=excluded.strict_match_status,
                strict_match_reasons=excluded.strict_match_reasons,
                parser_version=excluded.parser_version,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            row,
        )
        found = conn.execute("SELECT id FROM market_buckets WHERE bucket_key = ?", (bucket_key,)).fetchone()
        return int(found["id"]) if found else 0


def upsert_market_buckets(buckets: list[dict[str, Any]], path: Path | None = None) -> list[int]:
    if not buckets:
        return []
    init_v3_db(path)
    with connect(path) as conn:
        return [upsert_market_bucket(bucket, path, _connection=conn) for bucket in buckets]


def list_market_buckets(
    city: str | None = None,
    target_date: str | None = None,
    market_id: str | None = None,
    limit: int = 200,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    init_v3_db(path)
    where: list[str] = []
    params: list[Any] = []
    if city:
        where.append("city = ?")
        params.append(city)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    if market_id:
        where.append("market_id = ?")
        params.append(market_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    bounded_limit = max(1, min(int(limit or 200), 1000))
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM market_buckets
                {clause}
                ORDER BY target_date DESC, city, bucket_low, bucket_high, id DESC
                LIMIT ?
                """,
                (*params, bounded_limit),
            ).fetchall()
        ]
    for row in rows:
        row["neg_risk"] = bool(row.get("neg_risk"))
        row["enable_order_book"] = bool(row.get("enable_order_book"))
        row["strict_match_reasons"] = _loads_list(row.get("strict_match_reasons"))
    return rows


def market_bucket_summary(city: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    init_v3_db()
    rows = list_market_buckets(city=city, target_date=target_date, limit=1000)
    reason_counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("strict_match_reasons") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
    ready_rows = [row for row in rows if row.get("strict_match_status") == "matched"]
    return {
        "ok": True,
        "city": city or "",
        "target_date": target_date or "",
        "bucket_count": len(rows),
        "matched_bucket_count": len(ready_rows),
        "blocked_bucket_count": len(rows) - len(ready_rows),
        "markets": len({row.get("market_id") for row in rows if row.get("market_id")}),
        "tokens": len({row.get("yes_token_id") for row in rows if row.get("yes_token_id")}),
        "orderbook_enabled": sum(1 for row in rows if row.get("enable_order_book")),
        "with_tick_size": sum(1 for row in rows if row.get("tick_size") is not None),
        "with_order_min_size": sum(1 for row in rows if row.get("order_min_size") is not None),
        "with_two_sided_depth": sum(1 for row in rows if (row.get("bid_depth") or 0) > 0 and (row.get("ask_depth") or 0) > 0),
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "latest": rows[:20],
    }


def upsert_daily_max_prediction(prediction: dict[str, Any], path: Path | None = None) -> int:
    init_v3_db(path)
    now = utc_now()
    city_key = str(prediction.get("city_key") or prediction.get("city") or "")
    target_date = str(prediction.get("target_date") or "")
    issued_at = str(prediction.get("issued_at") or now)
    method = str(prediction.get("method") or "weatherbot-deb-v1")
    prediction_key = str(
        prediction.get("prediction_key")
        or _stable_key(
            "daily_max_prediction",
            city_key,
            target_date,
            issued_at,
            method,
            dump_json(prediction.get("source_run_ids", [])),
            dump_json(prediction.get("model_weights", {})),
        )
    )
    row = {
        "prediction_key": prediction_key,
        "city_key": city_key,
        "target_date": target_date,
        "issued_at": issued_at,
        "mu": _nullable_num(prediction.get("mu")),
        "sigma": _nullable_num(prediction.get("sigma")),
        "unit": str(prediction.get("unit") or "C"),
        "method": method,
        "model_weights_json": dump_json(prediction.get("model_weights", {})),
        "member_count": int(prediction.get("member_count") or 0),
        "components_json": dump_json(prediction.get("components", [])),
        "source_run_ids_json": dump_json(prediction.get("source_run_ids", [])),
        "member_daily_highs_json": dump_json(prediction.get("member_daily_highs", {})),
        "sigma_from_spread": _nullable_num(prediction.get("sigma_from_spread")),
        "sigma_from_history": _nullable_num(prediction.get("sigma_from_history")),
        "bias_correction": _nullable_num(prediction.get("bias_correction")),
        "bias_sample_count": int(prediction.get("bias_sample_count") or 0),
        "deb_version": str(prediction.get("deb_version") or method),
        "observed_floor": _nullable_num(prediction.get("observed_floor")),
        "sigma_floor": _nullable_num(prediction.get("sigma_floor")),
        "time_decay_factor": _nullable_num(prediction.get("time_decay_factor")),
        "mu_observed_floor_applied": 1 if prediction.get("mu_observed_floor_applied") else 0,
        "peak_hour": str(prediction.get("peak_hour") or ""),
        "peak_temp": _nullable_num(prediction.get("peak_temp")),
        "peak_source": str(prediction.get("peak_source") or ""),
        "validity_status": str(prediction.get("validity_status") or "valid"),
        "invalidated_at": prediction.get("invalidated_at"),
        "invalidation_reason": prediction.get("invalidation_reason"),
        "raw_json": dump_json(prediction),
        "created_at": now,
        "updated_at": now,
    }
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO daily_max_predictions (
                prediction_key, city_key, target_date, issued_at, mu, sigma, unit,
                method, model_weights_json, member_count, components_json,
                source_run_ids_json, member_daily_highs_json, sigma_from_spread,
                sigma_from_history, bias_correction, bias_sample_count, deb_version,
                observed_floor, sigma_floor, time_decay_factor,
                mu_observed_floor_applied, peak_hour, peak_temp, peak_source,
                validity_status, invalidated_at, invalidation_reason,
                raw_json, created_at, updated_at
            ) VALUES (
                :prediction_key, :city_key, :target_date, :issued_at, :mu, :sigma, :unit,
                :method, :model_weights_json, :member_count, :components_json,
                :source_run_ids_json, :member_daily_highs_json, :sigma_from_spread,
                :sigma_from_history, :bias_correction, :bias_sample_count, :deb_version,
                :observed_floor, :sigma_floor, :time_decay_factor,
                :mu_observed_floor_applied, :peak_hour, :peak_temp, :peak_source,
                :validity_status, :invalidated_at, :invalidation_reason,
                :raw_json, :created_at, :updated_at
            )
            ON CONFLICT(prediction_key) DO NOTHING
            """,
            row,
        )
        found = conn.execute(
            "SELECT id FROM daily_max_predictions WHERE prediction_key = ?",
            (prediction_key,),
        ).fetchone()
        return int(found["id"]) if found else 0


def list_daily_max_predictions(
    city_key: str | None = None,
    target_date: str | None = None,
    limit: int = 100,
    path: Path | None = None,
    include_invalid: bool = False,
) -> list[dict[str, Any]]:
    init_v3_db(path)
    where: list[str] = []
    params: list[Any] = []
    if city_key:
        where.append("city_key = ?")
        params.append(city_key)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    if not include_invalid:
        where.append("COALESCE(validity_status, 'valid') = 'valid'")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    bounded_limit = max(1, min(int(limit or 100), 1000))
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM daily_max_predictions
                {clause}
                ORDER BY issued_at DESC, id DESC
                LIMIT ?
                """,
                (*params, bounded_limit),
            ).fetchall()
        ]
    for row in rows:
        row["model_weights"] = _loads_obj(row.get("model_weights_json"))
        row["components"] = _loads_list(row.get("components_json"))
        row["source_run_ids"] = _loads_list(row.get("source_run_ids_json"))
        row["member_daily_highs"] = _loads_obj(row.get("member_daily_highs_json"))
        row["mu_observed_floor_applied"] = bool(row.get("mu_observed_floor_applied"))
        raw_payload = _loads_obj(row.get("raw_json"))
        row["raw"] = raw_payload
        row["forecast_algo"] = row.get("forecast_algo") or raw_payload.get("forecast_algo") or raw_payload.get("algo") or row.get("method")
        if "ensemble_samples" in raw_payload:
            row["ensemble_samples"] = raw_payload.get("ensemble_samples") or []
        if "ensemble_sample_weights" in raw_payload:
            row["ensemble_sample_weights"] = raw_payload.get("ensemble_sample_weights") or []
        row["cohort_contract"] = persisted_prediction_cohort_status(row)
    return rows


def daily_max_prediction_summary(city_key: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    rows = list_daily_max_predictions(city_key=city_key, target_date=target_date, limit=100)
    candidate = rows[0] if rows else None
    cohort_contract = candidate.get("cohort_contract") if candidate else None
    quality_ok = bool(candidate) and bool((cohort_contract or {}).get("ok", True))
    latest = candidate if quality_ok else None
    return {
        "ok": True,
        "city_key": city_key or "",
        "target_date": target_date or "",
        "count": len(rows),
        "latest": latest,
        "quality_ok": quality_ok,
        "quality_reasons": list((cohort_contract or {}).get("reasons") or []),
        "rejected_latest_id": candidate.get("id") if candidate and not quality_ok else None,
    }


def signal_decision_prediction_cohort_status(
    decision: dict[str, Any],
    path: Path | None = None,
) -> dict[str, Any]:
    return signal_decision_prediction_cohort_statuses([decision], path=path)[0]


def signal_decision_prediction_cohort_statuses(
    decisions: list[dict[str, Any]],
    path: Path | None = None,
) -> list[dict[str, Any]]:
    aligned_algorithms = {"polywx_aligned_deb_v1", "polywx", "polywx_aligned"}
    prediction_ids: set[int] = set()
    decision_prediction_ids: list[int] = []
    applicable: list[bool] = []
    for decision in decisions:
        forecast_algo = str(decision.get("forecast_algo") or decision.get("deb_version") or "").strip().lower()
        is_applicable = forecast_algo in aligned_algorithms
        applicable.append(is_applicable)
        evidence = decision.get("evidence_links")
        if not isinstance(evidence, dict):
            evidence = _loads_obj(decision.get("evidence_links_json"))
        prediction_id = int(evidence.get("daily_max_prediction_id") or 0)
        decision_prediction_ids.append(prediction_id)
        if prediction_id > 0:
            prediction_ids.add(prediction_id)

    contracts_by_prediction_id: dict[int, dict[str, Any]] = {}
    if prediction_ids:
        placeholders = ",".join("?" for _ in prediction_ids)
        with connect(path) as conn:
            prediction_rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT id, method, deb_version, components_json, raw_json
                    FROM daily_max_predictions
                    WHERE id IN ({placeholders})
                    """,
                    tuple(sorted(prediction_ids)),
                ).fetchall()
            ]
        for prediction in prediction_rows:
            prediction["components"] = _loads_list(prediction.get("components_json"))
            prediction["raw"] = _loads_obj(prediction.get("raw_json"))
            prediction["forecast_algo"] = (
                prediction["raw"].get("forecast_algo")
                or prediction["raw"].get("algo")
                or prediction.get("method")
            )
            contracts_by_prediction_id[int(prediction["id"])] = persisted_prediction_cohort_status(prediction)

    statuses: list[dict[str, Any]] = []
    for is_applicable, prediction_id in zip(applicable, decision_prediction_ids):
        if not is_applicable:
            statuses.append({
                "ok": False,
                "applicable": False,
                "version": "",
                "reasons": ["decision_forecast_algo_unverified"],
            })
        elif prediction_id <= 0:
            statuses.append({
                "ok": False,
                "applicable": True,
                "version": "",
                "reasons": ["decision_daily_max_prediction_missing"],
            })
        else:
            statuses.append(dict(contracts_by_prediction_id.get(prediction_id) or {
                "ok": False,
                "applicable": True,
                "version": "",
                "reasons": ["decision_daily_max_prediction_not_found"],
            }))
    return statuses


def _loads_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _loads_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _levels(raw: Any) -> list[dict[str, float]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    levels = []
    for item in raw or []:
        try:
            levels.append({"price": float(item.get("price")), "size": float(item.get("size"))})
        except Exception:
            continue
    return levels


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def insert_ai_review(signal_id: int, review: dict[str, Any]) -> None:
    init_v3_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_reviews (
                signal_id, provider, model, approve, confidence, summary,
                reasons, vetoes, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                review.get("provider", ""),
                review.get("model", ""),
                1 if review.get("approve") else 0,
                _num(review.get("confidence"), 0.0),
                review.get("summary", ""),
                dump_json(review.get("reasons", [])),
                dump_json(review.get("vetoes", [])),
                dump_json(review),
                utc_now(),
            ),
        )


def upsert_market_rule(rule: dict[str, Any]) -> None:
    init_v3_db()
    now = utc_now()
    rule = _normalize_market_rule(rule, now)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO market_rules (
                market_id, exchange_market_id, event_slug, market_slug, question, city, city_name,
                station_id, station_name, timezone, unit, bucket_low, bucket_high,
                metric, resolution_source_text, source_url, truth_confidence,
                confidence_reason, contract_id, target_local_date, bucket_boundary,
                rounding_rule, truth_provider_priority, rule_version, registry_version,
                parsed_at, manual_verified_at, raw_json, updated_at
            ) VALUES (
                :market_id, :exchange_market_id, :event_slug, :market_slug, :question, :city, :city_name,
                :station_id, :station_name, :timezone, :unit, :bucket_low, :bucket_high,
                :metric, :resolution_source_text, :source_url, :truth_confidence,
                :confidence_reason, :contract_id, :target_local_date, :bucket_boundary,
                :rounding_rule, :truth_provider_priority, :rule_version, :registry_version,
                :parsed_at, :manual_verified_at, :raw_json, :updated_at
            )
            ON CONFLICT(market_id) DO UPDATE SET
                event_slug=excluded.event_slug,
                exchange_market_id=excluded.exchange_market_id,
                market_slug=excluded.market_slug,
                question=excluded.question,
                city=excluded.city,
                city_name=excluded.city_name,
                station_id=excluded.station_id,
                station_name=excluded.station_name,
                timezone=excluded.timezone,
                unit=excluded.unit,
                bucket_low=excluded.bucket_low,
                bucket_high=excluded.bucket_high,
                metric=excluded.metric,
                resolution_source_text=excluded.resolution_source_text,
                source_url=excluded.source_url,
                truth_confidence=excluded.truth_confidence,
                confidence_reason=excluded.confidence_reason,
                contract_id=excluded.contract_id,
                target_local_date=excluded.target_local_date,
                bucket_boundary=excluded.bucket_boundary,
                rounding_rule=excluded.rounding_rule,
                truth_provider_priority=excluded.truth_provider_priority,
                rule_version=excluded.rule_version,
                registry_version=excluded.registry_version,
                parsed_at=excluded.parsed_at,
                manual_verified_at=COALESCE(excluded.manual_verified_at, market_rules.manual_verified_at),
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            {**rule, "raw_json": dump_json(rule), "updated_at": now},
        )


def upsert_market_rules(rules: list[dict[str, Any]], prune_missing: bool = False) -> None:
    if not rules:
        return
    init_v3_db()
    now = utc_now()
    normalized = [_normalize_market_rule(rule, now, duplicate_market_ids=_duplicate_market_ids(rules)) for rule in rules]
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO market_rules (
                market_id, exchange_market_id, event_slug, market_slug, question, city, city_name,
                station_id, station_name, timezone, unit, bucket_low, bucket_high,
                metric, resolution_source_text, source_url, truth_confidence,
                confidence_reason, contract_id, target_local_date, bucket_boundary,
                rounding_rule, truth_provider_priority, rule_version, registry_version,
                parsed_at, manual_verified_at, raw_json, updated_at
            ) VALUES (
                :market_id, :exchange_market_id, :event_slug, :market_slug, :question, :city, :city_name,
                :station_id, :station_name, :timezone, :unit, :bucket_low, :bucket_high,
                :metric, :resolution_source_text, :source_url, :truth_confidence,
                :confidence_reason, :contract_id, :target_local_date, :bucket_boundary,
                :rounding_rule, :truth_provider_priority, :rule_version, :registry_version,
                :parsed_at, :manual_verified_at, :raw_json, :updated_at
            )
            ON CONFLICT(market_id) DO UPDATE SET
                event_slug=excluded.event_slug,
                exchange_market_id=excluded.exchange_market_id,
                market_slug=excluded.market_slug,
                question=excluded.question,
                city=excluded.city,
                city_name=excluded.city_name,
                station_id=excluded.station_id,
                station_name=excluded.station_name,
                timezone=excluded.timezone,
                unit=excluded.unit,
                bucket_low=excluded.bucket_low,
                bucket_high=excluded.bucket_high,
                metric=excluded.metric,
                resolution_source_text=excluded.resolution_source_text,
                source_url=excluded.source_url,
                truth_confidence=excluded.truth_confidence,
                confidence_reason=excluded.confidence_reason,
                contract_id=excluded.contract_id,
                target_local_date=excluded.target_local_date,
                bucket_boundary=excluded.bucket_boundary,
                rounding_rule=excluded.rounding_rule,
                truth_provider_priority=excluded.truth_provider_priority,
                rule_version=excluded.rule_version,
                registry_version=excluded.registry_version,
                parsed_at=excluded.parsed_at,
                manual_verified_at=COALESCE(excluded.manual_verified_at, market_rules.manual_verified_at),
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            [{**rule, "raw_json": dump_json(rule), "updated_at": now} for rule in normalized],
        )
        if prune_missing:
            keep_ids = [str(rule.get("market_id") or "") for rule in normalized if rule.get("market_id")]
            if keep_ids:
                conn.execute("CREATE TEMP TABLE IF NOT EXISTS _market_rule_keep (market_id TEXT PRIMARY KEY)")
                conn.execute("DELETE FROM _market_rule_keep")
                conn.executemany("INSERT OR IGNORE INTO _market_rule_keep (market_id) VALUES (?)", [(item,) for item in keep_ids])
                conn.execute("DELETE FROM market_rules WHERE market_id NOT IN (SELECT market_id FROM _market_rule_keep)")
                conn.execute("DROP TABLE _market_rule_keep")


def _duplicate_market_ids(rules: list[dict[str, Any]]) -> set[str]:
    counts: dict[str, int] = {}
    for rule in rules:
        market_id = str(rule.get("market_id") or "")
        if market_id:
            counts[market_id] = counts.get(market_id, 0) + 1
    return {market_id for market_id, count in counts.items() if count > 1}


def _normalize_market_rule(
    rule: dict[str, Any],
    now: str,
    duplicate_market_ids: set[str] | None = None,
) -> dict[str, Any]:
    exchange_market_id = str(rule.get("exchange_market_id") or rule.get("market_id") or "")
    market_id = str(rule.get("market_id") or "")
    if not market_id or market_id in (duplicate_market_ids or set()):
        basis = "|".join(
            [
                str(rule.get("event_slug") or rule.get("contract_id") or ""),
                str(rule.get("question") or ""),
                str(rule.get("bucket_low") if rule.get("bucket_low") is not None else ""),
                str(rule.get("bucket_high") if rule.get("bucket_high") is not None else ""),
            ]
        )
        market_id = "rule:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    event_slug = str(rule.get("event_slug") or "")
    priority = rule.get("truth_provider_priority") or [
        "polymarket_resolved",
        "official_station",
        "visual_crossing_station",
        "open_meteo_archive",
    ]
    if not isinstance(priority, str):
        priority = dump_json(priority)
    return {
        **rule,
        "market_id": market_id,
        "exchange_market_id": exchange_market_id,
        "contract_id": str(rule.get("contract_id") or event_slug),
        "target_local_date": str(rule.get("target_local_date") or rule.get("target_date") or ""),
        "bucket_boundary": str(rule.get("bucket_boundary") or "inclusive"),
        "rounding_rule": str(rule.get("rounding_rule") or "source_reported_daily_high"),
        "truth_provider_priority": priority,
        "rule_version": str(rule.get("rule_version") or "settlement-rule-v1"),
        "registry_version": str(rule.get("registry_version") or "airport-settlement-registry-v1"),
        "parsed_at": str(rule.get("parsed_at") or now),
        "manual_verified_at": rule.get("manual_verified_at"),
    }


def upsert_truth_observation(observation: dict[str, Any]) -> None:
    init_v3_db()
    now = utc_now()
    _, truth_version, supersedes_truth_id = append_truth_observation(observation)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO truth_observations (
                city, city_name, target_date, station_id, station_name, unit,
                actual_temp, provider, source_url, observation_count,
                source_confidence, calibration_eligible, reason_if_ineligible,
                truth_version, supersedes_truth_id, raw_json, created_at
            ) VALUES (
                :city, :city_name, :target_date, :station_id, :station_name, :unit,
                :actual_temp, :provider, :source_url, :observation_count,
                :source_confidence, :calibration_eligible, :reason_if_ineligible,
                :truth_version, :supersedes_truth_id, :raw_json, :created_at
            )
            ON CONFLICT(city, target_date, station_id, provider) DO UPDATE SET
                actual_temp=excluded.actual_temp,
                source_url=excluded.source_url,
                observation_count=excluded.observation_count,
                source_confidence=excluded.source_confidence,
                calibration_eligible=excluded.calibration_eligible,
                reason_if_ineligible=excluded.reason_if_ineligible,
                truth_version=excluded.truth_version,
                supersedes_truth_id=excluded.supersedes_truth_id,
                raw_json=excluded.raw_json,
                created_at=excluded.created_at
            """,
            {
                **observation,
                "calibration_eligible": 1 if observation.get("calibration_eligible") else 0,
                "truth_version": truth_version,
                "supersedes_truth_id": supersedes_truth_id,
                "raw_json": dump_json(observation),
                "created_at": now,
            },
        )


def append_truth_observation(observation: dict[str, Any]) -> tuple[int, str, int | None]:
    init_v3_db()
    now = utc_now()
    truth_key = ":".join(
        [
            str(observation.get("city") or ""),
            str(observation.get("target_date") or ""),
            str(observation.get("station_id") or ""),
            str(observation.get("provider") or ""),
        ]
    )
    version_payload = {
        key: observation.get(key)
        for key in (
            "actual_temp",
            "observation_count",
            "source_confidence",
            "calibration_eligible",
            "reason_if_ineligible",
            "source_url",
            "observed_at",
            "is_preliminary",
            "is_final",
            "quality_flags",
        )
    }
    truth_version = hashlib.sha256(dump_json(version_payload).encode("utf-8")).hexdigest()
    with connect() as conn:
        existing = conn.execute(
            "SELECT id, truth_version FROM truth_observation_versions WHERE truth_key = ? ORDER BY id DESC LIMIT 1",
            (truth_key,),
        ).fetchone()
        if existing and existing["truth_version"] == truth_version:
            return int(existing["id"]), truth_version, None
        supersedes_truth_id = int(existing["id"]) if existing else None
        conn.execute(
            """
            INSERT INTO truth_observation_versions (
                truth_key, truth_version, supersedes_truth_id, city, city_name,
                target_date, station_id, station_name, unit, actual_temp, provider,
                source_url, observation_count, source_confidence,
                calibration_eligible, reason_if_ineligible, observed_at,
                retrieved_at, is_preliminary, is_final, quality_flags,
                raw_json, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                truth_key,
                truth_version,
                supersedes_truth_id,
                observation.get("city"),
                observation.get("city_name"),
                observation.get("target_date"),
                observation.get("station_id"),
                observation.get("station_name"),
                observation.get("unit"),
                observation.get("actual_temp"),
                observation.get("provider"),
                observation.get("source_url"),
                int(observation.get("observation_count") or 0),
                _num(observation.get("source_confidence"), 0.0),
                1 if observation.get("calibration_eligible") else 0,
                observation.get("reason_if_ineligible"),
                observation.get("observed_at"),
                observation.get("retrieved_at") or now,
                1 if observation.get("is_preliminary") else 0,
                1 if observation.get("is_final") else 0,
                dump_json(observation.get("quality_flags", [])),
                dump_json(observation),
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM truth_observation_versions WHERE truth_key = ? AND truth_version = ?",
            (truth_key, truth_version),
        ).fetchone()
        return int(row["id"]), truth_version, supersedes_truth_id


def upsert_settlement_contracts(contracts: list[dict[str, Any]]) -> None:
    if not contracts:
        return
    init_v3_db()
    now = utc_now()
    rows = []
    for contract in contracts:
        event_slug = str(contract.get("event_slug") or "")
        rows.append({
            **contract,
            "contract_id": str(contract.get("contract_id") or event_slug),
            "event_slug": event_slug,
            "truth_provider_priority": dump_json(contract.get("truth_provider_priority", [])),
            "verification_evidence": dump_json(contract.get("verification_evidence", [])),
            "manual_verified_by": contract.get("manual_verified_by"),
            "manual_verification_note": contract.get("manual_verification_note"),
            "manual_verification_snapshot": (
                dump_json(contract.get("manual_verification_snapshot"))
                if contract.get("manual_verification_snapshot") is not None
                else None
            ),
            "raw_json": dump_json(contract),
            "updated_at": now,
        })
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO settlement_contracts (
                contract_id, event_slug, city, city_name, target_local_date,
                station_id, station_name, timezone, unit, metric, rounding_rule,
                bucket_boundary, resolution_source_text, source_url,
                truth_provider_priority, rule_version, registry_version,
                parse_confidence, confidence_reason, auto_verified_at,
                manual_verified_at, manual_verified_by, manual_verification_note,
                manual_verification_snapshot, verification_evidence, raw_json, updated_at
            ) VALUES (
                :contract_id, :event_slug, :city, :city_name, :target_local_date,
                :station_id, :station_name, :timezone, :unit, :metric, :rounding_rule,
                :bucket_boundary, :resolution_source_text, :source_url,
                :truth_provider_priority, :rule_version, :registry_version,
                :parse_confidence, :confidence_reason, :auto_verified_at,
                :manual_verified_at, :manual_verified_by, :manual_verification_note,
                :manual_verification_snapshot, :verification_evidence, :raw_json, :updated_at
            )
            ON CONFLICT(contract_id) DO UPDATE SET
                city=excluded.city,
                city_name=excluded.city_name,
                target_local_date=excluded.target_local_date,
                station_id=excluded.station_id,
                station_name=excluded.station_name,
                timezone=excluded.timezone,
                unit=excluded.unit,
                metric=excluded.metric,
                rounding_rule=excluded.rounding_rule,
                bucket_boundary=excluded.bucket_boundary,
                resolution_source_text=excluded.resolution_source_text,
                source_url=excluded.source_url,
                truth_provider_priority=excluded.truth_provider_priority,
                rule_version=excluded.rule_version,
                registry_version=excluded.registry_version,
                parse_confidence=excluded.parse_confidence,
                confidence_reason=excluded.confidence_reason,
                auto_verified_at=excluded.auto_verified_at,
                manual_verified_at=COALESCE(excluded.manual_verified_at, settlement_contracts.manual_verified_at),
                manual_verified_by=COALESCE(excluded.manual_verified_by, settlement_contracts.manual_verified_by),
                manual_verification_note=COALESCE(excluded.manual_verification_note, settlement_contracts.manual_verification_note),
                manual_verification_snapshot=COALESCE(excluded.manual_verification_snapshot, settlement_contracts.manual_verification_snapshot),
                verification_evidence=excluded.verification_evidence,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            rows,
        )


def list_settlement_contracts(
    status: str = "all",
    city: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    init_v3_db()
    status = str(status or "all")
    where = []
    params: list[Any] = []
    if status == "verified":
        where.append("manual_verified_at IS NOT NULL AND manual_verified_at != ''")
    elif status == "unverified":
        where.append("(manual_verified_at IS NULL OR manual_verified_at = '')")
    elif status == "auto":
        where.append("auto_verified_at IS NOT NULL AND auto_verified_at != ''")
    elif status in {"mature-auto", "future-auto", "manual-required", "source-missing", "low-confidence"}:
        where.append("(manual_verified_at IS NULL OR manual_verified_at = '')")
    if city:
        where.append("city = ?")
        params.append(city)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    with connect() as conn:
        fetched_rows = [
            _decode_contract_row(dict(row))
            for row in conn.execute(
                f"""
                SELECT *
                FROM settlement_contracts
                {clause}
                ORDER BY
                    CASE WHEN manual_verified_at IS NULL OR manual_verified_at = '' THEN 0 ELSE 1 END,
                    target_local_date DESC,
                    city,
                    event_slug
                """,
                params,
            ).fetchall()
        ]
        summary = dict(
            conn.execute(
                """
                SELECT
                    COUNT(*) contracts,
                    SUM(CASE WHEN manual_verified_at IS NOT NULL AND manual_verified_at != '' THEN 1 ELSE 0 END) manual_verified,
                    SUM(CASE WHEN auto_verified_at IS NOT NULL AND auto_verified_at != '' THEN 1 ELSE 0 END) auto_verified
                FROM settlement_contracts
                """
            ).fetchone()
        )
    rows_for_status = [row for row in fetched_rows if _contract_matches_list_status(row, status)]
    total = len(rows_for_status)
    rows = [_contract_review_status(row) for row in rows_for_status[offset:offset + limit]]
    contracts = int(summary.get("contracts") or 0)
    manual_verified = int(summary.get("manual_verified") or 0)
    auto_verified = int(summary.get("auto_verified") or 0)
    return {
        "status": status,
        "city": city,
        "limit": limit,
        "offset": offset,
        "total": total,
        "summary": {
            "contracts": contracts,
            "manual_verified": manual_verified,
            "unverified": max(0, contracts - manual_verified),
            "auto_verified": auto_verified,
            "manual_progress": round((manual_verified / contracts) if contracts else 0.0, 4),
        },
        "contracts": rows,
}


def _contract_matches_list_status(contract: dict[str, Any], status: str) -> bool:
    manual_verified = bool(contract.get("manual_verified_at"))
    auto_verified = bool(contract.get("auto_verified_at"))
    if status in {"all", ""}:
        return True
    if status == "verified":
        return manual_verified
    if status == "unverified":
        return not manual_verified
    if status == "auto":
        return auto_verified
    if status == "mature-auto":
        return auto_verified and not manual_verified and _contract_is_mature(contract)
    if status == "future-auto":
        return auto_verified and not manual_verified and not _contract_is_mature(contract)
    if status == "manual-required":
        return not manual_verified and not auto_verified
    if status == "source-missing":
        return not manual_verified and (
            not contract.get("resolution_source_text") or not contract.get("source_url")
        )
    if status == "low-confidence":
        return not manual_verified and float(contract.get("parse_confidence") or 0.0) < 0.8
    return True


def _contract_review_status(contract: dict[str, Any]) -> dict[str, Any]:
    manual_verified = bool(contract.get("manual_verified_at"))
    auto_verified = bool(contract.get("auto_verified_at"))
    source_missing = not contract.get("resolution_source_text") or not contract.get("source_url")
    low_confidence = float(contract.get("parse_confidence") or 0.0) < 0.8
    mature = _contract_is_mature(contract)
    if manual_verified:
        review_status = "verified"
    elif auto_verified and mature:
        review_status = "mature-auto"
    elif auto_verified:
        review_status = "future-auto"
    else:
        review_status = "manual-required"
    tags: list[str] = []
    if manual_verified:
        tags.append("verified")
    if auto_verified:
        tags.append("auto_verified")
    if mature:
        tags.append("mature")
    else:
        tags.append("pending_settlement")
    if source_missing:
        tags.append("source_missing")
    if low_confidence:
        tags.append("low_confidence")
    if not manual_verified and not auto_verified:
        tags.append("manual_required")
    return {
        **contract,
        "review_status": review_status,
        "review_tags": tags,
    }


def _manual_verification_snapshot(
    contract: dict[str, Any],
    verified: bool,
    reviewer: str,
    note: str,
    reviewed_at: str,
) -> dict[str, Any]:
    reviewed_contract = _contract_review_status(dict(contract))
    return {
        "snapshot_version": "manual-contract-review-v1",
        "reviewed_at": reviewed_at,
        "verified": bool(verified),
        "reviewer": str(reviewer or ""),
        "note": str(note or ""),
        "contract_id": str(reviewed_contract.get("contract_id") or ""),
        "event_slug": str(reviewed_contract.get("event_slug") or ""),
        "city": str(reviewed_contract.get("city") or ""),
        "city_name": str(reviewed_contract.get("city_name") or ""),
        "target_local_date": str(reviewed_contract.get("target_local_date") or ""),
        "station_id": str(reviewed_contract.get("station_id") or ""),
        "station_name": str(reviewed_contract.get("station_name") or ""),
        "timezone": str(reviewed_contract.get("timezone") or ""),
        "unit": str(reviewed_contract.get("unit") or ""),
        "source_url": str(reviewed_contract.get("source_url") or ""),
        "parse_confidence": float(reviewed_contract.get("parse_confidence") or 0.0),
        "confidence_reason": str(reviewed_contract.get("confidence_reason") or ""),
        "review_status_before": str(reviewed_contract.get("review_status") or ""),
        "review_tags_before": list(reviewed_contract.get("review_tags") or []),
        "verification_evidence": list(reviewed_contract.get("verification_evidence") or []),
        "resolution_source_text": str(reviewed_contract.get("resolution_source_text") or ""),
    }


def set_settlement_contract_verification(
    contract_id: str,
    verified: bool,
    reviewer: str = "",
    note: str = "",
) -> dict[str, Any]:
    init_v3_db()
    contract_id = str(contract_id or "").strip()
    if not contract_id:
        raise ValueError("contract_id is required")
    note = str(note or "").strip()
    reviewer = str(reviewer or "").strip()
    if verified and not note:
        raise ValueError("manual verification note is required")
    now = utc_now()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM settlement_contracts WHERE contract_id = ? OR event_slug = ?",
            (contract_id, contract_id),
        ).fetchone()
        if not row:
            raise KeyError(contract_id)
        contract_row = _decode_contract_row(dict(row))
        actual_id = str(contract_row["contract_id"] or contract_id)
        verified_at = now if verified else None
        snapshot = _manual_verification_snapshot(contract_row, verified, reviewer, note, now)
        conn.execute(
            """
            UPDATE settlement_contracts
            SET manual_verified_at = ?,
                manual_verified_by = ?,
                manual_verification_note = ?,
                manual_verification_snapshot = ?,
                updated_at = ?
            WHERE contract_id = ?
            """,
            (verified_at, reviewer, note, dump_json(snapshot), now, actual_id),
        )
        conn.execute(
            """
            UPDATE market_rules
            SET manual_verified_at = ?
            WHERE contract_id = ? OR event_slug = ?
            """,
            (verified_at, actual_id, str(contract_row["event_slug"] or "")),
        )
        updated = conn.execute("SELECT * FROM settlement_contracts WHERE contract_id = ?", (actual_id,)).fetchone()
    return _contract_review_status(_decode_contract_row(dict(updated)))


def bulk_settlement_contract_verification(
    contract_ids: list[str] | None = None,
    limit: int = 5,
    reviewer: str = "",
    note: str = "",
    require_auto_verified: bool = True,
    mature_only: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    init_v3_db()
    limit = max(1, min(int(limit or 5), 50))
    note = str(note or "").strip()
    reviewer = str(reviewer or "").strip()
    if apply and not note:
        raise ValueError("manual verification note is required")
    contract_ids = [str(item).strip() for item in (contract_ids or []) if str(item).strip()]
    where = ["(manual_verified_at IS NULL OR manual_verified_at = '')"]
    params: list[Any] = []
    if require_auto_verified:
        where.append("auto_verified_at IS NOT NULL AND auto_verified_at != ''")
    if contract_ids:
        placeholders = ",".join("?" for _ in contract_ids)
        where.append(f"(contract_id IN ({placeholders}) OR event_slug IN ({placeholders}))")
        params.extend(contract_ids)
        params.extend(contract_ids)
    clause = " AND ".join(where)
    now = utc_now()
    query_limit = 500 if mature_only and not contract_ids else limit
    with connect() as conn:
        candidate_rows = [
            _decode_contract_row(dict(row))
            for row in conn.execute(
                f"""
                SELECT *
                FROM settlement_contracts
                WHERE {clause}
                ORDER BY target_local_date DESC, city, event_slug
                LIMIT ?
                """,
                [*params, query_limit],
            ).fetchall()
        ]
        rows = [
            row for row in candidate_rows
            if not mature_only or _contract_is_mature(row)
        ][:limit]
        selected_ids = [str(row["contract_id"]) for row in rows]
        skipped_requested = [
            item for item in contract_ids
            if item not in selected_ids and item not in {str(row.get("event_slug") or "") for row in rows}
        ]
        if apply and selected_ids:
            placeholders = ",".join("?" for _ in selected_ids)
            updates = [
                (
                    now,
                    reviewer,
                    note,
                    dump_json(_manual_verification_snapshot(row, True, reviewer, note, now)),
                    now,
                    str(row["contract_id"]),
                )
                for row in rows
            ]
            conn.executemany(
                """
                UPDATE settlement_contracts
                SET manual_verified_at = ?,
                    manual_verified_by = ?,
                    manual_verification_note = ?,
                    manual_verification_snapshot = ?,
                    updated_at = ?
                WHERE contract_id = ?
                """,
                updates,
            )
            conn.execute(
                f"""
                UPDATE market_rules
                SET manual_verified_at = ?
                WHERE contract_id IN ({placeholders}) OR event_slug IN (
                    SELECT event_slug FROM settlement_contracts WHERE contract_id IN ({placeholders})
                )
                """,
                [now, *selected_ids, *selected_ids],
            )
            rows = [
                _decode_contract_row(dict(row))
                for row in conn.execute(
                    f"SELECT * FROM settlement_contracts WHERE contract_id IN ({placeholders}) ORDER BY target_local_date DESC, city, event_slug",
                    selected_ids,
                ).fetchall()
            ]
    return {
        "ok": True,
        "applied": bool(apply),
        "selected": len(rows),
        "verified": len(rows) if apply else 0,
        "skipped_requested": skipped_requested,
        "require_auto_verified": require_auto_verified,
        "mature_only": mature_only,
        "contracts": [_contract_review_status(row) for row in rows],
    }


def _contract_is_mature(contract: dict[str, Any]) -> bool:
    from .model_dataset import is_settlement_pending

    target_date = str(contract.get("target_local_date") or "")
    timezone_name = str(contract.get("timezone") or "UTC")
    return bool(target_date) and not is_settlement_pending(target_date, timezone_name)


def _decode_contract_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("truth_provider_priority", "verification_evidence", "manual_verification_snapshot"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except Exception:
                row[key] = {} if key == "manual_verification_snapshot" else []
    return row


def upsert_metar_report(
    report: dict[str, Any],
    *,
    path: Path | None = None,
    _connection=None,
) -> int:
    if _connection is None:
        init_v3_db(path)
    now = utc_now()
    station_id = str(report.get("station_id") or report.get("station") or "").upper()
    report_time = str(report.get("report_time") or report.get("observed_at") or report.get("time") or "")
    if not station_id or not report_time:
        raise ValueError("METAR requires station_id and report_time")
    raw_text = str(report.get("raw_text") or report.get("raw") or report.get("metar") or "")
    report_key = _stable_key("metar", station_id, report_time)
    connection_context = connect(path) if _connection is None else nullcontext(_connection)
    with connection_context as conn:
        conn.execute(
            """
            INSERT INTO metar_reports (
                report_key, city, city_name, station_id, report_type, report_time,
                raw_text, temperature, dew_point, wind_direction, wind_speed,
                wind_gust, visibility, cloud_layers_json, altimeter, pressure,
                precipitation, sea_level_pressure, peak_wind_json, source_url,
                parser_version, parse_status, parse_warnings, raw_json,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(station_id, report_time) DO UPDATE SET
                city=excluded.city,
                city_name=excluded.city_name,
                station_id=excluded.station_id,
                report_type=excluded.report_type,
                report_time=excluded.report_time,
                raw_text=excluded.raw_text,
                temperature=excluded.temperature,
                dew_point=excluded.dew_point,
                wind_direction=excluded.wind_direction,
                wind_speed=excluded.wind_speed,
                wind_gust=excluded.wind_gust,
                visibility=excluded.visibility,
                cloud_layers_json=excluded.cloud_layers_json,
                altimeter=excluded.altimeter,
                pressure=excluded.pressure,
                precipitation=excluded.precipitation,
                sea_level_pressure=excluded.sea_level_pressure,
                peak_wind_json=excluded.peak_wind_json,
                source_url=excluded.source_url,
                parser_version=excluded.parser_version,
                parse_status=excluded.parse_status,
                parse_warnings=excluded.parse_warnings,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                report_key,
                report.get("city"),
                report.get("city_name"),
                station_id,
                report.get("report_type") or "METAR",
                report_time,
                raw_text,
                _num(report.get("temperature"), 0.0),
                _num(report.get("dew_point"), 0.0),
                _num(report.get("wind_direction"), 0.0),
                _num(report.get("wind_speed"), 0.0),
                _num(report.get("wind_gust"), 0.0),
                _num(report.get("visibility"), 0.0),
                dump_json(report.get("cloud_layers", [])),
                _num(report.get("altimeter"), 0.0),
                _num(report.get("pressure"), 0.0),
                _num(report.get("precipitation"), 0.0),
                _num(report.get("sea_level_pressure"), 0.0),
                dump_json(report.get("peak_wind", {})),
                report.get("source_url"),
                report.get("parser_version") or "weatherbot-v3",
                report.get("parse_status") or "parsed",
                dump_json(report.get("parse_warnings", [])),
                dump_json(report),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM metar_reports WHERE station_id = ? AND report_time = ?",
            (station_id, report_time),
        ).fetchone()
        return int(row["id"]) if row else 0


def upsert_metar_reports(reports: list[dict[str, Any]], path: Path | None = None) -> int:
    if not reports:
        return 0
    init_v3_db(path)
    with connect(path) as conn:
        for report in reports:
            upsert_metar_report(report, path=path, _connection=conn)
    return len(reports)


def upsert_mesonet_observation(observation: dict[str, Any]) -> int:
    init_v3_db()
    now = utc_now()
    station_id = str(observation.get("station_id") or observation.get("station") or "")
    observed_at = str(observation.get("observed_at") or observation.get("time") or "")
    network = str(observation.get("network") or observation.get("source") or "mesonet")
    observation_key = str(
        observation.get("observation_key")
        or _stable_key("mesonet", network, station_id, observed_at)
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO mesonet_observations (
                observation_key, city, city_name, station_id, station_name, network,
                observed_at, temperature, humidity, dew_point, wind_direction,
                wind_speed, wind_gust, pressure, precipitation, source_url,
                raw_response, raw_response_hash, parser_version, parse_status,
                parse_warnings, raw_unit, quality_flags, fetched_at, raw_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_key) DO UPDATE SET
                city=excluded.city,
                city_name=excluded.city_name,
                station_id=excluded.station_id,
                station_name=excluded.station_name,
                network=excluded.network,
                observed_at=excluded.observed_at,
                temperature=excluded.temperature,
                humidity=excluded.humidity,
                dew_point=excluded.dew_point,
                wind_direction=excluded.wind_direction,
                wind_speed=excluded.wind_speed,
                wind_gust=excluded.wind_gust,
                pressure=excluded.pressure,
                precipitation=excluded.precipitation,
                source_url=excluded.source_url,
                raw_response=excluded.raw_response,
                raw_response_hash=excluded.raw_response_hash,
                parser_version=excluded.parser_version,
                parse_status=excluded.parse_status,
                parse_warnings=excluded.parse_warnings,
                raw_unit=excluded.raw_unit,
                quality_flags=excluded.quality_flags,
                fetched_at=excluded.fetched_at,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                observation_key,
                observation.get("city"),
                observation.get("city_name"),
                station_id,
                observation.get("station_name"),
                network,
                observed_at,
                _nullable_num(observation.get("temperature")),
                _nullable_num(observation.get("humidity")),
                _nullable_num(observation.get("dew_point")),
                _nullable_num(observation.get("wind_direction")),
                _nullable_num(observation.get("wind_speed")),
                _nullable_num(observation.get("wind_gust")),
                _nullable_num(observation.get("pressure")),
                _nullable_num(observation.get("precipitation")),
                observation.get("source_url"),
                observation.get("raw_response") or "",
                observation.get("raw_response_hash") or _hash_text(observation.get("raw_response") or ""),
                observation.get("parser_version") or "weatherbot-mesonet-v1",
                observation.get("parse_status") or "parsed",
                dump_json(observation.get("parse_warnings", [])),
                observation.get("raw_unit") or observation.get("source_unit") or "",
                dump_json(observation.get("quality_flags", [])),
                observation.get("fetched_at") or now,
                dump_json(observation),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM mesonet_observations WHERE observation_key = ?",
            (observation_key,),
        ).fetchone()
        return int(row["id"]) if row else 0


HOURLY_CONSENSUS_UPSERT_SQL = """
    INSERT INTO hourly_consensus (
        consensus_key, city, city_name, target_date, local_hour, valid_time,
        station_id, forecast_temp, observed_temp, observation_source,
        humidity, cloud_cover, precipitation, wind_speed, wind_direction,
        pressure, dew_point, residual, forecast_spread, forecast_member_count,
        consensus_method, source_count, source_weights_json,
        forecast_source, forecast_sources_json, observation_sources_json,
        source_mix_json, consensus_version, build_status, build_warnings,
        peak_marker, taf_marker, raw_json, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(city, target_date, local_hour) DO UPDATE SET
        city=excluded.city,
        city_name=excluded.city_name,
        target_date=excluded.target_date,
        local_hour=excluded.local_hour,
        valid_time=excluded.valid_time,
        station_id=excluded.station_id,
        forecast_temp=excluded.forecast_temp,
        observed_temp=excluded.observed_temp,
        observation_source=excluded.observation_source,
        humidity=excluded.humidity,
        cloud_cover=excluded.cloud_cover,
        precipitation=excluded.precipitation,
        wind_speed=excluded.wind_speed,
        wind_direction=excluded.wind_direction,
        pressure=excluded.pressure,
        dew_point=excluded.dew_point,
        residual=excluded.residual,
        forecast_spread=excluded.forecast_spread,
        forecast_member_count=excluded.forecast_member_count,
        consensus_method=excluded.consensus_method,
        source_count=excluded.source_count,
        source_weights_json=excluded.source_weights_json,
        forecast_source=excluded.forecast_source,
        forecast_sources_json=excluded.forecast_sources_json,
        observation_sources_json=excluded.observation_sources_json,
        source_mix_json=excluded.source_mix_json,
        consensus_version=excluded.consensus_version,
        build_status=excluded.build_status,
        build_warnings=excluded.build_warnings,
        peak_marker=excluded.peak_marker,
        taf_marker=excluded.taf_marker,
        raw_json=excluded.raw_json,
        updated_at=excluded.updated_at
"""


def _hourly_consensus_values(row: dict[str, Any], now: str) -> tuple[Any, ...]:
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    local_hour = str(row.get("local_hour") or row.get("hour") or "")
    valid_time = str(row.get("valid_time") or row.get("time") or "")
    if not city or not target_date or not local_hour:
        raise ValueError("hourly consensus requires city, target_date and local_hour")
    consensus_key = _stable_key("hourly_consensus", city, target_date, local_hour)
    forecast_temp = _nullable_num(row.get("forecast_temp"))
    observed_temp = _nullable_num(row.get("observed_temp"))
    residual = row.get("residual")
    if residual is None and observed_temp is not None and forecast_temp is not None:
        residual = observed_temp - forecast_temp
    return (
        consensus_key,
        city,
        row.get("city_name"),
        target_date,
        local_hour,
        valid_time,
        row.get("station_id"),
        forecast_temp,
        observed_temp,
        row.get("observation_source"),
        _nullable_num(row.get("humidity")),
        _nullable_num(row.get("cloud_cover")),
        _nullable_num(row.get("precipitation")),
        _nullable_num(row.get("wind_speed")),
        _nullable_num(row.get("wind_direction")),
        _nullable_num(row.get("pressure")),
        _nullable_num(row.get("dew_point")),
        _nullable_num(residual),
        _nullable_num(row.get("forecast_spread")),
        int(row.get("forecast_member_count") or 0),
        row.get("consensus_method"),
        int(row.get("source_count") or 0),
        dump_json(row.get("source_weights", {})),
        row.get("forecast_source"),
        dump_json(row.get("forecast_sources", [])),
        dump_json(row.get("observation_sources", [])),
        dump_json(row.get("source_mix", {})),
        row.get("consensus_version") or "hourly-consensus-v1",
        row.get("build_status") or "built",
        dump_json(row.get("build_warnings", [])),
        row.get("peak_marker"),
        row.get("taf_marker"),
        dump_json(row),
        now,
        now,
    )


def upsert_hourly_consensus(row: dict[str, Any], path: Path | None = None) -> int:
    init_v3_db(path)
    now = utc_now()
    values = _hourly_consensus_values(row, now)
    with connect(path) as conn:
        conn.execute(HOURLY_CONSENSUS_UPSERT_SQL, values)
        found = conn.execute(
            "SELECT id FROM hourly_consensus WHERE city = ? AND target_date = ? AND local_hour = ?",
            (str(values[1]), str(values[3]), str(values[4])),
        ).fetchone()
        return int(found["id"]) if found else 0


def upsert_hourly_consensus_rows(rows: list[dict[str, Any]], path: Path | None = None) -> int:
    init_v3_db(path)
    now = utc_now()
    values = [_hourly_consensus_values(row, now) for row in rows]
    if not values:
        return 0
    with connect(path) as conn:
        conn.executemany(HOURLY_CONSENSUS_UPSERT_SQL, values)
    return len(values)


def weather_evidence_summary(city: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    init_v3_db()
    where: list[str] = []
    params: list[Any] = []
    if city:
        where.append("city = ?")
        params.append(city)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    consensus_clause = f"WHERE {' AND '.join(where)}" if where else ""
    city_only_clause = "WHERE city = ?" if city else ""
    city_params = [city] if city else []
    with connect() as conn:
        def count(sql: str, args: list[Any] | tuple[Any, ...] = ()) -> int:
            return int(conn.execute(sql, tuple(args)).fetchone()[0])

        return {
            "metar_reports": count(f"SELECT COUNT(*) FROM metar_reports {city_only_clause}", city_params),
            "mesonet_observations": count(
                f"SELECT COUNT(*) FROM mesonet_observations {city_only_clause}",
                city_params,
            ),
            "hourly_consensus": count(f"SELECT COUNT(*) FROM hourly_consensus {consensus_clause}", params),
            "forecast_runs": count(f"SELECT COUNT(*) FROM forecast_runs {consensus_clause}", params),
            "forecast_members": count(
                f"""
                SELECT COUNT(*)
                FROM forecast_members fm
                JOIN forecast_runs fr ON fr.id = fm.run_id
                {consensus_clause.replace('WHERE', 'WHERE fr.') if consensus_clause else ''}
                """,
                params,
            ) if not consensus_clause else count(
                """
                SELECT COUNT(*)
                FROM forecast_members fm
                JOIN forecast_runs fr ON fr.id = fm.run_id
                WHERE fr.city = ? AND fr.target_date = ?
                """ if city and target_date else (
                    """
                    SELECT COUNT(*)
                    FROM forecast_members fm
                    JOIN forecast_runs fr ON fr.id = fm.run_id
                    WHERE fr.city = ?
                    """ if city else """
                    SELECT COUNT(*)
                    FROM forecast_members fm
                    JOIN forecast_runs fr ON fr.id = fm.run_id
                    WHERE fr.target_date = ?
                    """
                ),
                params,
            ),
            "latest_metar_reports": [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM metar_reports {city_only_clause} ORDER BY report_time DESC, id DESC LIMIT 10",
                    tuple(city_params),
                ).fetchall()
            ],
            "latest_mesonet_observations": [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM mesonet_observations {city_only_clause} ORDER BY observed_at DESC, id DESC LIMIT 10",
                    tuple(city_params),
                ).fetchall()
            ],
            "latest_hourly_consensus": [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM hourly_consensus {consensus_clause} ORDER BY target_date DESC, local_hour DESC, id DESC LIMIT 24",
                    tuple(params),
                ).fetchall()
            ],
            "latest_forecast_runs": [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM forecast_runs {consensus_clause} ORDER BY COALESCE(available_at, retrieved_at, created_at) DESC, id DESC LIMIT 10",
                    tuple(params),
                ).fetchall()
            ],
        }


def forecast_summary(city: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    init_v3_db()
    where: list[str] = []
    params: list[Any] = []
    if city:
        where.append("city = ?")
        params.append(city)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    member_join_clause = f"WHERE {' AND '.join('fr.' + item for item in where)}" if where else ""
    with connect() as conn:
        run_count = int(conn.execute(f"SELECT COUNT(*) FROM forecast_runs {clause}", tuple(params)).fetchone()[0])
        runs = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM forecast_runs
                {clause}
                ORDER BY COALESCE(available_at, retrieved_at, created_at) DESC, id DESC
                LIMIT 50
                """,
                tuple(params),
            ).fetchall()
        ]
        member_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM forecast_members fm
                JOIN forecast_runs fr ON fr.id = fm.run_id
                {member_join_clause}
                """,
                tuple(params),
            ).fetchone()[0]
        )
        source_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT source, COUNT(*) AS runs, SUM(COALESCE(member_count, 0)) AS members
                FROM forecast_runs
                {clause}
                GROUP BY source
                ORDER BY runs DESC, source
                """,
                tuple(params),
            ).fetchall()
        ]
    return {
        "ok": True,
        "city": city or "",
        "target_date": target_date or "",
        "runs": run_count,
        "members": member_count,
        "sources": source_rows,
        "latest_runs": runs,
    }


def insert_forecast_run(
    run: dict[str, Any],
    members: list[dict[str, Any]] | None = None,
    *,
    path: Path | None = None,
    _connection=None,
) -> int:
    if _connection is None:
        init_v3_db(path)
    from .forecast_time import prepare_forecast_snapshot

    now = utc_now()
    prepared = prepare_forecast_snapshot(run, members or [])
    temporal_reasons = {
        "forecast_available_at_missing",
        "forecast_lead_missing",
        "forecast_lead_negative",
        "forecast_after_target_day",
        "forecast_after_target_start",
    }
    ineligibility_reason = str(prepared.get("ineligibility_reason") or "")
    quarantined_at = now if ineligibility_reason in temporal_reasons else None
    row = {
        "run_key": str(prepared["run_key"]),
        "snapshot_key": str(prepared["snapshot_key"]),
        "city": prepared.get("city"),
        "target_date": prepared.get("target_date"),
        "source": prepared.get("source"),
        "provider": prepared.get("provider"),
        "model": prepared.get("model"),
        "model_version": prepared.get("model_version"),
        "run_type": prepared.get("run_type", "forecast"),
        "run_at": prepared.get("run_at"),
        "retrieved_at": prepared.get("retrieved_at"),
        "available_at": prepared.get("available_at"),
        "availability_basis": prepared.get("availability_basis"),
        "valid_at": prepared.get("valid_at"),
        "horizon": prepared.get("horizon"),
        "lead_hours": _nullable_num(prepared.get("lead_hours")),
        "latitude": _nullable_num(prepared.get("latitude")),
        "longitude": _nullable_num(prepared.get("longitude")),
        "station_id": prepared.get("station_id"),
        "timezone": prepared.get("timezone"),
        "unit": prepared.get("unit"),
        "mean_high": _nullable_num(prepared.get("mean_high")),
        "std_high": _nullable_num(prepared.get("std_high")),
        "member_count": int(prepared.get("member_count") or len(members or [])),
        "source_url": prepared.get("source_url"),
        "raw_response_hash": prepared.get("raw_response_hash"),
        "data_license": prepared.get("data_license"),
        "quality_flags": dump_json(prepared.get("quality_flags", [])),
        "parser_version": prepared.get("parser_version") or "forecast-run-v1",
        "parse_status": prepared.get("parse_status") or "parsed",
        "parse_warnings": dump_json(prepared.get("parse_warnings", [])),
        "source_unit": prepared.get("source_unit") or prepared.get("unit") or "",
        "training_eligible": 1 if prepared.get("training_eligible") else 0,
        "ineligibility_reason": ineligibility_reason,
        "quarantined_at": quarantined_at,
        "quarantine_reason": ineligibility_reason if quarantined_at else None,
        "raw_json": dump_json(prepared),
        "created_at": now,
    }
    connection_context = connect(path) if _connection is None else nullcontext(_connection)
    with connection_context as conn:
        conn.execute(
            """
            INSERT INTO forecast_runs (
                run_key, snapshot_key, city, target_date, source, provider, model, model_version,
                run_type, run_at, retrieved_at, available_at, availability_basis,
                valid_at, horizon, lead_hours,
                latitude, longitude, station_id, timezone, unit, mean_high, std_high,
                member_count, source_url, raw_response_hash, data_license,
                quality_flags, parser_version, parse_status, parse_warnings, source_unit,
                training_eligible, ineligibility_reason,
                quarantined_at, quarantine_reason, raw_json, created_at
            ) VALUES (
                :run_key, :snapshot_key, :city, :target_date, :source, :provider, :model, :model_version,
                :run_type, :run_at, :retrieved_at, :available_at, :availability_basis,
                :valid_at, :horizon, :lead_hours,
                :latitude, :longitude, :station_id, :timezone, :unit, :mean_high, :std_high,
                :member_count, :source_url, :raw_response_hash, :data_license,
                :quality_flags, :parser_version, :parse_status, :parse_warnings, :source_unit,
                :training_eligible, :ineligibility_reason,
                :quarantined_at, :quarantine_reason, :raw_json, :created_at
            )
            ON CONFLICT(snapshot_key) DO NOTHING
            """,
            row,
        )
        found = conn.execute(
            "SELECT id FROM forecast_runs WHERE snapshot_key = ?",
            (row["snapshot_key"],),
        ).fetchone()
        if found is None:
            raise ValueError("forecast snapshot identity conflict")
        run_id = int(found["id"])
        for member in members or []:
            conn.execute(
                """
                INSERT INTO forecast_members (
                    run_id, member_name, high_temp, member_id, hourly_json,
                    raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, member_id) DO NOTHING
                """,
                (
                    run_id,
                    member.get("member_name") or member.get("member_id"),
                    _num(member.get("high_temp"), 0.0),
                    str(member.get("member_id") or member.get("member_name") or "deterministic"),
                    dump_json(member.get("hourly", [])),
                    dump_json(member),
                    now,
                ),
            )
        return run_id


def insert_forecast_runs(
    items: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    path: Path | None = None,
) -> list[int]:
    if not items:
        return []
    init_v3_db(path)
    with connect(path) as conn:
        return [
            insert_forecast_run(run, members, path=path, _connection=conn)
            for run, members in items
        ]


def insert_event_distribution(market_id: str, event_slug: str, distribution: dict[str, Any], signal_id: int | None = None) -> None:
    init_v3_db()
    with connect() as conn:
        if signal_id is None:
            conn.execute("DELETE FROM event_distributions WHERE market_id = ? AND signal_id IS NULL", (market_id,))
        else:
            conn.execute("DELETE FROM event_distributions WHERE market_id = ? AND signal_id = ?", (market_id, signal_id))
        conn.execute(
            """
            INSERT INTO event_distributions (
                market_id, event_slug, signal_id, sum_probability,
                normalized, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_id,
                event_slug,
                signal_id,
                _num(distribution.get("sum_probability"), 0.0),
                1 if distribution.get("normalized") else 0,
                dump_json(distribution),
                utc_now(),
            ),
        )


def upsert_signal_decision(signal_id: int, decision: dict[str, Any], path: Path | None = None) -> None:
    init_v3_db(path)
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO signal_decisions (
                signal_id, market_id, action, live_allowed, paper_allowed,
                reasons, cautions, model_distribution_json, model_bucket_probs_json,
                market_bucket_probs_json, edge_by_bucket_json, gate_reasons_json,
                raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                market_id=excluded.market_id,
                action=excluded.action,
                live_allowed=excluded.live_allowed,
                paper_allowed=excluded.paper_allowed,
                reasons=excluded.reasons,
                cautions=excluded.cautions,
                model_distribution_json=excluded.model_distribution_json,
                model_bucket_probs_json=excluded.model_bucket_probs_json,
                market_bucket_probs_json=excluded.market_bucket_probs_json,
                edge_by_bucket_json=excluded.edge_by_bucket_json,
                gate_reasons_json=excluded.gate_reasons_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                signal_id,
                decision.get("market_id"),
                decision.get("action"),
                1 if decision.get("live_allowed") else 0,
                1 if decision.get("paper_allowed", True) else 0,
                dump_json(decision.get("reasons", [])),
                dump_json(decision.get("cautions", [])),
                dump_json(decision.get("model_distribution", decision.get("model_distribution_json", {}))),
                dump_json(decision.get("model_bucket_probs", decision.get("model_bucket_probs_json", {}))),
                dump_json(decision.get("market_bucket_probs", decision.get("market_bucket_probs_json", {}))),
                dump_json(decision.get("edge_by_bucket", decision.get("edge_by_bucket_json", {}))),
                dump_json(decision.get("gate_reasons", decision.get("gate_reasons_json", []))),
                dump_json(decision),
                utc_now(),
            ),
        )
        conn.execute("UPDATE signals SET decision_json = ? WHERE id = ?", (dump_json(decision), signal_id))


def upsert_signal_decision_record(decision: dict[str, Any], path: Path | None = None) -> int:
    init_v3_db(path)
    now = utc_now()
    decision_id = str(decision.get("decision_id") or _stable_key(
        "signal_decision",
        decision.get("city_key") or decision.get("city") or "",
        decision.get("target_date") or "",
        decision.get("token_id") or decision.get("yes_token_id") or "",
        decision.get("issued_at") or "",
    ))
    row = {
        "decision_id": decision_id,
        "signal_id": decision.get("signal_id"),
        "market_id": str(decision.get("market_id") or ""),
        "bucket_id": decision.get("bucket_id"),
        "bucket_key": str(decision.get("bucket_key") or ""),
        "city_key": str(decision.get("city_key") or decision.get("city") or ""),
        "target_date": str(decision.get("target_date") or ""),
        "issued_at": str(decision.get("issued_at") or ""),
        "token_id": str(decision.get("token_id") or decision.get("yes_token_id") or ""),
        "yes_token_id": str(decision.get("yes_token_id") or decision.get("token_id") or ""),
        "bucket_direction": str(decision.get("bucket_direction") or ""),
        "bucket_lower": _nullable_num(decision.get("bucket_lower")),
        "bucket_upper": _nullable_num(decision.get("bucket_upper")),
        "mu": _nullable_num(decision.get("mu")),
        "sigma": _nullable_num(decision.get("sigma")),
        "deb_version": str(decision.get("deb_version") or ""),
        "forecast_algo": str(decision.get("forecast_algo") or decision.get("algo") or decision.get("deb_version") or ""),
        "model_probability": _nullable_num(decision.get("model_probability")),
        "market_ask": _nullable_num(decision.get("market_ask")),
        "market_bid": _nullable_num(decision.get("market_bid")),
        "market_mid": _nullable_num(decision.get("market_mid")),
        "market_implied_probability": _nullable_num(decision.get("market_implied_probability")),
        "edge": _nullable_num(decision.get("edge")),
        "edge_percent": _nullable_num(decision.get("edge_percent")),
        "strategy_name": str(decision.get("strategy_name") or "single_bucket_ev"),
        "kelly_fraction": _nullable_num(decision.get("kelly_fraction")),
        "position_size_usd": _nullable_num(decision.get("position_size_usd")),
        "ladder_group_id": str(decision.get("ladder_group_id") or ""),
        "strategy_revision_id": str(decision.get("strategy_revision_id") or ""),
        "strategy_params_hash": str(decision.get("strategy_params_hash") or ""),
        "strategy_params_snapshot_json": dump_json(decision.get("strategy_params_snapshot", {})),
        "sizing_bankroll_usd": _nullable_num(decision.get("sizing_bankroll_usd")),
        "sizing_max_per_trade_usd": _nullable_num(decision.get("sizing_max_per_trade_usd")),
        "kelly_multiplier": _nullable_num(decision.get("kelly_multiplier")),
        "bankroll_fraction_cap": _nullable_num(decision.get("bankroll_fraction_cap")),
        "orderbook_snapshot_json": dump_json(decision.get("orderbook_snapshot", decision.get("orderbook_snapshot_json", {}))),
        "tick_size": _nullable_num(decision.get("tick_size")),
        "order_min_size": _nullable_num(decision.get("order_min_size")),
        "neg_risk": 1 if decision.get("neg_risk") else 0,
        "book_age_seconds": _nullable_num(decision.get("book_age_seconds")),
        "spread_bps": _nullable_num(decision.get("spread_bps")),
        "gate_status": str(decision.get("gate_status") or ""),
        "paper_decision": str(decision.get("paper_decision") or ""),
        "live_decision": str(decision.get("live_decision") or ""),
        "blocked_reason_primary": str(decision.get("blocked_reason_primary") or ""),
        "evidence_links_json": dump_json(decision.get("evidence_links", decision.get("evidence_links_json", {}))),
        "decision_version": str(decision.get("decision_version") or "signal-decision-v3"),
        "action": str(decision.get("action") or "observe"),
        "live_allowed": 1 if decision.get("live_allowed") else 0,
        "paper_allowed": 1 if decision.get("paper_allowed") else 0,
        "reasons": dump_json(decision.get("reasons", [])),
        "cautions": dump_json(decision.get("cautions", [])),
        "model_distribution_json": dump_json(decision.get("model_distribution", decision.get("model_distribution_json", {}))),
        "model_bucket_probs_json": dump_json(decision.get("model_bucket_probs", decision.get("model_bucket_probs_json", {}))),
        "market_bucket_probs_json": dump_json(decision.get("market_bucket_probs", decision.get("market_bucket_probs_json", {}))),
        "edge_by_bucket_json": dump_json(decision.get("edge_by_bucket", decision.get("edge_by_bucket_json", {}))),
        "gate_reasons_json": dump_json(decision.get("gate_reasons", decision.get("gate_reasons_json", []))),
        "raw_json": dump_json({**decision, "decision_id": decision_id}),
        "updated_at": now,
    }
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO signal_decisions (
                decision_id, signal_id, market_id, bucket_id, bucket_key, city_key,
                target_date, issued_at, token_id, yes_token_id, bucket_direction,
                bucket_lower, bucket_upper, mu, sigma, deb_version, forecast_algo,
                model_probability, market_ask, market_bid, market_mid,
                market_implied_probability, edge, edge_percent, strategy_name,
                kelly_fraction, position_size_usd, ladder_group_id, orderbook_snapshot_json,
                strategy_revision_id, strategy_params_hash, strategy_params_snapshot_json,
                sizing_bankroll_usd, sizing_max_per_trade_usd, kelly_multiplier,
                bankroll_fraction_cap,
                tick_size, order_min_size, neg_risk, book_age_seconds, spread_bps,
                gate_status, paper_decision, live_decision, blocked_reason_primary,
                evidence_links_json, decision_version, action, live_allowed, paper_allowed,
                reasons, cautions, model_distribution_json, model_bucket_probs_json,
                market_bucket_probs_json, edge_by_bucket_json, gate_reasons_json,
                raw_json, updated_at
            ) VALUES (
                :decision_id, :signal_id, :market_id, :bucket_id, :bucket_key, :city_key,
                :target_date, :issued_at, :token_id, :yes_token_id, :bucket_direction,
                :bucket_lower, :bucket_upper, :mu, :sigma, :deb_version, :forecast_algo,
                :model_probability, :market_ask, :market_bid, :market_mid,
                :market_implied_probability, :edge, :edge_percent, :strategy_name,
                :kelly_fraction, :position_size_usd, :ladder_group_id, :orderbook_snapshot_json,
                :strategy_revision_id, :strategy_params_hash, :strategy_params_snapshot_json,
                :sizing_bankroll_usd, :sizing_max_per_trade_usd, :kelly_multiplier,
                :bankroll_fraction_cap,
                :tick_size, :order_min_size, :neg_risk, :book_age_seconds, :spread_bps,
                :gate_status, :paper_decision, :live_decision, :blocked_reason_primary,
                :evidence_links_json, :decision_version, :action, :live_allowed, :paper_allowed,
                :reasons, :cautions, :model_distribution_json, :model_bucket_probs_json,
                :market_bucket_probs_json, :edge_by_bucket_json, :gate_reasons_json,
                :raw_json, :updated_at
            )
            ON CONFLICT(decision_id) DO UPDATE SET
                signal_id=excluded.signal_id,
                market_id=excluded.market_id,
                bucket_id=excluded.bucket_id,
                bucket_key=excluded.bucket_key,
                city_key=excluded.city_key,
                target_date=excluded.target_date,
                issued_at=excluded.issued_at,
                token_id=excluded.token_id,
                yes_token_id=excluded.yes_token_id,
                bucket_direction=excluded.bucket_direction,
                bucket_lower=excluded.bucket_lower,
                bucket_upper=excluded.bucket_upper,
                mu=excluded.mu,
                sigma=excluded.sigma,
                deb_version=excluded.deb_version,
                forecast_algo=excluded.forecast_algo,
                model_probability=excluded.model_probability,
                market_ask=excluded.market_ask,
                market_bid=excluded.market_bid,
                market_mid=excluded.market_mid,
                market_implied_probability=excluded.market_implied_probability,
                edge=excluded.edge,
                edge_percent=excluded.edge_percent,
                strategy_name=excluded.strategy_name,
                kelly_fraction=excluded.kelly_fraction,
                position_size_usd=excluded.position_size_usd,
                ladder_group_id=excluded.ladder_group_id,
                strategy_revision_id=excluded.strategy_revision_id,
                strategy_params_hash=excluded.strategy_params_hash,
                strategy_params_snapshot_json=excluded.strategy_params_snapshot_json,
                sizing_bankroll_usd=excluded.sizing_bankroll_usd,
                sizing_max_per_trade_usd=excluded.sizing_max_per_trade_usd,
                kelly_multiplier=excluded.kelly_multiplier,
                bankroll_fraction_cap=excluded.bankroll_fraction_cap,
                orderbook_snapshot_json=excluded.orderbook_snapshot_json,
                tick_size=excluded.tick_size,
                order_min_size=excluded.order_min_size,
                neg_risk=excluded.neg_risk,
                book_age_seconds=excluded.book_age_seconds,
                spread_bps=excluded.spread_bps,
                gate_status=excluded.gate_status,
                paper_decision=excluded.paper_decision,
                live_decision=excluded.live_decision,
                blocked_reason_primary=excluded.blocked_reason_primary,
                evidence_links_json=excluded.evidence_links_json,
                decision_version=excluded.decision_version,
                action=excluded.action,
                live_allowed=excluded.live_allowed,
                paper_allowed=excluded.paper_allowed,
                reasons=excluded.reasons,
                cautions=excluded.cautions,
                model_distribution_json=excluded.model_distribution_json,
                model_bucket_probs_json=excluded.model_bucket_probs_json,
                market_bucket_probs_json=excluded.market_bucket_probs_json,
                edge_by_bucket_json=excluded.edge_by_bucket_json,
                gate_reasons_json=excluded.gate_reasons_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            row,
        )
        found = conn.execute("SELECT id FROM signal_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return int(found["id"]) if found else 0


def list_signal_decisions(
    city_key: str | None = None,
    target_date: str | None = None,
    decision_id: str | None = None,
    limit: int = 100,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    init_v3_db(path)
    where: list[str] = []
    params: list[Any] = []
    if decision_id:
        where.append("decision_id = ?")
        params.append(decision_id)
    if city_key:
        where.append("city_key = ?")
        params.append(city_key)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    bounded_limit = max(1, min(int(limit or 100), 1000))
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM signal_decisions
                {clause}
                ORDER BY issued_at DESC, edge DESC, id DESC
                LIMIT ?
                """,
                (*params, bounded_limit),
            ).fetchall()
        ]
    for row in rows:
        row["live_allowed"] = bool(row.get("live_allowed"))
        row["paper_allowed"] = bool(row.get("paper_allowed"))
        row["neg_risk"] = bool(row.get("neg_risk"))
        row["strategy_name"] = row.get("strategy_name") or "single_bucket_ev"
        row["forecast_algo"] = row.get("forecast_algo") or row.get("deb_version") or ""
        row["ladder_group_id"] = row.get("ladder_group_id") or ""
        row["strategy_revision_id"] = row.get("strategy_revision_id") or ""
        row["strategy_params_snapshot"] = _loads_obj(row.get("strategy_params_snapshot_json"))
        row["reasons"] = _loads_list(row.get("reasons"))
        row["cautions"] = _loads_list(row.get("cautions"))
        row["gate_reasons"] = _loads_list(row.get("gate_reasons_json"))
        row["model_distribution"] = _loads_obj(row.get("model_distribution_json"))
        row["model_bucket_probs"] = _loads_obj(row.get("model_bucket_probs_json"))
        row["market_bucket_probs"] = _loads_list(row.get("market_bucket_probs_json"))
        row["edge_by_bucket"] = _loads_obj(row.get("edge_by_bucket_json"))
        row["orderbook_snapshot"] = _loads_obj(row.get("orderbook_snapshot_json"))
        row["evidence_links"] = _loads_obj(row.get("evidence_links_json"))
    return rows


def upsert_model_reprice_event(event: dict[str, Any], path: Path | None = None) -> int:
    init_v3_db(path)
    now = utc_now()
    event_key = str(event.get("event_key") or _stable_key(
        "model_reprice",
        event.get("city_key") or event.get("city") or "",
        event.get("target_date") or "",
        event.get("market_id") or "",
        event.get("bucket_key") or "",
        event.get("triggered_at") or now,
        event.get("model_source") or "",
    ))
    row = {
        "event_key": event_key,
        "city_key": str(event.get("city_key") or event.get("city") or ""),
        "target_date": str(event.get("target_date") or ""),
        "market_id": str(event.get("market_id") or ""),
        "bucket_key": str(event.get("bucket_key") or ""),
        "triggered_at": str(event.get("triggered_at") or now),
        "model_source": str(event.get("model_source") or event.get("forecast_algo") or ""),
        "previous_model_prob": _nullable_num(event.get("previous_model_prob")),
        "model_prob": _nullable_num(event.get("model_prob")),
        "delta_prob": _nullable_num(event.get("delta_prob")),
        "market_mid": _nullable_num(event.get("market_mid")),
        "edge": _nullable_num(event.get("edge")),
        "alpha_candidate": 1 if event.get("alpha_candidate") else 0,
        "raw_json": dump_json(event),
        "created_at": now,
        "updated_at": now,
    }
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO model_reprice_events (
                event_key, city_key, target_date, market_id, bucket_key,
                triggered_at, model_source, previous_model_prob, model_prob,
                delta_prob, market_mid, edge, alpha_candidate, raw_json,
                created_at, updated_at
            ) VALUES (
                :event_key, :city_key, :target_date, :market_id, :bucket_key,
                :triggered_at, :model_source, :previous_model_prob, :model_prob,
                :delta_prob, :market_mid, :edge, :alpha_candidate, :raw_json,
                :created_at, :updated_at
            )
            ON CONFLICT(event_key) DO UPDATE SET
                previous_model_prob=excluded.previous_model_prob,
                model_prob=excluded.model_prob,
                delta_prob=excluded.delta_prob,
                market_mid=excluded.market_mid,
                edge=excluded.edge,
                alpha_candidate=excluded.alpha_candidate,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            row,
        )
        found = conn.execute("SELECT id FROM model_reprice_events WHERE event_key = ?", (event_key,)).fetchone()
        return int(found["id"]) if found else 0


def list_truth_delta_audit(
    city: str | None = None,
    *,
    limit: int = 500,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    init_v3_db(path)
    clauses: list[str] = []
    params: list[Any] = []
    if city:
        clauses.append("(LOWER(city) = LOWER(?) OR LOWER(icao) = LOWER(?))")
        params.extend([city, city])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit or 500), 2000)))
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM truth_delta_audit
                {where}
                ORDER BY date_local DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        ]
    for row in rows:
        row["raw"] = _loads_obj(row.get("raw_json"))
    return rows


def truth_delta_audit_summary(
    city: str | None = None,
    *,
    limit: int = 500,
    path: Path | None = None,
) -> dict[str, Any]:
    rows = list_truth_delta_audit(city, limit=limit, path=path)
    by_city: dict[str, dict[str, Any]] = {}
    deltas: list[float] = []
    hko_deltas: list[float] = []
    for row in rows:
        city_key = str(row.get("city") or row.get("icao") or "unknown").lower()
        entry = by_city.setdefault(
            city_key,
            {
                "city": row.get("city"),
                "icao": row.get("icao"),
                "count": 0,
                "latest_date": None,
                "delta_wu_minus_iem_values": [],
                "delta_hko_minus_iem_values": [],
            },
        )
        entry["count"] += 1
        if not entry["latest_date"] or str(row.get("date_local") or "") > str(entry["latest_date"] or ""):
            entry["latest_date"] = row.get("date_local")
        delta = _nullable_num(row.get("delta_wu_minus_iem"))
        if delta is not None:
            rounded = round(float(delta), 2)
            entry["delta_wu_minus_iem_values"].append(rounded)
            deltas.append(rounded)
        hko_delta = _nullable_num(row.get("delta_hko_minus_iem"))
        if hko_delta is not None:
            rounded_hko = round(float(hko_delta), 2)
            entry["delta_hko_minus_iem_values"].append(rounded_hko)
            hko_deltas.append(rounded_hko)

    histogram: dict[str, int] = {}
    for delta in deltas:
        bucket = round(delta * 2) / 2
        label = f"{bucket:+.1f}C"
        histogram[label] = histogram.get(label, 0) + 1

    hko_histogram: dict[str, int] = {}
    for delta in hko_deltas:
        bucket = round(delta * 2) / 2
        label = f"{bucket:+.1f}C"
        hko_histogram[label] = hko_histogram.get(label, 0) + 1

    return {
        "ok": True,
        "count": len(rows),
        "city_filter": city or "",
        "rows": rows,
        "by_city": list(by_city.values()),
        "histogram": [{"bucket": key, "count": value} for key, value in sorted(histogram.items())],
        "hko_vs_iem_histogram": [{"bucket": key, "count": value} for key, value in sorted(hko_histogram.items())],
    }


def list_model_reprice_events(
    city: str | None = None,
    target_date: str | None = None,
    *,
    alpha_only: bool = False,
    limit: int = 200,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    init_v3_db(path)
    clauses: list[str] = []
    params: list[Any] = []
    if city:
        clauses.append("LOWER(city_key) = LOWER(?)")
        params.append(city)
    if target_date:
        clauses.append("target_date = ?")
        params.append(target_date)
    if alpha_only:
        clauses.append("alpha_candidate = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit or 200), 1000)))
    with connect(path) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM model_reprice_events
                {where}
                ORDER BY triggered_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        ]
    for row in rows:
        row["alpha_candidate"] = bool(row.get("alpha_candidate"))
        row["raw"] = _loads_obj(row.get("raw_json"))
    return rows


def model_reprice_event_summary(
    city: str | None = None,
    target_date: str | None = None,
    *,
    alpha_only: bool = False,
    limit: int = 200,
    path: Path | None = None,
) -> dict[str, Any]:
    rows = list_model_reprice_events(
        city,
        target_date,
        alpha_only=alpha_only,
        limit=limit,
        path=path,
    )
    return {
        "ok": True,
        "count": len(rows),
        "alpha_count": sum(1 for row in rows if row.get("alpha_candidate")),
        "city_filter": city or "",
        "target_date_filter": target_date or "",
        "rows": rows,
    }


def latest_event_distribution(market_id: str) -> dict[str, Any] | None:
    init_v3_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT raw_json FROM event_distributions WHERE market_id = ? ORDER BY id DESC LIMIT 1",
            (market_id,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["raw_json"])
    except Exception:
        return None


def latest_signal_decision(signal_id: int) -> dict[str, Any] | None:
    init_v3_db()
    with connect() as conn:
        row = conn.execute("SELECT raw_json FROM signal_decisions WHERE signal_id = ?", (signal_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["raw_json"])
    except Exception:
        return None


def truth_coverage_summary() -> dict[str, Any]:
    init_v3_db()
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM truth_observations ORDER BY target_date DESC").fetchall()]
    by_city_date: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        by_city_date.setdefault(key, []).append(row)
    by_city: dict[str, dict[str, Any]] = {}
    provider_counts: dict[str, int] = {}
    excluded = 0
    for (city, target_date), day_rows in by_city_date.items():
        for row in day_rows:
            provider = str(row.get("provider") or "unknown")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            if not row.get("calibration_eligible"):
                excluded += 1
        eligible_rows = [
            row for row in day_rows
            if row.get("calibration_eligible") and row.get("actual_temp") is not None
        ]
        best_eligible = max(
            eligible_rows,
            key=lambda row: _num(row.get("source_confidence"), 0.0),
            default=None,
        )
        display_row = best_eligible or max(
            day_rows,
            key=lambda row: (
                1 if row.get("actual_temp") is not None else 0,
                _num(row.get("source_confidence"), 0.0),
            ),
        )
        item = by_city.setdefault(
            city,
            {
                "city": city,
                "city_name": display_row.get("city_name") or city,
                "station_id": display_row.get("station_id") or "",
                "total_observations": 0,
                "eligible_observations": 0,
                "open_meteo_fallbacks": 0,
                "legacy_unknown": 0,
                "latest_provider": "",
                "latest_date": "",
                "latest_confidence": 0.0,
            },
        )
        item["total_observations"] += 1
        if best_eligible:
            item["eligible_observations"] += 1
        if any(row.get("provider") == "open_meteo_archive" for row in day_rows):
            item["open_meteo_fallbacks"] += 1
        if any(row.get("provider") == "legacy_unknown" for row in day_rows):
            item["legacy_unknown"] += 1
        if not item["latest_date"] or target_date > item["latest_date"]:
            item["latest_date"] = target_date
            item["latest_provider"] = display_row.get("provider") or ""
            item["latest_confidence"] = _num(display_row.get("source_confidence"), 0.0)
            item["station_id"] = display_row.get("station_id") or item["station_id"]
    cities = sorted(by_city.values(), key=lambda row: (row["eligible_observations"], row["total_observations"]), reverse=True)
    total = sum(row["total_observations"] for row in cities)
    eligible = sum(row["eligible_observations"] for row in cities)
    fallbacks = sum(row["open_meteo_fallbacks"] for row in cities)
    legacy = sum(row["legacy_unknown"] for row in cities)
    return {
        "total_observations": total,
        "eligible_observations": eligible,
        "coverage_rate": round((eligible / total) if total else 0.0, 4),
        "open_meteo_fallbacks": fallbacks,
        "open_meteo_fallback_rate": round((fallbacks / total) if total else 0.0, 4),
        "legacy_unknown": legacy,
        "excluded_observations": excluded,
        "provider_counts": provider_counts,
        "cities": cities,
    }


def insert_order(table: str, order: dict[str, Any]) -> int:
    if table not in {"paper_orders", "live_orders"}:
        raise ValueError("invalid order table")
    init_v3_db()
    now = utc_now()
    live_cols = ", dry_run, clob_order_id" if table == "live_orders" else ""
    live_vals = ", :dry_run, :clob_order_id" if table == "live_orders" else ""
    with connect() as conn:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {table} (
                signal_id, idempotency_key, market_id, yes_token_id, side,
                limit_price, amount, shares, status, failure_reason, raw_json,
                created_at, updated_at{live_cols}
            ) VALUES (
                :signal_id, :idempotency_key, :market_id, :yes_token_id, :side,
                :limit_price, :amount, :shares, :status, :failure_reason, :raw_json,
                :created_at, :updated_at{live_vals}
            )
            """,
            {
                "signal_id": order.get("signal_id"),
                "idempotency_key": order.get("idempotency_key"),
                "market_id": order.get("market_id"),
                "yes_token_id": order.get("yes_token_id"),
                "side": order.get("side", "BUY"),
                "limit_price": _num(order.get("limit_price"), 0.0),
                "amount": _num(order.get("amount"), 0.0),
                "shares": _num(order.get("shares"), 0.0),
                "status": order.get("status", "created"),
                "failure_reason": order.get("failure_reason"),
                "raw_json": dump_json(order),
                "created_at": now,
                "updated_at": now,
                "dry_run": 1 if order.get("dry_run", True) else 0,
                "clob_order_id": order.get("clob_order_id"),
            },
        )
        row = conn.execute(f"SELECT id FROM {table} WHERE idempotency_key = ?", (order.get("idempotency_key"),)).fetchone()
        return int(row["id"]) if row else 0


def _paper_order_row(order: dict[str, Any], now: str) -> dict[str, Any]:
    return {
        "decision_id": str(order.get("decision_id") or ""),
        "signal_id": order.get("signal_id"),
        "idempotency_key": str(order.get("idempotency_key") or _stable_key("paper_order", now)),
        "market_id": str(order.get("market_id") or ""),
        "yes_token_id": str(order.get("yes_token_id") or order.get("token_id") or ""),
        "bucket_key": str(order.get("bucket_key") or ""),
        "strategy_name": str(order.get("strategy_name") or ""),
        "ladder_group_id": str(order.get("ladder_group_id") or ""),
        "strategy_revision_id": str(order.get("strategy_revision_id") or ""),
        "strategy_params_hash": str(order.get("strategy_params_hash") or ""),
        "strategy_params_snapshot_json": dump_json(order.get("strategy_params_snapshot", {})),
        "sizing_snapshot_json": dump_json(order.get("sizing_snapshot", {})),
        "execution_quote_json": dump_json(order.get("execution_quote", {})),
        "cap_reasons_json": dump_json(order.get("cap_reasons", [])),
        "city_key": str(order.get("city_key") or order.get("city") or ""),
        "target_date": str(order.get("target_date") or ""),
        "event_url": str(order.get("event_url") or ""),
        "side": str(order.get("side") or "BUY"),
        "limit_price": _nullable_num(order.get("limit_price")),
        "requested_amount": _nullable_num(order.get("requested_amount", order.get("amount"))),
        "amount": _nullable_num(order.get("amount", order.get("filled_amount"))),
        "shares": _nullable_num(order.get("shares", order.get("filled_shares"))),
        "filled_amount": _nullable_num(order.get("filled_amount")),
        "filled_shares": _nullable_num(order.get("filled_shares")),
        "unfilled_amount": _nullable_num(order.get("unfilled_amount")),
        "average_fill_price": _nullable_num(order.get("average_fill_price")),
        "mark_price": _nullable_num(order.get("mark_price")),
        "unrealized_pnl": _nullable_num(order.get("unrealized_pnl")),
        "realized_pnl": _nullable_num(order.get("realized_pnl")),
        "status": str(order.get("status") or "created"),
        "lifecycle_status": str(order.get("lifecycle_status") or order.get("status") or "created"),
        "fill_status": str(order.get("fill_status") or ""),
        "order_version": str(order.get("order_version") or ""),
        "model_probability": _nullable_num(order.get("model_probability")),
        "market_probability": _nullable_num(order.get("market_probability")),
        "edge": _nullable_num(order.get("edge")),
        "gate_status": str(order.get("gate_status") or ""),
        "failure_reason": order.get("failure_reason"),
        "risk_reasons_json": dump_json(order.get("risk_reasons", order.get("risk_reasons_json", []))),
        "orderbook_snapshot_json": dump_json(order.get("orderbook_snapshot", order.get("orderbook_snapshot_json", {}))),
        "evidence_links_json": dump_json(order.get("evidence_links", order.get("evidence_links_json", {}))),
        "raw_json": dump_json(order),
        "opened_at": str(order.get("opened_at") or ""),
        "closed_at": str(order.get("closed_at") or ""),
        "cohort_run_id": str(order.get("cohort_run_id") or ""),
        "created_at": str(order.get("created_at") or now),
        "updated_at": now,
    }


def _upsert_paper_order_conn(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    conn.execute(
        """
            INSERT INTO paper_orders (
                decision_id, signal_id, idempotency_key, market_id, yes_token_id,
                bucket_key, strategy_name, ladder_group_id, strategy_revision_id,
                strategy_params_hash, strategy_params_snapshot_json, sizing_snapshot_json,
                execution_quote_json, cap_reasons_json, city_key, target_date,
                event_url, side, limit_price,
                requested_amount, amount, shares, filled_amount, filled_shares,
                unfilled_amount, average_fill_price, mark_price, unrealized_pnl,
                realized_pnl, status, lifecycle_status, fill_status, order_version,
                model_probability, market_probability, edge, gate_status,
                failure_reason, risk_reasons_json, orderbook_snapshot_json,
                evidence_links_json, raw_json, opened_at, closed_at, cohort_run_id, created_at,
                updated_at
            ) VALUES (
                :decision_id, :signal_id, :idempotency_key, :market_id, :yes_token_id,
                :bucket_key, :strategy_name, :ladder_group_id, :strategy_revision_id,
                :strategy_params_hash, :strategy_params_snapshot_json, :sizing_snapshot_json,
                :execution_quote_json, :cap_reasons_json, :city_key, :target_date,
                :event_url, :side, :limit_price,
                :requested_amount, :amount, :shares, :filled_amount, :filled_shares,
                :unfilled_amount, :average_fill_price, :mark_price, :unrealized_pnl,
                :realized_pnl, :status, :lifecycle_status, :fill_status, :order_version,
                :model_probability, :market_probability, :edge, :gate_status,
                :failure_reason, :risk_reasons_json, :orderbook_snapshot_json,
                :evidence_links_json, :raw_json, :opened_at, :closed_at, :cohort_run_id, :created_at,
                :updated_at
            )
            ON CONFLICT(idempotency_key) DO UPDATE SET
                status=paper_orders.status,
                lifecycle_status=paper_orders.lifecycle_status,
                fill_status=paper_orders.fill_status,
                raw_json=paper_orders.raw_json,
                updated_at=paper_orders.updated_at
            """,
            row,
        )
    found = conn.execute(
        "SELECT id FROM paper_orders WHERE idempotency_key = ?",
        (row["idempotency_key"],),
    ).fetchone()
    return int(found["id"]) if found else 0


def upsert_paper_order_record(order: dict[str, Any], path: Path | None = None) -> int:
    init_v3_db(path)
    row = _paper_order_row(order, utc_now())
    with connect(path) as conn:
        return _upsert_paper_order_conn(conn, row)


def _fill_row(fill: dict[str, Any], now: str) -> dict[str, Any]:
    return {
        "idempotency_key": str(fill.get("idempotency_key") or _stable_key("fill", now)),
        "order_id": fill.get("order_id"),
        "order_type": str(fill.get("order_type") or "paper"),
        "decision_id": str(fill.get("decision_id") or ""),
        "market_id": str(fill.get("market_id") or ""),
        "yes_token_id": str(fill.get("yes_token_id") or ""),
        "fill_status": str(fill.get("fill_status") or "filled"),
        "price": _nullable_num(fill.get("price")),
        "shares": _nullable_num(fill.get("shares")),
        "amount": _nullable_num(fill.get("amount")),
        "source": str(fill.get("source") or ""),
        "raw_json": dump_json(fill),
        "created_at": str(fill.get("created_at") or now),
    }


def _insert_fill_conn(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    conn.execute(
        """
            INSERT INTO fills (
                idempotency_key, order_id, order_type, decision_id, market_id,
                yes_token_id, fill_status, price, shares, amount, source, raw_json,
                created_at
            ) VALUES (
                :idempotency_key, :order_id, :order_type, :decision_id, :market_id,
                :yes_token_id, :fill_status, :price, :shares, :amount, :source,
                :raw_json, :created_at
            )
            ON CONFLICT(idempotency_key) DO UPDATE SET
                order_id=fills.order_id,
                fill_status=fills.fill_status,
                raw_json=fills.raw_json
            """,
            row,
        )
    found = conn.execute(
        "SELECT id FROM fills WHERE idempotency_key = ?",
        (row["idempotency_key"],),
    ).fetchone()
    return int(found["id"]) if found else 0


def insert_fill_record(fill: dict[str, Any], path: Path | None = None) -> int:
    init_v3_db(path)
    row = _fill_row(fill, utc_now())
    with connect(path) as conn:
        return _insert_fill_conn(conn, row)


def persist_paper_order_fill_group(
    order_fill_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    path: Path | None = None,
) -> list[dict[str, int]]:
    """Persist a paper ladder as one transaction; any failed leg rolls back all legs."""
    init_v3_db(path)
    now = utc_now()
    stored: list[dict[str, int]] = []
    with connect(path) as conn:
        for order, fill in order_fill_pairs:
            order_id = _upsert_paper_order_conn(conn, _paper_order_row(order, now))
            fill_id = _insert_fill_conn(conn, _fill_row({**fill, "order_id": order_id}, now))
            stored.append({"order_id": order_id, "fill_id": fill_id})
    return stored


def get_paper_order_by_idempotency_key(idempotency_key: str, path: Path | None = None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    init_v3_db(path)
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM paper_orders WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    return _decode_paper_order(dict(row)) if row else None


def open_paper_order_for_token(yes_token_id: str, path: Path | None = None) -> dict[str, Any] | None:
    if not yes_token_id:
        return None
    init_v3_db(path)
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM paper_orders
            WHERE yes_token_id = ?
              AND COALESCE(lifecycle_status, status) IN ('open', 'paper_filled', 'paper_partial')
            ORDER BY id DESC
            LIMIT 1
            """,
            (yes_token_id,),
        ).fetchone()
    return _decode_paper_order(dict(row)) if row else None


def list_paper_orders(
    city_key: str | None = None,
    target_date: str | None = None,
    decision_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    init_v3_db(path)
    where: list[str] = []
    params: list[Any] = []
    if city_key:
        where.append("city_key = ?")
        params.append(city_key)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    if decision_id:
        where.append("decision_id = ?")
        params.append(decision_id)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    bounded_limit = max(1, min(int(limit or 100), 1000))
    with connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM paper_orders
            {clause}
            ORDER BY datetime(COALESCE(opened_at, updated_at, created_at)) DESC, id DESC
            LIMIT ?
            """,
            (*params, bounded_limit),
        ).fetchall()
    return [_decode_paper_order(dict(row)) for row in rows]


def paper_execution_summary(
    city_key: str | None = None,
    target_date: str | None = None,
    *,
    limit: int = 100,
    path: Path | None = None,
) -> dict[str, Any]:
    orders = list_paper_orders(city_key=city_key, target_date=target_date, limit=limit, path=path)
    settlements = list_settlements(
        city_key=city_key,
        target_date=target_date,
        limit=limit,
        path=path,
    )
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    total_filled = 0.0
    total_unrealized = 0.0
    open_orders = 0
    for order in orders:
        status = str(order.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        for reason in order.get("risk_reasons") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
        total_filled += _num(order.get("filled_amount"), 0.0)
        total_unrealized += _num(order.get("unrealized_pnl"), 0.0)
        if str(order.get("lifecycle_status") or "") == "open":
            open_orders += 1
    resolved = [row for row in settlements if row.get("settlement_status") == "resolved"]
    provisional = [
        row
        for row in settlements
        if str(row.get("settlement_status") or "").startswith("provisional")
    ]
    wins = sum(1 for row in resolved if row.get("result") == "win")
    brier_values = [
        float(row["brier_score"])
        for row in resolved
        if row.get("brier_score") is not None
    ]
    market_brier_values = [
        float(row["market_brier_score"])
        for row in resolved
        if row.get("market_brier_score") is not None
    ]
    return {
        "ok": True,
        "execution_version": "paper-execution-v2",
        "city_key": city_key or "",
        "target_date": target_date or "",
        "count": len(orders),
        "open_orders": open_orders,
        "filled_amount": round(total_filled, 4),
        "unrealized_pnl": round(total_unrealized, 4),
        "resolved_orders": len(resolved),
        "provisional_orders": len(provisional),
        "wins": wins,
        "losses": len(resolved) - wins,
        "win_rate": wins / len(resolved) if resolved else None,
        "realized_pnl": round(sum(_num(row.get("pnl"), 0.0) for row in resolved), 4),
        "brier_score": sum(brier_values) / len(brier_values) if brier_values else None,
        "market_brier_score": (
            sum(market_brier_values) / len(market_brier_values)
            if market_brier_values
            else None
        ),
        "status_counts": status_counts,
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "settlements": settlements,
        "orders": orders,
    }


def apply_paper_settlement_record(
    order_id: int,
    settlement: dict[str, Any],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Upsert one paper settlement and atomically close authoritative outcomes."""
    init_v3_db(path)
    now = utc_now()
    settlement_status = str(settlement.get("settlement_status") or "provisional_truth")
    authoritative = settlement_status == "resolved"
    with connect(path) as conn:
        order_row = conn.execute("SELECT * FROM paper_orders WHERE id = ?", (int(order_id),)).fetchone()
        if not order_row:
            return {"ok": False, "reason": "paper_order_not_found", "paper_order_id": int(order_id)}
        order = _decode_paper_order(dict(order_row))
        settlement_key = str(settlement.get("settlement_key") or f"paper_settlement:{int(order_id)}")
        outcome_yes = 1 if bool(settlement.get("outcome_yes")) else 0
        result = str(settlement.get("result") or ("win" if outcome_yes else "loss"))
        raw_payload = {**settlement, "paper_order": order}
        row = {
            "settlement_key": settlement_key,
            "paper_order_id": int(order_id),
            "decision_id": str(order.get("decision_id") or settlement.get("decision_id") or ""),
            "market_id": str(order.get("market_id") or settlement.get("market_id") or ""),
            "yes_token_id": str(order.get("yes_token_id") or settlement.get("yes_token_id") or ""),
            "city_key": str(order.get("city_key") or settlement.get("city_key") or ""),
            "target_date": str(order.get("target_date") or settlement.get("target_date") or ""),
            "result": result,
            "outcome_yes": outcome_yes,
            "settlement_status": settlement_status,
            "settlement_source": str(settlement.get("settlement_source") or ""),
            "actual_temp": _nullable_num(settlement.get("actual_temp")),
            "actual_provider": str(settlement.get("actual_provider") or ""),
            "actual_station": str(settlement.get("actual_station") or ""),
            "actual_confidence": _nullable_num(settlement.get("actual_confidence")),
            "calibration_eligible": 1 if settlement.get("calibration_eligible") else 0,
            "payout": _nullable_num(settlement.get("payout")) if authoritative else None,
            "pnl": _nullable_num(settlement.get("pnl")) if authoritative else None,
            "brier_score": _nullable_num(settlement.get("brier_score")),
            "market_brier_score": _nullable_num(settlement.get("market_brier_score")),
            "settled_at": str(settlement.get("settled_at") or now) if authoritative else "",
            "raw_json": dump_json(raw_payload),
            "created_at": now,
            "updated_at": now,
        }
        conn.execute(
            """
            INSERT INTO settlements (
                settlement_key, paper_order_id, decision_id, market_id, yes_token_id,
                city_key, target_date, result, outcome_yes, settlement_status,
                settlement_source, actual_temp, actual_provider, actual_station,
                actual_confidence, calibration_eligible, payout, pnl, brier_score,
                market_brier_score, settled_at, raw_json, created_at, updated_at
            ) VALUES (
                :settlement_key, :paper_order_id, :decision_id, :market_id, :yes_token_id,
                :city_key, :target_date, :result, :outcome_yes, :settlement_status,
                :settlement_source, :actual_temp, :actual_provider, :actual_station,
                :actual_confidence, :calibration_eligible, :payout, :pnl, :brier_score,
                :market_brier_score, :settled_at, :raw_json, :created_at, :updated_at
            )
            ON CONFLICT(settlement_key) DO UPDATE SET
                result=excluded.result,
                outcome_yes=excluded.outcome_yes,
                settlement_status=excluded.settlement_status,
                settlement_source=excluded.settlement_source,
                actual_temp=excluded.actual_temp,
                actual_provider=excluded.actual_provider,
                actual_station=excluded.actual_station,
                actual_confidence=excluded.actual_confidence,
                calibration_eligible=excluded.calibration_eligible,
                payout=excluded.payout,
                pnl=excluded.pnl,
                brier_score=excluded.brier_score,
                market_brier_score=excluded.market_brier_score,
                settled_at=excluded.settled_at,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            row,
        )
        if authoritative:
            order_raw = dict(order.get("raw") or {})
            order_raw["settlement"] = {key: value for key, value in row.items() if key != "raw_json"}
            conn.execute(
                """
                UPDATE paper_orders
                SET status = ?, lifecycle_status = 'settled', mark_price = ?,
                    unrealized_pnl = 0, realized_pnl = ?, closed_at = ?,
                    raw_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "paper_won" if outcome_yes else "paper_lost",
                    float(outcome_yes),
                    row["pnl"],
                    row["settled_at"],
                    dump_json(order_raw),
                    now,
                    int(order_id),
                ),
            )
        stored = conn.execute("SELECT * FROM settlements WHERE settlement_key = ?", (settlement_key,)).fetchone()
    return {"ok": True, "settlement": _decode_settlement(dict(stored)) if stored else row}


def list_settlements(
    *,
    city_key: str | None = None,
    target_date: str | None = None,
    status: str | None = None,
    limit: int = 200,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    init_v3_db(path)
    where: list[str] = []
    params: list[Any] = []
    if city_key:
        where.append("city_key = ?")
        params.append(city_key)
    if target_date:
        where.append("target_date = ?")
        params.append(target_date)
    if status:
        where.append("settlement_status = ?")
        params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM settlements {clause} ORDER BY id DESC LIMIT ?",
            (*params, max(1, min(int(limit or 200), 2000))),
        ).fetchall()
    return [_decode_settlement(dict(row)) for row in rows]


def _decode_paper_order(row: dict[str, Any]) -> dict[str, Any]:
    row["risk_reasons"] = _loads_list(row.get("risk_reasons_json"))
    row["orderbook_snapshot"] = _loads_obj(row.get("orderbook_snapshot_json"))
    row["evidence_links"] = _loads_obj(row.get("evidence_links_json"))
    row["strategy_params_snapshot"] = _loads_obj(row.get("strategy_params_snapshot_json"))
    row["sizing_snapshot"] = _loads_obj(row.get("sizing_snapshot_json"))
    row["execution_quote"] = _loads_obj(row.get("execution_quote_json"))
    row["cap_reasons"] = _loads_list(row.get("cap_reasons_json"))
    row["raw"] = _loads_obj(row.get("raw_json"))
    return row


def _decode_settlement(row: dict[str, Any]) -> dict[str, Any]:
    row["raw"] = _loads_obj(row.get("raw_json"))
    return row


def log_risk(event_type: str, message: str, severity: str = "warning", payload: dict[str, Any] | None = None) -> None:
    init_v3_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO risk_events (event_type, severity, message, raw_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (event_type, severity, message, dump_json(payload), utc_now()),
        )


def log_notification(channel: str, event_type: str, status: str, message: str, payload: dict[str, Any] | None = None) -> None:
    init_v3_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO notifications (channel, event_type, status, message, raw_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (channel, event_type, status, message, dump_json(payload), utc_now()),
        )


def log_data_fetch(
    *,
    source: str,
    stage: str,
    status: str,
    message: str = "",
    duration_ms: float | None = None,
    city: str = "",
    target_date: str = "",
    details: dict[str, Any] | None = None,
    started_at: str = "",
    finished_at: str = "",
    log_key: str = "",
    path: Path | None = None,
) -> int:
    init_v3_db(path)
    now = utc_now()
    clean_source = str(source or "unknown")
    clean_stage = str(stage or clean_source)
    clean_status = str(status or "INFO").upper()
    key = log_key or _stable_key(
        "data_fetch",
        clean_source,
        clean_stage,
        clean_status,
        city,
        target_date,
        started_at,
        finished_at or now,
        message,
    )
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO data_fetch_logs (
                log_key, source, stage, status, duration_ms, city, target_date,
                message, details_json, started_at, finished_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(log_key) DO UPDATE SET
                source=excluded.source,
                stage=excluded.stage,
                status=excluded.status,
                duration_ms=excluded.duration_ms,
                city=excluded.city,
                target_date=excluded.target_date,
                message=excluded.message,
                details_json=excluded.details_json,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at
            """,
            (
                key,
                clean_source,
                clean_stage,
                clean_status,
                _nullable_num(duration_ms),
                city,
                target_date,
                message,
                dump_json(details or {}),
                started_at,
                finished_at,
                now,
            ),
        )
        row = conn.execute("SELECT id FROM data_fetch_logs WHERE log_key = ?", (key,)).fetchone()
        return int(row["id"]) if row else 0


def list_data_fetch_logs(limit: int = 100) -> list[dict[str, Any]]:
    init_v3_db()
    bounded = max(1, min(int(limit or 100), 500))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM data_fetch_logs
            ORDER BY datetime(COALESCE(finished_at, created_at)) DESC, id DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()
        return [dict(row) for row in rows]


def dashboard_summary() -> dict[str, Any]:
    init_v3_db()
    with connect() as conn:
        def count(sql: str, args: tuple[Any, ...] = ()) -> int:
            return int(conn.execute(sql, args).fetchone()[0])

        return {
            "signals": count("SELECT COUNT(*) FROM signals"),
            "ai_reviews": count("SELECT COUNT(*) FROM ai_reviews"),
            "paper_orders": count("SELECT COUNT(*) FROM paper_orders"),
            "live_orders": count("SELECT COUNT(*) FROM live_orders"),
            "live_open_orders": count("SELECT COUNT(*) FROM live_orders WHERE status IN ('dry_run', 'submitted', 'open')"),
            "stations": count("SELECT COUNT(*) FROM stations"),
            "risk_events": count("SELECT COUNT(*) FROM risk_events"),
            "notifications": count("SELECT COUNT(*) FROM notifications"),
            "metar_reports": count("SELECT COUNT(*) FROM metar_reports"),
            "mesonet_observations": count("SELECT COUNT(*) FROM mesonet_observations"),
            "hourly_consensus": count("SELECT COUNT(*) FROM hourly_consensus"),
            "data_fetch_logs": count("SELECT COUNT(*) FROM data_fetch_logs"),
            "latest_risk_events": [dict(r) for r in conn.execute("SELECT * FROM risk_events ORDER BY id DESC LIMIT 10").fetchall()],
            "latest_live_orders": [dict(r) for r in conn.execute("SELECT * FROM live_orders ORDER BY id DESC LIMIT 10").fetchall()],
            "latest_paper_orders": [dict(r) for r in conn.execute("SELECT * FROM paper_orders ORDER BY id DESC LIMIT 10").fetchall()],
            "latest_metar_reports": [
                dict(r)
                for r in conn.execute("SELECT * FROM metar_reports ORDER BY report_time DESC, id DESC LIMIT 10").fetchall()
            ],
            "latest_mesonet_observations": [
                dict(r)
                for r in conn.execute("SELECT * FROM mesonet_observations ORDER BY observed_at DESC, id DESC LIMIT 10").fetchall()
            ],
            "latest_hourly_consensus": [
                dict(r)
                for r in conn.execute("SELECT * FROM hourly_consensus ORDER BY target_date DESC, local_hour DESC, id DESC LIMIT 24").fetchall()
            ],
            "latest_data_fetch_logs": list_data_fetch_logs(10),
        }


def _num(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _nullable_num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None
