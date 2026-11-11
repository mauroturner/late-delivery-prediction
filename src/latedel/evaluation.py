"""Evaluation of the frozen model against the simple rules, the paired bootstrap, the criteria C01 to C10 and what is stored of them (RF19, AC23, decisions D16-D18).

``evaluate_block`` measures, on one block of the split and for one calculation date, the model, the rules B0 to B4 and the two rates B2 and B5: AUC, PR-AUC,
Brier, ECE, bias, the share flagged, precision, recall and the net benefit, the reduced ROC curve and the calibration by bin. The model's risks are the ones that
the frozen model *recorded* in ``model_scores`` when each order was new (the test and the post-break block) or, on V2, the ones it gives now when it predicts
them (V2 is never scored in use). The rates of the baselines are the registered ones (``baseline_rates``), fitted on the training block at the launch.

``paired_bootstrap`` resamples the orders with replacement, the same indices for both scores, and takes the 2.5 and 97.5 percentiles of the difference of AUCs
(D18). ``evaluate_criteria`` turns a result of the test into C01-C09 with fixed Spanish verdicts, and ``post_break_criterion`` the informative C10. A criterion
that cannot be computed is *not passed*, with the reason, never approved (lesson D47 of project 02).

``evaluate`` is the body of the task ``evaluate``: V2 every time; the test once, when the calculation date reaches its end plus the purge, writing the part of the
test of the seal (``write_test_seal``: a repetition with the sealed configuration must reproduce it, another configuration is ``post_seal`` and never replaces it);
the post-break block when its own end plus the purge is reached. Nothing here is run on the real test block before U25 (D17).
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_curve

from latedel.baselines import BASELINE_IDS, RULE_THRESHOLD, BaselineRates, RateTable
from latedel.config import PipelineConfig
from latedel.features import build_features, read_order_frames
from latedel.launch import RegisteredModel, load_registered_model
from latedel.model import importance as model_importance
from latedel.model import predict_risk
from latedel.registry import SealError, SealMismatchError, read_seal, seal_state, write_test_seal
from latedel.split import labelled_orders
from latedel.threshold import Costs, choose_threshold, net_benefit, sensitivity, threshold_grid
from latedel.warehouse import apply_schema

logger = logging.getLogger(__name__)

MODEL = "model"
METHODS: tuple[str, ...] = (MODEL, *BASELINE_IDS)
PROBABILISTIC: tuple[str, ...] = (MODEL, "b2_mode", "b5_mode_noon")   # the methods whose score is a probability: Brier, ECE, bias, ROC and calibration
METHOD_LABELS_ES: dict[str, str] = {
    MODEL: "Modelo", "b0_all_late": "B0 todo tarde", "b1_first_class": "B1 solo First Class", "b2_mode": "B2 tasa de retraso por modo",
    "b3_scheduled_le2": "B3 días programados 2 o menos", "b4_standard_only": "B4 solo Standard Class (control negativo)", "b5_mode_noon": "B5 tasa por modo y hora del pedido",
}
SCOPES: tuple[str, ...] = ("valid_v2", "test", "post_break")
SEAL_LABELS: tuple[str, ...] = ("sealed", "post_seal")
METRICS: tuple[str, ...] = (
    "orders", "late_rate", "auc", "pr_auc", "brier", "ece", "bias", "threshold", "flagged_share", "precision", "recall", "nb_total", "nb_per_1000", "topk_k", "topk_late",
)
FEATURE_ES: dict[str, str] = {
    "shipping_mode": "Modo de envío", "scheduled_days": "Días programados", "payment_type": "Tipo de pago", "market": "Mercado", "order_region": "Región del pedido",
    "order_country": "País del pedido", "customer_segment": "Segmento del cliente", "customer_country": "País del cliente", "main_department": "Departamento principal",
    "main_category": "Categoría principal", "main_product": "Producto principal", "n_lines": "Número de líneas", "n_departments": "Número de departamentos",
    "units": "Unidades", "gross_sales": "Ventas brutas", "item_total": "Total del pedido", "discount": "Descuento", "max_discount_rate": "Mayor tasa de descuento",
    "order_hour": "Hora del pedido", "order_weekday": "Día de la semana", "order_day": "Día del mes", "order_month": "Mes",
}
SEEN_ES = ("sí", "en parte", "no")
TIE = 1e-9


class EvaluationError(RuntimeError):
    """A block cannot be evaluated: it is incomplete on the calculation date, or an order of it has no score."""


# --- AUC and the paired bootstrap --------------------------------------------------------------------------------------------------------------------


class _Auc:
    """The AUC of one score for any multiplicity of the orders: sorted once, so that a resample costs one pass and no sort (ties are counted by halves)."""

    def __init__(self, score: np.ndarray, y: np.ndarray) -> None:
        self.order = np.argsort(score, kind="stable")
        ordered = np.asarray(score)[self.order]
        self.starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]])
        self.y = np.asarray(y, dtype="float64")[self.order]

    def auc(self, weights: np.ndarray) -> float:
        w = weights[self.order]
        positives = np.add.reduceat(w * self.y, self.starts)
        negatives = np.add.reduceat(w * (1.0 - self.y), self.starts)
        n_pos, n_neg = positives.sum(), negatives.sum()
        if n_pos == 0 or n_neg == 0:
            return math.nan
        return float((positives * (np.cumsum(negatives) - negatives + 0.5 * negatives)).sum() / (n_pos * n_neg))


def auc_score(y: Sequence[int] | np.ndarray, score: Sequence[float] | np.ndarray) -> float:
    """The area under the ROC curve (ties counted by halves, like ``sklearn.metrics.roc_auc_score``); NaN if there is a single class."""
    y, score = np.asarray(y), np.asarray(score, dtype="float64")
    if len(y) != len(score):
        raise ValueError(f"las etiquetas y los puntajes deben tener el mismo largo; se recibió {len(y)} y {len(score)}")
    return _Auc(score, y).auc(np.ones(len(y)))


@dataclass(frozen=True)
class Bootstrap:
    """The paired bootstrap of the difference of two AUCs: the difference on the orders, the 95 % interval and how many resamples were discarded (one class)."""

    diff: float
    ci_low: float | None
    ci_high: float | None
    resamples: int       # asked for
    discarded: int       # resamples with a single class
    seed: int


def paired_bootstrap(y: Sequence[int] | np.ndarray, s_a: Sequence[float] | np.ndarray, s_b: Sequence[float] | np.ndarray, n: int, seed: int) -> Bootstrap:
    """AUC(`s_a`) - AUC(`s_b`) with its 95 % interval: `n` resamples of the orders with replacement, the *same* indices for both scores, drawn from
    ``numpy.random.default_rng(seed)``; the interval is the 2.5 and 97.5 percentiles of the differences. A resample with a single class is discarded and counted (D18).
    """
    y = np.asarray(y)
    s_a, s_b = np.asarray(s_a, dtype="float64"), np.asarray(s_b, dtype="float64")
    if not (len(y) == len(s_a) == len(s_b)) or len(y) == 0:
        raise ValueError("las etiquetas y los dos puntajes deben tener el mismo largo y no estar vacíos")
    if n < 1:
        raise ValueError(f"el número de remuestras debe ser positivo; se recibió {n}")
    ka, kb = _Auc(s_a, y), _Auc(s_b, y)
    rng = np.random.default_rng(seed)
    diffs, discarded = [], 0
    for _ in range(n):
        weights = np.bincount(rng.integers(0, len(y), len(y)), minlength=len(y)).astype("float64")
        a, b = ka.auc(weights), kb.auc(weights)
        if math.isnan(a):
            discarded += 1
            continue
        diffs.append(a - b)
    point = ka.auc(np.ones(len(y))) - kb.auc(np.ones(len(y)))
    if not diffs:
        return Bootstrap(point, None, None, n, discarded, seed)
    low, high = np.percentile(diffs, [2.5, 97.5])
    return Bootstrap(float(point), float(low), float(high), n, discarded, seed)


# --- the metrics of one score ----------------------------------------------------------------------------------------------------------------------------


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int) -> pd.DataFrame:
    """``bin``, ``p_mean``, ``observed`` and ``orders`` of the non-empty bins of equal width in [0, 1] (a probability of 1 goes in the last one)."""
    p, y = np.asarray(p, dtype="float64"), np.asarray(y)
    index = np.minimum((p * bins).astype("int64"), bins - 1)
    rows = [(b, float(p[index == b].mean()), float(y[index == b].mean()), int((index == b).sum())) for b in range(bins) if (index == b).any()]
    return pd.DataFrame(rows, columns=["bin", "p_mean", "observed", "orders"])


def expected_calibration_error(p: np.ndarray, y: np.ndarray, bins: int) -> float:
    """``sum over bins of (orders in the bin / orders) x |mean p in the bin - observed rate in the bin|`` with `bins` bins of equal width in [0, 1]."""
    table = calibration_table(p, y, bins)
    return float((table["orders"] / table["orders"].sum() * (table["p_mean"] - table["observed"]).abs()).sum())


def roc_table(y: np.ndarray, score: np.ndarray, points: int) -> pd.DataFrame:
    """The ROC curve reduced to at most `points` points (the first and the last always kept), as ``point``, ``fpr`` and ``tpr``."""
    if len(set(np.asarray(y).tolist())) < 2:
        return pd.DataFrame({"point": np.empty(0, dtype="int64"), "fpr": np.empty(0), "tpr": np.empty(0)})
    fpr, tpr, _ = roc_curve(y, score)
    if len(fpr) > points:
        keep = np.unique(np.linspace(0, len(fpr) - 1, points).round().astype("int64"))
        fpr, tpr = fpr[keep], tpr[keep]
    return pd.DataFrame({"point": np.arange(len(fpr)), "fpr": fpr, "tpr": tpr})


def method_metrics(y: np.ndarray, score: np.ndarray, flagged: np.ndarray, threshold: float, costs: Costs, *, probabilistic: bool, ece_bins: int) -> dict[str, float | None]:
    """The metrics of one method. `flagged` are the orders it flags; a metric that is not defined (precision with nothing flagged, recall with no late order) is None."""
    y, score, flagged = np.asarray(y), np.asarray(score, dtype="float64"), np.asarray(flagged, dtype=bool)
    n, late, flags = len(y), int(y.sum()), int(flagged.sum())
    true_positives = int(y[flagged].sum())
    benefit = net_benefit(y, flagged, costs)
    out: dict[str, float | None] = {
        "orders": float(n), "late_rate": late / n, "auc": _none(auc_score(y, score)), "pr_auc": float(average_precision_score(y, score)) if late else None,
        "brier": None, "ece": None, "bias": None, "threshold": float(threshold), "flagged_share": flags / n,
        "precision": true_positives / flags if flags else None, "recall": true_positives / late if late else None,
        "nb_total": benefit, "nb_per_1000": benefit / n * 1000,
    }
    if probabilistic:
        out["brier"] = float(np.mean((score - y) ** 2))
        out["ece"] = expected_calibration_error(score, y, ece_bins)
        out["bias"] = float(score.mean() - y.mean())
    return out


def _none(value: float) -> float | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else value


# --- the frame of one block ------------------------------------------------------------------------------------------------------------------------------


def block_dates(block: str, cfg: PipelineConfig) -> tuple[date, date]:
    """First and last order date of a block of the model (``valid_v1`` ends the day before V2 starts)."""
    s = cfg.split
    edges = {
        "train": (s.train_start, s.train_end), "valid_v1": (s.valid_start, s.valid_v2_start - timedelta(days=1)), "valid_v2": (s.valid_v2_start, s.valid_end),
        "test": (s.test_start, s.test_end), "post_break": (s.post_break_start, s.post_break_end),
    }
    if block not in edges:
        raise ValueError(f"bloque desconocido: {block!r}; se esperaba uno de {', '.join(edges)}")
    return edges[block]


def registered_baseline_rates(conn: sqlite3.Connection, cfg: PipelineConfig) -> BaselineRates:
    """The rates of B2 and B5 as the launch registered them in ``baseline_rates`` (fitted on the training block), not recomputed from any label."""
    apply_schema(conn)
    rows = conn.execute("SELECT baseline, cell, rate, train_orders FROM baseline_rates ORDER BY baseline, cell").fetchall()
    b2 = {(cell,): (rate, n) for baseline, cell, rate, n in rows if baseline == "b2_mode"}
    b5 = {tuple(cell.split("|")): (rate, n) for baseline, cell, rate, n in rows if baseline == "b5_mode_noon"}
    if not b2 or not b5:
        raise EvaluationError("no hay tasas de líneas base registradas: el modelo se registra en el lanzamiento")
    total = sum(n for _rate, n in b2.values())
    overall = sum(rate * n for rate, n in b2.values()) / total
    parent = RateTable(("shipping_mode",), {k: r for k, (r, _n) in b2.items()}, {k: n for k, (_r, n) in b2.items()}, overall)
    child = RateTable(("shipping_mode", "band"), {k: r for k, (r, _n) in b5.items()}, {k: n for k, (_r, n) in b5.items()}, overall, parent)
    return BaselineRates(parent, child, cfg.features.noon_hour, total, overall)


def _features(conn: sqlite3.Connection, ids: Sequence[int], feature_set: str) -> pd.DataFrame:
    orders, lines = read_order_frames(conn)
    wanted = set(int(i) for i in ids)
    return build_features(orders[orders["order_id"].isin(wanted)], lines[lines["order_id"].isin(wanted)], feature_set).loc[sorted(wanted)]


def _check_complete(conn: sqlite3.Connection, block: str, cfg: PipelineConfig, as_of: date) -> None:
    """Every order created in the block must have its outcome loaded and known on `as_of`; otherwise the block is not ready to be measured."""
    first, last = block_dates(block, cfg)
    open_orders = conn.execute(
        "SELECT COUNT(*) FROM raw_orders AS o LEFT JOIN raw_outcomes AS r ON r.order_id = o.order_id WHERE o.order_date >= ? AND o.order_date <= ? "
        "AND (r.order_id IS NULL OR r.outcome_known_date > ?)",
        (first.isoformat(), last.isoformat(), as_of.isoformat()),
    ).fetchone()[0]
    if open_orders:
        raise EvaluationError(f"el bloque {block} no está completo al {as_of}: {open_orders} pedidos todavía no tienen su resultado conocido")


def block_frame(conn: sqlite3.Connection, block: str, cfg: PipelineConfig, *, as_of: date, registered: RegisteredModel, rates: BaselineRates) -> pd.DataFrame:
    """The labelled orders of `block` known on `as_of` with the risk of the model and the score of every baseline, ordered by ``order_id``.

    On ``valid_v2`` the risk is the one the frozen model predicts now; in the other blocks it is the one recorded in ``model_scores`` when the order was new
    (an order of the block without a score is an ``EvaluationError``). The block must be complete on `as_of`.
    """
    _check_complete(conn, block, cfg, as_of)
    labelled = labelled_orders(conn, as_of, cfg)
    frame = labelled[labelled["block"] == block].reset_index(drop=True)
    if frame.empty:
        raise EvaluationError(f"no hay pedidos con etiqueta en el bloque {block} al {as_of}")
    if block == "valid_v2":
        frame["risk"] = predict_risk(registered.model, _features(conn, frame["order_id"], registered.model.feature_set)).astype("float64")
    else:
        scores = pd.read_sql_query("SELECT order_id, risk FROM model_scores WHERE as_of_date <= ?", conn, params=(as_of.isoformat(),))
        frame = frame.merge(scores, on="order_id", how="left", validate="one_to_one")
        if frame["risk"].isna().any():
            raise EvaluationError(f"{int(frame['risk'].isna().sum())} pedidos del bloque {block} no tienen puntaje: se puntúan cuando son nuevos, nunca después")
    for name, values in rates.scores(frame).items():
        frame[name] = values
    return frame[["order_id", "order_ts", "shipping_mode", "scheduled_days", "is_late", "risk", *BASELINE_IDS]]


# --- the result of one block --------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationResult:
    """The measures of one block on one calculation date. ``thresholds`` is the cut each method flags at: the model's registered one, 0.5 for the rules and B2, and
    the threshold of B5 chosen on V2. ``sensitivity`` has the scope(s) it was computed on (V2; V2 and the test when the block is the test)."""

    scope: str
    as_of: date
    orders: int
    metrics: dict[str, dict[str, float | None]]
    thresholds: dict[str, float]
    roc: pd.DataFrame
    calibration: pd.DataFrame
    bootstrap: dict[str, Bootstrap]
    sensitivity: pd.DataFrame
    costs: Costs = field(default=Costs(1.0, 1.0, 1.0))


def _v2_thresholds(v2: pd.DataFrame, cfg: PipelineConfig, model_threshold: float) -> dict[str, float]:
    costs, grid = Costs.from_config(cfg.costs), threshold_grid(cfg.costs)
    chosen = choose_threshold(v2["b5_mode_noon"].to_numpy(), v2["is_late"].to_numpy(), costs, grid)
    return {MODEL: float(model_threshold), **{name: RULE_THRESHOLD for name in BASELINE_IDS}, "b5_mode_noon": chosen}


def evaluate_frame(frame: pd.DataFrame, scope: str, cfg: PipelineConfig, *, v2: pd.DataFrame, model_threshold: float, as_of: date) -> EvaluationResult:
    """Measure the frame of a block (``block_frame``). `v2` is the frame of V2: it fixes the threshold of B5 and, for the test, the thresholds of the sensitivity."""
    costs = Costs.from_config(cfg.costs)
    thresholds = _v2_thresholds(v2, cfg, model_threshold)
    y = frame["is_late"].to_numpy()
    scores = {MODEL: frame["risk"].to_numpy(), **{name: frame[name].to_numpy() for name in BASELINE_IDS}}
    flags = {name: scores[name] >= thresholds[name] for name in METHODS}
    metrics = {
        name: method_metrics(y, scores[name], flags[name], thresholds[name], costs, probabilistic=name in PROBABILISTIC, ece_bins=cfg.evaluation.calibration_bins)
        for name in METHODS
    }
    # C08: with the same number of alerts as B2 at 0.5, the k orders of highest risk of the model (ties by creation time, then order number) against the k that B2 flags
    k = int(flags["b2_mode"].sum())
    ranked = frame.assign(_ts=frame["order_ts"]).sort_values(["risk", "_ts", "order_id"], ascending=[False, True, True], kind="stable")
    metrics[MODEL]["topk_k"], metrics["b2_mode"]["topk_k"] = float(k), float(k)
    metrics[MODEL]["topk_late"] = float(ranked["is_late"].iloc[:k].sum())
    metrics["b2_mode"]["topk_late"] = float(y[flags["b2_mode"]].sum())

    bootstrap: dict[str, Bootstrap] = {}
    if scope == "test":
        for other in ("b2_mode", "b5_mode_noon"):
            bootstrap[f"model_vs_{other}"] = paired_bootstrap(y, scores[MODEL], scores[other], cfg.evaluation.bootstrap_resamples, cfg.evaluation.bootstrap_seed)
    v2_scores = {MODEL: v2["risk"].to_numpy(), "b2_mode": v2["b2_mode"].to_numpy(), "b5_mode_noon": v2["b5_mode_noon"].to_numpy()}
    on_test = scope == "test"
    table = sensitivity(
        v2_scores, v2["is_late"].to_numpy(), {m: scores[m] for m in v2_scores} if on_test else None, y if on_test else None, cfg,
    )
    roc = pd.concat([roc_table(y, scores[m], cfg.evaluation.roc_points).assign(method=m) for m in PROBABILISTIC], ignore_index=True)
    calibration = pd.concat([calibration_table(scores[m], y, cfg.evaluation.calibration_bins).assign(method=m) for m in PROBABILISTIC], ignore_index=True)
    return EvaluationResult(scope, as_of, len(frame), metrics, thresholds, roc, calibration, bootstrap, table, costs)


def evaluate_block(conn: sqlite3.Connection, block: str, cfg: PipelineConfig, *, as_of: date) -> EvaluationResult:
    """Evaluate `block` (``valid_v2``, ``test`` or ``post_break``) on the calculation date `as_of`, with the frozen model of the registry (loaded only if its artifact
    has the registered SHA-256, M03) and the registered rates. The warehouse must hold the staging tables and the mart of a date on or after `as_of`."""
    if block not in SCOPES:
        raise ValueError(f"se evalúa uno de {', '.join(SCOPES)}; se recibió {block!r}")
    registered = load_registered_model(conn, cfg)
    rates = registered_baseline_rates(conn, cfg)
    v2 = block_frame(conn, "valid_v2", cfg, as_of=as_of, registered=registered, rates=rates)
    frame = v2 if block == "valid_v2" else block_frame(conn, block, cfg, as_of=as_of, registered=registered, rates=rates)
    return evaluate_frame(frame, block, cfg, v2=v2, model_threshold=registered.threshold, as_of=as_of)


# --- the criteria -------------------------------------------------------------------------------------------------------------------------------------------


def es(value: float | None, digits: int = 3) -> str:
    """A number in the notation of the documents: decimal comma, thousands point and a true minus sign."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/d"
    text = f"{value:,.{digits}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return text.replace("-", "−")


@dataclass(frozen=True)
class Criterion:
    criterion_id: str
    name_es: str
    value: float | None
    baseline_value: float | None
    ci_low: float | None
    ci_high: float | None
    threshold_es: str
    passed: bool | None          # None only for C10, which has no threshold
    seen_by_research: str
    seen_by_planner: str
    verdict_es: str

    def seal_entry(self) -> dict[str, Any]:
        """The entry of the part of the test of the seal (``id``, ``value``, ``baseline_value``, ``ci_low``, ``ci_high``, ``passed``)."""
        return {"id": self.criterion_id, "value": self.value, "baseline_value": self.baseline_value, "ci_low": self.ci_low, "ci_high": self.ci_high, "passed": self.passed}


# what was seen before the thresholds were fixed (requirements 8.1, D16): by the research and by the planning agent
SEEN: dict[str, tuple[str, str]] = {
    "C01": ("sí", "en parte"), "C02": ("no", "no"), "C03": ("no", "no"), "C04": ("en parte", "no"), "C05": ("no", "no"), "C06": ("no", "no"), "C07": ("en parte", "no"),
    "C08": ("en parte", "no"), "C09": ("no", "no"), "C10": ("sí", "no"),
}
NAMES_ES: dict[str, str] = {
    "C01": "Discriminación frente a la regla por modo", "C02": "Mejora fuera del ruido frente a la regla por modo",
    "C03": "Mejora fuera del ruido frente a la regla de dos variables", "C04": "Beneficio neto frente a la regla de la investigación (B2 a 0,5)",
    "C05": "Beneficio neto frente a la regla de dos variables con su propio umbral", "C06": "Calibración en la prueba", "C07": "Brier frente a la regla por modo",
    "C08": "Con el mismo número de alertas que B2 a 0,5", "C09": "Sensibilidad a los costos", "C10": "Tramo posterior al cambio de forma (informativo)",
}


def _criterion(criterion_id: str, threshold_es: str, passed: bool | None, value, baseline, verdict: str, ci: tuple[float | None, float | None] = (None, None)) -> Criterion:
    research, planner = SEEN[criterion_id]
    return Criterion(criterion_id, NAMES_ES[criterion_id], _none(value), _none(baseline), ci[0], ci[1], threshold_es, passed, research, planner, verdict)


def _beats(a: float | None, b: float | None, *, lower_is_better: bool = False) -> bool:
    """A strict advantage (a tie does not beat); an undefined number beats nothing."""
    if a is None or b is None:
        return False
    return a < b if lower_is_better else a > b


def _unavailable(criterion_id: str, threshold_es: str, what: str) -> Criterion:
    return _criterion(criterion_id, threshold_es, False, None, None, f"El modelo no supera este criterio: no se pudo calcular ({what}).")


def evaluate_criteria(result: EvaluationResult, cfg: PipelineConfig) -> list[Criterion]:
    """C01 to C09 on the result of the test, with the thresholds of ``[criteria]`` and a fixed verdict that says «supera» or «no supera» with its numbers."""
    if result.scope != "test":
        raise ValueError(f"los criterios C01 a C09 se calculan sobre la prueba; se recibió {result.scope!r}")
    return _criteria_of(result.metrics, result.bootstrap, cfg.criteria.c06_max_ece, cfg.criteria.c06_max_bias, _c09(result))


def _criteria_of(metrics: Mapping[str, Mapping[str, Any]], bootstrap: Mapping[str, Any], max_ece: float, max_bias: float, c09: Criterion) -> list[Criterion]:
    """C01 to C09 from the metrics of the model, B2 and B5 on the test, the two bootstraps, the limits of C06 and C09: what ``evaluate_criteria`` and the rebuild from
    the seal (``criteria_from_seal``) share, so that both write the same thresholds and verdicts."""
    m, b2, b5 = metrics[MODEL], metrics["b2_mode"], metrics["b5_mode_noon"]
    out: list[Criterion] = []

    def verdict(ok: bool, text_pass: str, text_fail: str) -> str:
        return text_pass if ok else text_fail

    # C01
    ok = _beats(m["auc"], b2["auc"])
    out.append(_criterion(
        "C01", "AUC(modelo) > AUC(B2)", ok, m["auc"], b2["auc"],
        f"El modelo {'supera' if ok else 'no supera'} a la regla por modo: AUC {es(m['auc'])} frente a {es(b2['auc'])}.",
    ))
    # C02 and C03: the lower end of the interval above 0
    for criterion_id, other, label_es, name in (("C02", "b2_mode", "la regla por modo", "B2"), ("C03", "b5_mode_noon", "la regla de dos variables", "B5")):
        boot = bootstrap.get(f"model_vs_{other}")
        threshold = f"Límite inferior del intervalo del 95 % de AUC(modelo) − AUC({name}) > 0"
        if boot is None or boot.ci_low is None or math.isnan(boot.diff):
            out.append(_unavailable(criterion_id, threshold, "ninguna remuestra tiene las dos clases"))
            continue
        ok = boot.ci_low > 0
        out.append(_criterion(
            criterion_id, threshold, ok, boot.diff, 0.0,
            f"El modelo {'supera' if ok else 'no supera'} a {label_es}"
            f"{' fuera del ruido' if criterion_id == 'C02' else ''}: la diferencia de AUC es {es(boot.diff)}, con un intervalo del 95 % de {es(boot.ci_low)} a {es(boot.ci_high)}.",
            (boot.ci_low, boot.ci_high),
        ))
    # C04 and C05: net benefit per 1,000 orders
    ok = _beats(m["nb_per_1000"], b2["nb_per_1000"])
    out.append(_criterion(
        "C04", "BN(modelo, t*) > BN(B2 a 0,5)", ok, m["nb_per_1000"], b2["nb_per_1000"],
        f"El modelo {'supera' if ok else 'no supera'} a la regla de la investigación (B2 a 0,5) en beneficio neto: "
        f"{es(m['nb_per_1000'], 1)} frente a {es(b2['nb_per_1000'], 1)} u. por cada 1.000 pedidos.",
    ))
    ok = _beats(m["nb_per_1000"], b5["nb_per_1000"])
    out.append(_criterion(
        "C05", "BN(modelo, t*) > BN(B5, t*_B5), los dos umbrales elegidos en V2", ok, m["nb_per_1000"], b5["nb_per_1000"],
        f"El modelo {'supera' if ok else 'no supera'} a la regla de dos variables en beneficio neto con su propio umbral ({es(b5['threshold'], 2)}): "
        f"{es(m['nb_per_1000'], 1)} frente a {es(b5['nb_per_1000'], 1)} u. por cada 1.000 pedidos.",
    ))
    # C06
    if m["ece"] is None or m["bias"] is None:
        out.append(_unavailable("C06", f"ECE ≤ {es(max_ece, 2)} y |media de p − tasa observada| ≤ {es(max_bias, 2)}", "la calibración no está definida"))
    else:
        ok = m["ece"] <= max_ece and abs(m["bias"]) <= max_bias
        out.append(_criterion(
            "C06", f"ECE ≤ {es(max_ece, 2)} y |media de p − tasa observada| ≤ {es(max_bias, 2)}", ok, m["ece"], max_ece,
            f"El modelo {'supera' if ok else 'no supera'} el control de calibración: ECE {es(m['ece'])} frente a un máximo de {es(max_ece, 2)} "
            f"y sesgo {es(m['bias'])} frente a un máximo de {es(max_bias, 2)} en valor absoluto.",
        ))
    # C07
    ok = _beats(m["brier"], b2["brier"], lower_is_better=True)
    out.append(_criterion(
        "C07", "Brier(modelo) < Brier(B2)", ok, m["brier"], b2["brier"],
        f"El modelo {'supera' if ok else 'no supera'} a la regla por modo en Brier: {es(m['brier'], 4)} frente a {es(b2['brier'], 4)} (menor es mejor).",
    ))
    # C08
    k, model_late, b2_late = int(m["topk_k"] or 0), m["topk_late"], b2["topk_late"]
    ok = _beats(model_late, b2_late)
    out.append(_criterion(
        "C08", "Con k alertas, pedidos tarde entre los k de mayor riesgo del modelo > pedidos tarde entre los k de B2", ok, model_late, b2_late,
        f"Con el mismo número de alertas que B2 a 0,5 (k = {es(float(k), 0)}), el modelo {'supera' if ok else 'no supera'} a B2: "
        f"{es(model_late, 0)} pedidos tarde frente a {es(b2_late, 0)}.",
    ))
    # C09
    out.append(c09)
    return out


C09_THRESHOLD_ES = "En cada punto de equilibrio de 0,10 a 0,90, de a 0,05, con los umbrales elegidos en V2: BN(modelo) ≥ BN(B2)"
C09_RANGE = (0.10, 0.90)   # the break-evens of the preregistered C09 (its threshold): the seal keeps how many held and of how many, not the range


def _c09(result: EvaluationResult) -> Criterion:
    test = result.sensitivity[result.sensitivity["scope"] == "test"]
    pivot = test.pivot(index="break_even", columns="method", values="nb_per_1000") if len(test) else pd.DataFrame()
    if pivot.empty or MODEL not in pivot or "b2_mode" not in pivot:
        return _unavailable("C09", C09_THRESHOLD_ES, "no hay sensibilidad en la prueba")
    holds = (pivot[MODEL] >= pivot["b2_mode"] - TIE)
    return _c09_criterion(int(holds.sum()), len(pivot), float(pivot.index.min()), float(pivot.index.max()))


def _c09_criterion(kept: int, total: int, low: float, high: float) -> Criterion:
    ok = kept == total
    if ok:
        text = f"El modelo supera o iguala a B2 en beneficio neto en los {total} puntos de equilibrio ({es(low, 2)} a {es(high, 2)})."
    else:
        text = f"El modelo no supera a B2 en todos los puntos de equilibrio: lo iguala o supera en {kept} de {total}."
    return _criterion("C09", C09_THRESHOLD_ES, ok, float(kept), float(total), text)


def _rounded_entry(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return None if entry is None else {k: round(v, 6) if isinstance(v, float) else v for k, v in entry.items()}


def criteria_from_seal(seal: Mapping[str, Any]) -> list[Criterion]:
    """C01 to C09 as the seal keeps them, for a warehouse that has no sealed test (B3 of the evaluation of ``eef9bea``, decision D88).

    The values are the sealed ones; the thresholds and the verdicts are the ones ``evaluate_criteria`` writes, built from the sealed metrics of the model, B2 and B5, the
    sealed intervals and the C06 limits of the sealed launch. Each criterion must give back its own sealed entry (value, reference, interval and verdict to 6 decimals):
    a seal whose numbers do not give its own verdicts is a ``SealError``, never shown.
    """
    test, launch = seal["test"], seal["launch"]
    entries = {e["id"]: e for e in test["criteria"]}
    bootstrap = {
        f"model_vs_{other}": SimpleNamespace(diff=entries[cid]["value"], ci_low=entries[cid]["ci_low"], ci_high=entries[cid]["ci_high"])
        for cid, other in (("C02", "b2_mode"), ("C03", "b5_mode_noon")) if entries.get(cid, {}).get("value") is not None
    }
    c09 = entries.get("C09", {})
    rebuilt = (
        _c09_criterion(int(c09["value"]), int(c09["baseline_value"]), *C09_RANGE) if c09.get("value") is not None and c09.get("baseline_value") is not None
        else _unavailable("C09", C09_THRESHOLD_ES, "no hay sensibilidad en la prueba")
    )
    criteria = _criteria_of(test["metrics"], bootstrap, launch["criteria"]["c06_max_ece"], launch["criteria"]["c06_max_bias"], rebuilt)
    for criterion in criteria:
        if _rounded_entry(criterion.seal_entry()) != _rounded_entry(entries.get(criterion.criterion_id)):
            raise SealError(f"la parte de la prueba del sello no da su propio {criterion.criterion_id}: sus métricas y su entrada no coinciden (docs/runbook.md, R7)")
    return criteria


def post_break_criterion(result: EvaluationResult) -> Criterion:
    """C10, informative and without a threshold: the figures of the post-break block of the model, B2 and B5, and the statement that they are written."""
    if result.scope != "post_break":
        raise ValueError(f"C10 se calcula sobre el tramo posterior al cambio de forma; se recibió {result.scope!r}")
    m, b2, b5 = (result.metrics[x] for x in (MODEL, "b2_mode", "b5_mode_noon"))
    text = (
        f"Informativo, sin umbral: en el tramo posterior al cambio de forma ({es(float(result.orders), 0)} pedidos) el AUC del modelo es {es(m['auc'])}, el de B2 {es(b2['auc'])} "
        f"y el de B5 {es(b5['auc'])}; el modelo marca el {es(m['flagged_share'] * 100, 1)} % de los pedidos con precisión {es(m['precision'])} y recall {es(m['recall'])}."
    )
    return _criterion("C10", "Sin umbral: AUC, BN, % marcado, precisión y recall del modelo, B2 y B5; el criterio es que esté escrito", None, m["auc"], b2["auc"], text)


# --- storage ---------------------------------------------------------------------------------------------------------------------------------------------------


def _norm(row: Sequence[Any]) -> tuple:
    return tuple(round(v, 6) if isinstance(v, float) else v for v in row)


def _replace(conn: sqlite3.Connection, table: str, where: str, args: tuple, columns: Sequence[str], rows: list[tuple], *, once: bool) -> None:
    """Replace the rows of `table` that `where` selects with `rows`. With `once`, rows already there must be the same (to 6 decimals) and are kept: a sealed value
    is written once and a repetition that does not reproduce it is a ``SealMismatchError`` (runbook R7)."""
    existing = [_norm(r) for r in conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE {where} ORDER BY {', '.join(columns)}", args)]
    if once and existing:
        if sorted(existing, key=repr) != sorted((_norm(r) for r in rows), key=repr):
            raise SealMismatchError(f"la evaluación sellada ya guardada en {table} no coincide con la que se acaba de calcular (docs/runbook.md, R7)")
        return
    conn.execute(f"DELETE FROM {table} WHERE {where}", args)
    conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", rows)


def _db_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value.item() if isinstance(value, np.generic) else value


def store_evaluation(conn: sqlite3.Connection, result: EvaluationResult, label: str) -> None:
    """Write the metrics, ROC, calibration, bootstrap and sensitivity of `result` under the seal label `label` (``sealed`` or ``post_seal``), in one transaction.

    The sealed rows of the test are written once (a repetition must reproduce them); everything else of a scope is replaced.
    """
    if label not in SEAL_LABELS:
        raise ValueError(f"la marca del sello debe ser una de {', '.join(SEAL_LABELS)}; se recibió {label!r}")
    once = result.scope == "test" and label == "sealed"
    scope = result.scope
    metric_rows = [(scope, method, metric, label, _db_value(value)) for method, values in result.metrics.items() for metric, value in values.items()]
    roc_rows = [(scope, r.method, label, int(r.point), float(r.fpr), float(r.tpr)) for r in result.roc.itertuples()]
    cal_rows = [(scope, r.method, label, int(r.bin), float(r.p_mean), float(r.observed), int(r.orders)) for r in result.calibration.itertuples()]
    boot_rows = [(name, label, _db_value(b.diff), b.ci_low, b.ci_high, b.resamples, b.discarded, b.seed) for name, b in result.bootstrap.items()]
    conn.execute("BEGIN IMMEDIATE")
    try:
        apply_schema(conn)
        _replace(conn, "eval_metrics", "scope = ? AND seal_state = ?", (scope, label), ("scope", "method", "metric", "seal_state", "value"), metric_rows, once=once)
        _replace(conn, "eval_roc", "scope = ? AND seal_state = ?", (scope, label), ("scope", "method", "seal_state", "point", "fpr", "tpr"), roc_rows, once=once)
        _replace(conn, "eval_calibration", "scope = ? AND seal_state = ?", (scope, label), ("scope", "method", "seal_state", "bin", "p_mean", "observed", "orders"), cal_rows, once=once)
        if boot_rows:
            _replace(conn, "eval_bootstrap", "seal_state = ?", (label,), ("comparison", "seal_state", "diff", "ci_low", "ci_high", "resamples", "discarded", "seed"), boot_rows, once=once)
        for sens_scope, part in result.sensitivity.groupby("scope"):
            rows = [(float(r.break_even), r.method, sens_scope, label, float(r.threshold), float(r.nb_total), float(r.nb_per_1000), float(r.flagged_share), int(r.orders)) for r in part.itertuples()]
            _replace(
                conn, "eval_sensitivity", "scope = ? AND seal_state = ?", (sens_scope, label),
                ("break_even", "method", "scope", "seal_state", "threshold", "nb_total", "nb_per_1000", "flagged_share", "orders"), rows, once=once and sens_scope == "test",
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


CRITERIA_COLUMNS = (
    "criterion_id", "seal_state", "scope", "name_es", "value", "baseline_value", "ci_low", "ci_high", "threshold_es", "passed", "seen_by_research", "seen_by_planner", "verdict_es",
)


def store_criteria(conn: sqlite3.Connection, criteria: Sequence[Criterion], label: str) -> None:
    """Write the criteria under `label`; the sealed C01-C09 are written once, C10 is replaced."""
    if label not in SEAL_LABELS:
        raise ValueError(f"la marca del sello debe ser una de {', '.join(SEAL_LABELS)}; se recibió {label!r}")

    def row(c: Criterion) -> tuple:
        scope = "post_break" if c.criterion_id == "C10" else "test"
        return (c.criterion_id, label, scope, c.name_es, c.value, c.baseline_value, c.ci_low, c.ci_high, c.threshold_es, None if c.passed is None else int(c.passed),
                c.seen_by_research, c.seen_by_planner, c.verdict_es)

    conn.execute("BEGIN IMMEDIATE")
    try:
        apply_schema(conn)
        for scope in ("test", "post_break"):
            rows = [row(c) for c in criteria if row(c)[2] == scope]
            if rows:
                _replace(conn, "eval_criteria", "scope = ? AND seal_state = ?", (scope, label), CRITERIA_COLUMNS, rows, once=scope == "test" and label == "sealed")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def load_criteria(conn: sqlite3.Connection, label: str) -> list[Criterion]:
    """The stored criteria under `label`, C01 to C10 in order."""
    apply_schema(conn)
    rows = conn.execute(f"SELECT {', '.join(CRITERIA_COLUMNS)} FROM eval_criteria WHERE seal_state = ? ORDER BY criterion_id", (label,)).fetchall()
    return [Criterion(r[0], r[3], r[4], r[5], r[6], r[7], r[8], None if r[9] is None else bool(r[9]), r[10], r[11], r[12]) for r in rows]


def load_metrics(conn: sqlite3.Connection, label: str) -> dict[str, dict[str, dict[str, float | None]]]:
    """The stored metrics under `label`: ``scope -> method -> metric -> value``."""
    apply_schema(conn)
    out: dict[str, dict[str, dict[str, float | None]]] = {}
    for scope, method, metric, value in conn.execute("SELECT scope, method, metric, value FROM eval_metrics WHERE seal_state = ? ORDER BY scope, method, metric", (label,)):
        out.setdefault(scope, {}).setdefault(method, {})[metric] = value
    return out


def seal_payload(criteria: Sequence[Criterion], result: EvaluationResult, *, run_key: str, as_of: date, config_sha256: str) -> dict[str, Any]:
    """The part of the test of the seal: the criteria C01-C09 and the metrics of the model, B2 and B5 on the test."""
    return {
        "run_key": run_key, "as_of": as_of.isoformat(), "config_sha256": config_sha256, "criteria": [c.seal_entry() for c in criteria if c.criterion_id != "C10"],
        "metrics": {name: {k: v for k, v in result.metrics[name].items()} for name in (MODEL, "b2_mode", "b5_mode_noon")},
    }


# --- the task ----------------------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationOutcome:
    """What a run of ``evaluate`` did: the label of its V2 figures, whether the test and the post-break block were evaluated in this run, and the seal result."""

    as_of: date
    label: str                         # sealed or post_seal: the configuration of the run against the sealed one
    valid_v2: EvaluationResult
    test: EvaluationResult | None
    test_criteria: list[Criterion]
    test_seal: str | None              # written, unchanged or post_seal; None if the test was not evaluated in this run
    post_break: EvaluationResult | None
    post_break_criterion: Criterion | None


def is_test_ready(cfg: PipelineConfig, as_of: date) -> bool:
    """True when the labels of the whole test block are known by `as_of`: its end plus the purge."""
    return as_of >= cfg.split.test_end + timedelta(days=cfg.split.purge_days)


def is_post_break_ready(cfg: PipelineConfig, as_of: date) -> bool:
    return as_of >= cfg.split.post_break_end + timedelta(days=cfg.split.purge_days)


def evaluate(conn: sqlite3.Connection, cfg: PipelineConfig, *, run_key: str, as_of: date, config_sha256: str) -> EvaluationOutcome:
    """The task ``evaluate``: the figures of V2 on every run; the test, once, when `as_of` reaches its end plus the purge; the post-break block likewise.

    `config_sha256` is the fingerprint of the configuration of this run (``fit_model`` returns it). With the sealed configuration the first evaluation of the test
    writes the part of the test of the seal and the rows ``sealed``; a repetition must reproduce them (``SealMismatchError``, R7). With another configuration
    the values are stored as ``post_seal`` and the sealed ones are not touched. A test already evaluated is not recomputed: the stored criteria stay. A test that the
    seal calls another configuration although the fingerprint is the sealed one (a key outside it changed a sealed value, D89) makes the whole run ``post_seal``: its
    V2 and post-break rows too, so V2 is stored after the test is decided.
    """
    apply_schema(conn)
    state = seal_state(cfg.paths.seal, config_sha256)
    label = "sealed" if state == "match" else "post_seal"
    valid = evaluate_block(conn, "valid_v2", cfg, as_of=as_of)

    test = criteria = test_seal = None
    sealed = read_seal(cfg.paths.seal)
    stored = conn.execute("SELECT COUNT(*) FROM eval_criteria WHERE seal_state = 'sealed' AND scope = 'test'").fetchone()[0]
    already = state == "match" and sealed is not None and sealed.get("test") is not None and stored > 0   # this warehouse already did it: a rebuild has no stored rows and must reproduce the seal
    if is_test_ready(cfg, as_of) and not already:
        test = evaluate_block(conn, "test", cfg, as_of=as_of)
        criteria = evaluate_criteria(test, cfg)
        test_seal = write_test_seal(cfg.paths.seal, seal_payload(criteria, test, run_key=run_key, as_of=as_of, config_sha256=config_sha256))
        kept = "sealed" if test_seal in ("written", "unchanged") else "post_seal"
        if kept == "post_seal":
            label = "post_seal"
        store_evaluation(conn, test, kept)
        store_criteria(conn, criteria, kept)
    store_evaluation(conn, valid, label)
    post = c10 = None
    if is_post_break_ready(cfg, as_of):
        post = evaluate_block(conn, "post_break", cfg, as_of=as_of)
        c10 = post_break_criterion(post)
        store_evaluation(conn, post, label)
        store_criteria(conn, [c10], label)
    logger.info("evaluate %s: as_of=%s label=%s test=%s seal=%s post_break=%s", run_key, as_of, label, test is not None, test_seal, post is not None)
    return EvaluationOutcome(as_of, label, valid, test, criteria or [], test_seal, post, c10)


# --- the tables of the launch: importance and sensitivity -----------------------------------------------------------------------------------------------


def importance_table(conn: sqlite3.Connection, cfg: PipelineConfig, *, as_of: date) -> pd.DataFrame:
    """Permutation importance of the frozen model on V2 (``model.importance``): ``feature``, ``feature_es``, ``auc_drop`` and ``sort_order``, largest first."""
    registered = load_registered_model(conn, cfg)
    labelled = labelled_orders(conn, as_of, cfg)
    v2 = labelled[labelled["block"] == "valid_v2"].sort_values("order_id")
    if v2.empty:
        raise EvaluationError(f"no hay pedidos con etiqueta en V2 al {as_of}")
    matrix = _features(conn, v2["order_id"], registered.model.feature_set)
    table = model_importance(registered.model, matrix, v2.set_index("order_id").loc[matrix.index, "is_late"].to_numpy(), cfg)
    table["feature_es"] = table["feature"].map(FEATURE_ES)
    table["sort_order"] = np.arange(1, len(table) + 1)
    return table[["feature", "feature_es", "auc_drop", "sort_order"]]


def store_importance(conn: sqlite3.Connection, table: pd.DataFrame) -> None:
    """Replace ``feature_importance`` with `table` (written at the launch)."""
    apply_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM feature_importance")
        conn.executemany("INSERT INTO feature_importance (feature, feature_es, auc_drop, sort_order) VALUES (?, ?, ?, ?)", [(r.feature, r.feature_es, float(r.auc_drop), int(r.sort_order)) for r in table.itertuples()])
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def store_leakage(conn: sqlite3.Connection, table: pd.DataFrame) -> None:
    """Replace ``eval_leakage`` with `table` (the result of ``leakage_demo.leakage_table``)."""
    apply_schema(conn)
    columns = ("source_column", "column_es", "kind", "known_at_es", "auc_with_mode_valid", "note_es", "sort_order")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM eval_leakage")
        conn.executemany(f"INSERT INTO eval_leakage ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", [tuple(_db_value(v) for v in r) for r in table[list(columns)].itertuples(index=False)])
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def record_launch_tables(conn: sqlite3.Connection, cfg: PipelineConfig, *, as_of: date) -> dict[str, int]:
    """The tables that only the launch writes (U14 deferred them to here): the importance and the demonstration of the leakage, both measured on V2 with the
    frozen model and the training block. Returns the number of rows of each. The sensitivity is stored by ``evaluate`` with the figures of V2."""
    from latedel.leakage_demo import leakage_table

    importance = importance_table(conn, cfg, as_of=as_of)
    store_importance(conn, importance)
    leakage = leakage_table(conn, cfg, as_of=as_of)
    store_leakage(conn, leakage)
    return {"feature_importance": len(importance), "eval_leakage": len(leakage)}

