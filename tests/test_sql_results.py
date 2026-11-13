"""Tests for helpers/sql_results.py. Run from the repo root:

    poetry run python -m unittest discover -s tests -v

GOLDEN_ROWS and GOLDEN_MD5 come from one real execute_sql call on synthetic data (no table was
read): the database computed the checksum with the same canonical encoding build_result_sql
emits, and the rows were copied from the tool output. They pin the Python encoding to the SQL.
"""

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from helpers import sql_results as sr

COLUMNS = ["k", "t", "n", "b", "d"]
GOLDEN_MD5 = "ff447c36e19c9d0577468a4a4a42440e"
GOLDEN_ROWS = [
    ["1", "a", "12.50", "true", "2026-08-01"],
    ["2", "", "0.10", "false", "2026-08-02"],
    ["3", None, None, None, None],
    ["4", "dup", "1.00", "true", "2026-08-03"],
    ["4", "dup", "1.00", "true", "2026-08-03"],
    ["5", "a|b", "2.5", "true", "2026-08-04"],
    ["6", "x\x1fy", "3", "false", "2026-08-05"],
    ["7", "p\x1eq", "4", "false", "2026-08-06"],
    ["8", 'say "hi", ok\nline2', "5", "true", "2026-08-07"],
    ["9", "é日本😀", "6.000", "true", "2026-08-08"],
    ["10", "V1:a", "7", "true", "2026-08-09"],
    ["11", "N", "8", "true", None],
    ["12", "ab", "9", "true", "c"],
    ["13", "a", "9", "true", "bc"],
    ["14", "\\N", "10", "true", "2026-08-10"],
    ["15", "tab\tx\ry", "11", "false", "2026-08-11"],
]
TYPES = [("k", "int"), ("t", "text"), ("n", "decimal"), ("b", "bool"), ("d", "text")]
SELECT = "SELECT k, t, n, b, d FROM synthetic ORDER BY k"


def copy_rows(rows=None):
    return [r[:] for r in (rows if rows is not None else GOLDEN_ROWS)]


class CanonicalChecksumTests(unittest.TestCase):
    def test_python_matches_the_database_checksum(self):
        self.assertEqual(sr.canonical_md5(COLUMNS, GOLDEN_ROWS), GOLDEN_MD5)

    def test_csv_round_trip_keeps_values_and_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = sr.write_result_csv(Path(tmp) / "r.csv", COLUMNS, GOLDEN_ROWS)
            header, rows = sr.read_result_csv(path)
        self.assertEqual(header, COLUMNS)
        self.assertEqual(rows, GOLDEN_ROWS)
        self.assertEqual(sr.canonical_md5(header, rows), GOLDEN_MD5)

    def test_row_order_is_ignored_but_duplicates_count(self):
        self.assertEqual(sr.canonical_md5(COLUMNS, GOLDEN_ROWS[::-1]), GOLDEN_MD5)
        self.assertNotEqual(sr.canonical_md5(COLUMNS, GOLDEN_ROWS[:3] + GOLDEN_ROWS[4:]), GOLDEN_MD5)
        self.assertNotEqual(sr.canonical_md5(COLUMNS, GOLDEN_ROWS + [GOLDEN_ROWS[3]]), GOLDEN_MD5)
        self.assertNotEqual(sr.canonical_md5(COLUMNS, GOLDEN_ROWS[:-1]), GOLDEN_MD5)

    def test_null_and_empty_string_are_distinct(self):
        rows = copy_rows()
        rows[1][1] = None
        self.assertNotEqual(sr.canonical_md5(COLUMNS, rows), GOLDEN_MD5)
        rows = copy_rows()
        rows[2][1] = ""
        self.assertNotEqual(sr.canonical_md5(COLUMNS, rows), GOLDEN_MD5)
        rows = copy_rows()
        rows[10][1] = None   # the text "N" versus NULL
        self.assertNotEqual(sr.canonical_md5(COLUMNS, rows), GOLDEN_MD5)

    def test_csv_keeps_null_empty_and_backslash_text_distinct(self):
        values = [[None], [""], ["\\N"], ["\\\\N"], ["\\x"]]
        with tempfile.TemporaryDirectory() as tmp:
            path = sr.write_result_csv(Path(tmp) / "r.csv", ["a"], values)
            _, back = sr.read_result_csv(path)
        self.assertEqual(back, values)

    def test_boundaries_cannot_be_confused(self):
        enc = sr.canonical_md5
        self.assertNotEqual(enc(["x", "y"], [["ab", "c"]]), enc(["x", "y"], [["a", "bc"]]))
        self.assertNotEqual(enc(["x", "y"], [["a", "b"]]), enc(["x"], [["a"], ["b"]]))
        self.assertNotEqual(enc(["x", "y"], [["a\x1f", "b"]]), enc(["x", "y"], [["a", "\x1fb"]]))
        self.assertNotEqual(enc(["x"], [["V1:a"]]), enc(["x"], [["a"]]))

    def test_header_and_column_count_matter(self):
        self.assertNotEqual(sr.canonical_md5(["k", "t", "n", "b", "x"], GOLDEN_ROWS), GOLDEN_MD5)
        self.assertNotEqual(sr.canonical_md5(COLUMNS[:4], [r[:4] for r in GOLDEN_ROWS]), GOLDEN_MD5)

    def test_single_cell_changes_are_detected(self):
        rows = copy_rows()
        rows[0][2] = "12.5"        # decimal scale is part of the value
        self.assertNotEqual(sr.canonical_md5(COLUMNS, rows), GOLDEN_MD5)
        rows = copy_rows()
        rows[8][1] = rows[8][1].replace("line2", "line3")
        self.assertNotEqual(sr.canonical_md5(COLUMNS, rows), GOLDEN_MD5)
        rows = copy_rows()
        rows[5][0], rows[6][0] = rows[6][0], rows[5][0]
        self.assertNotEqual(sr.canonical_md5(COLUMNS, rows), GOLDEN_MD5)

    def test_swapping_identical_rows_changes_nothing(self):
        rows = copy_rows()
        rows[3], rows[4] = rows[4], rows[3]
        self.assertEqual(sr.canonical_md5(COLUMNS, rows), GOLDEN_MD5)

    def test_csv_writer_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = sr.write_result_csv(Path(tmp) / "r.csv", ["a"], [["1"]])
            with self.assertRaises(FileExistsError):
                sr.write_result_csv(path, ["a"], [["2"]])


