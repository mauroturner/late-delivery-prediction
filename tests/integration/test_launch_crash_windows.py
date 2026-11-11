"""The two places where a launch can be interrupted, and that repeating it finishes the same launch (H3, R4-decision-doc-order-mismatch, decision D62).

The launch does, in this order: train, write the seal (which stops the run if the configuration does not reproduce a sealed one), save the artifact, register the model
and the rates in one transaction. D54 had described the recovery of a different order; the code is right (a registered model always has its seal), and these tests
inject a failure in each window and repeat the launch. Training is done once and handed back by a stand-in, since what is tested is what is saved and sealed.
"""

from __future__ import annotations

import shutil

import pytest

from latedel import launch
from latedel.launch import fit_model, load_registered_model
from latedel.model import train_model
from latedel.registry import artifact_sha256, read_seal
from latedel.warehouse import connect, rebuild_at

SOURCE = "ab" * 32
RUN = "run-L2017-01-07"


@pytest.fixture(scope="module")
def trained(cfg, launch_db, tmp_paths_cfg, tmp_path_factory):
    folder = tmp_path_factory.mktemp("crash_trained")
    config = tmp_paths_cfg(folder)
    db = folder / "w.sqlite"
    shutil.copyfile(launch_db, db)
    conn = connect(db)
    model = train_model(conn, config.deliveries.launch_date, config)
    conn.close()
    return model


@pytest.fixture()
def launching(trained, launch_db, tmp_paths_cfg, tmp_path, monkeypatch):
    config = tmp_paths_cfg(tmp_path)
    db = tmp_path / "w.sqlite"
    shutil.copyfile(launch_db, db)
    conn = connect(db)
    rebuild_at(conn, config.deliveries.launch_date, config)
    monkeypatch.setattr(launch, "train_model", lambda *args, **kwargs: trained)
    yield conn, config
    conn.close()


def run(conn, config):
    return fit_model(conn, config, run_key=RUN, as_of=config.deliveries.launch_date, source_sha256=SOURCE)


def state(conn, config):
    return {
        "seal": config.paths.seal.read_bytes() if config.paths.seal.exists() else None, "registry": conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0],
        "rates": conn.execute("SELECT COUNT(*) FROM baseline_rates").fetchone()[0], "artifact": (config.paths.models_dir / "v1.pkl").exists(),
    }


def assert_complete(conn, config, result, sealed_bytes):
    registered = load_registered_model(conn, config)             # the artifact has the registered fingerprint (M03)
    assert registered.artifact_sha256 == artifact_sha256((config.paths.models_dir / "v1.pkl").read_bytes()) == result["artifact_sha256"]
    assert config.paths.seal.read_bytes() == sealed_bytes, "the seal of the interrupted launch is the seal of the finished one, byte for byte"
    assert read_seal(config.paths.seal)["launch"]["config_sha256"] == result["config_sha256"] == registered.config_sha256
    assert conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0] == 1 and conn.execute("SELECT COUNT(*) FROM baseline_rates").fetchone()[0] >= 8
    assert result["mode"] == "launch" and result["seal_state"] == "match", "the repetition reproduces the sealed configuration and scores of V2"
    assert not conn.in_transaction


def test_a_failure_after_the_seal_and_before_the_artifact_leaves_a_seal_and_nothing_else_and_the_repetition_finishes_it(launching, monkeypatch):
    conn, config = launching
    real = launch.save_artifact

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(launch, "save_artifact", disk_full)
    with pytest.raises(OSError, match="disk full"):
        run(conn, config)
    after_crash = state(conn, config)
    assert after_crash["seal"] is not None and (after_crash["registry"], after_crash["rates"], after_crash["artifact"]) == (0, 0, False)
    assert read_seal(config.paths.seal)["launch"]["run_key"] == RUN and set(read_seal(config.paths.seal)) == {"version", "launch"}

    monkeypatch.setattr(launch, "save_artifact", real)
    result = run(conn, config)
    assert_complete(conn, config, result, after_crash["seal"])


def test_a_failure_inside_the_registration_leaves_the_seal_and_the_artifact_but_no_registry_and_the_repetition_finishes_it(launching, monkeypatch):
    conn, config = launching
    real = launch._relative
    calls = []

    def fails_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("killed while registering")
        return real(*args, **kwargs)

    monkeypatch.setattr(launch, "_relative", fails_once)
    with pytest.raises(RuntimeError, match="killed while registering"):
        run(conn, config)
    after_crash = state(conn, config)
    assert after_crash["seal"] is not None and after_crash["artifact"] is True and (after_crash["registry"], after_crash["rates"]) == (0, 0), "the transaction was undone"
    orphan = (config.paths.models_dir / "v1.pkl").read_bytes()
    assert not conn.in_transaction

    result = run(conn, config)
    assert_complete(conn, config, result, after_crash["seal"])
    assert (config.paths.models_dir / "v1.pkl").read_bytes() == orphan, "the artifact is the same bytes: the model is the one the interrupted launch trained"


def test_a_launch_that_finishes_is_not_a_launch_again_and_a_lost_seal_is_written_again_from_the_registry(launching):
    conn, config = launching
    first = run(conn, config)
    assert first["mode"] == "launch" and first["seal_state"] == "sealed"
    sealed = config.paths.seal.read_bytes()
    again = run(conn, config)
    assert again["mode"] == "reuse" and again["seal_state"] == "match" and config.paths.seal.read_bytes() == sealed
    config.paths.seal.unlink()                                   # the file is lost after a finished launch (this, not an interruption, is what the recovery branch of the reuse is for)
    restored = run(conn, config)
    assert restored["mode"] == "reuse" and restored["seal_state"] == "sealed" and read_seal(config.paths.seal)["launch"]["valid_v2_scores_sha256"] == first["valid_v2_scores_sha256"]
