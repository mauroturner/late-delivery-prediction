"""The star schema for Power BI: model marts in SQLite and one Parquet file per table (RF21, AC24, decision D21, docs/plan.md section 16).

``build_powerbi_marts`` rebuilds the dimensions and facts (tables ``star_<name>``) from the warehouse with the SQL files of ``sql/marts/powerbi`` and runs the controls
of ``sql/checks/model`` in the same transaction: if one returns rows, the marts keep what they had. ``export_star_schema`` writes each table of the semantic model with the
Arrow schema the model declares and the run, the calculation date, the source and the attribution as file metadata. ``verify_star_schema`` reads the files back and checks the
contract that Power BI relies on (AC24): schema, metadata, keys and referential integrity of every relationship.

The same shape as project 02's export, adapted: no personal column exists in the warehouse (they are dropped when the source is read, D07), so none can reach a file.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from latedel.config import PipelineConfig
from latedel.dashboard.semantic_model import ARROW_TYPES, SemanticModel, Table, arrow_schema, validate_model
from latedel.evaluation import METHOD_LABELS_ES, METHODS
from latedel.files import write_atomic
from latedel.registry import read_seal
from latedel.schema import ATTRIBUTION
from latedel.warehouse import SQL_DIR, CheckFailedError, apply_schema, require_json_functions, run_checks, run_sql_file, sql_params

logger = logging.getLogger(__name__)

MAX_PARQUET_BYTES = 25 * 1024 * 1024  # a versioned file must stay under GitHub's limits (RNF10)
STAR_PREFIX = "star_"
RUN_TABLE = "dim_pipeline_run"  # rewritten alone when a run closes or fails after the export
MODEL_BUILD = (    # the dimensions first: fact_order reads dim_geo
    "dim_date", "dim_month", "dim_shipping_mode", "dim_geo", "dim_product", "dim_segment", "dim_payment", "dim_model", "dim_pipeline_run", "dim_as_of",
    "fact_order", "fact_tracking_monthly", "fact_method_metrics", "fact_evaluation_summary", "fact_roc", "fact_calibration", "fact_cost_sensitivity", "fact_leakage",
    "fact_importance", "fact_validation",
)
METADATA_KEYS = ("latedel.run_key", "latedel.as_of", "latedel.source_sha256", "latedel.attribution")
BLOCK_LABELS_ES = {            # the block of the split, as the calendar and the month dimensions name it
    "train": "Entrenamiento", "purge_1": "Purga 1", "valid_v1": "Validación V1", "valid_v2": "Validación V2", "purge_2": "Purga 2", "test": "Prueba",
    "post_break": "Posterior al cambio de forma",
}


@dataclass(frozen=True)
class ExportMeta:
    """What every Parquet file says about the run that wrote it (RNF07, RNF09). Not the ``RunMeta`` of ``scoring``, which is what a score records."""

    run_key: str
    as_of: str  # the calculation date, YYYY-MM-DD
    source_sha256: str
    attribution: str = ATTRIBUTION

    def metadata(self) -> dict[str, str]:
        return {"latedel.run_key": self.run_key, "latedel.as_of": self.as_of, "latedel.source_sha256": self.source_sha256, "latedel.attribution": self.attribution}


def _params(conn: sqlite3.Connection, cfg: PipelineConfig) -> dict[str, object]:
    """The named parameters of the marts and the controls: the edges of the split, the first and last day of the calendar and the last calculation date loaded."""
    last = conn.execute("SELECT MAX(as_of_date) FROM deliveries WHERE status = 'loaded'").fetchone()[0]
    if last is None:
        raise RuntimeError("Falta una entrega cargada: cargue una entrega validada antes de exportar el modelo estrella")
    params = sql_params(cfg, date.fromisoformat(last))
    params["first_date"] = cfg.deliveries.first_order_date.isoformat()
    params["last_date"] = cfg.deliveries.close_date.isoformat()
    return params


def split_blocks(cfg: PipelineConfig) -> list[tuple[str, str, str, str]]:
    """``(block, block_es, first_day, last_day)`` of each block of the split, from ``[split]`` (D08): the one place where the edges become blocks for the model marts.

    The purges are the days between two blocks; a purge of no days has no row, and a date that no row holds is ``outside``. Any other block without days, and two blocks
    that share a day, are a ``ValueError`` that names them (review of H4, D78): an empty post-break block made every month ``pre_break``, and a day in two blocks repeated
    its orders. ``load_config`` refuses both already; this is the second line for a configuration built without it.
    """
    s, day = cfg.split, timedelta(days=1)
    edges = {
        "train": (s.train_start, s.train_end), "purge_1": (s.train_end + day, s.valid_start - day), "valid_v1": (s.valid_start, s.valid_v2_start - day),
        "valid_v2": (s.valid_v2_start, s.valid_end), "purge_2": (s.valid_end + day, s.test_start - day), "test": (s.test_start, s.test_end),
        "post_break": (s.post_break_start, s.post_break_end),
    }
    for block, (first, last) in edges.items():
        if first > last and not block.startswith("purge"):
            raise ValueError(f"[split]: el bloque {block} no tiene días (del {first} al {last}): sin él los marts del modelo estrella no pueden ubicar sus días")
    present = sorted((first, last, block) for block, (first, last) in edges.items() if first <= last)
    for (first, last, block), (later_first, later_last, later) in zip(present, present[1:]):
        if later_first <= last:
            raise ValueError(
                f"[split]: los bloques {block} (del {first} al {last}) y {later} (del {later_first} al {later_last}) se superponen: un día estaría en dos bloques "
                "y sus pedidos se repetirían en los marts"
            )
    return [(block, BLOCK_LABELS_ES[block], first.isoformat(), last.isoformat()) for block, (first, last) in edges.items() if first <= last]


def _load_references(conn: sqlite3.Connection, cfg: PipelineConfig) -> None:
    """``ref_method`` (the Spanish label of each method), ``ref_block`` (the blocks of the split with their first and last day) and ``ref_seal`` (whether each registered
    configuration is the sealed one), from the code, ``[split]`` and the seal file. The calendar, the months and the orders join ``ref_block``: the edges are not written again."""
    conn.execute("DROP TABLE IF EXISTS ref_method")
    conn.execute("CREATE TABLE ref_method (method TEXT PRIMARY KEY, label_es TEXT NOT NULL, sort_order INTEGER NOT NULL)")
    conn.executemany("INSERT INTO ref_method VALUES (?, ?, ?)", [(m, METHOD_LABELS_ES[m], i) for i, m in enumerate(METHODS, start=1)])
    conn.execute("DROP TABLE IF EXISTS ref_block")
    conn.execute("CREATE TABLE ref_block (block TEXT PRIMARY KEY, block_es TEXT NOT NULL, first_day TEXT NOT NULL, last_day TEXT NOT NULL)")
    conn.executemany("INSERT INTO ref_block VALUES (?, ?, ?, ?)", split_blocks(cfg))
    seal = read_seal(cfg.paths.seal)
    conn.execute("DROP TABLE IF EXISTS ref_seal")
    conn.execute("CREATE TABLE ref_seal (model_version TEXT PRIMARY KEY, seal_state TEXT NOT NULL)")
    rows = []
    for version, config_sha256 in conn.execute("SELECT model_version, config_sha256 FROM model_registry").fetchall():
        state = "none" if seal is None else "sealed" if seal["launch"]["config_sha256"] == config_sha256 else "post_seal"
        rows.append((version, state))
    conn.executemany("INSERT INTO ref_seal VALUES (?, ?)", rows)


def build_powerbi_marts(conn: sqlite3.Connection, cfg: PipelineConfig) -> None:
    """Rebuild the dimensions and facts and run the model controls, all in one transaction.

    Needs at least one loaded delivery and the JSON functions of SQLite (``require_json_functions``). If a control returns rows, ``CheckFailedError`` is raised and the
    marts keep what they had; a split with a block without days or two blocks that share a day is a ``ValueError`` before anything is built.
    """
    require_json_functions(conn)
    split_blocks(cfg)
    apply_schema(conn)
    params = _params(conn, cfg)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _load_references(conn, cfg)
        for name in MODEL_BUILD:
            run_sql_file(conn, SQL_DIR / "marts" / "powerbi" / f"{name}.sql", params)
        checks = run_checks(conn, "model", params)
        failing = [c for c in checks if c.violations]
        for c in failing:
            logger.error("model control %s: violations=%d sample=%s", c.check_id, c.violations, c.sample)
        if failing:
            raise CheckFailedError(checks)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    logger.info("power bi marts rebuilt: %d controls passed", len(checks))


def _read_table(conn: sqlite3.Connection, table: Table) -> pd.DataFrame:
    columns = ", ".join(table.column_names)
    order = ", ".join(table.key)
    return pd.read_sql_query(f"SELECT {columns} FROM {STAR_PREFIX}{table.name} ORDER BY {order}", conn)


def _arrow_table(frame: pd.DataFrame, table: Table) -> pa.Table:
    """The frame as an Arrow table with the model's schema; a key with a missing value is an error."""
    for key in table.key:
        if frame[key].isna().any():
            raise ValueError(f"{table.name}[{key}] tiene valores vacíos: una clave no puede")
    arrays = []
    for column in table.columns:
        series, kind = frame[column.name], ARROW_TYPES[column.dtype]
        if column.dtype == "date":
            values = pd.to_datetime(series, errors="raise").dt.date.astype(object).where(series.notna(), None)
            arrays.append(pa.array(values, type=kind))
        elif column.dtype == "bool":
            arrays.append(pa.Array.from_pandas(series.astype("Int64").astype("boolean"), type=kind))
        elif column.dtype in ("int32", "int64"):
            arrays.append(pa.Array.from_pandas(pd.array(series, dtype="Int64"), type=kind))
        elif column.dtype == "float64":
            arrays.append(pa.Array.from_pandas(pd.to_numeric(series).astype("float64"), type=kind))
        else:
            arrays.append(pa.array(series.astype(object).where(series.notna(), None), type=kind))
    return pa.Table.from_arrays(arrays, schema=arrow_schema(table))