class BuilderAndLintTests(unittest.TestCase):
    def setUp(self):
        self.sql = sr.build_result_sql(
            SELECT, COLUMNS, '"k", "t", "d"', sums=["n"], distinct=["t"], nulls=["t"], minmax=["d"]
        )

    def test_statement_uses_the_canonical_encoding(self):
        for fragment in ('COLLATE "C"', "char_length(u.v)", "WITH ORDINALITY", "md5('H'",
                         "CASE WHEN u.v IS NULL THEN 'N'", "'V' || char_length(u.v)", "'R' || char_length(fe.f)"):
            self.assertIn(fragment, self.sql)

    def test_every_column_is_in_the_row_encoding(self):
        for c in COLUMNS:
            self.assertIn(f'r."{c}"::text', self.sql)
            self.assertIn(f"'{c}'", self.sql)

    def test_controls_are_returned_on_row_one_only(self):
        for name in ("n_rows", "md5_rows", "sum_n", "distinct_t", "nulls_t", "min_d", "max_d"):
            self.assertIn(f'AS "_ctl_{name}"', self.sql)
        self.assertEqual(self.sql.count("CASE WHEN n._rn = 1 THEN"), 7)
        self.assertEqual(self.sql.count(";"), 0)

    def test_builder_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            sr.build_result_sql("DELETE FROM t", ["a"], '"a"')
        with self.assertRaises(ValueError):
            sr.build_result_sql("SELECT 1 AS a; SELECT 2 AS b", ["a"], '"a"')
        with self.assertRaises(ValueError):
            sr.build_result_sql("SELECT 1 AS _rn", ["_rn"], '"_rn"')
        with self.assertRaises(ValueError):
            sr.build_result_sql("SELECT 1 AS a", ["a", "a"], '"a"')
        with self.assertRaises(ValueError):
            sr.build_result_sql("SELECT 1 AS a", ["a"], '"a"', sums=["missing"])

    def test_quoted_names_are_escaped(self):
        sql = sr.build_result_sql('SELECT 1 AS "we""ird"', ['we"ird'], '1')
        self.assertIn('r."we""ird"::text', sql)

    def test_lint_accepts_read_only_selects(self):
        self.assertTrue(sr.lint_read_only("SELECT COUNT(*) FROM public.users WHERE signup_date < TIMESTAMP '2026-09-25';"))
        self.assertTrue(sr.lint_read_only("-- note\nWITH a AS (SELECT 1) SELECT * FROM a"))
        self.assertTrue(sr.lint_read_only("SELECT 'update' AS word"))

    def test_lint_rejects_writes_and_side_effects(self):
        for bad in (
            "INSERT INTO t VALUES (1)", "UPDATE t SET a = 1", "DELETE FROM t", "DROP TABLE t",
            "CREATE TEMP TABLE x AS SELECT 1", "SELECT 1 INTO x", "SELECT set_config('a','b',false)",
            "SELECT pg_advisory_lock(1)", "SELECT pg_try_advisory_lock(1)", "SELECT nextval('s')",
            "SELECT pg_sleep(5)", "SELECT 1; SELECT 2", "TRUNCATE t", "SET statement_timeout = 0",
            "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
        ):
            self.assertFalse(sr.lint_read_only(bad), bad)

    def test_query_identity_ignores_comments_and_whitespace_only(self):
        a = "-- result: r\nSELECT  1 AS a  -- note\n;"
        b = "SELECT 1 AS a"
        self.assertEqual(sr.query_sha(a.split("\n", 1)[1]), sr.query_sha(b))
        self.assertNotEqual(sr.query_sha("SELECT 2 AS a"), sr.query_sha(b))


