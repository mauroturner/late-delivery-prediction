"""The frozen launch (U14, AC20, AC21, M03, M05, decision D17): the model trained once, registered, sealed, and what a later or another run can and cannot change.

Seals and artifacts are written only in temporary folders. The fixture's launch warehouse stands for the launch run; the real file's launch is trained once per
session and, as in the product, no label of the test block is in the warehouse.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from latedel import launch as launch_module
from latedel import registry
from latedel.baselines import baseline_rates
from latedel.launch import LaunchRequiredError, fit_model, load_registered_model, m05_result
from latedel.registry import ArtifactMismatchError, SealMismatchError, load_artifact, read_seal, seal_state, valid_v2_scores_fingerprint, write_test_seal
from latedel.split import labelled_orders
from latedel.warehouse import connect
from tests.fixtures.helpers import file_digest

SOURCE = "ab" * 32
LAUNCH_RUN = "run-L2017-01-07"


def fresh(launch_db: Path, folder: Path, name: str = "w.sqlite"):
    """A private copy of the launch warehouse, connected."""
    target = folder / name
    shutil.copyfile(launch_db, target)
    return connect(target)


def run_launch(conn, config, run_key="run-other"):
    return fit_model(conn, config, run_key=run_key, as_of=config.deliveries.launch_date, source_sha256=SOURCE)


def rows(conn, sql: str):
    return conn.execute(sql).fetchall()


# --- the launch -----------------------------------------------------------------------------------------------------------------------------------------


def test_the_launch_saves_the_artifact_registers_the_model_and_writes_the_part_of_the_launch_of_the_seal(fitted_launch):
    config, result = fitted_launch.cfg, fitted_launch.result
    conn = connect(fitted_launch.db)
    (version, as_of, path, sha, feature_set, params_json, calibration, threshold, costs_json, config_sha, selection_json, v2_json) = conn.execute(
        "SELECT model_version, trained_as_of, artifact_path, artifact_sha256, feature_set, params_json, calibration, threshold, costs_json, config_sha256, selection_json, "
        "valid_v2_json FROM model_registry").fetchone()
    assert rows(conn, "SELECT COUNT(*) FROM model_registry") == [(1,)]
    assert (version, as_of, calibration) == ("v1", "2017-01-07", "isotonic") and result["mode"] == "launch" and result["seal_state"] == "sealed" and result["m05_passed"]
    assert sha == result["artifact_sha256"] == file_digest(config.paths.models_dir / "v1.pkl") and path.endswith("v1.pkl")
    assert feature_set == result["feature_set"] and threshold == result["threshold"] and config_sha == result["config_sha256"]
    assert json.loads(costs_json) == {"alert_cost": 5.0, "late_cost": 20.0, "effect": 0.5}
    params = json.loads(params_json)
    assert {"learning_rate", "max_leaf_nodes", "min_samples_leaf", "l2_regularization", "max_iter", "random_state"} == set(params) and params["max_iter"] == 40
    selection = json.loads(selection_json)
    assert len(selection) == 3 and sum(s["chosen"] for s in selection) == 1 and min(s["log_loss_v1"] for s in selection) == [s for s in selection if s["chosen"]][0]["log_loss_v1"]
    figures = json.loads(v2_json)
    assert figures["orders"] == result["orders"]["valid_v2"] and 0 < figures["auc"] < 1 and figures["threshold"] == threshold and len(figures["scores_sha256"]) == 64
    # the artifact loads with its fingerprint and is the model in use; the registry gives it back
    registered = load_registered_model(conn, config)
    assert registered.model.feature_set == feature_set and registered.threshold == threshold and registered.artifact_sha256 == sha and registered.trained_as_of.isoformat() == "2017-01-07"
    assert registered.valid_v2_scores_sha256 == result["valid_v2_scores_sha256"] == figures["scores_sha256"]
    assert load_artifact(config.paths.models_dir / "v1.pkl", sha).feature_set == feature_set
    # the seal has the launch and nothing else
    seal = read_seal(config.paths.seal)
    assert set(seal) == {"version", "launch"} and seal["launch"]["run_key"] == LAUNCH_RUN and seal["launch"]["config_sha256"] == config_sha
    assert seal["launch"]["threshold"] == threshold and seal["launch"]["valid_v2_scores_sha256"] == figures["scores_sha256"] and seal["launch"]["source_sha256"] == "ab" * 32
    # the rates of B2 and B5 are in the warehouse, fitted on the training block
    labelled = labelled_orders(conn, config.deliveries.launch_date, config)
    expected = baseline_rates(labelled, config).table()
    stored = conn.execute("SELECT baseline, cell, rate, train_orders FROM baseline_rates ORDER BY baseline, cell").fetchall()
    assert stored == [tuple(r) for r in expected.sort_values(["baseline", "cell"]).itertuples(index=False)] and {r[0] for r in stored} == {"b2_mode", "b5_mode_noon"}
    conn.close()


def test_the_registry_is_insert_only(fitted_launch, tmp_path):
    db = tmp_path / "w.sqlite"
    shutil.copyfile(fitted_launch.db, db)
    conn = connect(db)
    before = file_digest(db)
    attempts = (
        "UPDATE model_registry SET threshold = 0.1", "DELETE FROM model_registry",
        "INSERT OR REPLACE INTO model_registry SELECT * FROM model_registry", "REPLACE INTO model_registry SELECT * FROM model_registry", "INSERT INTO model_registry SELECT * FROM model_registry",
    )
    for statement in attempts:
        with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
            conn.execute(statement)
    conn.close()
    assert file_digest(db) == before


def test_other_threshold_after_the_seal_does_not_replace_the_sealed_result(cfg, launch_db, fitted_launch, tmp_paths_cfg, tmp_path):
    seal = fitted_launch.cfg.paths.seal
    sealed_launch = seal.read_bytes()
    test_part = {
        "run_key": "run-M2017-10", "as_of": "2017-10-31", "config_sha256": fitted_launch.result["config_sha256"],
        "criteria": [{"id": "C01", "value": 0.78, "baseline_value": 0.74, "ci_low": None, "ci_high": None, "passed": True}], "metrics": {"model": {"auc": 0.78}, "b2_mode": {"auc": 0.74}},
    }
    folder = tmp_path / "first"
    shutil.copytree(fitted_launch.cfg.paths.seal.parent, folder / "reports")
    sealed_cfg = replace(fitted_launch.cfg, paths=replace(fitted_launch.cfg.paths, seal=folder / "reports" / seal.name))
    assert write_test_seal(sealed_cfg.paths.seal, test_part) == "written"
    sealed = sealed_cfg.paths.seal.read_bytes()
    assert json.loads(sealed)["launch"] == json.loads(sealed_launch)["launch"] and "test" in json.loads(sealed)

    # another analysis, with another alert cost (so, another threshold), over the same seal
    other_folder = tmp_path / "other"
    other_cfg = tmp_paths_cfg(other_folder, costs={"alert_cost": 2.0})
    other_cfg = replace(other_cfg, paths=replace(other_cfg.paths, seal=sealed_cfg.paths.seal))
    conn = fresh(launch_db, tmp_path, "other.sqlite")
    other = run_launch(conn, other_cfg, run_key="run-other-costs")
    assert other["threshold"] != fitted_launch.result["threshold"] and other["break_even"] == 0.2
    assert other["seal_state"] == "post_seal" and other["m05_passed"] is False and m05_result(other["seal_state"]).passed is False
    assert sealed_cfg.paths.seal.read_bytes() == sealed, "the sealed result is not replaced"
    assert seal_state(sealed_cfg.paths.seal, other["config_sha256"]) == "post_seal" and seal_state(sealed_cfg.paths.seal, fitted_launch.result["config_sha256"]) == "match"
    # the other analysis is in its own warehouse, registered with its own threshold
    assert conn.execute("SELECT threshold, config_sha256 FROM model_registry").fetchone() == (other["threshold"], other["config_sha256"])
    # its evaluation of the test does not replace the sealed one either, whatever its values
    other_test = {**test_part, "config_sha256": other["config_sha256"], "criteria": [{**test_part["criteria"][0], "value": 0.5, "passed": False}], "metrics": {"model": {"auc": 0.5}}}
    assert write_test_seal(sealed_cfg.paths.seal, other_test) == "post_seal"
    assert sealed_cfg.paths.seal.read_bytes() == sealed
    # and the same configuration with other values does not either: it fails
    with pytest.raises(SealMismatchError, match="R7"):
        write_test_seal(sealed_cfg.paths.seal, {**test_part, "metrics": {"model": {"auc": 0.5}}})
    assert sealed_cfg.paths.seal.read_bytes() == sealed
    conn.close()


@pytest.mark.parametrize("change", [{"costs": {"alert_cost": 2.0}}, {"costs": {"late_cost": 30.0}}, {"sets": ("f2",)}], ids=["alert_cost", "late_cost", "other_variables"])
def test_other_costs_or_other_variables_are_post_seal_and_the_seal_stays(launch_db, fitted_launch, tmp_paths_cfg, tmp_path, change):
    seal_copy = tmp_path / "reports"
    shutil.copytree(fitted_launch.cfg.paths.seal.parent, seal_copy)
    sealed = (seal_copy / "model_seal.json").read_bytes()
    if "sets" in change:                                  # other variables: only the set that was not chosen
        change = {"sets": ("f0",) if fitted_launch.result["feature_set"] != "f0" else ("f2",)}
    config = tmp_paths_cfg(tmp_path / "run", **change)
    config = replace(config, paths=replace(config.paths, seal=seal_copy / "model_seal.json"))
    conn = fresh(launch_db, tmp_path)
    result = run_launch(conn, config)
    assert result["seal_state"] == "post_seal" and not result["m05_passed"] and result["config_sha256"] != fitted_launch.result["config_sha256"]
    assert (seal_copy / "model_seal.json").read_bytes() == sealed
    assert [p.name for p in seal_copy.iterdir()] == ["model_seal.json"]
    conn.close()


def test_a_rebuild_with_the_sealed_configuration_reproduces_the_sealed_values_and_rewrites_nothing(launch_db, fitted_launch, tmp_path):
    seal_copy = tmp_path / "reports"
    shutil.copytree(fitted_launch.cfg.paths.seal.parent, seal_copy)
    sealed = (seal_copy / "model_seal.json").read_bytes()
    config = replace(fitted_launch.cfg, paths=replace(fitted_launch.cfg.paths, seal=seal_copy / "model_seal.json", models_dir=tmp_path / "models"))
    conn = fresh(launch_db, tmp_path)
    again = fit_model(conn, config, run_key="run-rebuild", as_of=config.deliveries.launch_date, source_sha256="ab" * 32)
    assert again["seal_state"] == "match" and again["m05_passed"]
    assert again["config_sha256"] == fitted_launch.result["config_sha256"] and again["valid_v2_scores_sha256"] == fitted_launch.result["valid_v2_scores_sha256"]
    assert again["threshold"] == fitted_launch.result["threshold"] and again["artifact_sha256"] == fitted_launch.result["artifact_sha256"]
    assert (seal_copy / "model_seal.json").read_bytes() == sealed, "the seal of the first launch, with its run key, is untouched"
    conn.close()


def test_a_rebuild_whose_scores_of_v2_do_not_reproduce_the_seal_fails_before_saving_or_registering_anything(launch_db, fitted_launch, tmp_path, monkeypatch):
    seal_copy = tmp_path / "reports"
    shutil.copytree(fitted_launch.cfg.paths.seal.parent, seal_copy)
    sealed = (seal_copy / "model_seal.json").read_bytes()
    config = replace(fitted_launch.cfg, paths=replace(fitted_launch.cfg.paths, seal=seal_copy / "model_seal.json", models_dir=tmp_path / "models"))
    monkeypatch.setattr(launch_module, "valid_v2_scores_fingerprint", lambda frame: "ee" * 32)
    conn = fresh(launch_db, tmp_path)
    with pytest.raises(SealMismatchError, match="R7"):
        fit_model(conn, config, run_key="run-rebuild", as_of=config.deliveries.launch_date, source_sha256="ab" * 32)
    assert (seal_copy / "model_seal.json").read_bytes() == sealed
    assert not (tmp_path / "models").exists() and rows(conn, "SELECT COUNT(*) FROM model_registry") == [(0,)] and rows(conn, "SELECT COUNT(*) FROM baseline_rates") == [(0,)]
    conn.close()


# --- the frozen model in the runs that follow -------------------------------------------------------------------------------------------------------------


def test_a_later_run_trains_nothing_and_reports_the_same_model(fitted_launch, tmp_path, monkeypatch):
    db = tmp_path / "w.sqlite"
    shutil.copyfile(fitted_launch.db, db)
    conn = connect(db)
    monkeypatch.setattr(launch_module, "train_model", lambda *a, **k: pytest.fail("a later run must not train"))
    config = fitted_launch.cfg
    for as_of in (config.deliveries.launch_date, date_after(config, 24), date_after(config, 300)):
        result = fit_model(conn, config, run_key=f"run-{as_of}", as_of=as_of, source_sha256="ab" * 32)
        assert result["mode"] == "reuse" and result["seal_state"] == "match" and result["m05_passed"]
        assert result["threshold"] == fitted_launch.result["threshold"] and result["artifact_sha256"] == fitted_launch.result["artifact_sha256"]
        assert result["config_sha256"] == fitted_launch.result["config_sha256"] and result["registered_config_sha256"] == result["config_sha256"]
    assert file_digest(config.paths.models_dir / "v1.pkl") == fitted_launch.result["artifact_sha256"]
    conn.close()


def date_after(config, days: int):
    from datetime import timedelta

    return config.deliveries.launch_date + timedelta(days=days)


def test_a_later_run_with_other_costs_keeps_the_frozen_threshold_and_says_post_seal(fitted_launch, tmp_path):
    db = tmp_path / "w.sqlite"
    shutil.copyfile(fitted_launch.db, db)
    conn = connect(db)
    other = replace(fitted_launch.cfg, costs=replace(fitted_launch.cfg.costs, alert_cost=8.0))
    seal = fitted_launch.cfg.paths.seal.read_bytes()
    result = fit_model(conn, other, run_key="run-later", as_of=date_after(other, 24), source_sha256="ab" * 32)
    assert result["mode"] == "reuse" and result["threshold"] == fitted_launch.result["threshold"], "the registered threshold is the one in use"
    assert result["seal_state"] == "post_seal" and not result["m05_passed"] and result["config_sha256"] != result["registered_config_sha256"]
    assert fitted_launch.cfg.paths.seal.read_bytes() == seal
    conn.close()


def test_the_model_is_trained_only_at_the_launch(launch_db, tmp_paths_cfg, tmp_path):
    config = tmp_paths_cfg(tmp_path / "run")
    conn = fresh(launch_db, tmp_path)
    with pytest.raises(LaunchRequiredError, match="lanzamiento"):
        fit_model(conn, config, run_key="run-M", as_of=date_after(config, 24), source_sha256="ab" * 32)
    with pytest.raises(LaunchRequiredError):
        load_registered_model(conn, config)
    assert not (tmp_path / "run").exists(), "no artifact and no seal"
    assert rows(conn, "SELECT COUNT(*) FROM model_registry") == [(0,)] and rows(conn, "SELECT COUNT(*) FROM baseline_rates") == [(0,)]
    conn.close()


class SpyPickle:
    def __init__(self) -> None:
        self.loaded = 0

    def dumps(self, obj, protocol=None):
        import pickle

        return pickle.dumps(obj, protocol=protocol)

    def loads(self, data):
        self.loaded += 1
        raise AssertionError("pickle must not see the bytes of a changed artifact")


def test_an_artifact_with_another_fingerprint_is_refused_before_it_is_deserialized_in_the_runs_that_follow(launch_db, tmp_paths_cfg, tmp_path, monkeypatch):
    config = tmp_paths_cfg(tmp_path / "run")
    conn = fresh(launch_db, tmp_path)
    run_launch(conn, config)
    artifact = config.paths.models_dir / "v1.pkl"
    data = bytearray(artifact.read_bytes())
    data[len(data) // 2] ^= 0x01
    artifact.write_bytes(bytes(data))
    spy = SpyPickle()
    monkeypatch.setattr(registry, "pickle", spy)
    with pytest.raises(ArtifactMismatchError, match=r"M03.*R6"):
        fit_model(conn, config, run_key="run-M", as_of=date_after(config, 24), source_sha256="ab" * 32)
    with pytest.raises(ArtifactMismatchError):
        load_registered_model(conn, config)
    assert spy.loaded == 0
    conn.close()


def test_a_model_inside_the_project_folder_is_registered_with_a_relative_path_with_forward_slashes(cfg):
    inside = cfg.project_root / "data" / "models" / "v1.pkl"
    assert launch_module._relative(inside, cfg.project_root) == "data/models/v1.pkl"
    outside = Path(cfg.project_root.anchor) / "elsewhere" / "v1.pkl"
    assert launch_module._relative(outside, cfg.project_root) == str(outside)


def test_a_launch_that_stopped_after_registering_the_model_writes_its_seal_when_it_is_repeated(fitted_launch, tmp_path):
    folder = tmp_path / "copy"
    shutil.copytree(fitted_launch.folder, folder)
    seal = folder / "reports" / "model_seal.json"
    seal.unlink()
    config = replace(fitted_launch.cfg, paths=replace(fitted_launch.cfg.paths, seal=seal, models_dir=folder / "models"))
    conn = connect(folder / "fitted.sqlite")
    later = fit_model(conn, config, run_key="run-M", as_of=date_after(config, 24), source_sha256="ab" * 32)
    assert later["seal_state"] == "none" and not seal.exists(), "only the launch writes the seal"
    resumed = fit_model(conn, config, run_key="run-resumed", as_of=config.deliveries.launch_date, source_sha256="ab" * 32)
    assert resumed["mode"] == "reuse" and resumed["seal_state"] == "sealed"
    document = read_seal(seal)
    assert document["launch"]["config_sha256"] == fitted_launch.result["config_sha256"] and document["launch"]["valid_v2_scores_sha256"] == fitted_launch.result["valid_v2_scores_sha256"]
    assert document["launch"]["run_key"] == "run-resumed"
    conn.close()


def test_m05_is_a_warning_that_fails_only_for_a_configuration_that_is_not_the_sealed_one():
    assert [m05_result(s).passed for s in ("none", "sealed", "match", "post_seal")] == [True, True, True, False]
    assert m05_result("post_seal").rule_id == "M05" and m05_result("post_seal").severity.value == "warning"


def test_fitting_again_gives_the_same_scores_of_v2_in_any_warehouse(cfg, launch_db, tmp_paths_cfg, tmp_path):
    results = []
    for index in range(2):
        config = tmp_paths_cfg(tmp_path / f"run{index}")
        conn = fresh(launch_db, tmp_path, f"w{index}.sqlite")
        results.append(run_launch(conn, config, run_key=f"run-{index}"))
        conn.close()
    a, b = results
    assert a["valid_v2_scores_sha256"] == b["valid_v2_scores_sha256"] and a["config_sha256"] == b["config_sha256"] and a["threshold"] == b["threshold"]
    assert a["seal_state"] == b["seal_state"] == "sealed", "each folder has its own seal"


# --- the real file (real_data) ----------------------------------------------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_launch_is_sealed_and_a_rebuild_from_scratch_reproduces_it(cfg, real_launch_db, real_fitted, real_trained, tmp_path):
    config, result = real_fitted.cfg, real_fitted.result
    seal = read_seal(config.paths.seal)
    print("REAL launch", {k: result[k] for k in ("feature_set", "params", "threshold", "break_even", "seal_state", "orders")})
    print("REAL fingerprints", {k: result[k] for k in ("config_sha256", "valid_v2_scores_sha256", "artifact_sha256")})
    print("REAL V2 figures", {k: round(v, 5) if isinstance(v, float) else v for k, v in result["valid_v2"].items() if k != "scores_sha256"})
    assert result["mode"] == "launch" and result["seal_state"] == "sealed" and result["orders"] == {"train": 29_894, "valid_v1": 4_649, "valid_v2": 5_032}
    assert seal["launch"]["config_sha256"] == result["config_sha256"] and set(seal) == {"version", "launch"}
    assert result["valid_v2_scores_sha256"] == valid_v2_scores_fingerprint(real_trained.valid_v2), "two trainings in two warehouses give the same scores of V2"
    assert result["threshold"] == 0.66 and result["feature_set"] == "f2"
    # rebuild from scratch with the same configuration and the seal in place
    sealed = config.paths.seal.read_bytes()
    rebuild_cfg = replace(config, paths=replace(config.paths, models_dir=tmp_path / "models"))
    conn = fresh(real_launch_db, tmp_path)
    again = fit_model(conn, rebuild_cfg, run_key="real-rebuild", as_of=config.deliveries.launch_date, source_sha256=config.source.accepted_sha256)
    conn.close()
    assert again["seal_state"] == "match" and again["valid_v2_scores_sha256"] == result["valid_v2_scores_sha256"] and again["artifact_sha256"] == result["artifact_sha256"]
    assert config.paths.seal.read_bytes() == sealed
    assert not (Path(__file__).resolve().parents[2] / "reports" / "model_seal.json").exists()
