"""Monthly follow-up of the alerts that have already closed (RF18, AC22, decision D17).

For a calculation date `as_of`, ``tracking_at`` counts, by month of the order and shipping mode, the orders the frozen model scored, the alerts it raised and what
became of them once the outcome was known: the closed orders and alerts, the alerts that arrived late, the late orders that had no alert, the delays the model
expected (the sum of the risk of the closed orders) against the ones observed, and the orders that were cancelled afterwards (I04). It is what a logistics team
would see month by month. It changes neither the model nor the threshold.

Everything is read with the date as a filter (the scores recorded up to it, the outcomes known up to it), so a date computed later on a fuller warehouse gives
exactly what it gave on the day (RNF06). A cancelled order has no label of the task (D05): it is counted as cancelled and never as closed, late or missed.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd

from latedel.validate import RuleResult, make_result
from latedel.warehouse import apply_schema

logger = logging.getLogger(__name__)

TRACKING_COLUMNS: tuple[str, ...] = (
    "order_month", "shipping_mode", "orders_scored", "alerts", "closed_orders", "closed_alerts", "alerts_late", "late_closed", "late_missed", "late_expected",
    "cancelled_orders", "alerts_on_cancelled",
)
COUNTS: tuple[str, ...] = tuple(c for c in TRACKING_COLUMNS if c not in ("order_month", "shipping_mode", "late_expected"))

# a closed order is one with a known outcome that is not a cancellation; it is late when its real days are above the scheduled ones (the definition of stg_outcomes)
_SQL = """
WITH scored AS (
    SELECT o.order_id, substr(o.order_date, 1, 7) AS order_month, o.shipping_mode, o.scheduled_days, s.risk, s.is_alert
    FROM model_scores AS s
    JOIN raw_orders AS o ON o.order_id = s.order_id
    WHERE s.as_of_date <= :as_of AND o.order_date <= :as_of
),
known AS (
    SELECT order_id, is_cancelled, real_days FROM raw_outcomes WHERE outcome_known_date <= :as_of
),
joined AS (
    SELECT sc.order_month, sc.shipping_mode, sc.risk, sc.is_alert,
           CASE WHEN k.order_id IS NOT NULL AND k.is_cancelled = 0 THEN 1 ELSE 0 END AS closed,
           CASE WHEN k.order_id IS NOT NULL AND k.is_cancelled = 0 AND k.real_days > sc.scheduled_days THEN 1 ELSE 0 END AS late,
           CASE WHEN k.order_id IS NOT NULL AND k.is_cancelled = 1 THEN 1 ELSE 0 END AS cancelled
    FROM scored AS sc LEFT JOIN known AS k ON k.order_id = sc.order_id
)
SELECT order_month, shipping_mode,
       COUNT(*) AS orders_scored,
       SUM(is_alert) AS alerts,
       SUM(closed) AS closed_orders,
       SUM(closed * is_alert) AS closed_alerts,
       SUM(late * is_alert) AS alerts_late,
       SUM(late) AS late_closed,
       SUM(late * (1 - is_alert)) AS late_missed,
       SUM(closed * risk) AS late_expected,
       SUM(cancelled) AS cancelled_orders,
       SUM(cancelled * is_alert) AS alerts_on_cancelled
FROM joined
GROUP BY order_month, shipping_mode
ORDER BY order_month, shipping_mode
"""


def tracking_at(conn: sqlite3.Connection, as_of: date) -> pd.DataFrame:
    """The monthly follow-up on `as_of`: one row per month of the order and shipping mode with the columns of ``TRACKING_COLUMNS`` (counts as integers).

    Only the scores recorded on or before `as_of` and the outcomes known on or before it are read. A date before the first score gives an empty frame with the columns.
    """
    apply_schema(conn)
    rows = conn.execute(_SQL, {"as_of": as_of.isoformat()}).fetchall()
    frame = pd.DataFrame(rows, columns=list(TRACKING_COLUMNS))
    for column in COUNTS:
        frame[column] = frame[column].astype("int64")
    frame["late_expected"] = frame["late_expected"].astype("float64")
    return frame


def store_tracking(conn: sqlite3.Connection, as_of: date, frame: pd.DataFrame) -> None:
    """Replace the rows of ``tracking_monthly`` for `as_of` with `frame`, in one transaction; the rows of other dates are not touched."""
    if list(frame.columns) != list(TRACKING_COLUMNS):
        raise ValueError(f"el seguimiento debe tener exactamente las columnas {list(TRACKING_COLUMNS)}; se recibió {list(frame.columns)}")
    day = as_of.isoformat()
    rows = [(day, *[(v.item() if hasattr(v, "item") else v) for v in row]) for row in frame.itertuples(index=False, name=None)]
    conn.execute("BEGIN IMMEDIATE")
    try:
        apply_schema(conn)
        conn.execute("DELETE FROM tracking_monthly WHERE as_of_date = ?", (day,))
        conn.executemany(f"INSERT INTO tracking_monthly (as_of_date, {', '.join(TRACKING_COLUMNS)}) VALUES (?, {', '.join('?' for _ in TRACKING_COLUMNS)})", rows)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def tracking_rules(frame: pd.DataFrame) -> list[RuleResult]:
    """I02 (the alerts raised and the share of the scored orders) and I04 (the alerts on orders that were cancelled afterwards, D05): they only count."""
    scored, alerts = int(frame["orders_scored"].sum()), int(frame["alerts"].sum())
    cancelled, on_cancelled = int(frame["cancelled_orders"].sum()), int(frame["alerts_on_cancelled"].sum())
    return [
        make_result("I02", True, rows=alerts, orders=scored, examples=[{"alerts": alerts, "scored": scored, "alert_share": round(alerts / scored, 6) if scored else None}]),
        make_result("I04", True, rows=on_cancelled, orders=cancelled, examples=[{"alerts_on_cancelled": on_cancelled, "cancelled_orders": cancelled}]),
    ]


@dataclass(frozen=True)
class TrackResult:
    as_of: date
    frame: pd.DataFrame
    rules: list[RuleResult]

    def summary(self) -> dict:
        """What the run records of the follow-up (JSON-ready): the totals over every month and mode."""
        totals = {c: int(self.frame[c].sum()) for c in COUNTS}
        return {"as_of": self.as_of.isoformat(), "rows": int(len(self.frame)), **totals, "late_expected": float(self.frame["late_expected"].sum())}


def track(conn: sqlite3.Connection, as_of: date) -> TrackResult:
    """The task ``track``: compute the follow-up on `as_of`, replace the rows of that date in ``tracking_monthly`` and return them with the rules I02 and I04.

    Repeating a date gives the same rows; the rows of other dates stay as they were.
    """
    frame = tracking_at(conn, as_of)
    store_tracking(conn, as_of, frame)
    result = TrackResult(as_of, frame, tracking_rules(frame))
    logger.info("track %s: %s", as_of, result.summary())
    return result
