# WeatherBot Current State

## Current Layer
- Date: 2026-08-05. Phase 3/6: frozen forward observation, public dashboard separation, and storage hardening.
- Production data remains local at `D:\WeatherBot\data\weatherbot_v3.db`; the scheduler is stopped and the database has not changed since 2026-08-03.
- `LIVE_TRADING=false`; no profitability claim has been established.
- Frozen v2 validation remains unchanged: model-side `0.20<=ask<0.40`, `edge>=8%`, target `N=338`.

## Latest Evidence
- The current core-modal cohort has 5 records: 2 settled (1 win, 1 loss), 1 open, and 2 rejected; settled win rate is 50% at `N=2`, realized PnL is about `+$3.09`, and the open mark was slightly negative. This is operational evidence, not a profitability result.
- Forward validation has 168 enrolled and 140 anchored candidates. Mean CLV is about `-4.11pp` with a 95% interval entirely below zero, so the current strategy is not ready for live trading.
- The latest complete three-day funnel has 99 city/date predictions, all 99 with at least four model families and model spread, 1,089 strict-matched buckets, but zero latest `paper_allowed` decisions. Main blocks are wide spreads, D+0 timing, missing book sides, and insufficient effective edge; risk thresholds were not weakened.
- A signal-funnel audit bug that counted only positive-weight models was fixed; deterministic diagnostic model families now count independently from ensemble members and fusion weight.
- Sites version 3 is privately deployed at `https://weatherbot-polymarket-v1.kl28398052.chatgpt.site`.
- The hosted UI now distinguishes local live, public live, public read-only, and offline snapshot modes. A stale snapshot is visibly marked and mutation controls are disabled.
- Public mutations are owner-only and narrowly allowlisted to scheduler and paper-validation/order actions. Production refresh, secrets, legacy, canary, and live execution remain local-only.
- With the laptop offline, `/api/dashboard` returned `snapshot`, `write=false`, and the removed production-refresh route returned HTTP 403. The current fallback snapshot is dated 2026-07-18 and must not be treated as live.
- Initial 51-city indexing is about 2.57s; selected-city dashboard reads measured about 2.64s cold and 1.21s warm. `/api/source-health` measured about 2.45s cold and 0.07s cached.
- The production SQLite database is 45.67GB. Backup files totaling 24.32GB logical were losslessly NTFS-compressed to about 2.37GB stored, freeing roughly 20.45GB without deleting data.
- Raw duplicate JSON remains a later maintenance target. It will be checksum-archived before clearing, and SQLite compaction will only run in an approved offline window.

## Production Blockers
- Current forward CLV is materially negative and settled `N=2` is not decision-grade. Live trading stays locked.
- The Quick Tunnel URL changes after restart; reliable public live operation needs a named Cloudflare Tunnel or cloud backend.
- The offline public snapshot is stale. Snapshot publishing needs an automatic successful-cycle hook before it can be a useful fallback.
- SQLite is a 45.67GB single-writer bottleneck; broad derive work can still encounter contention despite bounded concurrency and retries.

## Next Task
- Resume the frozen paper cohort only when continuous collection is desired; judge by CLV/Brier/PnL, not signal count.
- Replace Quick Tunnel with a named tunnel and add automatic snapshot publication after a successful derive cycle.
- In the next approved downtime, archive old duplicate raw payloads, verify recovery, then compact SQLite; do not delete structured evidence.
