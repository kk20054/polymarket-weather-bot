# Layer 5 Market Buckets Verification

Generated: 2026-07-05T03:16:00.726390+00:00
Latest updated_at considered: 2026-07-04T11:34:34.269103+00:00
Rows considered: 332 current rows in `market_buckets`

## strict_match_status Distribution

| status | rows |
| --- | --- |
| blocked | 48 |
| matched | 284 |

## Unmatched Reason Top10

| reason | rows |
| --- | --- |
| tick_size_missing | 48 |
| order_min_size_missing | 48 |
| quote_price_missing | 48 |
| yes_token_missing | 7 |

## Unmatched Examples

| city | target_date | bucket | question | reasons |
| --- | --- | --- | --- | --- |
| atlanta | 2026-06-16 | 74-75F | Will the highest temperature in Atlanta be between 74-75°F on June 16? | ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| dallas | 2026-06-16 | 90-91F | Will the highest temperature in Dallas be between 90-91°F on June 16? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-16 | 25C | Will the highest temperature in London be 25°C on June 16? | ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| miami | 2026-06-16 | 90-91F | Will the highest temperature in Miami be between 90-91°F on June 16? | ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| munich | 2026-06-16 | 22C | Will the highest temperature in Munich be 22°C on June 16? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| sao-paulo | 2026-06-16 | 18C | Will the highest temperature in Sao Paulo be 18°C on June 16? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| seattle | 2026-06-16 | 70-71F | Will the highest temperature in Seattle be between 70-71°F on June 16? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| toronto | 2026-06-16 | 22C | Will the highest temperature in Toronto be 22°C on June 16? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| atlanta | 2026-06-17 | 82-83F | Will the highest temperature in Atlanta be between 82-83°F on June 17? | ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| buenos-aires | 2026-06-17 | 13C | Will the highest temperature in Buenos Aires be 13°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| chicago | 2026-06-17 | 64-65F | Will the highest temperature in Chicago be between 64-65°F on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| dallas | 2026-06-17 | 90-91F | Will the highest temperature in Dallas be between 90-91°F on June 17? | ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-17 | 24C | Will the highest temperature in London be 24°C on June 17? | ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| lucknow | 2026-06-17 | 39C | Will the highest temperature in Lucknow be 39°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| miami | 2026-06-17 | 90-91F | Will the highest temperature in Miami be between 90-91°F on June 17? | ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| munich | 2026-06-17 | 26C | Will the highest temperature in Munich be 26°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| paris | 2026-06-17 | 30C | Will the highest temperature in Paris be 30°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| sao-paulo | 2026-06-17 | 19C | Will the highest temperature in Sao Paulo be 19°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| seoul | 2026-06-17 | 28C | Will the highest temperature in Seoul be 28°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| shanghai | 2026-06-17 | 28C | Will the highest temperature in Shanghai be 28°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| wellington | 2026-06-17 | 14C | Will the highest temperature in Wellington be 14°C on June 17? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| chicago | 2026-06-19 | 76-77F | Will the highest temperature in Chicago be between 76-77°F on June 19? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| miami | 2026-06-19 | 90-91F | Will the highest temperature in Miami be between 90-91°F on June 19? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-19 | 30C | Will the highest temperature in London be 30°C on June 19? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-20 | 29C | Will the highest temperature in London be 29°C on June 20? | ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |

## Unmatched Examples By City/Date

| city | target_date | unmatched_rows | examples_max_5 |
| --- | --- | --- | --- |
| ankara | 2026-06-19 | 1 | 23C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| ankara | 2026-06-20 | 1 | 23C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| atlanta | 2026-06-16 | 1 | 74-75F: ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| atlanta | 2026-06-17 | 1 | 82-83F: ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| atlanta | 2026-06-19 | 1 | 82-83F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| buenos-aires | 2026-06-17 | 1 | 13C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| buenos-aires | 2026-06-19 | 1 | 14C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| chicago | 2026-06-17 | 1 | 64-65F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| chicago | 2026-06-19 | 1 | 76-77F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| dallas | 2026-06-16 | 1 | 90-91F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| dallas | 2026-06-17 | 1 | 90-91F: ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| dallas | 2026-06-19 | 1 | 84-85F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-16 | 1 | 25C: ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-17 | 1 | 24C: ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-19 | 1 | 30C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| london | 2026-06-20 | 1 | 29C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| lucknow | 2026-06-17 | 1 | 39C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| lucknow | 2026-06-19 | 1 | 39C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| lucknow | 2026-06-20 | 1 | 39C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| miami | 2026-06-16 | 1 | 90-91F: ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| miami | 2026-06-17 | 1 | 90-91F: ["yes_token_missing", "tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| miami | 2026-06-19 | 1 | 90-91F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| munich | 2026-06-16 | 1 | 22C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| munich | 2026-06-17 | 1 | 26C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| nyc | 2026-06-19 | 1 | 84-85F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| paris | 2026-06-17 | 1 | 30C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| paris | 2026-06-19 | 1 | 35C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| paris | 2026-06-20 | 1 | 32C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| sao-paulo | 2026-06-16 | 1 | 18C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| sao-paulo | 2026-06-17 | 1 | 19C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| sao-paulo | 2026-06-19 | 1 | 24C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| seattle | 2026-06-16 | 1 | 70-71F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| seattle | 2026-06-19 | 1 | 80-81F: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| seoul | 2026-06-17 | 1 | 28C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| seoul | 2026-06-19 | 1 | 29C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| seoul | 2026-06-20 | 1 | 25C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| shanghai | 2026-06-17 | 1 | 28C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| shanghai | 2026-06-19 | 1 | 29C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| shanghai | 2026-06-20 | 1 | 29C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| singapore | 2026-06-19 | 1 | 30C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| singapore | 2026-06-20 | 1 | 29C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| tokyo | 2026-06-19 | 1 | 27C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| tokyo | 2026-06-20 | 1 | 23C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| toronto | 2026-06-16 | 1 | 22C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| toronto | 2026-06-19 | 1 | 21C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| wellington | 2026-06-17 | 1 | 14C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| wellington | 2026-06-19 | 1 | 14C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |
| wellington | 2026-06-20 | 1 | 16C: ["tick_size_missing", "order_min_size_missing", "quote_price_missing"] |

## Required Trading Metadata Gaps

| check | rows |
| --- | --- |
| empty token_id/yes_token_id | 7 |
| missing tick_size | 48 |
| missing order_min_size | 48 |

## P0 Findings

None.

## Layer Conclusion

- Can enter next layer: yes
- Blockers:
  - unmatched rows: 48
  - missing token rows: 7
  - missing tick rows: 48
  - missing orderMinSize rows: 48
