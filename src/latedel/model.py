"""The model: a fixed grid of gradient-boosting candidates, selection and isotonic calibration on V1, the leak tripwire and the importance (RF14, decisions D11-D13, D36).

Everything here learns from the labelled orders of the split and nothing else:

- the candidates are fitted on the **training** block (24 of them: 3 feature sets x the 8 configurations of ``[model]``);
- the one with the lowest log loss on **V1**, uncalibrated, is selected (ties within 1e-6 go to the simplest);
- M01, the tripwire, stops the launch if the AUC of the selected candidate on V1 reaches ``leak_tripwire_auc`` (a second line of defence behind the guards of
  ``features.py``);
- the selected candidate is calibrated with isotonic regression on **V1** (``CalibratedClassifierCV(FrozenEstimator(pipeline))``); the threshold, the cost and the
  figures of validation come from **V2** and never from here.

The matrices come only from ``features.build_features`` (the leakage guards) and the labels only from ``split.labelled_orders``. ``RiskModel`` keeps the feature set
it was fitted with and ``predict_risk`` refuses a matrix whose columns are not exactly that set (M02).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.inspection import permutation_importance
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from latedel.config import ModelConfig, PipelineConfig
from latedel.features import CATEGORICAL, FEATURE_SETS, LeakageError, build_features, check_feature_matrix, read_order_frames
from latedel.split import blocks, check_label_timing, labelled_orders

TIE_TOLERANCE = 1e-6          # candidates within this log loss of the best are a tie, and the simplest of them is selected
SET_RANK: dict[str, int] = {name: rank for rank, name in enumerate(FEATURE_SETS)}   # f0 < f1 < f2
CALIBRATION_METHODS = ("isotonic",)


class BlockLabelError(ValueError):
    """A block of the split that the model learns from has the labels of a single class (all late or none late): there is nothing to fit, select, calibrate or measure."""


def require_both_classes(y: np.ndarray | pd.Series, block: str) -> None:
    """Raise ``BlockLabelError`` if the labels of `block` are all of one class: the AUC of V1 would be undefined (scikit-learn warns and answers NaN, and NaN passes the
    tripwire M01), the classifier cannot be fitted on one class and the threshold of V2 would be chosen on nothing. A block with no orders is not this error."""
    y = np.asarray(y)
    if y.size and y.min() == y.max():
        side = "todos llegaron tarde" if y[0] == 1 else "ninguno llegó tarde"
        raise BlockLabelError(
            f"el bloque {block} tiene una sola clase de etiqueta ({side}, {y.size} pedidos): no se puede ajustar, elegir, calibrar ni medir con él. "
            "Se revisan las fechas de [split] y los datos del bloque (docs/runbook.md, R5)"
        )


@dataclass(frozen=True)
class Candidate:
    """One point of the grid: a feature set and a configuration of the classifier."""

    feature_set: str
    learning_rate: float
    max_leaf_nodes: int
    min_samples_leaf: int

    def simplicity(self) -> tuple:
        """Sorts the simplest first: the smaller set, then fewer leaves, then more samples per leaf, then the lower learning rate."""
        return (SET_RANK[self.feature_set], self.max_leaf_nodes, -self.min_samples_leaf, self.learning_rate)

    def params(self) -> dict[str, float | int]:
        return {"learning_rate": self.learning_rate, "max_leaf_nodes": self.max_leaf_nodes, "min_samples_leaf": self.min_samples_leaf}


def candidates(cfg: ModelConfig, sets: Sequence[str]) -> list[Candidate]:
    """The grid in a fixed order: by set (as given), then learning rate, leaves and samples per leaf in the order of ``[model]`` (3 x 8 = 24 by default)."""
    unknown = [s for s in sets if s not in FEATURE_SETS]
    if unknown:
        raise ValueError(f"conjunto de variables desconocido: {unknown[0]!r}; se esperaba uno de {', '.join(FEATURE_SETS)}")
    return [
        Candidate(s, lr, leaves, msl)
        for s in sets for lr in cfg.learning_rate for leaves in cfg.max_leaf_nodes for msl in cfg.min_samples_leaf
    ]


def make_pipeline(c: Candidate, cfg: ModelConfig) -> Pipeline:
    """The unfitted pipeline of a candidate: an explicit ordinal encoding of the categorical columns of its set, then the classifier.

    Categories are the ones seen in fitting, in alphabetical order; one that was not seen is missing (NaN) at prediction time, which the classifier handles
    without an error. The classifier never stops early (it would split the training block at random) and is seeded.
    """
    columns = FEATURE_SETS[c.feature_set]
    categorical = [name for name in columns if name in CATEGORICAL]
    numeric = [name for name in columns if name not in CATEGORICAL]
    encoder = OrdinalEncoder(categories="auto", handle_unknown="use_encoded_value", unknown_value=np.nan, encoded_missing_value=np.nan)
    prep = ColumnTransformer([("categorical", encoder, categorical), ("numeric", "passthrough", numeric)], remainder="drop", verbose_feature_names_out=False)
    mask = [True] * len(categorical) + [False] * len(numeric)
    classifier = HistGradientBoostingClassifier(
        learning_rate=c.learning_rate, max_leaf_nodes=c.max_leaf_nodes, min_samples_leaf=c.min_samples_leaf, l2_regularization=cfg.l2_regularization,
        max_iter=cfg.max_iter, early_stopping=False, random_state=cfg.random_state, categorical_features=mask if any(mask) else None,
    )
    return Pipeline([("prep", prep), ("classifier", classifier)])


def fit_candidates(
    cands: Sequence[Candidate], cfg: ModelConfig, x_train: Mapping[str, pd.DataFrame], y_train: np.ndarray | pd.Series,
) -> list[tuple[Candidate, Pipeline]]:
    """Fit every candidate on the training matrix of its feature set (`x_train` maps a set name to the matrix of ``build_features``)."""
    fitted = []
    for c in cands:
        if c.feature_set not in x_train:
            raise ValueError(f"falta la matriz de entrenamiento del conjunto {c.feature_set}")
        pipeline = make_pipeline(c, cfg)
        pipeline.fit(x_train[c.feature_set], np.asarray(y_train))
        fitted.append((c, pipeline))
    return fitted


@dataclass(frozen=True)
class Selection:
    """The candidate with the lowest log loss on V1 and the loss of each of the others (``selection_json`` of the registry)."""

    chosen: Candidate
    pipeline: Pipeline
    losses: tuple[tuple[Candidate, float], ...]
    auc_v1: float

    @property
    def log_loss_v1(self) -> float:
        return dict(self.losses)[self.chosen]


def select(fitted: Sequence[tuple[Candidate, Pipeline]], x_v1: Mapping[str, pd.DataFrame], y_v1: np.ndarray | pd.Series) -> Selection:
    """The candidate with the lowest uncalibrated log loss on V1; within ``TIE_TOLERANCE`` of the best, the simplest. `x_v1` maps a set name to its matrix."""
    if not fitted:
        raise ValueError("no hay candidatos para elegir")
    y = np.asarray(y_v1)
    require_both_classes(y, "valid_v1")
    scored = []
    for c, pipeline in fitted:
        if c.feature_set not in x_v1:
            raise ValueError(f"falta la matriz de V1 del conjunto {c.feature_set}")
        scored.append((c, pipeline, float(log_loss(y, pipeline.predict_proba(x_v1[c.feature_set])[:, 1], labels=[0, 1]))))
    best = min(loss for _c, _p, loss in scored)
    chosen, pipeline, _loss = min((s for s in scored if s[2] <= best + TIE_TOLERANCE), key=lambda s: s[0].simplicity())
    auc = float(roc_auc_score(y, pipeline.predict_proba(x_v1[chosen.feature_set])[:, 1]))
    return Selection(chosen, pipeline, tuple((c, loss) for c, _p, loss in scored), auc)


def check_tripwire(selection: Selection, cfg: PipelineConfig) -> None:
    """M01: stop the launch if the AUC of the selected candidate on V1 reaches ``leak_tripwire_auc`` (decision D36). Raises ``LeakageError``."""
    limit = cfg.criteria.leak_tripwire_auc
    if selection.auc_v1 >= limit:
        raise LeakageError(
            f"M01: el AUC del candidato elegido en V1 es {selection.auc_v1:.4f}, igual o mayor que el umbral de {limit}: posible fuga de información "
            f"(el conjunto {selection.chosen.feature_set}). El lanzamiento se detiene (docs/runbook.md, R5)"
        )


def calibrate(pipeline: Pipeline, x_v1: pd.DataFrame, y_v1: np.ndarray | pd.Series, method: str = "isotonic") -> CalibratedClassifierCV:
    """Calibrate an already fitted pipeline with `x_v1` and `y_v1` only: ``CalibratedClassifierCV(FrozenEstimator(pipeline), method=...)``."""
    if method not in CALIBRATION_METHODS:
        raise ValueError(f"método de calibración desconocido: {method!r}; el único implementado es {', '.join(CALIBRATION_METHODS)}")
    calibrated = CalibratedClassifierCV(FrozenEstimator(pipeline), method=method)
    calibrated.fit(x_v1, np.asarray(y_v1))
    return calibrated


@dataclass(frozen=True)
class RiskModel:
    """The model in use: the selected pipeline (fitted on the training block), its isotonic calibrator (fitted on V1) and the feature set both expect."""

    feature_set: str
    candidate: Candidate
    pipeline: Pipeline
    calibrated: CalibratedClassifierCV
    method: str


def predict_risk(model: RiskModel | CalibratedClassifierCV | Pipeline, x: pd.DataFrame) -> np.ndarray:
    """The probability of a late delivery of each row of `x`, as a float array.

    For a ``RiskModel`` the columns of `x` must be exactly the ones of its feature set, in order (M02: ``LeakageError`` otherwise), and the probability is the
    calibrated one. For a bare estimator no check is made.
    """
    if isinstance(model, RiskModel):
        check_feature_matrix(x, model.feature_set)
        return model.calibrated.predict_proba(x)[:, 1]
    return model.predict_proba(x)[:, 1]


@dataclass(frozen=True)
class TrainedModel:
    """What the launch learns: the model in use, how it was selected and the calibrated risks of V2 (for the threshold and the figures of validation)."""

    model: RiskModel
    selection: Selection
    valid_v2: pd.DataFrame       # order_id, is_late, risk: the orders of V2 with the calibrated risk of the model
    x_v2: pd.DataFrame           # the matrix of V2 for the chosen feature set
    orders: Mapping[str, int]    # orders in the training block, V1 and V2


def _matrices(conn: sqlite3.Connection, sets: Sequence[str]) -> dict[str, pd.DataFrame]:
    orders, lines = read_order_frames(conn)
    return {name: build_features(orders, lines, name) for name in sets}


def train_model(conn: sqlite3.Connection, as_of: date, cfg: PipelineConfig) -> TrainedModel:
    """Fit, select, check and calibrate the model for the calculation date `as_of`, from the warehouse as staged for that date (see ``warehouse.rebuild_at``).

    Labels come from ``split.labelled_orders``, whose mart must be the one of `as_of` (``StaleMartError`` otherwise); the matrices from ``build_features`` of the
    staging tables. The training block fits the 24 candidates, V1 selects (and the tripwire looks at it) and calibrates, V2 only measures.
    """
    labelled = labelled_orders(conn, as_of, cfg)
    check_label_timing(labelled, cfg)
    parts = blocks(labelled)
    missing = [name for name in ("train", "valid_v1", "valid_v2") if name not in parts]
    if missing:
        raise ValueError(f"no hay pedidos con etiqueta en {', '.join(missing)} al {as_of}: el modelo no se puede ajustar")
    labels = labelled.set_index("order_id")["is_late"]
    for name in ("train", "valid_v1", "valid_v2"):
        require_both_classes(labels.loc[parts[name]["order_id"]], name)          # before anything is built or fitted
    sets = list(cfg.features.candidate_sets)
    matrices = _matrices(conn, sets)

    def rows(name: str, set_name: str) -> pd.DataFrame:
        return matrices[set_name].loc[parts[name]["order_id"].to_numpy()]

    fitted = fit_candidates(candidates(cfg.model, sets), cfg.model, {s: rows("train", s) for s in sets}, labels.loc[parts["train"]["order_id"]].to_numpy())
    y_v1 = labels.loc[parts["valid_v1"]["order_id"]].to_numpy()
    selection = select(fitted, {s: rows("valid_v1", s) for s in sets}, y_v1)
    check_tripwire(selection, cfg)
    chosen = selection.chosen.feature_set
    calibrated = calibrate(selection.pipeline, rows("valid_v1", chosen), y_v1, cfg.model.calibration)
    model = RiskModel(chosen, selection.chosen, selection.pipeline, calibrated, cfg.model.calibration)
    x_v2 = rows("valid_v2", chosen)
    v2_ids = parts["valid_v2"]["order_id"].to_numpy()
    valid_v2 = pd.DataFrame({"order_id": v2_ids, "is_late": labels.loc[v2_ids].to_numpy(), "risk": predict_risk(model, x_v2)})
    return TrainedModel(model, selection, valid_v2, x_v2, {name: len(parts[name]) for name in ("train", "valid_v1", "valid_v2")})


def importance(model: RiskModel, x_v2: pd.DataFrame, y_v2: np.ndarray | pd.Series, cfg: PipelineConfig) -> pd.DataFrame:
    """Permutation importance of the model in use on V2: ``feature`` and ``auc_drop`` (the mean fall of the AUC when the column is shuffled), largest first."""
    check_feature_matrix(x_v2, model.feature_set)
    result = permutation_importance(
        model.calibrated, x_v2, np.asarray(y_v2), scoring="roc_auc", n_repeats=cfg.evaluation.importance_repeats, random_state=cfg.model.random_state,
    )
    table = pd.DataFrame({"feature": list(x_v2.columns), "auc_drop": result.importances_mean})
    return table.sort_values(["auc_drop", "feature"], ascending=[False, True], kind="stable").reset_index(drop=True)
