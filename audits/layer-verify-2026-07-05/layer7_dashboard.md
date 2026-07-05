# Layer 7 Dashboard Contract Verification

Generated: 2026-07-05T03:22:19.360178+00:00
URL: http://127.0.0.1:8765/api/dashboard
Payload source: direct_import

## Payload Top-Level

| key | type |
| --- | --- |
| active_signals | list |
| backtest | dict |
| btc_price | NoneType |
| calibration | dict |
| city_evidence | list |
| data_readiness | dict |
| equity_curve | list |
| events | list |
| fetch_log | list |
| microstructure | NoneType |
| model_dataset_audit | NoneType |
| production_refresh | dict |
| recent_trades | list |
| recommendations | dict |
| stats | dict |
| truth_health | dict |
| v3 | dict |
| weather_city_series | list |
| weather_forecasts | list |
| weather_signals | list |
| windows | list |

## Weather Signal Field Issues

No weather signal field issues found, or no weather_signals list is present.

## City Evidence Module Issues

No ready-without-rows city evidence module issues found.

## Recommendation Field Issues

No recommendation field issues found, or no recommendation items are present.

## P0 Findings

None.

## Layer Conclusion

- Can enter next layer: yes
- Blockers:
  - No Layer 7 P0 contract blocker found
