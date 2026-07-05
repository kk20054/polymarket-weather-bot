# Layer 0-7 Verification Summary

Generated: 2026-07-05 Asia/Shanghai

Scope: read-only verification from local SQLite DB and local dashboard payload builder. No scheduler, Firecrawl, live trading, config mutation, or external network request was started.

## Reports

- `layer1_registry.md`
- `layer2_observations.md`
- `layer3_4_forecast.md`
- `layer5_market_buckets.md`
- `layer6_gates.md`
- `layer7_dashboard.md`

## Layer Conclusions

| Layer | Can enter next layer | P0 found | Main blockers |
| --- | --- | --- | --- |
| Layer 1 registry/settlement | yes | no | All 21 station rows still have `verification_status=provisional`; several have `settlement_rule_verified_at`, but status was not promoted. Live gate must remain blocked for unverified cities. |
| Layer 2 observations | no | no | Last-24h METAR rows, latest ages, and missing UTC hour intervals are now indexed per city in `layer2_observations.md`; mesonet masquerade check is clean, suspect rows = 0. |
| Layer 3-4 forecast/consensus | no | no | Stale forecast cities are named with latest forecast age; under-24 consensus city/date groups are listed; no timestamp alignment suspects after timezone-aware validation. |
| Layer 5 market buckets | yes | no | Unmatched buckets are grouped by city/date with up to 5 examples per group; metadata gaps remain mostly older buckets and no all-table metadata collapse was found. |
| Layer 6 decision gates | yes | no | Gate Top10 now includes sample `signal_decision_id` references; paper-allowed/live-blocked samples remain expected while live is locked. |
| Layer 7 dashboard contract | yes | no | `verify_layer7_dashboard.py` fell back to `dashboard_server.build_dashboard_payload()` when the HTTP API was unavailable and found no P0 contract issue. |

## Cross-Layer Findings

- No P0 data-destruction or live-gate inversion was proven by this verification pass.
- The largest true blocker is data freshness: Layer 2 observations and Layer 3 forecasts are stale, so higher layers can exist structurally but should not be trusted for production validation today.
- The registry table has `settlement_rule_verified_at` on several cities but still reports `verification_status=provisional`; this should be investigated before any live-gate discussion.
- Layer 5 and Layer 6 are structurally usable for analysis, but they inherit freshness/truth/sample blockers from lower layers.
- Layer 7 no longer requires a manually started backend for field-level validation; it falls back to direct payload construction.

## Recommended Next Action

Run a controlled freshness pass before strategy/paper validation:

```powershell
cd C:\Users\Administrator\Documents\polymarket\weatherbot
# Start the backend only when doing browser/UI validation.
# For contract-only validation, tools\verify_layer7_dashboard.py can import dashboard_server directly.
```

Then rerun:

```powershell
.\.venv\Scripts\python.exe tools\verify_layer2_observations.py --out-dir audits\layer-verify-2026-07-05
.\.venv\Scripts\python.exe tools\verify_layer3_4_forecast.py --out-dir audits\layer-verify-2026-07-05
.\.venv\Scripts\python.exe tools\verify_layer7_dashboard.py --out-dir audits\layer-verify-2026-07-05
```

Do not relax gates to make recommendations appear. Fix data freshness and verification status first.
