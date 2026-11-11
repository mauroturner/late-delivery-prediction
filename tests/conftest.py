"""Shared fixtures and the automatic skipping of the real_data and docker marks."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REAL_CSV = PROJECT_ROOT / "data" / "raw" / "DataCoSupplyChainDataset.csv"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip, instead of failing, the tests whose external requirement is missing."""
    skips = {
        "real_data": (not REAL_CSV.exists(), "the real CSV is not in data/raw/"),
        "docker": (shutil.which("docker") is None, "docker is not installed"),
    }
    for item in items:
        for mark, (missing, reason) in skips.items():
            if mark in item.keywords and missing:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def cfg():
    """The default configuration, loaded once (it is immutable)."""
    from latedel.config import load_config

    return load_config(PROJECT_ROOT / "config" / "pipeline.toml")


# --- the synthetic fixture and its malformed variants ----------------------------------------------------------------


@pytest.fixture(scope="session")
def fixture_truth(tmp_path_factory: pytest.TempPathFactory):
    """The valid synthetic CSV (3000 orders, seed 0) and what it contains."""
    from tests.fixtures.make_fixture import make_fixture

    return make_fixture(tmp_path_factory.mktemp("fixture") / "dataco_fixture.csv", n_orders=3000, seed=0)


@pytest.fixture(scope="session")
def fixture_csv(fixture_truth) -> Path:
    return fixture_truth.path


@pytest.fixture(scope="session")
def malformed_bytes(fixture_csv: Path, tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], bytes]:
    """Factory: malformed_bytes(kind) -> the bytes of the valid fixture damaged in the given way, built once per session and kind."""
    from tests.fixtures.make_fixture import make_malformed

    cache: dict[str, bytes] = {}
    folder = tmp_path_factory.mktemp("malformed")

    def build(kind: str) -> bytes:
        if kind not in cache:
            cache[kind] = make_malformed(fixture_csv, kind, folder / f"{kind}.csv").read_bytes()
        return cache[kind]

    return build


@pytest.fixture()
def malformed(malformed_bytes: Callable[[str], bytes], tmp_path: Path) -> Callable[[str], Path]:
    """Factory: malformed(kind) -> path of a copy, in this test's folder, of the valid fixture damaged in the given way."""

    def build(kind: str) -> Path:
        target = tmp_path / f"{kind}.csv"
        target.write_bytes(malformed_bytes(kind))
        return target

    return build


@pytest.fixture(scope="session")
def fixture_read(cfg, fixture_csv):
    """``(SourceFrames, SourceReport)`` of the valid fixture, read once. Frames are shared: tests that change them copy first."""
    from latedel.ingest import read_source

    return read_source(fixture_csv, cfg)


@pytest.fixture(scope="session")
def real_read(cfg):
    """``(SourceFrames, SourceReport)`` of the real CSV, read once per session (needs data/raw/DataCoSupplyChainDataset.csv)."""
    from latedel.ingest import read_source

    return read_source(REAL_CSV, cfg)


# --- the deliveries of the synthetic fixture ----------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def delivered(cfg, fixture_read, tmp_path_factory: pytest.TempPathFactory):
    """The 15 deliveries of the synthetic fixture published once per session: ``(folder, plan, entries)``. Tests that change a file copy the folder first."""
    from latedel.deliveries import delivery_plan, publish

    folder = tmp_path_factory.mktemp("deliveries")
    plan = delivery_plan(cfg.deliveries)
    entries = [publish(spec, fixture_read[0], folder, source_sha256="ab" * 32) for spec in plan]
    return folder, plan, entries


# --- warehouses of the synthetic fixture, built once per session and copied by the tests that write -------------------------------------------------


