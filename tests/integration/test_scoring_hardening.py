"""Hardening of the scoring after the native review of U14 (H3): a boolean is not a threshold, overlapping calls do not undo each other, a call reads only the orders of its
delivery, and the insert-only tables refuse every rewrite whatever the setting of ``recursive_triggers``.

R1-threshold-bool and R3-threshold-bool-leniency (``True`` was taken for 1.0), R4-scoring-concurrent-retry and R3-score-new-orders-race (the scored orders were read before
the transaction that inserts, so a call that overlapped another one lost its whole batch to the trigger), R4-scoring-full-table-read (every call read the accumulated
staging tables) and R1-registry-insertonly-sql (the insert-only enforcement is a trigger and the review asked whether it depends on ``recursive_triggers``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from latedel import scoring
from latedel.features import read_order_frames
from latedel.scoring import ScoreError
from latedel.warehouse import connect
from tests.fixtures.helpers import file_digest
from tests.integration.test_scoring import SCORES, load_next, score

OTHER = "INSERT INTO model_scores VALUES (?, '2017-01-31', 'run-other', 'v1', 0.25, 0.5, 0)"


@pytest.mark.parametrize("threshold", [True, False])
def test_a_boolean_is_not_a_threshold(monthly, threshold):
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    with pytest.raises(ScoreError, match="umbral"):
        score(conn, registered, spec, threshold=threshold)
    assert conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0] == 0
    # the numbers at the two edges are still thresholds
    assert score(conn, registered, spec, threshold=0).scored > 0


def test_a_numpy_number_and_an_integer_are_still_thresholds(monthly):
    import numpy as np

    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    assert score(conn, registered, spec, threshold=np.float64(0.5)).threshold == 0.5
    assert score(conn, registered, spec, threshold=1).already_scored > 0


def other_connection(conn) -> sqlite3.Connection:
    return connect(Path(conn.execute("PRAGMA database_list").fetchone()[2]))


def test_a_call_that_overlaps_another_one_keeps_the_scores_of_the_other_and_inserts_the_rest(monthly, monkeypatch):
    """R4-scoring-concurrent-retry, R3-score-new-orders-race: the other call commits half of the delivery while this one is predicting; this one inserts the other half."""
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    ids = [r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE delivery_id = ? ORDER BY order_id", (spec.delivery_id,))]
    other = other_connection(conn)
    original = scoring.predict_risk
    taken = []

    def racing(model, matrix):
        risk = original(model, matrix)
        taken[:] = [int(i) for i in matrix.index[::2]]
        other.executemany(OTHER, [(i,) for i in taken])          # committed: the connection is in autocommit
        return risk

    monkeypatch.setattr(scoring, "predict_risk", racing)
    result = score(conn, registered, spec)
    rows = conn.execute(SCORES).fetchall()
    mine = [r for r in rows if r[2] == f"run-{spec.delivery_id}"]
    theirs = [r for r in rows if r[2] == "run-other"]
    assert [r[0] for r in rows] == ids, "every order has exactly one score"
    assert sorted(r[0] for r in theirs) == sorted(taken) and {r[4] for r in theirs} == {0.25}, "the scores of the other call are exactly what it wrote"
    assert (result.scored, result.already_scored, result.alerts) == (len(mine), len(theirs), sum(r[6] for r in mine)) and len(mine) + len(theirs) == len(ids)
    assert not conn.in_transaction
    other.close()


def test_a_call_whose_whole_delivery_was_scored_in_the_meantime_inserts_nothing_and_does_not_fail(monthly, monkeypatch):
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    other = other_connection(conn)
    original = scoring.predict_risk

    def racing(model, matrix):
        risk = original(model, matrix)
        other.executemany(OTHER, [(int(i),) for i in matrix.index])
        return risk

    monkeypatch.setattr(scoring, "predict_risk", racing)
    result = score(conn, registered, spec)
    total = conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0]
    assert (result.scored, result.already_scored, result.alerts) == (0, total, 0) and {r[2] for r in conn.execute(SCORES)} == {"run-other"}
    other.close()


def test_a_call_reads_only_the_orders_of_its_delivery(monthly, monkeypatch):
    """R4-scoring-full-table-read: the frames that scoring reads are the delivery's, not the accumulated staging tables."""
    conn, registered, cfg, folder, plan, entries = monthly
    january, february = (load_next(conn, folder, plan, entries, cfg, i) for i in (1, 2))
    score(conn, registered, january)
    staged = conn.execute("SELECT COUNT(*) FROM stg_orders").fetchone()[0]
    expected = conn.execute("SELECT COUNT(*) FROM raw_orders WHERE delivery_id = ?", (february.delivery_id,)).fetchone()[0]
    seen = []
    original = scoring.read_order_frames

    def spy(connection, *args, **kwargs):
        orders, lines = original(connection, *args, **kwargs)
        seen.append((len(orders), len(lines), set(orders["order_id"]), set(lines["order_id"])))
        return orders, lines

    monkeypatch.setattr(scoring, "read_order_frames", spy)
    result = score(conn, registered, february)
    assert result.scored == expected and len(seen) == 1
    n_orders, n_lines, order_ids, line_orders = seen[0]
    assert n_orders == expected < staged / 2 and line_orders == order_ids and n_lines >= n_orders
    # and it scores what it scored before: the same risk for each order as a model reading the whole staging
    full_orders, full_lines = read_order_frames(conn)
    from latedel.features import build_features
    from latedel.model import predict_risk

    matrix = build_features(full_orders[full_orders["order_id"].isin(order_ids)], full_lines[full_lines["order_id"].isin(order_ids)], registered.model.feature_set)
    stored = pd.Series(dict(conn.execute("SELECT order_id, risk FROM model_scores WHERE run_key = ?", (f"run-{february.delivery_id}",)).fetchall()))
    pd.testing.assert_series_equal(stored.sort_index(), pd.Series(predict_risk(registered.model, matrix), index=matrix.index).sort_index(), check_names=False, check_index_type=False)


