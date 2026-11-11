"""The fingerprint of the configuration and the seal of the launch and of the test (U14, AC20, M05, decision D17).

Every seal here is written in a temporary folder; the seal of the project (``reports/model_seal.json``) is written only by the real run (U25).
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from latedel import registry
from latedel.model import Candidate
from latedel.registry import (
    LAUNCH_KEYS, SealError, SealMismatchError, canonical_sha256, config_fingerprint, read_seal, seal_state, valid_v2_scores_fingerprint, write_launch_seal, write_test_seal,
)

SOURCE = "ab" * 32
CHOSEN = SimpleNamespace(chosen=Candidate("f2", 0.05, 15, 100))


def launch_for(cfg, *, threshold=0.66, chosen=CHOSEN, source=SOURCE, run_key="run-L", scores="cd" * 32) -> dict:
    body, sha = config_fingerprint(cfg, chosen, threshold, source_sha256=source)
    return {"run_key": run_key, **body, "valid_v2_scores_sha256": scores, "config_sha256": sha}


def test_the_fingerprint_is_the_sha256_of_the_canonical_json_of_what_it_returns(cfg):
    body, sha = config_fingerprint(cfg, CHOSEN, 0.66, source_sha256=SOURCE)
    assert sha == canonical_sha256(body) and len(sha) == 64
    assert sha == config_fingerprint(cfg, CHOSEN, 0.66, source_sha256=SOURCE)[1], "the same configuration, the same fingerprint"
    assert set(body) == {"as_of", "source_sha256", "model_version", "feature_set", "params", "calibration", "threshold", "costs", "split", "criteria"}
    assert body["as_of"] == "2017-01-07" and body["feature_set"] == "f2" and body["threshold"] == 0.66 and body["calibration"] == "isotonic"
    assert body["params"] == {"learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 100, "l2_regularization": 1.0, "max_iter": 300}
    assert body["costs"] == {"alert_cost": 5.0, "late_cost": 20.0, "effect": 0.5}
    assert body["split"]["train_start"] == "2015-01-01" and body["split"]["purge_days"] == 7 and body["split"]["post_break_end"] == "2018-01-31"
    assert body["criteria"] == {"c06_max_ece": 0.03, "c06_max_bias": 0.02, "leak_tripwire_auc": 0.95}
    assert json.dumps(body)


def changed(cfg, section, **values):
    return replace(cfg, **{section: replace(getattr(cfg, section), **values)})


FINGERPRINT_CHANGES = {
    "threshold": lambda cfg: ({}, 0.67), "alert_cost": lambda cfg: (changed(cfg, "costs", alert_cost=8.0), 0.66), "late_cost": lambda cfg: (changed(cfg, "costs", late_cost=30.0), 0.66),
    "effect": lambda cfg: (changed(cfg, "costs", effect=0.6), 0.66), "l2": lambda cfg: (changed(cfg, "model", l2_regularization=2.0), 0.66),
    "max_iter": lambda cfg: (changed(cfg, "model", max_iter=200), 0.66), "model_version": lambda cfg: (changed(cfg, "model", model_version="v2"), 0.66),
    "train_end": lambda cfg: (changed(cfg, "split", train_end=date(2016, 6, 29)), 0.66), "purge": lambda cfg: (changed(cfg, "split", purge_days=6), 0.66),
    "criteria": lambda cfg: (changed(cfg, "criteria", c06_max_ece=0.04), 0.66), "tripwire": lambda cfg: (changed(cfg, "criteria", leak_tripwire_auc=0.9), 0.66),
    "launch_date": lambda cfg: (changed(cfg, "deliveries", launch_date=date(2017, 1, 8)), 0.66),
}


@pytest.mark.parametrize("name", sorted(FINGERPRINT_CHANGES))
def test_another_threshold_costs_split_or_model_setting_is_another_configuration(cfg, name):
    other_cfg, threshold = FINGERPRINT_CHANGES[name](cfg)
    base = config_fingerprint(cfg, CHOSEN, 0.66, source_sha256=SOURCE)[1]
    assert config_fingerprint(other_cfg or cfg, CHOSEN, threshold, source_sha256=SOURCE)[1] != base


def test_another_feature_set_candidate_or_source_is_another_configuration(cfg):
    base = config_fingerprint(cfg, CHOSEN, 0.66, source_sha256=SOURCE)[1]
    for chosen in (Candidate("f1", 0.05, 15, 100), Candidate("f2", 0.1, 15, 100), Candidate("f2", 0.05, 31, 100), Candidate("f2", 0.05, 15, 20)):
        assert config_fingerprint(cfg, SimpleNamespace(chosen=chosen), 0.66, source_sha256=SOURCE)[1] != base
    assert config_fingerprint(cfg, CHOSEN, 0.66, source_sha256="ef" * 32)[1] != base


def test_what_the_fingerprint_leaves_out_does_not_change_it(cfg):
    base = config_fingerprint(cfg, CHOSEN, 0.66, source_sha256=SOURCE)[1]
    other = replace(cfg, evaluation=replace(cfg.evaluation, bootstrap_resamples=5, roc_points=7), site=replace(cfg.site, min_cell_orders=50))
    assert config_fingerprint(other, CHOSEN, 0.66, source_sha256=SOURCE)[1] == base
    assert launch_for(cfg, run_key="a")["config_sha256"] == launch_for(cfg, run_key="b", scores="ee" * 32)["config_sha256"]


def test_the_fingerprint_of_the_scores_of_v2_is_sorted_rounded_and_sensitive():
    frame = pd.DataFrame({"order_id": [3, 1, 2], "risk": [0.31, 0.11, 0.2100004], "is_late": [1, 0, 1]})
    base = valid_v2_scores_fingerprint(frame)
    assert base == valid_v2_scores_fingerprint(frame.iloc[::-1]), "the order of the rows does not matter"
    assert base == valid_v2_scores_fingerprint(frame.assign(risk=frame["risk"] + 4e-8)), "below the sixth decimal nothing changes"
    assert base != valid_v2_scores_fingerprint(frame.assign(risk=frame["risk"] + 4e-6))
    assert base != valid_v2_scores_fingerprint(frame.assign(order_id=[3, 1, 4]))
    assert base == valid_v2_scores_fingerprint(frame.assign(is_late=[0, 0, 0])), "the labels are not part of the scores"
    assert len(base) == 64


# --- reading the seal --------------------------------------------------------------------------------------------------------------------------------


def test_no_file_is_no_seal_and_a_file_that_is_not_a_seal_is_never_taken_for_an_absent_one(cfg, tmp_path):
    path = tmp_path / "seal.json"
    assert read_seal(path) is None and seal_state(path, "0" * 64) == "none"
    good = {"version": 1, "launch": launch_for(cfg)}
    cases = {
        "not json": "{", "list": "[]", "other version": json.dumps({**good, "version": 2}), "no version": json.dumps({"launch": good["launch"]}), "extra key": json.dumps({**good, "x": 1}),
        "no launch": json.dumps({"version": 1}), "launch is not an object": json.dumps({"version": 1, "launch": 3}),
        "bad launch hash": json.dumps({"version": 1, "launch": {**good["launch"], "config_sha256": "zz"}}),
        "bad scores hash": json.dumps({"version": 1, "launch": {**good["launch"], "valid_v2_scores_sha256": "short"}}),
        "bad test": json.dumps({**good, "test": {"config_sha256": "no"}}), "test is not an object": json.dumps({**good, "test": 3}),
    }
    for name, text in cases.items():
        path.write_text(text, encoding="utf-8")
        with pytest.raises(SealError):
            read_seal(path)
        with pytest.raises(SealError):
            seal_state(path, "0" * 64)
        assert path.read_text(encoding="utf-8") == text, name


def test_seal_state_says_none_match_or_post_seal(cfg, tmp_path):
    path = tmp_path / "seal.json"
    launch = launch_for(cfg)
    assert seal_state(path, launch["config_sha256"]) == "none"
    write_launch_seal(path, launch)
    assert seal_state(path, launch["config_sha256"]) == "match"
    assert seal_state(path, launch_for(cfg, threshold=0.7)["config_sha256"]) == "post_seal"


# --- the part of the launch ---------------------------------------------------------------------------------------------------------------------------


def test_the_launch_is_written_once_exactly_and_a_repetition_changes_nothing(cfg, tmp_path):
    path = tmp_path / "reports" / "seal.json"
    launch = launch_for(cfg)
    assert write_launch_seal(path, launch) == "written"
    first = path.read_bytes()
    document = json.loads(first)
    assert document == {"version": 1, "launch": launch} and list(document["launch"]) != [] and set(document["launch"]) == set(LAUNCH_KEYS)
    assert first.endswith(b"\n") and json.dumps(document, sort_keys=True, indent=1, ensure_ascii=False).encode() + b"\n" == first
    assert write_launch_seal(path, launch) == "unchanged" and path.read_bytes() == first
    assert write_launch_seal(path, {**launch, "run_key": "another-run"}) == "unchanged" and path.read_bytes() == first, "the run key is not part of what is compared"
    assert [p.name for p in path.parent.iterdir()] == ["seal.json"], "no temporary file is left"


def test_another_configuration_leaves_the_seal_as_it_is(cfg, tmp_path):
    path = tmp_path / "seal.json"
    write_launch_seal(path, launch_for(cfg))
    sealed = path.read_bytes()
    for other in (launch_for(cfg, threshold=0.7), launch_for(changed(cfg, "costs", alert_cost=8.0), threshold=0.8), launch_for(cfg, chosen=SimpleNamespace(chosen=Candidate("f0", 0.1, 15, 20)))):
        assert write_launch_seal(path, other) == "post_seal"
        assert path.read_bytes() == sealed
        assert seal_state(path, other["config_sha256"]) == "post_seal"


def test_the_same_configuration_that_does_not_reproduce_the_scores_of_v2_fails(cfg, tmp_path):
    path = tmp_path / "seal.json"
    write_launch_seal(path, launch_for(cfg))
    sealed = path.read_bytes()
    with pytest.raises(SealMismatchError, match="R7"):
        write_launch_seal(path, launch_for(cfg, scores="ee" * 32))
    assert path.read_bytes() == sealed


def test_a_launch_that_is_not_complete_or_whose_fingerprint_is_not_its_own_is_refused_and_writes_nothing(cfg, tmp_path):
    path = tmp_path / "seal.json"
    launch = launch_for(cfg)
    broken = {
        "missing key": {k: v for k, v in launch.items() if k != "costs"}, "extra key": {**launch, "x": 1}, "short hash": {**launch, "config_sha256": "abc"},
        "scores hash": {**launch, "valid_v2_scores_sha256": "XYZ"}, "not its hash": {**launch, "config_sha256": "ff" * 32}, "edited body": {**launch, "threshold": 0.1},
    }
    for name, document in broken.items():
        with pytest.raises(SealError):
            write_launch_seal(path, document)
        assert not path.exists(), name


def test_a_seal_that_appears_while_the_launch_is_being_written_is_not_overwritten(cfg, tmp_path, monkeypatch):
    path = tmp_path / "seal.json"
    other = launch_for(cfg, threshold=0.7)
    write_launch_seal(path, other)
    sealed = path.read_bytes()
    calls = []
    real = registry.read_seal

    def blind_once(p):
        calls.append(1)
        return None if len(calls) == 1 else real(p)          # the first look says "no seal" and then somebody has written it

    monkeypatch.setattr(registry, "read_seal", blind_once)
    assert write_launch_seal(path, launch_for(cfg)) == "post_seal"
    assert path.read_bytes() == sealed


# --- the part of the test -----------------------------------------------------------------------------------------------------------------------------


def part_of_test(cfg, **values) -> dict:
    return {"run_key": "run-M2017-10", "as_of": "2017-10-31", "config_sha256": launch_for(cfg)["config_sha256"],
            "criteria": [{"id": "C01", "value": 0.7812345678, "baseline_value": 0.74, "ci_low": None, "ci_high": None, "passed": True}],
            "metrics": {"model": {"auc": 0.77123456789}, "b2_mode": {"auc": 0.74}, "b5_mode_noon": {"auc": 0.781}}, **values}


def test_the_test_is_written_once_after_the_launch_and_the_launch_stays_as_it_was(cfg, tmp_path):
    path = tmp_path / "seal.json"
    with pytest.raises(SealError, match="no hay sello"):
        write_test_seal(path, part_of_test(cfg))
    launch = launch_for(cfg)
    write_launch_seal(path, launch)
    before = json.loads(path.read_bytes())
    assert write_test_seal(path, part_of_test(cfg)) == "written"
    after = json.loads(path.read_bytes())
    assert after["launch"] == before["launch"] == launch and after["test"] == part_of_test(cfg) and set(after) == {"version", "launch", "test"}
    assert [p.name for p in path.parent.iterdir()] == ["seal.json"]


def test_a_repetition_with_the_same_values_to_six_decimals_changes_nothing_and_other_values_fail(cfg, tmp_path):
    path = tmp_path / "seal.json"
    write_launch_seal(path, launch_for(cfg))
    write_test_seal(path, part_of_test(cfg))
    sealed = path.read_bytes()
    near = part_of_test(cfg, run_key="run-again", criteria=[{"id": "C01", "value": 0.7812345681, "baseline_value": 0.74, "ci_low": None, "ci_high": None, "passed": True}],
                     metrics={"model": {"auc": 0.77123456791}, "b2_mode": {"auc": 0.74}, "b5_mode_noon": {"auc": 0.781}})
    assert write_test_seal(path, near) == "unchanged" and path.read_bytes() == sealed
    far = part_of_test(cfg, metrics={"model": {"auc": 0.77124}, "b2_mode": {"auc": 0.74}, "b5_mode_noon": {"auc": 0.781}})
    with pytest.raises(SealMismatchError, match="R7"):
        write_test_seal(path, far)
    flipped = part_of_test(cfg, criteria=[{"id": "C01", "value": 0.7812345678, "baseline_value": 0.74, "ci_low": None, "ci_high": None, "passed": False}])
    with pytest.raises(SealMismatchError):
        write_test_seal(path, flipped)
    assert path.read_bytes() == sealed


def test_a_test_evaluated_with_another_configuration_is_post_seal_and_leaves_the_seal_alone(cfg, tmp_path):
    path = tmp_path / "seal.json"
    write_launch_seal(path, launch_for(cfg))
    sealed = path.read_bytes()
    other = part_of_test(cfg, config_sha256=launch_for(cfg, threshold=0.7)["config_sha256"])
    assert write_test_seal(path, other) == "post_seal" and path.read_bytes() == sealed, "before the test is sealed"
    write_test_seal(path, part_of_test(cfg))
    sealed = path.read_bytes()
    assert write_test_seal(path, other) == "post_seal" and path.read_bytes() == sealed, "and after"


def test_a_part_of_test_that_is_incomplete_is_refused(cfg, tmp_path):
    path = tmp_path / "seal.json"
    write_launch_seal(path, launch_for(cfg))
    sealed = path.read_bytes()
    for broken in ({k: v for k, v in part_of_test(cfg).items() if k != "metrics"}, {**part_of_test(cfg), "x": 1}, part_of_test(cfg, config_sha256="nope")):
        with pytest.raises(SealError):
            write_test_seal(path, broken)
    with pytest.raises(SealError, match="JSON"):
        write_test_seal(path, part_of_test(cfg, metrics={"model": {"auc": object()}}))
    assert path.read_bytes() == sealed


def test_a_write_that_fails_leaves_the_seal_as_it_was_and_no_temporary_file(cfg, tmp_path, monkeypatch):
    path = tmp_path / "seal.json"
    write_launch_seal(path, launch_for(cfg))
    sealed = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_test_seal(path, part_of_test(cfg))
    assert path.read_bytes() == sealed and [p.name for p in tmp_path.iterdir()] == ["seal.json"]


def test_the_seal_of_the_project_is_not_written_by_the_tests(project_seal_unchanged):
    assert project_seal_unchanged(), "the real run (U25) writes it; no test writes or changes it"
