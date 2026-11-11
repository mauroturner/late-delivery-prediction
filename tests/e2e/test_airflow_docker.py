"""``airflow dags test`` in Docker, twice, in quick mode (U23, AC31, AC32). Skipped unless the Docker engine is running.

Run it with ``pytest -m docker``. It builds the image of ``airflow/`` (several minutes the first time), starts the database and the initialization service of the compose
file, runs ``pip check`` in the image, runs the DAG on a synthetic source twice in a row and checks that the first run is the launch, the second one the delivery
``M2017-01``, that what the launch fixed (the model artifact, its registry row and the seal) did not change, and that the new orders were scored. It writes only inside
``data/quick/`` (git-ignored), the ``airflow/logs``, ``airflow/config`` and ``airflow/plugins`` folders the containers use and the empty ``outputs``, ``reports`` and
``dashboard`` folders that the writable mounts create when they are missing, with throwaway secrets passed through the
environment (the four of the compose file, the password of the metadata database included), and removes the folders it created and the database volume at the end. The
image stays in the local cache (it is what makes the next run fast).
"""

from __future__ import annotations

import base64
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.make_fixture import make_fixture

PROJECT = Path(__file__).resolve().parents[2]
AIRFLOW = PROJECT / "airflow"
QUICK = PROJECT / "data" / "quick"
CREATED = ("logs", "config", "plugins")   # folders of airflow/ that the containers' bind mounts create
MOUNTED = ("outputs", "reports", "dashboard")   # folders of the project that the writable mounts create when they are missing (the quick run writes under data/quick)

pytestmark = pytest.mark.docker


BUILD_TIMEOUT_S = 1800   # the first build downloads the image and a few hundred packages
STEP_TIMEOUT_S = 900     # bringing the stack up, ``pip check`` and one ``dags test``
TEARDOWN_TIMEOUT_S = 300


CURRENT: dict[str, str] = {}   # the secrets of the test that is running: the same for every ``docker`` call of it (the database is initialised with one password)


def throwaway_secrets() -> dict[str, str]:
    """The four secrets of the compose file, new for each call: it has no default for any (AC32)."""
    return {
        "FERNET_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "AIRFLOW__API_AUTH__JWT_SECRET": secrets.token_urlsafe(48),
        "_AIRFLOW_WWW_USER_PASSWORD": secrets.token_urlsafe(16),
        "LATEDEL_DB_PASSWORD": secrets.token_hex(24),
    }


