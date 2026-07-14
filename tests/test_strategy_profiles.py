from tests import ensure_test_environment

ensure_test_environment()

import sqlite3
import unittest
from pathlib import Path

from weatherbot_v3.db import connect, init_v3_db, list_signal_decisions, upsert_signal_decision_record
from weatherbot_v3.strategy_profiles import (
    DEFAULT_PARAMETERS,
    activate_strategy_profile,
    create_strategy_profile_revision,
    get_active_strategy_profile,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_DB_DIR = ROOT / ".tmp-tests"


def test_db_path(name: str) -> Path:
    TEST_DB_DIR.mkdir(exist_ok=True)
    path = TEST_DB_DIR / f"{name}.db"
    path.unlink(missing_ok=True)
    return path


class StrategyProfileTests(unittest.TestCase):
    def test_canonical_hash_is_stable_and_parameter_change_creates_revision(self):
        path = test_db_path("strategy_profile_hash")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        first = create_strategy_profile_revision(DEFAULT_PARAMETERS, path=path)
        reordered = {key: DEFAULT_PARAMETERS[key] for key in reversed(DEFAULT_PARAMETERS)}
        same = create_strategy_profile_revision(reordered, path=path)
        changed = create_strategy_profile_revision(
            {"strategies": {"single_bucket_ev": {"min_edge": 0.06}}},
            path=path,
        )

        self.assertEqual(first["revision_id"], same["revision_id"])
        self.assertNotEqual(first["revision_id"], changed["revision_id"])
        self.assertEqual(changed["revision_no"], first["revision_no"] + 1)

    def test_revisions_and_activation_events_are_immutable(self):
        path = test_db_path("strategy_profile_immutable")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        revision = create_strategy_profile_revision(DEFAULT_PARAMETERS, path=path)
        activate_strategy_profile(revision["revision_id"], scope="paper_default", path=path)
        with self.assertRaises(sqlite3.IntegrityError):
            with connect(path) as conn:
                conn.execute(
                    "UPDATE strategy_profile_revisions SET change_note='mutated' WHERE revision_id=?",
                    (revision["revision_id"],),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with connect(path) as conn:
                conn.execute("DELETE FROM strategy_profile_activation_events")

    def test_activation_scopes_are_independent(self):
        path = test_db_path("strategy_profile_scopes")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        init_v3_db(path)
        first = create_strategy_profile_revision(DEFAULT_PARAMETERS, path=path)
        second = create_strategy_profile_revision(
            {"sizing": {"kelly_multiplier": 0.10}},
            path=path,
        )
        activate_strategy_profile(first["revision_id"], scope="signal_generation", path=path)
        activate_strategy_profile(second["revision_id"], scope="paper_default", path=path)

        self.assertEqual(get_active_strategy_profile("signal_generation", path=path)["revision_id"], first["revision_id"])
        self.assertEqual(get_active_strategy_profile("paper_default", path=path)["revision_id"], second["revision_id"])

    def test_signal_decision_persists_profile_and_sizing_context(self):
        path = test_db_path("strategy_profile_decision_snapshot")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        revision = create_strategy_profile_revision(DEFAULT_PARAMETERS, path=path)
        upsert_signal_decision_record({
            "decision_id": "profile-decision",
            "city_key": "chicago",
            "target_date": "2026-07-13",
            "issued_at": "2026-07-13T00:00:00+00:00",
            "strategy_name": "single_bucket_ev",
            "strategy_revision_id": revision["revision_id"],
            "strategy_params_hash": revision["content_sha256"],
            "strategy_params_snapshot": {"revision_id": revision["revision_id"]},
            "sizing_bankroll_usd": 40,
            "sizing_max_per_trade_usd": 2,
            "kelly_multiplier": 0.15,
            "bankroll_fraction_cap": 0.05,
        }, path=path)

        row = list_signal_decisions(decision_id="profile-decision", path=path)[0]
        self.assertEqual(row["strategy_revision_id"], revision["revision_id"])
        self.assertEqual(row["strategy_params_snapshot"]["revision_id"], revision["revision_id"])
        self.assertEqual(row["sizing_bankroll_usd"], 40)


if __name__ == "__main__":
    unittest.main()
