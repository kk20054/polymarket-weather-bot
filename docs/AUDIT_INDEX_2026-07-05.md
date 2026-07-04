# Audit Index 2026-07-05

This index is tracked. The audit report bodies are local evidence under `audits/` and were intentionally not staged.

## Reports

- `audits/docs-audit-2026-07-05/README.md`
  - Scope: root/docs markdown inventory, freshness, duplication, and keep/trim/merge/archive recommendations.
  - Key finding: `docs/CURRENT_STATE.md` should be trimmed further to one current layer, 3 blockers, and 1 next action; `PROJECT_PROGRESS_CN.md` is over the 15 KB target.

- `audits/scripts-audit-2026-07-05/README.md`
  - Scope: root `*.py` and `tools/*.py`.
  - Key finding: `bot_v1.py` is the clearest duplicate/dead-code candidate; `bot_v2.py`, `weatherbet.py`, and `dashboard_db.py` are legacy-dependent and should not be deleted yet.

- `audits/schema-config-audit-2026-07-05/README.md`
  - Scope: current SQLite schema, AGENTS Build Order Layer 1-6 alignment, and `V3Config` field usage.
  - Key finding: Layer 1-6 tables align with AGENTS; config orphan candidates are `truth_provider_mode`, `live_max_open_positions`, `live_daily_loss_limit`, and `live_max_drawdown_pct`.

## Verification

- `git diff --check`: passed.
- `.venv\Scripts\python.exe -m unittest discover -s tests`: failed, 196 tests run, 3 stable failures.
  - `tests.test_deb_gaussian.DebGaussianTests.test_gaussian_bucket_integral_matches_one_sigma_interval`: expected old one-sigma probability `0.682689`, actual current bucket logic returns `0.8185946141203637`.
  - `tests.test_v3_core.V3CoreTests.test_dashboard_recommendations_use_fresh_verified_signal_decisions`: expected recommendation count `1`, actual `0`.
  - `tests.test_v3_core.V3CoreTests.test_dashboard_recommendations_keep_spread_only_watch_candidate`: expected recommendation count `1`, actual `0`.

No dashboard server, scheduler, Firecrawl, or external network request was started for this audit.
