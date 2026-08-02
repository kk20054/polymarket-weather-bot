# WeatherBot Current State

## Current Layer
- Date: 2026-08-03. Phase 3/6: frozen forward observation and operator-access hardening.
- Production data remains at `D:\WeatherBot\data\weatherbot_v3.db`; the scheduler is stopped after a bounded smoke cycle.
- `LIVE_TRADING=false`; no profitability claim has been established.
- Frozen v2 validation remains unchanged: model-side `0.20<=ask<0.40`, `edge>=8%`, target `N=338`.

## Latest Evidence
- Sites version 2 is deployed privately at `https://weatherbot-polymarket-v1.kl28398052.chatgpt.site`.
- Hosted reads use a protected Cloudflare origin tunnel while the laptop is online and fall back to the immutable snapshot when it is offline.
- Hosted writes are owner-only and allowlisted. API settings, live/canary execution, secrets, and legacy actions remain local-only.
- The tunnel origin requires `WEATHERBOT_ORIGIN_TOKEN`; localhost remains usable without the token.
- The recurring operator freeze was traced to ordinary `/api/dashboard` reads launching the full legacy dashboard rebuild after its 20-second cache expired.
- Ordinary dashboard reads are now lightweight by default; the full rebuild runs only when `WEATHERBOT_DASHBOARD_AUTO_BUILD=true` is explicitly set.
- Initial 51-city indexing improved from more than 44s to 2.57s. Selected-city dashboard reads measured 2.64s cold and 1.21s warm with an approximately 186KB response.
- `/api/source-health` improved from about 74s to 2.45s cold and 0.07s cached while retaining all 49 source-health rows.
- The production SQLite database is 45.67GB. An exact read-only scan found 911MB of duplicate `raw_json` older than 30 days; 14-day and 7-day estimates are about 5.7GB and 20.2GB respectively.
- `storage-archive` now checkpoints a checksum-verified recovery manifest before every database clear, survives interrupted batches, and restores archived `raw_json` without loading whole archives into memory.
- Six obsolete SQLite backups occupy 24.3GB uncompressed on NTFS. Transparent backup compression is the safest first physical-space reduction; production SQLite uses `auto_vacuum=0`, so raw-blob archival alone will not shrink its 45.67GB file.
- No storage archive or VACUUM has been applied to the production database in this turn.
- A prior uninterrupted scheduler run lasted about 41 hours. Core polling continued, but network failures and occasional `database is locked` errors were observed; derive concurrency is now capped at 2 and transient SQLite locks receive two bounded retries.
- The post-fix smoke cycle completed derive for 49/49 enabled cities with no lock failure, refreshed 1,078/1,078 order books, and completed one paper execution pass with zero fresh executable candidates.
- The latest three-day funnel has 130 city/date predictions and 1,430 strictly matched buckets, but only 3 decisions were ever paper-allowed and none is currently dashboard-visible. Dominant blocks are absent book sides, D+0 peak-window timing, insufficient effective edge, and spread/price constraints; thresholds were not weakened.
- Frozen forward evidence currently has 162 enrolled candidates and 137 anchors. Mean CLV is -4.22 percentage points (95% CI -6.77 to -1.68); D+0 and the 8-15% edge subgroup are negative, so no validated trading edge exists yet.
- The active paper cohort has 4 records: 1 settled loss (-$1.17), 1 open position (-$0.285 mark-to-market), and 2 rejected orders. Rejected orders are now excluded from open-position and unrealized-PnL totals.

## Production Blockers
- The current Cloudflare Quick Tunnel URL is temporary and changes after restart; stable remote operation needs a named tunnel/domain.
- SQLite remains large enough that uncached first reads take 2-3s; remote hosting still needs a named tunnel and later database migration/compaction.
- Forward validation has not reached the frozen `N=338` stop rule; strategy quality and profitability remain unproven.
- Current forward CLV is materially negative overall. Live trading must remain locked until the frozen evaluation reaches its stop rule and reverses this evidence.
- SQLite is still a write-contention bottleneck under broad 49-city derive workloads; bounded retries reduce transient failures but do not replace a future database architecture decision.

## Next Task
- Keep collecting the frozen forward cohort without changing model or risk thresholds; use CLV/Brier/P&L rather than raw signal count as the go/no-go evidence.
- Replace the Quick Tunnel with a named tunnel or cloud backend before stable remote operation; keep the lightweight dashboard path as the default.
- During the next approved downtime, transparently compress old backup files first; separately choose a 7/14/30-day raw-payload retention window before archival and offline SQLite compaction.
