# Layer 6 Gate Verification

Generated: 2026-07-05T03:16:01.388694+00:00
Rows considered: latest 100 signal_decisions plus all-time strategy counts.

## gate_reasons Top10

| reason | rows | sample_decision_ids |
| --- | --- | --- |
| insufficient_bias_samples | 100 | f6a83a3b46b0cbe09b99997ffdad2a06, c864999aa2f44b4db1ecb94f2e3bdf03, fcf7d6355d82998a13b3a4ec1d7d26a6 |
| settlement_rule_unverified | 100 | f6a83a3b46b0cbe09b99997ffdad2a06, c864999aa2f44b4db1ecb94f2e3bdf03, fcf7d6355d82998a13b3a4ec1d7d26a6 |
| live_trading_disabled | 100 | f6a83a3b46b0cbe09b99997ffdad2a06, c864999aa2f44b4db1ecb94f2e3bdf03, fcf7d6355d82998a13b3a4ec1d7d26a6 |
| paper_gate_not_passed | 97 | f6a83a3b46b0cbe09b99997ffdad2a06, c864999aa2f44b4db1ecb94f2e3bdf03, fcf7d6355d82998a13b3a4ec1d7d26a6 |
| spread_too_wide | 87 | f6a83a3b46b0cbe09b99997ffdad2a06, c864999aa2f44b4db1ecb94f2e3bdf03, fcf7d6355d82998a13b3a4ec1d7d26a6 |
| edge_below_min | 74 | f6a83a3b46b0cbe09b99997ffdad2a06, c864999aa2f44b4db1ecb94f2e3bdf03, fcf7d6355d82998a13b3a4ec1d7d26a6 |
| low_price_tail_bucket | 19 | f6a83a3b46b0cbe09b99997ffdad2a06, 241ad6e01e7495332529e8aeaeb0cbf2, fd1a0ce1af6d111a678530f93d679343 |

## paper_allowed=true And live_allowed=false Samples

| decision_id | city | target_date | strategy | model_p | ask | edge | primary_block |
| --- | --- | --- | --- | --- | --- | --- | --- |
| febe29444b027ec874f71a0660dbd0b1 | shanghai | 2026-07-05 | -- | 0.32285648637329833 | 0.04 | 0.28285648637329835 | insufficient_bias_samples |
| 7f17ded7e698dbb05f73308d0f2a0f23 | nyc | 2026-07-05 | -- | 0.10746505419264195 | 0.071 | 0.036465054192641955 | insufficient_bias_samples |
| 3be131aa4a04b5beb02dcc7cc1bade31 | dallas | 2026-07-05 | -- | 0.3535290085631339 | 0.24 | 0.11352900856313392 | insufficient_bias_samples |

## Strategy Counts

| strategy | latest_100_rows | all_time_rows |
| --- | --- | --- |
| ladder_grid | 0 | 0 |
| single_bucket_ev | 11 | 33 |
| tail_buying | 0 | 0 |
| unknown | 89 | 1472 |

## P0 Findings

None.

## Layer Conclusion

- Can enter next layer: yes
- Blockers:
  - paper_allowed/live_blocked samples in latest 100: 3