def _serialize(conn: sqlite3.Connection, table: Table, metadata: dict[str, str]) -> bytes:
    """The Parquet file of a table as bytes (so its size is known before anything is written)."""
    arrow = _arrow_table(_read_table(conn, table), table).replace_schema_metadata(metadata)
    sink = pa.BufferOutputStream()
    pq.write_table(arrow, sink, compression="snappy")
    return sink.getvalue().to_pybytes()


def export_run_table(conn: sqlite3.Connection, out_dir: Path, model: SemanticModel, meta: ExportMeta) -> Path:
    """Rebuild ``dim_pipeline_run`` from ``pipeline_runs`` and rewrite only its Parquet file, atomically.

    The run table is the one file that changes when a run closes or fails after the export, so Power BI, which reads the files and not SQLite, sees the same status
    as SQLite.
    """
    [table] = [t for t in model.tables if t.name == RUN_TABLE]
    conn.execute("BEGIN IMMEDIATE")
    try:
        run_sql_file(conn, SQL_DIR / "marts" / "powerbi" / f"{RUN_TABLE}.sql")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return write_atomic(Path(out_dir) / f"{RUN_TABLE}.parquet", _serialize(conn, table, meta.metadata()))


def export_star_schema(conn: sqlite3.Connection, out_dir: Path, model: SemanticModel, meta: ExportMeta, cfg: PipelineConfig) -> list[Path]:
    """Rebuild the marts and write one Parquet file per table of `model`; returns the paths in the model's order.

    The model is checked first, the controls run before any file is written, and so does the size check: a table over ``MAX_PARQUET_BYTES`` is an error and nothing is
    written. Each file is replaced atomically. The run table shows each run as SQLite has it: the run being exported is still ``running``.
    """
    problems = validate_model(model)
    if problems:
        raise ValueError(f"El modelo semántico es incoherente: {problems[0]}")
    build_powerbi_marts(conn, cfg)
    blobs = {table.name: _serialize(conn, table, meta.metadata()) for table in model.tables}
    heavy = [f"{name} ({len(blob):,} bytes)" for name, blob in blobs.items() if len(blob) > MAX_PARQUET_BYTES]
    if heavy:
        raise ValueError(f"Un Parquet versionado pesa a lo sumo {MAX_PARQUET_BYTES:,} bytes y pasan del tope: {', '.join(heavy)}")
    out_dir = Path(out_dir)
    paths = []
    for table in model.tables:
        path = write_atomic(out_dir / f"{table.name}.parquet", blobs[table.name])
        logger.info("exported %s: %d rows, %d bytes", path.name, pq.ParquetFile(path).metadata.num_rows, path.stat().st_size)
        paths.append(path)
    return paths


