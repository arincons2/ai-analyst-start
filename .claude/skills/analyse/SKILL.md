---
name: analyse
description: Answers a framed business question about Beam's data with SQL aggregates from the live PostgreSQL database (through the course-db MCP server) saved in the run folder, and one pandas script that can be rerun on them, where every headline number carries its comparison. Use after a question has been framed, when it is time to compute the answer. Reads the frame note in the run folder under outputs/ and writes the queries, the script, result tables and charts next to it.
user-invocable: true
allowed-tools: Read Bash Write Edit Glob AskUserQuestion mcp__course-db__list_tables mcp__course-db__execute_sql
argument-hint: "[path to the run folder under outputs/]"
---

You answer the framed question with code that can be rerun.

The failure you exist to prevent: a confident number with no baseline, or a number that came out of a chain of arithmetic written in prose that nobody can check. "Trial conversion is 50%" tells a reader nothing until it says 50% against what. And a number computed in the conversation cannot be rerun, reviewed or corrected. The answer might even be right. Nobody can tell.

So the model reasons and the code computes. You decide what to compute and what to compare it with. The database aggregates, and a script in the run folder does every rate, comparison, check and chart.

The data lives in PostgreSQL, and only you can reach it, through the `course-db` MCP tools. A script cannot call them. So the work is split in two: you run SQL and save the aggregate results into the run folder, and `analysis.py` reads those saved results. Anyone can rerun the script from the saved results, and the saved SQL shows exactly where they came from.

## What this is NOT

- Not the brief. You report numbers on the CLI; the Deliver step writes the brief.
- Not the review. You run the checks your plan needs; the Validate step reviews the work.
- Not a notebook. The output is one script that runs top to bottom.
- Not a window into raw rows. You never print raw rows into the conversation; every query returns aggregates only and the script prints aggregates only.
- Not a data editor. You never modify anything in `data/`, and you send the database SELECT statements and nothing else: no INSERT, UPDATE, DELETE, DROP, CREATE or ALTER, no temporary tables, no `SET` or `set_config`, no advisory locks, no `nextval`, no `pg_sleep`.

The database role cannot write to Beam's tables, views or sequences and cannot create objects in the project schemas. That is not the same as a read-only connection: the role can still create temporary tables and change session settings, and the session has no statement timeout. Keeping every query a bounded aggregate SELECT is your job, not the database's. Filter large tables by date, and run one statement per call.

## The rules

These hold in every act.

- **A number alone is not a finding.** Every headline number carries a comparison: a prior period, a segment, or a benchmark.
- **If no comparison exists, say "no comparison available".** Never invent a baseline to fill the gap.
- **Segment before you average** when the frame or a quirk suggests groups behave differently. An average over groups that move in opposite directions hides both.
- **Percentage points and relative percent are different numbers.** A rate going from 40% to 44% is up 4 percentage points and up 10% relative. Name which one you mean, every time.
- **Never trust a period that has not closed.** A month still in progress, or a trial still inside its 14 days, is not a result yet.

## Act 1: Find the question (the gate)

1. Find the run folder. Use the argument if given. Otherwise use the newest folder under `outputs/` that holds a `frame.md`.
2. Read `frame.md` for the question, the decision it informs, the audience, and what would change the decision.
3. If there is no frame note, ask with AskUserQuestion for the question and the decision it informs. Then write `outputs/<YYYY-MM-DD>_<slug>/frame.md` yourself before anything else, with the same fields the Frame step writes: question, decision, audience, what would change the decision, and the tables likely involved. Get the date with `date +%F`; never guess it.
4. Never proceed on a guessed question. You may confirm a question you infer; you may not assume one.

## Act 2: Load the context

