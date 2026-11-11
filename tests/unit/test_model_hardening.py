"""Hardening of the model after the native review of U12 (H3): a block of the split with a single class of label is a documented error, not scikit-learn's.

R3-001: ``select`` computed the AUC of V1 without looking at its labels, so a V1 that happened to be all late (or all on time) stopped the launch with
``ValueError: Only one class present in y_true`` from the library, an error that says nothing about the split. The same blind spot is in the other two blocks the
model learns from: the training block (the classifier cannot fit one class) and V2 (the AUC and the threshold).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from latedel import model
from latedel.model import BlockLabelError, Candidate, select, train_model
from latedel.warehouse import rebuild_at
from tests.fixtures.model_helpers import small_cfg


class Stub:
    """A fitted pipeline that gives every order the same probability."""

    def __init__(self, p: float) -> None:
        self.p = p

    def predict_proba(self, x):
        return np.column_stack([np.full(len(x), 1 - self.p), np.full(len(x), self.p)])


def candidate_stubs():
    return [(Candidate("f0", 0.1, 15, 20), Stub(0.3)), (Candidate("f1", 0.1, 15, 20), Stub(0.6))]


def matrices(n: int):
    return {"f0": pd.DataFrame({"x": range(n)}), "f1": pd.DataFrame({"x": range(n)})}


@pytest.mark.parametrize(("labels", "side"), [(np.ones(8, dtype=int), "todos llegaron tarde"), (np.zeros(8, dtype=int), "ninguno llegó tarde")])
def test_select_refuses_a_v1_with_a_single_class_with_its_own_error_that_names_the_block(labels, side):
    with pytest.raises(BlockLabelError, match="valid_v1") as caught:
        select(candidate_stubs(), matrices(8), labels)
    assert "una sola clase" in str(caught.value) and side in str(caught.value) and "8 pedidos" in str(caught.value)
    assert isinstance(caught.value, ValueError), "it is still a ValueError, like the other refusals of the module"
    assert "Only one class" not in str(caught.value), "the message is ours, not the library's"


def test_select_still_selects_when_v1_has_both_classes():
    selection = select(candidate_stubs(), matrices(8), np.array([1, 0, 1, 0, 1, 1, 0, 0]))
    assert selection.chosen.feature_set == "f1" and 0 <= selection.auc_v1 <= 1 and len(selection.losses) == 2


@pytest.fixture()
def launch_with(launch_conn, cfg):
    """A copy of the launch warehouse where every non-cancelled order of one block arrived late (or on time), with its mart rebuilt for the launch."""
    config = small_cfg(cfg)

    def change(first: str, last: str, *, late: bool):
        days, label = (99, 1) if late else (0, 0)
        launch_conn.execute(
            "UPDATE raw_outcomes SET real_days = ?, source_label = ? WHERE is_cancelled = 0 AND order_id IN (SELECT order_id FROM raw_orders WHERE order_date >= ? AND order_date <= ?)",
            (days, label, first, last),
        )
        rebuild_at(launch_conn, config.deliveries.launch_date, config)
        return launch_conn, config

    return change


@pytest.mark.parametrize(
    ("block", "first", "last"), [("valid_v1", "2016-07-08", "2016-09-30"), ("valid_v2", "2016-10-01", "2016-12-31"), ("train", "2015-01-01", "2016-06-30")],
)
@pytest.mark.parametrize("late", [True, False])
def test_the_training_stops_before_fitting_anything_when_a_block_has_a_single_class(launch_with, monkeypatch, block, first, last, late):
    conn, config = launch_with(first, last, late=late)

    def fitted(*args, **kwargs):
        raise AssertionError("nothing is fitted when the labels of a block cannot be used")

    monkeypatch.setattr(model, "fit_candidates", fitted)
    with pytest.raises(BlockLabelError, match=block) as caught:
        train_model(conn, config.deliveries.launch_date, config)
    assert "una sola clase" in str(caught.value) and ("todos llegaron tarde" if late else "ninguno llegó tarde") in str(caught.value)
    assert not conn.in_transaction


def test_a_block_with_both_classes_trains_as_before(launch_conn, cfg, monkeypatch):
    config = small_cfg(cfg)
    rebuild_at(launch_conn, config.deliveries.launch_date, config)
    trained = train_model(launch_conn, config.deliveries.launch_date, config)
    assert trained.orders["valid_v1"] > 100 and 0 < trained.selection.auc_v1 < 1 and len(trained.valid_v2) == trained.orders["valid_v2"]
