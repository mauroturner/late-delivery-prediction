"""The pipeline as one tuple of tasks that the CLI and the Airflow DAG both walk (RF24, plan section 19, decision D35).

Every task is a function ``fn(ctx) -> dict`` with no dependency on Airflow. Tasks pass state to each other through files and tables, never through the orchestrator: each
run has ``data/interim/runs/<run_key>.json`` with what every task produced.

Three rules keep the records honest (they come from the evaluations of projects 01 and 02):

* A rejection, or a failure before ``load``, writes nothing to SQLite and nothing to the exports (AC11). Its record is the state file and ``reports/validation/history``.
* From ``load`` on, the run is ``running`` in SQLite, in ``dim_pipeline_run.parquet`` and in ``summary.json``. Only the last task, ``report``, closes it, in the order of
  ``CLOSING_STEPS``: SQLite, the exported run table alone, the summary alone, the HTML, the manifest, and only then the state file says ``ok`` (decision D72). A process
  killed anywhere before the end leaves nothing a person reads that says ``ok`` while another file still says ``running``, and the same ``report`` run again finishes
  the close from any step. A task that fails after the load marks the run ``failed`` in SQLite and rewrites the files that exist.
* A run that ended is not opened again, and a task whose delivery is not the last one loaded is refused before it writes anything (AC28). The refusals come first and write
  nothing.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from latedel import ingest
from latedel.config import PipelineConfig, load_config
from latedel.dashboard.render import render_design
from latedel.dashboard.semantic_model import MODEL
from latedel.deliveries import (
    MANIFEST_NAME,
    DeliverySpec,
    check_coverage,
    delivery_plan,
    mark_status,
    next_pending,
    publish,
    read_delivery,
    read_manifest,
)
from latedel.evaluation import evaluate as evaluate_model
from latedel.evaluation import record_launch_tables
from latedel.export import RUN_TABLE, ExportMeta, export_run_table, export_star_schema, verify_star_schema
from latedel.files import write_atomic
from latedel.ingest import load_source_report, read_interim
from latedel.launch import fit_model as fit_frozen_model
from latedel.launch import load_registered_model, m05_result
from latedel.reporting import load_summary, write_summary
from latedel.schema import SourceError
from latedel.scoring import RunMeta, score_new_orders
from latedel.site_export import export_site, export_summary, verify_site
from latedel.sysinfo import MemoryPeaks, peak_memory
from latedel.tracking import track as track_follow_up
from latedel.validate import (
    RuleResult,
    ValidationError,
    combine_reports,
    make_result,
    read_report,
    validate_published_delivery,
    validate_source,
    with_results,
    write_report,
)
from latedel.warehouse import apply_schema, connect, load_delivery, record_run, record_validation_results

logger = logging.getLogger(__name__)

RUN_KEY_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
DELIVERIES_DIR = "deliveries"
SITE_SUMMARY = "summary.json"
EXPORTED_META_FILE = "fact_order.parquet"      # written by the export only, so the run, date and source it carries are those of the last export
MODEL_TASK_IDS = ("fit_model", "score", "track", "evaluate")   # the tasks that calculate the latest loaded date
CLOSED_STATUSES = ("ok", "no_delivery")        # the states of a run that ended: nothing runs in it again
SUMMARY_TEXT_CHARS = 200                       # the longest text a task summary carries to the orchestrator
CLOSING_STEPS = ("sqlite", "run_table", "summary", "html", "manifest")   # the order in which ``report`` closes a run (decision D72)
LAUNCH_TABLES = ("feature_importance", "eval_leakage")                     # written by the launch only; a launch interrupted before them is completed by its repetition


class UsageError(ValueError):
    """The run was asked for something that cannot be done as asked (the CLI exits with 2)."""


class RunStateError(RuntimeError):
    """The state of the run or of the warehouse does not allow the task: say what to do instead of guessing."""


class RunRefusedError(RunStateError):
    """A task was refused before it started, because the run is not in a state that can take it: nothing was written (the CLI exits with 2)."""


class ExportError(RuntimeError):
    """The exported files do not keep the contract of the star schema."""


@dataclass(frozen=True)
class RunContext:
    cfg: PipelineConfig
    run_key: str
    started_at: datetime
    state_path: Path


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    func: Callable[[RunContext], dict]
    retries: int
    retry_delay_min: int
    timeout_min: int
    short_circuit: bool = False


# --- run key and state ------------------------------------------------------------------------------------------


def make_run_key(source: str | datetime) -> str:
    """The key of a run: from an Airflow ``run_id`` (made safe for file names) or from a UTC time (to the microsecond: runs in a row differ)."""
    if isinstance(source, datetime):
        return source.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    key = RUN_KEY_UNSAFE.sub("_", str(source)).strip("_")
    if not key:
        raise ValueError("una clave de corrida no puede estar vacía")
    return key


def _state_path(cfg: PipelineConfig, run_key: str) -> Path:
    return Path(cfg.paths.interim_dir) / "runs" / f"{run_key}.json"


def _read_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _write_state(path: Path, state: dict) -> None:
    write_atomic(path, json.dumps(state, indent=2, ensure_ascii=False, default=str) + "\n")


def _context(cfg: PipelineConfig, run_id: str | None, now: datetime | None) -> RunContext:
    """The context of a run; ``started_at`` is fixed by the first task and kept in the state file."""
    when = (now or datetime.now(UTC)).astimezone(UTC)
    run_key = make_run_key(run_id if run_id is not None else when)
    path = _state_path(cfg, run_key)
    state = _read_state(path)
    if not state:
        state = {"run_key": run_key, "started_at": when.isoformat(timespec="seconds"), "status": "running", "tasks": {}}
        _write_state(path, state)
    return RunContext(cfg, run_key, datetime.fromisoformat(state["started_at"]), path)


def _task_result(ctx: RunContext, task_id: str) -> dict:
    """What a task of this run produced; a task that did not run (or failed) is an error that says which to run."""
    result = _read_state(ctx.state_path).get("tasks", {}).get(task_id)
    if result is None or result.get("status") in ("failed", "rejected"):
        raise RunStateError(
            f"La corrida {ctx.run_key} no tiene el resultado de la tarea {task_id}: corra esa tarea antes "
            f"(python -m latedel task {task_id} --run-key {ctx.run_key})"
        )
    return result


def _save_task(ctx: RunContext, task_id: str, result: dict, status: str | None = None) -> None:
    state = _read_state(ctx.state_path)
    state.setdefault("tasks", {})[task_id] = result
    if status:
        state["status"] = status
    if status == "ok":  # the close wrote every exported file from SQLite: an earlier refresh that failed does not matter any more
        state.pop("exports_stale", None)
    _write_state(ctx.state_path, state)


def _mark_rejected(ctx: RunContext, task_id: str, report) -> None:
    state = _read_state(ctx.state_path)
    state["status"] = "rejected"
    failing = [r.rule_id for r in report.results if not r.passed and r.severity.value == "blocking"]
    state.setdefault("tasks", {})[task_id] = {"status": "rejected", "rules": failing}
    _write_state(ctx.state_path, state)


def _now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _peak() -> MemoryPeaks | None:
    """The peak memory of the process, or None: it is telemetry, so whatever stops it from being measured (the system will not say, there is no ``resource`` module) is
    logged with its traceback and never discards the result of a task that has finished."""
    try:
        return peak_memory()
    except Exception:
        logger.warning("could not measure the peak memory", exc_info=True)
        return None


def task_summary(task_id: str, result: dict) -> dict:
    """What a task tells the orchestrator (the Airflow UI shows it): the task and the flat part of its result, small and JSON.

    Only booleans, numbers, short text and ``None`` are kept (long text is cut at ``SUMMARY_TEXT_CHARS``, a number that is not finite becomes ``None``); lists, tables and
    nested records stay in the run's state file, which is where the whole result is.
    """
    summary: dict = {}
    for key, value in result.items():
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        elif isinstance(value, str) and len(value) > SUMMARY_TEXT_CHARS:
            value = value[: SUMMARY_TEXT_CHARS - 1] + "…"
        elif not (value is None or isinstance(value, bool | int | float | str)):
            continue
        summary[key] = value
    summary["task"] = task_id  # last: a result with a key of that name must not replace the task it came from
    return {"task": summary.pop("task"), **summary}


def _refresh_or_mark_stale(ctx: RunContext, *, summary: bool, html: bool, when: str) -> bool:
    """Bring the exported files in line with SQLite as well as it can be done; True if it could not, and then the state file says so.

    The caller's outcome stays what it was (a failure is still the failure, a run with no delivery still ends as such): a refresh that fails is not allowed to replace it.
    But it is not silent either: it is logged with its traceback and the state of the run keeps ``exports_stale`` (what failed) until a refresh works, because the files
    Power BI reads would otherwise keep describing the run as it was before.
    """
    try:
        _refresh_exports(ctx, summary=summary, html=html)
    except Exception as error:
        logger.warning("could not refresh the exported files %s: %s", when, error, exc_info=True)
        mark = f"{type(error).__name__}: {error}"[:300]
        try:
            state = _read_state(ctx.state_path)
            state["exports_stale"] = mark
            _write_state(ctx.state_path, state)
        except Exception:
            logger.warning("could not record in %s that the exported files are stale", ctx.state_path, exc_info=True)
        return True
    try:  # clearing the mark is bookkeeping too: if it fails, the caller's outcome still stands
        state = _read_state(ctx.state_path)
        if "exports_stale" in state:  # an earlier refresh failed and this one worked
            del state["exports_stale"]
            _write_state(ctx.state_path, state)
    except Exception:
        logger.warning("could not clear the stale mark in %s after a refresh that worked", ctx.state_path, exc_info=True)
    return False


# --- the warehouse, as the tasks need it ----------------------------------------------------------------------------


def _record(ctx: RunContext, **fields) -> None:
    """Write fields of this run to ``pipeline_runs`` (the row is created on the first call)."""
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        apply_schema(conn)
        record_run(conn, {"run_key": ctx.run_key, **fields})
    finally:
        conn.close()


def _read_only(cfg: PipelineConfig) -> sqlite3.Connection:
    """The warehouse for reading; before the first load there is none, and asking must not create it (AC11)."""
    path = Path(cfg.paths.warehouse)
    if not path.exists():
        return sqlite3.connect(":memory:")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _has_record(ctx: RunContext) -> bool:
    if not Path(ctx.cfg.paths.warehouse).exists():
        return False
    conn = _read_only(ctx.cfg)
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'pipeline_runs'").fetchone()
        return bool(exists and conn.execute("SELECT 1 FROM pipeline_runs WHERE run_key = ?", (ctx.run_key,)).fetchone())
    finally:
        conn.close()


def _loaded_by(conn: sqlite3.Connection, delivery_id: str) -> str | None:
    """The run that loaded the delivery into this warehouse, or None."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'deliveries'").fetchone():
        return None
    row = conn.execute("SELECT run_key FROM deliveries WHERE delivery_id = ? AND status = 'loaded'", (delivery_id,)).fetchone()
    return None if row is None else row[0]


