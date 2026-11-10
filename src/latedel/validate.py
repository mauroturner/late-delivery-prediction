"""Validation of the source and of every monthly delivery (RF07, decision D19).

The rules are listed in ``RULES`` with their identifier, their severity and the scope in which they are evaluated:

- the **source** rules V01 to V09 are blocking and V10 to V18 are warnings (V10 to V14) and information (V15 to V18). The first task already ran the blocking
  ones while it fetched and read the CSV, so ``validate_source`` turns its outcome into rule results: a ``SourceError`` that names the failing rule, or the
  ``SourceReport`` of a clean read, whose counts are the warnings and the information;
- the **delivery** rules V20 to V24 (blocking) and I01, I03 and I05 (information) are evaluated by ``validate_delivery`` against the plan, the warehouse (which
  is only read) and the manifest entry of the delivery;
- the **model** rules M01 to M05 (M05 is a warning) and I02 and I04 are produced by the steps of the model, which build their results with ``make_result`` and
  add them to the same report with ``with_results``.

A rule that could not be evaluated because an earlier blocking rule failed is listed in ``not_evaluated``; it is never reported as passed. ``rows_affected``
counts the rows of the tables that the rule finds wrong (the lines of a source; the orders, lines and outcomes of a delivery) and ``orders_affected`` the
orders they belong to. A report never carries the value of a personal column (decision D39) nor a path of this
machine, and the examples of a rejection are cut to printable ASCII and a short length.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from latedel.config import PipelineConfig
from latedel.deliveries import (
    DELIVERY_FILES,
    DeliveryError,
    DeliveryFrames,
    DeliverySpec,
    ManifestEntry,
    delivery_digest,
    delivery_plan,
    read_delivery,
)
from latedel.files import write_atomic
from latedel.ingest import MONEY_TOLERANCE, SourceReport, _clean
from latedel.schema import SCHEDULED_DAYS_BY_MODE, SourceError

logger = logging.getLogger(__name__)

EXAMPLE_LIMIT = 5
_SAFE_RUN_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PATH_KEYS = frozenset({"kept"})   # keys of a problem of a SourceError that hold a path of this machine


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


SEVERITY_ES = {Severity.BLOCKING: "bloqueante", Severity.WARNING: "advertencia", Severity.INFO: "informativa"}


@dataclass(frozen=True)
class Rule:
    name_es: str
    severity: Severity
    scope: str  # "source" (evaluated once per fingerprint), "delivery" (every run) or "model"


# rule id -> rule. The order is the order of the report.
RULES: dict[str, Rule] = {
    "V01": Rule("El CSV tiene la huella aceptada (o la registrada, en modo local)", Severity.BLOCKING, "source"),
    "V02": Rule("Descarga segura y tamaño: HTTPS en cada salto, host permitido, redirecciones y bytes dentro del límite", Severity.BLOCKING, "source"),
    "V03": Rule("Codificación cp1252 estricta, sin bytes nulos ni marca de orden de bytes, con saltos CRLF o LF", Severity.BLOCKING, "source"),
    "V04": Rule("El encabezado tiene las 53 columnas exactas, en orden", Severity.BLOCKING, "source"),
    "V05": Rule("Cada fila tiene 53 campos y las fechas, los enteros y los decimales convierten", Severity.BLOCKING, "source"),
    "V06": Rule("El correo y la contraseña del cliente están enmascarados en todas las filas", Severity.BLOCKING, "source"),
    "V07": Rule("Cada línea tiene su identificador y cada pedido un solo valor en sus columnas constantes", Severity.BLOCKING, "source"),
    "V08": Rule("En los pedidos no cancelados, la etiqueta es «días reales mayores que los programados»", Severity.BLOCKING, "source"),
    "V09": Rule("Cada resultado se conoce dentro de los días que cubre la purga", Severity.BLOCKING, "source"),
    "V10": Rule("Pedido con días programados distintos de los de su modo de envío", Severity.WARNING, "source"),
    "V11": Rule("Pedido cancelado (queda fuera de la tarea; se puntúa si es nuevo)", Severity.WARNING, "source"),
    "V12": Rule("Columnas repetidas que no coinciden (se usa una copia)", Severity.WARNING, "source"),
    "V13": Rule("Importes que no cuadran: ventas contra precio por cantidad, o total contra ventas menos descuento", Severity.WARNING, "source"),
    "V14": Rule("Espacios finales en el nombre del producto (se recortan)", Severity.WARNING, "source"),
    "V15": Rule("Columnas personales descartadas en la lectura", Severity.INFO, "source"),
    "V16": Rule("Columnas constantes, vacías o repetidas que no se llevan", Severity.INFO, "source"),
    "V17": Rule("Pedidos desde el cambio de forma de 2017-10 (régimen posterior)", Severity.INFO, "source"),
    "V18": Rule("Columnas apartadas al flujo de resultados", Severity.INFO, "source"),
    "V20": Rule("La entrega es la siguiente del plan y sus períodos empiezan el día siguiente al fin de los de la anterior", Severity.BLOCKING, "delivery"),
    "V21": Rule("Cada pedido tiene su fecha dentro del período de pedidos y cada resultado la suya dentro del de resultados", Severity.BLOCKING, "delivery"),
    "V22": Rule("Ningún pedido, línea ni resultado está ya cargado ni repetido dentro de la entrega", Severity.BLOCKING, "delivery"),
    "V23": Rule("Cada línea y cada resultado corresponde a un pedido cargado o de la misma entrega", Severity.BLOCKING, "delivery"),
    "V24": Rule("La entrega no está vacía y sus filas y su SHA-256 son los del manifiesto", Severity.BLOCKING, "delivery"),
    "M01": Rule("Trampa de fuga: el AUC del candidato elegido en la primera mitad de la validación está por debajo del límite", Severity.BLOCKING, "model"),
    "M02": Rule("Las columnas de la matriz son las del conjunto elegido y ninguna es de resultado ni personal", Severity.BLOCKING, "model"),
    "M03": Rule("El artefacto del modelo tiene la huella registrada (se comprueba antes de cargarlo)", Severity.BLOCKING, "model"),
    "M04": Rule("Cada pedido nuevo tiene una probabilidad finita en [0, 1]", Severity.BLOCKING, "model"),
    "M05": Rule("La configuración es distinta de la sellada (el resultado se guarda aparte, como posterior al sello)", Severity.WARNING, "model"),
    "I01": Rule("Pedidos nuevos en la entrega", Severity.INFO, "delivery"),
    "I02": Rule("Alertas emitidas y porcentaje de pedidos alertados", Severity.INFO, "model"),
    "I03": Rule("Resultados que llegaron en la entrega", Severity.INFO, "delivery"),
    "I04": Rule("Alertas sobre pedidos que después se cancelaron", Severity.INFO, "model"),
    "I05": Rule("Pedidos de la entrega desde el cambio de forma de 2017-10", Severity.INFO, "delivery"),
}
SOURCE_RULES = tuple(rule_id for rule_id, rule in RULES.items() if rule.scope == "source")
SOURCE_BLOCKING = tuple(rule_id for rule_id in SOURCE_RULES if RULES[rule_id].severity is Severity.BLOCKING)
DELIVERY_RULES = tuple(rule_id for rule_id, rule in RULES.items() if rule.scope == "delivery")
MODEL_RULES = tuple(rule_id for rule_id, rule in RULES.items() if rule.scope == "model")


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    name_es: str
    severity: Severity
    scope: str
    passed: bool
    rows_affected: int
    orders_affected: int
    examples: list[dict]

    @property
    def status_es(self) -> str:
        if self.passed:
            return "pasa"
        return "falla" if self.severity is Severity.BLOCKING else "advierte"


@dataclass(frozen=True)
class ValidationReport:
    run_key: str
    source_sha256: str
    delivery_id: str | None
    rows_delivery: int
    results: list[RuleResult]
    not_evaluated: tuple[str, ...] = field(default=())

    @property
    def blocking_failed(self) -> bool:
        return any(r.severity is Severity.BLOCKING and not r.passed for r in self.results)


class ValidationError(RuntimeError):
    """The run was rejected: at least one blocking rule failed (raised by the ``validate`` task of the pipeline)."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        failing = ", ".join(r.rule_id for r in report.results if r.severity is Severity.BLOCKING and not r.passed)
        super().__init__(f"la validación de {report.run_key} rechazó la corrida: fallan las reglas bloqueantes {failing}")


