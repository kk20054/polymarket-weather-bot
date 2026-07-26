from __future__ import annotations

from tests import ensure_test_environment

ensure_test_environment()

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbot_v3.db import connect, init_v3_db
from weatherbot_v3.forward_validation import (
    enroll_forward_validation_candidates,
    forward_validation_summary,
    required_sample_size,
    snapshot_forward_validation_anchor_quotes,
)


class ForwardValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "weatherbot.db"
        self.protocol = {
            "protocol_id": "test-forward-v2",
            "status": "frozen",
            "started_at": "2026-07-26T17:30:00+00:00",
            "ask_min": 0.20,
            "ask_max": 0.40,
            "edge_min": 0.08,
            "strategy_name": "core_modal_v1",
            "forecast_algo": "polywx_aligned_deb_v1",
            "target_n": 338,
            "power_effect_clv": 0.03,
            "expected_evaluation_date": "2026-08-12",
            "anchor_kind": "decision_plus_6h_capped_peak_minus_1h",
            "anchor_quote_max_age_seconds": 900,
            "hypothesis_a_edge_min": 0.15,
            "main_cohort": "model_side_candidate",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_preregistered_three_point_clv_effect_requires_338_samples(self):
        self.assertEqual(required_sample_size(0.2216, 0.03), 338)

    def test_model_side_candidate_is_immutable_and_stratified_by_paper_gate(self):
        path = self.db_path
        init_v3_db(path)
        decision_at = datetime(2026, 7, 27, 8, tzinfo=timezone.utc)
        self._insert_station(path)
        self._insert_decision(
            path,
            decision_at=decision_at,
            entry_ask=0.25,
            paper_allowed=False,
            blocked_reason="spread_too_wide",
        )

        first = enroll_forward_validation_candidates(
            path=path,
            protocol=self.protocol,
            as_of=(decision_at + timedelta(minutes=1)).isoformat(),
        )
        self.assertEqual(first["stored"], 1)

        with connect(path) as conn:
            conn.execute(
                """
                UPDATE signal_decisions
                SET decision_time_ask=0.35, paper_allowed=1,
                    blocked_reason_primary='', updated_at=?
                WHERE decision_id='decision-1'
                """,
                ((decision_at + timedelta(minutes=10)).isoformat(),),
            )
            conn.commit()
        second = enroll_forward_validation_candidates(
            path=path,
            protocol=self.protocol,
            as_of=(decision_at + timedelta(minutes=11)).isoformat(),
        )
        self.assertEqual(second["stored"], 0)
        with connect(path) as conn:
            frozen = dict(conn.execute(
                "SELECT * FROM forward_validation_candidates"
            ).fetchone())
        self.assertAlmostEqual(frozen["entry_ask"], 0.25)
        self.assertEqual(frozen["paper_allowed_at_entry"], 0)
        self.assertEqual(frozen["blocked_reason_primary"], "spread_too_wide")
        self.assertEqual(frozen["anchor_at"], "2026-07-27T13:00:00+00:00")

    def test_anchor_quote_uses_last_valid_ask_before_anchor(self):
        path = self.db_path
        init_v3_db(path)
        decision_at = datetime(2026, 7, 27, 8, tzinfo=timezone.utc)
        anchor_at = datetime(2026, 7, 27, 13, tzinfo=timezone.utc)
        self._insert_station(path)
        self._insert_decision(
            path,
            decision_at=decision_at,
            entry_ask=0.25,
            paper_allowed=False,
            blocked_reason="spread_too_wide",
        )
        enroll_forward_validation_candidates(
            path=path,
            protocol=self.protocol,
            as_of=(decision_at + timedelta(minutes=1)).isoformat(),
        )
        with connect(path) as conn:
            for key, captured, ask in (
                ("before", anchor_at - timedelta(minutes=10), 0.30),
                ("after", anchor_at + timedelta(minutes=1), 0.50),
            ):
                conn.execute(
                    """
                    INSERT INTO orderbooks (
                        snapshot_key, market_id, yes_token_id, best_bid, best_ask,
                        snapshot_type, quote_timestamp, created_at
                    ) VALUES (?, 'market-1', 'token-1', ?, ?, 'clob_book', ?, ?)
                    """,
                    (
                        key,
                        ask - 0.01,
                        ask,
                        captured.isoformat(),
                        captured.isoformat(),
                    ),
                )
            conn.commit()

        capture = snapshot_forward_validation_anchor_quotes(
            path=path,
            protocol=self.protocol,
            as_of=(anchor_at + timedelta(minutes=2)).isoformat(),
        )
        self.assertEqual(capture["stored"], 1)
        summary = forward_validation_summary(
            path=path,
            protocol=self.protocol,
            use_cache=False,
        )
        self.assertEqual(summary["progress"]["enrolled_candidates"], 1)
        self.assertEqual(summary["progress"]["samples"], 1)
        self.assertAlmostEqual(summary["clv"]["mean"], 0.05)
        self.assertEqual(summary["strata"]["paper_allowed"]["false"]["enrolled"], 1)
        self.assertEqual(summary["strata"]["paper_allowed"]["true"]["enrolled"], 0)
        self.assertEqual(summary["hypotheses"]["H-A"]["n"], 1)

    def _insert_station(self, path: Path) -> None:
        with connect(path) as conn:
            conn.execute(
                """
                INSERT INTO stations (
                    city_key, city_name, station_id, station_name, timezone, unit,
                    settlement_timezone, updated_at
                ) VALUES (
                    'chicago', 'Chicago', 'KORD', 'O Hare', 'UTC', 'F', 'UTC', ?
                )
                """,
                (datetime.now(timezone.utc).isoformat(),),
            )
            conn.commit()

    def _insert_decision(
        self,
        path: Path,
        *,
        decision_at: datetime,
        entry_ask: float,
        paper_allowed: bool,
        blocked_reason: str,
    ) -> None:
        raw = {
            "decision_time": decision_at.isoformat(),
            "peak_hour": "14:00",
        }
        with connect(path) as conn:
            conn.execute(
                """
                INSERT INTO signal_decisions (
                    decision_id, market_id, bucket_key, token_id, yes_token_id,
                    city_key, target_date, issued_at, model_probability,
                    market_ask, decision_time_ask, quote_age_at_decision_seconds,
                    edge, strategy_name, forecast_algo, paper_allowed,
                    blocked_reason_primary, gate_reasons_json, raw_json, updated_at
                ) VALUES (
                    'decision-1', 'market-1', 'bucket-1', 'token-1', 'token-1',
                    'chicago', '2026-07-27', ?, 0.45, ?, ?, 12.0, 0.20,
                    'core_modal_v1', 'polywx_aligned_deb_v1', ?, ?, ?, ?, ?
                )
                """,
                (
                    (decision_at - timedelta(hours=2)).isoformat(),
                    entry_ask,
                    entry_ask,
                    int(paper_allowed),
                    blocked_reason,
                    json.dumps([blocked_reason] if blocked_reason else []),
                    json.dumps(raw),
                    decision_at.isoformat(),
                ),
            )
            conn.commit()


if __name__ == "__main__":
    unittest.main()
