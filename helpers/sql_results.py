"""Save, verify and validate SQL results that reach a run folder through the course-db MCP tools.

The model runs aggregate SQL through the MCP server and saves each result as a CSV in
outputs/<run>/data/. This module checks that the saved files are complete and consistent,
and validates them before analysis.py calculates anything. It also holds the one canonical
encoding used by both the SQL control statement (build_result_sql) and the Python check.

What the checks do and do not establish
---------------------------------------
* Query identity: a SHA-256 of the query text ties a CSV to one saved query. It does not prove
  the query ran or what it returned.
* CSV structure and contents: the CSV is re-read and compared with the controls (row count,
  sums, null and distinct counts, minimum and maximum, row checksum).
* Faithfulness to the SQL result: the controls and the row checksum are computed by Postgres in
  the same statement as the rows, so they describe the same snapshot. A CSV that reproduces
  them matches what that statement returned.
* MD5 is an accidental-corruption check. It is not a security guarantee and not proof of data
  provenance. It cannot tell a faithful copy from rows and a hash that were both invented.
* Separate SQL calls do not necessarily share a database snapshot. The session runs at read
  committed and each statement sees the data as of its own start. The drift probe compares row
  counts before and after the queries; it cannot detect changed values, and it does not prove
  that all queries used an identical database state. Controls guarantee consistency only
  between a result's rows and its own controls, because those come from one statement.

Database access
---------------
The students role cannot write to Beam's tables, views or sequences and cannot create objects
in the project schemas. It can still create temporary tables and change session settings, and
the session has no statement timeout. The connection is therefore not fully read-only: keep
every query a bounded, aggregate-only SELECT. lint_read_only is a heuristic, not a guard.

Workflow commands (run from the repo root)
------------------------------------------
1. Write the result's SELECT in outputs/<run>/queries.sql under '-- result: <name>' and
   '-- types: col=type, ...' (types: int, decimal, date, timestamp, bool, text).
2. python -m helpers.sql_results wrap <run_dir> <name> --order-by '"col1", "col2"'
   prints the single statement to run through execute_sql. It returns the rows plus controls.
3. Paste the tool output exactly as returned into outputs/<run>/data/_raw/<name>.json.
4. python -m helpers.sql_results ingest <run_dir> <name>
   writes data/<name>.csv, registers it in data/_manifest.json and verifies it.
5. python -m helpers.sql_results verify <run_dir> checks every registered result again.

CSV convention
--------------
NULL is written as \\N. A text value that starts with a backslash gets one more leading
backslash, so NULL, the empty string and the text "\\N" are all distinct and round-trip exactly.

Canonical encoding (SQL and Python use the same one)
----------------------------------------------------
field  = "N" for NULL, else "V" + character_count + ":" + text
row    = "R" + length_of_fields + ":" + fields
payload = "H" + row(column names) + "B" + row_count + ":" + sorted(rows) joined with nothing
checksum = md5(payload)
Rows are sorted in code point order, which equals COLLATE "C" on a UTF8 database, so the
checksum ignores row order but counts duplicate rows. Lengths make every boundary unambiguous,
so separator characters inside text cannot collide.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

MAX_ROWS = 500
NULL_TOKEN = "\\N"
RESERVED_NAMES = {"_rn", "_row_enc"}

PATTERNS = {
    "int": re.compile(r"-?\d+"),
    "decimal": re.compile(r"-?\d+(\.\d+)?"),
    "date": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "timestamp": re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?"),
    "bool": re.compile(r"true|false"),
    "text": re.compile(r".*", re.S),
}

FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|copy|call|vacuum|"
    r"reindex|refresh|merge|into|set_config|setval|nextval|pg_sleep|pg_terminate_backend|"
    r"pg_cancel_backend|lo_import|lo_export|dblink\w*)\b|\bpg_(try_)?advisory\w*",
    re.I,
)


# ---------------------------------------------------------------- failures

class Failures:
    """Collects every failed check so one run reports them all, then stops."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, ok, message: str) -> bool:
        if not ok:
            self.items.append(message)
        return bool(ok)

    def stop_if_any(self) -> None:
        if self.items:
            print("VALIDATION FAILED:")
            for message in self.items:
                print(f" - {message}")
            sys.exit(2)