class CutoffTests(unittest.TestCase):
    def write_yaml(self, root, table, cutoff):
        (Path(root) / f"{table}.yaml").write_text(
            f'table: {table}\ndata_coverage:\n  data_cutoff: "{cutoff}"\n', encoding="utf-8")

    def test_exclusive_bound_is_the_day_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            for t in ("users", "payments"):
                self.write_yaml(tmp, t, "2026-09-24")
            self.assertEqual(sr.cutoff_exclusive(["users", "payments"], tmp).isoformat(), "2026-09-25")

    def test_disagreeing_yaml_cutoffs_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_yaml(tmp, "users", "2026-09-24")
            self.write_yaml(tmp, "payments", "2026-09-23")
            with self.assertRaises(SystemExit):
                sr.cutoff_exclusive(["users", "payments"], tmp)

    def test_the_real_knowledge_yaml_files_agree(self):
        repo = Path(__file__).resolve().parents[1] / "knowledge" / "beam"
        tables = ["users", "subscriptions", "payments", "sessions", "trials"]
        self.assertEqual(sr.cutoff_exclusive(tables, repo).isoformat(), "2026-09-25")

    def test_last_closed_month(self):
        from datetime import date
        self.assertEqual(sr.last_closed_month_start(date(2026, 9, 25)), date(2026, 8, 1))
        self.assertEqual(sr.last_closed_month_start(date(2026, 10, 1)), date(2026, 9, 1))
        self.assertEqual(sr.last_closed_month_start(date(2026, 1, 15)), date(2025, 12, 1))
        self.assertEqual(sr.last_closed_month_start(date(2026, 3, 1)), date(2026, 2, 1))

    def test_predicate_follows_the_column_type(self):
        from datetime import date
        cut = date(2026, 9, 25)
        pred, _ = sr.cutoff_predicate("started_at", "timestamp without time zone", cut)
        self.assertEqual(pred, '"started_at" < TIMESTAMP \'2026-09-25 00:00:00\'')
        pred, _ = sr.cutoff_predicate("d", "date", cut)
        self.assertEqual(pred, '"d" < DATE \'2026-09-25\'')
        pred, note = sr.cutoff_predicate("ts", "timestamp with time zone", cut)
        self.assertEqual(pred, '"ts" < TIMESTAMP WITH TIME ZONE \'2026-09-25 00:00:00+00\'')
        self.assertIn("UTC", note)
        self.assertNotIn("<=", pred)
        with self.assertRaises(ValueError):
            sr.cutoff_predicate("c", "character varying(255)", cut)

    def test_p1_p2_reconciliation(self):
        f = sr.Failures()
        sr.check_scope_counts(f, "t", rows_total=100, rows_to_cutoff=90, rows_after_cutoff=7,
                              rows_null_cut=3, rows_in_scope=90)
        self.assertEqual(f.items, [])
        sr.check_scope_counts(f, "t", 100, 90, 7, 3, rows_in_scope=89)
        sr.check_scope_counts(f, "t", 100, 90, 7, 2, rows_in_scope=90)
        self.assertEqual(len(f.items), 2)


