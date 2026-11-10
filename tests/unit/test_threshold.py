"""The cost threshold and its sensitivity (U13, AC19): the net benefit in a hand case, a threshold that comes from V2 alone, no option of the costs ignored, and never F1.

The hand case has three orders. With the default costs (alert 5, late 20, effect 0.5: an alert on a late order earns 10 - 5 = 5, on an on-time order it loses 5)
the sets that a threshold can flag, and their net benefit, are written here by hand.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from latedel import threshold as threshold_module
from latedel.threshold import (
    Costs, break_even_grid, choose_on_v2, choose_threshold, net_benefit, sensitivity, threshold_grid, v2_threshold,
)

P = np.array([0.9, 0.4, 0.2])
Y = np.array([1, 1, 0])
DEFAULT = Costs(alert_cost=5.0, late_cost=20.0, effect=0.5)


def test_the_default_costs_have_a_break_even_of_one_half_and_come_from_the_configuration(cfg):
    assert DEFAULT.break_even() == 0.5
    assert Costs.from_config(cfg.costs) == DEFAULT
    assert Costs(8.0, 20.0, 0.5).break_even() == 0.8 and Costs(5.0, 40.0, 0.5).break_even() == 0.25 and Costs(5.0, 20.0, 1.0).break_even() == 0.25


def test_net_benefit_hand_case_and_threshold_from_v2_only(cfg):
    # by hand: an alert on a late order earns 20 x 0.5 - 5 = 5; on an on-time order it loses 5; not flagging adds nothing
    flagged = {(True, False, False): 5.0, (True, True, False): 10.0, (True, True, True): 5.0, (False, False, False): 0.0, (False, False, True): -5.0, (False, True, False): 5.0}
    for marks, expected in flagged.items():
        assert net_benefit(Y, np.array(marks), DEFAULT) == expected
    grid = threshold_grid(cfg.costs)
    # flagged sets by threshold: t <= 0.2 all (5); 0.2 < t <= 0.4 the first two (10); 0.4 < t <= 0.9 the first (5); above 0.9 none (0): the best is 10,
    # reached at 0.21 ... 0.40; on a tie the largest threshold is taken
    assert choose_threshold(P, Y, DEFAULT, grid) == 0.40

    v2 = pd.DataFrame({"block": "valid_v2", "risk": P, "is_late": Y})
    others = pd.concat([
        pd.DataFrame({"block": "valid_v1", "risk": P, "is_late": 1 - Y}),
        pd.DataFrame({"block": "train", "risk": [0.1, 0.2, 0.3], "is_late": [0, 0, 0]}),
        pd.DataFrame({"block": "test", "risk": [0.95, 0.96, 0.97], "is_late": [1, 1, 1]}),
    ])
    choice = choose_on_v2(pd.concat([v2, others], ignore_index=True), DEFAULT, grid)
    assert choice.threshold == 0.40 and choice.net_benefit == 10.0 and choice.break_even == 0.5 and choice.orders == 3
    assert choice.flagged_share == pytest.approx(2 / 3) and choice.net_benefit_per_1000 == pytest.approx(10 / 3 * 1000)
    mixed = pd.concat([v2, others], ignore_index=True).assign(block="valid_v2")
    assert choose_on_v2(mixed, DEFAULT, grid).threshold != 0.40, "control: with the other blocks mixed in, the threshold would be another one"
    only_others = others.copy()
    with pytest.raises(ValueError, match="V2"):
        choose_on_v2(only_others, DEFAULT, grid)
    with pytest.raises(ValueError, match="block"):
        choose_on_v2(v2.drop(columns=["block"]), DEFAULT, grid)


def test_the_threshold_of_a_trained_model_is_the_one_of_its_v2_risks_with_the_configured_costs(cfg):
    valid_v2 = pd.DataFrame({"order_id": [1, 2, 3], "is_late": Y, "risk": P})
    choice = v2_threshold(valid_v2, cfg)
    assert (choice.threshold, choice.net_benefit, choice.break_even) == (0.40, 10.0, 0.5)
    pricey = replace(cfg, costs=replace(cfg.costs, alert_cost=8.0))
    assert v2_threshold(valid_v2, pricey).break_even == 0.8 and v2_threshold(valid_v2, pricey).net_benefit == 4.0


def test_the_grid_and_the_break_evens_follow_the_configuration_without_float_noise(cfg):
    grid = threshold_grid(cfg.costs)
    assert len(grid) == 99 and grid[0] == 0.01 and grid[-1] == 0.99 and grid[34] == 0.35 and grid[9] == 0.1
    b = break_even_grid(cfg.costs)
    assert len(b) == 17 and b[0] == 0.10 and b[-1] == 0.90 and b[5] == 0.35 and b[1] == 0.15
    coarse = replace(cfg.costs, threshold_min=0.1, threshold_max=0.9, threshold_step=0.2, sensitivity_min=0.2, sensitivity_max=0.8, sensitivity_step=0.3)
    assert threshold_grid(coarse).tolist() == [0.1, 0.3, 0.5, 0.7, 0.9] and break_even_grid(coarse).tolist() == [0.2, 0.5, 0.8]


def test_a_tie_goes_to_the_largest_threshold_and_a_threshold_that_flags_nothing_is_allowed():
    grid = np.array([0.2, 0.5, 0.8])
    assert choose_threshold(np.array([0.6, 0.6]), np.array([1, 1]), DEFAULT, grid) == 0.5       # flags both at 0.2 and 0.5 with the same benefit; 0.8 flags none
    assert choose_threshold(np.array([0.6, 0.6]), np.array([0, 0]), DEFAULT, grid) == 0.8       # every alert loses: flag nothing, at the largest threshold
    assert choose_threshold(np.array([0.1, 0.2]), np.array([0, 1]), DEFAULT, np.array([0.5])) == 0.5


def test_inputs_that_cannot_be_a_score_or_a_label_are_refused():
    grid = np.array([0.5])
    for p, y, message in (
        (np.array([0.5, 0.5]), np.array([1]), "mismo largo"), (np.array([]), np.array([]), "no hay pedidos"), (np.array([np.nan]), np.array([1]), "finitos"),
        (np.array([0.5]), np.array([2]), "0 o 1"), (np.array([[0.5]]), np.array([[1]]), "vectores"),
    ):
        with pytest.raises(ValueError, match=message):
            choose_threshold(p, y, DEFAULT, grid)
    with pytest.raises(ValueError, match="grilla"):
        choose_threshold(P, Y, DEFAULT, np.array([]))
    with pytest.raises(ValueError, match="mismo largo"):
        net_benefit(Y, np.array([True]), DEFAULT)
    with pytest.raises(ValueError, match="0 o 1"):
        net_benefit(np.array([3, 1, 0]), np.array([True, False, False]), DEFAULT)


def test_costs_that_make_no_sense_are_refused():
    for args in ((0.0, 20.0, 0.5), (-1.0, 20.0, 0.5), (5.0, 0.0, 0.5), (5.0, 20.0, 0.0), (5.0, 20.0, 1.5), (float("nan"), 20.0, 0.5), (5.0, float("inf"), 0.5)):
        with pytest.raises(ValueError):
            Costs(*args)


# --- no key of [costs] is ignored (AC19) ------------------------------------------------------------------------------------------------------------------


def scenario(cfg, seed: int = 3):
    """Calibrated risks of 2,000 orders (each late with its own probability), so that the best threshold is near the break-even and can move both ways."""
    rng = np.random.default_rng(seed)
    risk = rng.random(2000)
    late = (rng.random(2000) < risk).astype(int)
    valid_v2 = pd.DataFrame({"order_id": range(2000), "is_late": late, "risk": risk})
    choice = v2_threshold(valid_v2, cfg)
    table = sensitivity({"model": risk, "b2": np.round(risk * 4) / 4}, late, {"model": risk[::-1], "b2": np.round(risk[::-1] * 4) / 4}, late[::-1], cfg)
    return choice, table


CHANGES = {
    "alert_cost": 8.0, "late_cost": 40.0, "effect": 1.0,
    "threshold_min": 0.62, "threshold_max": 0.31, "threshold_step": 0.07,
    "sensitivity_min": 0.2, "sensitivity_max": 0.7, "sensitivity_step": 0.1,
}


def test_every_cost_key_changes_the_result(cfg):
    base_choice, base_table = scenario(cfg)
    assert 0.35 < base_choice.threshold < 0.65 and len(base_table) == 17 * 2 * 2
    assert set(CHANGES) == set(cfg.costs.__dataclass_fields__), "a key was added to [costs] without a case here"
    for key, value in CHANGES.items():
        changed = replace(cfg, costs=replace(cfg.costs, **{key: value}))
        choice, table = scenario(changed)
        same_choice = (choice.threshold, choice.net_benefit, choice.break_even) == (base_choice.threshold, base_choice.net_benefit, base_choice.break_even)
        same_table = table.shape == base_table.shape and table.equals(base_table)
        assert not (same_choice and same_table), f"[costs] {key} does not change anything"
    # and each one changes what the formula says it changes
    choice, _ = scenario(replace(cfg, costs=replace(cfg.costs, alert_cost=8.0)))
    assert choice.break_even == 0.8 and choice.threshold > base_choice.threshold
    choice, _ = scenario(replace(cfg, costs=replace(cfg.costs, late_cost=40.0)))
    assert choice.break_even == 0.25 and choice.threshold < base_choice.threshold
    choice, _ = scenario(replace(cfg, costs=replace(cfg.costs, effect=1.0)))
    assert choice.break_even == 0.25 and choice.threshold < base_choice.threshold
    assert scenario(replace(cfg, costs=replace(cfg.costs, threshold_min=0.62)))[0].threshold >= 0.62
    assert scenario(replace(cfg, costs=replace(cfg.costs, threshold_max=0.31)))[0].threshold <= 0.31
    assert scenario(replace(cfg, costs=replace(cfg.costs, threshold_step=0.07)))[0].threshold in threshold_grid(replace(cfg.costs, threshold_step=0.07))
    assert sorted(scenario(replace(cfg, costs=replace(cfg.costs, sensitivity_min=0.2, sensitivity_max=0.7, sensitivity_step=0.1)))[1]["break_even"].unique()) == [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]


def test_the_late_cost_and_the_effect_scale_the_sensitivity_and_leave_its_thresholds_where_they_were(cfg):
    _, base = scenario(cfg)
    _, doubled = scenario(replace(cfg, costs=replace(cfg.costs, late_cost=40.0)))
    assert doubled["threshold"].tolist() == base["threshold"].tolist() and doubled["flagged_share"].tolist() == base["flagged_share"].tolist()
    np.testing.assert_allclose(doubled["nb_total"], 2 * base["nb_total"])


# --- never F1 (AC19) --------------------------------------------------------------------------------------------------------------------------------------


def test_the_cost_threshold_does_not_flag_almost_everything_where_f1_would(cfg):
    from sklearn.metrics import f1_score

    rng = np.random.default_rng(1)
    late = (rng.random(4000) < 0.45).astype(int)
    risk = 0.02 + 0.96 * rng.random(4000)                    # scores that say nothing about an order that is late 45% of the time: F1 can only grow by flagging more
    grid = threshold_grid(cfg.costs)
    f1_best = grid[int(np.argmax([f1_score(late, risk >= t) for t in grid]))]
    assert (risk >= f1_best).mean() >= 0.95, "in this fixture F1 chooses to flag at least 95% of the orders"
    choice = v2_threshold(pd.DataFrame({"order_id": range(4000), "is_late": late, "risk": risk}), cfg)
    assert choice.flagged_share < 0.5 and choice.net_benefit > net_benefit(late, risk >= f1_best, DEFAULT)
    assert choice.threshold > f1_best


def test_f1_is_not_imported_by_the_threshold_or_the_model():
    for module in ("threshold.py", "model.py"):
        source = (Path(threshold_module.__file__).parent / module).read_text(encoding="utf-8")
        assert "f1_score" not in source and "f_score" not in source, module


# --- the sensitivity --------------------------------------------------------------------------------------------------------------------------------------


def hand_scores():
    v2 = {"model": P, "b2": np.array([0.8, 0.8, 0.4])}
    test = {"model": np.array([0.7, 0.3, 0.45, 0.1]), "b2": np.array([0.8, 0.8, 0.4, 0.4])}
    return v2, Y, test, np.array([1, 0, 1, 0])


def test_the_sensitivity_has_one_row_per_break_even_method_and_scope_with_the_figures_of_the_hand_case(cfg):
    v2, y_v2, test, y_test = hand_scores()
    table = sensitivity(v2, y_v2, test, y_test, cfg)
    assert table.columns.tolist() == ["break_even", "method", "scope", "threshold", "nb_total", "nb_per_1000", "flagged_share", "orders"]
    assert len(table) == 17 * 2 * 2
    assert table["break_even"].drop_duplicates().tolist() == break_even_grid(cfg.costs).tolist()
    first = table.iloc[:4]
    assert first[["method", "scope"]].values.tolist() == [["model", "valid_v2"], ["model", "test"], ["b2", "valid_v2"], ["b2", "test"]]
    at = lambda b, method, scope: table[(table["break_even"] == b) & (table["method"] == method) & (table["scope"] == scope)].iloc[0]  # noqa: E731
    # b = 0.5 is the default costs: by hand the model flags the first two orders of V2 at 0.40 and earns 10
    row = at(0.5, "model", "valid_v2")
    assert (row["threshold"], row["nb_total"], row["flagged_share"], row["orders"]) == (0.40, 10.0, pytest.approx(2 / 3), 3)
    assert row["nb_per_1000"] == pytest.approx(10 / 3 * 1000)
    # on the test the threshold of V2 (0.40) flags the orders with 0.7 and 0.45: one late (+5) and one on time... 0.45 is late too, so +5 +5
    row = at(0.5, "model", "test")
    assert (row["threshold"], row["nb_total"], row["flagged_share"], row["orders"]) == (0.40, 10.0, 0.5, 4)
    assert row["nb_per_1000"] == pytest.approx(2500.0)
    # b = 0.1 -> alert cost 1: an alert on a late order earns 9; the best set is still the first two of V2 (earns 18)
    assert (at(0.1, "model", "valid_v2")["threshold"], at(0.1, "model", "valid_v2")["nb_total"]) == (0.40, 18.0)
    # b = 0.9 -> alert cost 9: an alert on a late order earns 1; the first two earn 2
    assert at(0.9, "model", "valid_v2")["nb_total"] == 2.0
    # B2 scores 0.8, 0.8, 0.4: at b = 0.5 the best set is the first two orders (0.8) for 10, threshold up to 0.8
    row = at(0.5, "b2", "valid_v2")
    assert (row["threshold"], row["nb_total"], row["flagged_share"]) == (0.8, 10.0, pytest.approx(2 / 3))


def test_the_net_benefit_never_rises_when_the_alert_gets_more_expensive(cfg):
    _, table = scenario(cfg)
    for method, part in table[table["scope"] == "valid_v2"].groupby("method"):       # on V2, where the threshold is the best one for each break-even
        assert (part.sort_values("break_even")["nb_per_1000"].diff().dropna() <= 1e-9).all(), method


def test_the_threshold_of_the_sensitivity_comes_from_v2_and_the_test_only_applies_it(cfg):
    v2, y_v2, test, y_test = hand_scores()
    base = sensitivity(v2, y_v2, test, y_test, cfg)
    other_test = sensitivity(v2, y_v2, test, 1 - y_test, cfg)
    assert other_test["threshold"].tolist() == base["threshold"].tolist(), "the labels of the test choose nothing"
    assert not other_test["nb_total"].equals(base["nb_total"])
    other_v2 = sensitivity(v2, 1 - y_v2, test, y_test, cfg)
    assert other_v2["threshold"].tolist() != base["threshold"].tolist()


def test_without_a_test_the_sensitivity_has_only_the_rows_of_v2_and_the_arguments_must_agree(cfg):
    v2, y_v2, test, y_test = hand_scores()
    table = sensitivity(v2, y_v2, None, None, cfg)
    assert len(table) == 17 * 2 and set(table["scope"]) == {"valid_v2"}
    with pytest.raises(ValueError, match="van juntos"):
        sensitivity(v2, y_v2, test, None, cfg)
    with pytest.raises(ValueError, match="mismos métodos"):
        sensitivity(v2, y_v2, {"model": test["model"]}, y_test, cfg)