1. Read `knowledge/beam/quirks.md`, `knowledge/beam/corrections.md`, and for each table involved both `knowledge/beam/<table>.md` and `knowledge/beam/<table>.yaml`.
2. The YAML is the semantic layer and is authoritative for business definitions: grain, relationships, measures, filters and the data cutoff. Use its definitions as written and never edit it. Where it marks a definition unresolved (the official churn definition, revenue, MRR, active user, trial-to-paid conversion across `trials` and `subscriptions`), say so and do not present a number as official. A figure on an unresolved definition is labelled as an operational or exploratory measure.
3. If a table involved has no note, run the profile skill on it first: read `.claude/skills/profile/SKILL.md` and follow it, then come back here.
4. Say in one line which quirks apply to this question, which YAML definitions you will use, and which corrections, if any.
5. For each join the question needs, write down the expected cardinality from the YAML relationships (`payments_to_subscriptions` and `subscriptions_to_users` are many to one, `users_to_subscriptions` and `users_to_trials` are zero or one, `subscriptions_to_payments` and `users_to_sessions` are zero or many, `payments_to_users` is indirect through `subscriptions`). A relationship the YAML marks `not_established`, such as `trials` to `subscriptions`, cannot be used as an official join. Never assume a cardinality the YAML does not state.
6. For trial conversion questions, the official measure is the YAML `conversion_rate`: converted over converted plus canceled, so trials with no outcome yet are out of the denominator. You also compute a sensitivity comparison on matured trials only (`ends_at < TIMESTAMP '2026-09-25'`). Never substitute one definition for the other.

## Act 3: Plan

Write three to six bullets describing the computation. The plan must say:

- What each headline number is and the comparison it will carry (prior period, segment or benchmark), or that none exists.
- How each applicable quirk is handled. For example: matured trials only; free plans excluded from churn; scheduled starts after today excluded; the September 2025 anomaly named if it enters a comparison.
- Which segments you will split by and why.
- Which queries you will run, what each returns, and at what grain.
- For each join, the expected cardinality and the expected number of rows lost in scope and why (a parent filter, NULL foreign keys, orphans), using the whole-table numbers from the preflight. A join with no stated expectation is not ready to run.
- Which checks the script will run.

Show the plan before running anything.

If the question needs session-based active users (DAU or MAU from `sessions`), ask with AskUserQuestion whether the user accepts the operational definitions in `sessions.yaml` before computing, and label the results as session-based operational metrics, not official Beam active-user figures.

## Act 4: Query, then write and run the script

### Step 1: Get the aggregates

The commands below live in `helpers/sql_results.py`. Run them from the repo root.

1. **Write the query.** Put each result's SELECT into `outputs/<run>/queries.sql` under three header lines: `-- result: <name>`, `-- file: data/<name>.csv` and `-- types: col=type, ...` (types: `int`, `decimal`, `date`, `timestamp`, `bool`, `text`, in column order). Write it before you run anything. The file is the audit trail, so keep it in step with what you ran.
2. **What a query may contain.** One SELECT or WITH statement that returns aggregates, never raw rows: grouped counts and sums, never `SELECT *`, never one row per user, subscription, payment or session. The `sessions` table has about 4.3 million rows, so always aggregate it in SQL (by day, week or month) and filter by `session_date`.
3. **The cutoff.** Keep rows dated before the exclusive bound 2026-09-25 00:00, so 2026-09-24 at any time of day is in and you never write `<= '2026-09-24'` on a timestamp. Match the predicate to the column's data type: `"<col>" < TIMESTAMP '2026-09-25 00:00:00'` for `timestamp without time zone`, `"<col>" < DATE '2026-09-25'` for `date`, and `"<col>" < TIMESTAMP WITH TIME ZONE '2026-09-25 00:00:00+00'` for `timestamp with time zone`. That last one assumes 00:00 UTC, so state the assumption in the plan and the report. For any other type, stop and ask. Name the cutoff in every result, and exclude September 2026 from closed-month comparisons. Take business definitions (the active base, churn numerator and denominator, conversion rate) from the YAML measures and examples, and adapt them rather than inventing new ones.
4. **Result formats.** Return dates and timestamps as text with `to_char` (`'YYYY-MM-DD'` and `'YYYY-MM-DD HH24:MI:SS'`), and rates as `ROUND(x::numeric, 6)`, never float, so the SQL text and the saved CSV agree.
5. **Cap.** Each result has between 1 and 500 rows. A result that would have more is tightened or restated, never saved partially. A result with no rows is a failure unless the plan said it would be empty, in which case a separate count query shows 0.
6. **Wrap and run.** Print the single statement with `poetry run python -m helpers.sql_results wrap outputs/<run> <name> --order-by '"col1", "col2"'`. It wraps your SELECT so that the same statement also returns its own controls on row 1 as `_ctl_*` columns: the row count, sums, null and distinct counts, minimums and maximums, and a row checksum. Run exactly that statement with `mcp__course-db__execute_sql`, one statement per call.
7. **Save the raw output.** Paste the tool output exactly as returned into `outputs/<run>/data/_raw/<name>.json`. Change nothing.
8. **Ingest.** Run `poetry run python -m helpers.sql_results ingest outputs/<run> <name>`. It writes `data/<name>.csv` (NULL is `\N`), registers the result and its controls in `data/_manifest.json`, and verifies it. It never overwrites a saved result. Money amounts stay as returned, exactly, with their scale.
9. **Preflight and diagnostics.** The same way, as registered results: for every join in the plan, the whole-table integrity statement (no cutoff and no filters; child rows, NULL foreign keys, non-NULL orphans, rows after an inner join to the parent, duplicate parent keys) and, for a zero or one relationship, the maximum children per parent. Keep the NULL foreign keys and the orphans apart. For trial conversion, one maturity diagnostic: trial counts by `ends_at < TIMESTAMP '2026-09-25'` and by `outcome` (null, converted, canceled). Any join result that is not the one the YAML predicts stops the run unless you can explain it in the plan.
10. **Scope checks.** An analytical statement that joins also returns, from the same statement, the child rows in scope before the join, the rows after it, and the measure total before and after. They must match the expectation in the plan, and any unexplained loss or gain stops the run.
11. **Drift probe.** At the end of this step, re-count `rows_total` for every table you used and compare with the preflight. Record both numbers. If they differ, say that the results mix database states.
12. **Gate.** Run `poetry run python -m helpers.sql_results verify outputs/<run>`. If it exits with a non-zero code, fix the file or the query and run it again. Do not write or run `analysis.py` until it passes. If completeness cannot be established, say so and stop.

