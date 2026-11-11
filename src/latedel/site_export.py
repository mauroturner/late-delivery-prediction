"""The five small JSON files that the personal site copies (RF23, AC26, docs/plan.md section 18, decision D22).

``export_site`` writes ``summary.json``, ``validation_rules.json``, ``leakage.json``, ``algorithms.json`` and ``alerts.json`` to the site folder from the model marts of the
warehouse (the ``star_*`` tables, the very rows the Parquet files hold, so a figure on the site is the figure in the report) and from the tables of the evaluation. Nothing
identifies an order or a person: no file has an order number, a customer identifier or the value of a personal column (the personal columns appear in ``leakage.json`` by
name only), and an aggregate cell with fewer orders than ``[site] min_cell_orders`` has its values ``null`` and ``"suppressed": true`` (D07).

The files are a contract that another repository consumes (item 3 of the risk list): a change of shape is a change of ``tests/contract/test_site_export.py``. Floats are
rounded to what the site shows (D22); every file is written with ``json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1)`` and a final newline. The five together
stay under ``[site] max_total_bytes`` and nothing is replaced if they would not.

Until the test is evaluated, everything that would be a result of the test (its metrics, ROC, calibration, cost curve, examples and the late share of its block) is empty or
``null``: the site never shows a number the seal has not written (D17).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from latedel.config import PipelineConfig
from latedel.evaluation import NAMES_ES, SEEN
from latedel.export import ExportMeta
from latedel.files import write_atomic
from latedel.registry import read_seal
from latedel.reporting import pending_delivery
from latedel.schema import (
    DUPLICATE_OR_CONSTANT_COLUMNS,
    LEAKING_COLUMNS,
    PERSONAL_COLUMNS,
    SOURCE_COLUMNS,
)
from latedel.validate import RULES

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SITE_FILES = ("summary.json", "validation_rules.json", "leakage.json", "algorithms.json", "alerts.json")
REQUIRED_STAR = (
    "star_dim_date", "star_dim_month", "star_dim_shipping_mode", "star_dim_geo", "star_dim_product", "star_dim_model", "star_fact_order", "star_fact_tracking_monthly",
    "star_fact_method_metrics", "star_fact_evaluation_summary", "star_fact_roc", "star_fact_calibration", "star_fact_cost_sensitivity", "star_fact_leakage",
    "star_fact_importance", "star_fact_validation",
)
METHODS = ("model", "b2_mode", "b5_mode_noon")
METHOD_LABELS_ES = {"model": "Modelo", "b2_mode": "B2: tasa de retraso por modo", "b5_mode_noon": "B5: tasa por modo y hora del pedido"}
CRITERIA_IDS = tuple(f"C{i:02d}" for i in range(1, 11))
BLOCKS = (
    ("train", "Entrenamiento"), ("purge_1", "Purga entre el entrenamiento y la validación"), ("valid_v1", "Validación V1 (elige y calibra)"),
    ("valid_v2", "Validación V2 (fija el umbral)"), ("purge_2", "Purga entre la validación y la prueba"), ("test", "Prueba (se toca una vez)"),
    ("post_break", "Tramo posterior al cambio de forma (informativo)"),
)
SEVERITY_EN = {"bloqueante": "blocking", "advertencia": "warning", "informativa": "info"}
STATUS_EN = {"pasa": "pass", "falla": "fail", "advierte": "warn"}
EXAMPLE_LABELS = ("Pedido A", "Pedido B", "Pedido C", "Pedido D", "Pedido E")
EXAMPLE_CELLS = (
    ("First Class", None, "Riesgo mediano de los pedidos First Class de la prueba"), ("Second Class", None, "Riesgo mediano de los pedidos Second Class de la prueba"),
    ("Standard Class", None, "Riesgo mediano de los pedidos Standard Class de la prueba"), ("Same Day", "before", "Riesgo mediano de los Same Day antes del mediodía de la prueba"),
    ("Same Day", "from", "Riesgo mediano de los Same Day desde el mediodía de la prueba"),
)

# What the source's columns that are not in the model are, in words (leakage.json): the Spanish name, when it is known and why it is left out.
COLUMN_KINDS = {
    **{name: "personal" for name in PERSONAL_COLUMNS}, "Late_delivery_risk": "target",
    **{name: "duplicate" for name in DUPLICATE_OR_CONSTANT_COLUMNS[:4]}, **{name: "constant" for name in DUPLICATE_OR_CONSTANT_COLUMNS[4:]},
}
PERSONAL_NOTE_ES = "Dato personal: se descarta al leer el archivo y no llega a ninguna salida; aquí solo figura su nombre."
DUPLICATE_NOTE_ES = "Repite otra columna de la fuente: se lleva una sola copia."
CONSTANT_NOTE_ES = "Constante, vacía o una dirección: no aporta información al modelo."
TARGET_NOTE_ES = "Es la etiqueta, escrita con otras palabras: los días reales superan a los programados."


class SiteExportError(RuntimeError):
    """The files of the site cannot be written: the model marts are missing, the run is not recorded, or the files together would pass the size cap."""


# --- small helpers -----------------------------------------------------------------------------------------------------------------------


def _num(value: Any, decimals: int) -> float | None:
    """`value` rounded, or None when it is missing or not finite."""
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return round(number, decimals) if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


def _bool(value: Any) -> bool | None:
    return None if value is None or pd.isna(value) else bool(value)


def _day(key: Any) -> str | None:
    """A date written as a number (AAAAMMDD) as ``AAAA-MM-DD``."""
    if key is None or pd.isna(key):
        return None
    key = int(key)
    return f"{key // 10000:04d}-{key // 100 % 100:02d}-{key % 100:02d}"


def _month(key: Any) -> str:
    key = int(key)
    return f"{key // 100:04d}-{key % 100:02d}"


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def _require_star(conn: sqlite3.Connection) -> None:
    present = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = [t for t in REQUIRED_STAR if t not in present]
    if missing:
        raise SiteExportError(f"Faltan los marts del modelo ({', '.join(missing)}): corra la tarea export antes de escribir los archivos del sitio")


def _run_status(conn: sqlite3.Connection, run_key: str) -> str:
    row = conn.execute("SELECT status FROM pipeline_runs WHERE run_key = ?", (run_key,)).fetchone()
    if row is None:
        raise SiteExportError(f"La corrida {run_key} no está registrada en el almacén: los archivos del sitio dicen el estado de una corrida que existe")
    return str(row[0])


def _header(meta: ExportMeta) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "run_key": meta.run_key, "as_of": meta.as_of, "source_sha256": meta.source_sha256, "attribution": meta.attribution}


def _prefer_sealed(frame: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """The rows of an evaluation fact once per `key`: the sealed one if there is one, otherwise the ``post_seal`` one."""
    if frame.empty or "seal_state" not in frame:
        return frame
    ordered = frame.assign(_rank=frame["seal_state"].map({"sealed": 0, "post_seal": 1}).fillna(2)).sort_values([*key, "_rank"])
    return ordered.drop_duplicates(key, keep="first").drop(columns="_rank")


def _suppressed(cell_orders: Any, minimum: int) -> bool:
    """A cell of fewer orders than the minimum is hidden (D07); a cell with no count is hidden too."""
    return cell_orders is None or pd.isna(cell_orders) or int(cell_orders) < max(minimum, 1)      # an empty cell has nothing to show whatever the minimum


def _ratio(numerator: Any, denominator: Any, decimals: int = 3) -> float | None:
    return None if denominator is None or pd.isna(denominator) or not denominator else _num(float(numerator) / float(denominator), decimals)


class _Evaluated:
    """What the evaluation has stored in the star tables: the metrics of the model and the rules by scope, sealed ones first."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        metrics = _prefer_sealed(_query(conn, "SELECT * FROM star_fact_method_metrics"), ["scope", "method"])
        self.metrics = metrics
        self.scopes = set(metrics["scope"]) if len(metrics) else set()
        self.test = "test" in self.scopes

    def row(self, scope: str, method: str) -> pd.Series | None:
        part = self.metrics[(self.metrics["scope"] == scope) & (self.metrics["method"] == method)] if len(self.metrics) else self.metrics
        return None if part.empty else part.iloc[0]

    def value(self, scope: str, method: str, column: str, decimals: int) -> float | None:
        row = self.row(scope, method)
        return None if row is None else _num(row[column], decimals)


