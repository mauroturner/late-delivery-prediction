"""The threshold of the model on its validation (U13, AC19): the fixture, and the real launch (V2 only, never the test block, D17)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from latedel.baselines import baseline_rates
from latedel.split import blocks, labelled_orders
from latedel.threshold import Costs, break_even_grid, choose_threshold, net_benefit, sensitivity, threshold_grid, v2_threshold
from tests.fixtures.model_helpers import small_cfg


def v2_scores(trained, labelled, cfg) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """The V2 scores of the model, B2 and B5 (the latter two fitted on the training block), aligned on the order ids of V2."""
    v2_orders = blocks(labelled)["valid_v2"].set_index("order_id").loc[trained.valid_v2["order_id"]].reset_index()
    rules = baseline_rates(labelled, cfg).scores(v2_orders)
    scores = {"model": trained.valid_v2["risk"].to_numpy(), "b2_mode": rules["b2_mode"], "b5_mode_noon": rules["b5_mode_noon"]}
    return scores, trained.valid_v2["is_late"].to_numpy()


def test_the_threshold_of_the_fixture_model_maximizes_the_net_benefit_on_its_v2_orders(cfg, launch_conn):
    from latedel.model import train_model

    config = small_cfg(cfg)
    trained = train_model(launch_conn, config.deliveries.launch_date, config)
    choice = v2_threshold(trained.valid_v2, config)
    p, y = trained.valid_v2["risk"].to_numpy(), trained.valid_v2["is_late"].to_numpy()
    costs = Costs.from_config(config.costs)
    brute = {float(t): net_benefit(y, p >= t, costs) for t in threshold_grid(config.costs)}
    assert choice.net_benefit == max(brute.values()) == brute[choice.threshold]
    assert choice.threshold == max(t for t, v in brute.items() if v == max(brute.values())), "on a tie, the largest threshold"
    assert choice.orders == len(y) == trained.orders["valid_v2"] and choice.flagged_share == pytest.approx((p >= choice.threshold).mean())
    assert choice.break_even == 0.5
    assert 0 < choice.threshold < 1


def test_the_sensitivity_of_the_fixture_has_a_row_for_each_break_even_of_the_range_and_each_method(cfg, launch_conn):
    from latedel.model import train_model

    config = small_cfg(cfg)
    trained = train_model(launch_conn, config.deliveries.launch_date, config)
    scores, y = v2_scores(trained, labelled_orders(launch_conn, config.deliveries.launch_date, config), config)
    table = sensitivity(scores, y, None, None, config)
    assert len(table) == 17 * 3 and set(table["method"]) == {"model", "b2_mode", "b5_mode_noon"}
    for method, part in table.groupby("method"):
        assert part["break_even"].tolist() == break_even_grid(config.costs).tolist(), method
        assert (part["flagged_share"].between(0, 1)).all()
        assert (part.sort_values("break_even")["nb_per_1000"].diff().dropna() <= 1e-9).all(), "a dearer alert never earns more"
    at_default = table[np.isclose(table["break_even"], 0.5) & (table["method"] == "model")].iloc[0]
    assert at_default["threshold"] == v2_threshold(trained.valid_v2, config).threshold
    assert at_default["nb_total"] == v2_threshold(trained.valid_v2, config).net_benefit


# --- the real file (real_data): the threshold chosen on V2, nothing on the test block ---------------------------------------------------------------------


@pytest.mark.real_data
def test_real_launch_threshold_on_v2_and_the_sensitivity_of_the_model_b2_and_b5(cfg, real_launch_conn, real_trained):
    launch = cfg.deliveries.launch_date
    labelled = labelled_orders(real_launch_conn, launch, cfg)
    scores, y = v2_scores(real_trained, labelled, cfg)
    choice = v2_threshold(real_trained.valid_v2, cfg)
    table = sensitivity(scores, y, None, None, cfg)
    costs = Costs.from_config(cfg.costs)
    other = {name: choose_threshold(p, y, costs, threshold_grid(cfg.costs)) for name, p in scores.items()}
    print("REAL threshold on V2", choice)
    print("REAL thresholds on V2 at the default costs", other)
    summary = table[np.isin(table["break_even"], [0.1, 0.3, 0.5, 0.7, 0.9])].pivot(index="break_even", columns="method", values=["threshold", "nb_per_1000", "flagged_share"])
    print("REAL sensitivity on V2\n" + summary.round(3).to_string())
    assert choice.orders == 5_032 and choice.break_even == 0.5
    assert 0.2 < choice.threshold < 0.9 and choice.threshold == other["model"]
    assert choice.net_benefit > 0 and 0 < choice.flagged_share < 1
    assert len(table) == 17 * 3
    for method in scores:
        assert (table[table["method"] == method].sort_values("break_even")["nb_per_1000"].diff().dropna() <= 1e-9).all()
    b2 = table[(table["method"] == "b2_mode") & np.isclose(table["break_even"], 0.5)].iloc[0]
    assert b2["threshold"] == other["b2_mode"] and 0.4652 < b2["threshold"] <= 0.8017, "B2 at 0.5 flags First Class and Second Class"
    assert isinstance(table, pd.DataFrame)