Separate SQL calls do not necessarily share a database snapshot. The session runs at read committed, so each statement sees the data as of its own start. The drift probe compares row counts before and after the queries; it cannot detect changed values, and it does not prove that all queries used an identical database state. The controls guarantee consistency only between a result's rows and its own controls, because those come from one statement. When parts and a total must agree, get them from one statement, for example with `GROUPING SETS`.

The row checksum is MD5. It is an accidental-corruption check, not a security guarantee and not proof of data provenance. It cannot tell a faithful copy from rows and a checksum that were both invented.

### Step 2: Write and run the script

Write one script at `outputs/<run>/analysis.py`:

1. Begin with an `INPUTS` spec and call `validate_inputs(run_dir, INPUTS, tables)` from `helpers.sql_results` before any calculation. The spec lists, per result: `not_null` columns, `key` columns that must be unique together, `cutoff_cols` that must lie before the exclusive cutoff, `closed_month_cols` that must not pass the last closed month, and `rates` as (numerator, denominator, rate, decimals). The helper reads the cutoff from `data_coverage.data_cutoff` in each involved table's YAML (they must all agree) and uses the day after it, 2026-09-25. It verifies the saved files again, types every column strictly, collects every failure, prints them all and exits with code 2 before any number is computed. The script never uses `errors="coerce"`, `fillna`, `dropna` or a `try`/`except` around a validation, and a failure is never replaced by a default.
2. Read only the saved results in `outputs/<run>/data/`. The script does not read `data/*.csv` and does not connect to the database.
3. Do the calculations in the script: rates, differences, comparisons and segment totals, from the saved aggregates. Compute rates from the integer counts and keep them as proportions.
4. Reconcile with the helpers in `helpers.sql_results`, and name the tolerance each time:
   - **Integer counts** (segment totals, rows before and after a join): `reconcile_counts`, exact equality.
   - **Money** (stored `numeric(10,2)` amounts): `reconcile_money`, exact Decimal arithmetic. Exact equality in cents is required for sums of stored amounts. A tolerance of 0.005 per rounded value is allowed only where the SQL or script applied an explicit `ROUND(x, 2)`, and the spec names which parts were rounded (add one more if the total was rounded too). Never reconcile money after scaling or float conversion.
   - **Rates and proportions:** `check_rate` recomputes the rate from its counts and compares at 1e-9 if unrounded, or 0.5 times 10 to the minus d if the SQL rounded to d decimals. The rate must lie in [0, 1] with the numerator not above the denominator. Rates are never summed.