def verify_star_schema(out_dir: Path, model: SemanticModel) -> list[str]:
    """Read the Parquet files back and list every way they break the contract (empty when they keep it, AC24).

    Checks: the model itself (keys, relationships), the schema and metadata of every file, non-null keys, unique grains and referential integrity of every
    relationship (all foreign keys exist, none is empty).
    """
    problems = [f"model: {p}" for p in validate_model(model)]
    out_dir = Path(out_dir)
    frames: dict[str, pd.DataFrame] = {}
    for table in model.tables:
        path = out_dir / f"{table.name}.parquet"
        if not path.exists():
            problems.append(f"{table.name}: {path.name} does not exist")
            continue
        file = pq.ParquetFile(path)
        if not file.schema_arrow.equals(arrow_schema(table), check_metadata=False):
            problems.append(f"{table.name}: the schema of {path.name} differs from the model's")
            continue
        metadata = {k.decode(): v.decode() for k, v in (file.schema_arrow.metadata or {}).items()}
        missing = [k for k in METADATA_KEYS if not metadata.get(k)]
        if missing:
            problems.append(f"{table.name}: metadata {', '.join(missing)} missing")
        frame = file.read().to_pandas()
        frames[table.name] = frame
        for key in table.key:
            if frame[key].isna().any():
                problems.append(f"{table.name}[{key}]: has empty values")
        if frame.duplicated(list(table.key)).any():
            problems.append(f"{table.name}: the key {list(table.key)} is not unique")
    for r in model.relationships:
        if r.dim_table not in frames or r.fact_table not in frames:
            continue
        fact_values = frames[r.fact_table][r.fact_column]
        known = set(frames[r.dim_table][r.dim_column].dropna())
        empty = int(fact_values.isna().sum())
        orphans = int((~fact_values.dropna().isin(known)).sum())
        if empty or orphans:
            problems.append(f"{r.rel_id} {r.fact_table}[{r.fact_column}]: {orphans} values not in {r.dim_table}, {empty} empty")
    return problems