def docker(*args: str, timeout: int = STEP_TIMEOUT_S, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run ``docker`` with the throwaway secrets; a step that exceeds its own timeout fails naming the step, not as a generic timeout."""
    environment = {
        **os.environ,
        **(CURRENT if env is None else env),
        "ENV_FILE_PATH": str(AIRFLOW / ".env.example"),
        "LATEDEL_CONFIG": "/opt/latedel/config/pipeline.quick.toml",
    }
    try:
        return subprocess.run(["docker", *args], cwd=AIRFLOW, env=environment, capture_output=True, text=True, timeout=timeout, check=check, encoding="utf-8")
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"docker {' '.join(args)} did not end in {timeout} s (a stalled engine, not a failed test):\n" + str(error.stdout or "")[-1500:] + str(error.stderr or "")[-1500:])


def sqlite_rows(query: str) -> list[tuple]:
    conn = sqlite3.connect(f"{(QUICK / 'latedel.sqlite').as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def teardown(existing: set[str], existing_mounts: set[str]) -> list[str]:
    """Remove the stack, its database volume and the folders this test created; return what could not be done (empty: all clean).

    A failed teardown used to be invisible and the next run failed on a leftover folder with a message that hid the cause.
    """
    problems: list[str] = []
    try:
        down = docker("compose", "down", "--volumes", check=False, timeout=TEARDOWN_TIMEOUT_S)
        if down.returncode != 0:
            problems.append(f"docker compose down --volumes returned {down.returncode}:\n" + (down.stdout + down.stderr)[-2000:])
    except (Exception, pytest.fail.Exception) as error:   # a timeout of the step is reported by pytest.fail
        problems.append(f"docker compose down --volumes did not finish: {error}")
    leftovers = [QUICK] + [AIRFLOW / name for name in CREATED if name not in existing]
    for folder in leftovers:
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            problems.append(f"{folder} could not be removed (a file still open, or a permission)")
    for name in MOUNTED:   # an empty folder that did not exist before is removed; one with files in it is a quick run that wrote outside data/quick, and is reported
        folder = PROJECT / name
        if name not in existing_mounts and folder.exists():
            try:
                folder.rmdir()
            except OSError:
                problems.append(f"{folder} was created by the mounts and is not empty: {sorted(p.name for p in folder.iterdir())[:5]}")
    return problems


def dags_test() -> subprocess.CompletedProcess:
    return docker("compose", "--profile", "debug", "run", "--rm", "airflow-cli", "airflow", "dags", "test", "latedel_monthly", check=False)


@pytest.fixture(autouse=True)
def secrets_of_this_test():
    """New throwaway secrets for each test, the same ones for each of its commands: the PostgreSQL service takes its password when the volume is first initialised."""
    CURRENT.clear()
    CURRENT.update(throwaway_secrets())
    yield
    CURRENT.clear()


@pytest.fixture()
def engine():
    if shutil.which("docker") is None or subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("the Docker engine is not running")


def test_the_compose_file_needs_each_secret_and_puts_the_database_password_in_the_connection_string(engine) -> None:
    """The ``${VAR:?}`` interpolation works inside the connection string with this version of Docker Compose (the plan's open check, D23): without the password the file does
    not render, naming the variable; with it, the connection string and the PostgreSQL service carry it. Nothing is built or started."""
    given = dict(CURRENT)
    rendered = docker("compose", "config", env=given, check=False)
    assert rendered.returncode == 0, rendered.stderr[-2000:]
    assert f"postgresql+psycopg://airflow:{given['LATEDEL_DB_PASSWORD']}@postgres/airflow" in rendered.stdout
    assert f"POSTGRES_PASSWORD: {given['LATEDEL_DB_PASSWORD']}" in rendered.stdout
    for name in given:
        missing = {k: v for k, v in given.items() if k != name}
        refused = docker("compose", "config", env=missing, check=False)
        assert refused.returncode != 0 and name in refused.stderr, (name, refused.stderr[-800:])
        blank = docker("compose", "config", env={**given, name: ""}, check=False)
        assert blank.returncode != 0 and name in blank.stderr, (name, "empty", blank.stderr[-800:])   # ``:?`` refuses an empty value too


def test_the_dag_runs_the_launch_and_then_january_in_quick_mode_with_airflow_dags_test(engine) -> None:
    assert not QUICK.exists(), "data/quick is left over from another run (or from a teardown that failed: run `docker compose down --volumes` in airflow/ and delete it)"
    existing = {name for name in CREATED if (AIRFLOW / name).exists()}
    existing_mounts = {name for name in MOUNTED if (PROJECT / name).exists()}
    make_fixture(QUICK / "dataco_fixture.csv", n_orders=1200, seed=0)
    try:
        for step in (("compose", "build"), ("compose", "up", "airflow-init")):
            done = docker(*step, check=False, timeout=BUILD_TIMEOUT_S if step[1] == "build" else STEP_TIMEOUT_S)
            assert done.returncode == 0, f"docker {' '.join(step)} failed:\n" + (done.stdout + done.stderr)[-3000:]

        # every pin of requirements.txt installs next to Airflow's own dependencies on the Python 3.13 of the image, with none broken
        consistent = docker("compose", "--profile", "debug", "run", "--rm", "airflow-cli", "bash", "-c", "pip check", check=False)
        assert consistent.returncode == 0 and "No broken requirements found" in consistent.stdout, consistent.stdout[-2000:]

        first = dags_test()
        assert first.returncode == 0, first.stdout[-3000:] + first.stderr[-3000:]
        registry = "SELECT model_version, trained_as_of, artifact_path, artifact_sha256, config_sha256 FROM model_registry"
        launch_model = sqlite_rows(registry)
        launch_seal = (QUICK / "reports" / "model_seal.json").read_bytes()
        artifact = [p for p in (QUICK / "models").rglob("*") if p.is_file()]
        launch_artifact = {p.relative_to(QUICK).as_posix(): p.read_bytes() for p in artifact}
        assert [r[0] for r in sqlite_rows("SELECT delivery_id FROM pipeline_runs ORDER BY started_at, run_key")] == ["L2017-01-07"]
        assert len(launch_model) == 1 and launch_seal and launch_artifact
        assert sqlite_rows("SELECT COUNT(*) FROM model_scores") == [(0,)]   # the launch scores no order

        second = dags_test()
        assert second.returncode == 0, second.stdout[-3000:] + second.stderr[-3000:]
        runs = sqlite_rows("SELECT delivery_id, status, as_of_date FROM pipeline_runs ORDER BY as_of_date")
        assert runs == [("L2017-01-07", "ok", "2017-01-07"), ("M2017-01", "ok", "2017-01-31")]
        assert sqlite_rows("SELECT delivery_id, status FROM deliveries ORDER BY delivery_id") == [("L2017-01-07", "loaded"), ("M2017-01", "loaded")]
        # what the launch fixed does not move: the same registered model, the same artifact bytes and the same seal
        assert sqlite_rows(registry) == launch_model
        assert (QUICK / "reports" / "model_seal.json").read_bytes() == launch_seal
        assert {p.relative_to(QUICK).as_posix(): p.read_bytes() for p in (QUICK / "models").rglob("*") if p.is_file()} == launch_artifact
        # and the orders of the new delivery have their score
        orders, scored = sqlite_rows("SELECT (SELECT COUNT(*) FROM raw_orders WHERE delivery_id = 'M2017-01'), (SELECT COUNT(*) FROM model_scores)")[0]
        assert orders > 0 and scored == orders
        assert (QUICK / "outputs" / "powerbi" / "fact_order.parquet").is_file() and (QUICK / "dashboard" / "powerbi-design.html").is_file()
        assert (QUICK / "outputs" / "site" / "summary.json").is_file()
    finally:
        problems = teardown(existing, existing_mounts)
        if problems:   # shown in the report of this test (captured stderr) even when the test itself already failed
            sys.stderr.write("TEARDOWN FAILED:\n" + "\n".join(problems) + "\n")
    assert not problems, "the teardown failed, so the next run will find leftovers:\n" + "\n".join(problems)