5. Use `helpers.checks` only where it fits aggregates. `parts_sum_to_total` works on numbers: call it with `tolerance=0` for counts, because its default allows half a percent. `join_fan_out` needs row-level frames and does not apply to saved aggregates; the SQL scope checks replace it. `date_boundaries` works at day grain but flags month-start values as a partial month, so for months use the `closed_month_cols` check. `matured_only` needs a per-row end date and defaults to the real today, so maturity is computed in SQL with `ends_at < TIMESTAMP '2026-09-25'` instead.
6. For trial conversion, report the YAML conversion rate as the headline and the matured-only rate beside it as a sensitivity comparison, with the difference in percentage points and both populations. When the difference is 1 percentage point or more, ask with AskUserQuestion for a decision before the run continues. Never swap one definition for the other.
7. Save each result table to `outputs/<run>/data/<name>.csv`, under a name that is not a registered input. Never overwrite a saved result.
8. Make charts only through `helpers.charts`: `bar` or `line`, then `save` into `outputs/<run>/charts/<name>.png`. Every title states the takeaway the chart proves, not a label. Pass rates as proportions and set `value_fmt` to show them correctly, for example `value_fmt="{:.1%}"` on `bar` (labels on the bars) or `value_fmt="{:.0%}"` on `line` (y axis ticks). Never rescale the values to get a percentage.
9. Print the headline numbers, each with its comparison, and the check summaries. Print aggregates only.

Run it from the repo root with `poetry run python outputs/<run>/analysis.py`. If it fails, fix it and rerun until it runs clean. A validation failure stops the analysis: fix the query or the saved result and rerun, and report no number from a run that failed. If a later check fails, decide whether it changes the answer and fix the query or the script, or report it. If the data cannot answer the question, say so plainly and stop.

## Act 5: Report

On the CLI, report:

- Each headline number with its comparison, stated as percentage points or relative percent, saying which.
- What was segmented and why.
- Which checks ran and what each said.
- Which definitions were unresolved or operational, and how the numbers are labelled.
- The cutoff applied (exclusive at 2026-09-25 00:00, so 2026-09-24 is in) and any time zone assumption.
- The completeness result for every saved result, the drift probe numbers, and the join checks.
- For trial conversion, the YAML rate, the matured-only rate and the gap in percentage points.
- What was not checked, including that separate calls do not necessarily share one snapshot, that the saved results are a snapshot of the database at query time, and that MD5 is an accidental-corruption check, not proof of provenance.
- The path of the run folder.

Keep it under twenty lines. No brief here; the Deliver step writes the brief.

## Principles

- **The question is the gate.** No frame note, no analysis. Ask; never guess.
- **The model reasons, the code computes.** Every number comes out of a saved SQL aggregate or `analysis.py`. None comes from prose arithmetic.
- **The SQL is the audit trail.** Every query is in `queries.sql`, SELECT-only, aggregate-only, with the cutoff applied. The connection itself is not read-only, so this rule is yours to keep.
- **No complete result, no analysis.** A result is used only after `ingest` and `verify` pass, and `analysis.py` stops on any failed validation.
- **Exact where it is exact.** Counts and stored amounts reconcile exactly. A tolerance needs a named rounding operation.
- **Never silently substitute a definition.** The YAML measure is official; a sensitivity comparison sits beside it and is labelled.
- **The YAML defines the business terms.** Use its measures as written, and label anything it marks unresolved.
- **Always compare.** A number without its comparison is not a finding. When there is no comparison, say so.
- **Quirks first.** Read what is known about the tables before computing anything, and say how each applicable quirk was handled.
- **Closed periods only.** An open month or an unmatured trial is not a result.
- **Name the unit of change.** Percentage points or relative percent, never left for the reader to guess.
- **Rerunnable or it did not happen.** Anyone can run the script again on the saved results and get the same tables, charts and numbers.
- **Say what you did not check.** A gap you name is safer than one a reader finds later.
