"""The simple rules B0 to B5 (U11, AC16): exact values in a hand case, rates that only the training block can move, and the check of the Same Day region."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from latedel.baselines import BASELINE_IDS, baseline_rates, fit_rates, order_band, same_day_region_check, score
from tests.fixtures.hand_cases import HAND_B2, HAND_B5, HAND_TRAIN_LATE_RATE, HAND_TRAIN_ORDERS, baseline_case


def test_rates_ignore_validation_and_test_labels(cfg):
    a = baseline_rates(baseline_case(other_label=0), cfg)
    b = baseline_rates(baseline_case(other_label=1), cfg)
    assert baseline_case(other_label=0)["is_late"].ne(baseline_case(other_label=1)["is_late"]).sum() > 20, "the labels outside the training block do change"
    pd.testing.assert_frame_equal(a.table(), b.table(), check_exact=True)
    assert a.train_late_rate == b.train_late_rate == HAND_TRAIN_LATE_RATE and a.train_orders == b.train_orders == HAND_TRAIN_ORDERS
    probe = baseline_case(other_label=0)
    for name in BASELINE_IDS:
        np.testing.assert_array_equal(a.scores(probe)[name], b.scores(probe)[name])


def test_the_rates_of_b2_and_b5_are_the_counts_of_the_hand_case(cfg):
    rates = baseline_rates(baseline_case(), cfg)
    assert {k[0]: v for k, v in rates.b2.rates.items()} == pytest.approx(HAND_B2)
    assert {k: v for k, v in rates.b5.rates.items()} == pytest.approx(HAND_B5)
    assert rates.b5.orders == {
        ("First Class", "before"): 4, ("Second Class", "before"): 4, ("Same Day", "before"): 3, ("Same Day", "from"): 3, ("Standard Class", "from"): 10,
    }
    table = rates.table()
    assert table.columns.tolist() == ["baseline", "cell", "rate", "train_orders"]
    assert len(table) == 4 + 5
    assert table.set_index(["baseline", "cell"]).loc[("b5_mode_noon", "Same Day|before")].tolist() == [0.0, 3]
    assert table.set_index(["baseline", "cell"]).loc[("b2_mode", "Standard Class")].tolist() == [0.4, 10]


def test_each_baseline_scores_the_orders_it_is_given_by_its_rule(cfg):
    rates = baseline_rates(baseline_case(), cfg)
    orders = pd.DataFrame({
        "shipping_mode": ["First Class", "Second Class", "Same Day", "Same Day", "Standard Class"],
        "scheduled_days": [1, 2, 0, 0, 4],
        "order_ts": ["2017-02-01T08:00", "2017-02-01T23:59", "2017-02-01T11:59", "2017-02-01T12:00", "2017-02-01T12:00"],
    })
    scores = rates.scores(orders)
    assert list(scores) == list(BASELINE_IDS)
    np.testing.assert_array_equal(scores["b0_all_late"], [1, 1, 1, 1, 1])
    np.testing.assert_array_equal(scores["b1_first_class"], [1, 0, 0, 0, 0])
    np.testing.assert_allclose(scores["b2_mode"], [1.0, 0.75, 0.5, 0.5, 0.4])
    np.testing.assert_array_equal(scores["b3_scheduled_le2"], [1, 1, 1, 1, 0])
    np.testing.assert_array_equal(scores["b4_standard_only"], [0, 0, 0, 0, 1])
    # B5: the second Second Class order is at 23:59, a cell with no training order, and takes the rate of its mode
    np.testing.assert_allclose(scores["b5_mode_noon"], [1.0, 0.75, 0.0, 1.0, 0.4])
    flags = rates.flags(orders)
    np.testing.assert_array_equal(flags["b2_mode"], [True, True, True, True, False])
    np.testing.assert_array_equal(flags["b0_all_late"], [True] * 5)
    np.testing.assert_array_equal(flags["b4_standard_only"], [False, False, False, False, True])


def test_b4_the_negative_control_flags_only_what_is_below_the_base_rate(cfg):
    case = baseline_case()
    train = case[case["block"] == "train"]
    rates = baseline_rates(case, cfg)
    flagged = rates.flags(train)["b4_standard_only"]
    assert train.loc[flagged, "is_late"].mean() == pytest.approx(0.4)
    assert 0.4 < HAND_TRAIN_LATE_RATE
    assert train.loc[rates.flags(train)["b1_first_class"], "is_late"].mean() == 1.0


def test_an_unseen_cell_takes_the_rate_of_its_mode_and_an_unseen_mode_the_overall_rate(cfg):
    rates = baseline_rates(baseline_case(), cfg)
    unseen_cell = pd.DataFrame({"shipping_mode": ["Same Day"], "scheduled_days": [0], "order_ts": ["2017-02-01T12:00"], "is_late": [1]})
    assert rates.scores(unseen_cell)["b5_mode_noon"][0] == 1.0, "Same Day from noon is a cell of the training block"
    first_after = pd.DataFrame({"shipping_mode": ["First Class"], "scheduled_days": [1], "order_ts": ["2017-02-01T15:00"]})
    assert rates.scores(first_after)["b5_mode_noon"][0] == 1.0, "First Class from noon has no training order: the rate of First Class"
    other = pd.DataFrame({"shipping_mode": ["Pigeon"], "scheduled_days": [9], "order_ts": ["2017-02-01T15:00"]})
    assert rates.scores(other)["b2_mode"][0] == pytest.approx(HAND_TRAIN_LATE_RATE)
    assert rates.scores(other)["b5_mode_noon"][0] == pytest.approx(HAND_TRAIN_LATE_RATE)


def test_the_noon_hour_of_the_configuration_moves_the_cells_of_b5(cfg):
    case = baseline_case()
    at_10 = baseline_rates(case, replace(cfg, features=replace(cfg.features, noon_hour=10)))
    assert at_10.b5.rates[("Same Day", "before")] == 0.0 and at_10.b5.orders[("Same Day", "before")] == 1
    assert at_10.b5.rates[("Same Day", "from")] == pytest.approx(3 / 5)
    assert baseline_rates(case, cfg).b5.rates[("Same Day", "from")] == 1.0


def test_order_band_cuts_at_the_hour_and_keeps_the_index():
    ts = pd.Series(["2017-02-01T11:59", "2017-02-01T12:00", "2017-02-01T00:00"], index=[7, 8, 9])
    assert order_band(ts, 12).tolist() == ["before", "from", "before"] and order_band(ts, 12).index.tolist() == [7, 8, 9]


def test_fit_rates_refuses_other_blocks_and_missing_columns():
    case = baseline_case()
    with pytest.raises(ValueError, match="entrenamiento"):
        fit_rates(case, ["shipping_mode"])
    with pytest.raises(ValueError, match="is_late"):
        fit_rates(case.drop(columns=["is_late"]), ["shipping_mode"])
    with pytest.raises(ValueError, match="al menos una columna"):
        fit_rates(case[case["block"] == "train"], [])
    with pytest.raises(ValueError, match="al menos un pedido"):
        fit_rates(case.iloc[0:0], ["shipping_mode"])
    with pytest.raises(ValueError, match="faltan columnas"):
        score(fit_rates(case[case["block"] == "train"], ["shipping_mode"]), pd.DataFrame({"x": [1]}))
    assert len(score(fit_rates(case[case["block"] == "train"], ["shipping_mode"]), case.iloc[0:0])) == 0


def test_baseline_rates_needs_the_block_column_and_a_training_block(cfg):
    case = baseline_case()
    with pytest.raises(ValueError, match="block"):
        baseline_rates(case.drop(columns=["block"]), cfg)
    with pytest.raises(ValueError, match="entrenamiento"):
        baseline_rates(case[case["block"] != "train"], cfg)
    with pytest.raises(ValueError, match="faltan columnas"):
        baseline_rates(case, cfg).scores(case.drop(columns=["order_ts"]))


# --- the region effect in Same Day with the hour controlled -----------------------------------------------------------------------------------------


def _same_day(rows):
    """rows: (region, hour, is_late) of Same Day training orders -> (labelled, orders)."""
    labelled = pd.DataFrame({
        "order_id": range(len(rows)), "order_ts": [f"2015-06-01T{h:02d}:00" for _r, h, _l in rows], "shipping_mode": "Same Day", "is_late": [late for _r, _h, late in rows], "block": "train",
    })
    orders = pd.DataFrame({"order_id": range(len(rows)), "order_region": [r for r, _h, _l in rows]})
    return labelled, orders


def test_a_region_effect_that_is_only_the_hour_disappears_when_the_hour_is_controlled(cfg):
    # region A orders early (on time), region B orders late in the day (late): the region "predicts" lateness only through the hour
    rows = [("A", 9, 0)] * 40 + [("A", 15, 1)] * 10 + [("B", 9, 0)] * 10 + [("B", 15, 1)] * 40
    check = same_day_region_check(*_same_day(rows), cfg)
    assert check.orders == 100 and check.regions == 2
    assert check.raw_chi2 == pytest.approx(36.0) and check.raw_dof == 1 and check.raw_p < 1e-8
    assert check.controlled_chi2 == 0.0 and check.controlled_dof == 0 and check.controlled_p == 1.0
    assert check.region_vs_band_p < 1e-8, "the region is associated with the time of the order, which explains the raw effect"
    assert check.late_share_by_band == {"before": 0.0, "from": 1.0}


def test_a_region_effect_inside_the_hour_survives_the_control(cfg):
    rows = [("A", 15, 1)] * 40 + [("A", 15, 0)] * 10 + [("B", 15, 1)] * 10 + [("B", 15, 0)] * 40 + [("A", 9, 0)] * 25 + [("B", 9, 0)] * 25
    check = same_day_region_check(*_same_day(rows), cfg)
    assert check.controlled_dof == 1 and check.controlled_chi2 == pytest.approx(36.0) and check.controlled_p < 1e-8
    assert check.region_vs_band_p > 0.5, "the regions order at the same times"


def test_the_region_check_reads_only_same_day_training_orders(cfg):
    labelled, orders = _same_day([("A", 9, 0)] * 10 + [("B", 15, 1)] * 10)
    extra = labelled.iloc[:6].copy()
    extra["order_id"] = range(100, 106)
    extra["shipping_mode"] = ["Standard Class"] * 3 + ["Same Day"] * 3
    extra["block"] = ["train"] * 3 + ["valid_v1"] * 3
    labelled = pd.concat([labelled, extra], ignore_index=True)
    orders = pd.concat([orders, pd.DataFrame({"order_id": range(100, 106), "order_region": "C"})], ignore_index=True)
    assert same_day_region_check(labelled, orders, cfg).orders == 20


def test_the_region_check_needs_a_region_for_every_order_and_the_block_column(cfg):
    labelled, orders = _same_day([("A", 9, 0)] * 5)
    with pytest.raises(ValueError, match="región"):
        same_day_region_check(labelled, orders.iloc[:3], cfg)
    with pytest.raises(ValueError, match="block"):
        same_day_region_check(labelled.drop(columns=["block"]), orders, cfg)