# ---------------------------------------------------------------- canonical encoding

def enc_field(value: str | None) -> str:
    return "N" if value is None else f"V{len(value)}:{value}"


def enc_row(values) -> str:
    fields = "".join(enc_field(v) for v in values)
    return f"R{len(fields)}:{fields}"


def canonical_payload(columns, rows) -> str:
    body = "".join(sorted(enc_row(r) for r in rows))
    return "H" + enc_row(columns) + "B" + f"{len(rows)}:" + body


def canonical_md5(columns, rows) -> str:
    """Checksum of every column name and every row, NULL distinct from the empty string."""
    return hashlib.md5(canonical_payload(columns, rows).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- CSV reading and writing

def csv_encode(value: str | None) -> str:
    if value is None:
        return NULL_TOKEN
    return "\\" + value if value.startswith("\\") else value


def csv_decode(text: str) -> str | None:
    if text == NULL_TOKEN:
        return None
    return text[1:] if text.startswith("\\") else text


def write_result_csv(path, columns, rows) -> Path:
    """Write rows (strings or None) with the NULL convention. Refuses to overwrite a file."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} already exists; saved results are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([csv_encode(v) for v in row])
    return path


def read_result_csv(path):
    """Return (columns, rows) with NULL decoded to None. Rows keep their raw length."""
    with Path(path).open(newline="", encoding="utf-8") as fh:
        raw = list(csv.reader(fh))
    if not raw:
        return [], []
    return raw[0], [[csv_decode(v) for v in r] for r in raw[1:]]


# ---------------------------------------------------------------- SQL text helpers

def _strip_sql(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    return re.sub(r"\s+", " ", text).strip().rstrip(";").strip()


def query_sha(text: str) -> str:
    """SHA-256 of the query with comments and extra whitespace removed (identity only)."""
    return hashlib.sha256(_strip_sql(text).encode("utf-8")).hexdigest()


def lint_read_only(text: str) -> bool:
    """Heuristic: one SELECT or WITH statement with no write or side-effect keywords."""
    body = re.sub(r"'(?:[^']|'')*'", "''", _strip_sql(text))
    return (
        bool(re.match(r"(?i)(select|with)\b", body))
        and ";" not in body
        and not FORBIDDEN_SQL.search(body)
    )


def query_blocks(path) -> dict[str, str]:
    """Split queries.sql into blocks, each starting with a line '-- result: <name>'."""
    blocks: dict[str, list[str]] = {}
    name = None
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = re.match(r"--\s*result:\s*(\w+)\s*$", line)
        if match:
            name = match.group(1)
            blocks[name] = []
        elif name:
            blocks[name].append(line)
    return {k: "\n".join(v) for k, v in blocks.items()}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build_result_sql(select_sql, columns, order_by, *, sums=(), distinct=(), nulls=(), minmax=()) -> str:
    """Wrap a result SELECT in one statement that also returns its own controls.

    The controls come back on row 1 only, as _ctl_* columns, computed in the same statement
    (one snapshot) as the rows. Dates and timestamps in select_sql must already be text
    (to_char), and rates must be numeric, never float, so SQL and CSV text agree.
    """
    body = select_sql.strip().rstrip(";").strip()
    if not lint_read_only(body):
        raise ValueError("select_sql must be a single read-only SELECT or WITH statement")
    columns = list(columns)
    if not columns or len(set(columns)) != len(columns):
        raise ValueError("columns must be a non-empty list of distinct names")
    for name in columns:
        if name.startswith("_ctl_") or name in RESERVED_NAMES:
            raise ValueError(f"column name {name!r} is reserved")
    for name in (*sums, *distinct, *nulls, *minmax):
        if name not in columns:
            raise ValueError(f"control column {name!r} is not a result column")

    array = ", ".join(f"r.{_q(c)}::text" for c in columns)
    names = ", ".join(_lit(c) for c in columns)
    ctl = ["COUNT(*) AS n_rows"]
    ctl += [f"SUM(x.{_q(c)}) AS {_q('sum_' + c)}" for c in sums]
    ctl += [f"COUNT(DISTINCT x.{_q(c)}) AS {_q('distinct_' + c)}" for c in distinct]
    ctl += [f"COUNT(*) FILTER (WHERE x.{_q(c)} IS NULL) AS {_q('nulls_' + c)}" for c in nulls]
    for c in minmax:
        ctl += [f"MIN(x.{_q(c)})::text AS {_q('min_' + c)}", f"MAX(x.{_q(c)})::text AS {_q('max_' + c)}"]
    ctl_names = ["n_rows", "md5_rows"]
    ctl_names += [f"sum_{c}" for c in sums] + [f"distinct_{c}" for c in distinct]
    ctl_names += [f"nulls_{c}" for c in nulls]
    ctl_names += [n for c in minmax for n in (f"min_{c}", f"max_{c}")]
    out_cols = ", ".join(f"n.{_q(c)}" for c in columns)
    out_ctl = ", ".join(
        f"CASE WHEN n._rn = 1 THEN ctl.{_q(n)} END AS {_q('_ctl_' + n)}" for n in ctl_names
    )
    return f"""WITH r AS (
{body}
),
rows_enc AS (
  SELECT r.*, 'R' || char_length(fe.f) || ':' || fe.f AS _row_enc
  FROM r CROSS JOIN LATERAL (
    SELECT COALESCE(string_agg(CASE WHEN u.v IS NULL THEN 'N' ELSE 'V' || char_length(u.v) || ':' || u.v END, '' ORDER BY u.ord), '') AS f
    FROM unnest(ARRAY[{array}]) WITH ORDINALITY AS u(v, ord)
  ) fe
),
hdr AS (
  SELECT 'R' || char_length(h.f) || ':' || h.f AS header_enc
  FROM (SELECT string_agg('V' || char_length(u.nm) || ':' || u.nm, '' ORDER BY u.ord) AS f
        FROM unnest(ARRAY[{names}]) WITH ORDINALITY AS u(nm, ord)) h
),
ctl AS (
  SELECT {", ".join(ctl)},
         md5('H' || (SELECT header_enc FROM hdr) || 'B' || COUNT(*) || ':' || COALESCE(string_agg(x._row_enc, '' ORDER BY x._row_enc COLLATE "C"), '')) AS md5_rows
  FROM rows_enc x
),
numbered AS (SELECT rows_enc.*, row_number() OVER (ORDER BY {order_by}) AS _rn FROM rows_enc)
SELECT {out_cols},
       {out_ctl}
FROM numbered n CROSS JOIN ctl
ORDER BY n._rn"""


# ---------------------------------------------------------------- cutoff

def cutoff_exclusive(tables, kb="knowledge/beam") -> date:
    """The exclusive upper bound: the day after data_cutoff, read from each table's YAML.

    data_cutoff 2026-09-24 gives 2026-09-25, so records on 2026-09-24 at any time are eligible.
    """
    import yaml

    found = set()
    for table in tables:
        cfg = yaml.safe_load(Path(kb, f"{table}.yaml").read_text(encoding="utf-8"))
        found.add(str(cfg["data_coverage"]["data_cutoff"]))
    if len(found) != 1:
        raise SystemExit(f"YAML data_cutoff values disagree: {sorted(found)}")
    return date.fromisoformat(found.pop()) + timedelta(days=1)


def last_closed_month_start(cut_excl: date) -> date:
    """First day of the last month that is fully inside the cutoff."""
    month_of_last_day = (cut_excl - timedelta(days=1)).replace(day=1)
    if cut_excl.day == 1:
        return month_of_last_day
    return (month_of_last_day - timedelta(days=1)).replace(day=1)


def cutoff_predicate(column: str, data_type: str, cut_excl: date) -> tuple[str, str]:
    """SQL predicate for 'dated before the exclusive cutoff', chosen by the column's data type.

    Returns (predicate, note). A timestamp with time zone is compared at 00:00 UTC, which is an
    assumption (the database TimeZone setting is utc); the note says so and the user must be told.
    Any other type raises ValueError, so a text column is never compared by accident.
    """
    day = cut_excl.isoformat()
    kind = data_type.strip().lower()
    col = _q(column)
    if kind == "timestamp without time zone":
        return f"{col} < TIMESTAMP '{day} 00:00:00'", "naive timestamp, compared as written"
    if kind == "date":
        return f"{col} < DATE '{day}'", "date column"
    if kind == "timestamp with time zone":
        return (
            f"{col} < TIMESTAMP WITH TIME ZONE '{day} 00:00:00+00'",
            "timestamp with time zone compared at 00:00 UTC (assumption: report it)",
        )
    raise ValueError(f"cannot apply the cutoff to a column of type {data_type!r}")


def check_scope_counts(f: Failures, label, rows_total, rows_to_cutoff, rows_after_cutoff,
                       rows_null_cut, rows_in_scope) -> None:
    """P1 and P2 reconciliation: NULL cutoff dates, in-scope rows and later rows add up."""
    f.check(
        rows_to_cutoff + rows_after_cutoff + rows_null_cut == rows_total,
        f"{label}: {rows_to_cutoff} + {rows_after_cutoff} + {rows_null_cut} != {rows_total} rows",
    )
    f.check(rows_in_scope == rows_to_cutoff,
            f"{label}: P2 rows_in_scope {rows_in_scope} != P1 rows_to_cutoff {rows_to_cutoff}")


# ---------------------------------------------------------------- reconciliation

def _decimal(value) -> Decimal:
    if isinstance(value, (float, bool)):
        raise TypeError("money and rates must be int, str or Decimal, never float or bool")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def reconcile_counts(f: Failures, label, parts, total) -> None:
    """Integer counts reconcile exactly."""
    for v in (*parts, total):
        if isinstance(v, bool) or not isinstance(v, int):
            raise TypeError("counts must be int")
    f.check(sum(parts) == total, f"{label}: parts sum to {sum(parts)}, total is {total}")


def reconcile_money(f: Failures, label, parts, total, rounded_parts: int = 0,
                    total_rounded: bool = False) -> None:
    """Exact Decimal comparison of stored numeric(10,2) amounts.

    The tolerance is 0 unless the SQL or script applied an explicit ROUND(x, 2). Each rounded
    part, and the total if it was rounded too, may be off by 0.005. Name them in the spec.
    """
    values = [_decimal(p) for p in parts]
    if rounded_parts < 0 or rounded_parts > len(values):
        raise ValueError("rounded_parts must be between 0 and the number of parts")
    tolerance = Decimal("0.005") * (rounded_parts + (1 if total_rounded else 0))
    diff = abs(sum(values, Decimal(0)) - _decimal(total))
    f.check(diff <= tolerance, f"{label}: parts differ from total by {diff} (tolerance {tolerance})")


def check_rate(f: Failures, label, num, den, rate, decimals: int | None = None) -> None:
    """A rate must equal numerator over denominator, lie in [0, 1], and not exceed it."""
    num, den, rate = int(num), int(den), _decimal(rate)
    if not f.check(den > 0 and 0 <= num <= den, f"{label}: numerator {num} / denominator {den} invalid"):
        return
    tolerance = Decimal("1e-9") if decimals is None else Decimal("0.5") * Decimal(10) ** -decimals
    f.check(0 <= rate <= 1, f"{label}: rate {rate} is outside [0, 1]")
    f.check(abs(Decimal(num) / Decimal(den) - rate) <= tolerance,
            f"{label}: rate {rate} differs from {num}/{den}")


# ---------------------------------------------------------------- manifest

def _load_manifest(run: Path) -> dict:
    path = run / "data" / "_manifest.json"
    if not path.exists():
        return {"results": {}}
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def register_result(run_dir, name: str, columns, controls: dict) -> None:
    """Add one result to data/_manifest.json. Refuses to replace an existing entry.

    columns is a list of (name, type) with type in int, decimal, date, timestamp, bool, text.
    controls holds the row 1 _ctl_* values from the SQL (without the _ctl_ prefix): n_rows,
    md5_rows, and optionally sums, nulls, distinct, min, max as {column: value} dicts.
    """
    run = Path(run_dir)
    manifest = _load_manifest(run)
    if name in manifest["results"]:
        raise ValueError(f"{name!r} is already registered; saved results are never replaced")
    block = query_blocks(run / "queries.sql").get(name)
    if block is None:
        raise ValueError(f"queries.sql has no '-- result: {name}' block")
    manifest["results"][name] = {
        "file": f"{name}.csv",
        "query_sha256": query_sha(block),
        "columns": [{"name": c, "type": t} for c, t in columns],
        "controls": controls,
    }
    (run / "data").mkdir(parents=True, exist_ok=True)
    (run / "data" / "_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )


def _same_value(kind: str, got, want) -> bool:
    if kind in ("int", "decimal"):
        return Decimal(str(got)) == Decimal(str(want))
    return str(got) == str(want)


def _verify_one(run: Path, name: str, m: dict, blocks: dict, f: Failures) -> None:
    if f.check(name in blocks, f"{name}: no '-- result: {name}' block in queries.sql"):
        f.check(lint_read_only(blocks[name]), f"{name}: query is not a single read-only SELECT")
        f.check(query_sha(blocks[name]) == m["query_sha256"],
                f"{name}: query text changed since the result was registered")
    path = run / "data" / m["file"]
    if not f.check(path.exists(), f"{name}: {m['file']} is missing"):
        return
    header, rows = read_result_csv(path)
    names = [c["name"] for c in m["columns"]]
    types = {c["name"]: c["type"] for c in m["columns"]}
    ctl = m["controls"]
    if not f.check(header == names, f"{name}: CSV columns {header} differ from the manifest {names}"):
        return
    if not f.check(all(len(r) == len(names) for r in rows), f"{name}: a row has the wrong number of fields"):
        return
    f.check(len(rows) == int(ctl["n_rows"]), f"{name}: {len(rows)} rows saved, {ctl['n_rows']} expected")
    f.check(0 < len(rows) <= MAX_ROWS, f"{name}: {len(rows)} rows is outside 1..{MAX_ROWS}")
    pos = {c: i for i, c in enumerate(names)}
    invalid = set()
    for c in names:
        if types[c] not in PATTERNS:
            f.check(False, f"{name}.{c}: unknown type {types[c]!r}")
            invalid.add(c)
            continue
        bad = sum(1 for r in rows if r[pos[c]] is not None and not PATTERNS[types[c]].fullmatch(r[pos[c]]))
        if not f.check(bad == 0, f"{name}.{c}: {bad} values are not a valid {types[c]}"):
            invalid.add(c)
    ctl = {
        kind: {c: v for c, v in ctl.get(kind, {}).items() if c not in invalid}
        for kind in ("sums", "nulls", "distinct", "min", "max")
    } | {"md5_rows": ctl["md5_rows"]}

    def values(col):
        return [r[pos[col]] for r in rows if r[pos[col]] is not None]

    for c, want in ctl.get("sums", {}).items():
        ok_type = types.get(c) in ("int", "decimal")
        if f.check(ok_type, f"{name}.{c}: a sum control needs an int or decimal column"):
            got = sum((Decimal(v) for v in values(c)), Decimal(0))
            f.check(got == Decimal(str(want)), f"{name}.{c}: sum {got} differs from control {want}")
    for c, want in ctl.get("nulls", {}).items():
        got = sum(r[pos[c]] is None for r in rows)
        f.check(got == int(want), f"{name}.{c}: {got} NULLs, control says {want}")
    for c, want in ctl.get("distinct", {}).items():
        f.check(len(set(values(c))) == int(want), f"{name}.{c}: distinct count differs from control {want}")
    for c, want in ctl.get("min", {}).items():
        vals = values(c)
        f.check(bool(vals) and _same_value(types[c], min(vals, key=lambda v: Decimal(v) if types[c] in ("int", "decimal") else v), want),
                f"{name}.{c}: minimum differs from control {want}")
    for c, want in ctl.get("max", {}).items():
        vals = values(c)
        f.check(bool(vals) and _same_value(types[c], max(vals, key=lambda v: Decimal(v) if types[c] in ("int", "decimal") else v), want),
                f"{name}.{c}: maximum differs from control {want}")
    f.check(canonical_md5(names, rows) == ctl["md5_rows"],
            f"{name}: row checksum differs from the database checksum")


def verify(run_dir, only: str | None = None) -> Failures:
    """Check every registered result in a run folder (or just `only`). Returns the failures."""
    run, f = Path(run_dir), Failures()
    manifest = _load_manifest(run)["results"]
    queries = run / "queries.sql"
    f.check(queries.exists(), "queries.sql is missing")
    f.check(bool(manifest), "data/_manifest.json lists no results")
    blocks = query_blocks(queries) if queries.exists() else {}
    for name, m in manifest.items():
        if only is None or name == only:
            _verify_one(run, name, m, blocks, f)
    return f


# ---------------------------------------------------------------- wrap a block and ingest its output

def block_types(block: str) -> list[tuple[str, str]]:
    """Read '-- types: month_start=date, plan=text, ...' from a query block, in column order."""
    match = re.search(r"(?m)^\s*--\s*types:\s*(.+)$", block)
    if not match:
        return []
    pairs = []
    for part in match.group(1).split(","):
        name, _, kind = part.strip().partition("=")
        pairs.append((name.strip(), kind.strip()))
    return pairs


def default_controls(types) -> dict:
    """Controls to compute for each column type: sums for numbers, distinct for text, etc."""
    return {
        "sums": [c for c, t in types if t in ("int", "decimal")],
        "distinct": [c for c, t in types if t == "text"],
        "nulls": [c for c, _ in types],
        "minmax": [c for c, t in types if t in ("int", "decimal", "date", "timestamp")],
    }


def wrap_block(run_dir, name: str, order_by: str) -> str:
    """The single statement to run for one result: the saved block plus its own controls."""
    block = query_blocks(Path(run_dir) / "queries.sql")[name]
    types = block_types(block)
    if not types:
        raise ValueError(f"the '-- result: {name}' block has no '-- types:' line")
    inner = re.sub(r"(?m)^\s*--.*$", "", block).strip()
    return build_result_sql(inner, [c for c, _ in types], order_by, **default_controls(types))


def parse_tool_output(text: str) -> list[dict]:
    """Parse execute_sql output: JSON objects, one per row, newline separated or not.

    Numbers keep their scale (12.50 stays 12.50) because floats are read as Decimal.
    """
    decoder = json.JSONDecoder(parse_float=Decimal)
    rows, i = [], 0
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return rows
        obj, i = decoder.raw_decode(text, i)
        rows.append(obj)


def _as_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def ingest_raw(run_dir, name: str) -> Failures:
    """Turn data/_raw/<name>.json (the tool output, pasted as returned) into the CSV and manifest.

    Reads the column types from the block's '-- types:' line, takes the controls from the
    _ctl_* values on row 1, writes data/<name>.csv with the NULL convention, registers the
    result, and verifies it. Refuses to overwrite a CSV or replace a registration.
    """
    run, f = Path(run_dir), Failures()
    blocks = query_blocks(run / "queries.sql") if (run / "queries.sql").exists() else {}
    if not f.check(name in blocks, f"{name}: no '-- result: {name}' block in queries.sql"):
        return f
    types = block_types(blocks[name])
    if not f.check(bool(types), f"{name}: the block has no '-- types:' line"):
        return f
    raw = run / "data" / "_raw" / f"{name}.json"
    if not f.check(raw.exists(), f"{name}: {raw} is missing"):
        return f
    try:
        objects = parse_tool_output(raw.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        f.check(False, f"{name}: the raw output is not valid JSON rows ({exc})")
        return f
    columns = [c for c, _ in types]
    if not f.check(bool(objects), f"{name}: the raw output has no rows"):
        return f
    if not f.check(all([k for k in o if not k.startswith("_ctl_")] == columns for o in objects),
                   f"{name}: the raw output columns differ from the '-- types:' line"):
        return f
    first = objects[0]
    if not f.check(first.get("_ctl_n_rows") is not None and first.get("_ctl_md5_rows"),
                   f"{name}: row 1 carries no controls; run the wrapped statement"):
        return f
    controls: dict = {"n_rows": int(first["_ctl_n_rows"]), "md5_rows": first["_ctl_md5_rows"]}
    buckets = (("sum_", "sums"), ("distinct_", "distinct"), ("nulls_", "nulls"), ("min_", "min"), ("max_", "max"))
    for key, value in first.items():
        if key.startswith("_ctl_") and key not in ("_ctl_n_rows", "_ctl_md5_rows"):
            base = key[len("_ctl_"):]
            for prefix, bucket in buckets:
                if base.startswith(prefix):
                    controls.setdefault(bucket, {})[base[len(prefix):]] = _as_text(value)
                    break
    rows = [[_as_text(o[c]) for c in columns] for o in objects]
    try:
        write_result_csv(run / "data" / f"{name}.csv", columns, rows)
        register_result(run, name, types, controls)
    except (FileExistsError, ValueError) as exc:
        f.check(False, f"{name}: {exc}")
        return f
    return verify(run, only=name)


# ---------------------------------------------------------------- analysis.py input validation

def validate_inputs(run_dir, spec: dict, tables, kb="knowledge/beam") -> dict:
    """Verify, type and validate every input before any calculation. Exits with code 2 on failure.

    spec maps a result name to optional keys: not_null (columns), key (columns that must be
    unique together), cutoff_cols (date or timestamp columns that must lie before the exclusive
    cutoff), closed_month_cols (month start columns that must not pass the last closed month),
    rates (list of (numerator, denominator, rate, decimals)).
    Never replace a failure with a default: there is no coercion, fill or silent drop here.
    """
    import pandas as pd

    run = Path(run_dir)
    cut = cutoff_exclusive(tables, kb)
    last_closed = last_closed_month_start(cut)
    f = verify(run)
    manifest = _load_manifest(run)["results"]
    frames: dict = {}
    for name, s in spec.items():
        if not f.check(name in manifest, f"{name}: not registered in the manifest"):
            continue
        header, rows = read_result_csv(run / "data" / manifest[name]["file"])
        types = {c["name"]: c["type"] for c in manifest[name]["columns"]}
        if header != list(types) or not all(len(r) == len(header) for r in rows):
            continue  # verify() already reported it
        df = pd.DataFrame(rows, columns=header, dtype=object)
        try:
            for c, t in types.items():
                col = df[c]
                if t == "int":
                    df[c] = pd.array([None if v is None else int(v) for v in col], dtype="Int64")
                elif t == "decimal":
                    df[c] = pd.Series([None if v is None else Decimal(v) for v in col], dtype=object)
                elif t in ("date", "timestamp"):
                    df[c] = pd.to_datetime(col, errors="raise")
                elif t == "bool":
                    df[c] = pd.array([None if v is None else v == "true" for v in col], dtype="boolean")
        except (ValueError, TypeError, ArithmeticError) as exc:
            f.check(False, f"{name}: a value could not be typed ({exc})")
            continue
        for c in s.get("not_null", []):
            f.check(df[c].notna().all(), f"{name}.{c}: has missing values")
        if s.get("key"):
            f.check(not df.duplicated(s["key"]).any(), f"{name}: duplicate keys on {s['key']}")
        for c in s.get("cutoff_cols", []):
            f.check((df[c] < pd.Timestamp(cut)).all(), f"{name}.{c}: values on or after {cut}")
        for c in s.get("closed_month_cols", []):
            f.check((df[c].dt.day == 1).all(), f"{name}.{c}: not all month starts")
            f.check((df[c] <= pd.Timestamp(last_closed)).all(),
                    f"{name}.{c}: month after the last closed month {last_closed}")
        for num, den, rate, decimals in s.get("rates", []):
            for i in range(len(df)):
                if pd.isna(df[num].iloc[i]) or pd.isna(df[den].iloc[i]) or pd.isna(df[rate].iloc[i]):
                    f.check(False, f"{name}: row {i} has a missing rate input")
                    break
                check_rate(f, f"{name} row {i}", df[num].iloc[i], df[den].iloc[i], df[rate].iloc[i], decimals)
        frames[name] = df
    f.stop_if_any()
    return frames


USAGE = """usage:
  python -m helpers.sql_results wrap <run_dir> <name> --order-by '"col1", "col2"'
  python -m helpers.sql_results ingest <run_dir> <name>
  python -m helpers.sql_results verify <run_dir>"""


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "verify":
        verify(args[1]).stop_if_any()
        print("verify: every registered result is complete and consistent")
    elif len(args) == 3 and args[0] == "ingest":
        ingest_raw(args[1], args[2]).stop_if_any()
        print(f"ingest: {args[2]} saved, registered and verified")
    elif len(args) == 5 and args[0] == "wrap" and args[3] == "--order-by":
        print(wrap_block(args[1], args[2], args[4]))
    else:
        sys.exit(USAGE)
