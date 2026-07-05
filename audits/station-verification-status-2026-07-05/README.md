# Station Verification Status Conflict Audit - 2026-07-05

## Conflict Query

Rows where `settlement_rule_verified_at` is non-empty but `verification_status != 'verified'`:

| city | station_id | settlement_station_id | verification_status | settlement_rule_verified_at | source |
|---|---|---|---|---|---|
| atlanta | KATL | KATL | provisional | 2026-07-04T00:49:31.053183+00:00 | polymarket_rule |
| chicago | KORD | KORD | provisional | 2026-07-04T00:49:26.214481+00:00 | polymarket_rule |
| dallas | KDAL | KDAL | provisional | 2026-07-04T00:49:37.823878+00:00 | polymarket_rule |
| hong-kong | VHHH | VHHH | provisional | 2026-07-04T00:49:23.754146+00:00 | polymarket_rule |
| nyc | KLGA | KLGA | provisional | 2026-07-04T00:49:33.460417+00:00 | polymarket_rule |
| shanghai | ZSPD | ZSPD | provisional | 2026-07-04T00:49:21.398521+00:00 | polymarket_rule |
| tokyo | RJTT | RJTT | provisional | 2026-07-04T00:49:28.697314+00:00 | polymarket_rule |

## Write Path Trace

- `sync_station_registry`: upserts registry defaults. It preserves a non-empty `settlement_rule_verified_at`, but only preserves `verification_status` when the old status is already one of `verified`, `settlement_mismatch`, or `no_active_market`. If a row has a probe timestamp while still `provisional`, the next registry sync can keep the timestamp and reset the status to `provisional`.
- `apply_market_probe_result`: writes probe results into `stations` and is the correct path for `polymarket-market-probe`. For active markets it sets `verification_status` to `verified` when `settlement_station_id == station_id`, or `settlement_mismatch` when they differ, and sets `settlement_rule_verified_at`.
- `polymarket-market-probe`: calls `probe_polymarket_markets(..., apply=True)` through the CLI, and `probe_polymarket_markets` calls `apply_market_probe_result`.
- `set_settlement_contract_verification`: updates `settlement_contracts` and `market_rules` manual verification fields only; it does not write `stations`.

## Root Cause

`verified_at` should not be cleared. The seven rows contain settlement probe timestamps and matching station IDs in the current DB snapshot. The status should be upgraded from `provisional` to `verified` when `settlement_station_id` matches `station_id`, and to `settlement_mismatch` when it does not.

The durable code defect is in `sync_station_registry`: it treats status as the only protection flag, while `settlement_rule_verified_at` is also a protection signal. That allows a split-brain state: verified timestamp preserved, status overwritten by registry default.

## Review Patch

Patch file for review only: `audits/station-verification-status-2026-07-05/stations-verification-reconcile.patch`.

This patch is not applied in this round.