# --- summary.json ------------------------------------------------------------------------------------------------------------------------


def _criteria(conn: sqlite3.Connection, cfg: PipelineConfig) -> list[dict[str, Any]]:
    stored = _prefer_sealed(_query(conn, "SELECT * FROM star_fact_evaluation_summary"), ["criterion_id"])
    by_id = {r["criterion_id"]: r for r in stored.to_dict("records")}
    wait = pending_delivery(cfg)
    out = []
    for criterion_id in CRITERIA_IDS:
        r = by_id.get(criterion_id)
        if r is None:
            text = f"Pendiente: la prueba se evalúa con la entrega de {wait}."
            out.append({
                "id": criterion_id, "name_es": NAMES_ES[criterion_id], "passed": None, "value": None, "baseline_value": None, "ci_low": None, "ci_high": None,
                "threshold_es": text, "seen_by_research": SEEN[criterion_id][0], "seen_by_planner": SEEN[criterion_id][1], "verdict_es": text,
            })
            continue
        out.append({
            "id": criterion_id, "name_es": r["name_es"], "passed": _bool(r["passed"]), "value": _num(r["value"], 4), "baseline_value": _num(r["baseline_value"], 4),
            "ci_low": _num(r["ci_low"], 4), "ci_high": _num(r["ci_high"], 4), "threshold_es": r["threshold_es"], "seen_by_research": r["seen_by_research"],
            "seen_by_planner": r["seen_by_planner"], "verdict_es": r["verdict_es"],
        })
    return out