def _latest_loaded_as_of(conn: sqlite3.Connection) -> str | None:
    """The calculation date of the last delivery the warehouse holds, or None when it holds none."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'deliveries'").fetchone():
        return None
    row = conn.execute("SELECT MAX(as_of_date) FROM deliveries WHERE status = 'loaded'").fetchone()
    return None if row is None else row[0]


def _deliveries_dir(cfg: PipelineConfig) -> Path:
    return Path(cfg.paths.raw_dir) / DELIVERIES_DIR


def _manifest_path(cfg: PipelineConfig) -> Path:
    return _deliveries_dir(cfg) / MANIFEST_NAME


def _validation_dir(cfg: PipelineConfig) -> Path:
    return Path(cfg.paths.reports_dir) / "validation"


def _spec_of(cfg: PipelineConfig, delivery_id: str) -> DeliverySpec:
    [spec] = [s for s in delivery_plan(cfg.deliveries) if s.delivery_id == delivery_id]
    return spec


def _entry_of(cfg: PipelineConfig, delivery_id: str):
    [entry] = [e for e in read_manifest(_manifest_path(cfg)) if e.delivery_id == delivery_id]
    return entry


def _meta(ctx: RunContext) -> ExportMeta:
    """What the files of this run say about it: the run, the calculation date of its delivery and the source."""
    return ExportMeta(ctx.run_key, _task_result(ctx, "deliver")["as_of"], _task_result(ctx, "fetch_source")["sha256"])


def _exported_meta(cfg: PipelineConfig) -> ExportMeta | None:
    """The run, date and source that the exported model files describe (read from a file only the export writes), or None."""
    path = Path(cfg.paths.outputs_dir) / EXPORTED_META_FILE
    if not path.exists():
        return None
    metadata = {k.decode(): v.decode() for k, v in (pq.read_schema(path).metadata or {}).items()}
    keys = ("latedel.run_key", "latedel.as_of", "latedel.source_sha256")
    return ExportMeta(*(metadata[k] for k in keys)) if all(k in metadata for k in keys) else None


def _add_rules(ctx: RunContext, results: list[RuleResult]) -> None:
    """Add rule results of the model steps to the report of this run (its file and the table of SQLite): the report lists a rule once it was evaluated."""
    folder = _validation_dir(ctx.cfg)
    report = with_results(read_report(folder / "history" / f"{ctx.run_key}.json"), results)
    write_report(report, folder)
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        record_validation_results(conn, report)
    finally:
        conn.close()


# --- tasks ----------------------------------------------------------------------------------------------------------


def fetch_source(ctx: RunContext) -> dict:
    """The CSV is the pinned one and the three streams are built from it (RF01-RF03); a source rule that fails rejects the run."""
    try:
        return ingest.fetch_source(ctx)
    except SourceError as error:
        report = validate_source(error, run_key=ctx.run_key)
        write_report(report, _validation_dir(ctx.cfg))
        _mark_rejected(ctx, "fetch_source", report)
        logger.error("run %s rejected: %s", ctx.run_key, ValidationError(report))
        raise ValidationError(report) from error


def _refuse_a_delivery_loaded_without_close(ctx: RunContext, spec: DeliverySpec) -> None:
    """A delivery the warehouse holds but the manifest does not call loaded belongs to a run that never closed: resume that one."""
    if not Path(ctx.cfg.paths.warehouse).exists():
        return
    conn = _read_only(ctx.cfg)
    try:
        owner = _loaded_by(conn, spec.delivery_id)
        status = None
        if owner is not None and conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'pipeline_runs'").fetchone():
            row = conn.execute("SELECT status FROM pipeline_runs WHERE run_key = ?", (owner,)).fetchone()
            status = None if row is None else row[0]
    finally:
        conn.close()
    if owner is not None and owner != ctx.run_key:
        raise RunStateError(
            f"La entrega {spec.delivery_id} ya está cargada en el almacén por la corrida {owner} (estado {status or 'sin registro'}), pero el manifiesto "
            f"no la marca cargada. Si esa corrida no llegó a cerrarse, retómela con `--run-key {owner} --from <tarea>` en lugar de empezar otra; "
            "si el manifiesto se tocó a mano, vea docs/runbook.md, R4"
        )


def deliver(ctx: RunContext) -> dict:
    """Publish the next pending delivery; with none left, the run ends as ``no_delivery`` (RF06)."""
    cfg = ctx.cfg
    frames, _report = read_interim(cfg.paths.interim_dir)
    plan = delivery_plan(cfg.deliveries)
    check_coverage(frames, plan)
    spec = next_pending(_manifest_path(cfg), plan)
    fetched = _task_result(ctx, "fetch_source")
    if spec is None:
        message = f"No hay entregas pendientes: las {len(plan)} del plan ya están cargadas"
        _record(
            ctx, started_at=ctx.started_at.isoformat(timespec="seconds"), finished_at=_now_text(), status="no_delivery",
            source_sha256=fetched["sha256"], message=message,
        )
        logger.info("run %s: %s", ctx.run_key, message)
        # Power BI reads the exported files, not SQLite: the run table and the HTML (which lists the runs) say this run too (the result says if they could not)
        stale = _refresh_or_mark_stale(ctx, summary=False, html=True, when="after a no_delivery run")
        return {"delivered": False, "run_status": "no_delivery", "message": message, "exports_stale": stale}
    _refuse_a_delivery_loaded_without_close(ctx, spec)
    entry = publish(spec, frames, _deliveries_dir(cfg), source_sha256=fetched["sha256"])
    return {
        "delivered": True, "delivery_id": spec.delivery_id, "kind": spec.kind, "period_start": spec.outcomes_from.isoformat(), "period_end": spec.outcomes_to.isoformat(),
        "as_of": spec.as_of.isoformat(), "orders": entry.orders, "lines": entry.lines, "outcomes": entry.outcomes, "sha256": entry.sha256_json,
        "path": str(_deliveries_dir(cfg) / spec.delivery_id),
    }


def validate(ctx: RunContext) -> dict:
    """Validate the delivery; a blocking rule rejects the run, writing nothing to SQLite and leaving the exports as they are (RF07, AC11)."""
    cfg = ctx.cfg
    delivered, fetched = _task_result(ctx, "deliver"), _task_result(ctx, "fetch_source")
    spec = _spec_of(cfg, delivered["delivery_id"])
    entry = _entry_of(cfg, spec.delivery_id)
    conn = _read_only(cfg)
    try:
        by_delivery = validate_published_delivery(_deliveries_dir(cfg), entry, spec, conn, cfg, run_key=ctx.run_key)
    finally:
        conn.close()
    source = load_source_report(cfg.paths.interim_dir)
    if source is None:
        raise RunStateError(f"Falta el reporte de la fuente en {cfg.paths.interim_dir}: corra fetch_source (python -m latedel task fetch_source --run-key {ctx.run_key})")
    report = combine_reports(validate_source(source, run_key=ctx.run_key, source_sha256=fetched["sha256"]), by_delivery)
    write_report(report, _validation_dir(cfg))
    if report.blocking_failed:
        mark_status(_manifest_path(cfg), spec.delivery_id, "rejected", ctx.run_key)  # the files stay where they are; it is the pending delivery
        _mark_rejected(ctx, "validate", report)
        logger.error("run %s rejected: %s", ctx.run_key, ValidationError(report))
        raise ValidationError(report)
    return {
        "delivery_id": spec.delivery_id, "orders": entry.orders,
        "warnings": [r.rule_id for r in report.results if not r.passed and r.severity.value == "warning"],
        "not_evaluated": list(report.not_evaluated),
    }


def load(ctx: RunContext) -> dict:
    """Load the delivery in one transaction and open the run as ``running`` (RF08).

    A retry of a load that committed (the run record may not have been written) does not load again: the warehouse says this run loaded it.
    """
    cfg = ctx.cfg
    delivered, fetched = _task_result(ctx, "deliver"), _task_result(ctx, "fetch_source")
    spec = _spec_of(cfg, delivered["delivery_id"])
    entry = _entry_of(cfg, spec.delivery_id)
    frames = read_delivery(_deliveries_dir(cfg), entry)
    conn = connect(cfg.paths.warehouse)
    try:
        resumed = _loaded_by(conn, spec.delivery_id) == ctx.run_key
        controls = None
        if resumed:
            logger.info("run %s: %s is already in the warehouse; recording the run only", ctx.run_key, spec.delivery_id)
        else:
            controls = len(load_delivery(conn, frames, spec, ctx.run_key, cfg, sha256_json=entry.sha256_json).checks)
        report = read_report(_validation_dir(cfg) / "history" / f"{ctx.run_key}.json")
        record_run(conn, {
            "run_key": ctx.run_key, "started_at": ctx.started_at.isoformat(timespec="seconds"), "finished_at": None, "status": "running",
            "delivery_id": spec.delivery_id, "as_of_date": spec.as_of.isoformat(), "source_sha256": fetched["sha256"],
            "orders_delivery": len(frames.orders), "outcomes_delivery": len(frames.outcomes), "message": "",
        })
        record_validation_results(conn, report)
    finally:
        conn.close()
    return {"orders": len(frames.orders), "lines": len(frames.order_lines), "outcomes": len(frames.outcomes), "as_of": spec.as_of.isoformat(), "controls": controls, "resumed": resumed}


def fit_model(ctx: RunContext) -> dict:
    """Train and freeze the model at the launch, or check the frozen one (M03, M05); the launch also writes the importance and the leakage tables (RF16)."""
    cfg = ctx.cfg
    delivered, fetched = _task_result(ctx, "deliver"), _task_result(ctx, "fetch_source")
    as_of = date.fromisoformat(delivered["as_of"])
    conn = connect(cfg.paths.warehouse)
    try:
        result = fit_frozen_model(conn, cfg, run_key=ctx.run_key, as_of=as_of, source_sha256=fetched["sha256"])
        missing = any(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0 for table in LAUNCH_TABLES)
        tables = record_launch_tables(conn, cfg, as_of=as_of) if result["mode"] == "launch" or missing else {}
    finally:
        conn.close()
    rules = [make_result("M03", True), m05_result(result["seal_state"])]
    if result["mode"] == "launch":
        rules.insert(0, make_result("M01", True))
    _add_rules(ctx, rules)
    return {**result, "launch_tables": tables}


def score(ctx: RunContext) -> dict:
    """Score once each new order of the delivery with the frozen model (RF17, M02, M04). The launch and the close have no new orders."""
    cfg = ctx.cfg
    delivered = _task_result(ctx, "deliver")
    if delivered["kind"] == "launch":    # the launch trains the model on its orders: they are history, not new orders, and the model has not been fixed before they exist (D17)
        return {"delivery_id": delivered["delivery_id"], "scored": 0, "already_scored": 0, "alerts": 0, "skipped": "launch", "as_of": delivered["as_of"]}
    conn = connect(cfg.paths.warehouse)
    try:
        registered = load_registered_model(conn, cfg)
        meta = RunMeta(ctx.run_key, date.fromisoformat(delivered["as_of"]), registered.model_version)
        scored = score_new_orders(conn, delivered["delivery_id"], registered.model, registered.threshold, meta)
    finally:
        conn.close()
    _add_rules(ctx, [make_result("M02", True), make_result("M04", True)])
    return {
        "delivery_id": scored.delivery_id, "scored": scored.scored, "already_scored": scored.already_scored, "alerts": scored.alerts, "threshold": scored.threshold,
        "model_version": scored.model_version, "as_of": scored.as_of.isoformat(),
    }


def track(ctx: RunContext) -> dict:
    """The follow-up of the alerts that closed, with only what was known on the calculation date (RF18, I02, I04)."""
    delivered = _task_result(ctx, "deliver")
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        followed = track_follow_up(conn, date.fromisoformat(delivered["as_of"]))
    finally:
        conn.close()
    _add_rules(ctx, followed.rules)
    return followed.summary()


def evaluate(ctx: RunContext) -> dict:
    """The figures of V2 on every run; the test once, when its labels are all known; the post-break block likewise (RF19)."""
    cfg = ctx.cfg
    delivered, fitted = _task_result(ctx, "deliver"), _task_result(ctx, "fit_model")
    conn = connect(cfg.paths.warehouse)
    try:
        outcome = evaluate_model(conn, cfg, run_key=ctx.run_key, as_of=date.fromisoformat(delivered["as_of"]), config_sha256=fitted["config_sha256"])
    finally:
        conn.close()
    return {
        "label": outcome.label, "test_evaluated": outcome.test is not None, "test_seal": outcome.test_seal, "post_break_evaluated": outcome.post_break is not None,
        "criteria": {c.criterion_id: c.passed for c in outcome.test_criteria}, "post_break_criterion": outcome.post_break_criterion.passed if outcome.post_break_criterion else None,
    }


def export(ctx: RunContext) -> dict:
    """Rebuild the model marts and write the star schema and the site files, read back and checked (RF21, RF23).

    The run stays ``running`` here, in SQLite and in the exported files: only ``report`` closes it.
    """
    cfg, meta = ctx.cfg, _meta(ctx)
    conn = connect(cfg.paths.warehouse)
    try:
        paths = export_star_schema(conn, cfg.paths.outputs_dir, MODEL, meta, cfg)
        problems = verify_star_schema(cfg.paths.outputs_dir, MODEL)
        if problems:
            raise ExportError(f"el modelo estrella exportado rompe su contrato: {problems[0]} ({len(problems)} problemas)")
        site = export_site(conn, cfg.paths.site_dir, meta, cfg)
    finally:
        conn.close()
    unread = verify_site(cfg.paths.site_dir)   # read back like the star schema: the five files and the manifest written last are one export (D77)
    if unread:
        raise ExportError(f"los archivos del sitio no son una misma exportación: {'; '.join(unread)}")
    return {"files": [str(p) for p in paths], "site_files": [str(p) for p in site], "bytes": sum(p.stat().st_size for p in [*paths, *site])}


def render_dashboard(ctx: RunContext) -> dict:
    """The HTML design of the report from the semantic model and the exported tables (RF22)."""
    cfg = ctx.cfg
    path = render_design(MODEL, cfg.paths.outputs_dir, cfg.paths.dashboard_html, _meta(ctx), min_cell_orders=cfg.site.min_cell_orders)
    return {"html": str(path), "bytes": path.stat().st_size}


def _run_peaks(ctx: RunContext) -> tuple[float | None, float | None]:
    """The largest working set and the largest committed memory of the tasks of this run and of the process now (None where no task measured it)."""
    tasks = _read_state(ctx.state_path).get("tasks", {}).values()
    now = _peak()
    working = [t.get("peak_working_set_mb") for t in tasks] + [now.working_set_mb if now else None]
    commit = [t.get("peak_commit_mb") for t in tasks] + [now.commit_mb if now else None]
    working, commit = [p for p in working if p is not None], [p for p in commit if p is not None]
    return (max(working) if working else None, max(commit) if commit else None)


# --- the close of a run -----------------------------------------------------------------------------------------------


def _close_sqlite(ctx: RunContext) -> None:
    delivered = _task_result(ctx, "deliver")
    working, commit = _run_peaks(ctx)
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        scored, alerts = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(s.is_alert), 0) FROM model_scores s JOIN raw_orders o ON o.order_id = s.order_id WHERE o.delivery_id = ?", (delivered["delivery_id"],)
        ).fetchone()
        record_run(conn, {
            "run_key": ctx.run_key, "status": "ok", "finished_at": _now_text(), "orders_scored": int(scored), "alerts": int(alerts),
            "peak_working_set_mb": None if working is None else round(working, 1), "peak_commit_mb": None if commit is None else round(commit, 1), "message": "ok",
        })
    finally:
        conn.close()


def _close_run_table(ctx: RunContext) -> None:
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        export_run_table(conn, ctx.cfg.paths.outputs_dir, MODEL, _meta(ctx))
    finally:
        conn.close()


def _close_summary(ctx: RunContext) -> None:
    conn = connect(ctx.cfg.paths.warehouse)
    try:
        export_summary(conn, ctx.cfg.paths.site_dir, _meta(ctx), ctx.cfg)
    finally:
        conn.close()


def _close_html(ctx: RunContext) -> None:
    cfg = ctx.cfg
    render_design(MODEL, cfg.paths.outputs_dir, cfg.paths.dashboard_html, _meta(ctx), min_cell_orders=cfg.site.min_cell_orders)


def _close_manifest(ctx: RunContext) -> None:
    mark_status(_manifest_path(ctx.cfg), _task_result(ctx, "deliver")["delivery_id"], "loaded", ctx.run_key)


_CLOSERS = {"sqlite": _close_sqlite, "run_table": _close_run_table, "summary": _close_summary, "html": _close_html, "manifest": _close_manifest}
assert tuple(_CLOSERS) == CLOSING_STEPS


def _close_run(ctx: RunContext) -> None:
    """Close the run in the order that leaves nothing saying ``ok`` that is not: SQLite, the exported run table alone, the summary alone, the HTML, the manifest (D72).

    A step that raises is handled by the task wrapper, which marks the run ``failed`` and rewrites what exists. A process that dies between two steps leaves the state file
    on ``running``, so the same ``report`` can run again and do every step again with the same result (each is a rewrite from SQLite and the last, ``mark_status``, accepts
    its own run).
    """
    for step in CLOSING_STEPS:
        _CLOSERS[step](ctx)


def report(ctx: RunContext) -> dict:
    """Write the evaluation summary and close the run (RF20)."""
    cfg = ctx.cfg
    conn = connect(cfg.paths.warehouse)
    try:
        summary = load_summary(conn, cfg, run_key=ctx.run_key, as_of=date.fromisoformat(_task_result(ctx, "deliver")["as_of"]))
    finally:
        conn.close()
    json_path, md_path = write_summary(summary, cfg.paths.reports_dir)
    _close_run(ctx)
    return {
        "summary_json": str(json_path), "summary_md": str(md_path), "test_evaluated": summary.test_evaluated, "failed_criteria": [c.criterion_id for c in summary.failed],
        "run_status": "ok",
    }


def _refresh_exports(ctx: RunContext, *, summary: bool, html: bool) -> None:
    """Bring the files that were exported before in line with SQLite: the run table, the summary if it is this run's, and the HTML.

    Nothing is created that was not there. The run table is rewritten from SQLite; the summary only when it describes this run (it carries the run's status); the HTML
    always, since it shows the runs.
    """
    cfg = ctx.cfg
    if not (Path(cfg.paths.outputs_dir) / f"{RUN_TABLE}.parquet").exists():
        return
    exported = _exported_meta(cfg)
    try:
        own = _meta(ctx)
    except RunStateError:
        own = None
    shown = exported or own
    if shown is None:
        return
    conn = connect(cfg.paths.warehouse)
    try:
        export_run_table(conn, cfg.paths.outputs_dir, MODEL, replace(shown, run_key=ctx.run_key))
        summary_path = Path(cfg.paths.site_dir) / SITE_SUMMARY
        if summary and own is not None and summary_path.exists() and json.loads(summary_path.read_text(encoding="utf-8")).get("run_key") == ctx.run_key:
            export_summary(conn, cfg.paths.site_dir, own, cfg)
    finally:
        conn.close()
    if html and Path(cfg.paths.dashboard_html).exists():
        render_design(MODEL, cfg.paths.outputs_dir, cfg.paths.dashboard_html, shown, min_cell_orders=cfg.site.min_cell_orders)


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec("fetch_source", fetch_source, 3, 10, 20),
    TaskSpec("deliver", deliver, 0, 0, 5, short_circuit=True),
    TaskSpec("validate", validate, 0, 0, 10),
    TaskSpec("load", load, 1, 5, 20),
    TaskSpec("fit_model", fit_model, 1, 5, 20),
    TaskSpec("score", score, 1, 5, 10),
    TaskSpec("track", track, 1, 5, 10),
    TaskSpec("evaluate", evaluate, 1, 5, 20),
    TaskSpec("export", export, 1, 5, 10),
    TaskSpec("render_dashboard", render_dashboard, 1, 5, 10),
    TaskSpec("report", report, 1, 5, 5),
)
TASK_IDS = tuple(t.task_id for t in TASKS)


# --- running tasks ----------------------------------------------------------------------------------------------------


def _spec(task_id: str) -> TaskSpec:
    for spec in TASKS:
        if spec.task_id == task_id:
            return spec
    raise UsageError(f"tarea desconocida {task_id!r}; las tareas son {', '.join(TASK_IDS)}")


def _reopen(ctx: RunContext) -> None:
    """A run that failed and is resumed is in progress again, in the state file and in SQLite, until it closes or fails again."""
    state = _read_state(ctx.state_path)
    if state.get("status") != "failed":
        return
    state["status"] = "running"
    _write_state(ctx.state_path, state)
    if _has_record(ctx):
        _record(ctx, status="running", finished_at=None, message="")
        _refresh_or_mark_stale(ctx, summary=True, html=True, when=f"when resuming {ctx.run_key}")


def _refuse_a_closed_run(spec: TaskSpec, ctx: RunContext) -> None:
    """A run that ended (``ok`` or ``no_delivery``) is a record of what it did: no task is run in it again (AC28).

    ``run --run-key <closed run> --from deliver`` would publish and load the next delivery under the closed run's key, overwriting its row in SQLite, in the exported run
    table and in its state file. A closed run is refused before anything is written; the next delivery is another run.
    """
    status = _read_state(ctx.state_path).get("status")
    if status in CLOSED_STATUSES:
        raise RunRefusedError(
            f"La corrida {ctx.run_key} está cerrada (estado {status}): no se vuelve a abrir para correr {spec.task_id}. "
            "Para cargar otra entrega empiece otra corrida (python -m latedel run)"
        )


def _refuse_a_second_delivery(spec: TaskSpec, ctx: RunContext) -> None:
    """A run that already took a delivery keeps it: ``deliver`` again would give it the next one and replace the first in its records."""
    if spec.task_id != "deliver":
        return
    held = _read_state(ctx.state_path).get("tasks", {}).get("deliver", {})
    if not held.get("delivered"):
        return
    cfg = ctx.cfg
    pending = next_pending(_manifest_path(cfg), delivery_plan(cfg.deliveries))
    if pending is None or pending.delivery_id != held["delivery_id"]:
        then = "ninguna" if pending is None else pending.delivery_id
        raise RunRefusedError(
            f"La corrida {ctx.run_key} ya tiene la entrega {held['delivery_id']}, y la siguiente pendiente es {then}: una corrida no cambia de entrega. "
            "Empiece otra corrida (python -m latedel run)"
        )


def _refuse_an_older_date(spec: TaskSpec, ctx: RunContext) -> None:
    """The model tasks calculate the latest loaded date, so the run they belong to must be the run of that delivery.

    ``task score --run-key <older run>`` would calculate the latest date and record it in the state of the older run (or of a run that had not loaded yet). A task that
    would calculate a date other than its run's is refused before anything is written.
    """
    if spec.task_id not in MODEL_TASK_IDS:
        return
    held = _read_state(ctx.state_path).get("tasks", {}).get("deliver", {})
    if not held.get("delivered"):
        return  # no delivery in this run: the task has its own error for that
    conn = _read_only(ctx.cfg)
    try:
        latest = _latest_loaded_as_of(conn)
    finally:
        conn.close()
    if latest is None:
        return  # nothing is loaded at all: the task says so
    if latest != held["as_of"]:
        raise RunRefusedError(
            f"La corrida {ctx.run_key} es de la entrega {held['delivery_id']} (cálculo al {held['as_of']}), pero la última entrega cargada es la del {latest} "
            f"y la tarea {spec.task_id} calcula esa fecha: no puede correr en esta corrida. "
            + ("Cargue primero su entrega (python -m latedel task load --run-key " + ctx.run_key + ")" if held["as_of"] > latest else "Para calcular otra fecha empiece otra corrida (python -m latedel run)")
        )


def _refuse_after_a_short_circuit(spec: TaskSpec, ctx: RunContext) -> None:
    """A run that ended at a short circuit (no delivery) has nothing for the tasks after it: say so instead of failing on the missing keys.

    ``run_all`` stops its loop there; this is the same rule for the caller of ``run_task``, which the Airflow DAG is not (its short circuit skips the rest) but a person or
    another scheduler can be. The run is not marked failed: it ended as it should.
    """
    tasks = _read_state(ctx.state_path).get("tasks", {})
    for earlier in TASKS[: TASK_IDS.index(spec.task_id)]:
        if earlier.short_circuit and not tasks.get(earlier.task_id, {}).get("delivered", True):
            raise RunRefusedError(
                f"La corrida {ctx.run_key} terminó en la tarea {earlier.task_id} sin entrega (no_delivery): no hay nada que hacer en la tarea "
                f"{spec.task_id}. Para cargar otra entrega empiece otra corrida (python -m latedel run)"
            )


def _record_failure(ctx: RunContext, spec: TaskSpec, error: BaseException) -> None:
    """Record a failed task: the state file, the row of SQLite and the files already exported.

    Recording the failure is bookkeeping: a full disk or a locked database here must not replace the error of the task, which is what the caller has to see. Each write is
    tried on its own, logged with its traceback if it fails, and the next one goes on (AC27).
    """
    message = f"{spec.task_id}: {type(error).__name__}: {error}"[:500]
    try:
        state = _read_state(ctx.state_path)
        state["status"] = "failed"
        state.setdefault("tasks", {})[spec.task_id] = {"status": "failed", "error": message, "traceback": "".join(traceback.format_exception(error))}
        _write_state(ctx.state_path, state)
    except Exception:
        logger.warning("could not record the failure of %s in %s", spec.task_id, ctx.state_path, exc_info=True)
    try:
        if _has_record(ctx):  # the run reached the load: SQLite and the files already exported must not keep saying it is running or done
            _record(ctx, status="failed", finished_at=_now_text(), message=message)
    except Exception:
        logger.warning("could not record the failure of %s in the warehouse of run %s", spec.task_id, ctx.run_key, exc_info=True)
    try:
        if _has_record(ctx):
            _refresh_or_mark_stale(ctx, summary=True, html=True, when=f"after {spec.task_id} failed")
    except Exception:
        logger.warning("could not refresh the exported files of run %s after %s failed", ctx.run_key, spec.task_id, exc_info=True)


def _run_spec(spec: TaskSpec, ctx: RunContext) -> dict:
    _refuse_a_closed_run(spec, ctx)
    _refuse_after_a_short_circuit(spec, ctx)
    _refuse_a_second_delivery(spec, ctx)
    _refuse_an_older_date(spec, ctx)
    started = time.perf_counter()
    logger.info("task %s start (run %s)", spec.task_id, ctx.run_key)
    _reopen(ctx)
    try:
        result = spec.func(ctx)
    except ValidationError:
        raise  # the run was rejected: the task already said so, and nothing was written to SQLite
    except BaseException as error:
        _record_failure(ctx, spec, error)
        logger.exception("task %s failed after %.1f s: %s", spec.task_id, time.perf_counter() - started, error, exc_info=error)
        raise
    if "seconds" in result:  # a task that times its own stages keeps them; ``seconds`` is the wall time of the whole task
        result = {**{k: v for k, v in result.items() if k != "seconds"}, "stage_seconds": result["seconds"]}
    peaks = _peak()
    result = {
        **result, "seconds": round(time.perf_counter() - started, 2), "peak_working_set_mb": None if peaks is None else round(peaks.working_set_mb, 1),
        "peak_commit_mb": None if peaks is None or peaks.commit_mb is None else round(peaks.commit_mb, 1), "memory_counter": None if peaks is None else peaks.counter,
    }
    _save_task(ctx, spec.task_id, result, status=result.get("run_status"))
    logger.info("task %s end in %.1f s", spec.task_id, result["seconds"])
    return result


def run_task(task_id: str, run_id: str | None = None, cfg: PipelineConfig | None = None, *, now: datetime | None = None) -> dict:
    """Run one task of the run `run_id` (a new run if none) and return what it produced: what the Airflow DAG calls."""
    spec = _spec(task_id)
    return _run_spec(spec, _context(cfg or load_config(), run_id, now))


def _select(only: list[str] | None, start: str | None) -> list[str]:
    """The task ids to run, in the order of the flow; an unknown task or the two selections together is a usage error (nothing is written)."""
    if only is not None and not only:
        raise UsageError("`only` está vacío: indique al menos una tarea (o no lo indique para correr todas)")
    if only and start is not None:
        raise UsageError("`only` y `start` no se combinan: elija las tareas o desde cuál seguir")
    ids = list(TASK_IDS)
    if start is not None:
        _spec(start)
        ids = ids[ids.index(start):]
    if only:
        for task_id in only:
            _spec(task_id)
        ids = [i for i in ids if i in only]
    return ids


def run_all(
    cfg: PipelineConfig, only: list[str] | None = None, start: str | None = None, run_key: str | None = None,
    *, now: datetime | None = None, on_task: Callable[[str, dict], None] | None = None,
) -> list[dict]:
    """Run the tasks in order: all, only the listed ones, or from `start` on; stops after a run with no delivery.

    Returns one ``{"task", "result"}`` per task that ran; `on_task` is called with each one as it ends, so a caller can show progress even if a later task fails.
    ``ValidationError`` is not caught: the run was rejected.
    """
    ids = _select(only, start)
    ctx = _context(cfg, run_key, now)
    outcome = []
    for task_id in ids:
        spec = _spec(task_id)
        result = _run_spec(spec, ctx)
        outcome.append({"task": task_id, "result": result})
        if on_task is not None:
            on_task(task_id, result)
        if spec.short_circuit and not result.get("delivered", True):
            logger.info("run %s ends after %s: there is no delivery", ctx.run_key, task_id)
            break
    return outcome


# --- replay: the runs of the plan in a row ---------------------------------------------------------------------------------------


def _last_index(cfg: PipelineConfig, until: str | None, plan: list[DeliverySpec]) -> int:
    """The index in `plan` of the last delivery a replay may run; an `until` that cannot be run as asked is a usage error."""
    if until is None:
        return len(plan) - 1
    ids = [spec.delivery_id for spec in plan]
    if until not in ids:
        raise UsageError(f"--until {until}: no es una entrega del plan ({', '.join(ids)})")
    last = ids.index(until)
    pending = next_pending(_manifest_path(cfg), plan)
    if pending is not None and plan.index(pending) > last:
        raise UsageError(f"--until {until} es anterior a la siguiente entrega pendiente, {pending.delivery_id}")
    return last


def replay(cfg: PipelineConfig, until: str | None = None, *, on_task: Callable[[str, dict], None] | None = None) -> list[list[dict]]:
    """Run the pending deliveries one run after another, until none is left or the delivery `until` (its id, for example ``M2017-03``) has been run.

    Each run is a complete run of the next pending delivery; the outcomes come back one list per run. With nothing pending, nothing runs and nothing is recorded (a run that
    finds no delivery is ``run_all``'s, not this loop's). A failure or a rejection stops the replay.
    """
    plan = delivery_plan(cfg.deliveries)
    last = _last_index(cfg, until, plan)
    runs: list[list[dict]] = []
    while (pending := next_pending(_manifest_path(cfg), plan)) is not None and plan.index(pending) <= last:
        runs.append(run_all(cfg, on_task=on_task))
        again = next_pending(_manifest_path(cfg), plan)
        if again == pending:
            raise RunStateError(f"La corrida de {pending.delivery_id} terminó sin cargar la entrega: el replay se detiene en vez de repetirla")
    return runs
