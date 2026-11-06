# Beam data quirks

Things we know about our own tables. Check this list before reading a number as a finding.

- **Monthly price change.** The monthly plan price rose from $10 to $12 in March 2026. Monthly payments before and after that point differ for that reason, not because of a data problem.
- **Trials mature late.** Trials launched on 2026-08-01 and last 14 days. A trial's outcome is only known once it matures, so recent trials show `outcome` empty. Those are still open and must not be counted as failures.
- **Free plans never end.** Rows with `subscriptions.plan = 'free'` have no `end_date`. Exclude them from any churn or retention calculation.
- **Future end dates.** `subscriptions.end_date` can be in the future for scheduled cancellations. A subscription with a future end date is still active today.
- **Future start dates.** A few subscriptions have a `start_date` after today: they are scheduled starts, mostly trials that will convert when they mature. Exclude rows that start after the analysis date from any count of what has happened.
- **September 2025 spike.** September 2025 shows a signup spike of roughly five times a normal month, with a matching jump in the active base and in monthly actives. The daily-to-monthly active ratio falls from about .25 to about .21 at that point. Treat it as a known anomaly and say so whenever it enters a comparison.
- **Timestamps everywhere.** Dates in the exports are timestamps. Many `users.signup_date` and subscription dates carry midnight times, others carry real times; compare on dates, not on timestamps, unless the time of day matters.
- **One row per user in subscriptions.** `subscriptions.user_id` is unique, so each user has a single subscription row and there is no history of plan changes or earlier subscriptions; churn and tenure must be read from that row's `start_date` and `end_date`.
- **Payments start on 2025-01-01.** Earlier payments are not in the export, so a paid subscription that started before then can have no payment rows (11,122 subscriptions have payments, against 12,513 on a paid plan).
