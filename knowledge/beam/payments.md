# payments

Grain (a guess from the data, confirm it): one row per payment_id. 100,914 rows.

In plain words (inferred): one row per payment taken. A subscription can appear many times, since 11,122 distinct `subscription_id` values sit across 100,914 rows.

## Columns

| Name | Type | Null rate | Distinct |
| --- | --- | --- | --- |
| payment_id | int64 | 0.00% | 100,914 |
| subscription_id | int64 | 0.00% | 11,122 |
| payment_date | datetime64[ns] | 0.00% | 13,232 |
| amount_usd | float64 | 0.00% | 3 |
| method | object | 0.00% | 4 |

## Date ranges

- `payment_date`: 2025-01-01T00:00:00 to 2026-09-24T23:51:30

## Low-cardinality values

- `amount_usd`: 10.0 (59,064), 12.0 (37,384), 100.0 (4,466)
- `method`: card (50,237), paypal (20,272), apple_pay (15,298), google_pay (15,107)

## Meaning

- `payment_id`: identifies one payment (inferred).
- `subscription_id`: the subscription the payment belongs to (inferred); joins to `subscriptions.subscription_id`. Many payments per subscription, so a join from subscriptions to payments fans out.
- `payment_date`: when the payment was taken (inferred). The data ends on 2026-09-24, as the project's "today". The table starts on 2025-01-01, so payments before then are not here (the README says "payments from 2025-01-01"; no quirk covers this).
- `amount_usd`: the amount charged (inferred). $10 and $12 are most likely monthly payments before and after the March 2026 price rise, and $100 an annual payment (inferred, not confirmed). The quirk "Monthly price change" explains the two monthly amounts.
- `method`: how the customer paid: card, paypal, apple_pay or google_pay (inferred).

## Notes for the next reader

- Only 11,122 subscriptions have payments, while 12,513 subscriptions are on a paid plan (9,376 monthly plus 3,137 annual, from `subscriptions`). This is not explained by any quirk. One possible cause is subscriptions whose payments fall before 2025-01-01 (inferred, not checked).
- The first month where `amount_usd` is 12.0 for monthly payments is the best source for the exact day the price rose. This has not been checked.
