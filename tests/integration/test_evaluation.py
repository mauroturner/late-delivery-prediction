"""The evaluation on the warehouse of the synthetic fixture (U15, AC20, AC23, decisions D16-D18).

``history`` is the fixture's whole story: the launch, the 13 monthly deliveries and the close, loaded in order, the monthly ones scored by the frozen model when
they were new. The tests evaluate it at different calculation dates on private copies and compare what the evaluation says with figures recomputed here from the raw
tables with scikit-learn, never with the module under test. Nothing here reads the test block of the real file (D17).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import brier_score_loss, roc_auc_score

from latedel.baselines import baseline_rates
from latedel.evaluation import (
    EvaluationError,
    Criterion,
    block_frame,
    evaluate,
    evaluate_block,
    evaluate_criteria,
    load_criteria,
    load_metrics,
    record_launch_tables,
    registered_baseline_rates,
    store_evaluation,
)
from latedel.features import FEATURE_SETS
from latedel.launch import fit_model, load_registered_model
from latedel.registry import SealMismatchError, read_seal, valid_v2_scores_fingerprint
from latedel.schema import LEAKING_COLUMNS
from latedel.split import labelled_orders
from latedel.warehouse import connect
from tests.conftest import copy_of_history

SOURCE = "ab" * 32
EVAL_TABLES = ("eval_metrics", "eval_bootstrap", "eval_criteria", "eval_sensitivity", "eval_roc", "eval_calibration")


def run_evaluate(conn, cfg, as_of: date, *, run_key: str | None = None):
    config_sha256 = fit_model(conn, cfg, run_key=run_key or f"run-{as_of}", as_of=as_of, source_sha256=SOURCE)["config_sha256"]
    return evaluate(conn, cfg, run_key=run_key or f"run-{as_of}", as_of=as_of, config_sha256=config_sha256)


@pytest.fixture(scope="module")
def evaluated(history, tmp_path_factory):
    """A copy of the history where the test was evaluated once, at 2017-10-31 (the first date its labels are all known): ``(folder, cfg, outcome)``. The tests that
    start with that first evaluation copy it (``copy_evaluated``) instead of evaluating again; the outcome is the one that first evaluation returned."""
    folder = tmp_path_factory.mktemp("evaluated")
    db, cfg = copy_of_history(history.cfg, history.folder / "launch", history.db, folder)
    conn = connect(db)
    outcome = run_evaluate(conn, cfg, date(2017, 10, 31))
    conn.close()
    return SimpleNamespace(folder=folder, cfg=cfg, outcome=outcome)


@pytest.fixture()
def copy_evaluated(evaluated, history, tmp_path):
    """``(conn, cfg, outcome)`` on a private copy of ``evaluated``: the warehouse and the seal right after the first evaluation of the test, and what it returned."""
    db, cfg = copy_of_history(history.cfg, evaluated.folder / "launch", evaluated.folder / "w.sqlite", tmp_path / "copy")
    assert cfg.evaluation == evaluated.cfg.evaluation and cfg.costs == evaluated.cfg.costs
    conn = connect(db)
    yield conn, cfg, evaluated.outcome
    conn.close()


def eval_rows(conn: sqlite3.Connection) -> dict[str, list]:
    return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in EVAL_TABLES}


def independent_block(conn, cfg, first: date, last: date, as_of: date) -> pd.DataFrame:
    """The labelled orders of a block with the recorded risk and the registered B2 and B5 rates, from the raw tables and ``model_scores`` with plain SQL."""
    rows = conn.execute(
        "SELECT o.order_id, o.shipping_mode, o.scheduled_days, o.order_ts, r.real_days, s.risk FROM raw_orders AS o JOIN raw_outcomes AS r ON r.order_id = o.order_id "
        "JOIN model_scores AS s ON s.order_id = o.order_id WHERE r.is_cancelled = 0 AND o.order_date >= ? AND o.order_date <= ? AND r.outcome_known_date <= ? ORDER BY o.order_id",
        (first.isoformat(), last.isoformat(), as_of.isoformat()),
    ).fetchall()
    frame = pd.DataFrame(rows, columns=["order_id", "mode", "scheduled", "ts", "real", "risk"])
    frame["late"] = (frame["real"] > frame["scheduled"]).astype(int)
    b2 = dict(conn.execute("SELECT cell, rate FROM baseline_rates WHERE baseline = 'b2_mode'").fetchall())
    b5 = dict(conn.execute("SELECT cell, rate FROM baseline_rates WHERE baseline = 'b5_mode_noon'").fetchall())
    frame["b2"] = frame["mode"].map(b2)
    band = np.where(frame["ts"].str.slice(11, 13).astype(int) < cfg.features.noon_hour, "before", "from")
    frame["b5"] = [b5[f"{m}|{b}"] for m, b in zip(frame["mode"], band, strict=True)]
    return frame


def test_the_fixture_history_has_a_scored_test_block_and_a_post_break_block(history):
    from latedel.warehouse import connect

    conn = connect(history.db)
    cfg = history.cfg
    test = independent_block(conn, cfg, cfg.split.test_start, cfg.split.test_end, date(2018, 2, 7))
    post = independent_block(conn, cfg, cfg.split.post_break_start, cfg.split.post_break_end, date(2018, 2, 7))
    assert len(test) > 400 and 0.2 < test["late"].mean() < 0.9 and len(post) > 100
    assert conn.execute("SELECT COUNT(*) FROM eval_metrics").fetchone()[0] == 0, "the fixture of the history evaluates nothing"
    conn.close()


def test_before_the_end_of_the_test_only_v2_is_evaluated_and_the_summary_has_no_criteria(copy_history):
    conn, cfg = copy_history()
    outcome = run_evaluate(conn, cfg, date(2017, 10, 6))                    # a day before the test block's end plus the purge
    assert outcome.test is None and outcome.test_criteria == [] and outcome.test_seal is None and outcome.post_break is None and outcome.label == "sealed"
    assert read_seal(cfg.paths.seal).get("test") is None
    assert conn.execute("SELECT COUNT(*) FROM eval_criteria").fetchone()[0] == 0
    scopes = {r[0] for r in conn.execute("SELECT DISTINCT scope FROM eval_metrics")}
    assert scopes == {"valid_v2"} and {r[0] for r in conn.execute("SELECT DISTINCT scope FROM eval_sensitivity")} == {"valid_v2"}
    methods = {r[0] for r in conn.execute("SELECT DISTINCT method FROM eval_metrics")}
    assert methods == {"model", "b0_all_late", "b1_first_class", "b2_mode", "b3_scheduled_le2", "b4_standard_only", "b5_mode_noon"}
    # the figures of V2 are the ones that were sealed at the launch: the fingerprint of the risks of V2 is the sealed one
    registered = load_registered_model(conn, cfg)
    rates = registered_baseline_rates(conn, cfg)
    v2 = block_frame(conn, "valid_v2", cfg, as_of=date(2017, 10, 6), registered=registered, rates=rates)
    assert valid_v2_scores_fingerprint(v2.assign(risk=v2["risk"])) == read_seal(cfg.paths.seal)["launch"]["valid_v2_scores_sha256"]
    # and the test cannot be measured while its labels are not all known: on the last day of the block some of its orders have no outcome yet
    with pytest.raises(EvaluationError, match="no está completo"):
        evaluate_block(conn, "test", cfg, as_of=date(2017, 9, 30))
    with pytest.raises(ValueError, match="se evalúa uno de"):
        evaluate_block(conn, "train", cfg, as_of=date(2017, 10, 6))


def test_registered_rates_are_the_training_rates_and_never_a_later_label(copy_history):
    conn, cfg = copy_history()
    registered = registered_baseline_rates(conn, cfg)
    fitted = baseline_rates(labelled_orders(conn, cfg.deliveries.launch_date, cfg), cfg)
    assert registered.b2.rates == fitted.b2.rates and registered.b5.rates == fitted.b5.rates
    assert registered.b2.orders == fitted.b2.orders and registered.train_orders == fitted.train_orders
    assert registered.train_late_rate == pytest.approx(fitted.train_late_rate)
    # the rates come from the table: with the labels of every block changed, the registered rates are the same
    conn.execute("UPDATE raw_outcomes SET real_days = real_days + 5")
    assert registered_baseline_rates(conn, cfg).b5.rates == registered.b5.rates
    conn.execute("DELETE FROM baseline_rates")
    with pytest.raises(EvaluationError, match="no hay tasas"):
        registered_baseline_rates(conn, cfg)


def test_the_first_evaluation_of_the_test_writes_the_sealed_rows_and_the_part_of_the_seal(copy_evaluated):
    conn, cfg, outcome = copy_evaluated                                         # right after run_evaluate(conn, cfg, date(2017, 10, 31))
    assert outcome.test is not None and outcome.test_seal == "written" and outcome.label == "sealed" and outcome.post_break is None
    ids = [c.criterion_id for c in outcome.test_criteria]
    assert ids == [f"C0{i}" for i in range(1, 10)]
    sealed = read_seal(cfg.paths.seal)
    assert [e["id"] for e in sealed["test"]["criteria"]] == ids and sealed["test"]["as_of"] == "2017-10-31"
    assert sealed["test"]["config_sha256"] == sealed["launch"]["config_sha256"] and set(sealed["test"]["metrics"]) == {"model", "b2_mode", "b5_mode_noon"}

    # figures recomputed from the raw tables, with scikit-learn and the rows of baseline_rates
    frame = independent_block(conn, cfg, cfg.split.test_start, cfg.split.test_end, date(2017, 10, 31))
    stored = load_metrics(conn, "sealed")["test"]
    assert stored["model"]["orders"] == len(frame) == outcome.test.orders
    for name, column in (("model", "risk"), ("b2_mode", "b2"), ("b5_mode_noon", "b5")):
        assert stored[name]["auc"] == pytest.approx(roc_auc_score(frame["late"], frame[column]), abs=1e-12), name
        assert stored[name]["brier"] == pytest.approx(brier_score_loss(frame["late"], frame[column]), abs=1e-12), name
    threshold = stored["model"]["threshold"]
    flagged = frame["risk"] >= threshold
    assert stored["model"]["flagged_share"] == pytest.approx(flagged.mean())
    assert stored["model"]["nb_total"] == pytest.approx(frame["late"][flagged].sum() * 20 * 0.5 - flagged.sum() * 5)
    b2_flagged = frame["b2"] >= 0.5
    assert stored["b2_mode"]["nb_total"] == pytest.approx(frame["late"][b2_flagged].sum() * 10 - b2_flagged.sum() * 5) and stored["b2_mode"]["threshold"] == 0.5
    # the criteria the stored numbers decide
    by_id = {c.criterion_id: c for c in load_criteria(conn, "sealed")}
    assert by_id["C01"].passed == (stored["model"]["auc"] > stored["b2_mode"]["auc"]) and by_id["C07"].passed == (stored["model"]["brier"] < stored["b2_mode"]["brier"])
    k = int(b2_flagged.sum())
    top = frame.sort_values(["risk", "ts", "order_id"], ascending=[False, True, True]).head(k)
    assert by_id["C08"].value == top["late"].sum() and by_id["C08"].baseline_value == frame["late"][b2_flagged].sum()
    assert [c.passed for c in outcome.test_criteria] == [by_id[i].passed for i in ids]
    assert {r[0] for r in conn.execute("SELECT DISTINCT seal_state FROM eval_criteria")} == {"sealed"}


def test_the_test_is_evaluated_once_and_later_runs_leave_it_alone(copy_evaluated):
    conn, cfg, _first = copy_evaluated                                          # evaluated at 2017-10-31
    before, seal = eval_rows(conn), cfg.paths.seal.read_bytes()
    later = run_evaluate(conn, cfg, date(2017, 11, 30))
    assert later.test is None and later.test_seal is None and later.test_criteria == []
    assert cfg.paths.seal.read_bytes() == seal
    after = eval_rows(conn)
    assert after["eval_criteria"] == before["eval_criteria"] and after["eval_bootstrap"] == before["eval_bootstrap"]
    assert [r for r in after["eval_metrics"] if r[0] == "test"] == [r for r in before["eval_metrics"] if r[0] == "test"]


def test_a_rebuild_from_scratch_with_the_sealed_configuration_reproduces_the_seal_and_a_different_value_fails(copy_evaluated):
    conn, cfg, _first = copy_evaluated                                          # evaluated at 2017-10-31
    before, seal = eval_rows(conn), cfg.paths.seal.read_bytes()
    for table in EVAL_TABLES:
        conn.execute(f"DELETE FROM {table}")
    again = run_evaluate(conn, cfg, date(2017, 10, 31), run_key="run-rebuild")
    assert again.test_seal == "unchanged" and again.label == "sealed"
    assert cfg.paths.seal.read_bytes() == seal, "the seal is not rewritten"
    assert eval_rows(conn) == before, "the rebuilt rows are the sealed ones"

    # a seal whose value was altered by hand is not reproduced: the run fails, the seal keeps what it says and nothing of the test is replaced
    document = json.loads(seal)
    document["test"]["metrics"]["model"]["auc"] += 0.01
    cfg.paths.seal.write_text(json.dumps(document, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    tampered = cfg.paths.seal.read_bytes()
    for table in EVAL_TABLES:
        conn.execute(f"DELETE FROM {table}")
    with pytest.raises(SealMismatchError, match="R7"):
        run_evaluate(conn, cfg, date(2017, 10, 31), run_key="run-bad")
    assert cfg.paths.seal.read_bytes() == tampered
    assert conn.execute("SELECT COUNT(*) FROM eval_criteria").fetchone()[0] == 0


def test_other_costs_after_the_seal_are_stored_apart_and_never_replace_the_sealed_result(copy_evaluated):
    conn, cfg, _first = copy_evaluated                                          # evaluated at 2017-10-31
    sealed_rows, seal = eval_rows(conn), cfg.paths.seal.read_bytes()

    other = replace(cfg, costs=replace(cfg.costs, alert_cost=9.0))               # another break-even: 0.9 instead of 0.5
    outcome = run_evaluate(conn, other, date(2017, 10, 31), run_key="run-other")
    assert outcome.label == "post_seal" and outcome.test_seal == "post_seal" and outcome.test is not None
    assert cfg.paths.seal.read_bytes() == seal, "the seal does not change"
    now = eval_rows(conn)
    for table in ("eval_metrics", "eval_criteria", "eval_roc", "eval_calibration", "eval_sensitivity"):
        kept = [r for r in now[table] if "sealed" in r and "post_seal" not in r]
        assert kept == [r for r in sealed_rows[table] if "sealed" in r and "post_seal" not in r], table
    assert any("post_seal" in r for r in now["eval_criteria"]) and any("post_seal" in r for r in now["eval_metrics"] if r[0] == "test")
    assert {c.criterion_id for c in load_criteria(conn, "post_seal")} == {f"C0{i}" for i in range(1, 10)}
    sealed_net = {(r[1], r[2]): r[4] for r in now["eval_metrics"] if r[0] == "test" and r[3] == "sealed" and r[2] == "nb_total"}
    other_net = {(r[1], r[2]): r[4] for r in now["eval_metrics"] if r[0] == "test" and r[3] == "post_seal" and r[2] == "nb_total"}
    assert sealed_net[("b2_mode", "nb_total")] != other_net[("b2_mode", "nb_total")], "other costs, other net benefit"
    # the same threshold of the model is used (the registered one), and a repetition of the other configuration replaces only its own rows
    assert load_metrics(conn, "post_seal")["test"]["model"]["threshold"] == load_metrics(conn, "sealed")["test"]["model"]["threshold"]
    run_evaluate(conn, other, date(2017, 11, 30), run_key="run-other-2")
    assert [r for r in eval_rows(conn)["eval_criteria"] if "post_seal" not in r] == [r for r in sealed_rows["eval_criteria"] if "post_seal" not in r]


def test_the_sealed_rows_of_the_test_are_written_once_a_repetition_must_reproduce_them(copy_evaluated):
    conn, cfg, outcome = copy_evaluated                                         # right after run_evaluate(conn, cfg, date(2017, 10, 31))
    before = eval_rows(conn)
    store_evaluation(conn, outcome.test, "sealed")                              # the same values: nothing changes, nothing fails
    assert eval_rows(conn) == before
    changed = replace(outcome.test, metrics={**outcome.test.metrics, "model": {**outcome.test.metrics["model"], "auc": 0.123456}})
    with pytest.raises(SealMismatchError, match="R7"):
        store_evaluation(conn, changed, "sealed")
    assert eval_rows(conn) == before, "a rejected rewrite leaves every row as it was"
    store_evaluation(conn, changed, "post_seal")                                # the same values under the other label are another analysis
    assert load_metrics(conn, "post_seal")["test"]["model"]["auc"] == 0.123456 and load_metrics(conn, "sealed")["test"]["model"]["auc"] != 0.123456
    with pytest.raises(ValueError, match="marca del sello"):
        store_evaluation(conn, outcome.test, "maybe")
    with pytest.raises(ValueError, match="marca del sello"):
        from latedel.evaluation import store_criteria

        store_criteria(conn, [], "maybe")


def test_an_order_of_the_block_without_a_score_is_refused_not_dropped(copy_history):
    conn, cfg = copy_history()
    missing = conn.execute("SELECT order_id FROM model_scores WHERE order_id IN (SELECT order_id FROM raw_orders WHERE order_date >= ? LIMIT 5) LIMIT 1", (cfg.split.test_start.isoformat(),)).fetchone()[0]
    conn.execute("DROP TRIGGER model_scores_no_delete")
    conn.execute("DELETE FROM model_scores WHERE order_id = ?", (missing,))
    with pytest.raises(EvaluationError, match="no tienen puntaje"):
        evaluate_block(conn, "test", cfg, as_of=date(2017, 10, 31))


def test_the_evaluation_of_a_date_does_not_depend_on_what_happens_later(copy_history):
    """The same date on the whole history and on the warehouse as it was right after that delivery: every figure is identical."""
    whole, cfg = copy_history()
    then, cfg_then = copy_history("M2017-10")
    a = evaluate_block(whole, "test", cfg, as_of=date(2017, 10, 31))
    b = evaluate_block(then, "test", cfg_then, as_of=date(2017, 10, 31))
    assert a.metrics == b.metrics and a.bootstrap == b.bootstrap and a.thresholds == b.thresholds
    pd.testing.assert_frame_equal(a.roc, b.roc)
    pd.testing.assert_frame_equal(a.calibration, b.calibration)
    pd.testing.assert_frame_equal(a.sensitivity, b.sensitivity)
    # and the control: the same block read one month later than its end sees the same labels, so a later date changes nothing either
    c = evaluate_block(whole, "test", cfg, as_of=date(2018, 2, 7))
    assert c.metrics == a.metrics


def test_the_post_break_block_is_evaluated_at_the_close_with_an_informative_c10(copy_history):
    conn, cfg = copy_history()
    outcome = run_evaluate(conn, cfg, date(2018, 2, 7))
    assert outcome.post_break is not None and outcome.post_break_criterion is not None and outcome.test is not None
    c10 = outcome.post_break_criterion
    assert c10.criterion_id == "C10" and c10.passed is None
    stored = {c.criterion_id: c for c in load_criteria(conn, "sealed")}
    assert sorted(stored) == [f"C{i:02d}" for i in range(1, 11)] and stored["C10"].passed is None and stored["C10"].verdict_es == c10.verdict_es
    frame = independent_block(conn, cfg, cfg.split.post_break_start, cfg.split.post_break_end, date(2018, 2, 7))
    assert load_metrics(conn, "sealed")["post_break"]["model"]["auc"] == pytest.approx(roc_auc_score(frame["late"], frame["risk"]), abs=1e-12)
    assert read_seal(cfg.paths.seal)["test"]["as_of"] == "2018-02-07"
    assert not any(c["id"] == "C10" for c in read_seal(cfg.paths.seal)["test"]["criteria"]), "the seal holds the test, not the informative block"


def test_the_importance_and_the_demonstration_of_the_leakage_are_stored_at_the_launch(copy_history):
    conn, cfg = copy_history()
    launch = cfg.deliveries.launch_date
    counts = record_launch_tables(conn, cfg, as_of=launch)
    registered = load_registered_model(conn, cfg)
    features = FEATURE_SETS[registered.model.feature_set]
    assert counts == {"feature_importance": len(features), "eval_leakage": 1 + len(LEAKING_COLUMNS) - 1}
    importance = conn.execute("SELECT feature, feature_es, auc_drop, sort_order FROM feature_importance ORDER BY sort_order").fetchall()
    assert {r[0] for r in importance} == set(features) and [r[3] for r in importance] == list(range(1, len(features) + 1))
    assert [r[2] for r in importance] == sorted((r[2] for r in importance), reverse=True) and all(r[1] for r in importance)
    leak = {r[0]: r for r in conn.execute("SELECT source_column, kind, auc_with_mode_valid, sort_order FROM eval_leakage")}
    assert leak["shipping_mode"][1] == "reference" and set(leak) == {"shipping_mode", *LEAKING_COLUMNS} - {"Late_delivery_risk"}
    assert leak["Days for shipping (real)"][2] > leak["shipping_mode"][2] + 0.1, "the real days give the answer away"
    again = record_launch_tables(conn, cfg, as_of=launch)
    assert again == counts and conn.execute("SELECT COUNT(*) FROM eval_leakage").fetchone()[0] == counts["eval_leakage"], "a repetition replaces the rows"
    with pytest.raises(EvaluationError):
        record_launch_tables(conn, cfg, as_of=date(2015, 3, 1))                  # nothing of V2 is known on that date


def test_criteria_are_decided_from_the_result_of_the_test_and_a_stored_criterion_reads_back_the_same(copy_evaluated):
    conn, cfg, outcome = copy_evaluated                                         # right after run_evaluate(conn, cfg, date(2017, 10, 31))
    again = evaluate_criteria(outcome.test, cfg)
    assert again == outcome.test_criteria and all(isinstance(c, Criterion) for c in again)
    assert load_criteria(conn, "sealed") == outcome.test_criteria
    for c in outcome.test_criteria:
        assert ("no supera" in c.verdict_es) == (not c.passed), c.verdict_es


# --- the summary that reads the stored evaluation ------------------------------------------------------------------------------------------------------


def test_the_summary_reads_the_stored_evaluation_and_every_verdict_agrees_with_its_number(copy_history, tmp_path):
    from latedel.evaluation import es
    from latedel.reporting import RESULTS_END, RESULTS_START, load_summary, read_block, sync_results, write_summary

    conn, cfg = copy_history()
    run_evaluate(conn, cfg, date(2017, 10, 6))
    pending = load_summary(conn, cfg, run_key="run-pending", as_of=date(2017, 10, 6))
    assert pending.criteria == [] and not pending.test_evaluated and set(pending.metrics) == {"valid_v2"}
    _json, pending_md = write_summary(pending, tmp_path / "pending")
    assert "La prueba se evalúa con la entrega de 2017-10" in pending_md.read_text(encoding="utf-8")

    run_evaluate(conn, cfg, date(2017, 10, 31))
    first = load_summary(conn, cfg, run_key="run-M2017-10", as_of=date(2017, 10, 31))
    assert first.test_evaluated and [c.criterion_id for c in first.criteria] == [f"C{i:02d}" for i in range(1, 10)] and first.post_seal_criteria == []
    test = first.metrics["test"]
    _json, md_path = write_summary(first, tmp_path / "reports")
    text = md_path.read_text(encoding="utf-8")
    by_id = {c.criterion_id: c for c in first.criteria}
    for c in first.criteria:
        assert ("no supera" in c.verdict_es) == (c.passed is False), c.verdict_es
    assert es(test["model"]["auc"]) in by_id["C01"].verdict_es and es(test["b2_mode"]["auc"]) in by_id["C01"].verdict_es
    assert es(test["model"]["brier"], 4) in by_id["C07"].verdict_es and es(test["b2_mode"]["brier"], 4) in by_id["C07"].verdict_es
    assert es(test["model"]["ece"]) in by_id["C06"].verdict_es
    failed = [c.criterion_id for c in first.criteria if c.passed is False]
    assert all(f"| {i} |" in text for i in by_id)
    assert ("no supera" in text.splitlines()[2]) == bool(failed), "the headline names the criteria that fail"
    readme = tmp_path / "README.md"
    readme.write_text(f"# P\n\n{RESULTS_START}\n{RESULTS_END}\n", encoding="utf-8", newline="")
    sync_results(readme, md_path)
    assert read_block(readme) == text.strip("\n")

    # a later run leaves the sealed text alone: the summary is read from the tables, not computed again
    run_evaluate(conn, cfg, date(2017, 11, 30))
    later = load_summary(conn, cfg, run_key="run-M2017-11", as_of=date(2017, 11, 30))
    assert later.criteria == first.criteria and later.metrics["test"] == first.metrics["test"]


# --- B3 of the evaluation of eef9bea: after the seal, a warehouse with no sealed rows (decision D88) -------------------------------------------------------


def criteria_table(text: str) -> list[str]:
    return [line for line in text.splitlines() if re.match(r"\| C\d\d \|", line)]


def rounded(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: rounded(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [rounded(v) for v in value]
    return value


def test_after_the_seal_a_warehouse_without_sealed_rows_shows_the_sealed_result_from_the_seal_and_the_post_seal_analysis_below(copy_evaluated, tmp_path):
    """B3: after the seal, a replay from scratch with other costs (the README's own path for another cost) kept the seal, but its summary said «La prueba se evalúa con
    la entrega de 2017-10: hasta entonces los criterios C01 a C10 quedan pendientes», with ``state: pending`` and no criteria: the warehouse has no sealed rows, only
    post-seal ones. The summary takes the sealed criteria and metrics from the seal, with the costs that were sealed, and shows the post-seal analysis below them."""
    from latedel.reporting import RESULTS_END, RESULTS_START, load_summary, read_block, summary_json, sync_results, write_summary

    conn, cfg, _first = copy_evaluated                                          # the test sealed at 2017-10-31, its rows in this warehouse
    sealed = load_summary(conn, cfg, run_key="run-sealed", as_of=date(2017, 10, 31))
    sealed_text = write_summary(sealed, tmp_path / "sealed")[1].read_text(encoding="utf-8")
    seal = cfg.paths.seal.read_bytes()
    for table in EVAL_TABLES:                                                   # a warehouse rebuilt from scratch: no evaluation stored yet
        conn.execute(f"DELETE FROM {table}")
    other = replace(cfg, costs=replace(cfg.costs, alert_cost=9.0))
    assert run_evaluate(conn, other, date(2017, 10, 31), run_key="run-other").test_seal == "post_seal"
    assert load_criteria(conn, "sealed") == [] and cfg.paths.seal.read_bytes() == seal

    summary = load_summary(conn, other, run_key="run-other", as_of=date(2017, 10, 31))
    in_the_seal = ("model", "b2_mode", "b5_mode_noon")                           # the seal keeps the metrics of the three methods of the summary's tables
    assert summary.test_evaluated and rounded(summary.criteria) == rounded(sealed.criteria) and set(summary.metrics) == {"test"}
    assert rounded(summary.metrics["test"]) == rounded({m: sealed.metrics["test"][m] for m in in_the_seal})
    assert summary.costs == sealed.costs == {"alert_cost": 5.0, "late_cost": 20.0, "effect": 0.5}   # the sealed costs, not this run's
    assert [c.criterion_id for c in summary.post_seal_criteria] == [f"C0{i}" for i in range(1, 10)]
    document = summary_json(summary)
    assert document["state"] == "evaluated" and document["pending_delivery"] is None and document["failed"] == summary_json(sealed)["failed"]
    assert rounded(document["criteria"]) == rounded(summary_json(sealed)["criteria"]) and document["post_seal"]["costs"]["alert_cost"] == 9.0
    json_path, md_path = write_summary(summary, tmp_path / "reports")
    text = md_path.read_text(encoding="utf-8")
    assert "quedan pendientes" not in text and text.splitlines()[2] == sealed_text.splitlines()[2]   # the same headline: «El modelo supera … y no supera …»
    assert criteria_table(text)[:9] == criteria_table(sealed_text)                                    # the sealed rows first, as the sealed run wrote them
    assert "model_seal.json" in text and text.index("Posterior al sello") > text.index("| C09 |") and "alerta 9,0" in text
    assert json.loads(json_path.read_text(encoding="utf-8"))["state"] == "evaluated"
    readme = tmp_path / "README.md"                                             # docs-sync copies the same block into the README
    readme.write_text(f"# P\n\n{RESULTS_START}\n{RESULTS_END}\n", encoding="utf-8", newline="")
    sync_results(readme, md_path)
    assert read_block(readme) == text.strip("\n")


def test_the_sealed_criteria_rebuilt_from_the_project_s_seal_are_the_rows_of_its_versioned_summary(cfg, tmp_path):
    """The rebuild from the seal writes, for the real seal of U25, the same criteria rows and the same table of the test that ``reports/evaluation_summary.md`` has (it
    reads the versioned files and writes only under the test's folder)."""
    from latedel.reporting import load_summary, write_summary
    from latedel.warehouse import apply_schema

    versioned = (cfg.project_root / "reports" / "evaluation_summary.md").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    apply_schema(conn)
    summary = load_summary(conn, cfg, run_key="run-rebuilt", as_of=date(2018, 2, 7))
    conn.close()
    text = write_summary(summary, tmp_path)[1].read_text(encoding="utf-8")
    assert summary.test_evaluated and [c.criterion_id for c in summary.criteria] == [f"C0{i}" for i in range(1, 10)]
    assert criteria_table(text) == criteria_table(versioned)[:9]
    test_table = text[text.index("**Prueba"):].split("\n\n")[1]
    assert test_table in versioned and text.splitlines()[2] == versioned.splitlines()[2]


# --- B4 of the evaluation of eef9bea: a rebuild with a key outside the fingerprint changed (decision D89) ------------------------------------------------------


@pytest.mark.parametrize("change", [{"costs": {"sensitivity_step": 0.1}}, {"evaluation": {"bootstrap_seed": 1}}], ids=["sensitivity_step", "bootstrap_seed"])
def test_a_rebuild_with_a_key_outside_the_fingerprint_changed_is_post_seal_and_finishes(copy_evaluated, change):
    """B4, the evaluator's repro on the fixture: a replay from scratch with ``[costs] sensitivity_step = 0.1`` or ``[evaluation] bootstrap_seed = 1`` failed at the
    first evaluation of the test with ``SealMismatchError``, so the post-seal analysis could not finish. It finishes as post-seal: every row of the run under
    ``post_seal`` (V2 included), no sealed row, and the seal byte for byte as it was."""
    conn, cfg, _first = copy_evaluated
    seal = cfg.paths.seal.read_bytes()
    for table in EVAL_TABLES:
        conn.execute(f"DELETE FROM {table}")
    other = cfg
    for section, values in change.items():
        other = replace(other, **{section: replace(getattr(other, section), **values)})
    outcome = run_evaluate(conn, other, date(2017, 10, 31), run_key="run-other")
    assert outcome.test_seal == "post_seal" and outcome.label == "post_seal" and outcome.test is not None
    assert cfg.paths.seal.read_bytes() == seal
    assert load_criteria(conn, "sealed") == [] and conn.execute("SELECT COUNT(*) FROM eval_metrics WHERE seal_state = 'sealed'").fetchone()[0] == 0
    assert {c.criterion_id for c in load_criteria(conn, "post_seal")} == {f"C0{i}" for i in range(1, 10)}
