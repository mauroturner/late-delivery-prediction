"""The monthly follow-up on the warehouse of the synthetic fixture (U16, AC22): the counts agree with the labelled orders and with the evaluation, and a date does not
depend on what happens after it."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from latedel.evaluation import evaluate_block
from latedel.split import labelled_orders
from latedel.tracking import track, tracking_at


def scored_with_outcome(conn, cfg, as_of: date) -> pd.DataFrame:
    """Every scored order up to `as_of` with its label if it is known then, from ``labelled_orders`` (the mart) and ``model_scores``: an independent path."""
    scores = pd.read_sql_query(
        "SELECT s.order_id, s.risk, s.is_alert, substr(o.order_date, 1, 7) AS month, o.shipping_mode FROM model_scores AS s JOIN raw_orders AS o ON o.order_id = s.order_id "
        "WHERE s.as_of_date <= ?", conn, params=(as_of.isoformat(),),
    )
    labelled = labelled_orders(conn, as_of, cfg)[["order_id", "is_late"]]
    return scores.merge(labelled, on="order_id", how="left")


def test_the_follow_up_of_the_fixture_agrees_with_the_labelled_orders_and_the_scores(copy_history):
    conn, cfg = copy_history()
    for as_of in (date(2017, 3, 31), date(2017, 10, 31), date(2018, 2, 7)):
        frame = tracking_at(conn, as_of)
        merged = scored_with_outcome(conn, cfg, as_of)
        closed = merged[merged["is_late"].notna()]
        assert int(frame["orders_scored"].sum()) == len(merged) > 100 and int(frame["alerts"].sum()) == int(merged["is_alert"].sum())
        assert int(frame["closed_orders"].sum()) == len(closed) and int(frame["late_closed"].sum()) == int(closed["is_late"].sum())
        assert int(frame["closed_alerts"].sum()) == int(closed["is_alert"].sum()) and int(frame["alerts_late"].sum()) == int((closed["is_alert"] * closed["is_late"]).sum())
        assert int(frame["late_missed"].sum()) == int(((1 - closed["is_alert"]) * closed["is_late"]).sum())
        assert frame["late_expected"].sum() == pytest.approx(closed["risk"].sum())
        # what is scored and neither closed nor a cancellation is open: the three add up
        assert int(frame["orders_scored"].sum()) >= int(frame["closed_orders"].sum()) + int(frame["cancelled_orders"].sum())
        assert int(frame["cancelled_orders"].sum()) == int(conn.execute(
            "SELECT COUNT(*) FROM model_scores AS s JOIN raw_outcomes AS r ON r.order_id = s.order_id WHERE r.is_cancelled = 1 AND r.outcome_known_date <= ? AND s.as_of_date <= ?",
            (as_of.isoformat(), as_of.isoformat()),
        ).fetchone()[0])
    last = tracking_at(conn, date(2018, 2, 7))
    assert int(last["cancelled_orders"].sum()) > 0 and int(last["alerts_on_cancelled"].sum()) > 0, "the fixture has cancelled orders that were scored and alerted"


def test_the_closed_alerts_of_the_test_months_are_the_alerts_the_evaluation_counts(copy_history):
    """The follow-up and the evaluation read the same scores and labels by different paths: the alerts of the months of the test are the orders it flags."""
    conn, cfg = copy_history()
    as_of = date(2017, 10, 31)
    result = evaluate_block(conn, "test", cfg, as_of=as_of)
    frame = tracking_at(conn, as_of)
    months = frame[(frame["order_month"] >= "2017-01") & (frame["order_month"] <= "2017-09")]
    flagged = result.metrics["model"]["flagged_share"] * result.orders
    assert int(months["closed_alerts"].sum()) == round(flagged) and int(months["closed_orders"].sum()) == result.orders
    assert int(months["alerts_late"].sum()) == round(result.metrics["model"]["precision"] * flagged)
    assert int(months["late_closed"].sum()) == round(result.metrics["model"]["late_rate"] * result.orders)


def test_a_date_on_the_whole_history_is_what_it_was_on_the_day(copy_history):
    whole, _ = copy_history()
    then, _ = copy_history("M2017-10")
    pd.testing.assert_frame_equal(tracking_at(whole, date(2017, 10, 31)), tracking_at(then, date(2017, 10, 31)))
    assert tracking_at(whole, date(2017, 10, 31)).equals(tracking_at(whole, date(2017, 10, 31)))


def test_the_task_keeps_one_set_of_rows_for_each_date_it_ran(copy_history):
    conn, _ = copy_history()
    dates = [date(2017, 1, 31), date(2017, 6, 30), date(2017, 10, 31)]
    results = [track(conn, d) for d in dates]
    for d, r in zip(dates, results, strict=True):
        assert conn.execute("SELECT COUNT(*) FROM tracking_monthly WHERE as_of_date = ?", (d.isoformat(),)).fetchone()[0] == len(r.frame) > 0
    total = sum(len(r.frame) for r in results)
    track(conn, dates[1])
    assert conn.execute("SELECT COUNT(*) FROM tracking_monthly").fetchone()[0] == total, "repeating a date replaces its rows and adds nothing"
    assert results[0].summary()["orders_scored"] < results[2].summary()["orders_scored"], "more is scored as the months go by"
    assert [r.rule_id for r in results[2].rules] == ["I02", "I04"]
