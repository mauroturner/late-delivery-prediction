"""The model on the warehouse (U12, AC17, AC18, AC21 in part): the positive and negative controls, determinism, the refusals and the real launch.

The controls are synthetic sources of 12,000 orders: one with an effect planted in a column the order-time features can see (Standard Class orders of the
LATAM market are late more often) and one with none. The model is trained at the launch, like the product, and compared with B5 on the test block of the
*fixture*; nothing here reads the test block of the real file (D17).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from latedel.baselines import baseline_rates
from latedel.features import LeakageError, build_features, read_order_frames
from latedel.model import Candidate, importance, make_pipeline, predict_risk, train_model
from latedel.split import StaleMartError, blocks, labelled_orders
from latedel.warehouse import connect, rebuild_at
from tests.fixtures.model_helpers import fixture_test_block, paired_auc_interval, small_cfg


def evaluate_control(control) -> dict:
    """Train at the launch, then score the test block of the fixture with the model and with B5, and take the paired intervals once."""
    cfg = control.cfg
    launch = connect(control.launch_db)
    trained = train_model(launch, cfg.deliveries.launch_date, cfg)
    rates = baseline_rates(labelled_orders(launch, cfg.deliveries.launch_date, cfg), cfg)
    launch.close()
    test, x = fixture_test_block(control, trained.model.feature_set)
    scores = rates.scores(test)
    risk, y = predict_risk(trained.model, x), test["is_late"].to_numpy()
    return {
        "trained": trained, "risk": risk, "y": y, "b5": scores["b5_mode_noon"], "b2": scores["b2_mode"], "test_orders": len(test),
        "vs_b5": paired_auc_interval(y, risk, scores["b5_mode_noon"]), "vs_b2": paired_auc_interval(y, risk, scores["b2_mode"]),
    }


@pytest.fixture(scope="module")
def controls(control_run):
    return {signal: evaluate_control(control_run(signal)) for signal in ("standard_market", "none")}


def test_positive_control_beats_b5_and_negative_control_does_not(controls):
    positive, negative = controls["standard_market"], controls["none"]
    for result in (positive, negative):
        assert result["test_orders"] > 2500 and result["trained"].model.feature_set == "f2"
        assert result["trained"].selection.auc_v1 < 0.95, "the tripwire stays quiet on honest data"
    diff, low, high = positive["vs_b5"]
    assert diff > 0 and low > 0, f"the planted effect is found: the model beats B5, the interval is ({low:.4f}, {high:.4f})"
    diff0, low0, high0 = negative["vs_b5"]
    assert low0 < 0 < high0, f"with no signal the interval contains 0: ({low0:.4f}, {high0:.4f})"
    print("CONTROLS positive", round(diff, 4), (round(low, 4), round(high, 4)), "negative", round(diff0, 4), (round(low0, 4), round(high0, 4)),
          "auc positive", round(roc_auc_score(positive["y"], positive["risk"]), 4), round(roc_auc_score(positive["y"], positive["b5"]), 4),
          "negative", round(roc_auc_score(negative["y"], negative["risk"]), 4), round(roc_auc_score(negative["y"], negative["b5"]), 4))


def test_the_model_beats_the_rule_by_mode_in_both_controls_because_it_finds_the_noon_cut(controls):
    for signal in ("standard_market", "none"):
        diff, low, _high = controls[signal]["vs_b2"]
        assert diff > 0.02 and low > 0, signal


def test_the_planted_effect_shows_in_the_importance_and_the_absent_one_does_not(controls, control_run):
    def table(signal):
        result = controls[signal]
        return importance(result["trained"].model, result["trained"].x_v2, result["trained"].valid_v2["is_late"], control_run(signal).cfg).set_index("feature")["auc_drop"]

    positive, negative = table("standard_market"), table("none")
    assert positive["market"] > 0.005 > negative["market"], (positive["market"], negative["market"])
    assert positive["market"] > 4 * negative["market"]
    assert negative["order_hour"] > 0.01, "the noon cut is there in both"


def test_two_trainings_choose_the_same_configuration_and_give_identical_probabilities(control_run):
    control = control_run("standard_market")
    runs = []
    for _ in range(2):
        conn = connect(control.launch_db)
        runs.append(train_model(conn, control.cfg.deliveries.launch_date, control.cfg))
        conn.close()
    a, b = runs
    assert a.selection.chosen == b.selection.chosen and a.selection.losses == b.selection.losses
    pd.testing.assert_frame_equal(a.valid_v2, b.valid_v2, check_exact=True)
    np.testing.assert_array_equal(predict_risk(a.model, a.x_v2), predict_risk(b.model, b.x_v2))


# --- the training from the labelled orders ---------------------------------------------------------------------------------------------------------------


def test_the_default_grid_of_24_is_fitted_and_the_best_log_loss_on_v1_is_selected(cfg, launch_conn):
    fast = replace(cfg, model=replace(cfg.model, max_iter=25))
    trained = train_model(launch_conn, fast.deliveries.launch_date, fast)
    losses = trained.selection.losses
    assert len(losses) == 24 and len({c for c, _ in losses}) == 24
    best = min(loss for _c, loss in losses)
    assert trained.selection.log_loss_v1 <= best + 1e-6
    assert trained.model.feature_set == trained.selection.chosen.feature_set and trained.model.candidate == trained.selection.chosen
    assert trained.orders["train"] > trained.orders["valid_v1"] > 0 and trained.orders["valid_v2"] > 0


def test_the_model_is_trained_on_the_training_block_selected_on_v1_and_measured_on_v2(cfg, launch_conn):
    config = small_cfg(cfg)
    trained = train_model(launch_conn, config.deliveries.launch_date, config)
    labelled = labelled_orders(launch_conn, config.deliveries.launch_date, config)
    parts = blocks(labelled)
    assert trained.orders == {"train": len(parts["train"]), "valid_v1": len(parts["valid_v1"]), "valid_v2": len(parts["valid_v2"])}
    assert trained.valid_v2["order_id"].tolist() == parts["valid_v2"]["order_id"].tolist()
    assert trained.valid_v2["is_late"].tolist() == parts["valid_v2"]["is_late"].tolist()
    risk = trained.valid_v2["risk"].to_numpy()
    assert np.isfinite(risk).all() and risk.min() >= 0 and risk.max() <= 1
    assert trained.x_v2.index.tolist() == parts["valid_v2"]["order_id"].tolist()
    assert tuple(trained.x_v2.columns) == tuple(build_features(*read_order_frames(launch_conn), trained.model.feature_set).columns)
    # the calibrator was fitted with V1: its risks for V1 are what isotonic regression of V1 would give, so they differ from the raw scores
    v1_matrix = build_features(*read_order_frames(launch_conn), trained.model.feature_set).loc[parts["valid_v1"]["order_id"]]
    raw = trained.selection.pipeline.predict_proba(v1_matrix)[:, 1]
    calibrated = predict_risk(trained.model, v1_matrix)
    assert not np.array_equal(raw, calibrated)
    assert log_loss(parts["valid_v1"]["is_late"], calibrated, labels=[0, 1]) <= log_loss(parts["valid_v1"]["is_late"], raw, labels=[0, 1]) + 1e-9


def test_a_mart_built_for_another_date_is_refused_before_anything_is_fitted(cfg, launch_conn):
    rebuild_at(launch_conn, date(2016, 12, 31), cfg)
    with pytest.raises(StaleMartError):
        train_model(launch_conn, cfg.deliveries.launch_date, small_cfg(cfg))


def test_a_date_with_a_missing_block_is_refused_and_says_which(cfg, launch_conn):
    rebuild_at(launch_conn, date(2016, 9, 30), cfg)
    with pytest.raises(ValueError, match="valid_v2"):
        train_model(launch_conn, date(2016, 9, 30), small_cfg(cfg))
    rebuild_at(launch_conn, date(2015, 12, 31), cfg)
    with pytest.raises(ValueError, match=r"valid_v1, valid_v2"):
        train_model(launch_conn, date(2015, 12, 31), small_cfg(cfg))


def test_the_tripwire_stops_the_training_before_the_calibration(cfg, launch_conn, monkeypatch):
    from latedel import model as model_module

    config = small_cfg(replace(cfg, criteria=replace(cfg.criteria, leak_tripwire_auc=0.5)))
    calls = []
    real = model_module.calibrate
    monkeypatch.setattr(model_module, "calibrate", lambda *a, **k: calls.append(1) or real(*a, **k))
    with pytest.raises(LeakageError, match="M01"):
        train_model(launch_conn, config.deliveries.launch_date, config)
    assert calls == [], "nothing is calibrated, saved or scored after the tripwire"


def test_a_category_not_seen_in_training_is_missing_not_an_error(cfg, launch_conn):
    orders, lines = read_order_frames(launch_conn)
    x = build_features(orders, lines, "f1")
    labelled = labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    train = blocks(labelled)["train"]
    pipeline = make_pipeline(Candidate("f1", 0.1, 15, 20), cfg.model).fit(x.loc[train["order_id"]], train["is_late"].to_numpy())
    probe = x.iloc[:20].copy()
    unseen = probe.assign(order_country="Atlantis", main_product="A product nobody sold", market="Mars")
    missing = probe.assign(order_country=None, main_product=None, market=None)
    p_unseen, p_missing = pipeline.predict_proba(unseen)[:, 1], pipeline.predict_proba(missing)[:, 1]
    assert np.isfinite(p_unseen).all()
    np.testing.assert_array_equal(p_unseen, p_missing)


# --- the real file (real_data): the launch and its validation ---------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_launch_selection_calibration_and_validation_scores(cfg, real_launch_conn):
    launch = cfg.deliveries.launch_date
    trained = train_model(real_launch_conn, launch, cfg)
    rates = baseline_rates(labelled_orders(real_launch_conn, launch, cfg), cfg)
    chosen = trained.selection.chosen
    losses = {f"{c.feature_set}/{c.learning_rate}/{c.max_leaf_nodes}/{c.min_samples_leaf}": round(v, 5) for c, v in trained.selection.losses}
    v2 = trained.valid_v2
    labelled = labelled_orders(real_launch_conn, launch, cfg)
    v2_orders = blocks(labelled)["valid_v2"]
    scores = rates.scores(v2_orders)
    auc = {"model": roc_auc_score(v2["is_late"], v2["risk"]), "b2": roc_auc_score(v2["is_late"], scores["b2_mode"]), "b5": roc_auc_score(v2["is_late"], scores["b5_mode_noon"])}
    table = importance(trained.model, trained.x_v2, v2["is_late"], cfg)
    print("REAL selection", chosen, "log loss V1", round(trained.selection.log_loss_v1, 5), "AUC V1", round(trained.selection.auc_v1, 4))
    print("REAL all 24 log losses on V1", losses)
    print("REAL V2 orders", len(v2), "AUC", {k: round(v, 4) for k, v in auc.items()}, "log loss", round(log_loss(v2["is_late"], v2["risk"]), 5),
          "Brier", round(brier_score_loss(v2["is_late"], v2["risk"]), 5), "mean risk", round(v2["risk"].mean(), 4), "observed", round(v2["is_late"].mean(), 4))
    print("REAL importance", table.head(6).round(4).to_dict("records"))
    assert len(trained.selection.losses) == 24 and trained.orders == {"train": 29_894, "valid_v1": 4_649, "valid_v2": 5_032}
    assert 0.70 < trained.selection.auc_v1 < cfg.criteria.leak_tripwire_auc
    assert trained.selection.log_loss_v1 <= min(loss for _c, loss in trained.selection.losses) + 1e-6
    assert 0.70 < auc["model"] < 0.85 and 0.70 < auc["b2"] < auc["b5"] < 0.85
    assert abs(v2["risk"].mean() - v2["is_late"].mean()) < 0.05