def _summary(conn: sqlite3.Connection, meta: ExportMeta, cfg: PipelineConfig) -> dict[str, Any]:
    lines, orders, cancelled, first, last, test, closed, late = conn.execute(
        "SELECT COALESCE(SUM(n_lines), 0), COUNT(*), COALESCE(SUM(outcome_status = 'cancelled'), 0), MIN(order_date_key), MAX(order_date_key), "
        "COALESCE(SUM(block = 'test' AND outcome_status != 'cancelled'), 0), COALESCE(SUM(outcome_status = 'closed'), 0), COALESCE(SUM(is_late), 0) FROM star_fact_order"
    ).fetchone()
    model = _query(conn, "SELECT threshold, break_even, seal_state FROM star_dim_model ORDER BY model_version DESC LIMIT 1")
    evaluated = _Evaluated(conn)
    sealed_test = conn.execute("SELECT COUNT(*) FROM star_fact_evaluation_summary WHERE seal_state = 'sealed' AND criterion_id = 'C01'").fetchone()[0]
    state = "pending"
    if len(model) and model["seal_state"].iloc[0] == "post_seal":
        state = "post_seal"
    elif sealed_test:
        state = "sealed"
    sealed = read_seal(cfg.paths.seal) or {}
    launch, test_part = sealed.get("launch") or {}, sealed.get("test") or {}
    return {
        "status": _run_status(conn, meta.run_key),
        "evaluation_status": state,
        "headline": {
            "rows": int(lines), "orders": int(orders), "orders_cancelled": int(cancelled), "orders_task": int(orders - cancelled), "test_orders": int(test),
            "period_start": _day(first), "period_end": _day(last), "late_share_task": _ratio(late, closed, 3),
            "threshold": _num(model["threshold"].iloc[0], 2) if len(model) else None, "break_even": _num(model["break_even"].iloc[0], 2) if len(model) else None,
            "alert_cost": cfg.costs.alert_cost, "late_cost": cfg.costs.late_cost, "effect": cfg.costs.effect,
            "test_alert_share": evaluated.value("test", "model", "flagged_share", 3), "test_precision": evaluated.value("test", "model", "precision", 3),
            "test_recall": evaluated.value("test", "model", "recall", 3),
            "auc": {m: evaluated.value("test", m, "auc", 3) for m in METHODS},
            "net_benefit_per_1000": {m: evaluated.value("test", m, "nb_per_1000", 1) for m in METHODS},
        },
        "criteria": _criteria(conn, cfg),
        "seal": {
            "config_sha256": launch.get("config_sha256"), "launch_run_key": launch.get("run_key"), "test_run_key": test_part.get("run_key"), "test_as_of": test_part.get("as_of"),
        },
    }


# --- validation_rules.json ---------------------------------------------------------------------------------------------------------------


