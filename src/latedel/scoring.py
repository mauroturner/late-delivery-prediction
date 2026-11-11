"""Scoring of the new orders of a delivery with the frozen model (RF17, AC22, M02 and M04, decision D33).

``score_new_orders`` is the function that a service would wrap: it takes the orders that came with a delivery, builds their variables with ``build_features`` from
the two staging tables of the orders (the only inputs, as for every variable of the model), asks the frozen model for the calibrated probability of each one
(``predict_risk`` refuses a matrix that is not exactly the set of the model: M02), checks that every probability is a finite number in [0, 1] (M04) and inserts one
row per order into ``model_scores``, which is insert-only: a score is never changed, deleted or replaced. A repetition of the same task scores only the orders that
have no score yet. Orders that are cancelled later are scored like any other (D05). Nothing here reads what is known after an order is created.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

import numpy as np

from latedel.features import build_features, read_order_frames
from latedel.model import RiskModel, predict_risk
from latedel.warehouse import apply_schema

logger = logging.getLogger(__name__)

CHUNK = 500    # variables per statement, well under the limit of SQLite


class ScoreError(RuntimeError):
    """The scores of a delivery cannot be produced or are not valid (M04); nothing was inserted."""


@dataclass(frozen=True)
class RunMeta:
    """What a run adds to every row it writes: its key, its calculation date and the version of the model."""

    run_key: str
    as_of: date
    model_version: str


@dataclass(frozen=True)
class ScoreResult:
    delivery_id: str
    scored: int            # rows inserted by this call
    already_scored: int    # orders of the delivery that had a score from an earlier call
    alerts: int            # of the orders scored by this call
    threshold: float
    model_version: str
    as_of: date


def _scored_among(conn: sqlite3.Connection, ids: list[int]) -> set[int]:
    found: set[int] = set()
    for start in range(0, len(ids), CHUNK):
        part = ids[start : start + CHUNK]
        found |= {r[0] for r in conn.execute(f"SELECT order_id FROM model_scores WHERE order_id IN ({','.join('?' * len(part))})", part)}
    return found


def score_new_orders(conn: sqlite3.Connection, delivery_id: str, model: RiskModel, threshold: float, run_meta: RunMeta) -> ScoreResult:
    """Score once each new order of `delivery_id`; the alert is ``risk >= threshold``. All or nothing: an invalid probability (M04) inserts no row.

    The delivery must be loaded and the staging tables built for ``run_meta.as_of`` (``warehouse.load_delivery`` and ``warehouse.rebuild_at`` do it). A delivery
    without orders (the launch is not scored, the close has none) gives an empty result.
    """
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:       # a bool is an int in Python: True is not the threshold 1.0
        raise ScoreError(f"el umbral debe ser un número entre 0 y 1, se recibió {threshold!r}")
    apply_schema(conn)
    ids = [r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE delivery_id = ? ORDER BY order_id", (delivery_id,))]
    done = _scored_among(conn, ids)
    todo = [i for i in ids if i not in done]
    if not todo:
        return ScoreResult(delivery_id, 0, len(done), 0, float(threshold), run_meta.model_version, run_meta.as_of)

    orders, lines = read_order_frames(conn, delivery_id)         # only this delivery's orders and lines, not everything accumulated
    orders = orders[orders["order_id"].isin(todo)]
    if len(orders) != len(todo):
        raise ScoreError(f"{len(todo) - len(orders)} pedidos de la entrega {delivery_id} no están en las etapas de {run_meta.as_of}: se reconstruyen con rebuild_at antes de puntuar")
    lines = lines[lines["order_id"].isin(todo)]
    matrix = build_features(orders, lines, model.feature_set)
    risk = predict_risk(model, matrix)
    if len(risk) != len(todo) or not np.isfinite(risk).all() or risk.min() < 0 or risk.max() > 1:
        raise ScoreError(f"M04: la entrega {delivery_id} no tiene una probabilidad finita en [0, 1] por pedido nuevo; no se insertó ningún puntaje")
    rows = [
        (int(order), run_meta.as_of.isoformat(), run_meta.run_key, run_meta.model_version, float(p), float(threshold), int(p >= threshold))
        for order, p in zip(matrix.index, risk, strict=True)
    ]
    conn.execute("BEGIN IMMEDIATE")
    try:
        # the orders are looked at again inside the transaction that inserts: a call that overlapped this one and committed first has scored some of them, and those scores
        # are its own and stay as they are; this call inserts the rest instead of losing its whole batch to the insert-only trigger (H3)
        meanwhile = _scored_among(conn, todo)
        rows = [r for r in rows if r[0] not in meanwhile]
        conn.executemany("INSERT INTO model_scores (order_id, as_of_date, run_key, model_version, risk, threshold, is_alert) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    alerts = sum(r[6] for r in rows)
    already = len(done) + len(meanwhile)
    logger.info("score %s: delivery=%s scored=%d already=%d alerts=%d threshold=%s", run_meta.run_key, delivery_id, len(rows), already, alerts, threshold)
    return ScoreResult(delivery_id, len(rows), already, alerts, float(threshold), run_meta.model_version, run_meta.as_of)