# --- helpers ------------------------------------------------------------------------------------------------------------


def make_result(rule_id: str, passed: bool, rows: int = 0, orders: int = 0, examples: list[dict] | None = None) -> RuleResult:
    """The result of `rule_id`. An information rule always passes: it only counts."""
    rule = RULES[rule_id]
    return RuleResult(rule_id, rule.name_es, rule.severity, rule.scope, True if rule.severity is Severity.INFO else bool(passed), int(rows), int(orders), examples or [])


def _ordered(results: dict[str, RuleResult]) -> list[RuleResult]:
    return [results[rule_id] for rule_id in RULES if rule_id in results]


def _log_verdict(report: ValidationReport) -> None:
    blocking = [r.rule_id for r in report.results if r.severity is Severity.BLOCKING and not r.passed]
    warnings = [r.rule_id for r in report.results if r.severity is Severity.WARNING and not r.passed]
    logger.log(
        logging.WARNING if blocking else logging.INFO,
        "validation of %s (%s): %s; blocking failed: %s; warnings: %s; not evaluated: %s",
        report.run_key, report.delivery_id or "source", "rejected" if blocking else "accepted",
        ", ".join(blocking) or "none", ", ".join(warnings) or "none", ", ".join(report.not_evaluated) or "none",
    )


# --- the rules of the source -----------------------------------------------------------------------------------------------


