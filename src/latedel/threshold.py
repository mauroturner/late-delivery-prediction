"""The alert threshold chosen by cost, and how it changes with the cost (RF15, AC19, decision D14).

The net benefit of flagging an order is ``is_late x late_cost x effect - alert_cost``; of not flagging it, 0. The threshold is the value of the grid
(``threshold_min`` to ``threshold_max``, every ``threshold_step``) that maximizes the net benefit **on V2**; on a tie, the largest (fewer alerts). The costs are
declared assumptions, in generic units (D14): what decides the threshold is their quotient, the break-even ``alert_cost / (late_cost x effect)``. F1 is never
used: this module and ``model.py`` do not import it (a test reads the source).

``sensitivity`` repeats the choice for the model, B2 and B5 at each break-even of the declared range, by rescaling ``alert_cost`` to ``b x late_cost x effect``, and
reports the net benefit per 1,000 orders and the share flagged on V2 and, when the test is evaluated, on the test with the threshold chosen on V2.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from latedel.config import CostsConfig, PipelineConfig

VALID_V2 = "valid_v2"
TEST = "test"
TIE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Costs:
    """The assumed costs, in generic units: what an alert costs, what a late order costs and the share of that cost an alert avoids."""

    alert_cost: float
    late_cost: float
    effect: float

    def __post_init__(self) -> None:
        for name in ("alert_cost", "late_cost"):
            value = getattr(self, name)
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name}: se esperaba un número positivo, se recibió {value!r}")
        if not np.isfinite(self.effect) or not 0 < self.effect <= 1:
            raise ValueError(f"effect: se esperaba un número en (0, 1], se recibió {self.effect!r}")

    @classmethod
    def from_config(cls, cfg: CostsConfig) -> Costs:
        return cls(cfg.alert_cost, cfg.late_cost, cfg.effect)

    def break_even(self) -> float:
        """The probability of a late delivery above which an alert pays: ``alert_cost / (late_cost x effect)``."""
        return self.alert_cost / (self.late_cost * self.effect)

    def at_break_even(self, break_even: float) -> Costs:
        """The same late cost and effect with the alert cost that gives `break_even`."""
        return Costs(break_even * self.late_cost * self.effect, self.late_cost, self.effect)


def threshold_grid(cfg: CostsConfig) -> np.ndarray:
    """The thresholds tried: ``threshold_min`` to ``threshold_max`` in steps of ``threshold_step``, rounded to ten decimals."""
    return _steps(cfg.threshold_min, cfg.threshold_max, cfg.threshold_step)


def break_even_grid(cfg: CostsConfig) -> np.ndarray:
    """The break-evens of the sensitivity: ``sensitivity_min`` to ``sensitivity_max`` in steps of ``sensitivity_step`` (17 by default)."""
    return _steps(cfg.sensitivity_min, cfg.sensitivity_max, cfg.sensitivity_step)


def _steps(low: float, high: float, step: float) -> np.ndarray:
    count = int(np.floor((high - low) / step + 1e-9)) + 1
    return np.round(low + step * np.arange(count), 10)


def _check(p, y) -> tuple[np.ndarray, np.ndarray]:
    p, y = np.asarray(p, dtype="float64"), np.asarray(y)
    if p.ndim != 1 or p.shape != y.shape:
        raise ValueError(f"los puntajes y las etiquetas deben ser vectores del mismo largo; se recibió {p.shape} y {y.shape}")
    if len(p) == 0:
        raise ValueError("no hay pedidos para elegir el umbral")
    if not np.isfinite(p).all():
        raise ValueError("hay puntajes que no son finitos")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("las etiquetas deben ser 0 o 1")
    return p, y.astype("int64")


def net_benefit(y: np.ndarray, flagged: np.ndarray, costs: Costs) -> float:
    """The total net benefit of flagging the orders in `flagged`: ``sum over flagged (is_late x late_cost x effect - alert_cost)``; not flagged add 0."""
    y = np.asarray(y)
    flagged = np.asarray(flagged, dtype=bool)
    if y.shape != flagged.shape or y.ndim != 1:
        raise ValueError(f"las etiquetas y las marcas deben ser vectores del mismo largo; se recibió {y.shape} y {flagged.shape}")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("las etiquetas deben ser 0 o 1")
    return float(y[flagged].sum() * costs.late_cost * costs.effect - flagged.sum() * costs.alert_cost)


def _net_benefit_by_threshold(p: np.ndarray, y: np.ndarray, costs: Costs, grid: np.ndarray) -> np.ndarray:
    flags = p[:, None] >= grid[None, :]
    late_flagged = (flags & (y[:, None] == 1)).sum(axis=0)
    return late_flagged * costs.late_cost * costs.effect - flags.sum(axis=0) * costs.alert_cost


def choose_threshold(p: np.ndarray, y: np.ndarray, costs: Costs, grid: np.ndarray) -> float:
    """The value of `grid` that maximizes the net benefit of flagging ``p >= t`` for the labels `y`; on a tie, the largest. `p` and `y` are those of V2."""
    p, y = _check(p, y)
    grid = np.asarray(grid, dtype="float64")
    if grid.ndim != 1 or len(grid) == 0:
        raise ValueError("la grilla de umbrales no puede estar vacía")
    benefit = _net_benefit_by_threshold(p, y, costs, grid)
    best = np.flatnonzero(benefit >= benefit.max() - TIE_TOLERANCE)
    return float(grid[best].max())


@dataclass(frozen=True)
class ThresholdChoice:
    """The threshold chosen on V2 with the figures that go with it."""

    threshold: float
    break_even: float
    net_benefit: float
    net_benefit_per_1000: float
    flagged_share: float
    orders: int


def choose_on_v2(scored: pd.DataFrame, costs: Costs, grid: np.ndarray) -> ThresholdChoice:
    """Choose the threshold from the V2 rows of `scored` (columns ``block``, ``risk`` and ``is_late``); rows of any other block are never read."""
    for column in ("block", "risk", "is_late"):
        if column not in scored.columns:
            raise ValueError(f"los puntajes necesitan la columna {column}")
    v2 = scored.loc[scored["block"] == VALID_V2]
    if v2.empty:
        raise ValueError("no hay pedidos de V2 para elegir el umbral")
    p, y = _check(v2["risk"], v2["is_late"])
    t = choose_threshold(p, y, costs, grid)
    flagged = p >= t
    benefit = net_benefit(y, flagged, costs)
    return ThresholdChoice(t, costs.break_even(), benefit, benefit / len(y) * 1000, float(flagged.mean()), len(y))


def v2_threshold(valid_v2: pd.DataFrame, cfg: PipelineConfig) -> ThresholdChoice:
    """The threshold of the model trained at the launch: chosen on its V2 risks (``TrainedModel.valid_v2``) with the costs and the grid of ``[costs]``."""
    return choose_on_v2(valid_v2.assign(block=VALID_V2), Costs.from_config(cfg.costs), threshold_grid(cfg.costs))


SENSITIVITY_COLUMNS = ("break_even", "method", "scope", "threshold", "nb_total", "nb_per_1000", "flagged_share", "orders")


def sensitivity(
    scores_v2: Mapping[str, np.ndarray], y_v2: np.ndarray, scores_test: Mapping[str, np.ndarray] | None, y_test: np.ndarray | None, cfg: PipelineConfig,
) -> pd.DataFrame:
    """The net benefit of each method at each break-even of the declared range, one row per break-even, method and scope.

    For every ``b`` in ``break_even_grid`` the alert cost is rescaled to ``b x late_cost x effect`` and, for each method in `scores_v2` (the model, B2, B5),
    the threshold is chosen on V2 (scope ``valid_v2``). When `scores_test` is given, that threshold is applied to the test scores of the same method (scope
    ``test``), whose figures are per 1,000 orders of the test. The test is never used to choose anything.
    """
    base = Costs.from_config(cfg.costs)
    grid = threshold_grid(cfg.costs)
    if (scores_test is None) != (y_test is None):
        raise ValueError("los puntajes y las etiquetas de la prueba van juntos")
    if scores_test is not None and set(scores_test) != set(scores_v2):
        raise ValueError("la prueba y V2 deben tener los mismos métodos")
    rows = []
    for b in break_even_grid(cfg.costs):
        costs = base.at_break_even(float(b))
        for method, p_v2 in scores_v2.items():
            p, y = _check(p_v2, y_v2)
            t = choose_threshold(p, y, costs, grid)
            for scope, p_scope, y_scope in ((VALID_V2, p, y), (TEST, scores_test[method] if scores_test else None, y_test)):
                if p_scope is None:
                    continue
                p_scope, y_scope = _check(p_scope, y_scope)
                flagged = p_scope >= t
                benefit = net_benefit(y_scope, flagged, costs)
                rows.append((float(b), method, scope, t, benefit, benefit / len(y_scope) * 1000, float(flagged.mean()), len(y_scope)))
    return pd.DataFrame(rows, columns=list(SENSITIVITY_COLUMNS))
