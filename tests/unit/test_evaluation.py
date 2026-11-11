"""The metrics, the paired bootstrap and the criteria (U15, AC23, decisions D16-D18).

The first test is the hand case of ten orders: every number below was counted by hand from the list of ten, and the bootstrap is checked against a plain
resampling with scikit-learn that draws from the same generator. The rest pins the edges (ties, one class, undefined values) and the fixed verdicts.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score, roc_auc_score

from latedel.evaluation import (
    METHODS,
    Bootstrap,
    EvaluationResult,
    auc_score,
    calibration_table,
    evaluate_criteria,
    expected_calibration_error,
    es,
    method_metrics,
    paired_bootstrap,
    post_break_criterion,
    roc_table,
)
from latedel.threshold import Costs

COSTS = Costs(5.0, 20.0, 0.5)

# the ten orders of the hand case: the risk of the model and whether the order was late. Five are late (1, 2, 4, 6, 10) and five are not (3, 5, 7, 8, 9)
RISK = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
LATE = np.array([1, 1, 0, 1, 0, 1, 0, 0, 0, 1])
RULE = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0])    # a rule that flags the first five orders: a score of 0 or 1


def test_ten_order_hand_case_and_bootstrap_is_seeded():
    """AC23: exact metrics for ten orders; the same seed gives the same interval; the interval is the one of a plain resampling with the same draws."""
    m = method_metrics(LATE, RISK, RISK >= 0.5, 0.5, COSTS, probabilistic=True, ece_bins=10)
    # AUC: of the 25 pairs (late, not late) the late order has the higher risk in 17: order 1 beats 5, order 2 beats 5, order 4 beats 4 (5, 7, 8, 9), order 6 beats 3
    assert m["auc"] == pytest.approx(17 / 25)
    # average precision: the late orders sit at positions 1, 2, 4, 6 and 10 of the ranking, so the precisions are 1/1, 2/2, 3/4, 4/6 and 5/10
    assert m["pr_auc"] == pytest.approx((1 + 1 + 3 / 4 + 4 / 6 + 5 / 10) / 5)
    assert m["pr_auc"] == pytest.approx(average_precision_score(LATE, RISK))
    # Brier: (0.1^2 + 0.2^2 + 0.7^2 + 0.4^2 + 0.5^2 + 0.6^2 + 0.3^2 + 0.2^2 + 0.1^2 + 0.95^2) / 10
    assert m["brier"] == pytest.approx(2.3525 / 10)
    # ECE: each order is alone in its bin of width 0.1, so it is the mean of |p - y| = (0.1 + 0.2 + 0.7 + 0.4 + 0.5 + 0.6 + 0.3 + 0.2 + 0.1 + 0.95) / 10
    assert m["ece"] == pytest.approx(0.405)
    # bias: the mean risk 0.455 minus the late rate 0.5
    assert m["bias"] == pytest.approx(-0.045) and m["late_rate"] == 0.5 and m["orders"] == 10
    # flagged at 0.5: orders 1 to 5; late among them: 1, 2 and 4
    assert (m["flagged_share"], m["precision"], m["recall"], m["threshold"]) == (0.5, 3 / 5, 3 / 5, 0.5)
    # net benefit: 3 late x 20 x 0.5 - 5 flagged x 5 = 30 - 25 = 5, that is 500 for every 1,000 orders
    assert (m["nb_total"], m["nb_per_1000"]) == (5.0, 500.0)

    # the bootstrap: the model against the rule, 300 paired resamples with seed 0 (the procedure does not depend on the count; the product's default is 1,000)
    boot = paired_bootstrap(LATE, RISK, RULE, 300, 0)
    again = paired_bootstrap(LATE, RISK, RULE, 300, 0)
    other = paired_bootstrap(LATE, RISK, RULE, 300, 1)
    assert boot == again and (boot.ci_low, boot.ci_high) != (other.ci_low, other.ci_high)
    assert (boot.resamples, boot.seed) == (300, 0) and boot.diff == pytest.approx(17 / 25 - roc_auc_score(LATE, RULE))
    rng, diffs, discarded = np.random.default_rng(0), [], 0
    for _ in range(300):
        ix = rng.integers(0, 10, 10)
        if LATE[ix].min() == LATE[ix].max():
            discarded += 1
            continue
        diffs.append(roc_auc_score(LATE[ix], RISK[ix]) - roc_auc_score(LATE[ix], RULE[ix]))
    low, high = np.percentile(diffs, [2.5, 97.5])
    assert boot.ci_low == pytest.approx(low, abs=1e-12) and boot.ci_high == pytest.approx(high, abs=1e-12) and boot.discarded == discarded


def test_the_auc_counts_ties_by_halves_like_scikit_learn_and_is_undefined_with_one_class():
    rng = np.random.default_rng(3)
    for _ in range(20):
        y = rng.integers(0, 2, 60)
        score = rng.integers(0, 5, 60) / 4          # few distinct values: many ties
        if y.min() == y.max():
            continue
        assert auc_score(y, score) == pytest.approx(roc_auc_score(y, score), abs=1e-12)
    assert math.isnan(auc_score([1, 1, 1], [0.1, 0.2, 0.3])) and math.isnan(auc_score([0, 0], [0.5, 0.5]))
    with pytest.raises(ValueError, match="mismo largo"):
        auc_score([0, 1], [0.1])


def test_a_resample_with_a_single_class_is_discarded_and_counted_and_none_left_gives_no_interval():
    y = np.array([1] + [0] * 9)                     # one late order in ten: many resamples miss it
    boot = paired_bootstrap(y, np.arange(10) / 10, np.arange(10)[::-1] / 10, 400, 0)
    assert boot.discarded > 0 and boot.ci_low is not None and boot.resamples == 400
    kept = 400 - boot.discarded
    assert kept > 100, "most resamples keep the late order"
    none = paired_bootstrap(np.ones(5, dtype=int), np.arange(5) / 5, np.arange(5) / 5, 50, 0)
    assert (none.ci_low, none.ci_high, none.discarded) == (None, None, 50) and math.isnan(none.diff)
    with pytest.raises(ValueError, match="remuestras"):
        paired_bootstrap(LATE, RISK, RULE, 0, 0)
    with pytest.raises(ValueError, match="mismo largo"):
        paired_bootstrap(LATE, RISK[:5], RULE, 10, 0)


def test_undefined_metrics_are_none_and_a_rule_has_no_probability_metrics():
    nothing_flagged = method_metrics(LATE, RULE, RULE >= 2, 2.0, COSTS, probabilistic=False, ece_bins=10)
    assert nothing_flagged["precision"] is None and nothing_flagged["recall"] == 0.0 and nothing_flagged["nb_total"] == 0.0
    assert all(nothing_flagged[k] is None for k in ("brier", "ece", "bias"))
    no_late = method_metrics(np.zeros(4, dtype=int), np.array([0.1, 0.2, 0.3, 0.4]), np.array([True, False, False, False]), 0.4, COSTS, probabilistic=True, ece_bins=10)
    assert no_late["recall"] is None and no_late["pr_auc"] is None and no_late["auc"] is None and no_late["precision"] == 0.0
    assert no_late["nb_total"] == -5.0, "an alert on an order that is not late costs the alert"


def test_the_calibration_table_puts_a_risk_of_one_in_the_last_bin_and_skips_empty_bins():
    p = np.array([0.0, 0.05, 0.95, 1.0, 1.0])
    y = np.array([0, 0, 1, 1, 0])
    table = calibration_table(p, y, 10)
    assert list(table["bin"]) == [0, 9] and list(table["orders"]) == [2, 3]
    assert table.loc[1, "p_mean"] == pytest.approx((0.95 + 1 + 1) / 3) and table.loc[1, "observed"] == pytest.approx(2 / 3)
    assert expected_calibration_error(p, y, 10) == pytest.approx(2 / 5 * abs(0.025 - 0) + 3 / 5 * abs((0.95 + 2) / 3 - 2 / 3))


def test_the_roc_curve_is_reduced_to_the_requested_points_keeping_both_ends():
    rng = np.random.default_rng(1)
    y, score = rng.integers(0, 2, 500), rng.random(500)
    full = roc_table(y, score, 10_000)
    small = roc_table(y, score, 25)
    assert len(small) <= 25 and len(full) > 100
    assert (small["fpr"].iloc[0], small["tpr"].iloc[0]) == (0.0, 0.0) and (small["fpr"].iloc[-1], small["tpr"].iloc[-1]) == (1.0, 1.0)
    assert list(small["point"]) == list(range(len(small)))
    assert roc_table(np.ones(4, dtype=int), np.arange(4) / 4, 10).empty


def test_numbers_are_written_with_a_decimal_comma_a_thousands_point_and_a_true_minus():
    assert es(0.7712) == "0,771" and es(-0.004) == "−0,004" and es(1504.44, 1) == "1.504,4" and es(None) == "n/d" and es(float("nan")) == "n/d"
    assert es(42.0, 0) == "42" and es(0.03, 2) == "0,03"


# --- the criteria, on results written by hand ----------------------------------------------------------------------------------------------------------


def _metrics(**overrides) -> dict:
    base = {
        "orders": 100.0, "late_rate": 0.5, "auc": 0.7, "pr_auc": 0.7, "brier": 0.2, "ece": 0.02, "bias": 0.01, "threshold": 0.5, "flagged_share": 0.4, "precision": 0.8,
        "recall": 0.6, "nb_total": 100.0, "nb_per_1000": 1000.0, "topk_k": 40.0, "topk_late": 35.0,
    }
    return {**base, **overrides}


def _result(cfg, *, model=None, b2=None, b5=None, bootstrap=None, sensitivity=None) -> EvaluationResult:
    methods = {name: _metrics() for name in METHODS}
    methods["model"] = _metrics(**(model or {}))
    methods["b2_mode"] = _metrics(**{"auc": 0.6, "brier": 0.25, "nb_per_1000": 800.0, "topk_late": 30.0, **(b2 or {})})
    methods["b5_mode_noon"] = _metrics(**{"auc": 0.7, "nb_per_1000": 900.0, **(b5 or {})})
    rows = [(b, m, "test", 0.5, 1.0, nb, 0.4, 100) for b, (a, c) in zip([0.1, 0.5, 0.9], [(900, 800), (1000, 800), (500, 400)], strict=True) for m, nb in (("model", a), ("b2_mode", c))]
    table = pd.DataFrame(rows, columns=["break_even", "method", "scope", "threshold", "nb_total", "nb_per_1000", "flagged_share", "orders"]) if sensitivity is None else sensitivity
    boots = {"model_vs_b2_mode": Bootstrap(0.1, 0.05, 0.15, 1000, 0, 0), "model_vs_b5_mode_noon": Bootstrap(0.02, 0.01, 0.03, 1000, 0, 0)} if bootstrap is None else bootstrap
    empty = pd.DataFrame()
    return EvaluationResult("test", date(2017, 10, 31), 100, methods, {}, empty, empty, boots, table, COSTS)


def test_each_criterion_is_decided_by_its_number_and_its_verdict_says_supera_or_no_supera_with_it(cfg):
    criteria = {c.criterion_id: c for c in evaluate_criteria(_result(cfg), cfg)}
    assert list(criteria) == [f"C0{i}" for i in range(1, 10)] and all(c.passed for c in criteria.values())
    assert "AUC 0,700 frente a 0,600" in criteria["C01"].verdict_es and criteria["C01"].verdict_es.startswith("El modelo supera a la regla por modo")
    assert criteria["C03"].verdict_es == "El modelo supera a la regla de dos variables: la diferencia de AUC es 0,020, con un intervalo del 95 % de 0,010 a 0,030."
    for c in criteria.values():
        assert "no supera" not in c.verdict_es, c.verdict_es

    failing = {c.criterion_id: c for c in evaluate_criteria(
        _result(
            cfg, model={"auc": 0.55, "brier": 0.3, "ece": 0.05, "bias": 0.03, "nb_per_1000": 700.0, "topk_late": 20.0},
            bootstrap={"model_vs_b2_mode": Bootstrap(-0.05, -0.1, 0.0, 1000, 0, 0), "model_vs_b5_mode_noon": Bootstrap(0.0, -0.02, 0.02, 1000, 0, 0)},
            sensitivity=pd.DataFrame(
                [(b, m, "test", 0.5, 1.0, nb, 0.4, 100) for b, (a, c) in zip([0.1, 0.5], [(400, 800), (1000, 800)], strict=True) for m, nb in (("model", a), ("b2_mode", c))],
                columns=["break_even", "method", "scope", "threshold", "nb_total", "nb_per_1000", "flagged_share", "orders"],
            ),
        ), cfg)}
    assert {k: c.passed for k, c in failing.items()} == {k: False for k in failing}
    for c in failing.values():
        assert "no supera" in c.verdict_es, c.verdict_es
    assert "AUC 0,550 frente a 0,600" in failing["C01"].verdict_es
    assert failing["C03"].verdict_es == "El modelo no supera a la regla de dos variables: la diferencia de AUC es 0,000, con un intervalo del 95 % de −0,020 a 0,020."
    assert (failing["C03"].value, failing["C03"].ci_low, failing["C03"].ci_high, failing["C03"].baseline_value) == (0.0, -0.02, 0.02, 0.0)
    assert "ECE 0,050 frente a un máximo de 0,03" in failing["C06"].verdict_es and "sesgo 0,030 frente a un máximo de 0,02" in failing["C06"].verdict_es
    assert failing["C09"].verdict_es == "El modelo no supera a B2 en todos los puntos de equilibrio: lo iguala o supera en 1 de 2." and (failing["C09"].value, failing["C09"].baseline_value) == (1.0, 2.0)
    assert "20 pedidos tarde frente a 30" in failing["C08"].verdict_es and "k = 40" in failing["C08"].verdict_es


def test_a_tie_does_not_beat_and_each_half_of_c06_fails_on_its_own(cfg):
    equal = pd.DataFrame(
        [(b, m, "test", 0.5, 1.0, 800.0, 0.4, 100) for b in (0.1, 0.5) for m in ("model", "b2_mode")],
        columns=["break_even", "method", "scope", "threshold", "nb_total", "nb_per_1000", "flagged_share", "orders"],
    )
    tie = {c.criterion_id: c for c in evaluate_criteria(
        _result(cfg, model={"auc": 0.6, "brier": 0.25, "nb_per_1000": 800.0, "topk_late": 30.0}, b5={"nb_per_1000": 800.0}, sensitivity=equal), cfg)}
    assert [tie[k].passed for k in ("C01", "C04", "C05", "C07", "C08")] == [False] * 5, "a tie is not an advantage"
    assert tie["C09"].passed is True, "C09 asks for 'equal or greater'"
    only_ece = {c.criterion_id: c for c in evaluate_criteria(_result(cfg, model={"ece": 0.031}), cfg)}
    only_bias = {c.criterion_id: c for c in evaluate_criteria(_result(cfg, model={"bias": -0.021}), cfg)}
    exactly = {c.criterion_id: c for c in evaluate_criteria(_result(cfg, model={"ece": 0.03, "bias": -0.02}), cfg)}
    assert (only_ece["C06"].passed, only_bias["C06"].passed, exactly["C06"].passed) == (False, False, True)


def test_a_criterion_that_cannot_be_computed_is_not_passed_and_says_why(cfg):
    undefined = {c.criterion_id: c for c in evaluate_criteria(
        _result(cfg, model={"auc": None, "ece": None, "bias": None, "brier": None}, bootstrap={"model_vs_b2_mode": Bootstrap(math.nan, None, None, 10, 10, 0)}, sensitivity=pd.DataFrame(
            columns=["break_even", "method", "scope", "threshold", "nb_total", "nb_per_1000", "flagged_share", "orders"])), cfg)}
    for criterion_id in ("C01", "C02", "C03", "C06", "C07", "C09"):
        c = undefined[criterion_id]
        assert c.passed is False and "no supera" in c.verdict_es, criterion_id
    assert "no se pudo calcular" in undefined["C02"].verdict_es and "ninguna remuestra tiene las dos clases" in undefined["C02"].verdict_es
    assert undefined["C03"].verdict_es.endswith("(ninguna remuestra tiene las dos clases).")
    assert undefined["C06"].value is None and "la calibración no está definida" in undefined["C06"].verdict_es
    assert "no hay sensibilidad en la prueba" in undefined["C09"].verdict_es


def test_c10_is_informative_without_a_threshold_and_the_criteria_refuse_the_wrong_scope(cfg):
    post = replace(_result(cfg), scope="post_break", orders=8043)
    c10 = post_break_criterion(post)
    assert c10.passed is None and c10.criterion_id == "C10" and c10.value == 0.7 and c10.baseline_value == 0.6
    assert "Informativo, sin umbral" in c10.verdict_es and "8.043 pedidos" in c10.verdict_es and "no supera" not in c10.verdict_es
    assert c10.seal_entry() == {"id": "C10", "value": 0.7, "baseline_value": 0.6, "ci_low": None, "ci_high": None, "passed": None}
    with pytest.raises(ValueError, match="sobre la prueba"):
        evaluate_criteria(post, cfg)
    with pytest.raises(ValueError, match="tramo posterior"):
        post_break_criterion(_result(cfg))


def test_what_was_seen_before_the_thresholds_is_recorded_for_every_criterion(cfg):
    criteria = evaluate_criteria(_result(cfg), cfg) + [post_break_criterion(replace(_result(cfg), scope="post_break"))]
    seen = {c.criterion_id: (c.seen_by_research, c.seen_by_planner) for c in criteria}
    assert seen["C01"] == ("sí", "en parte") and seen["C02"] == ("no", "no") and seen["C04"] == ("en parte", "no") and seen["C10"] == ("sí", "no")
    assert len(seen) == 10 and all(r in ("sí", "en parte", "no") and p in ("sí", "en parte", "no") for r, p in seen.values())


def test_the_bootstrap_gives_what_the_small_helper_of_the_controls_gave_before_u15_replaced_it():
    """D52 used a test-only paired bootstrap (rank-sum AUC over resampled indices); the product's gives the same difference and interval on the same draws."""
    from scipy.stats import rankdata

    def old_auc(y, score):
        ranks = rankdata(score)
        positives = y == 1
        n1, n0 = int(positives.sum()), int((~positives).sum())
        return (ranks[positives].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)

    rng = np.random.default_rng(5)
    y = rng.integers(0, 2, 600)
    a, b = np.round(rng.random(600) + 0.3 * y, 2), rng.integers(0, 6, 600) / 5 + 0.1 * y      # ties in both scores
    draws, diffs = np.random.default_rng(0), []
    for _ in range(200):
        ix = draws.integers(0, 600, 600)
        if y[ix].min() == y[ix].max():
            continue
        diffs.append(old_auc(y[ix], a[ix]) - old_auc(y[ix], b[ix]))
    low, high = np.percentile(diffs, [2.5, 97.5])
    boot = paired_bootstrap(y, a, b, 200, 0)
    assert boot.diff == pytest.approx(old_auc(y, a) - old_auc(y, b), abs=1e-12)
    assert boot.ci_low == pytest.approx(low, abs=1e-12) and boot.ci_high == pytest.approx(high, abs=1e-12)
