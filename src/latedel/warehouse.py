"""The SQLite warehouse: connection, SQL runner, load of a delivery, controls and run records (RF09, decision D20).

Rules of the runner (docs/plan.md section 5), the same as in projects 01 and 02:

- autocommit connection (``isolation_level=None``); transactions are explicit ``BEGIN IMMEDIATE``;
- ``journal_mode=DELETE`` (WAL does not behave on Docker Desktop mounts), ``synchronous=FULL``, ``foreign_keys=ON``;
- never ``executescript()``: it commits the open transaction first. SQL files are split into statements and run one by one, so a failure can roll the whole
  load back;
- tables derived from others are rebuilt inside the transaction, so SQLite rolls them back too.

A load is all or nothing: the rows of the three tables of the delivery, its record in ``deliveries``, the staging tables and the controls are one
transaction. A delivery that is empty, already loaded, repeats a key or fails a control leaves the warehouse byte for byte as it was.
"""

from __future__ import annotations

import functools
import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
import pyarrow as pa

from latedel.config import PipelineConfig
from latedel.deliveries import DeliveryFrames, DeliverySpec
from latedel.schema import ORDER_LINES_SCHEMA, ORDERS_SCHEMA, OUTCOMES_SCHEMA
from latedel.validate import SEVERITY_ES, ValidationReport

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
CHECK_GROUPS = ("load", "model")
SAMPLE_LIMIT = 5
BUSY_TIMEOUT_MS = 30_000  # an overlapping run waits this long for the write lock before failing
STAGING = ("staging/stg_orders.sql", "staging/stg_order_lines.sql", "staging/stg_outcomes.sql")   # in this order: the lines read stg_orders
OPS_MARTS = ("marts/ops/mart_main_line.sql", "marts/ops/mart_labelled_orders.sql")
_RAW = (("raw_orders", ORDERS_SCHEMA, "orders"), ("raw_order_lines", ORDER_LINES_SCHEMA, "order_lines"), ("raw_outcomes", OUTCOMES_SCHEMA, "outcomes"))
RUN_COLUMNS = (
    "started_at", "finished_at", "status", "delivery_id", "as_of_date", "source_sha256", "orders_delivery", "outcomes_delivery", "orders_scored", "alerts",
    "peak_working_set_mb", "peak_commit_mb", "message",
)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    violations: int
    sample: list[tuple]


@dataclass(frozen=True)
class LoadResult:
    delivery_id: str
    as_of: date
    orders: int       # rows of the delivery now in raw_orders
    lines: int        # rows of the delivery now in raw_order_lines
    outcomes: int     # rows of the delivery now in raw_outcomes
    staged: dict[str, int]   # rows of stg_orders, stg_order_lines and stg_outcomes after the load (everything up to the calculation date)
    checks: list[CheckResult]


class EmptyDeliveryError(RuntimeError):
    """The delivery has no orders and no outcomes: nothing was written and the warehouse keeps what it had."""

    def __init__(self, run_key: str, delivery_id: str) -> None:
        super().__init__(f"La carga {run_key} se rechazó: la entrega {delivery_id} no tiene pedidos ni resultados y el almacén queda como estaba")


class DeliveryAlreadyLoadedError(RuntimeError):
    """The delivery is already in the warehouse: loading it again would duplicate its rows."""

    def __init__(self, delivery_id: str) -> None:
        super().__init__(f"La entrega {delivery_id} ya está cargada en el almacén; no se carga de nuevo")


class CheckFailedError(RuntimeError):
    """A control returned rows: the load was rolled back."""

    def __init__(self, checks: list[CheckResult]) -> None:
        self.checks = checks
        failing = [c for c in checks if c.violations]
        detail = "; ".join(f"{c.check_id}: {c.violations} filas, por ejemplo {c.sample[:2]}" for c in failing)
        super().__init__(f"La carga se deshizo; controles con filas que los violan: {detail}")


class SchemaDriftError(RuntimeError):
    """A table of the warehouse does not have the columns its SQL definition declares (runbook R10)."""

    def __init__(self, table: str, source: str, missing: tuple[str, ...], unexpected: tuple[str, ...], retyped: tuple[str, ...]) -> None:
        self.table, self.missing, self.unexpected, self.retyped = table, missing, unexpected, retyped
        parts = []
        if missing:
            parts.append(f"faltan las columnas {list(missing)}")
        if unexpected:
            parts.append(f"sobran las columnas {list(unexpected)}")
        if retyped:
            parts.append(f"tienen otro tipo las columnas {list(retyped)}")
        super().__init__(
            f"La tabla {table} no coincide con su definición en sql/schema/{source}: {'; '.join(parts)}. "
            "El almacén lo creó una versión anterior: se reconstruye desde cero (docs/runbook.md, R10)."
        )