def test_the_frames_of_one_delivery_are_the_rows_of_the_whole_staging_that_belong_to_it(monthly):
    conn, registered, cfg, folder, plan, entries = monthly
    for index in (1, 2):
        load_next(conn, folder, plan, entries, cfg, index)
    delivery = plan[2].delivery_id
    orders, lines = read_order_frames(conn, delivery_id=delivery)
    whole_orders, whole_lines = read_order_frames(conn)
    ids = {r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE delivery_id = ?", (delivery,))}
    pd.testing.assert_frame_equal(orders.reset_index(drop=True), whole_orders[whole_orders["order_id"].isin(ids)].reset_index(drop=True))
    pd.testing.assert_frame_equal(lines.reset_index(drop=True), whole_lines[whole_lines["order_id"].isin(ids)].reset_index(drop=True))
    assert 0 < len(orders) < len(whole_orders) and list(orders.columns) == list(whole_orders.columns)
    empty_orders, empty_lines = read_order_frames(conn, delivery_id="NOPE")
    assert empty_orders.empty and empty_lines.empty and list(empty_orders.columns) == list(whole_orders.columns) and list(empty_lines.columns) == list(whole_lines.columns)


STATEMENTS = {
    "model_scores": (
        "UPDATE model_scores SET risk = 0.5", "DELETE FROM model_scores", "INSERT OR REPLACE INTO model_scores SELECT * FROM model_scores",
        "REPLACE INTO model_scores SELECT * FROM model_scores", "INSERT INTO model_scores SELECT * FROM model_scores", "INSERT OR IGNORE INTO model_scores SELECT * FROM model_scores",
        "INSERT INTO model_scores SELECT * FROM model_scores WHERE true ON CONFLICT(order_id) DO UPDATE SET risk = 0.5",
        "INSERT INTO model_scores SELECT * FROM model_scores WHERE true ON CONFLICT(order_id) DO NOTHING",
    ),
    "model_registry": (
        "UPDATE model_registry SET threshold = 0.1", "DELETE FROM model_registry", "INSERT OR REPLACE INTO model_registry SELECT * FROM model_registry",
        "REPLACE INTO model_registry SELECT * FROM model_registry", "INSERT INTO model_registry SELECT * FROM model_registry", "INSERT OR IGNORE INTO model_registry SELECT * FROM model_registry",
        "INSERT INTO model_registry SELECT * FROM model_registry WHERE true ON CONFLICT(model_version) DO UPDATE SET threshold = 0.1",
        "INSERT INTO model_registry SELECT * FROM model_registry WHERE true ON CONFLICT(model_version) DO NOTHING",
    ),
}


@pytest.mark.parametrize("recursive", ["ON", "OFF"])
def test_the_insert_only_tables_refuse_every_rewrite_whatever_recursive_triggers_says(monthly, recursive):
    """R1-registry-insertonly-sql: the protection is the three triggers, and it holds with ``recursive_triggers`` on and off (SQLite's default is off)."""
    conn, registered, cfg, folder, plan, entries = monthly
    score(conn, registered, load_next(conn, folder, plan, entries, cfg, 1))
    path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.execute(f"PRAGMA recursive_triggers = {recursive}")
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == (recursive == "ON")
    before, tables = file_digest(path), {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in STATEMENTS}
    for table, statements in STATEMENTS.items():
        for statement in statements:
            with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
                conn.execute(statement)
    assert file_digest(path) == before and {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in STATEMENTS} == tables
