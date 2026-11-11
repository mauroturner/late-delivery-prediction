"""The demonstration of the leakage: what each column that gives the result away does to the AUC on V2 (decision D31, RF16).

For every column of ``LEAKING_COLUMNS`` except the target, a classifier of the same family as the model is trained on the **training** block with the shipping mode
and that column, and its AUC is measured on **V2**; the reference is the shipping mode alone. The point is to show, with this project's own code and without
touching the test, why those columns stay out of the model: a team that fitted them would report an AUC that is the answer itself.

This module reads the outcome tables on purpose (``stg_outcomes``) and nothing else writes through it: it runs with ``PRAGMA query_only`` on, imports neither the
registry nor the scoring, and a test reads its source and checks the model tables before and after.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from latedel.config import PipelineConfig
from latedel.evaluation import auc_score
from latedel.schema import LEAKING_COLUMNS
from latedel.split import labelled_orders

TARGET = "Late_delivery_risk"
REFERENCE = "shipping_mode"


@dataclass(frozen=True)
class LeakingColumn:
    source: str            # the column of the source
    stored_as: str         # the column of stg_outcomes that holds it (or a derived name)
    categorical: bool
    column_es: str
    kind: str              # outcome or profit
    known_at_es: str
    note_es: str


COLUMNS: tuple[LeakingColumn, ...] = (
    LeakingColumn("Delivery Status", "delivery_status", True, "Estado de entrega", "outcome", "Después de la entrega",
                  "Dice si el pedido llegó tarde, a tiempo o adelantado: es la respuesta escrita con otras palabras."),
    LeakingColumn("Days for shipping (real)", "real_days", False, "Días de envío reales", "outcome", "Después de la entrega",
                  "La etiqueta es esta columna menos los días programados: con el modo (que fija los programados) la respuesta es exacta."),
    LeakingColumn("shipping date (DateOrders)", "shipping_hours", False, "Fecha de envío (horas desde el pedido)", "outcome", "Al despachar",
                  "Es el pedido más 12, 48 o de 48 a 144 horas según el modo: ya contiene la demora que se quiere predecir."),
    LeakingColumn("Order Status", "order_status", True, "Estado del pedido", "outcome", "Después de crear el pedido",
                  "Cambia con el avance del pedido (completo, cancelado, en revisión); se conoce después de crearlo."),
    LeakingColumn("Benefit per order", "benefit_total", False, "Beneficio por pedido", "profit", "Momento no establecido",
                  "Se excluye por precaución: no consta cuándo se calcula; dentro de cada modo las líneas tarde tienen un beneficio algo menor."),
    LeakingColumn("Order Profit Per Order", "benefit_total", False, "Ganancia por pedido", "profit", "Momento no establecido",
                  "Es la misma columna que el beneficio por pedido (V12): el almacén guarda una sola copia."),
    LeakingColumn("Order Item Profit Ratio", "profit_ratio_mean", False, "Razón de ganancia por línea", "profit", "Momento no establecido",
                  "Se excluye por precaución, igual que el beneficio."),
)
_covered = {c.source for c in COLUMNS}
if _covered != set(LEAKING_COLUMNS) - {TARGET}:
    raise RuntimeError(f"leakage_demo no cubre exactamente las columnas de LEAKING_COLUMNS menos el objetivo: {sorted(_covered ^ (set(LEAKING_COLUMNS) - {TARGET}))}")


def _pipeline(cfg: PipelineConfig, categorical: list[str], numeric: list[str]) -> Pipeline:
    """The same family as the model, with the first point of the grid: an encoder for the text columns and gradient boosting that never stops early."""
    encoder = OrdinalEncoder(categories="auto", handle_unknown="use_encoded_value", unknown_value=np.nan, encoded_missing_value=np.nan)
    prep = ColumnTransformer([("categorical", encoder, categorical), ("numeric", "passthrough", numeric)], remainder="drop", verbose_feature_names_out=False)
    mask = [True] * len(categorical) + [False] * len(numeric)
    classifier = HistGradientBoostingClassifier(
        learning_rate=cfg.model.learning_rate[0], max_leaf_nodes=cfg.model.max_leaf_nodes[0], min_samples_leaf=cfg.model.min_samples_leaf[0],
        l2_regularization=cfg.model.l2_regularization, max_iter=cfg.model.max_iter, early_stopping=False, random_state=cfg.model.random_state,
        categorical_features=mask,
    )
    return Pipeline([("prep", prep), ("classifier", classifier)])


def _frame(conn: sqlite3.Connection, cfg: PipelineConfig, as_of: date) -> pd.DataFrame:
    """The training and V2 orders with their label, shipping mode and the columns that give the result away, as known on `as_of`."""
    labelled = labelled_orders(conn, as_of, cfg)
    labelled = labelled[labelled["block"].isin(["train", "valid_v2"])]
    outcomes = pd.read_sql_query(
        "SELECT order_id, delivery_status, order_status, real_days, shipping_ts, benefit_total, profit_ratio_mean FROM stg_outcomes WHERE outcome_known_date <= ?",
        conn, params=(as_of.isoformat(),),
    )
    frame = labelled.merge(outcomes, on="order_id", how="left", validate="one_to_one")
    if frame["real_days"].isna().any():
        raise ValueError("hay pedidos con etiqueta sin su fila de resultados en las etapas: se reconstruyen con rebuild_at")
    hours = (pd.to_datetime(frame["shipping_ts"]) - pd.to_datetime(frame["order_ts"])) / pd.Timedelta(hours=1)
    return frame.assign(shipping_hours=hours.astype("float64"))


def _auc(frame: pd.DataFrame, cfg: PipelineConfig, extra: LeakingColumn | None) -> float:
    categorical = [REFERENCE] + ([extra.stored_as] if extra is not None and extra.categorical else [])
    numeric = [extra.stored_as] if extra is not None and not extra.categorical else []
    columns = categorical + numeric
    train, valid = frame[frame["block"] == "train"], frame[frame["block"] == "valid_v2"]
    pipeline = _pipeline(cfg, categorical, numeric).fit(train[columns], train["is_late"].to_numpy())
    return float(auc_score(valid["is_late"].to_numpy(), pipeline.predict_proba(valid[columns])[:, 1]))


def leakage_table(conn: sqlite3.Connection, cfg: PipelineConfig, *, as_of: date | None = None) -> pd.DataFrame:
    """One row per column that gives the result away, and the reference of the shipping mode alone, with the AUC on V2 of a classifier trained on the training block.

    The columns are ``source_column``, ``column_es``, ``kind`` (``reference``, ``outcome`` or ``profit``), ``known_at_es``, ``auc_with_mode_valid``, ``note_es`` and
    ``sort_order`` (the reference first, then by AUC, highest first). The connection is read-only for the call; the date defaults to the launch.
    """
    day = as_of or cfg.deliveries.launch_date
    conn.execute("PRAGMA query_only = ON")
    try:
        frame = _frame(conn, cfg, day)
        rows = [(REFERENCE, "Modo de envío (referencia)", "reference", "Al crear el pedido", _auc(frame, cfg, None), "Lo que se sabe al crear el pedido: la regla que el modelo tiene que superar.")]
        scored = [(c, _auc(frame, cfg, c)) for c in COLUMNS]
    finally:
        conn.execute("PRAGMA query_only = OFF")
    rows += [(c.source, c.column_es, c.kind, c.known_at_es, auc, c.note_es) for c, auc in sorted(scored, key=lambda item: (-item[1], item[0].source))]
    table = pd.DataFrame(rows, columns=["source_column", "column_es", "kind", "known_at_es", "auc_with_mode_valid", "note_es"])
    table["sort_order"] = np.arange(len(table))
    return table
