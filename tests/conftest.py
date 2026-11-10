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