class ReconciliationTests(unittest.TestCase):
    def test_counts_are_exact(self):
        f = sr.Failures()
        sr.reconcile_counts(f, "ok", [3, 4], 7)
        self.assertEqual(f.items, [])
        sr.reconcile_counts(f, "off by one", [3, 4], 8)
        self.assertEqual(len(f.items), 1)
        for bad in ([[3.0], 3], [[True], 1]):
            with self.assertRaises(TypeError):
                sr.reconcile_counts(sr.Failures(), "x", *bad)

    def test_money_is_exact_unless_rounding_is_named(self):
        f = sr.Failures()
        sr.reconcile_money(f, "exact", ["10.10", "20.20"], "30.30")
        self.assertEqual(f.items, [])
        sr.reconcile_money(f, "one cent off", ["10.10", "20.20"], "30.31")
        self.assertEqual(len(f.items), 1)
        g = sr.Failures()      # two parts each rounded to cents: up to 0.01 apart
        sr.reconcile_money(g, "rounded", ["10.10", "20.20"], "30.31", rounded_parts=2)
        self.assertEqual(g.items, [])
        sr.reconcile_money(g, "too far", ["10.10", "20.20"], "30.32", rounded_parts=2)
        self.assertEqual(len(g.items), 1)
        h = sr.Failures()      # a rounded total counts as one more rounded value
        sr.reconcile_money(h, "total rounded", ["10.10"], "10.1049", rounded_parts=0, total_rounded=True)
        self.assertEqual(h.items, [])

    def test_money_rejects_floats_and_bad_rounding_counts(self):
        with self.assertRaises(TypeError):
            sr.reconcile_money(sr.Failures(), "x", [1.1], "1.1")
        with self.assertRaises(ValueError):
            sr.reconcile_money(sr.Failures(), "x", ["1.00"], "1.00", rounded_parts=2)

    def test_rates(self):
        f = sr.Failures()
        sr.check_rate(f, "exact", 828, 1655, Decimal(828) / Decimal(1655))
        sr.check_rate(f, "rounded 4dp", 828, 1655, Decimal("0.5003"), decimals=4)
        self.assertEqual(f.items, [])
        sr.check_rate(f, "wrong", 828, 1655, Decimal("0.5100"), decimals=4)
        sr.check_rate(f, "num above den", 5, 4, Decimal("1.25"))
        sr.check_rate(f, "zero den", 0, 0, Decimal("0"))
        self.assertEqual(len(f.items), 3)   # one failure each; invalid inputs stop at once


# Controls returned by one real execute_sql call of build_result_sql(...) on the synthetic rows.
DATABASE_CONTROLS = {
    "n_rows": 16,
    "md5_rows": GOLDEN_MD5,
    "sums": {"n": "89.100", "k": "124"},
    "distinct": {"t": 13},
    "nulls": {"t": 1},
    "min": {"k": "1"},
    "max": {"k": "15"},
}


def make_run(tmp, rows=None, select=SELECT, types=None, controls_override=None):
    """Build a run folder the way the Analyse step would: queries.sql, one CSV, one manifest."""
    rows = copy_rows(rows)
    run = Path(tmp)
    (run / "data").mkdir(parents=True, exist_ok=True)
    (run / "queries.sql").write_text(f"-- result: synth\n-- file: data/synth.csv\n{select};\n", encoding="utf-8")
    sr.write_result_csv(run / "data" / "synth.csv", COLUMNS, rows)
    nums = [Decimal(r[2]) for r in rows if r[2] is not None]
    controls = {
        "n_rows": len(rows),
        "md5_rows": sr.canonical_md5(COLUMNS, rows),
        "sums": {"n": str(sum(nums, Decimal(0))), "k": str(sum(int(r[0]) for r in rows))},
        "nulls": {"t": sum(r[1] is None for r in rows)},
        "distinct": {"t": len({r[1] for r in rows if r[1] is not None})},
        "min": {"k": "1"}, "max": {"k": "15"},
    }
    sr.register_result(run, "synth", types or TYPES, controls_override or controls)
    return run


