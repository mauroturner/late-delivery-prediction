"""The scoring of the new orders with the frozen model (U14, AC21, AC22 in part, M02, M04, decisions D05 and D33): one score per new order, never changed afterwards.

The fixture's model is trained, registered and sealed once at the launch (``fitted_launch``); each test copies that warehouse, loads the monthly deliveries into the
copy and scores them as the monthly runs will. The real file's first two months are scored with the real model; no label is read.
"""

from __future__ import annotations

import ast
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from latedel.deliveries import read_delivery
from latedel.launch import fit_model, load_registered_model
from latedel.scoring import RunMeta, ScoreError, score_new_orders
from latedel.warehouse import connect, load_delivery, rebuild_at
from tests.fixtures.helpers import file_digest

SCORES = "SELECT order_id, as_of_date, run_key, model_version, risk, threshold, is_alert FROM model_scores ORDER BY order_id"


def load_next(conn, folder, plan, entries, cfg, index):
    spec = plan[index]
    load_delivery(conn, read_delivery(folder, entries[index]), spec, f"run-{spec.delivery_id}", cfg, sha256_json=entries[index].sha256_json)
    return spec


def score(conn, registered, spec, *, run_key=None, threshold=None):
    meta = RunMeta(run_key or f"run-{spec.delivery_id}", spec.as_of, registered.model_version)
    return score_new_orders(conn, spec.delivery_id, registered.model, registered.threshold if threshold is None else threshold, meta)


