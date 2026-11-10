"""H1 (R3-004): the hook of ``tests/conftest.py`` that skips, instead of failing, the tests whose external requirement is missing.

The default run deselects ``real_data`` and ``docker`` before the hook matters, so the one behaviour the hook exists for (a developer who overrides ``-m`` on a
machine without the CSV or without Docker) was never run. Two tests run it: the hook itself with stand-in test items, and a real pytest run in a copy of the
project that has no CSV.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from tests import conftest

PROJECT = Path(__file__).resolve().parents[2]


class Item:
    def __init__(self, *marks: str) -> None:
        self.keywords = set(marks)
        self.added: list = []

    def add_marker(self, marker) -> None:
        self.added.append(marker)


def run_hook(*items: Item) -> None:
    conftest.pytest_collection_modifyitems(None, list(items))  # type: ignore[arg-type]


def reasons(item: Item) -> list[str]:
    return [m.kwargs["reason"] for m in (a.mark for a in item.added)]


def test_a_real_data_test_is_skipped_with_its_reason_when_the_csv_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "REAL_CSV", tmp_path / "absent.csv")
    real, docker, plain, both = Item("real_data"), Item("docker"), Item("slow"), Item("real_data", "docker")
    run_hook(real, docker, plain, both)
    assert reasons(real) == ["the real CSV is not in data/raw/"]
    assert plain.added == [], "a test with no external requirement is left alone"
    assert "the real CSV is not in data/raw/" in reasons(both)


def test_a_real_data_test_is_kept_when_the_csv_is_there(tmp_path, monkeypatch):
    present = tmp_path / "x.csv"
    present.write_bytes(b"x")
    monkeypatch.setattr(conftest, "REAL_CSV", present)
    real = Item("real_data")
    run_hook(real)
    assert real.added == []


def test_a_docker_test_is_skipped_when_docker_is_not_installed_and_kept_when_it_is(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    missing = Item("docker")
    run_hook(missing)
    assert reasons(missing) == ["docker is not installed"]
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    present = Item("docker")
    run_hook(present)
    assert present.added == []


def test_a_pytest_run_with_the_marks_enabled_skips_instead_of_failing_without_the_csv(tmp_path):
    """The behaviour end to end, in a copy of the project with no data folder: ``-m real_data`` reports skips and exits 0."""
    copy = tmp_path / "project"
    copy.mkdir()
    for name in ("src", "tests", "sql", "config"):
        shutil.copytree(PROJECT / name, copy / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy(PROJECT / "pyproject.toml", copy)
    assert not (copy / "data").exists()
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--basetemp", str(tmp_path / "bt"), "-rs", "-q", "-m", "real_data or docker",
         "tests/contract/test_validate_source.py"],
        cwd=copy, capture_output=True, text=True, timeout=300, check=False,
    )
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-800:]
    assert "the real CSV is not in data/raw/" in done.stdout and " skipped" in done.stdout and "failed" not in done.stdout.splitlines()[-1]


def test_the_hook_marks_with_a_skip_not_an_xfail():
    item = Item("real_data")
    original = conftest.REAL_CSV
    try:
        conftest.REAL_CSV = Path("definitely-absent.csv")
        run_hook(item)
    finally:
        conftest.REAL_CSV = original
    assert item.added[0].mark.name == "skip"
