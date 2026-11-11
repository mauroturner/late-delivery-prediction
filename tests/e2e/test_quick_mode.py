"""The documented quick mode as separate processes: from another working directory it touches only ``data/quick/`` (U22, AC30, decisions D24 and D73).

Project 01 wrote its quick run over the versioned outputs and resolved a relative path against the directory of the command. This is the check that this project does
neither: the commands of the README, run with another working directory, leave every file outside ``data/quick/`` as it was (not a byte, not a modification time),
every versioned output of the full run (``outputs/``, ``reports/``, ``dashboard/``, ``docs/figures/``, sealed in U25) has the same SHA-256 before and after, and the
quick folder itself is removed at the end. The run is the launch and the delivery ``M2017-01`` of the shipped quick configuration over a small synthetic source; the tests
read its outputs and share the one run.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from latedel.dashboard.semantic_model import MODEL
from latedel.export import verify_star_schema
from latedel.pipeline import TASK_IDS

PROJECT = Path(__file__).resolve().parents[2]
QUICK = PROJECT / "data" / "quick"
SKIP = {".venv", "__pycache__", ".pytest_cache", ".git", "quick"}
ORDERS = "1200"          # a small source: the point is where the files go, not what the model learns
VERSIONED = ("outputs", "reports", "dashboard", "docs/figures")   # the folders of the full run's versioned outputs (D81)
UNVERSIONED = "reports/validation/history/"   # the validation report of every run, kept on the machine and never versioned (.gitignore)
SEALED_RUN_FILES = 39    # what U25 versioned there: 20 Parquet, the 5 site files and their manifest, the HTML, the 4 reports, the seal and 7 figures


def python_m(*args: str, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(PROJECT / "src"), "PYTHONIOENCODING": "utf-8"}
    env.pop("LATEDEL_CONFIG", None)
    env.pop("LATEDEL_HOME", None)
    return subprocess.run([sys.executable, "-m", "latedel", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, encoding="utf-8")


def in_process(*args: str) -> SimpleNamespace:
    """A command of the CLI run in this process (``main(argv)``), with what it wrote: the same command as ``python -m latedel``, without starting an interpreter."""
    from latedel.cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args))
    return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())


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


def versioned_outputs() -> dict[str, str]:
    """The SHA-256 of every versioned file of the full run, by its path in the project (review of U25, R3-quick-mode-coverage)."""
    found = {}
    for top in VERSIONED:
        for path in sorted((PROJECT / top).rglob("*")):
            name = path.relative_to(PROJECT).as_posix()
            if path.is_file() and not name.startswith(UNVERSIONED):
                found[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


@pytest.fixture(scope="module")
def quick(tmp_path_factory):
    """The quick commands, run from an empty folder that is not the project: the fixture (written from the project folder, as the README says) and a replay up to ``M2017-01``."""
    assert not QUICK.exists(), "data/quick is left over from another run"
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    before, sealed = fingerprint(), versioned_outputs()
    try:
        made = in_process("make-fixture", "data/quick/dataco_fixture.csv", "--orders", ORDERS)   # one process less: the command resolves the path against the project
        config = str(PROJECT / "config" / "pipeline.quick.toml")
        replayed = python_m("replay", "--config", config, "--until", "M2017-01", cwd=elsewhere)   # the launch and the delivery of January, in one process
        yield {"made": made, "replayed": replayed, "before": before, "after": fingerprint(), "elsewhere": elsewhere, "sealed": sealed, "sealed_after": versioned_outputs()}
    finally:
        shutil.rmtree(QUICK, ignore_errors=True)


def test_quick_replay_from_other_cwd_touches_only_data_quick(quick, project_seal_unchanged):
    assert quick["made"].returncode == 0, quick["made"].stderr
    assert quick["replayed"].returncode == 0, quick["replayed"].stderr
    assert quick["after"] == quick["before"]    # not a byte, not a modification time, outside data/quick/
    assert list(quick["elsewhere"].iterdir()) == []    # and nothing in the directory the commands were started from
    assert (QUICK / "latedel.sqlite").is_file() and (QUICK / "outputs" / "powerbi" / "fact_order.parquet").is_file()
    assert (QUICK / "outputs" / "site" / "summary.json").is_file() and (QUICK / "dashboard" / "powerbi-design.html").is_file()
    assert (QUICK / "reports" / "evaluation_summary.json").is_file() and (QUICK / "reports" / "model_seal.json").is_file()   # the seal of the quick run, in the quick folder
    assert project_seal_unchanged()   # the versioned seal is U25's: the quick run leaves it as it found it


def test_quick_replay_leaves_every_versioned_output_of_the_full_run_byte_identical(quick):
    """Review of U25, R3-quick-mode-coverage: the test used to assert that no ``outputs`` folder existed at the project root; since U25 the outputs are versioned, so the
    check is that a quick replay leaves each of them with the SHA-256 it had before (the size and the modification time are the fingerprint's)."""
    assert quick["replayed"].returncode == 0, quick["replayed"].stderr
    assert len(quick["sealed"]) == SEALED_RUN_FILES, sorted(quick["sealed"])
    for name in ("outputs/powerbi/fact_order.parquet", "outputs/site/manifest.json", "dashboard/powerbi-design.html", "reports/model_seal.json", "docs/figures/mode_rates.png"):
        assert name in quick["sealed"], name
    assert quick["sealed_after"] == quick["sealed"]


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
