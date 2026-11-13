---
name: profile
description: Profiles one of Beam's tables from the live PostgreSQL database (through the course-db MCP server) and writes what it learned into the knowledge folder, so the next analysis starts from facts instead of column names. Use before trusting a table you have not worked with, or when an analysis needs a table that has no note in knowledge/beam/.
user-invocable: true
allowed-tools: Read Bash Write Edit AskUserQuestion mcp__course-db__list_tables mcp__course-db__execute_sql
argument-hint: <table name, e.g. subscriptions or public.subscriptions>
---

You profile a table before anyone trusts it.

The failure you exist to prevent: a question about a table gets answered from its column names alone. The schema says `outcome`, so the analyst counts outcomes, and nobody notices that a fifth of them are empty because those trials have not finished yet. The names looked obvious. The data said something else.

Your job is to describe the table from the data, not from the schema, and to write down what you learned in `knowledge/beam/<table>.md` so the next analysis does not have to rediscover it.

## What this is NOT

- Not analysis. You describe the table; you do not answer a business question with it.
- Not cleaning. You never modify, filter or rewrite anything in the database or in `data/`.
- Not a schema dump. A list of column names and types is where you start, not what you deliver.
- Not a write path. You send the database SELECT statements and nothing else: no INSERT, UPDATE, DELETE, DROP, CREATE or ALTER, no temporary tables, no `SET` or `set_config`, no advisory locks, no `nextval`, no `pg_sleep`.

The database role cannot write to Beam's tables, views or sequences and cannot create objects in the project schemas. That is not the same as a read-only connection: the role can still create temporary tables and change session settings, and the session has no statement timeout. Keeping every query a bounded aggregate SELECT is your job, not the database's. Filter large tables by date, and run one statement per call.

## Act 1: Resolve the target (the gate)

1. The target is a table name, with or without its schema (`subscriptions` or `public.subscriptions`). A name with no schema is looked up in `public`.
2. Call `mcp__course-db__list_tables` with `output_format` set to `simple` and check that the table is in the list. If it is not, list the tables that do exist, say which one you could not find, and stop. Do not guess a near match.
3. Call `list_tables` again with `table_names` set to the target and read its columns and types. This is where you start, not what you deliver.

## Act 2: Profile from the data

Every query is one aggregate SELECT, one statement per call, through `mcp__course-db__execute_sql`. Never `SELECT *`, never raw rows. The sessions table has about 4.3 million rows, so always aggregate in SQL.

The cutoff is exclusive at 2026-09-25 00:00. Records dated 2026-09-24 at any time of day are eligible, and you never write `<= '2026-09-24'` on a timestamp.

1. **Keys.** The primary key is the YAML `primary_key`. Foreign keys are the YAML `relationships` where this table is the `from` side. Confirm them against the `list_tables` detailed output if it shows constraints. If it does not, say the keys come from the YAML only. With no YAML, say that no grain is declared and skip the grain comparison.
2. **Cutoff column and its type.** The cutoff column is the one the YAML examples filter on: `users.signup_date`, `subscriptions.start_date`, `payments.payment_date`, `sessions.session_date`, `trials.started_at`. For any other table, ask. Read the column's data type from `list_tables` and write the cutoff predicate to match:
   - `timestamp without time zone`: `"<col>" < TIMESTAMP '2026-09-25 00:00:00'`.
   - `date`: `"<col>" < DATE '2026-09-25'`.
   - `timestamp with time zone`: `"<col>" < TIMESTAMP WITH TIME ZONE '2026-09-25 00:00:00+00'`. This assumes the cutoff is 00:00 UTC, which is an assumption: state it in the note and tell the user.
   - Any other type: stop and ask. Never compare a text column to the cutoff.
   `cutoff_predicate` in `helpers/sql_results.py` returns the same predicates. Below, `<cut_pred>` means that predicate, and `<not_cut_pred>` means `NOT (<cut_pred>)`.
3. **P1, table facts (whole table).**

   ```sql
   SELECT COUNT(*) AS rows_total,
          COUNT(*) FILTER (WHERE <cut_pred>) AS rows_to_cutoff,
          COUNT(*) FILTER (WHERE <not_cut_pred>) AS rows_after_cutoff,
          COUNT(*) FILTER (WHERE "<cut>" IS NULL) AS rows_null_cut
   FROM <schema>.<table>;
   ```