def _validation(conn: sqlite3.Connection) -> dict[str, Any]:
    """The rules in the order of the report. A rule of the source describes one file and is read once, from the latest loaded run; the others are summed over the runs
    that loaded a delivery. A rule that no loaded run recorded is ``not_evaluated``."""
    rows = _query(
        conn,
        "SELECT v.rule_id, v.status, v.rows_affected, v.orders_affected, d.as_of_date FROM star_fact_validation AS v "
        "JOIN deliveries AS d ON d.run_key = v.run_key AND d.status = 'loaded' ORDER BY d.as_of_date, v.rule_id",
    )
    rules = []
    for rule_id, rule in RULES.items():
        mine = rows[rows["rule_id"] == rule_id]
        shown = mine.tail(1) if rule.scope == "source" else mine
        statuses = set(shown["status"])
        status = "not_evaluated" if shown.empty else "fail" if "falla" in statuses else "warn" if "advierte" in statuses else "pass"
        rules.append({
            "rule_id": rule_id, "name_es": rule.name_es, "severity": rule.severity.value, "scope": rule.scope, "status": status,
            "rows": 0 if shown.empty else _int(shown["rows_affected"].sum()), "orders": 0 if shown.empty else _int(shown["orders_affected"].sum()),
        })
    lines, orders, cancelled, cancelled_lines = conn.execute(
        "SELECT COALESCE(SUM(n_lines), 0), COUNT(*), COALESCE(SUM(outcome_status = 'cancelled'), 0), COALESCE(SUM(CASE WHEN outcome_status = 'cancelled' THEN n_lines END), 0) "
        "FROM star_fact_order"
    ).fetchone()
    loaded, rejected = conn.execute("SELECT COALESCE(SUM(status = 'loaded'), 0), COALESCE(SUM(status = 'rejected'), 0) FROM deliveries").fetchone()
    totals = {
        "rows_raw": int(lines), "orders_raw": int(orders), "orders_cancelled": int(cancelled), "rows_cancelled": int(cancelled_lines), "orders_task": int(orders - cancelled),
        "personal_columns_dropped": len(PERSONAL_COLUMNS), "leaking_columns_set_aside": len(LEAKING_COLUMNS), "deliveries_loaded": int(loaded), "deliveries_rejected": int(rejected),
    }
    return {"rules": rules, "totals": totals}


# --- leakage.json ------------------------------------------------------------------------------------------------------------------------