def _tidy(value: Any) -> Any:
    """A JSON-friendly value for a report: text cut to printable ASCII and a short length, nested structures cleaned the same way."""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _tidy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_tidy(item) for item in list(value)[:EXAMPLE_LIMIT * 4]]
    return _clean(str(value))


def _source_problem(problem: dict[str, Any]) -> dict[str, Any]:
    """A problem of a ``SourceError`` as an example of the report: JSON-safe, short and without a path of this machine."""
    return {key: _tidy(value) for key, value in problem.items() if key not in _PATH_KEYS}


def _failure_counts(error: SourceError) -> tuple[int, int]:
    """``(rows, orders)`` that a rejection affects, from its problems. A rule about the file or its header affects neither: it says where."""
    rows = orders = 0
    for problem in error.problems:
        count = int(problem.get("count", 0))
        if error.rule_id in ("V05", "V06"):
            rows += count
        elif error.rule_id == "V07":
            if problem.get("kind") == "order_level_column":
                orders += int(problem.get("orders", count))
            else:
                rows += count
        elif error.rule_id in ("V08", "V09"):
            orders += count
    return rows, orders


def _evaluated_before(error: SourceError) -> tuple[str, ...]:
    """The blocking rules that were evaluated, and passed, before the one that failed.

    The order is that of the rules, except for a V02 that the download raised (an address or a body that is refused): the hash (V01) is compared only after the
    body is complete, so it was never evaluated. A V02 about the size of a file already on disk comes after the hash was checked.
    """
    index = SOURCE_BLOCKING.index(error.rule_id)
    before = SOURCE_BLOCKING[:index]
    if error.rule_id == "V02" and not any(p.get("kind") == "size" for p in error.problems):
        return ()
    return before


