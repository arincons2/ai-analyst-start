# users

Grain: one row per user (user_id). 31,165 rows, whole table (live database, profiled 2026-11-13).

In plain words (inferred): `user_id` has 31,165 distinct values, one per row.

## Columns

| Name | Type | Null rate | Distinct |
| --- | --- | --- | --- |
| user_id | integer | 0.00% | 31,165 |
| signup_date | timestamp | 0.00% | not profiled |
| country | varchar | not profiled | not profiled |
| device_type | varchar | not profiled | not profiled |

## Date ranges (to cutoff 2026-09-25 00:00, exclusive)

- `signup_date`: 2022-01-01 00:00:00 to 2026-09-24 22:59:05 (0 rows after the cutoff)

## Integrity

- Whole table: trials to users, 0 orphans. Sessions to users, 0 orphans.

## Meaning

- `signup_date`: when the user signed up (inferred). For all 2,073 trial users it falls on the same calendar date as the trial start.
- `country`, `device_type`: signup attributes only, not profiled in this run.

## Notes for the next reader

- The September 2025 spike (see quirks) sits in this table's signups.
- Verdict: Proceed.
