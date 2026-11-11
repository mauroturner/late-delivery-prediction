"""The monthly follow-up of the closed alerts (U16, AC22, decisions D05 and D17).

The hand case has ten orders in two months and two shipping modes; every count below was made by hand from the list. The same warehouse is read on two
calculation dates, and the first date is read on a warehouse that already holds the second one's scores and outcomes: what happens later changes nothing.
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd
import pytest

from latedel.tracking import COUNTS, TRACKING_COLUMNS, store_tracking, track, tracking_at, tracking_rules
from latedel.warehouse import apply_schema

# (order_id, month, mode, scheduled days, risk, alert, score date, outcome known date or None, cancelled, real days)
ORDERS = [
    (1, "2017-01", "First Class", 1, 0.90, 1, "2017-01-31", "2017-01-20", 0, 2),    # closed, late, alert
    (2, "2017-01", "First Class", 1, 0.80, 1, "2017-01-31", "2017-01-25", 0, 2),    # closed, late, alert
    (3, "2017-01", "First Class", 1, 0.70, 1, "2017-01-31", "2017-02-10", 0, 2),    # open on January 31, closed and late on February 28
    (4, "2017-01", "Standard Class", 4, 0.30, 0, "2017-01-31", "2017-01-22", 0, 6),  # closed, late, no alert: missed
    (5, "2017-01", "Standard Class", 4, 0.20, 0, "2017-01-31", "2017-01-23", 0, 3),  # closed, on time
    (6, "2017-01", "Standard Class", 4, 0.60, 1, "2017-01-31", "2017-01-24", 0, 3),  # closed, on time, alert: a false alarm
    (7, "2017-01", "Standard Class", 4, 0.65, 1, "2017-01-31", "2017-01-26", 1, 0),  # cancelled, with an alert
    (8, "2017-02", "Standard Class", 4, 0.50, 1, "2017-02-28", "2017-02-27", 0, 5),  # closed, late, alert
    (9, "2017-02", "Standard Class", 4, 0.10, 0, "2017-02-28", "2017-03-05", 0, 2),  # open on February 28
    (10, "2017-02", "Standard Class", 4, 0.40, 0, "2017-02-28", "2017-02-20", 0, 2),  # closed, on time
]


@pytest.fixture()
def hand() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    apply_schema(conn)
    for order_id, month, mode, scheduled, risk, alert, scored, known, cancelled, real in ORDERS:
        conn.execute(
            "INSERT INTO raw_orders VALUES (?, 'D', ?, ?, 'pre_break', ?, ?, 'DEBIT', 'M', 'R', 'C', 'S', 'C')", (order_id, f"{month}-10T09:00", f"{month}-10", mode, scheduled)
        )
        conn.execute("INSERT INTO model_scores VALUES (?, ?, 'run', 'v1', ?, 0.5, ?)", (order_id, scored, risk, alert))
        if known is not None:
            conn.execute("INSERT INTO raw_outcomes VALUES (?, 'D', ?, 'x', 'y', ?, '2017-01-12T09:00', 0, ?, 0.0, 0.0)", (order_id, known, real, cancelled))
    return conn


def rows(frame: pd.DataFrame) -> dict[tuple[str, str], dict]:
    return {(r["order_month"], r["shipping_mode"]): {k: round(float(r[k]), 9) for k in TRACKING_COLUMNS[2:]} for _, r in frame.iterrows()}


def counts(**kw) -> dict:
    base = dict.fromkeys(TRACKING_COLUMNS[2:], 0.0)
    return {**base, **{k: round(float(v), 9) for k, v in kw.items()}}


def test_closed_alert_metrics_hand_case(hand):
    """AC22: exact monthly counts of the closed alerts on two dates; an open order is scored but not closed; a cancelled one is counted apart."""
    january = tracking_at(hand, date(2017, 1, 31))
    assert list(january.columns) == list(TRACKING_COLUMNS) and len(january) == 2
    assert rows(january) == {
        ("2017-01", "First Class"): counts(orders_scored=3, alerts=3, closed_orders=2, closed_alerts=2, alerts_late=2, late_closed=2, late_missed=0, late_expected=0.9 + 0.8),
        ("2017-01", "Standard Class"): counts(
            orders_scored=4, alerts=2, closed_orders=3, closed_alerts=1, alerts_late=0, late_closed=1, late_missed=1, late_expected=0.3 + 0.2 + 0.6, cancelled_orders=1,
            alerts_on_cancelled=1,
        ),
    }
    assert {c: str(january[c].dtype) for c in COUNTS} == {c: "int64" for c in COUNTS}

    february = tracking_at(hand, date(2017, 2, 28))
    assert rows(february) == {
        ("2017-01", "First Class"): counts(orders_scored=3, alerts=3, closed_orders=3, closed_alerts=3, alerts_late=3, late_closed=3, late_missed=0, late_expected=0.9 + 0.8 + 0.7),
        ("2017-01", "Standard Class"): rows(january)[("2017-01", "Standard Class")],
        ("2017-02", "Standard Class"): counts(orders_scored=3, alerts=1, closed_orders=2, closed_alerts=1, alerts_late=1, late_closed=1, late_missed=0, late_expected=0.5 + 0.4),
    }
    # precision and recall of the closed alerts, as the dashboard takes them from these counts: January, Standard Class
    jan = rows(january)[("2017-01", "Standard Class")]
    assert jan["alerts_late"] / jan["closed_alerts"] == 0.0 and jan["alerts_late"] / (jan["alerts_late"] + jan["late_missed"]) == 0.0
    # every late closed order is an alert that arrived late or a late order without an alert
    for frame in (january, february):
        assert (frame["late_closed"] == frame["alerts_late"] + frame["late_missed"]).all()
        assert (frame["closed_alerts"] <= frame["alerts"]).all() and (frame["closed_alerts"] <= frame["closed_orders"]).all()


def test_a_date_reads_only_what_was_recorded_and_known_by_it(hand):
    """The warehouse already holds February's scores and outcomes; January 31 sees none of them, and a date before any score is empty."""
    january = tracking_at(hand, date(2017, 1, 31))
    assert set(january["order_month"]) == {"2017-01"} and int(january["orders_scored"].sum()) == 7
    hand.execute("DROP TRIGGER model_scores_no_delete")
    hand.execute("DELETE FROM model_scores WHERE as_of_date > '2017-01-31'")
    hand.execute("DELETE FROM raw_outcomes WHERE outcome_known_date > '2017-01-31'")
    assert tracking_at(hand, date(2017, 1, 31)).equals(january), "removing everything after the date changes nothing"
    empty = tracking_at(hand, date(2017, 1, 30))
    assert empty.empty and list(empty.columns) == list(TRACKING_COLUMNS)