def validate_source(outcome: SourceError | SourceReport, *, run_key: str, source_sha256: str = "") -> ValidationReport:
    """The report of the source rules (V01 to V18) from what the first task found.

    A ``SourceError`` fails its rule (one of V01 to V09 and nothing else) and every rule that was not evaluated before it is listed in ``not_evaluated``.
    A ``SourceReport`` means that the whole file was read: V01 to V09 pass and its counts give V10 to V18.
    """
    results: dict[str, RuleResult] = {}
    if isinstance(outcome, SourceError):
        if outcome.rule_id not in SOURCE_BLOCKING:
            raise ValueError(f"{outcome.rule_id} no es una regla bloqueante de la fuente ({', '.join(SOURCE_BLOCKING)})")
        for rule_id in _evaluated_before(outcome):
            results[rule_id] = make_result(rule_id, True)
        rows, orders = _failure_counts(outcome)
        examples = [_source_problem(problem) for problem in outcome.problems[:EXAMPLE_LIMIT]]
        results[outcome.rule_id] = make_result(outcome.rule_id, False, rows, orders, examples)
        not_evaluated = tuple(rule_id for rule_id in SOURCE_RULES if rule_id not in results)
    else:
        for rule_id in SOURCE_BLOCKING:
            results[rule_id] = make_result(rule_id, True)
        results.update(_counted_rules(outcome))
        not_evaluated = ()
    report = ValidationReport(run_key, source_sha256, None, 0, _ordered(results), not_evaluated)
    _log_verdict(report)
    return report


def _counted_rules(report: SourceReport) -> dict[str, RuleResult]:
    """V10 to V18 from the counts that the reader made."""
    affected, counts = report.affected, report.counts

    def size(rule_id: str) -> tuple[int, int]:
        found = affected.get(rule_id)
        if found is None:
            raise ValueError(f"el reporte de la fuente no cuenta las filas y los pedidos de {rule_id}: es de una versión anterior y se reconstruye")
        return int(found["rows"]), int(found["orders"])

    out: dict[str, RuleResult] = {}
    rows, orders = size("V10")
    out["V10"] = make_result("V10", orders == 0, rows, orders, [{"expected_days": dict(SCHEDULED_DAYS_BY_MODE)}])
    rows, orders = size("V11")
    out["V11"] = make_result("V11", orders == 0, rows, orders)
    rows, orders = size("V12")
    out["V12"] = make_result("V12", rows == 0, rows, orders, [{"pairs": counts["V12"]["pairs"], "cells": counts["V12"]["discrepancies"]}])
    rows, orders = size("V13")
    out["V13"] = make_result("V13", rows == 0, rows, orders, [{"tolerance": MONEY_TOLERANCE}])
    rows, orders = size("V14")
    out["V14"] = make_result("V14", rows == 0, rows, orders)
    out["V15"] = make_result("V15", True, examples=[{"columns": counts["V15"]["columns"]}])
    out["V16"] = make_result("V16", True, examples=[{"columns": counts["V16"]["columns"]}])
    rows, orders = size("V17")
    out["V17"] = make_result("V17", True, rows, orders)
    out["V18"] = make_result("V18", True, examples=[{"columns": counts["V18"]["columns"]}])
    return out


# --- the rules of the delivery ----------------------------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _loaded_ids(conn: sqlite3.Connection, table: str, column: str) -> np.ndarray:
    """The values of one integer column of a warehouse table (an empty array while the warehouse has no such table). `table` and `column` are constants."""
    if not _table_exists(conn, table):
        return np.empty(0, dtype="int64")
    return np.fromiter((row[0] for row in conn.execute(f"SELECT {column} FROM {table}")), dtype="int64")


def _loaded_deliveries(conn: sqlite3.Connection) -> dict[str, str]:
    """``{delivery_id: outcomes_to}`` of the deliveries that the warehouse holds as loaded."""
    if not _table_exists(conn, "deliveries"):
        return {}
    return {row[0]: row[1] for row in conn.execute("SELECT delivery_id, outcomes_to FROM deliveries WHERE status = 'loaded'")}