4. **P2, per-column stats (rows before the cutoff, one output row).**

   ```sql
   SELECT COUNT(*) AS rows_in_scope,
          COUNT(*) FILTER (WHERE "c" IS NULL) AS c__nulls,
          ROUND(COUNT(*) FILTER (WHERE "c" IS NULL)::numeric / NULLIF(COUNT(*), 0), 4) AS c__null_rate,
          COUNT(DISTINCT "c") AS c__distinct,
          MIN("c") AS c__min, MAX("c") AS c__max      -- min and max for numeric, date and timestamp columns only
          -- repeat the block for every column
   FROM <schema>.<table>
   WHERE <cut_pred>;
   ```

   The `WHERE` clause is the exclusive cutoff. Rows whose cutoff column is NULL are outside it, which is why P1 counts them separately.
5. **P1 and P2 must reconcile.** `rows_to_cutoff + rows_after_cutoff + rows_null_cut = rows_total`, and P2's `rows_in_scope = rows_to_cutoff`. If either fails, stop and say so. `check_scope_counts` in `helpers/sql_results.py` states the same rule.
6. **P3, small value sets.** A column has a small value set when its P2 distinct count is 20 or fewer, and it is not a key and not a timestamp. For each, run:

   ```sql
   SELECT "c", COUNT(*) AS n FROM <schema>.<table> WHERE <cut_pred> GROUP BY "c" ORDER BY n DESC, "c" LIMIT 21;
   ```

   NULL counts as a value, and the counts must sum to `rows_in_scope`. Columns above 20 distinct values get the distinct count only.
7. **P4, grain (whole table).**

   ```sql
   SELECT COUNT(*) AS rows_total, COUNT(DISTINCT "<pk>") AS pk_distinct,
          COUNT(*) FILTER (WHERE "<pk>" IS NULL) AS pk_nulls
   FROM <schema>.<table>;
   ```

   Run the same shape for every other column the YAML grain or relationship notes call unique, such as `subscriptions.user_id` and `trials.user_id`.
8. If a query is slow or fails, run it per column. Never sample silently.

Every count, rate, range and distinct value you report comes from a query result. Never hand-roll a count. Rates such as the null rate are computed in the SQL itself. Report whole-table figures (P1, P4) and cutoff figures (P2, P3) separately, and label each as "whole table" or "to cutoff".

Look hardest at four things: the grain and the duplicate ids, the null rates, the date ranges and the small value sets.

## Act 2b: Keys, grain and relationships

Separate whole-table integrity (no cutoff, no filters) from anything that uses the cutoff. Everything here is whole table.

1. Build a table of every uniqueness claim in the YAML (`grain`, `primary_key`, relationship notes), the observed result, and match or mismatch. Add the YAML `row_count_observed` against `rows_total`.
2. For each YAML relationship where this table is the child, run the P5 statement. `<child>.<fk>` points at `<parent>.<k>`:

   ```sql
   SELECT (SELECT COUNT(*) FROM <child>) AS child_rows,
          (SELECT COUNT(*) FROM <child> WHERE "<fk>" IS NULL) AS null_fk,
          (SELECT COUNT(*) FROM <child> c WHERE c."<fk>" IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM <parent> p WHERE p."<k>" = c."<fk>")) AS orphan_fk,
          (SELECT COUNT(*) FROM <child> c JOIN <parent> p ON p."<k>" = c."<fk>") AS inner_joined_rows,
          (SELECT COUNT(*) - COUNT(DISTINCT "<k>") FROM <parent>) AS parent_key_duplicates;
   ```

   - A NULL foreign key and a non-NULL orphan are different findings. Report them separately.
   - Expected: `inner_joined_rows = child_rows - null_fk - orphan_fk` and `parent_key_duplicates = 0`. A larger `inner_joined_rows` is a fan-out.
3. For each YAML relationship where this table is the parent, run P7. For a relationship the YAML calls zero_or_one, or whose child column it calls unique, report the maximum children per parent and the parents with more than one:

   ```sql
   SELECT MAX(n) AS max_children, COUNT(*) FILTER (WHERE n > 1) AS parents_over_one
   FROM (SELECT "<fk>", COUNT(*) AS n FROM <child> GROUP BY "<fk>") g;
   ```

   For zero_or_many, report the parents with no child (`NOT EXISTS`) and the maximum children, as information only. Never call either an error.
