# sessions

Grain: one row per session (session_id). 4,335,470 rows, whole table (live database, profiled 2026-11-13).

In plain words (inferred): each row is one visit by a user with a duration and an activity type. `session_id` has 4,335,470 distinct values.

## Columns

| Name | Type | Null rate | Distinct |
| --- | --- | --- | --- |
| session_id | integer | 0.00% | 4,335,470 |
| user_id | integer | 0.00% | 29,568 users |
| session_date | timestamp | 0.00% | not profiled |
| duration_minutes | integer | 0.00% | not profiled |
| activity_type | varchar | 0.00% | 3 (browse, listen, read) |

## Date ranges

- `session_date`: latest value 2026-09-26 00:00:00. 273 sessions (all of trial users) are dated on or after 2026-09-25, past the cutoff, and are excluded by default.
- `duration_minutes`: 1 to 157 (whole table).

## Integrity

- Whole table: sessions to users, 0 orphans, 0 rows with a null in session_date, duration_minutes or activity_type.

## Meaning

- `session_date`: when the session happened (inferred). For trial users, 9,242 sessions on the trial start date are timestamped earlier than `trials.started_at` of the same date, so activity on the first calendar day starts before the trial timestamp. Day one is therefore read by calendar date.
- `duration_minutes`: minutes in the session (inferred); the average for trial users in their first 14 days is about 10.5 across days.

## Notes for the next reader

- Large table: always aggregate in SQL and filter by `session_date`.
- Session counts per day for trial users fall from about 5,200 on day one to about 3,000 by day twelve; day thirteen has only 74 sessions in total, which is unexplained and not used.
- No official active-user definition exists (see `sessions.yaml`).
- Verdict: Proceed.