# --- connection and SQL files -------------------------------------------------------------------------------------------


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating the folder and the file) the warehouse in autocommit mode with the project's pragmas."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _has_sql(text: str) -> bool:
    """True if `text` has anything besides whitespace, semicolons and comments."""
    i, n = 0, len(text)
    while i < n:
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        elif text[i].isspace() or text[i] == ";":
            i += 1
        else:
            return True
    return False


def split_statements(sql_text: str) -> list[str]:
    """Split a SQL file into statements, keeping semicolons inside strings, comments and triggers (the result is a new list: it is cached by text)."""
    return list(_split_statements(sql_text))


@functools.lru_cache(maxsize=256)
def _split_statements(sql_text: str) -> tuple[str, ...]:
    statements: list[str] = []
    start = 0
    for position, character in enumerate(sql_text):
        if character == ";" and sqlite3.complete_statement(sql_text[start : position + 1]):
            chunk = sql_text[start : position + 1]
            if _has_sql(chunk):
                statements.append(chunk.strip())
            start = position + 1
    if _has_sql(sql_text[start:]):  # a last statement without its semicolon
        statements.append(sql_text[start:].strip())
    return tuple(statements)


def run_sql_file(conn: sqlite3.Connection, path: Path, params: Mapping[str, object] | None = None) -> None:
    """Execute the statements of a SQL file one by one, binding the named parameters it uses (the others in `params` are ignored)."""
    text = Path(path).read_text(encoding="utf-8")
    for statement in split_statements(text):
        conn.execute(statement, dict(params or {}))


def sql_params(cfg: PipelineConfig, as_of: date) -> dict[str, object]:
    """The named parameters that the SQL files may use: the calculation date, the purge and the edges of the blocks of the split (as ISO dates)."""
    s = cfg.split
    return {
        "as_of": as_of.isoformat(), "purge_days": s.purge_days,
        "train_start": s.train_start.isoformat(), "train_end": s.train_end.isoformat(), "valid_start": s.valid_start.isoformat(),
        "valid_v2_start": s.valid_v2_start.isoformat(), "valid_end": s.valid_end.isoformat(), "test_start": s.test_start.isoformat(),
        "test_end": s.test_end.isoformat(), "post_break_start": s.post_break_start.isoformat(), "post_break_end": s.post_break_end.isoformat(),
    }


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _declared_tables(path: Path) -> dict[str, dict[str, str]]:
    """``{table: {column: declared type}}`` of a schema file, read by creating its tables in memory (once per version of the file: it is cached by its size and time)."""
    stat = Path(path).stat()
    return _declared_tables_cached(str(path), stat.st_size, stat.st_mtime_ns)