def _leakage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every column of the source that is not an input of the model, with what it is; the ones measured in V2 carry their AUC next to the mode alone."""
    measured = {r["source_column"]: r for r in _query(conn, "SELECT * FROM star_fact_leakage").to_dict("records")}
    columns = []
    for name in SOURCE_COLUMNS:
        if name in measured:
            r = measured[name]
            columns.append({
                "column": name, "column_es": r["column_es"], "kind": "leak" if r["kind"] == "outcome" else "precaution", "known_at_es": r["known_at_es"],
                "auc_with_mode_valid": _num(r["auc_with_mode_valid"], 3), "note_es": r["note_es"],
            })
        elif name in COLUMN_KINDS:
            kind = COLUMN_KINDS[name]
            note = {"personal": PERSONAL_NOTE_ES, "duplicate": DUPLICATE_NOTE_ES, "constant": CONSTANT_NOTE_ES, "target": TARGET_NOTE_ES}[kind]
            known = "Después de la entrega" if kind == "target" else "No es una variable del pedido" if kind != "personal" else "Al crear el pedido, pero no se lleva"
            columns.append({"column": name, "column_es": name, "kind": kind, "known_at_es": known, "auc_with_mode_valid": None, "note_es": note})
    evaluated = _Evaluated(conn)
    reference = measured.get("shipping_mode")
    return {
        "columns": columns,
        "reference": {
            "auc_mode_only_valid": None if reference is None else _num(reference["auc_with_mode_valid"], 3),
            "auc_selected_model_valid": evaluated.value("valid_v2", "model", "auc", 3),
        },
    }


# --- algorithms.json ---------------------------------------------------------------------------------------------------------------------


def _split(conn: sqlite3.Connection, cfg: PipelineConfig, evaluated: _Evaluated) -> list[dict[str, Any]]:
    s = cfg.split
    edges = {
        "train": (s.train_start, s.train_end), "purge_1": (s.train_end + timedelta(days=1), s.valid_start - timedelta(days=1)),
        "valid_v1": (s.valid_start, s.valid_v2_start - timedelta(days=1)), "valid_v2": (s.valid_v2_start, s.valid_end),
        "purge_2": (s.valid_end + timedelta(days=1), s.test_start - timedelta(days=1)), "test": (s.test_start, s.test_end), "post_break": (s.post_break_start, s.post_break_end),
    }
    counts = {
        r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT block, COUNT(*), SUM(outcome_status = 'closed'), COALESCE(SUM(is_late), 0) FROM star_fact_order WHERE outcome_status != 'cancelled' GROUP BY block")
    }
    out = []
    for block, label in BLOCKS:
        n, closed, late = counts.get(block, (0, 0, 0))
        hidden = _suppressed(n, cfg.site.min_cell_orders)
        wait = block in ("test", "post_break") and block not in evaluated.scopes     # the late share of the test and of the later block waits for their evaluation
        out.append({
            "block": block, "label_es": label, "start": edges[block][0].isoformat(), "end": edges[block][1].isoformat(), "suppressed": hidden,
            "orders": None if hidden else int(n), "late_share": None if hidden or wait else _ratio(late, closed, 3),
        })
    return out


def _mode_rates(conn: sqlite3.Connection, cfg: PipelineConfig) -> list[dict[str, Any]]:
    modes = _query(conn, "SELECT mode_key, mode_es, scheduled_days, train_late_rate, sort_order FROM star_dim_shipping_mode ORDER BY sort_order")
    orders = dict(conn.execute("SELECT cell, train_orders FROM baseline_rates WHERE baseline = 'b2_mode'").fetchall())
    out = []
    for r in modes.to_dict("records"):
        n = orders.get(r["mode_key"])
        hidden = _suppressed(n, cfg.site.min_cell_orders)
        out.append({
            "mode": r["mode_key"], "mode_es": r["mode_es"], "scheduled_days": _int(r["scheduled_days"]), "suppressed": hidden,
            "train_orders": None if hidden else int(n), "train_late_rate": None if hidden else _num(r["train_late_rate"], 3),
        })
    return out


def _hour_profile(conn: sqlite3.Connection, cfg: PipelineConfig) -> list[dict[str, Any]]:
    modes = _query(conn, "SELECT mode_key FROM star_dim_shipping_mode ORDER BY sort_order")["mode_key"].tolist()
    cells = _query(
        conn,
        "SELECT mode_key, order_hour, COUNT(*) AS n, COALESCE(SUM(is_late), 0) AS late FROM star_fact_order WHERE block = 'train' AND outcome_status = 'closed' "
        "GROUP BY mode_key, order_hour",
    )
    found = {(r["mode_key"], int(r["order_hour"])): r for r in cells.to_dict("records")}
    out = []
    for mode in modes:
        for hour in range(24):
            r = found.get((mode, hour))
            n = 0 if r is None else int(r["n"])
            hidden = _suppressed(n, cfg.site.min_cell_orders)
            out.append({
                "mode": mode, "hour": hour, "suppressed": hidden, "train_orders": None if hidden else n, "train_late_rate": None if hidden else _ratio(r["late"], n, 3),
            })
    return out


def _selection(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    row = conn.execute("SELECT selection_json FROM model_registry ORDER BY model_version DESC LIMIT 1").fetchone()
    if row is None:
        return []
    return [
        {"feature_set": c["feature_set"], "learning_rate": c["learning_rate"], "max_leaf_nodes": int(c["max_leaf_nodes"]), "min_samples_leaf": int(c["min_samples_leaf"]),
         "log_loss_v1": _num(c["log_loss_v1"], 4), "chosen": bool(c["chosen"])}
        for c in json.loads(row[0])
    ]


def _methods(evaluated: _Evaluated) -> list[dict[str, Any]]:
    out = []
    for scope in ("test", "post_break"):
        for method in METHODS:
            r = evaluated.row(scope, method)
            if r is None:
                continue
            out.append({
                "method": method, "label_es": METHOD_LABELS_ES[method], "scope": scope, "auc": _num(r["auc"], 3), "pr_auc": _num(r["pr_auc"], 3), "brier": _num(r["brier"], 4),
                "threshold": _num(r["threshold"], 2), "flagged_share": _num(r["flagged_share"], 3), "precision": _num(r["precision"], 3), "recall": _num(r["recall"], 3),
                "net_benefit_per_1000": _num(r["nb_per_1000"], 1),
            })
    return out


def _roc(conn: sqlite3.Connection, evaluated: _Evaluated) -> list[dict[str, Any]]:
    if not evaluated.test:
        return []
    frame = _prefer_sealed(_query(conn, "SELECT * FROM star_fact_roc WHERE scope = 'test'"), ["method", "point"])
    return [
        {"method": m, "fpr": _num(r["fpr"], 4), "tpr": _num(r["tpr"], 4)}
        for m in METHODS for r in frame[frame["method"] == m].sort_values("point").to_dict("records")
    ]


def _calibration(conn: sqlite3.Connection, cfg: PipelineConfig, evaluated: _Evaluated) -> list[dict[str, Any]]:
    if not evaluated.test:
        return []
    frame = _prefer_sealed(_query(conn, "SELECT * FROM star_fact_calibration WHERE scope = 'test' AND method = 'model'"), ["bin"])
    out = []
    for r in frame.sort_values("bin").to_dict("records"):
        hidden = _suppressed(r["orders"], cfg.site.min_cell_orders)
        out.append({
            "bin": int(r["bin"]), "suppressed": hidden, "orders": None if hidden else int(r["orders"]), "p_mean": None if hidden else _num(r["p_mean"], 3),
            "observed": None if hidden else _num(r["observed"], 3),
        })
    return out


def _cost_curve(conn: sqlite3.Connection, evaluated: _Evaluated) -> list[dict[str, Any]]:
    if not evaluated.test:
        return []
    frame = _prefer_sealed(_query(conn, "SELECT * FROM star_fact_cost_sensitivity WHERE scope = 'test'"), ["break_even", "method"])
    out = []
    for point, part in frame.groupby("break_even", sort=True):
        by = {r["method"]: r for r in part.to_dict("records")}
        model = by.get("model")
        out.append({
            "break_even": _num(point, 2), "threshold_model": None if model is None else _num(model["threshold"], 2),
            "nb_model": None if model is None else _num(model["nb_per_1000"], 1),
            "nb_b2_mode": None if "b2_mode" not in by else _num(by["b2_mode"]["nb_per_1000"], 1),
            "nb_b5_mode_noon": None if "b5_mode_noon" not in by else _num(by["b5_mode_noon"]["nb_per_1000"], 1),
            "flagged_share_model": None if model is None else _num(model["flagged_share"], 3),
        })
    return out


def _threshold(conn: sqlite3.Connection) -> dict[str, Any] | None:
    r = _query(conn, "SELECT threshold, break_even, alert_cost, late_cost, effect FROM star_dim_model ORDER BY model_version DESC LIMIT 1")
    if r.empty:
        return None
    row = r.iloc[0]
    return {
        "value": _num(row["threshold"], 2), "break_even": _num(row["break_even"], 2), "alert_cost": _num(row["alert_cost"], 2), "late_cost": _num(row["late_cost"], 2),
        "effect": _num(row["effect"], 2), "chosen_on": "valid_v2",
    }


def _importance(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    frame = _query(conn, "SELECT feature, feature_es, auc_drop FROM star_fact_importance ORDER BY auc_drop DESC, feature LIMIT 10")
    return [{"feature": r["feature"], "feature_es": r["feature_es"], "auc_drop": _num(r["auc_drop"], 4)} for r in frame.to_dict("records")]


def _examples(conn: sqlite3.Connection, cfg: PipelineConfig, evaluated: _Evaluated) -> list[dict[str, Any]]:
    """The rule of D22: in the test, for each cell of B5 in a fixed order, the order with the lower-median risk (ties by ``order_ts`` and then ``order_id``).

    Only the month of the order is published, never the day or the number. Empty until the test is evaluated, and a cell with fewer orders than the minimum gives no example.
    """
    if not evaluated.test:
        return []
    orders = _query(
        conn,
        "SELECT o.order_id, o.mode_key, o.order_ts, o.order_hour, o.order_date_key, o.n_lines, o.risk, o.is_alert, o.is_late, o.real_days, g.market, m.mode_es, m.scheduled_days, "
        "d.weekday_es FROM star_fact_order AS o JOIN star_dim_geo AS g ON g.geo_key = o.geo_key JOIN star_dim_shipping_mode AS m ON m.mode_key = o.mode_key "
        "JOIN star_dim_date AS d ON d.date_key = o.order_date_key WHERE o.block = 'test' AND o.outcome_status = 'closed' AND o.risk IS NOT NULL",
    )
    out = []
    for (mode, band, rule), label in zip(EXAMPLE_CELLS, EXAMPLE_LABELS, strict=True):
        cell = orders[orders["mode_key"] == mode]
        if band is not None:
            cell = cell[cell["order_hour"] < cfg.features.noon_hour] if band == "before" else cell[cell["order_hour"] >= cfg.features.noon_hour]
        if _suppressed(len(cell), cfg.site.min_cell_orders):
            continue
        r = cell.sort_values(["risk", "order_ts", "order_id"]).iloc[(len(cell) - 1) // 2]
        out.append({
            "label": label, "rule_es": rule, "month": _day(r["order_date_key"])[:7], "mode": mode, "mode_es": r["mode_es"], "scheduled_days": _int(r["scheduled_days"]),
            "order_hour": _int(r["order_hour"]), "weekday_es": r["weekday_es"], "market": r["market"], "n_lines": _int(r["n_lines"]), "risk": _num(r["risk"], 2),
            "is_alert": _bool(r["is_alert"]), "outcome": "tarde" if bool(r["is_late"]) else "a tiempo", "real_days": _int(r["real_days"]),
        })
    return out


def _algorithms(conn: sqlite3.Connection, cfg: PipelineConfig) -> dict[str, Any]:
    evaluated = _Evaluated(conn)
    return {
        "split": _split(conn, cfg, evaluated), "mode_rates": _mode_rates(conn, cfg), "hour_profile": _hour_profile(conn, cfg), "selection": _selection(conn),
        "methods": _methods(evaluated), "roc": _roc(conn, evaluated), "calibration": _calibration(conn, cfg, evaluated), "cost_curve": _cost_curve(conn, evaluated),
        "threshold": _threshold(conn), "importance": _importance(conn), "examples": _examples(conn, cfg, evaluated),
    }


# --- alerts.json -------------------------------------------------------------------------------------------------------------------------


def _tracking_cell(sums: pd.Series, minimum: int) -> dict[str, Any]:
    """Counts and rates of a group of the follow-up; hidden when the group scored fewer orders than the minimum."""
    hidden = _suppressed(sums["orders_scored"], minimum)
    shown = None if hidden else sums
    return {
        "suppressed": hidden, "orders": None if hidden else _int(shown["orders_scored"]), "alerts": None if hidden else _int(shown["alerts"]),
        "alert_share": None if hidden else _ratio(shown["alerts"], shown["orders_scored"], 3),
        "closed_orders": None if hidden else _int(shown["closed_orders"]), "late_observed": None if hidden else _int(shown["late_closed"]),
        "late_expected": None if hidden else _num(shown["late_expected"], 1), "closed_alerts": None if hidden else _int(shown["closed_alerts"]),
        "alerts_late": None if hidden else _int(shown["alerts_late"]),
        "precision": None if hidden else _ratio(shown["alerts_late"], shown["closed_alerts"], 3),
        "recall": None if hidden else _ratio(shown["alerts_late"], shown["alerts_late"] + shown["late_missed"], 3),
        "cancelled_orders": None if hidden else _int(shown["cancelled_orders"]),
    }


def _alerts(conn: sqlite3.Connection, cfg: PipelineConfig) -> dict[str, Any]:
    minimum = cfg.site.min_cell_orders
    track = _query(conn, "SELECT * FROM star_fact_tracking_monthly")
    regime = dict(conn.execute("SELECT month_key, regime FROM star_dim_month").fetchall())
    modes = _query(conn, "SELECT mode_key, mode_es FROM star_dim_shipping_mode ORDER BY sort_order")
    numeric = [c for c in track.columns if c not in ("month_key", "mode_key")]
    monthly = []
    for key, part in track.groupby("month_key", sort=True):
        cell = _tracking_cell(part[numeric].sum(), minimum)
        monthly.append({"month": _month(key), "regime": regime.get(int(key)), **cell})
    by_mode = []
    for r in modes.to_dict("records"):
        part = track[track["mode_key"] == r["mode_key"]]
        cell = _tracking_cell(part[numeric].sum(), minimum)
        by_mode.append({"mode": r["mode_key"], "mode_es": r["mode_es"], **{k: cell[k] for k in ("suppressed", "orders", "alerts", "precision", "recall")}})
    scored = _query(
        conn,
        "SELECT g.market AS market, COUNT(*) AS orders, SUM(o.is_alert) AS alerts, COALESCE(SUM(o.outcome_status = 'closed'), 0) AS closed, COALESCE(SUM(o.is_late), 0) AS late "
        "FROM star_fact_order AS o JOIN star_dim_geo AS g ON g.geo_key = o.geo_key WHERE o.risk IS NOT NULL GROUP BY g.market ORDER BY g.market",
    )
    by_market = []
    for r in scored.to_dict("records"):
        hidden = _suppressed(r["orders"], minimum)
        by_market.append({
            "market": r["market"], "suppressed": hidden, "orders": None if hidden else int(r["orders"]), "alerts": None if hidden else int(r["alerts"]),
            "late_rate_observed": None if hidden or _suppressed(r["closed"], minimum) else _ratio(r["late"], r["closed"], 3),
        })
    on_cancelled, alerts_total = int(track["alerts_on_cancelled"].sum()) if len(track) else 0, int(track["alerts"].sum()) if len(track) else 0
    model = _query(conn, "SELECT model_version, threshold FROM star_dim_model ORDER BY model_version DESC LIMIT 1")
    return {
        "threshold": _num(model["threshold"].iloc[0], 2) if len(model) else None, "model_version": str(model["model_version"].iloc[0]) if len(model) else None,
        "monthly": monthly, "by_mode": by_mode, "by_market": by_market,
        "cancelled": {"alerts_on_later_cancelled": on_cancelled, "share_of_alerts": _ratio(on_cancelled, alerts_total, 3)},
    }


# --- writing ------------------------------------------------------------------------------------------------------------------------------


def _text(document: dict) -> str:
    """One document as the text of its file: sorted keys, readable, UTF-8 and no NaN that a strict parser would refuse."""
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False) + "\n"


def _check_size(texts: dict[str, str], out_dir: Path, cap: int) -> None:
    """The five files together stay under `cap` bytes; a file that is not being replaced counts with the size it has."""
    sizes = {name: len(text.encode("utf-8")) for name, text in texts.items()}
    for name in SITE_FILES:
        if name not in sizes and (out_dir / name).is_file():
            sizes[name] = (out_dir / name).stat().st_size
    total = sum(sizes.values())
    if total > cap:
        raise SiteExportError(f"Los archivos del sitio pesan {_thousands(total)} bytes y el tope es {_thousands(cap)}: no se escribió ninguno")


def _write(texts: dict[str, str], out_dir: Path, cap: int) -> list[Path]:
    out_dir = Path(out_dir)
    _check_size(texts, out_dir, cap)
    paths = [write_atomic(out_dir / name, text) for name, text in texts.items()]
    for path in paths:
        logger.info("exported %s: %d bytes", path.name, path.stat().st_size)
    return paths


def export_site(conn: sqlite3.Connection, out_dir: Path, meta: ExportMeta, cfg: PipelineConfig) -> list[Path]:
    """Write the five files of the site from the model marts of `conn` and return their paths (``SITE_FILES`` order).

    Needs the model marts (the ``export`` task builds them first) and a run recorded under ``meta.run_key``. Nothing is written if the five files together would pass
    ``cfg.site.max_total_bytes``, and every document is built before the first file is replaced.
    """
    _require_star(conn)
    _run_status(conn, meta.run_key)
    header = _header(meta)
    documents = {
        "summary.json": _summary(conn, meta, cfg), "validation_rules.json": _validation(conn), "leakage.json": _leakage(conn),
        "algorithms.json": _algorithms(conn, cfg), "alerts.json": _alerts(conn, cfg),
    }
    return _write({name: _text({**header, **documents[name]}) for name in SITE_FILES}, out_dir, cfg.site.max_total_bytes)


def export_summary(conn: sqlite3.Connection, out_dir: Path, meta: ExportMeta, cfg: PipelineConfig) -> Path:
    """Rewrite only ``summary.json``, with the status the warehouse has for the run: the file that closing a run changes (D35)."""
    _require_star(conn)
    text = _text({**_header(meta), **_summary(conn, meta, cfg)})
    [path] = _write({"summary.json": text}, out_dir, cfg.site.max_total_bytes)
    return path


__all__ = ["SITE_FILES", "SiteExportError", "export_site", "export_summary"]
