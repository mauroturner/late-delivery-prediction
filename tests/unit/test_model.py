"""The model (U12, AC18): the grid, the selection on V1, the tripwire M01, the isotonic calibration and the importance, on small matrices made here.

The warehouse side (the training from the labelled orders, the controls and the probes of the date) is in ``tests/integration/test_model.py``.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from sklearn.frozen import FrozenEstimator

from latedel import model as model_module
from latedel.features import FEATURE_SETS, LeakageError
from latedel.model import (
    Candidate, RiskModel, Selection, calibrate, candidates, check_tripwire, fit_candidates, importance, make_pipeline, predict_risk, select,
)


def f0_frame(n: int = 600, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    """A matrix of F0: First Class orders are late 90% of the time, the others 30%; the scheduled days are noise."""
    rng = np.random.default_rng(seed)
    mode = rng.choice(["First Class", "Second Class", "Standard Class", "Same Day"], n)
    late = np.where(mode == "First Class", rng.random(n) < 0.9, rng.random(n) < 0.3).astype(int)
    x = pd.DataFrame({"shipping_mode": mode, "scheduled_days": rng.choice([0, 1, 2, 4], n)}, index=pd.Index(range(n), name="order_id"))
    return x, late


class Stub:
    """Stands for a fitted pipeline: it predicts the probabilities it was given."""

    def __init__(self, p) -> None:
        self.p = np.asarray(p, dtype=float)

    def predict_proba(self, x):
        assert len(x) == len(self.p)
        return np.column_stack([1 - self.p, self.p])


def test_the_grid_has_24_candidates_in_a_fixed_order(cfg):
    cands = candidates(cfg.model, list(cfg.features.candidate_sets))
    assert len(cands) == 24 and len(set(cands)) == 24
    assert [c.feature_set for c in cands] == ["f0"] * 8 + ["f1"] * 8 + ["f2"] * 8
    assert cands[0] == Candidate("f0", 0.05, 15, 20) and cands[1] == Candidate("f0", 0.05, 15, 100) and cands[2] == Candidate("f0", 0.05, 31, 20)
    assert cands[4] == Candidate("f0", 0.1, 15, 20) and cands[-1] == Candidate("f2", 0.1, 31, 100)
    assert candidates(cfg.model, list(cfg.features.candidate_sets)) == cands, "the same order every time"


def test_the_grid_follows_the_configuration_and_refuses_an_unknown_set(cfg):
    small = replace(cfg.model, learning_rate=(0.1,), max_leaf_nodes=(7, 9, 11), min_samples_leaf=(5,))
    assert [c.max_leaf_nodes for c in candidates(small, ["f1"])] == [7, 9, 11]
    assert len(candidates(small, ["f2", "f0"])) == 6 and candidates(small, ["f2", "f0"])[0].feature_set == "f2"
    with pytest.raises(ValueError, match="f9"):
        candidates(cfg.model, ["f0", "f9"])


def test_a_pipeline_encodes_the_categorical_columns_of_its_set_and_fixes_the_classifier(cfg):
    for name, columns in FEATURE_SETS.items():
        pipeline = make_pipeline(Candidate(name, 0.05, 31, 100), cfg.model)
        classifier = pipeline.named_steps["classifier"]
        assert classifier.get_params()["learning_rate"] == 0.05 and classifier.max_leaf_nodes == 31 and classifier.min_samples_leaf == 100
        assert classifier.l2_regularization == cfg.model.l2_regularization and classifier.max_iter == cfg.model.max_iter and classifier.random_state == cfg.model.random_state
        assert classifier.early_stopping is False, "early stopping would split the training block at random"
        n_categorical = len(classifier.categorical_features)
        assert n_categorical == len(columns) and sum(classifier.categorical_features) == (1 if name == "f0" else 10)
        # the categorical columns come first in the transformer and the mask says so
        assert classifier.categorical_features == [True] * sum(classifier.categorical_features) + [False] * (len(columns) - sum(classifier.categorical_features))


def test_the_model_options_of_the_configuration_reach_the_classifier(cfg):
    other = replace(cfg.model, l2_regularization=3.5, max_iter=11, random_state=9)
    classifier = make_pipeline(Candidate("f0", 0.1, 15, 20), other).named_steps["classifier"]
    assert (classifier.l2_regularization, classifier.max_iter, classifier.random_state) == (3.5, 11, 9)


def test_two_fits_of_the_same_candidate_predict_exactly_the_same(cfg):
    x, y = f0_frame()
    a = fit_candidates([Candidate("f0", 0.1, 15, 20)], cfg.model, {"f0": x}, y)[0][1]
    b = fit_candidates([Candidate("f0", 0.1, 15, 20)], cfg.model, {"f0": x}, y)[0][1]
    np.testing.assert_array_equal(a.predict_proba(x), b.predict_proba(x))


def test_fit_candidates_needs_the_matrix_of_each_set(cfg):
    x, y = f0_frame()
    with pytest.raises(ValueError, match="f1"):
        fit_candidates([Candidate("f1", 0.1, 15, 20)], cfg.model, {"f0": x}, y)


# --- selection ---------------------------------------------------------------------------------------------------------------------------------------


def test_select_takes_the_candidate_with_the_lowest_log_loss_on_v1():
    y = np.array([1, 0, 1, 0, 1, 1])
    good, middling, bad = Stub([0.9, 0.1, 0.8, 0.2, 0.9, 0.7]), Stub([0.6, 0.4, 0.6, 0.4, 0.6, 0.6]), Stub([0.4, 0.6, 0.4, 0.6, 0.4, 0.4])
    cands = [Candidate("f0", 0.05, 15, 20), Candidate("f1", 0.1, 31, 100), Candidate("f2", 0.05, 31, 20)]
    x = {name: pd.DataFrame({"a": range(6)}) for name in ("f0", "f1", "f2")}
    picked = select([(cands[0], middling), (cands[1], good), (cands[2], bad)], x, y)
    assert picked.chosen == cands[1] and picked.pipeline is good
    losses = dict(picked.losses)
    assert list(dict(picked.losses)) == cands and losses[cands[1]] < losses[cands[0]] < losses[cands[2]]
    assert losses[cands[1]] == pytest.approx(-np.mean(np.log([0.9, 0.9, 0.8, 0.8, 0.9, 0.7])))
    assert picked.log_loss_v1 == losses[cands[1]] and isinstance(picked, Selection)
    assert picked.auc_v1 == 1.0


TIE = [
    # (simpler candidate, more complex candidate): same predictions, so a tie, and the simpler one is selected
    (Candidate("f0", 0.1, 31, 20), Candidate("f1", 0.05, 15, 100)),     # the smaller set first, whatever the rest
    (Candidate("f1", 0.1, 15, 20), Candidate("f1", 0.05, 31, 20)),      # then fewer leaves
    (Candidate("f1", 0.1, 15, 100), Candidate("f1", 0.05, 15, 20)),     # then more samples per leaf
    (Candidate("f1", 0.05, 15, 20), Candidate("f1", 0.1, 15, 20)),      # then the lower learning rate
]


@pytest.mark.parametrize(("simple", "complex_"), TIE)
@pytest.mark.parametrize("complex_first", [True, False])
def test_a_tie_within_a_millionth_goes_to_the_simplest_candidate(simple, complex_, complex_first):
    y = np.array([1, 0, 1, 0])
    p = np.array([0.8, 0.3, 0.7, 0.2])
    better = p + 4e-7 * (2 * y - 1)           # every probability a little closer to its label: a loss lower by about 5e-7, less than the 1e-6 tolerance
    x = {name: pd.DataFrame({"a": range(4)}) for name in ("f0", "f1", "f2")}
    assert select([(simple, Stub(p)), (complex_, Stub(better))], x, y).losses[1][1] < select([(simple, Stub(p))], x, y).losses[0][1]
    pair = [(complex_, Stub(better)), (simple, Stub(p))] if complex_first else [(simple, Stub(p)), (complex_, Stub(better))]
    assert select(pair, x, y).chosen == simple


def test_a_loss_better_by_more_than_the_tolerance_beats_simplicity():
    y = np.array([1, 0, 1, 0])
    x = {name: pd.DataFrame({"a": range(4)}) for name in ("f0", "f2")}
    simple, complex_ = Candidate("f0", 0.05, 15, 20), Candidate("f2", 0.1, 31, 20)
    picked = select([(simple, Stub([0.7, 0.3, 0.7, 0.3])), (complex_, Stub([0.8, 0.2, 0.8, 0.2]))], x, y)
    assert picked.chosen == complex_


def test_select_refuses_nothing_to_choose_from_or_a_missing_matrix():
    with pytest.raises(ValueError, match="candidatos"):
        select([], {}, np.array([1, 0]))
    with pytest.raises(ValueError, match="f1"):
        select([(Candidate("f1", 0.1, 15, 20), Stub([0.5, 0.5]))], {"f0": pd.DataFrame({"a": [1, 2]})}, np.array([1, 0]))


# --- M01 -------------------------------------------------------------------------------------------------------------------------------------------------


def selection_with_auc(auc: float) -> Selection:
    c = Candidate("f2", 0.1, 15, 20)
    return Selection(c, Stub([0.5]), ((c, 0.6),), auc)


def test_the_tripwire_stops_the_launch_when_the_auc_on_v1_reaches_the_limit(cfg):
    limit = cfg.criteria.leak_tripwire_auc
    check_tripwire(selection_with_auc(limit - 1e-6), cfg)
    for auc in (limit, 0.99, 1.0):
        with pytest.raises(LeakageError, match=r"M01.*posible fuga.*R5"):
            check_tripwire(selection_with_auc(auc), cfg)


def test_the_tripwire_limit_is_the_one_of_the_criteria_section(cfg):
    stricter = replace(cfg, criteria=replace(cfg.criteria, leak_tripwire_auc=0.7))
    check_tripwire(selection_with_auc(0.69), stricter)
    with pytest.raises(LeakageError):
        check_tripwire(selection_with_auc(0.71), stricter)


def test_a_candidate_whose_matrix_hides_the_label_trips_the_wire(cfg):
    x, y = f0_frame(800)
    leaky = x.assign(scheduled_days=y)                      # a column that is the label: the guards of the matrix are bypassed on purpose
    candidate = Candidate("f0", 0.1, 15, 20)
    fitted = fit_candidates([candidate], cfg.model, {"f0": leaky.iloc[:500]}, y[:500])
    picked = select(fitted, {"f0": leaky.iloc[500:]}, y[500:])
    assert picked.auc_v1 > 0.99
    with pytest.raises(LeakageError, match="M01"):
        check_tripwire(picked, cfg)
    honest = fit_candidates([candidate], cfg.model, {"f0": x.iloc[:500]}, y[:500])
    check_tripwire(select(honest, {"f0": x.iloc[500:]}, y[500:]), cfg)


# --- calibration -----------------------------------------------------------------------------------------------------------------------------------------


def fitted_f0(cfg):
    x, y = f0_frame(1500)
    pipeline = fit_candidates([Candidate("f0", 0.1, 15, 20)], cfg.model, {"f0": x.iloc[:700]}, y[:700])[0][1]
    return pipeline, x, y


def test_the_calibrator_is_fitted_with_v1_only_and_gives_finite_probabilities_in_zero_one(cfg):
    pipeline, x, y = fitted_f0(cfg)
    v1, rest = x.iloc[700:1000], x.iloc[1000:]
    raw_before = pipeline.predict_proba(rest)[:, 1].copy()
    calibrated = calibrate(pipeline, v1, y[700:1000], "isotonic")
    risk = calibrated.predict_proba(rest)[:, 1]
    assert np.isfinite(risk).all() and risk.min() >= 0.0 and risk.max() <= 1.0, "M04"
    assert isinstance(calibrated.estimator, FrozenEstimator)
    np.testing.assert_array_equal(pipeline.predict_proba(rest)[:, 1], raw_before)    # calibrating does not refit the pipeline
    # nothing after V1 can reach it: the labels of the rest do not enter, and the same V1 gives exactly the same calibrator
    again = calibrate(pipeline, v1, y[700:1000], "isotonic")
    np.testing.assert_array_equal(again.predict_proba(rest)[:, 1], risk)
    for part in (slice(0, 60), slice(-60, None)):             # every row of V1 counts: the first ones and the last ones
        other_v1_labels = y[700:1000].copy()
        other_v1_labels[part] = 1 - other_v1_labels[part]
        assert not np.array_equal(calibrate(pipeline, v1, other_v1_labels, "isotonic").predict_proba(rest)[:, 1], risk), "the V1 labels are what it learns from"


def test_the_calibrated_risk_never_decreases_when_the_raw_score_increases(cfg):
    pipeline, x, y = fitted_f0(cfg)
    calibrated = calibrate(pipeline, x.iloc[700:1100], y[700:1100], "isotonic")
    probe = x.iloc[1100:]
    raw = pipeline.predict_proba(probe)[:, 1]
    order = np.argsort(raw, kind="stable")
    assert (np.diff(calibrated.predict_proba(probe)[:, 1][order]) >= -1e-12).all()


def test_an_unknown_calibration_method_is_refused(cfg):
    pipeline, x, y = fitted_f0(cfg)
    with pytest.raises(ValueError, match="sigmoid"):
        calibrate(pipeline, x.iloc[700:1000], y[700:1000], "sigmoid")


# --- the risk model and the importance -------------------------------------------------------------------------------------------------------------------


def risk_model(cfg):
    pipeline, x, y = fitted_f0(cfg)
    c = Candidate("f0", 0.1, 15, 20)
    calibrated = calibrate(pipeline, x.iloc[700:1000], y[700:1000], "isotonic")
    return RiskModel("f0", c, pipeline, calibrated, "isotonic"), x, y


def test_predict_risk_refuses_a_matrix_that_is_not_exactly_the_set_of_the_model(cfg):
    model, x, _y = risk_model(cfg)
    assert predict_risk(model, x.iloc[1000:1010]).shape == (10,)
    with pytest.raises(LeakageError, match="exactamente las del conjunto f0"):
        predict_risk(model, x.iloc[1000:1010].assign(extra=1))
    with pytest.raises(LeakageError):
        predict_risk(model, x.iloc[1000:1010][["scheduled_days", "shipping_mode"]])
    with pytest.raises(LeakageError, match="resultado"):
        predict_risk(model, x.iloc[1000:1010].rename(columns={"scheduled_days": "real_days"}))
    duplicated = pd.concat([x.iloc[1000:1005], x.iloc[1000:1005]])
    with pytest.raises(LeakageError, match="repetidos"):
        predict_risk(model, duplicated)
    np.testing.assert_array_equal(predict_risk(model, x.iloc[1000:1010]), model.calibrated.predict_proba(x.iloc[1000:1010])[:, 1])


def test_predict_risk_of_a_bare_estimator_is_its_positive_class_probability(cfg):
    pipeline, x, _y = fitted_f0(cfg)
    np.testing.assert_array_equal(predict_risk(pipeline, x.iloc[1000:1010]), pipeline.predict_proba(x.iloc[1000:1010])[:, 1])


def test_the_importance_ranks_the_column_that_carries_the_signal_first_and_is_seeded(cfg):
    model, x, y = risk_model(cfg)
    table = importance(model, x.iloc[1000:], y[1000:], cfg)
    assert table.columns.tolist() == ["feature", "auc_drop"] and sorted(table["feature"]) == ["scheduled_days", "shipping_mode"]
    assert table["feature"].iloc[0] == "shipping_mode" and table["auc_drop"].iloc[0] > 0.1
    assert table["auc_drop"].is_monotonic_decreasing
    pd.testing.assert_frame_equal(table, importance(model, x.iloc[1000:], y[1000:], cfg), check_exact=True)


def test_the_importance_repeats_and_the_matrix_guard_come_from_the_configuration(cfg, monkeypatch):
    model, x, y = risk_model(cfg)
    seen = {}
    real = model_module.permutation_importance

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(model_module, "permutation_importance", spy)
    other = replace(cfg, evaluation=replace(cfg.evaluation, importance_repeats=2), model=replace(cfg.model, random_state=7))
    importance(model, x.iloc[1000:], y[1000:], other)
    assert seen["n_repeats"] == 2 and seen["random_state"] == 7 and seen["scoring"] == "roc_auc"
    with pytest.raises(LeakageError):
        importance(model, x.iloc[1000:].assign(extra=1), y[1000:], cfg)
