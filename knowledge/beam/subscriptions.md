# subscriptions

Grain (a guess from the data, confirm it): one row per subscription_id. 30,747 rows.

In plain words (inferred): one row per subscription, and also one per user, because `user_id` has as many distinct values (30,747) as there are rows. The table holds no repeat subscriptions, upgrades or plan switches for the same user.

## Columns

| Name | Type | Null rate | Distinct |
| --- | --- | --- | --- |
| subscription_id | int64 | 0.00% | 30,747 |
| user_id | int64 | 0.00% | 30,747 |
| plan | object | 0.00% | 3 |
| start_date | datetime64[ns] | 0.00% | 9,584 |
| end_date | datetime64[ns] | 87.90% | 1,789 |
| status | object | 0.00% | 3 |

## Date ranges

- `start_date`: 2022-01-11T00:00:00 to 2026-10-04T17:15:33
- `end_date`: 2022-03-07T00:00:00 to 2026-11-11T18:47:46

## Low-cardinality values

- `plan`: free (18,234), monthly (9,376), annual (3,137)
- `status`: active (27,028), expired (2,929), canceled (790)

## Meaning

- `subscription_id`: identifies one subscription (inferred).
- `user_id`: the user who holds it; unique in this table (inferred).
- `plan`: the plan the subscription is on: free, monthly or annual (inferred). The monthly plan price rose from $10 to $12 in March 2026 (see quirk "Monthly price change").
- `start_date`: when the subscription started (inferred). Some values are after the data's last day, 2026-09-24; these are scheduled starts (see quirk "Future start dates").
- `end_date`: when the subscription ends or ended (inferred). The 87.90% nulls are explained in part by the quirk "Free plans never end": free plans are 59.3% of rows. The rest of the nulls look like subscriptions with no end, mostly active paid ones (inferred). Some values are after 2026-09-24; these are scheduled cancellations (see quirk "Future end dates").
- `status`: current state of the subscription: active, expired or canceled (inferred). What separates "expired" from "canceled" is not documented.

## Notes for the next reader

- The count of rows with an `end_date` (3,719) equals the count of rows with status expired or canceled (2,929 + 790), and the count of null `end_date` (27,028) equals the count of active. That suggests `status` is not active for every row with an end date, including ones in the future (inferred from counts, not checked row by row). The quirk "Future end dates" says a future end date is still active today, so decide active or ended from the dates, not from `status` alone, until a person confirms how `status` treats scheduled cancellations.
- Because each user has one row, any churn or tenure measure must come from `start_date` and `end_date` of that row. There is no history of earlier subscriptions to draw on.