class VerifyTests(unittest.TestCase):
    def test_a_faithful_result_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(sr.verify(make_run(tmp)).items, [])

    def test_controls_computed_by_the_database_pass_against_the_saved_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, controls_override=DATABASE_CONTROLS)
            self.assertEqual(sr.verify(run).items, [])

    def rewrite(self, run, mutate):
        header, rows = sr.read_result_csv(run / "data" / "synth.csv")
        mutate(rows)
        (run / "data" / "synth.csv").unlink()
        sr.write_result_csv(run / "data" / "synth.csv", header, rows)

    def test_a_truncated_result_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            self.rewrite(run, lambda rows: rows.pop())
            self.assertTrue(any("rows saved" in m for m in sr.verify(run).items))

    def test_a_dropped_duplicate_row_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            self.rewrite(run, lambda rows: rows.pop(3))
            self.assertTrue(sr.verify(run).items)

    def test_a_changed_cell_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            self.rewrite(run, lambda rows: rows[8].__setitem__(1, "say hi"))
            self.assertTrue(any("checksum" in m for m in sr.verify(run).items))

    def test_null_turned_into_empty_string_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            self.rewrite(run, lambda rows: rows[2].__setitem__(1, ""))
            self.assertTrue(any("NULLs" in m or "checksum" in m for m in sr.verify(run).items))

    def test_a_changed_sum_is_caught_before_the_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            self.rewrite(run, lambda rows: rows[0].__setitem__(2, "12.51"))
            messages = sr.verify(run).items
            self.assertTrue(any("sum" in m for m in messages))

    def test_an_edited_query_fails_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            (run / "queries.sql").write_text(
                "-- result: synth\nSELECT k, t, n, b, d FROM synthetic WHERE k > 0;\n", encoding="utf-8")
            self.assertTrue(any("query text changed" in m for m in sr.verify(run).items))

    def test_a_write_query_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            (run / "queries.sql").write_text("-- result: synth\nDELETE FROM synthetic;\n", encoding="utf-8")
            self.assertTrue(any("read-only" in m for m in sr.verify(run).items))

    def test_missing_file_wrong_columns_and_bad_types_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            (run / "data" / "synth.csv").unlink()
            self.assertTrue(any("missing" in m for m in sr.verify(run).items))
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            (run / "data" / "synth.csv").unlink()
            sr.write_result_csv(run / "data" / "synth.csv", ["k", "t", "n", "b"], [[r[0], r[1], r[2], r[3]] for r in GOLDEN_ROWS])
            self.assertTrue(any("columns" in m for m in sr.verify(run).items))
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            self.rewrite(run, lambda rows: rows[0].__setitem__(0, "one"))
            self.assertTrue(any("not a valid int" in m for m in sr.verify(run).items))

    def test_zero_rows_and_oversized_results_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            manifest = run / "data" / "_manifest.json"
            data = json.loads(manifest.read_text())
            data["results"]["synth"]["controls"]["n_rows"] = 0
            manifest.write_text(json.dumps(data))
            self.rewrite(run, lambda rows: rows.clear())
            self.assertTrue(any("outside 1.." in m for m in sr.verify(run).items))
        with tempfile.TemporaryDirectory() as tmp:
            big = [[str(i), "x", "1.00", "true", "d"] for i in range(sr.MAX_ROWS + 1)]
            run = make_run(tmp, rows=big)
            self.assertTrue(any("outside 1.." in m for m in sr.verify(run).items))

    def test_register_never_replaces_and_needs_a_query_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            with self.assertRaises(ValueError):
                sr.register_result(run, "synth", TYPES, {"n_rows": 1, "md5_rows": "x"})
            with self.assertRaises(ValueError):
                sr.register_result(run, "other", TYPES, {"n_rows": 1, "md5_rows": "x"})

    def test_command_line_verify_exit_codes(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp)
            ok = subprocess.run([sys.executable, "-m", "helpers.sql_results", "verify", str(run)],
                                capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.rewrite(run, lambda rows: rows.pop())
            bad = subprocess.run([sys.executable, "-m", "helpers.sql_results", "verify", str(run)],
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("VALIDATION FAILED", bad.stdout)

    rewrite_helper = rewrite


MONTH_TYPES = [("month_start", "date"), ("paid_active", "int"), ("churned", "int"), ("rate", "decimal")]
MONTH_COLUMNS = [c for c, _ in MONTH_TYPES]


def make_month_run(tmp, rows, name="churn"):
    run = Path(tmp)
    (run / "data").mkdir(parents=True, exist_ok=True)
    (run / "queries.sql").write_text(f"-- result: {name}\nSELECT 1 AS month_start;\n", encoding="utf-8")
    sr.write_result_csv(run / "data" / f"{name}.csv", MONTH_COLUMNS, rows)
    sr.register_result(run, name, MONTH_TYPES, {
        "n_rows": len(rows), "md5_rows": sr.canonical_md5(MONTH_COLUMNS, rows),
        "sums": {"paid_active": str(sum(int(r[1]) for r in rows if r[1] is not None))}})
    return run


class ValidateInputsTests(unittest.TestCase):
    KB = Path(__file__).resolve().parents[1] / "knowledge" / "beam"
    SPEC = {"churn": {"not_null": ["month_start", "paid_active"], "key": ["month_start"],
                      "closed_month_cols": ["month_start"], "cutoff_cols": ["month_start"],
                      "rates": [("churned", "paid_active", "rate", 4)]}}
    GOOD = [["2026-07-01", "1000", "40", "0.0400"], ["2026-08-01", "1100", "55", "0.0500"]]

    def run_validate(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_month_run(tmp, rows)
            return sr.validate_inputs(run, self.SPEC, ["subscriptions"], self.KB)

    def test_valid_inputs_return_typed_frames(self):
        frames = self.run_validate(self.GOOD)
        df = frames["churn"]
        self.assertEqual(str(df["paid_active"].dtype), "Int64")
        self.assertEqual(df["rate"].iloc[0], Decimal("0.0400"))
        self.assertEqual(str(df["month_start"].dtype), "datetime64[ns]")

    def expect_stop(self, rows):
        with self.assertRaises(SystemExit) as ctx:
            self.run_validate(rows)
        self.assertEqual(ctx.exception.code, 2)

    def test_september_2026_stops_a_closed_month_comparison(self):
        self.expect_stop(self.GOOD + [["2026-09-01", "1200", "30", "0.0250"]])

    def test_a_date_on_or_after_the_cutoff_stops(self):
        self.expect_stop([["2026-09-25", "1000", "40", "0.0400"]])

    def test_duplicate_keys_stop(self):
        self.expect_stop(self.GOOD + [self.GOOD[0]])

    def test_a_missing_value_stops(self):
        self.expect_stop([["2026-07-01", None, "40", "0.0400"]])

    def test_a_rate_that_does_not_match_its_counts_stops(self):
        self.expect_stop([["2026-07-01", "1000", "40", "0.0500"]])

    def test_a_non_month_start_stops(self):
        self.expect_stop([["2026-07-15", "1000", "40", "0.0400"]])

    def test_an_unregistered_input_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_month_run(tmp, self.GOOD)
            with self.assertRaises(SystemExit):
                sr.validate_inputs(run, {"nope": {}}, ["subscriptions"], self.KB)


TYPES_LINE = "-- types: k=int, t=text, n=decimal, b=bool, d=text"
CONTROL_VALUES = {
    "_ctl_n_rows": "16", "_ctl_md5_rows": json.dumps(GOLDEN_MD5), "_ctl_sum_n": "89.100",
    "_ctl_sum_k": "124", "_ctl_distinct_t": "13", "_ctl_nulls_t": "1",
    "_ctl_min_k": '"1"', "_ctl_max_k": '"15"',
}


def tool_output(rows=None, separator="\n", controls=True):
    """Text shaped like execute_sql output: numbers as raw tokens (scale kept), one object per row."""
    rows = copy_rows(rows)

    def token(column, value):
        if value is None:
            return "null"
        if column in ("k", "n"):
            return value
        return json.dumps(value, ensure_ascii=False)

    objects = []
    for i, row in enumerate(rows):
        fields = [f'"{c}":{token(c, v)}' for c, v in zip(COLUMNS, row)]
        for key, raw in CONTROL_VALUES.items():
            if controls:
                fields.append(f'"{key}":{raw if i == 0 else "null"}')
        objects.append("{" + ",".join(fields) + "}")
    return separator.join(objects)


def make_block_run(tmp, raw_text=None, types_line=TYPES_LINE):
    run = Path(tmp)
    (run / "data" / "_raw").mkdir(parents=True)
    (run / "queries.sql").write_text(
        f"-- result: synth\n-- file: data/synth.csv\n{types_line}\n{SELECT};\n", encoding="utf-8")
    if raw_text is not None:
        (run / "data" / "_raw" / "synth.json").write_text(raw_text, encoding="utf-8")
    return run


class WrapAndIngestTests(unittest.TestCase):
    def test_tool_output_parsing_keeps_scale_and_accepts_both_layouts(self):
        for sep in ("\n", ""):
            objects = sr.parse_tool_output(tool_output(separator=sep))
            self.assertEqual(len(objects), 16)
            self.assertEqual(sr._as_text(objects[0]["n"]), "12.50")
            self.assertEqual(sr._as_text(objects[8]["n"]), "5")
            self.assertEqual(sr._as_text(objects[8]["t"]), 'say "hi", ok\nline2')
            self.assertEqual(sr._as_text(objects[0]["_ctl_sum_n"]), "89.100")
            self.assertIsNone(objects[2]["t"])

    def test_wrap_block_uses_the_saved_block_and_its_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_block_run(tmp)
            sql = sr.wrap_block(run, "synth", '"k", "t"')
        self.assertIn("FROM synthetic ORDER BY k", sql)
        self.assertNotIn("-- types", sql)
        for name in ("n_rows", "md5_rows", "sum_n", "sum_k", "distinct_t", "nulls_k", "min_k", "max_n"):
            self.assertIn(f'AS "_ctl_{name}"', sql)
        self.assertNotIn("distinct_k", sql)     # distinct counts are only for text columns

    def test_wrap_block_needs_a_types_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_block_run(tmp, types_line="-- note")
            with self.assertRaises(ValueError):
                sr.wrap_block(run, "synth", '"k"')

    def test_ingest_writes_the_csv_registers_and_verifies(self):
        for sep in ("\n", ""):
            with tempfile.TemporaryDirectory() as tmp:
                run = make_block_run(tmp, tool_output(separator=sep))
                self.assertEqual(sr.ingest_raw(run, "synth").items, [])
                header, rows = sr.read_result_csv(run / "data" / "synth.csv")
                self.assertEqual(header, COLUMNS)
                self.assertEqual(rows, GOLDEN_ROWS)
                self.assertEqual(sr.verify(run).items, [])
                manifest = json.loads((run / "data" / "_manifest.json").read_text())
                self.assertEqual(manifest["results"]["synth"]["controls"]["md5_rows"], GOLDEN_MD5)
                self.assertEqual(manifest["results"]["synth"]["controls"]["sums"]["n"], "89.100")

    def test_ingest_never_overwrites_a_saved_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_block_run(tmp, tool_output())
            self.assertEqual(sr.ingest_raw(run, "synth").items, [])
            self.assertTrue(sr.ingest_raw(run, "synth").items)

    def test_a_truncated_paste_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_block_run(tmp, tool_output(GOLDEN_ROWS[:-3]))
            self.assertTrue(any("rows saved" in m for m in sr.ingest_raw(run, "synth").items))

    def test_a_changed_value_in_the_paste_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = copy_rows()
            rows[8][1] = "say hi"
            run = make_block_run(tmp, tool_output(rows))
            self.assertTrue(any("checksum" in m for m in sr.ingest_raw(run, "synth").items))

    def test_missing_controls_wrong_columns_and_bad_json_stop(self):
        cases = {
            "no controls": tool_output(controls=False),
            "bad json": tool_output()[:-5],
            "wrong columns": tool_output().replace('"d":', '"e":'),
        }
        for label, text in cases.items():
            with tempfile.TemporaryDirectory() as tmp:
                run = make_block_run(tmp, text)
                self.assertTrue(sr.ingest_raw(run, "synth").items, label)
                self.assertFalse((run / "data" / "synth.csv").exists(), label)

    def test_command_line_wrap_and_ingest(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            run = make_block_run(tmp, tool_output())
            wrap = subprocess.run([sys.executable, "-m", "helpers.sql_results", "wrap", str(run), "synth",
                                   "--order-by", '"k"'], capture_output=True, text=True)
            self.assertEqual(wrap.returncode, 0, wrap.stderr)
            self.assertIn('COLLATE "C"', wrap.stdout)
            ingest = subprocess.run([sys.executable, "-m", "helpers.sql_results", "ingest", str(run), "synth"],
                                    capture_output=True, text=True)
            self.assertEqual(ingest.returncode, 0, ingest.stdout + ingest.stderr)
            again = subprocess.run([sys.executable, "-m", "helpers.sql_results", "ingest", str(run), "synth"],
                                   capture_output=True, text=True)
            self.assertEqual(again.returncode, 2)
            usage = subprocess.run([sys.executable, "-m", "helpers.sql_results"], capture_output=True, text=True)
            self.assertNotEqual(usage.returncode, 0)


if __name__ == "__main__":
    unittest.main()
