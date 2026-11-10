"""The split in time: the block of an order, the labelled orders of a calculation date and the guards on them (RF12, AC15, decision D08).

Blocks, by the date of the order (``requirements.md`` section 4): ``train``, ``purge_1``, ``valid_v1``, ``valid_v2``, ``purge_2``, ``test`` and ``post_break``.
A date that no block holds is ``outside``. The purges (``purge_1`` and ``purge_2``) belong to no block of the model: ``blocks`` never returns them, so no
fit, no selection, no calibration and no threshold can use them. The SQL of ``mart_labelled_orders`` names the same blocks (decision D46); a test compares
the two, so the edges live in ``[split]`` and nowhere else.

``labelled_orders`` is the one place that reads labels for the model. It reads the mart for a calculation date and refuses a mart that was built for another
one: a mart built for a later date is cut back to `as_of`, and one built for an earlier date (it would be missing labels) is ``StaleMartError``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import date, datetime

import pandas as pd

from latedel.config import PipelineConfig, SplitConfig

BLOCK_NAMES: tuple[str, ...] = ("train", "purge_1", "valid_v1", "valid_v2", "purge_2", "test", "post_break")
OUTSIDE = "outside"
MODEL_BLOCKS: tuple[str, ...] = ("train", "valid_v1", "valid_v2", "test", "post_break")   # the blocks a calculation may use; the purges are not among them
LABELLED_COLUMNS: tuple[str, ...] = (
    "order_id", "order_ts", "order_date", "regime", "shipping_mode", "scheduled_days", "outcome_known_date", "is_late", "block",
)


class SplitError(RuntimeError):
    """The labels of the split are not what the purges promise (a label of one block known after the next block starts)."""


class StaleMartError(RuntimeError):
    """``mart_labelled_orders`` was built for an earlier date than the one asked for: it would be missing labels. Rebuild it with ``rebuild_at``."""


def _split(cfg: PipelineConfig | SplitConfig) -> SplitConfig:
    return cfg.split if isinstance(cfg, PipelineConfig) else cfg


def _as_date(value: date | datetime | pd.Timestamp | str) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if isinstance(value, datetime):   # also pd.Timestamp
        return value.date()
    return value


def block(order_date: date | datetime | pd.Timestamp | str, cfg: PipelineConfig | SplitConfig) -> str:
    """The block of an order created on `order_date`: one of ``BLOCK_NAMES`` or ``outside``. The edges come from ``[split]`` and are inclusive."""
    s = _split(cfg)
    day = _as_date(order_date)
    if s.train_start <= day <= s.train_end:
        return "train"
    if s.train_end < day < s.valid_start:
        return "purge_1"
    if s.valid_start <= day < s.valid_v2_start:
        return "valid_v1"
    if s.valid_v2_start <= day <= s.valid_end:
        return "valid_v2"
    if s.valid_end < day < s.test_start:
        return "purge_2"
    if s.test_start <= day <= s.test_end:
        return "test"
    if s.post_break_start <= day <= s.post_break_end:
        return "post_break"
    return OUTSIDE


def blocks(labelled: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The labelled orders by block of the model (``MODEL_BLOCKS`` that have orders); purged and outside orders are in none of them."""
    if "block" not in labelled.columns:
        raise ValueError("los pedidos con etiqueta necesitan la columna block (salen de labelled_orders)")
    return {name: labelled[labelled["block"] == name] for name in MODEL_BLOCKS if (labelled["block"] == name).any()}


def check_label_timing(labelled: pd.DataFrame, cfg: PipelineConfig) -> None:
    """AC15: every training label is known before the validation starts and every validation label by the launch. Raises ``SplitError`` otherwise."""
    s = cfg.split
    train = labelled[labelled["block"] == "train"]
    late = train[train["outcome_known_date"] >= pd.Timestamp(s.valid_start)]
    if len(late):
        raise SplitError(
            f"{len(late)} etiquetas del entrenamiento se conocen el {s.valid_start} o después (por ejemplo, el pedido {int(late['order_id'].iloc[0])}): "
            f"la purga de {s.purge_days} días no alcanza"
        )
    valid = labelled[labelled["block"].isin(["valid_v1", "valid_v2"])]
    late = valid[valid["outcome_known_date"] > pd.Timestamp(cfg.deliveries.launch_date)]
    if len(late):
        raise SplitError(
            f"{len(late)} etiquetas de la validación se conocen después del lanzamiento ({cfg.deliveries.launch_date}) "
            f"(por ejemplo, el pedido {int(late['order_id'].iloc[0])}): la purga de {s.purge_days} días no alcanza"
        )


def labelled_orders(conn: sqlite3.Connection, as_of: date, cfg: PipelineConfig) -> pd.DataFrame:
    """The labelled orders for the calculation date `as_of`: ``LABELLED_COLUMNS``, one row per non-cancelled order whose outcome is known on that date.

    ``order_date`` and ``outcome_known_date`` are timestamps and ``is_late`` an integer (0 or 1). The purged orders are included, with their block, and
    ``blocks`` leaves them out. Nothing is written. A mart built for a later date is cut back to `as_of`; one built for an earlier date raises
    ``StaleMartError`` (the number of rows is compared with what the raw tables hold for the date).
    """
    day = as_of.isoformat()
    frame = pd.read_sql_query(
        f"SELECT {', '.join(LABELLED_COLUMNS)} FROM mart_labelled_orders WHERE outcome_known_date <= ? AND order_date <= ? ORDER BY order_id", conn, params=(day, day),
    )
    expected = conn.execute(
        "SELECT COUNT(*) FROM raw_outcomes AS r JOIN raw_orders AS o ON o.order_id = r.order_id WHERE r.is_cancelled = 0 AND r.outcome_known_date <= ? AND o.order_date <= ?",
        (day, day),
    ).fetchone()[0]
    if len(frame) != expected:
        raise StaleMartError(
            f"mart_labelled_orders tiene {len(frame)} pedidos con etiqueta conocida al {day} y los datos crudos tienen {expected}: "
            "el mart se construyó para otra fecha; reconstruirlo con warehouse.rebuild_at(conn, as_of, cfg)"
        )
    frame["order_date"] = pd.to_datetime(frame["order_date"], format="%Y-%m-%d")
    frame["outcome_known_date"] = pd.to_datetime(frame["outcome_known_date"], format="%Y-%m-%d")
    frame["is_late"] = frame["is_late"].astype("int64")
    return frame


def block_counts(labelled: pd.DataFrame) -> Mapping[str, int]:
    """Orders per block of ``BLOCK_NAMES`` (zero where there are none), plus ``outside`` if any."""
    counts = labelled["block"].value_counts()
    result = {name: int(counts.get(name, 0)) for name in BLOCK_NAMES}
    if counts.get(OUTSIDE, 0):
        result[OUTSIDE] = int(counts[OUTSIDE])
    return result