def test_the_task_stores_the_rows_of_its_date_replaces_them_on_repetition_and_leaves_other_dates(hand):
    first = track(hand, date(2017, 1, 31))
    stored = hand.execute("SELECT * FROM tracking_monthly WHERE as_of_date = '2017-01-31' ORDER BY order_month, shipping_mode").fetchall()
    assert len(stored) == 2 and stored[0][:3] == ("2017-01-31", "2017-01", "First Class") and first.summary()["orders_scored"] == 7
    second = track(hand, date(2017, 2, 28))
    assert second.summary()["rows"] == 3 and hand.execute("SELECT COUNT(*) FROM tracking_monthly").fetchone()[0] == 5
    again = track(hand, date(2017, 1, 31))
    assert hand.execute("SELECT * FROM tracking_monthly WHERE as_of_date = '2017-01-31' ORDER BY order_month, shipping_mode").fetchall() == stored
    assert hand.execute("SELECT COUNT(*) FROM tracking_monthly").fetchone()[0] == 5 and again.frame.equals(first.frame)
    # a score recorded afterwards changes the later date and, once the date is computed again, only that date
    hand.execute("UPDATE raw_outcomes SET real_days = 6 WHERE order_id = 10")
    track(hand, date(2017, 2, 28))
    assert hand.execute("SELECT * FROM tracking_monthly WHERE as_of_date = '2017-01-31' ORDER BY order_month, shipping_mode").fetchall() == stored
    assert hand.execute("SELECT late_closed FROM tracking_monthly WHERE as_of_date = '2017-02-28' AND order_month = '2017-02'").fetchone()[0] == 2


def test_the_task_refuses_a_frame_that_is_not_the_follow_up_and_stores_nothing(hand):
    with pytest.raises(ValueError, match="exactamente las columnas"):
        store_tracking(hand, date(2017, 1, 31), pd.DataFrame({"order_month": ["2017-01"]}))
    assert hand.execute("SELECT COUNT(*) FROM tracking_monthly").fetchone()[0] == 0
    broken = tracking_at(hand, date(2017, 1, 31)).assign(alerts=None)
    with pytest.raises(sqlite3.IntegrityError):
        store_tracking(hand, date(2017, 1, 31), broken)
    assert hand.execute("SELECT COUNT(*) FROM tracking_monthly").fetchone()[0] == 0 and not hand.in_transaction


def test_i02_and_i04_only_count(hand):
    january = tracking_at(hand, date(2017, 1, 31))
    i02, i04 = tracking_rules(january)
    assert (i02.rule_id, i02.passed, i02.rows_affected, i02.orders_affected) == ("I02", True, 5, 7)
    assert i02.examples == [{"alerts": 5, "scored": 7, "alert_share": round(5 / 7, 6)}]
    assert (i04.rule_id, i04.passed, i04.rows_affected, i04.orders_affected) == ("I04", True, 1, 1)
    empty = tracking_rules(tracking_at(hand, date(2016, 1, 1)))
    assert empty[0].examples[0]["alert_share"] is None and empty[0].passed is True


def test_the_table_exists_with_its_key_and_the_previous_tables_are_untouched(hand):
    columns = [r[1] for r in hand.execute("PRAGMA table_info(tracking_monthly)")]
    assert columns == ["as_of_date", *TRACKING_COLUMNS]
    key = [r[1] for r in hand.execute("PRAGMA table_info(tracking_monthly)") if r[5]]
    assert key == ["as_of_date", "order_month", "shipping_mode"]
    # the insert-only tables are still insert-only
    with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
        hand.execute("UPDATE model_scores SET risk = 0.1")