4. Test only the relationships the YAML declares, using its names: `users_to_subscriptions`, `users_to_trials`, `users_to_sessions`, `subscriptions_to_users`, `subscriptions_to_payments`, `payments_to_subscriptions`, `trials_to_users` and `sessions_to_users`. `payments_to_users` is indirect: check it as the two hops. `trials_to_subscriptions` is `not_established`: run no join between them and assume no cardinality.
5. Any result that is not the expected one goes into the verdict as Caution, with the numbers.

## Act 3: Read what is already known

1. Read `knowledge/beam/quirks.md` and `knowledge/beam/corrections.md`.
2. If `knowledge/beam/<table>.yaml` exists, read it. It is the semantic layer for the table: grain, relationships, business rules, data coverage and limitations. Treat it as authoritative and never edit it.
3. If `knowledge/beam/<table>.md` already exists, read it. Anything a person wrote there is a fact you keep.
4. Match what the profile shows against the quirks and the YAML. A null rate or a date range that a quirk or the YAML already explains is expected. A live figure that differs from the YAML, such as a row count, a distinct count or a date range, or one that no quirk explains, is what the verdict is about. Name the difference with both numbers. When the two sources describe different populations, for example an old export and the live table, say so rather than calling one of them wrong. Never edit the YAML or `quirks.md`.

## Act 4: Write the note

1. Build the note from the query results of Acts 2 and 2b: the grain line, a columns table (name, type, null rate, distinct count), the date ranges and the low-cardinality values, with the cutoff stated, and an `## Integrity` section with the Act 2b table, each line labelled "whole table" or "to cutoff".
2. Write it to `knowledge/beam/<table>.md`, and write only the `.md`. If the note already exists, replace the generated sections (grain line, columns, date ranges, low-cardinality values, integrity) with the fresh output and keep every sentence a person wrote, in `## Meaning` and anywhere else.
3. Fill the `## Meaning` section: one line per column saying what it means for the business, inferred from its name, its values and the quirks. Mark each inference with "(inferred)" until a person confirms it. Where a quirk explains a column's nulls or range, say so and name the quirk.
4. State the grain in plain words under the generated grain line if the data supports it, marked as inferred.

## Act 5: Verdict

Give one verdict:

- **Proceed**: the grain is clear, and every null rate and duplicate is explained.
- **Caution**: something is unexplained. Name it.
- **Blocked**: the table cannot answer questions at this grain. Say why.

Follow it with the reasons, one line each.

## Act 6: Propose new quirks

If the profile shows something that is not in `quirks.md`, propose the exact sentence you would add and ask with AskUserQuestion whether to append it. Append only on a yes. On a no, leave `quirks.md` untouched and keep the point in the verdict.

## Deliver

The note is the deliverable. On the CLI, show only:

- The verdict and its reasons, one line each.
- The path of the note you wrote or updated.
- Any quirk you proposed and what was decided.

Keep it under fifteen lines. Do not paste the note into the CLI.

## Principles

- **Describe from the data.** The schema tells you what a column was meant to hold. The profile tells you what it holds.
- **The database counts, you read.** Every number comes from a SQL aggregate run through `course-db`. None comes from prose arithmetic, and no query is ever anything but a read-only SELECT.
- **The YAML is authoritative.** The semantic layer in `knowledge/beam/<table>.yaml` is read, never edited. Where the live data disagrees with it, say so.
- **The cutoff is exclusive and typed.** `< 2026-09-25 00:00` in the column's own type, so 2026-09-24 is in. Time zones are stated, never guessed silently.
- **Whole table is not to cutoff.** Integrity checks use the whole table. Statistics use the cutoff. Label every figure.
- **The connection is not read-only.** Only the database privileges stop writes to Beam's tables. The SELECT-only, bounded-query rule is yours to keep.
- **Separate calls do not share a snapshot.** The session runs at read committed, so each statement sees the data as of its own start. Figures from different queries may not describe one identical database state, and you do not claim they do.
- **Write it down once.** What you learn goes into the note, so no later analysis pays for it again.
- **A guess is labelled a guess.** Every meaning you infer says "(inferred)" until a person confirms it.
- **Keep what people wrote.** Updating a note never deletes a fact someone added by hand.
- **Quirks are appended only with consent.** You propose the sentence; a person decides.
- **The note is for the next reader, not this run.** Write it so someone opening it cold can trust the table or know exactly why not.
