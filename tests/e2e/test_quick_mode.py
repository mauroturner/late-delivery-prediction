"""The documented quick mode as separate processes: from another working directory it touches only ``data/quick/`` (U22, AC30, decisions D24 and D73).

Project 01 wrote its quick run over the versioned outputs and resolved a relative path against the directory of the command. This is the check that this project does
neither: the commands of the README, run with another working directory, leave every file outside ``data/quick/`` as it was (not a byte, not a modification time), the
seal and the reports of the full run are not created, and the quick folder itself is removed at the end. The run is the launch and the delivery ``M2017-01`` of the
shipped quick configuration over a small synthetic source; the tests read its outputs and share the one run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from latedel.dashboard.semantic_model import MODEL
from latedel.export import verify_star_schema
from latedel.pipeline import TASK_IDS

PROJECT = Path(__file__).resolve().parents[2]
QUICK = PROJECT / "data" / "quick"
SKIP = {".venv", "__pycache__", ".pytest_cache", ".git", "quick"}
ORDERS = "1200"          # a small source: the point is where the files go, not what the model learns


def python_m(*args: str, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT / "src"), "PYTHONIOENCODING": "utf-8"}
    env.pop("LATEDEL_CONFIG", None)
    env.pop("LATEDEL_HOME", None)
    return subprocess.run([sys.executable, "-m", "latedel", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, encoding="utf-8")


def fingerprint() -> dict[str, tuple[int, int]]:
    """Size and modification time of every file of the project outside the folders that are not its state (the environment, caches, the quick folder)."""
    found = {}
    for folder, names, files in os.walk(PROJECT):
        names[:] = [n for n in names if n not in SKIP]   # do not even walk the environment
        for name in files:
            path = Path(folder) / name
            stat = path.stat()
            found[path.relative_to(PROJECT).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return found


@pytest.fixture(scope="module")
def quick(tmp_path_factory):
    """The quick commands, run from an empty folder that is not the project: the fixture (written from the project folder, as the README says) and a replay up to ``M2017-01``."""
    assert not QUICK.exists(), "data/quick is left over from another run"
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    before = fingerprint()
    try:
        made = python_m("make-fixture", "data/quick/dataco_fixture.csv", "--orders", ORDERS, cwd=PROJECT, timeout=120)
        config = str(PROJECT / "config" / "pipeline.quick.toml")
        replayed = python_m("replay", "--config", config, "--until", "M2017-01", cwd=elsewhere)   # the launch and the delivery of January, in one process
        yield {"made": made, "replayed": replayed, "before": before, "after": fingerprint(), "elsewhere": elsewhere}
    finally:
        shutil.rmtree(QUICK, ignore_errors=True)


def test_quick_replay_from_other_cwd_touches_only_data_quick(quick):
    assert quick["made"].returncode == 0, quick["made"].stderr
    assert quick["replayed"].returncode == 0, quick["replayed"].stderr
    assert quick["after"] == quick["before"]    # not a byte, not a modification time, outside data/quick/
    assert list(quick["elsewhere"].iterdir()) == []    # and nothing in the directory the commands were started from
    assert (QUICK / "latedel.sqlite").is_file() and (QUICK / "outputs" / "powerbi" / "fact_order.parquet").is_file()
    assert (QUICK / "outputs" / "site" / "summary.json").is_file() and (QUICK / "dashboard" / "powerbi-design.html").is_file()
    assert (QUICK / "reports" / "evaluation_summary.json").is_file() and (QUICK / "reports" / "model_seal.json").is_file()   # the seal of the quick run, in the quick folder
    assert not (PROJECT / "reports" / "model_seal.json").exists() and not (PROJECT / "outputs").exists()   # the versioned ones are U25's


def test_the_quick_flow_ran_every_task_for_the_launch_and_for_january(quick):
    out = quick["replayed"].stdout
    assert [line.split()[0] for line in out.splitlines() if line.strip() and not line.startswith(("entrega", "2 corridas"))] == list(TASK_IDS) * 2
    assert out.index("entrega L2017-01-07") < out.index("entrega M2017-01") and out.rstrip().endswith("2 corridas")
    assert verify_star_schema(QUICK / "outputs" / "powerbi", MODEL) == []


def test_the_quick_folder_is_the_only_place_the_configuration_writes(quick):
    """Every path of ``pipeline.quick.toml`` is under ``data/quick``, including the README and the figures, which this run does not write."""
    from latedel.config import load_config

    cfg = load_config(PROJECT / "config" / "pipeline.quick.toml")
    paths = [cfg.source.local_path, *vars(cfg.paths).values()]
    assert all(Path(p).resolve().is_relative_to(QUICK.resolve()) for p in paths), [str(p) for p in paths]
    assert not cfg.source.url and cfg.source.mode == "local"