def _iso(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def _size(delivery: DeliveryFrames) -> tuple[int, int]:
    """``(rows, orders)`` of a whole delivery: its three tables and the distinct orders they mention."""
    ids = set(delivery.orders["order_id"]) | set(delivery.order_lines["order_id"]) | set(delivery.outcomes["order_id"])
    return len(delivery.orders) + len(delivery.order_lines) + len(delivery.outcomes), len(ids)


def _period_text(spec: DeliverySpec) -> str:
    orders = f"{spec.orders_from}/{spec.orders_to}" if spec.orders_from else "none"
    return f"orders {orders}; outcomes {spec.outcomes_from}/{spec.outcomes_to}; as of {spec.as_of}"


def _rule_v20(delivery: DeliveryFrames, spec: DeliverySpec, conn: sqlite3.Connection, cfg: PipelineConfig) -> RuleResult:
    plan = delivery_plan(cfg.deliveries)
    index = next((i for i, planned in enumerate(plan) if planned.delivery_id == spec.delivery_id), None)
    problem: dict[str, Any] | None = None
    if index is None:
        problem = {"reason": "not_in_plan", "plan": [p.delivery_id for p in plan]}
    elif plan[index] != spec:
        problem = {"reason": "other_period", "period": _period_text(spec), "expected_period": _period_text(plan[index])}
    else:
        loaded = _loaded_deliveries(conn)
        if spec.delivery_id in loaded:
            problem = {"reason": "already_loaded"}
        else:
            missing = [p.delivery_id for p in plan[:index] if p.delivery_id not in loaded]
            if missing:
                problem = {"reason": "previous_not_loaded", "missing": missing}
            elif index > 0:
                previous = plan[index - 1].delivery_id
                if date.fromisoformat(loaded[previous]) + timedelta(days=1) != spec.outcomes_from:
                    problem = {"reason": "not_contiguous", "previous": previous, "outcomes_to": loaded[previous]}
    if problem is None:
        return make_result("V20", True)
    rows, orders = _size(delivery)
    return make_result("V20", False, rows, orders, [{"delivery_id": spec.delivery_id, **problem}])


def _outside(dates: pd.Series, first: Any, last: Any) -> pd.Series:
    if first is None or last is None:
        return pd.Series(True, index=dates.index)
    return (dates < pd.Timestamp(first)) | (dates > pd.Timestamp(last))


def _rule_v21(delivery: DeliveryFrames, spec: DeliverySpec) -> RuleResult:
    orders, outcomes = delivery.orders, delivery.outcomes
    bad_orders = orders[_outside(orders["order_date"], spec.orders_from, spec.orders_to)]
    bad_outcomes = outcomes[_outside(outcomes["outcome_known_date"], spec.outcomes_from, spec.outcomes_to)]
    if bad_orders.empty and bad_outcomes.empty:
        return make_result("V21", True)
    lines = delivery.order_lines[delivery.order_lines["order_id"].isin(set(bad_orders["order_id"]))]
    examples = [{"table": "orders", "order_id": int(r.order_id), "order_date": _iso(r.order_date)} for r in bad_orders.head(EXAMPLE_LIMIT).itertuples()]
    examples += [{"table": "outcomes", "order_id": int(r.order_id), "outcome_known_date": _iso(r.outcome_known_date)} for r in bad_outcomes.head(EXAMPLE_LIMIT).itertuples()]
    ids = set(bad_orders["order_id"]) | set(bad_outcomes["order_id"])
    return make_result("V21", False, len(bad_orders) + len(lines) + len(bad_outcomes), len(ids), examples)


def _flagged(frame: pd.DataFrame, key: str, loaded: np.ndarray) -> tuple[pd.DataFrame, pd.Series]:
    """The rows of `frame` whose `key` is already loaded or repeats an earlier row of the frame, and the reason of each."""
    is_loaded = frame[key].isin(loaded).to_numpy()
    repeated = frame[key].duplicated(keep="first").to_numpy() & ~is_loaded
    mask = is_loaded | repeated
    return frame[mask], pd.Series(np.where(is_loaded[mask], "loaded", "repeated"), index=frame.index[mask])


def _rule_v22(delivery: DeliveryFrames, conn: sqlite3.Connection) -> RuleResult:
    parts = (
        ("orders", delivery.orders, "order_id", _loaded_ids(conn, "raw_orders", "order_id")),
        ("order_lines", delivery.order_lines, "line_id", _loaded_ids(conn, "raw_order_lines", "line_id")),
        ("outcomes", delivery.outcomes, "order_id", _loaded_ids(conn, "raw_outcomes", "order_id")),
    )
    rows, ids, examples = 0, set(), []
    for table, frame, key, loaded in parts:
        bad, reason = _flagged(frame, key, loaded)
        rows += len(bad)
        ids |= set(bad["order_id"])
        for index in bad.index[:EXAMPLE_LIMIT]:
            example = {"table": table, "reason": reason[index], "order_id": int(bad.loc[index, "order_id"])}
            if key == "line_id":
                example["line_id"] = int(bad.loc[index, "line_id"])
            examples.append(example)
    return make_result("V22", rows == 0, rows, len(ids), examples)


def _rule_v23(delivery: DeliveryFrames, conn: sqlite3.Connection) -> RuleResult:
    known = np.concatenate([_loaded_ids(conn, "raw_orders", "order_id"), delivery.orders["order_id"].to_numpy(dtype="int64")])
    lines = delivery.order_lines[~delivery.order_lines["order_id"].isin(known)]
    outcomes = delivery.outcomes[~delivery.outcomes["order_id"].isin(known)]
    if lines.empty and outcomes.empty:
        return make_result("V23", True)
    examples = [{"table": "order_lines", "order_id": int(r.order_id), "line_id": int(r.line_id)} for r in lines.head(EXAMPLE_LIMIT).itertuples()]
    examples += [{"table": "outcomes", "order_id": int(r.order_id)} for r in outcomes.head(EXAMPLE_LIMIT).itertuples()]
    return make_result("V23", False, len(lines) + len(outcomes), len(set(lines["order_id"]) | set(outcomes["order_id"])), examples)


def _unreadable_files(folder: Path) -> list[str]:
    bad = []
    for name in DELIVERY_FILES:
        path = Path(folder) / f"{name}.parquet"
        try:
            if not path.is_file():
                raise OSError(name)
            pq.ParquetFile(path)
        except (OSError, pa.ArrowException):
            bad.append(f"{name}.parquet")
    return bad


def _rule_v24(delivery: DeliveryFrames, entry: ManifestEntry, folder: Path) -> RuleResult:
    rows, orders = _size(delivery)
    reasons: list[str] = []
    example: dict[str, Any] = {"delivery_id": entry.delivery_id, "rows": rows, "manifest_rows": entry.orders + entry.lines + entry.outcomes}
    if len(delivery.orders) + len(delivery.outcomes) == 0:
        reasons.append("empty")
    if (len(delivery.orders), len(delivery.order_lines), len(delivery.outcomes)) != (entry.orders, entry.lines, entry.outcomes):
        reasons.append("rows_differ_from_manifest")
    try:
        digest, file_rows = delivery_digest(folder)
    except DeliveryError:
        reasons.append("unreadable_files")
        example["message"] = "no se pueden leer los archivos de la entrega: " + ", ".join(_unreadable_files(folder))
    else:
        example.update(sha256=digest, manifest_sha256=entry.sha256_json)
        if digest != entry.sha256_json:
            reasons.append("sha256_differs_from_manifest")
        if file_rows != {"orders": entry.orders, "order_lines": entry.lines, "outcomes": entry.outcomes} and "rows_differ_from_manifest" not in reasons:
            reasons.append("rows_differ_from_manifest")
    if not reasons:
        return make_result("V24", True)
    return make_result("V24", False, rows, orders, [{**example, "reasons": reasons}])


def _rule_i01(delivery: DeliveryFrames, conn: sqlite3.Connection) -> RuleResult:
    new = delivery.orders[~delivery.orders["order_id"].isin(_loaded_ids(conn, "raw_orders", "order_id"))]
    return make_result("I01", True, int(delivery.order_lines["order_id"].isin(set(new["order_id"])).sum()), len(new))


def _rule_i03(delivery: DeliveryFrames, conn: sqlite3.Connection) -> RuleResult:
    arrived = delivery.outcomes[~delivery.outcomes["order_id"].isin(_loaded_ids(conn, "raw_outcomes", "order_id"))]
    return make_result("I03", True, len(arrived), len(arrived))


def _rule_i05(delivery: DeliveryFrames) -> RuleResult:
    post = delivery.orders[delivery.orders["regime"] == "post_break"]
    return make_result("I05", True, int(delivery.order_lines["order_id"].isin(set(post["order_id"])).sum()), len(post))


def validate_delivery(
    delivery: DeliveryFrames, spec: DeliverySpec, conn: sqlite3.Connection, cfg: PipelineConfig, *, entry: ManifestEntry, folder: Path, run_key: str,
) -> ValidationReport:
    """Validate one delivery against the plan, the warehouse and its manifest entry; every rule is evaluated.

    `entry` is the manifest line of the delivery and `folder` the folder of its three files: V24 compares what was read, and the files themselves, with what
    was published. The warehouse is only read, and a warehouse without tables is an empty one. The rules of the model are listed as not evaluated: the steps
    of the model add them with ``with_results``.
    """
    results = {
        "V20": _rule_v20(delivery, spec, conn, cfg),
        "V21": _rule_v21(delivery, spec),
        "V22": _rule_v22(delivery, conn),
        "V23": _rule_v23(delivery, conn),
        "V24": _rule_v24(delivery, entry, Path(folder)),
        "I01": _rule_i01(delivery, conn),
        "I03": _rule_i03(delivery, conn),
        "I05": _rule_i05(delivery),
    }
    report = ValidationReport(run_key, "", spec.delivery_id, _size(delivery)[0], _ordered(results), MODEL_RULES)
    _log_verdict(report)
    return report


def validate_published_delivery(
    out_dir: Path, entry: ManifestEntry, spec: DeliverySpec, conn: sqlite3.Connection, cfg: PipelineConfig, *, run_key: str,
) -> ValidationReport:
    """Read a published delivery without trusting its manifest entry and validate it; a delivery whose files cannot be read fails V24 on its own.

    ``read_delivery`` would raise on a file that does not match the manifest, which is the very thing V24 reports: so the files are read as they are and the
    comparison is a rule of the report. Nothing else is evaluated for files that cannot be read at all, and the rules left out are listed as not evaluated.
    """
    folder = Path(out_dir) / entry.delivery_id
    unreadable = _unreadable_files(folder)
    frames: DeliveryFrames | None = None
    if not unreadable:
        try:
            frames = read_delivery(out_dir, entry, verify=False)
        except (DeliveryError, OSError, pa.ArrowException, KeyError, ValueError, TypeError):
            unreadable = [f"{name}.parquet" for name in DELIVERY_FILES]
    if frames is not None:
        return validate_delivery(frames, spec, conn, cfg, entry=entry, folder=folder, run_key=run_key)
    message = "no se pueden leer los archivos de la entrega: " + ", ".join(unreadable)
    result = make_result("V24", False, 0, 0, [{"delivery_id": entry.delivery_id, "reasons": ["unreadable_files"], "message": message}])
    left_out = tuple(r for r in RULES if RULES[r].scope in ("delivery", "model") and r != "V24")
    report = ValidationReport(run_key, "", spec.delivery_id, 0, [result], left_out)
    _log_verdict(report)
    return report


def combine_reports(source: ValidationReport, delivery: ValidationReport) -> ValidationReport:
    """The report of a run: the rules of the source and then those of the delivery, in the order of ``RULES``."""
    results = {r.rule_id: r for r in [*source.results, *delivery.results]}
    return replace(
        delivery, source_sha256=source.source_sha256 or delivery.source_sha256, run_key=delivery.run_key or source.run_key,
        results=_ordered(results), not_evaluated=tuple(r for r in RULES if r not in results),
    )


# --- report files ------------------------------------------------------------------------------------------------------------


def _report_json(report: ValidationReport) -> str:
    document = {
        "run_key": report.run_key,
        "source_sha256": report.source_sha256,
        "delivery_id": report.delivery_id,
        "rows_delivery": report.rows_delivery,
        "blocking_failed": report.blocking_failed,
        "not_evaluated": list(report.not_evaluated),
        "results": [
            {
                "rule_id": r.rule_id, "name_es": r.name_es, "severity": SEVERITY_ES[r.severity], "scope": r.scope, "status": r.status_es,
                "rows_affected": r.rows_affected, "orders_affected": r.orders_affected, "examples": r.examples,
            }
            for r in report.results
        ],
    }
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _report_markdown(report: ValidationReport) -> str:
    verdict = "Rechazado: hay reglas bloqueantes que fallan." if report.blocking_failed else "Aceptado: ninguna regla bloqueante falla."
    lines = [
        "# Reporte de validación",
        "",
        f"- Corrida: `{report.run_key}`",
        f"- Resultado: **{verdict}**",
        f"- SHA-256 del CSV: `{report.source_sha256 or 'no disponible'}`",
        f"- Entrega: {report.delivery_id or 'ninguna (es el reporte de la fuente)'}; filas de la entrega: {report.rows_delivery}",
        "",
        "## Reglas",
        "",
        "| ID | Regla | Severidad | Estado | Filas | Pedidos |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for r in report.results:
        lines.append(f"| {r.rule_id} | {r.name_es} | {SEVERITY_ES[r.severity]} | {r.status_es} | {r.rows_affected} | {r.orders_affected} |")
    if report.not_evaluated:
        reason = (
            "No se pudieron evaluar porque una regla bloqueante anterior falló: " if report.blocking_failed
            else "Se evalúan en otro momento del flujo: "
        )
        lines += ["", "## No evaluadas", "", reason + ", ".join(report.not_evaluated) + "."]
    shown = [r for r in report.results if r.examples and (not r.passed or r.severity is Severity.INFO)]
    if shown:
        lines += ["", "## Ejemplos", ""]
        for r in shown:
            lines += [f"### {r.rule_id}: {r.name_es}", ""]
            lines += [f"- `{json.dumps(example, ensure_ascii=False)}`" for example in r.examples]
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def write_report(report: ValidationReport, out_dir: Path) -> tuple[Path, Path]:
    """Write ``latest.json``/``latest.md`` and a copy per run in ``history/``; return the latest pair. Nothing is written for an unusable ``run_key``."""
    if not _SAFE_RUN_KEY.fullmatch(report.run_key):
        raise ValueError(f"run_key {report.run_key!r} no sirve como nombre de archivo")
    out_dir = Path(out_dir)
    text_json, text_md = _report_json(report), _report_markdown(report)
    for directory, stem in ((out_dir, "latest"), (out_dir / "history", report.run_key)):
        write_atomic(directory / f"{stem}.json", text_json)
        write_atomic(directory / f"{stem}.md", text_md)
    return out_dir / "latest.json", out_dir / "latest.md"


def read_report(path: Path) -> ValidationReport:
    """The report written by ``write_report`` (``latest.json`` or a copy in ``history/``)."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    by_label = {label: severity for severity, label in SEVERITY_ES.items()}
    results = [
        RuleResult(r["rule_id"], r["name_es"], by_label[r["severity"]], r["scope"], r["status"] == "pasa", r["rows_affected"], r["orders_affected"], r["examples"])
        for r in document["results"]
    ]
    return ValidationReport(
        document["run_key"], document["source_sha256"], document["delivery_id"], document["rows_delivery"], results, tuple(document["not_evaluated"]),
    )


def with_results(report: ValidationReport, results: list[RuleResult]) -> ValidationReport:
    """The report with `results` added (or replacing the ones of the same rule), in the order of ``RULES``, and no longer listing them as not evaluated."""
    merged = {r.rule_id: r for r in report.results}
    merged.update({r.rule_id: r for r in results})
    return replace(report, results=_ordered(merged), not_evaluated=tuple(r for r in report.not_evaluated if r not in merged))

