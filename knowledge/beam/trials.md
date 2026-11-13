# trials

Grain: one row per trial (trial_id) and one per user (user_id). 2,073 rows, whole table (live database, profiled 2026-11-13).

In plain words (inferred): each trial user has exactly one 14-day trial. `trial_id` and `user_id` both have 2,073 distinct values, and the maximum trials per user is 1.

## Columns

| Name | Type | Null rate | Distinct |
| --- | --- | --- | --- |
| trial_id | integer | 0.00% | 2,073 |
| user_id | integer | 0.00% | 2,073 |
| started_at | timestamp | 0.00% | not profiled |
| ends_at | timestamp | 0.00% | not profiled |
| outcome | varchar | 20.17% (418 rows) | 2 values plus NULL |
| outcome_at | timestamp | 20.17% (418 rows) | not profiled |

## Date ranges (to cutoff 2026-09-25 00:00, exclusive; no row is after it)

- `started_at`: 2026-08-01 00:29:17 to 2026-09-24 22:59:05
- `ends_at`: 2026-08-15 00:29:17 to 2026-10-08 22:59:05 (exactly 14 days after `started_at` in all 2,073 rows)

## Low-cardinality values (to cutoff)

- `outcome`: converted (828), canceled (827), NULL (418)

## Integrity

- Whole table: trials to users, 2,073 child rows, 0 null keys, 0 orphans, 2,073 rows after the join (no fan-out). Max trials per user 1.
- Whole table: 418 trials have no outcome and all end after the cutoff (null_but_ended 0). 102 canceled trials also end after the cutoff, so their outcome is known before the trial ends. All 828 converted outcomes have `outcome_at` equal to `ends_at`.
- Canceled `outcome_at` is 4 to 13 days after `started_at` (average 8.41); converted is exactly 14 days.

## Meaning

- `started_at`: when the trial began (inferred). Every user's `signup_date` falls on the same calendar date as `started_at` (2,073 of 2,073).
- `ends_at`: end of the 14 days (inferred). A trial matured by the analysis date when `ends_at` is before 2026-09-25 (1,553 of 2,073).
- `outcome`: converted or canceled once resolved, NULL while the trial is active (see quirk "Trials mature late"). A cancel can be recorded before `ends_at`, a conversion only at `ends_at`, so a rate over all resolved trials still inside their 14 days counts cancels and almost no conversions (inferred from the timing above).
- `outcome_at`: when the outcome was recorded; NULL exactly when `outcome` is NULL.

## Notes for the next reader

- Exploratory only, not an official link: of the 2,073 trial users, 1,655 have a subscription row. All 828 converted trials have a monthly or annual subscription starting on the date of `ends_at`; all 827 canceled trials have a free subscription; the 418 open trials have none. Per `trials.yaml` the trials-to-subscriptions relationship stays `not_established`.
- Verdict: Proceed, with the maturity caution above.