def test_scores_never_change_in_later_runs(monthly):
    conn, registered, cfg, folder, plan, entries = monthly
    january = load_next(conn, folder, plan, entries, cfg, 1)
    first = score(conn, registered, january)
    snapshot = conn.execute(SCORES).fetchall()
    assert first.scored == len(snapshot) > 50 and first.already_scored == 0

    february = load_next(conn, folder, plan, entries, cfg, 2)
    score(conn, registered, february)
    after = conn.execute(SCORES).fetchall()
    january_ids = {r[0] for r in snapshot}
    assert [r for r in after if r[0] in january_ids] == snapshot, "the scores of January are exactly what they were after the next run"
    assert len(after) > len(snapshot)

    # a later run that scores January again, with another threshold and another key, adds nothing and changes nothing
    again = score(conn, registered, january, run_key="run-later", threshold=0.0)
    assert (again.scored, again.already_scored, again.alerts) == (0, first.scored, 0)
    assert conn.execute(SCORES).fetchall() == after

    # and nothing can change them in SQL, whatever the statement
    digest = file_digest(Path(conn.execute("PRAGMA database_list").fetchone()[2]))
    for statement in (
        "UPDATE model_scores SET risk = 0.5", "UPDATE model_scores SET is_alert = 1 - is_alert", "DELETE FROM model_scores", f"DELETE FROM model_scores WHERE order_id = {snapshot[0][0]}", f"UPDATE model_scores SET risk = 0.5 WHERE order_id = {snapshot[0][0]}",
        "INSERT OR REPLACE INTO model_scores SELECT * FROM model_scores", "REPLACE INTO model_scores SELECT * FROM model_scores", "INSERT INTO model_scores SELECT * FROM model_scores",
        "INSERT OR IGNORE INTO model_scores SELECT * FROM model_scores",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
            conn.execute(statement)
    assert conn.execute(SCORES).fetchall() == after
    assert file_digest(Path(conn.execute("PRAGMA database_list").fetchone()[2])) == digest


def test_each_new_order_of_a_delivery_gets_exactly_one_score_with_the_date_the_key_the_version_and_the_threshold(monthly):
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    result = score(conn, registered, spec)
    new_orders = [r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE delivery_id = ? ORDER BY order_id", (spec.delivery_id,))]
    rows = conn.execute(SCORES).fetchall()
    assert [r[0] for r in rows] == new_orders and result.scored == len(new_orders)
    for order, as_of, run_key, version, risk, threshold, is_alert in rows:
        assert (as_of, run_key, version, threshold) == (spec.as_of.isoformat(), f"run-{spec.delivery_id}", "v1", registered.threshold)
        assert 0.0 <= risk <= 1.0 and is_alert == int(risk >= registered.threshold)
    assert result.alerts == sum(r[6] for r in rows) and result.threshold == registered.threshold and result.model_version == "v1" and result.as_of == spec.as_of
    # the scores are what the frozen model says about the variables of each order, the same as scoring them one at a time
    from latedel.features import build_features, read_order_frames
    from latedel.model import predict_risk

    orders, lines = read_order_frames(conn)
    matrix = build_features(orders[orders["order_id"].isin(new_orders)], lines[lines["order_id"].isin(new_orders)], registered.model.feature_set)
    np.testing.assert_array_equal([r[4] for r in rows], predict_risk(registered.model, matrix))
    one = new_orders[3]
    np.testing.assert_array_equal(predict_risk(registered.model, matrix.loc[[one]]), [rows[3][4]])


def test_orders_that_are_cancelled_later_are_scored_like_any_other(monthly, fixture_read):
    conn, registered, cfg, folder, plan, entries = monthly
    frames = fixture_read[0]
    cancelled = set(frames.outcomes.loc[frames.outcomes["is_cancelled"], "order_id"])
    expected_cancelled = set()
    for index in (1, 2, 3):
        spec = load_next(conn, folder, plan, entries, cfg, index)
        score(conn, registered, spec)
        expected_cancelled |= {r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE delivery_id = ?", (spec.delivery_id,))} & cancelled
    assert len(expected_cancelled) > 0, "the fixture has orders that are cancelled later in these months"
    scored = {r[0] for r in conn.execute("SELECT order_id FROM model_scores")}
    assert expected_cancelled <= scored, "an order that is cancelled afterwards is scored when it is new, as in use"
    assert len(scored) == conn.execute("SELECT COUNT(*) FROM raw_orders WHERE delivery_id IN (?, ?, ?)", [p.delivery_id for p in plan[1:4]]).fetchone()[0]


def test_a_repeated_task_scores_only_what_has_no_score_and_a_delivery_without_orders_scores_nothing(monthly):
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    ids = [r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE delivery_id = ? ORDER BY order_id", (spec.delivery_id,))]
    # a task that stopped half way: the first orders were scored by an earlier run with another key
    earlier = ids[: len(ids) // 2]
    conn.executemany("INSERT INTO model_scores VALUES (?, '2017-01-31', 'run-interrupted', 'v1', 0.25, 0.5, 0)", [(i,) for i in earlier])
    kept = conn.execute(SCORES).fetchall()
    result = score(conn, registered, spec)
    assert (result.scored, result.already_scored) == (len(ids) - len(earlier), len(earlier))
    rows = conn.execute(SCORES).fetchall()
    assert [r[0] for r in rows] == ids and rows[: len(earlier)] == kept, "the earlier scores are kept as they were"
    assert {r[2] for r in rows[len(earlier):]} == {f"run-{spec.delivery_id}"}
    for name in ("close", "unknown"):
        delivery_id = plan[-1].delivery_id if name == "close" else "NOPE"
        nothing = score_new_orders(conn, delivery_id, registered.model, registered.threshold, RunMeta("run-x", spec.as_of, "v1"))
        assert (nothing.scored, nothing.already_scored, nothing.alerts) == (0, 0, 0), name
    assert conn.execute(SCORES).fetchall() == rows


def test_a_failure_while_inserting_undoes_the_whole_delivery(monthly):
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    ids = [r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE delivery_id = ? ORDER BY order_id", (spec.delivery_id,))]
    conn.execute(f"CREATE TEMP TRIGGER boom BEFORE INSERT ON model_scores WHEN NEW.order_id = {ids[-1]} BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(sqlite3.IntegrityError, match="boom"):
        score(conn, registered, spec)
    assert conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0] == 0 and not conn.in_transaction
    conn.execute("DROP TRIGGER boom")
    assert score(conn, registered, spec).scored == len(ids)


class Calibrated:
    """Stands for the calibrator of a model: it answers the probabilities it was given, whatever the orders."""

    def __init__(self, values) -> None:
        self.values = values

    def predict_proba(self, x):
        p = np.resize(np.asarray(self.values, dtype=float), len(x))
        return np.column_stack([1 - p, p])


@pytest.mark.parametrize("bad", [[np.nan], [1.0000001], [-0.0000001], [np.inf], [0.4, 0.5, np.nan]])
def test_a_probability_that_is_not_finite_in_zero_one_inserts_no_score(monthly, bad):
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    broken = replace(registered.model, calibrated=Calibrated(bad))
    with pytest.raises(ScoreError, match="M04"):
        score_new_orders(conn, spec.delivery_id, broken, registered.threshold, RunMeta("run-x", spec.as_of, "v1"))
    assert conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0] == 0


@pytest.mark.parametrize("threshold", [-0.1, 1.5, float("nan"), "0.5", None])
def test_a_threshold_that_is_not_a_probability_is_refused(monthly, threshold):
    conn, registered, cfg, folder, plan, entries = monthly
    spec = load_next(conn, folder, plan, entries, cfg, 1)
    with pytest.raises(ScoreError, match="umbral"):
        score_new_orders(conn, spec.delivery_id, registered.model, threshold, RunMeta("run-x", spec.as_of, "v1"))
    assert conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0] == 0


def test_a_delivery_whose_orders_are_not_in_the_staging_tables_of_the_date_is_refused(monthly):
    conn, registered, cfg, folder, plan, entries = monthly
    january = load_next(conn, folder, plan, entries, cfg, 1)
    rebuild_at(conn, cfg.deliveries.launch_date, cfg)                  # the staging tables go back to the launch: January is not in them
    with pytest.raises(ScoreError, match="rebuild_at"):
        score(conn, registered, january)
    assert conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0] == 0


def test_the_scoring_module_reads_only_the_orders_and_never_the_labels():
    source = (Path(__file__).resolve().parents[2] / "src" / "latedel" / "scoring.py").read_text(encoding="utf-8")
    imported = {n.module for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("latedel")}
    assert imported == {"latedel.features", "latedel.model", "latedel.warehouse"}, imported
    assert "labelled_orders" not in source and "baseline" not in source


def test_the_frozen_model_is_the_one_in_every_run(monthly, fitted_launch):
    conn, registered, cfg, folder, plan, entries = monthly
    for index in (1, 2):
        spec = load_next(conn, folder, plan, entries, cfg, index)
        result = fit_model(conn, cfg, run_key=f"run-{spec.delivery_id}", as_of=spec.as_of, source_sha256="ab" * 32)
        assert result["mode"] == "reuse" and result["artifact_sha256"] == registered.artifact_sha256 == fitted_launch.result["artifact_sha256"]
        assert score(conn, registered, spec).scored > 0


# --- the real file (real_data): the first months with the real model ---------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_model_scores_the_first_two_months_once_each_and_never_again(real_fitted):
    config = real_fitted.cfg
    target = real_fitted.folder / "scored.sqlite"
    shutil.copyfile(real_fitted.db, target)
    conn = connect(target)
    registered = load_registered_model(conn, config)
    before = file_digest(config.paths.seal)
    snapshots = []
    for index in (1, 2):
        spec = real_fitted.plan[index]
        load_delivery(conn, read_delivery(real_fitted.deliveries, real_fitted.entries[index]), spec, f"real-{spec.delivery_id}", config, sha256_json=real_fitted.entries[index].sha256_json)
        result = score(conn, registered, spec, run_key=f"real-{spec.delivery_id}")
        expected = conn.execute("SELECT COUNT(*) FROM raw_orders WHERE delivery_id = ?", (spec.delivery_id,)).fetchone()[0]
        print("REAL scored", spec.delivery_id, "orders", result.scored, "alerts", result.alerts, "share", round(result.alerts / result.scored, 3), "threshold", result.threshold)
        assert result.scored == expected > 1_000 and result.already_scored == 0 and 0 < result.alerts < result.scored
        snapshots.append(conn.execute(SCORES).fetchall())
    assert [r for r in snapshots[1] if r[0] in {x[0] for x in snapshots[0]}] == snapshots[0], "the scores of the first month are what they were after the second"
    rows = snapshots[1]
    assert all(0 <= r[4] <= 1 and r[5] == registered.threshold and r[3] == "v1" for r in rows)
    assert conn.execute("SELECT MIN(order_date) FROM raw_orders WHERE order_id IN (SELECT order_id FROM model_scores)").fetchone()[0] >= config.split.test_start.isoformat()
    assert file_digest(config.paths.seal) == before, "scoring does not touch the seal"
    conn.close()
