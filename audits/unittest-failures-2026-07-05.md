# Unittest Baseline Failure Analysis - 2026-07-05

Baseline command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests 2>&1 | Tee-Object -FilePath audits\unittest-baseline-2026-07-05.log
```

Baseline result: 196 tests, 3 failures, 0 errors.

## FAIL / ERROR Index

| Test | Baseline failure | First related commit | Classification | Resolution |
|---|---|---|---|---|
| `tests.test_deb_gaussian.DebGaussianTests.test_gaussian_bucket_integral_matches_one_sigma_interval` | Expected one-sigma continuous interval probability `0.682689`; actual `0.8185946141` because Celsius market buckets are now interpreted as truncated integer intervals `[24, 27)`. | `35f9801 feat(layer2-6): add PWS evidence and mixed DEB peak` | Test outdated and replaced by new implementation rule. | Updated the test name and assertion to verify Celsius integer-bucket truncation, including persisted `bucket_high=27.0`. |
| `tests.test_v3_core.V3CoreTests.test_dashboard_recommendations_use_fresh_verified_signal_decisions` | Expected one recommendation; actual count `0` because the recommendation query filtered by server `date.today()` before applying station-local day logic. On an Asia/Shanghai host, Chicago local D+0 can be one calendar day behind the server. | `4325a0d feat(layer6-7): close recommendation loop and add diagnostics` | True bug plus test environment dependency. | Fixed `_recommendations_payload` to query from the minimum station-local date and skip past targets per station. Updated the fixture target to Chicago local today. |
| `tests.test_v3_core.V3CoreTests.test_dashboard_recommendations_keep_spread_only_watch_candidate` | Same root cause as above; the fixture was intended to exercise D+0 METAR freshness but host `date.today()` made it a Chicago D+1 without a forecast row. | `4325a0d feat(layer6-7): close recommendation loop and add diagnostics` | True bug plus test environment dependency. | Same fix as above; spread-only watch candidate now exercises station-local D+0. |

## Post-Fix Targeted Check

Command:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_deb_gaussian.DebGaussianTests.test_celsius_bucket_integral_uses_truncated_integer_interval tests.test_v3_core.V3CoreTests.test_dashboard_recommendations_use_fresh_verified_signal_decisions tests.test_v3_core.V3CoreTests.test_dashboard_recommendations_keep_spread_only_watch_candidate
```

Result: 3 tests passed.