@functools.lru_cache(maxsize=16)
def _declared_tables_cached(path: str, size: int, mtime_ns: int) -> dict[str, dict[str, str]]:
    del size, mtime_ns            # only part of the cache key
    scratch = sqlite3.connect(":memory:")
    try:
        for statement in split_statements(Path(path).read_text(encoding="utf-8")):
            scratch.execute(statement)
        names = [r[0] for r in scratch.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {t: {r[1]: (r[2] or "").upper() for r in scratch.execute(f'PRAGMA table_info("{t}")')} for t in names}
    finally:
        scratch.close()


def _check_no_drift(conn: sqlite3.Connection, path: Path) -> None:
    """Raise ``SchemaDriftError`` if a table that already exists has other columns than the file declares."""
    for table, declared in _declared_tables(path).items():
        if not _table_exists(conn, table):
            continue
        actual = {r[1]: (r[2] or "").upper() for r in conn.execute(f'PRAGMA table_info("{table}")')}
        missing = tuple(c for c in declared if c not in actual)
        unexpected = tuple(c for c in actual if c not in declared)
        retyped = tuple(c for c in declared if c in actual and declared[c] != actual[c])
        if missing or unexpected or retyped:
            raise SchemaDriftError(table, Path(path).name, missing, unexpected, retyped)


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create the tables of ``sql/schema`` that do not exist yet (idempotent; keeps the data).

    A table that exists with other columns than its SQL file declares is not migrated: it raises ``SchemaDriftError`` naming the table and the columns,
    instead of failing later with a generic error.
    """
    for path in sorted((SQL_DIR / "schema").glob("*.sql")):
        _check_no_drift(conn, path)
        run_sql_file(conn, path)


# --- controls --------------------------------------------------------------------------------------------------------------


def run_checks(conn: sqlite3.Connection, group: Literal["load", "model"], params: Mapping[str, object] | None = None) -> list[CheckResult]:
    """Run the controls of a group; each file selects the rows that break its rule (none = it passes). `params` binds the named parameters they use."""
    if group not in CHECK_GROUPS:
        raise ValueError(f"grupo de controles desconocido: {group!r}; se esperaba uno de {', '.join(CHECK_GROUPS)}")
    bound = dict(params or {})
    results = []
    for path in sorted((SQL_DIR / "checks" / group).glob("chk_*.sql")):
        statements = split_statements(path.read_text(encoding="utf-8"))
        if len(statements) != 1:
            raise ValueError(f"{path.name} debe tener exactamente un SELECT y tiene {len(statements)} sentencias")
        select = statements[0].rstrip().rstrip(";")
        violations = conn.execute(f"SELECT COUNT(*) FROM ({select})", bound).fetchone()[0]
        sample = conn.execute(f"SELECT * FROM ({select}) LIMIT {SAMPLE_LIMIT}", bound).fetchall() if violations else []
        results.append(CheckResult(path.stem, int(violations), sample))
    return results


# --- load ------------------------------------------------------------------------------------------------------------------


def _column_values(frame: pd.DataFrame, field: pa.Field) -> list:
    """One column of a delivery as Python values that SQLite can store (dates as ISO text, booleans as 0 or 1)."""
    column = frame[field.name]
    if pa.types.is_date32(field.type):
        return column.dt.strftime("%Y-%m-%d").tolist()
    if pa.types.is_boolean(field.type):
        return column.astype(bool).astype(int).tolist()
    if pa.types.is_integer(field.type):
        return column.astype("int64").tolist()
    if pa.types.is_floating(field.type):
        return column.astype("float64").tolist()
    return column.astype(str).tolist()


def _insert_rows(conn: sqlite3.Connection, table: str, schema: pa.Schema, frame: pd.DataFrame, delivery_id: str) -> None:
    """Insert the rows of `frame` into `table` as part of the transaction already open."""
    if frame.empty:
        return
    names = ["delivery_id", *schema.names]
    values = [[delivery_id] * len(frame), *(_column_values(frame, field) for field in schema)]
    marks = ", ".join("?" for _ in names)
    conn.executemany(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({marks})", zip(*values, strict=True))


def _build_layers(conn: sqlite3.Connection, params: Mapping[str, object]) -> None:
    """Rebuild the staging tables and the operational marts for the calculation date in `params`, inside the transaction already open."""
    for name in (*STAGING, *OPS_MARTS):
        run_sql_file(conn, SQL_DIR / name, params)


def _count(conn: sqlite3.Connection, table: str, where: str = "", args: tuple = ()) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0])


def load_delivery(
    conn: sqlite3.Connection, delivery: DeliveryFrames, spec: DeliverySpec, run_key: str, cfg: PipelineConfig, *, sha256_json: str,
) -> LoadResult:
    """Add a validated delivery to the warehouse and rebuild the staging tables (and the marts) up to its calculation date, in one transaction.

    `sha256_json` is the fingerprint of the delivery in its manifest, recorded in ``deliveries``. A delivery with no orders and no outcomes or one already
    loaded is refused; if anything fails, including a key that repeats or a control, the transaction is rolled back and the warehouse keeps exactly what
    it had, down to the bytes of the file.
    """
    if len(delivery.orders) + len(delivery.outcomes) == 0:
        raise EmptyDeliveryError(run_key, spec.delivery_id)
    params = sql_params(cfg, spec.as_of)
    conn.execute("BEGIN IMMEDIATE")
    try:
        apply_schema(conn)
        existing = conn.execute("SELECT status FROM deliveries WHERE delivery_id = ?", (spec.delivery_id,)).fetchone()
        if existing is not None and existing[0] == "loaded":
            raise DeliveryAlreadyLoadedError(spec.delivery_id)

        for table, schema, attribute in _RAW:
            _insert_rows(conn, table, schema, getattr(delivery, attribute), spec.delivery_id)
        conn.execute(
            "INSERT INTO deliveries (delivery_id, kind, orders_from, orders_to, outcomes_from, outcomes_to, as_of_date, orders, lines, outcomes, sha256_json, "
            "status, run_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'loaded', ?) ON CONFLICT(delivery_id) DO UPDATE SET kind = excluded.kind, "
            "orders_from = excluded.orders_from, orders_to = excluded.orders_to, outcomes_from = excluded.outcomes_from, outcomes_to = excluded.outcomes_to, "
            "as_of_date = excluded.as_of_date, orders = excluded.orders, lines = excluded.lines, outcomes = excluded.outcomes, "
            "sha256_json = excluded.sha256_json, status = 'loaded', run_key = excluded.run_key",
            (
                spec.delivery_id, spec.kind, spec.orders_from and spec.orders_from.isoformat(), spec.orders_to and spec.orders_to.isoformat(),
                spec.outcomes_from.isoformat(), spec.outcomes_to.isoformat(), spec.as_of.isoformat(), len(delivery.orders), len(delivery.order_lines),
                len(delivery.outcomes), sha256_json, run_key,
            ),
        )
        _build_layers(conn, params)

        checks = run_checks(conn, "load", params)
        failing = [c for c in checks if c.violations]
        for c in failing:
            logger.error("load %s rolled back: control %s violations=%d sample=%s", run_key, c.check_id, c.violations, c.sample)
        if failing:
            raise CheckFailedError(checks)
        loaded = {attribute: _count(conn, table, "WHERE delivery_id = ?", (spec.delivery_id,)) for table, _schema, attribute in _RAW}
        expected = {attribute: len(getattr(delivery, attribute)) for _table, _schema, attribute in _RAW}
        if loaded != expected:
            raise RuntimeError(f"La carga {run_key} se deshizo: se esperaban {expected} filas de {spec.delivery_id} y hay {loaded}")
        staged = {"orders": _count(conn, "stg_orders"), "order_lines": _count(conn, "stg_order_lines"), "outcomes": _count(conn, "stg_outcomes")}
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        logger.warning("load %s rolled back: the warehouse keeps what it had", run_key)
        raise
    logger.info("load %s: delivery=%s rows=%s staged=%s controls=%d", run_key, spec.delivery_id, loaded, staged, len(checks))
    return LoadResult(spec.delivery_id, spec.as_of, loaded["orders"], loaded["order_lines"], loaded["outcomes"], staged, checks)


def rebuild_at(conn: sqlite3.Connection, as_of: date, cfg: PipelineConfig) -> list[CheckResult]:
    """Rebuild the staging tables and the operational marts for the calculation date `as_of` from the raw tables, which are not touched.

    This is how anything that is computed for a date gets what was known on it, whatever the warehouse holds since (RNF06): the staging tables take the
    orders created up to the date and the outcomes known up to it, and the labelled orders only those with such an outcome. It is one transaction, with the
    controls of the load: a control with violations undoes it (``CheckFailedError``). Returns the results of the controls.
    """
    params = sql_params(cfg, as_of)
    conn.execute("BEGIN IMMEDIATE")
    try:
        apply_schema(conn)
        _build_layers(conn, params)
        checks = run_checks(conn, "load", params)
        failing = [c for c in checks if c.violations]
        if failing:
            raise CheckFailedError(checks)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    logger.info("rebuilt the staging tables and the marts at %s", as_of)
    return checks


# --- run records ------------------------------------------------------------------------------------------------------------


def record_run(conn: sqlite3.Connection, run: dict) -> None:
    """Insert the run in ``pipeline_runs`` or update only the columns given for its ``run_key``."""
    if "run_key" not in run:
        raise ValueError("un registro de corrida necesita run_key")
    unknown = sorted(set(run) - {"run_key", *RUN_COLUMNS})
    if unknown:
        raise ValueError(f"columna desconocida de pipeline_runs: {unknown[0]!r}")
    given = [c for c in RUN_COLUMNS if c in run]
    names = ", ".join(["run_key", *given])
    marks = ", ".join("?" for _ in range(len(given) + 1))
    update = ", ".join(f"{c} = excluded.{c}" for c in given) or "run_key = run_key"
    conn.execute(
        f"INSERT INTO pipeline_runs ({names}) VALUES ({marks}) ON CONFLICT(run_key) DO UPDATE SET {update}",
        [run["run_key"], *(run[c] for c in given)],
    )


def record_validation_results(conn: sqlite3.Connection, report: ValidationReport) -> None:
    """Store the rule results of a report, replacing the rows of the same ``run_key`` and rule."""
    rows = [
        (report.run_key, r.rule_id, r.name_es, SEVERITY_ES[r.severity], r.scope, int(r.passed), r.rows_affected, r.orders_affected,
         json.dumps(r.examples, ensure_ascii=False))
        for r in report.results
    ]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany("DELETE FROM validation_results WHERE run_key = ? AND rule_id = ?", [(report.run_key, r[1]) for r in rows])
        conn.executemany("INSERT INTO validation_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise

