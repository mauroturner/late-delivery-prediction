"""The task ``fit_model``: the model is trained once, at the launch, and frozen (RF16, AC20, AC21, decision D17).

At the launch ``fit_model`` trains the 24 candidates, selects and calibrates the model, chooses the threshold on V2, checks the configuration against the seal,
saves the artifact with its SHA-256, registers it (``model_registry`` and ``baseline_rates``, both insert-only) and writes the part of the launch of the seal. In
every other run it trains nothing: it checks that the artifact still has the registered SHA-256 (M03) and compares the configuration with the sealed one (M05).

The order at the launch is: train, check the seal, save the artifact, register, in that order, so that a configuration that does not reproduce what was sealed
(runbook R7) stops the run before anything is saved or registered.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from latedel.baselines import baseline_rates
from latedel.config import PipelineConfig
from latedel.model import Candidate, RiskModel, train_model
from latedel.registry import (
    config_fingerprint, load_artifact, save_artifact, seal_state, valid_v2_scores_fingerprint, write_launch_seal,
)
from latedel.split import labelled_orders
from latedel.threshold import v2_threshold
from latedel.validate import RuleResult, make_result
from latedel.warehouse import apply_schema

logger = logging.getLogger(__name__)


class LaunchRequiredError(RuntimeError):
    """There is no registered model and this run is not the launch: the model is trained once, at the launch, and is not trained in any other run."""


@dataclass(frozen=True)
class RegisteredModel:
    """The frozen model as the registry has it, with its artifact already checked against its SHA-256 (M03)."""

    model: RiskModel
    threshold: float
    model_version: str
    chosen: Candidate
    artifact_sha256: str
    config_sha256: str
    trained_as_of: date
    valid_v2_scores_sha256: str


class _Chosen:
    """What ``config_fingerprint`` needs from a selection: the candidate."""

    def __init__(self, chosen: Candidate) -> None:
        self.chosen = chosen


_REGISTRY_COLUMNS = (
    "model_version, trained_as_of, artifact_path, artifact_sha256, feature_set, params_json, calibration, threshold, costs_json, config_sha256, selection_json, valid_v2_json"
)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def artifact_path(cfg: PipelineConfig) -> Path:
    return cfg.paths.models_dir / f"{cfg.model.model_version}.pkl"


def _row(conn: sqlite3.Connection, version: str) -> sqlite3.Row | tuple | None:
    return conn.execute(f"SELECT {_REGISTRY_COLUMNS} FROM model_registry WHERE model_version = ?", (version,)).fetchone()


def load_registered_model(conn: sqlite3.Connection, cfg: PipelineConfig) -> RegisteredModel:
    """The model registered for ``[model] model_version``, loaded only if its artifact has the registered SHA-256 (M03, ``ArtifactMismatchError`` otherwise)."""
    apply_schema(conn)
    row = _row(conn, cfg.model.model_version)
    if row is None:
        raise LaunchRequiredError(f"no hay un modelo registrado con la versión {cfg.model.model_version}: se entrena en el lanzamiento")
    version, trained_as_of, path, sha256, feature_set, params_json, _calibration, threshold, _costs, config_sha256, _selection, valid_v2_json = row
    location = Path(path) if Path(path).is_absolute() else cfg.project_root / path
    model = load_artifact(location, sha256)
    params = json.loads(params_json)
    chosen = Candidate(feature_set, params["learning_rate"], params["max_leaf_nodes"], params["min_samples_leaf"])
    return RegisteredModel(model, float(threshold), version, chosen, sha256, config_sha256, date.fromisoformat(trained_as_of), json.loads(valid_v2_json)["scores_sha256"])


def m05_result(state: str) -> RuleResult:
    """M05 (a warning): the configuration of the run is not the sealed one. It passes with no seal yet or with the sealed configuration."""
    return make_result("M05", passed=state != "post_seal")


def _v2_figures(trained, choice) -> dict[str, Any]:
    valid = trained.valid_v2
    risk, late = valid["risk"], valid["is_late"]
    return {
        "orders": int(len(valid)), "late_rate": float(late.mean()), "mean_risk": float(risk.mean()), "auc": float(roc_auc_score(late, risk)),
        "log_loss": float(log_loss(late, risk, labels=[0, 1])), "brier": float(brier_score_loss(late, risk)), "threshold": choice.threshold,
        "flagged_share": choice.flagged_share, "net_benefit": choice.net_benefit, "net_benefit_per_1000": choice.net_benefit_per_1000,
        "scores_sha256": valid_v2_scores_fingerprint(valid),
    }


def _selection_json(trained) -> str:
    chosen = trained.selection.chosen
    return json.dumps([{**c.params(), "feature_set": c.feature_set, "log_loss_v1": loss, "chosen": c == chosen} for c, loss in trained.selection.losses])


def fit_model(conn: sqlite3.Connection, cfg: PipelineConfig, *, run_key: str, as_of: date, source_sha256: str) -> dict[str, Any]:
    """Train and freeze the model if this is the launch; otherwise check the frozen one. Returns what the run records (JSON-ready).

    `as_of` is the calculation date of the run and `source_sha256` the fingerprint of the source file. ``seal_state`` in the result is ``sealed`` (this run wrote
    the part of the launch of the seal), ``match`` (a rebuild with the sealed configuration that reproduced the sealed scores of V2), ``post_seal`` (another
    configuration than the sealed one: M05 fails and nothing sealed is touched) or ``none`` (there is no seal yet).
    """
    apply_schema(conn)
    if _row(conn, cfg.model.model_version) is not None:
        return _reuse(conn, cfg, run_key=run_key, as_of=as_of, source_sha256=source_sha256)
    if as_of != cfg.deliveries.launch_date:
        raise LaunchRequiredError(
            f"no hay un modelo registrado con la versión {cfg.model.model_version} y la corrida es la del {as_of}, no la del lanzamiento "
            f"({cfg.deliveries.launch_date}): el modelo se entrena una sola vez, en el lanzamiento"
        )
    return _launch(conn, cfg, run_key=run_key, as_of=as_of, source_sha256=source_sha256)


def _launch(conn: sqlite3.Connection, cfg: PipelineConfig, *, run_key: str, as_of: date, source_sha256: str) -> dict[str, Any]:
    trained = train_model(conn, as_of, cfg)
    choice = v2_threshold(trained.valid_v2, cfg)
    rates = baseline_rates(labelled_orders(conn, as_of, cfg), cfg)
    body, config_sha256 = config_fingerprint(cfg, trained.selection, choice.threshold, source_sha256=source_sha256)
    figures = _v2_figures(trained, choice)
    launch = {"run_key": run_key, **body, "valid_v2_scores_sha256": figures["scores_sha256"], "config_sha256": config_sha256}

    outcome = write_launch_seal(cfg.paths.seal, launch)          # stops the run before anything is saved if the sealed configuration does not reproduce (R7)
    path = artifact_path(cfg)
    artifact_sha256 = save_artifact(trained.model, path)
    chosen = trained.selection.chosen
    params = {**chosen.params(), "l2_regularization": cfg.model.l2_regularization, "max_iter": cfg.model.max_iter, "random_state": cfg.model.random_state}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            f"INSERT INTO model_registry ({_REGISTRY_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cfg.model.model_version, as_of.isoformat(), _relative(path, cfg.project_root), artifact_sha256, chosen.feature_set, json.dumps(params),
                cfg.model.calibration, choice.threshold, json.dumps(body["costs"]), config_sha256, _selection_json(trained), json.dumps(figures),
            ),
        )
        conn.executemany(
            "INSERT INTO baseline_rates (baseline, cell, rate, train_orders) VALUES (?, ?, ?, ?)",
            [(r.baseline, r.cell, float(r.rate), int(r.train_orders)) for r in rates.table().itertuples()],
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    state = {"written": "sealed", "unchanged": "match", "post_seal": "post_seal"}[outcome]
    logger.info("fit_model %s: launch, set=%s threshold=%s seal=%s", run_key, chosen.feature_set, choice.threshold, state)
    return {
        "mode": "launch", "model_version": cfg.model.model_version, "feature_set": chosen.feature_set, "params": params, "threshold": choice.threshold,
        "break_even": choice.break_even, "artifact_sha256": artifact_sha256, "config_sha256": config_sha256, "valid_v2_scores_sha256": figures["scores_sha256"],
        "seal_state": state, "m05_passed": state != "post_seal", "orders": dict(trained.orders), "valid_v2": figures,
    }


def _reuse(conn: sqlite3.Connection, cfg: PipelineConfig, *, run_key: str, as_of: date, source_sha256: str) -> dict[str, Any]:
    registered = load_registered_model(conn, cfg)                     # M03: the artifact is deserialized only with the registered fingerprint
    body, config_sha256 = config_fingerprint(cfg, _Chosen(registered.chosen), registered.threshold, source_sha256=source_sha256)
    state = seal_state(cfg.paths.seal, config_sha256)
    if state == "none" and as_of == cfg.deliveries.launch_date:       # the launch was interrupted after registering the model and before sealing it
        outcome = write_launch_seal(cfg.paths.seal, {"run_key": run_key, **body, "valid_v2_scores_sha256": registered.valid_v2_scores_sha256, "config_sha256": config_sha256})
        state = {"written": "sealed", "unchanged": "match", "post_seal": "post_seal"}[outcome]
    logger.info("fit_model %s: reuse of %s, threshold=%s seal=%s", run_key, registered.model_version, registered.threshold, state)
    return {
        "mode": "reuse", "model_version": registered.model_version, "feature_set": registered.chosen.feature_set, "params": asdict(registered.chosen),
        "threshold": registered.threshold, "artifact_sha256": registered.artifact_sha256, "config_sha256": config_sha256, "registered_config_sha256": registered.config_sha256,
        "valid_v2_scores_sha256": registered.valid_v2_scores_sha256, "seal_state": state, "m05_passed": state != "post_seal",
    }
