"""The simple rules the model must beat, B0 to B5, and the check of the region effect in Same Day (RF13, AC16, decisions D10 and D15).

====  ==========================================================  ======================================================================
B0    everything late                                              score 1
B1    only First Class                                             score 1 for First Class, else 0
B2    the training late rate of the shipping mode                  the rate; flags what is at 0.5 or more (the rule of the research and the site)
B3    scheduled days of 2 or fewer                                 score 1 if so, else 0
B4    negative control: only Standard Class                        score 1 for Standard Class, else 0
B5    the training late rate of the mode by time of the order      the rate of the cell (mode x before or from ``noon_hour``): 8 cells
====  ==========================================================  ======================================================================

Every rate is computed from the training block only. ``baseline_rates`` receives the labelled orders of all blocks and takes the training ones itself, so
there is no way to hand it a validation or a test label; ``fit_rates`` refuses a frame that has any other block. A cell that has no training order takes the
rate of its mode, and a mode that has none the rate of the whole training block.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import chi2, chi2_contingency

from latedel.config import PipelineConfig
from latedel.features import _TIMESTAMP_FORMAT

RULE_THRESHOLD = 0.5                      # B0 to B4 flag a score of at least this (B2 flags the modes whose training rate is 0.5 or more)
BASELINE_IDS: tuple[str, ...] = ("b0_all_late", "b1_first_class", "b2_mode", "b3_scheduled_le2", "b4_standard_only", "b5_mode_noon")
RATE_BASELINES: tuple[str, ...] = ("b2_mode", "b5_mode_noon")   # the ones with rates; the others are rules without parameters
BEFORE_NOON, FROM_NOON = "before", "from"


def order_band(order_ts: pd.Series, noon_hour: int) -> pd.Series:
    """``"before"`` or ``"from"`` (`noon_hour`) for the hour at which each order was created."""
    hours = pd.to_datetime(order_ts, format=_TIMESTAMP_FORMAT).dt.hour
    return pd.Series(np.where(hours < noon_hour, BEFORE_NOON, FROM_NOON), index=order_ts.index)


@dataclass(frozen=True)
class RateTable:
    """The late rate of each cell of `by` in the training block, and what a cell without training orders takes (its parent, or the overall rate)."""

    by: tuple[str, ...]
    rates: Mapping[tuple, float]
    orders: Mapping[tuple, int]
    overall: float
    parent: RateTable | None = field(default=None)

    def lookup(self, key: tuple) -> float:
        if key in self.rates:
            return self.rates[key]
        if self.parent is not None:
            return self.parent.lookup(key[: len(self.parent.by)])
        return self.overall


def fit_rates(train: pd.DataFrame, by: list[str]) -> RateTable:
    """The training late rate by `by` (columns of `train`, which must have ``is_late``); with more than one column, the first one is the parent."""
    if not by:
        raise ValueError("fit_rates necesita al menos una columna")
    if "is_late" not in train.columns:
        raise ValueError("fit_rates necesita la columna is_late")
    if "block" in train.columns and (train["block"] != "train").any():
        raise ValueError("fit_rates solo acepta pedidos del entrenamiento: la columna block trae otros bloques")
    if train.empty:
        raise ValueError("fit_rates necesita al menos un pedido del entrenamiento")
    grouped = train.groupby(list(by))["is_late"].agg(["mean", "size"])
    keys = [k if isinstance(k, tuple) else (k,) for k in grouped.index]
    parent = fit_rates(train, by[:1]) if len(by) > 1 else None
    return RateTable(
        tuple(by), {k: float(m) for k, m in zip(keys, grouped["mean"], strict=True)}, {k: int(n) for k, n in zip(keys, grouped["size"], strict=True)},
        float(train["is_late"].mean()), parent,
    )


def score(rates: RateTable, orders: pd.DataFrame) -> np.ndarray:
    """The rate of the cell of each order (a column per name in ``rates.by`` must be in `orders`)."""
    missing = [c for c in rates.by if c not in orders.columns]
    if missing:
        raise ValueError(f"faltan columnas para puntuar con las tasas: {missing}")
    if len(orders) == 0:
        return np.empty(0, dtype="float64")
    keys = list(zip(*(orders[c].tolist() for c in rates.by), strict=True))
    return np.array([rates.lookup(k) for k in keys], dtype="float64")


@dataclass(frozen=True)
class BaselineRates:
    """The rates of B2 and B5, fitted on the training block, and what the rules need to score any orders."""

    b2: RateTable
    b5: RateTable
    noon_hour: int
    train_orders: int
    train_late_rate: float

    def table(self) -> pd.DataFrame:
        """``baseline_rates`` of the warehouse: ``baseline``, ``cell``, ``rate``, ``train_orders`` (4 cells of B2, 8 of B5)."""
        rows = [("b2_mode", f"{mode}", rate, self.b2.orders[(mode,)]) for (mode,), rate in sorted(self.b2.rates.items())]
        rows += [("b5_mode_noon", f"{mode}|{band}", rate, self.b5.orders[(mode, band)]) for (mode, band), rate in sorted(self.b5.rates.items())]
        return pd.DataFrame(rows, columns=["baseline", "cell", "rate", "train_orders"])

    def scores(self, orders: pd.DataFrame) -> dict[str, np.ndarray]:
        """The score of each baseline for `orders` (``shipping_mode``, ``scheduled_days`` and ``order_ts``), in ``BASELINE_IDS`` order."""
        for column in ("shipping_mode", "scheduled_days", "order_ts"):
            if column not in orders.columns:
                raise ValueError(f"faltan columnas para puntuar las líneas base: {column}")
        prepared = orders.assign(band=order_band(orders["order_ts"], self.noon_hour)) if len(orders) else orders.assign(band=pd.Series(dtype=str))
        mode = orders["shipping_mode"]
        return {
            "b0_all_late": np.ones(len(orders)),
            "b1_first_class": (mode == "First Class").to_numpy(dtype="float64"),
            "b2_mode": score(self.b2, prepared),
            "b3_scheduled_le2": (orders["scheduled_days"] <= 2).to_numpy(dtype="float64"),
            "b4_standard_only": (mode == "Standard Class").to_numpy(dtype="float64"),
            "b5_mode_noon": score(self.b5, prepared),
        }

    def flags(self, orders: pd.DataFrame) -> dict[str, np.ndarray]:
        """The orders each rule flags at its own cut (a score of ``RULE_THRESHOLD`` or more). B5 gets its cost threshold elsewhere (D15)."""
        return {name: values >= RULE_THRESHOLD for name, values in self.scores(orders).items()}


def baseline_rates(labelled: pd.DataFrame, cfg: PipelineConfig) -> BaselineRates:
    """Fit B2 and B5 on the training orders of `labelled` (the frame of ``split.labelled_orders``; the other blocks are ignored, never read for their label)."""
    if "block" not in labelled.columns:
        raise ValueError("los pedidos con etiqueta necesitan la columna block (salen de split.labelled_orders)")
    train = labelled.loc[labelled["block"] == "train", ["shipping_mode", "order_ts", "is_late", "block"]]
    if train.empty:
        raise ValueError("no hay pedidos del entrenamiento para ajustar las líneas base")
    train = train.assign(band=order_band(train["order_ts"], cfg.features.noon_hour))
    return BaselineRates(
        fit_rates(train, ["shipping_mode"]), fit_rates(train, ["shipping_mode", "band"]), cfg.features.noon_hour, len(train), float(train["is_late"].mean()),
    )


# --- the region effect in Same Day, with the hour controlled (requirements section 4) ----------------------------------------------------------------


@dataclass(frozen=True)
class RegionCheck:
    """Does the region still matter in Same Day once the time of the order is controlled? Training orders only."""

    orders: int
    regions: int
    raw_chi2: float
    raw_dof: int
    raw_p: float
    controlled_chi2: float        # the sum, over the two times of the order, of the chi-square of region against late
    controlled_dof: int
    controlled_p: float
    region_vs_band_p: float       # is the region associated with the time of the order? (it would explain a raw effect)
    late_share_by_band: Mapping[str, float]


def _pearson(table: pd.DataFrame) -> tuple[float, int]:
    """Chi-square and degrees of freedom of a contingency table, after dropping its empty rows and columns; (0, 0) if no test is possible."""
    table = table.loc[table.sum(axis=1) > 0, table.sum(axis=0) > 0]
    if table.shape[0] < 2 or table.shape[1] < 2:
        return 0.0, 0
    statistic, _p, dof, _expected = chi2_contingency(table.to_numpy(), correction=False)
    return float(statistic), int(dof)


def same_day_region_check(labelled: pd.DataFrame, orders: pd.DataFrame, cfg: PipelineConfig) -> RegionCheck:
    """The check of the region effect in Same Day: raw, within each time of the order (the hour controlled), and region against the time itself.

    `labelled` is the frame of ``split.labelled_orders`` (only its training orders are read) and `orders` has ``order_id`` and ``order_region`` (the orders of
    ``features.read_order_frames``). Where the late outcome does not vary inside a time of the order (every order late, or none), that stratum has nothing
    left for the region to explain and adds 0 to the controlled statistic.
    """
    if "block" not in labelled.columns:
        raise ValueError("los pedidos con etiqueta necesitan la columna block (salen de split.labelled_orders)")
    train = labelled[(labelled["block"] == "train") & (labelled["shipping_mode"] == "Same Day")]
    train = train.merge(orders[["order_id", "order_region"]], on="order_id", how="left", validate="one_to_one")
    if train["order_region"].isna().any():
        raise ValueError("hay pedidos Same Day del entrenamiento sin región")
    train = train.assign(band=order_band(train["order_ts"], cfg.features.noon_hour))
    raw = pd.crosstab(train["order_region"], train["is_late"])
    raw_stat, raw_dof = _pearson(raw)
    controlled_stat, controlled_dof = 0.0, 0
    for _band, stratum in train.groupby("band"):
        stat, dof = _pearson(pd.crosstab(stratum["order_region"], stratum["is_late"]))
        controlled_stat += stat
        controlled_dof += dof
    band_stat, band_dof = _pearson(pd.crosstab(train["order_region"], train["band"]))
    return RegionCheck(
        orders=len(train), regions=int(train["order_region"].nunique()),
        raw_chi2=raw_stat, raw_dof=raw_dof, raw_p=float(chi2.sf(raw_stat, raw_dof)) if raw_dof else 1.0,
        controlled_chi2=controlled_stat, controlled_dof=controlled_dof, controlled_p=float(chi2.sf(controlled_stat, controlled_dof)) if controlled_dof else 1.0,
        region_vs_band_p=float(chi2.sf(band_stat, band_dof)) if band_dof else 1.0,
        late_share_by_band={str(band): float(g["is_late"].mean()) for band, g in train.groupby("band")},
    )
