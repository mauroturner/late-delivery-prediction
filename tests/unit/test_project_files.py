"""The environment and the project files (U01, item 5: delivery and environment): the pins of D28 and what is never versioned (RNF10)."""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]

# decision D28: the direct dependencies and the transitive ones, and nothing else
RUNTIME = {
    "numpy": "2.4.6", "pandas": "2.3.3", "scipy": "1.17.1", "scikit-learn": "1.9.1", "pyarrow": "26.0.1", "matplotlib": "3.11.2",
    "cloudpickle": "3.1.2", "contourpy": "1.3.3", "cycler": "0.12.1", "fonttools": "4.68.0", "joblib": "1.6.0", "kiwisolver": "1.5.1",
    "narwhals": "2.28.0", "packaging": "26.3", "pillow": "13.0.0", "pyparsing": "3.3.3", "python-dateutil": "2.9.0.post0", "pytz": "2026.6",
    "six": "1.17.0", "threadpoolctl": "3.8.0", "tzdata": "2026.6",
}
DEV = {"pytest": "9.2.0", "colorama": "0.4.6", "iniconfig": "2.3.1", "pluggy": "1.7.0", "Pygments": "2.21.0"}


def pins(name: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in (PROJECT / name).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-r "):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)", line)
        assert match, f"{name}: {line!r} is not an exact == pin"
        assert match.group(1) not in found, f"{name}: {match.group(1)} appears twice"
        found[match.group(1)] = match.group(2)
    return found


def test_runtime_requirements_are_exactly_the_pins_of_d28():
    assert pins("requirements.txt") == RUNTIME


def test_dev_requirements_include_the_runtime_file_and_add_only_the_test_tools():
    assert pins("requirements-dev.txt") == DEV
    assert "-r requirements.txt" in (PROJECT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()


def test_python_dateutil_is_not_the_4x_that_conflicts_with_airflow():
    """Project 01 D44 and project 02 D55: Airflow's own dependencies need dateutil below 3, and six comes with it."""
    runtime = pins("requirements.txt")
    assert runtime["python-dateutil"] == "2.9.0.post0" and runtime["six"] == "1.17.0"


def test_no_dependency_outside_the_declared_set_is_required():
    names = {n.lower() for n in {**RUNTIME, **DEV}}
    assert not ({"openpyxl", "lifetimes", "xgboost", "lightgbm", "requests", "httpx", "shap"} & names)


def test_installed_versions_match_the_pins():
    wrong = {}
    for name, version in {**RUNTIME, **DEV}.items():
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            wrong[name] = (version, installed)
    assert not wrong, f"installed versions differ from the pins (pinned, installed): {wrong}"


@pytest.mark.slow
def test_pip_check_reports_no_broken_requirements():
    result = subprocess.run([sys.executable, "-m", "pip", "check", "--disable-pip-version-check"], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No broken requirements found" in result.stdout


def test_pyproject_declares_the_package_the_pythons_and_the_marks():
    document = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    assert document["project"]["name"] == "latedel"
    assert document["project"]["requires-python"] == ">=3.11,<3.14"
    options = document["tool"]["pytest"]["ini_options"]
    assert "not real_data and not docker" in options["addopts"] and "--strict-markers" in options["addopts"]
    assert {m.split(":", 1)[0] for m in options["markers"]} == {"real_data", "docker", "slow"}
    assert options["pythonpath"] == ["src", "."]


@pytest.mark.parametrize("entry", [
    "data/raw/", "data/interim/", "data/warehouse/", "data/models/", "data/quick/", "airflow/logs/", "airflow/config/", "airflow/plugins/",
    "airflow/.env", "reports/validation/history/", ".venv/", "__pycache__/", ".pytest_cache/",
])
def test_gitignore_keeps_generated_state_and_the_environment_out_of_git(entry):
    assert entry in (PROJECT / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_the_sealed_reports_and_outputs_are_not_ignored():
    """The model seal, the reports, the Parquet and the site files are versioned (RNF10)."""
    ignored = set((PROJECT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert not ({"reports/", "outputs/", "reports/model_seal.json", "dashboard/"} & ignored)


def test_the_package_imports_from_the_source_folder(project_root):
    import latedel

    assert Path(latedel.__file__).resolve().parent == project_root / "src" / "latedel"
    assert latedel.__version__ == "0.1.0"
