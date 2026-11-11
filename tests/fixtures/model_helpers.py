"""What the tests of the model share: the positive and negative controls built once per session.

The interval of C03 in the tests of the controls comes from the product's ``evaluation.paired_bootstrap`` (U15), which replaced the small bootstrap of this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from latedel.config import PipelineConfig
from latedel.deliveries import delivery_plan, publish, read_delivery
from latedel.features import build_features
from latedel.ingest import SourceFrames, read_source
from latedel.split import block
from latedel.warehouse import connect, load_delivery
from tests.fixtures.make_fixture import make_fixture

CONTROL_ORDERS = 12_000


def control_cfg(cfg: PipelineConfig) -> PipelineConfig:
    """The configuration of the controls: one point of the grid and the set that has the hour (F2), so that the model has what it needs to find a planted effect."""
    return replace(
        cfg,
        model=replace(cfg.model, learning_rate=(0.1,), max_leaf_nodes=(15,), min_samples_leaf=(20,)),
        features=replace(cfg.features, candidate_sets=("f2",)),
        evaluation=replace(cfg.evaluation, importance_repeats=2),
    )


def small_cfg(cfg: PipelineConfig, sets: tuple[str, ...] = ("f0", "f1", "f2")) -> PipelineConfig:
    """One point of the grid for each set: three fits instead of twenty-four."""
    return replace(cfg, model=replace(cfg.model, learning_rate=(0.1,), max_leaf_nodes=(15,), min_samples_leaf=(20,)), features=replace(cfg.features, candidate_sets=sets))


@dataclass(frozen=True)
class Control:
    """A synthetic source of `CONTROL_ORDERS` orders with a planted signal (``standard_market``) or none, and a warehouse loaded with its launch delivery only."""

    signal: str
    cfg: PipelineConfig
    frames: SourceFrames
    launch_db: Path         # the warehouse after the launch only (the test labels do not exist in it)


def build_control(signal: str, folder: Path, cfg: PipelineConfig, n_orders: int = CONTROL_ORDERS) -> Control:
    cfg = control_cfg(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    truth = make_fixture(folder / "source.csv", n_orders=n_orders, seed=0, signal=signal)
    frames, _report = read_source(truth.path, cfg)
    spec = delivery_plan(cfg.deliveries)[0]
    entry = publish(spec, frames, folder / "deliveries", source_sha256="ab" * 32)
    conn = connect(folder / "launch.sqlite")
    load_delivery(conn, read_delivery(folder / "deliveries", entry), spec, f"control-{spec.delivery_id}", cfg, sha256_json=entry.sha256_json)
    conn.close()
    return Control(signal, cfg, frames, folder / "launch.sqlite")


def fixture_test_block(control: Control, feature_set: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(orders, matrix)`` of the test block of the fixture, computed from its frames and not from the warehouse: the orders that are not cancelled, with
    ``is_late`` (real days above the scheduled days), and the feature matrix of `feature_set` built by ``build_features``."""
    cfg, frames = control.cfg, control.frames
    merged = frames.orders.merge(frames.outcomes[["order_id", "real_days", "is_cancelled"]], on="order_id")
    merged = merged[(~merged["is_cancelled"]) & (merged["order_date"].map(lambda d: block(d, cfg)) == "test")].copy()
    merged["is_late"] = (merged["real_days"] > merged["scheduled_days"]).astype("int64")
    ids = set(merged["order_id"])
    matrix = build_features(frames.orders[frames.orders["order_id"].isin(ids)], frames.order_lines[frames.order_lines["order_id"].isin(ids)], feature_set)
    return merged.set_index("order_id").loc[matrix.index].reset_index(), matrix