@pytest.fixture(scope="session")
def launch_db(cfg, delivered, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A warehouse of the synthetic fixture that holds only the launch delivery (the test block does not exist in it). Tests that write copy it first."""
    from latedel.warehouse import connect
    from tests.fixtures.helpers import load_through

    path = tmp_path_factory.mktemp("launch_db") / "launch.sqlite"
    conn = connect(path)
    load_through(conn, delivered, cfg, 1)
    conn.close()
    return path


@pytest.fixture()
def launch_conn(launch_db: Path, tmp_path: Path):
    """A connection to a private copy of ``launch_db``."""
    from latedel.warehouse import connect

    target = tmp_path / "launch_copy.sqlite"
    shutil.copyfile(launch_db, target)
    conn = connect(target)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def real_launch_db(cfg, real_read, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A warehouse of the real file that holds only the launch delivery: the test labels are physically absent (D17). Needs the real CSV."""
    from latedel.deliveries import delivery_plan, publish, read_delivery
    from latedel.warehouse import connect, load_delivery

    folder = tmp_path_factory.mktemp("real_launch")
    spec = delivery_plan(cfg.deliveries)[0]
    entry = publish(spec, real_read[0], folder / "deliveries", source_sha256=cfg.source.accepted_sha256)
    conn = connect(folder / "real_launch.sqlite")
    load_delivery(conn, read_delivery(folder / "deliveries", entry), spec, f"real-{spec.delivery_id}", cfg, sha256_json=entry.sha256_json)
    conn.close()
    return folder / "real_launch.sqlite"


@pytest.fixture()
def real_launch_conn(real_launch_db: Path, tmp_path: Path):
    """A connection to a private copy of ``real_launch_db``."""
    from latedel.warehouse import connect

    target = tmp_path / "real_launch_copy.sqlite"
    shutil.copyfile(real_launch_db, target)
    conn = connect(target)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def control_run(cfg, tmp_path_factory: pytest.TempPathFactory):
    """Factory: control_run("standard_market") or control_run("none") -> a ``Control`` (12,000 synthetic orders, the launch and the full warehouse), built once per signal."""
    from tests.fixtures.model_helpers import build_control

    cache: dict = {}

    def build(signal: str):
        if signal not in cache:
            cache[signal] = build_control(signal, tmp_path_factory.mktemp(f"control_{signal}"), cfg)
        return cache[signal]

    return build


@pytest.fixture(scope="session")
def real_trained(cfg, real_launch_db: Path, tmp_path_factory: pytest.TempPathFactory):
    """The model of the real launch (24 candidates fitted on the training block, selected and calibrated on V1), trained once per session. Needs the real CSV."""
    from latedel.model import train_model
    from latedel.warehouse import connect

    conn = connect(real_launch_db)
    try:
        return train_model(conn, cfg.deliveries.launch_date, cfg)
    finally:
        conn.close()


@pytest.fixture(scope="session")
def tmp_paths_cfg(cfg):
    """Factory: tmp_paths_cfg(folder, **overrides) -> the configuration with a small grid and the seal and the models of the model in `folder` (never in the project)."""
    from dataclasses import replace

    from tests.fixtures.model_helpers import small_cfg

    def build(folder: Path, *, sets=("f0", "f1", "f2"), costs: dict | None = None):
        base = small_cfg(cfg, sets)
        base = replace(base, model=replace(base.model, max_iter=40))            # a few trees: these tests are about what is saved and sealed, not about the fit
        base = replace(base, paths=replace(base.paths, seal=folder / "reports" / "model_seal.json", models_dir=folder / "models"))
        return replace(base, costs=replace(base.costs, **costs)) if costs else base

    return build


@pytest.fixture(scope="session")
def fitted_launch(cfg, launch_db, tmp_paths_cfg, tmp_path_factory: pytest.TempPathFactory):
    """The fixture's model trained, registered and sealed once at the launch, with its seal and artifact in a temporary folder: ``(cfg, db path, result, folder)``.

    Tests that only read it share it; tests that change something copy the database and the folder first.
    """
    from types import SimpleNamespace

    from latedel.launch import fit_model
    from latedel.warehouse import connect

    folder = tmp_path_factory.mktemp("fitted_launch")
    config = tmp_paths_cfg(folder)
    db = folder / "fitted.sqlite"
    shutil.copyfile(launch_db, db)
    conn = connect(db)
    result = fit_model(conn, config, run_key="run-L2017-01-07", as_of=config.deliveries.launch_date, source_sha256="ab" * 32)
    conn.close()
    return SimpleNamespace(cfg=config, db=db, result=result, folder=folder)


@pytest.fixture(scope="session")
def real_fitted(cfg, real_launch_db: Path, real_read, tmp_path_factory: pytest.TempPathFactory):
    """The real model trained (24 candidates), registered and sealed once at the launch, with its seal and artifact in a temporary folder (never in the project):
    ``(cfg, db path, result, folder, plan, deliveries folder, entries)``. Needs the real CSV. Nothing of the test block is read."""
    from dataclasses import replace
    from types import SimpleNamespace

    from latedel.deliveries import delivery_plan, publish
    from latedel.launch import fit_model
    from latedel.warehouse import connect

    folder = tmp_path_factory.mktemp("real_fitted")
    config = replace(cfg, paths=replace(cfg.paths, seal=folder / "reports" / "model_seal.json", models_dir=folder / "models"))
    db = folder / "real.sqlite"
    shutil.copyfile(real_launch_db, db)
    conn = connect(db)
    result = fit_model(conn, config, run_key="real-L2017-01-07", as_of=config.deliveries.launch_date, source_sha256=config.source.accepted_sha256)
    conn.close()
    plan = delivery_plan(config.deliveries)
    entries = [publish(spec, real_read[0], folder / "deliveries", source_sha256=config.source.accepted_sha256) for spec in plan[:3]]
    return SimpleNamespace(cfg=config, db=db, result=result, folder=folder, plan=plan, deliveries=folder / "deliveries", entries=entries)


# --- the whole history of the synthetic fixture, loaded and scored month by month (U15 onwards) -----------------------------------------------------------


@pytest.fixture(scope="session")
def history(fitted_launch, delivered, tmp_path_factory: pytest.TempPathFactory):
    """The fixture's model (``fitted_launch``) with the 14 deliveries after the launch loaded and, for the monthly ones, scored by the frozen model in order.

    ``(cfg, db, folder, plan)`` plus ``snapshots``: ``{delivery_id: path}`` of the database as it was right after each delivery was loaded and scored, so that a test
    can ask what a later date would have changed. The seal and the models of its configuration are in its own folder (a copy of the launch's): tests that write copy
    the whole folder with ``copy_history``. Nothing here evaluates anything.
    """
    from dataclasses import replace
    from types import SimpleNamespace

    from latedel.deliveries import read_delivery
    from latedel.launch import load_registered_model
    from latedel.scoring import RunMeta, score_new_orders
    from latedel.warehouse import connect, load_delivery

    folder = tmp_path_factory.mktemp("history")
    shutil.copytree(fitted_launch.folder, folder / "launch", dirs_exist_ok=True)
    base = fitted_launch.cfg
    cfg = replace(base, paths=replace(base.paths, seal=folder / "launch" / "reports" / "model_seal.json", models_dir=folder / "launch" / "models"))
    db = folder / "history.sqlite"
    shutil.copyfile(fitted_launch.db, db)
    conn = connect(db)
    registered = load_registered_model(conn, cfg)
    deliveries, plan, entries = delivered
    snapshots = {}
    for index in range(1, len(plan)):
        spec = plan[index]
        load_delivery(conn, read_delivery(deliveries, entries[index]), spec, f"run-{spec.delivery_id}", cfg, sha256_json=entries[index].sha256_json)
        if spec.kind == "monthly":
            score_new_orders(conn, spec.delivery_id, registered.model, registered.threshold, RunMeta(f"run-{spec.delivery_id}", spec.as_of, registered.model_version))
        if spec.delivery_id in ("M2017-09", "M2017-10", "M2017-11"):
            snapshot = folder / f"after_{spec.delivery_id}.sqlite"
            conn.close()
            shutil.copyfile(db, snapshot)
            conn = connect(db)
            snapshots[spec.delivery_id] = snapshot
    conn.close()
    return SimpleNamespace(cfg=cfg, db=db, folder=folder, plan=plan, snapshots=snapshots, config_sha256=fitted_launch.result["config_sha256"])


@pytest.fixture()
def copy_history(history, tmp_path):
    """Factory: ``copy_history(snapshot=None, costs=None)`` -> ``(conn, cfg)`` on a private copy of the history (or of one of its snapshots) with its own seal and
    models folder; ``costs`` replaces keys of ``[costs]`` (another configuration than the sealed one)."""
    from dataclasses import replace

    from latedel.warehouse import connect

    opened = []

    def build(snapshot: str | None = None, costs: dict | None = None):
        target = tmp_path / f"copy_{len(opened)}"
        shutil.copytree(history.folder / "launch", target / "launch")
        db = target / "w.sqlite"
        shutil.copyfile(history.snapshots[snapshot] if snapshot else history.db, db)
        cfg = replace(history.cfg, paths=replace(history.cfg.paths, seal=target / "launch" / "reports" / "model_seal.json", models_dir=target / "launch" / "models"))
        cfg = replace(cfg, evaluation=replace(cfg.evaluation, bootstrap_resamples=200, importance_repeats=1))    # what these tests check does not depend on the count
        if costs:
            cfg = replace(cfg, costs=replace(cfg.costs, **costs))
        conn = connect(db)
        opened.append(conn)
        return conn, cfg

    yield build
    for conn in opened:
        conn.close()
