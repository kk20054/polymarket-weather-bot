# WeatherBot Agent Rules

This is the short operating guide for Codex and other coding agents. Read detailed rules from `docs/AGENTS_DETAIL_CN.md` only when the current task needs them.

## Mission

```text
real data -> leakage-free probabilities -> realistic paper execution
-> production dashboard -> 14-30 day validation -> small live canary
```

WeatherBot is usable for observation and controlled simulation. Do not claim stable profitability or unlock unattended live trading until validation gates prove an edge.

## Three Default Documents

- `AGENTS.md`: agent workflow, safety boundaries, and standard commands.
- `docs/CURRENT_STATE.md`: the only default turn-start context; keep it under 40 lines.
- `README.md`: installation, startup, operator workflow, strategy meaning, and limitations.

Indexed references, not default context:

- `docs/IMPLEMENTATION_LOGIC_CN.md`: stable layers and data flow.
- `docs/DATA_STORAGE_CN.md`: logical and physical data paths.
- `docs/AGENTS_DETAIL_CN.md`: PolyWX, UI, data, algorithm, naming, and Git details.
- `docs/FORWARD_VALIDATION_PREREGISTRATION_*.md`: frozen validation protocol.
- `docs/SOURCE_REGISTER.csv`: reusable external evidence and refresh rules.
- Git history: the project ledger and release history.
- `audits/`: ignored temporary evidence; never scan it by default.

## Turn Start Protocol

1. Read only `docs/CURRENT_STATE.md`.
2. Run `git status --short --branch`.
3. Confirm the named layer, scope, and blockers; read only the relevant indexed reference.
4. Reuse `docs/SOURCE_REGISTER.csv` and existing evidence before browsing.
5. Do not recursively scan `data/`, `audits/`, `backups/`, `.venv/`, generated output, or archives.

For substantial work, establish this compact task contract in the plan or commentary, not another Markdown file:

```text
goal | deliverable | in scope | out of scope | authoritative input | checks | commit
```

## Turn End Protocol

1. Run `scripts/check.ps1` with the smallest sufficient scope.
2. Update `docs/CURRENT_STATE.md` only when durable facts, blockers, or the single next task changed.
3. Do not create per-turn ledgers, recap documents, or audit indexes unless explicitly requested.
4. Keep temporary evidence under ignored `audits/`; keep formal history in Git.
5. Report changed files, checks, usability, and remaining blocker.

## Error Handling

Development-stage failures must be loud. A stack trace is a feature, not a defect.

**Default: let it raise.** Do not wrap code in `try/except` unless you can name the specific exception and the fallback is semantically correct, not merely convenient.

Banned patterns:

```python
# BANNED: hides a real failure behind a plausible-looking default
try:
    rows = fetch_something()
except Exception:
    return {}

# BANNED: a failed fetch becomes a number that looks like data
except Exception:
    price = 0.0

# BANNED: silent skip with no record
except Exception:
    continue
```

Allowed patterns:

```python
# OK: narrow, expected, and the caller can distinguish the outcome
try:
    parsed = datetime.fromisoformat(text)
except ValueError:
    return None          # None is a declared part of the contract

# OK: outermost boundary keeps the service alive but records the truth
except Exception:
    log.exception("orderbook refresh failed for %s", token_id)
    mark_state(token_id, "fetch_failed")   # never "0" and never a stale value
    raise_if_strict()
```

Rules:

- **Three states are never collapsed into one.** `absent` (genuinely nothing there), `invalid` (present but unusable), and `fetch_failed` (we could not look) must remain distinguishable in the schema and in gate reasons.
- **A failure must never be able to masquerade as data.** Never write `0`, never keep a stale value while refreshing its timestamp, never fill both sides of a book from a single indicative price.
- **No fabricated or approximated values in the evidence chain (Layers 0-6).** The presentation layer may degrade, but must label the degradation on screen.
- **Boundary catching is allowed, silent success is not.** HTTP handlers and scheduler loops may catch so the process survives, but must log with traceback and return an explicit error field. Returning an empty successful payload is banned.
- **A per-item failure inside a loop is recorded, not skipped.** One city failing must not silently shrink the result set.
- **`except: pass` requires a comment stating why the failure is genuinely irrelevant.** Without it, remove the handler.
- **Returning `None` is fine when `None` is in the contract.** The caller must not then coerce it to `0`, `""`, or `False`.

If you are unsure whether to catch: do not catch. Surfacing an error costs one turn; a swallowed error has already cost this project multiple rounds of misdiagnosis.

## Diagnosis Before Fix

For any "it does not work" or "there is no output" task:

1. **Counts over the full population, not examples.** A `GROUP BY` over every row beats three hand-picked cases.
2. **Build the funnel.** Show stage-by-stage counts from input to final output so the drop-off point is visible.
3. **Every negative outcome must land in a named category.** "Unknown" is not an acceptable classification.
4. **Distinguish three failure classes explicitly:** no row was produced / rows were produced but rejected / rows passed but the UI hid them. The fixes are unrelated.
5. **The most important finding goes in the first paragraph.** Do not park the actual root cause in an appendix as "a separate issue".
6. **No threshold change before evidence.** If evidence shows a threshold was set against a wrong measurement unit or a wrong definition, fixing the definition is a bug fix, not a loosened gate — say so explicitly.

Diagnosis and fix should normally be separate commits.

## One Canonical Implementation

One concept gets exactly one implementation, shared by strategy, persistence, API, and UI.

- If two code paths compute the same number and disagree, that is a defect regardless of which one is correct.
- Adding a second way to compute an existing quantity requires deleting the first.
- Persisted values must be reproducible from their stated inputs by the same function.

## Statistical Claims

- No claim of edge without a preregistered hypothesis, a stated sample size, and a power calculation.
- Post-hoc slicing is exploratory only and can never justify continued investment on its own.
- Always separate **not proven** from **disproven**. If the experiment lacked the power to detect the effect, say that instead of concluding there is no effect.
- Report the sample size and confidence interval next to every point estimate.
- A frozen preregistration is immutable. If it is found to be defective, supersede it with a new version that documents the defect; never edit it in place.

## Deployment Verification

Editing code does not change a running service.

- After changing anything the backend or scheduler loads at import time, restart it and state the new PID and start time.
- Verify the new path is actually live with counts, not assumptions: how many new rows carry the new field, how many use the new contract.
- Report whether the change is committed, deployed, and verified as three separate facts.

## Build Order

- Layer 0: reference corpus and schema evidence.
- Layer 1: station registry and settlement contracts.
- Layer 2: METAR and supplementary observations.
- Layer 3: forecast runs and ensemble members.
- Layer 4: hourly consensus and daily-max distributions.
- Layer 5: strictly matched market buckets and executable quotes.
- Layer 6: signal decisions, evidence, and risk gates.
- Layer 7: persisted-data dashboard.
- Layer 8: paper execution and risk controls.
- Layer 9: 14-30 day validation and replay.
- Layer 10: live canary only after gates pass.

One turn should touch at most one layer plus its direct consumer. For Layers 0-6, plan before code unless the user already supplied an approved implementation plan.

## Canonical Code Paths

- `weatherbot_v3/`: production business modules.
- `dashboard_server.py`: FastAPI and adapter layer only.
- `frontend/src/`: the only editable production frontend.
- `dashboard/`: compatibility static UI; do not add features here.
- `legacy/`: read-only history.
- `sites-dashboard/`: ignored experiment, never a production dependency.
- `tests/`: regression and contract tests.
- `tools/`: read-only diagnostics and replay.
- `scripts/`: the only supported operator/developer entry commands.

Runtime data stays behind the project `data/` Junction to `D:\WeatherBot\data`. Never replace it with scattered absolute paths or delete its physical target.

## Coverage and Configuration

- Any default set of cities, models, or sources comes from the registry (`stations.enabled`), never from a hardcoded list, a sample list, or a default function argument.
- A silent `limit` that truncates a population is a coverage defect. Truncation must be explicit and reported.
- Environment variables may switch behaviour but must not become a de facto allowlist.

## Safety Red Lines

- Backend startup stays lightweight; no automatic legacy scan or simulation resume.
- Do not start `weatherbet.py`.
- `LIVE_TRADING=false` remains the default.
- The first possible live action is BUY YES limit-only canary with idempotency, balance, tick, minimum-size, freshness, duplicate, spread, depth, and daily-limit checks.
- Do not commit `audits/`, `data/`, `.env`, `config.json`, `.venv/`, generated builds, dependencies, backups, or secrets.
- Do not weaken data, calibration, liquidity, or risk gates merely to create more trades.
- **Never conflate a statistical cohort inclusion rule with a trading risk gate.** Widening what enters an analysis sample is allowed and must be labelled as such; widening what is allowed to trade is not.
- Do not modify a frozen preregistration, a started paper cohort's parameter snapshot, or historical audit evidence.

## Standard Commands

```powershell
# Start backend, frontend, scheduler, and browser through the canonical launcher.
.\scripts\dev.ps1

# Default code-and-UI gate. Other scopes: docs, backend, frontend, full.
.\scripts\check.ps1
.\scripts\check.ps1 -Scope backend
```

Verification scope rules:

- Run the smallest scope that covers the change, plus any new regression test.
- Do not run `-Scope full` or `npm run build` unless the change touches the frontend or you were asked for a release gate.
- Read-only analysis turns run no tests at all.
- A pre-existing unrelated failure is reported, not opportunistically fixed.

Use Git commits/tags as the only code release history. Do not create copied "latest/final/v2" source trees. A build or local launcher is generated from the tracked mainline and is not a second editable version.
