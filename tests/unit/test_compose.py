"""The Airflow compose file and its image: nothing exposed beyond the machine, no secret with a value (the password of the metadata database included), read-only code, the
same pins as the command line (U23, AC32, decision D23)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
AIRFLOW = PROJECT / "airflow"
SECRETS = ("FERNET_KEY", "AIRFLOW__API_AUTH__JWT_SECRET", "_AIRFLOW_WWW_USER_PASSWORD", "LATEDEL_DB_PASSWORD")
REQUIRED = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*):\?(?P<message>[^}]*)\}")   # ${NAME:?message}: the file refuses to start without NAME
SECRET_NAME = re.compile(r"PASSWORD|SECRET|KEY|TOKEN", re.IGNORECASE)
# scheme://user:password@host; the password is a whole ${...} interpolation (whose message may have spaces and slashes) or a run of characters that a URL allows
USERINFO = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://(?P<user>[^:/@\s]*):(?P<password>\$\{[^}]*\}|[^@/\s]+)@")


def code_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def values_of(text: str) -> list[tuple[str, str]]:
    """Every value of the file with the key that holds it: ``key: value`` pairs, ``- KEY=value`` and ``- value`` list items. The file is read line by line (it has no
    multi-line scalar that carries a value except the script of the init service, whose lines are read as plain values with no key)."""
    found: list[tuple[str, str]] = []
    for line in code_lines(text):
        body = line.strip()
        item = re.match(r"-\s+(?P<value>.+)$", body)
        if item:
            body = item.group("value")
            pair = re.match(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$", body)
            found.append((pair.group("key"), pair.group("value")) if pair else ("", body))
            continue
        pair = re.match(r"(?P<key>[A-Za-z_<][A-Za-z0-9_<>*-]*):\s*(?P<value>.*)$", body)
        if pair and pair.group("value"):
            found.append((pair.group("key"), pair.group("value").strip().strip("'\"")))
        else:
            found.append(("", body))
    return found


def find_literal_secrets(text: str) -> list[str]:
    """What in the file is a secret with a value: the value of a key that looks like a secret (``PASSWORD``, ``SECRET``, ``KEY``, ``TOKEN``) that is not ``${NAME:?...}``, a
    password inside a connection string that is not ``${NAME:?...}``, and a ``${NAME}`` or ``${NAME:-default}`` of a secret (a default is a literal)."""
    problems = []
    for key, value in values_of(text):
        if key and SECRET_NAME.search(key) and not REQUIRED.fullmatch(value) and not re.fullmatch(r"\$\{[A-Z_]+:-[^}]*\}", value) and value:
            problems.append(f"{key}: a secret key with the value {value!r}")
        if key and SECRET_NAME.search(key) and re.fullmatch(r"\$\{[A-Za-z_]+:-[^}]*\}", value):
            problems.append(f"{key}: a secret with a default ({value})")
        for match in USERINFO.finditer(value):
            if not REQUIRED.fullmatch(match.group("password")):
                problems.append(f"{key or 'value'}: a password in a connection string, {match.group('password')!r}")
    for name in SECRETS:
        for use in re.findall(r"\$\{" + re.escape(name) + r"(?!:\?)[^}]*\}", text):
            problems.append(f"{name}: used without ':?' in {use}")
    return problems


@pytest.fixture(scope="module")
def compose() -> str:
    return (AIRFLOW / "docker-compose.yaml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (AIRFLOW / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def env_example() -> str:
    return (AIRFLOW / ".env.example").read_text(encoding="utf-8")


def test_ui_bound_to_localhost_and_secrets_required(compose, env_example):
    published = re.findall(r'^\s*-\s*"?(\d[\d.]*:\d+:\d+|\d+:\d+)"?\s*$', "\n".join(code_lines(compose)), flags=re.MULTILINE)
    assert published and all(entry.startswith("127.0.0.1:") for entry in published)
    lines = code_lines(compose)
    for name in SECRETS:
        uses = [line for line in lines if "${" + name in line]
        assert uses, name
        assert all("${" + name + ":?" in line for line in uses), f"{name} needs a required variable (${{{name}:?...}}), not a default"
        assert re.search(rf"^{name}=\s*$", env_example, flags=re.MULTILINE), f"{name} must be listed, empty, in .env.example"
    # nothing else in the example carries a value: it is a template, not a set of credentials
    assert [line for line in code_lines(env_example) if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=", line.strip())] == []
    assert [line.split("=")[0] for line in code_lines(env_example)] == list(SECRETS)   # exactly the four, in this order


def test_no_literal_secret_including_the_metadata_db_password(compose):
    """Every value of the compose file is read: no secret has a value in it. The password of the metadata database (project 02 left ``airflow``/``airflow`` and its D68
    pending) comes from the environment in the PostgreSQL service and inside the connection string of the Airflow services."""
    assert find_literal_secrets(compose) == []
    values = values_of(compose)
    assert len(values) > 80   # the walk reads the whole file, not a few lines
    database = [(k, v) for k, v in values if "LATEDEL_DB_PASSWORD" in v]
    assert {k for k, _ in database} == {"POSTGRES_PASSWORD", "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"}
    [connection] = [v for k, v in database if k == "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"]
    assert re.fullmatch(r"postgresql\+psycopg://airflow:\$\{LATEDEL_DB_PASSWORD:\?[^}]+\}@postgres/airflow", connection)
    assert not re.search(r"airflow:airflow@|POSTGRES_PASSWORD:\s*airflow\b", compose)   # the literal of project 02 is gone
    for name in SECRETS:
        assert any(f"${{{name}:?" in v for _, v in values), name


@pytest.mark.parametrize(
    ("find", "replace"),
    [
        ("POSTGRES_PASSWORD: ${LATEDEL_DB_PASSWORD:?set LATEDEL_DB_PASSWORD in airflow/.env (see .env.example)}", "POSTGRES_PASSWORD: airflow"),
        ("POSTGRES_PASSWORD: ${LATEDEL_DB_PASSWORD:?set LATEDEL_DB_PASSWORD in airflow/.env (see .env.example)}", "POSTGRES_PASSWORD: ${LATEDEL_DB_PASSWORD:-airflow}"),
        ("airflow:${LATEDEL_DB_PASSWORD:?set LATEDEL_DB_PASSWORD in airflow/.env (see .env.example)}@postgres", "airflow:airflow@postgres"),
        ("airflow:${LATEDEL_DB_PASSWORD:?set LATEDEL_DB_PASSWORD in airflow/.env (see .env.example)}@postgres", "airflow:${LATEDEL_DB_PASSWORD}@postgres"),
        ("AIRFLOW__CORE__FERNET_KEY: ${FERNET_KEY:?set FERNET_KEY in airflow/.env (see .env.example)}", "AIRFLOW__CORE__FERNET_KEY: 0123456789abcdef"),
        ("AIRFLOW__API_AUTH__JWT_SECRET: ${AIRFLOW__API_AUTH__JWT_SECRET:?set AIRFLOW__API_AUTH__JWT_SECRET in airflow/.env (see .env.example)}", "AIRFLOW__API_AUTH__JWT_SECRET: secret"),
        ("_AIRFLOW_WWW_USER_PASSWORD: ${_AIRFLOW_WWW_USER_PASSWORD:?set _AIRFLOW_WWW_USER_PASSWORD in airflow/.env (see .env.example)}", "_AIRFLOW_WWW_USER_PASSWORD: airflow"),
    ],
)
def test_the_walk_finds_a_secret_that_gets_a_value_or_a_default(compose, find, replace):
    """The check is not vacuous: each way a secret could get a value (a literal, a default, no ``:?``, a literal inside the connection string) is found."""
    assert compose.count(find) == 1, find
    assert find_literal_secrets(compose.replace(find, replace)), replace


def test_the_code_and_the_configuration_are_mounted_read_only_and_only_the_outputs_are_writable(compose):
    mounts = [line.split("-", 1)[1].strip() for line in code_lines(compose) if re.match(r"\s*-\s*\.\./", line)]
    assert not [m for m in mounts if m.split(":")[0] in ("..", "../")]   # the whole project, writable, is not mounted
    read_only = {m.split(":")[0] for m in mounts if m.endswith(":ro")}
    writable = {m.split(":")[0] for m in mounts if not m.endswith(":ro")}
    assert {"../src", "../config", "../sql"} <= read_only
    assert writable == {"../data", "../outputs", "../reports", "../dashboard"}
    assert all(m.split(":")[1].startswith("/opt/latedel/") for m in mounts)


def test_the_pipeline_finds_its_code_and_its_configuration_inside_the_container(compose):
    text = "\n".join(code_lines(compose))
    for fragment in (
        "PYTHONPATH: /opt/latedel/src", "LATEDEL_HOME: /opt/latedel", "LATEDEL_CONFIG: ${LATEDEL_CONFIG:-/opt/latedel/config/pipeline.toml}",
        "AIRFLOW__CORE__EXECUTOR: LocalExecutor", "AIRFLOW__CORE__LOAD_EXAMPLES: 'false'", "image: latedel-airflow:3.4.0",
    ):
        assert fragment in text, fragment
    assert "custseg" not in text.lower() and "oilgas" not in text.lower()
    assert re.search(r"^name: latedel-airflow$", compose, flags=re.MULTILINE)   # its own volume and network, apart from other Airflow projects


def test_the_external_images_are_pinned_by_version_and_by_digest(compose, dockerfile):
    external = re.findall(r"^\s*image:\s*(postgres\S+)\s*$", compose, flags=re.MULTILINE) + re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)
    assert len(external) == 2
    for image in external:
        assert re.fullmatch(r"[a-z/]+:\d+(\.\d+)+@sha256:[0-9a-f]{64}", image), f"{image}: needs an explicit version tag and a digest"
    own = re.findall(r"^\s*image:\s*(latedel\S+)\s*$", compose, flags=re.MULTILINE)
    assert own == ["latedel-airflow:3.4.0"]   # the image this folder builds: a version, no registry digest to pin


def test_the_airflow_image_installs_the_exact_pins_of_the_command_line(compose, dockerfile):
    assert "COPY requirements.txt /requirements.txt" in dockerfile
    assert 'RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /requirements.txt' in dockerfile
    assert re.search(r"^ARG AIRFLOW_VERSION=3\.4\.0$", dockerfile, flags=re.MULTILINE) and re.search(r"^FROM apache/airflow:3\.4\.0@", dockerfile, flags=re.MULTILINE)
    assert not (AIRFLOW / "requirements-airflow.txt").exists()   # no second list with ranges
    pins = code_lines((PROJECT / "requirements.txt").read_text(encoding="utf-8"))
    assert pins and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][^\s]*", line) for line in pins)
    assert re.search(r"context:\s*\.\.\s*\n\s*dockerfile:\s*airflow/Dockerfile", compose)   # the build sees requirements.txt


def test_the_build_context_lets_in_only_requirements_txt():
    rules = code_lines((PROJECT / ".dockerignore").read_text(encoding="utf-8"))
    assert rules == ["*", "!requirements.txt"]


def test_the_database_publishes_no_port_so_its_credentials_stay_on_the_compose_network(compose):
    """Only the web UI is published (to 127.0.0.1); the database lives on the internal network, whatever its password."""
    block = re.search(r"^  postgres:\n(.*?)^  airflow-apiserver:", compose, flags=re.MULTILINE | re.DOTALL)
    assert block, "the postgres service"
    assert not re.search(r"^\s*(ports|expose|network_mode):", block.group(1), flags=re.MULTILINE)
    published = [m for m in re.findall(r"^\s*ports:\s*$", compose, flags=re.MULTILINE)]
    assert len(published) == 1   # the api server's, and nothing else


def test_the_dags_are_mounted_read_only_and_the_init_does_not_chown_them(compose):
    """``./dags`` writable inside every Airflow container would let a compromised task rewrite the DAG that runs the next one."""
    local = [line.split("-", 1)[1].strip() for line in code_lines(compose) if re.match(r"\s*-\s*\./", line)]
    dags = [m for m in local if m.startswith("./dags:")]
    assert dags == ["./dags:/opt/airflow/dags:ro"]
    assert {m.split(":")[0] for m in local if not m.endswith(":ro")} == {"./logs", "./config"}   # what Airflow itself writes (./plugins is read-only since H8)
    chown = [line for line in code_lines(compose) if "chown" in line]
    assert len(chown) == 1 and "dags" not in chown[0]   # a read-only mount cannot be chowned: the init would fail


def test_only_the_init_service_builds_the_image_so_a_cold_build_does_not_race(compose):
    """Five services with the same ``build`` and the same tag build it at the same time, and on a cold cache all but one fail with ``image ... already exists`` (found by
    the Docker test the first time it ran, D74). One service builds; the others only name the tag."""
    assert len(re.findall(r"^\s*build:\s*$", compose, flags=re.MULTILINE)) == 1
    init = re.search(r"^  airflow-init:\n(.*?)^  # `docker compose --profile debug", compose, flags=re.MULTILINE | re.DOTALL)
    assert init and re.search(r"build:\s*\n\s*context:\s*\.\.\s*\n\s*dockerfile:\s*airflow/Dockerfile", init.group(1))
    common = re.search(r"^x-airflow-common:\n(.*?)^services:", compose, flags=re.MULTILINE | re.DOTALL)
    assert common and "build:" not in common.group(1) and "image: latedel-airflow:3.4.0" in common.group(1)


def init_script(compose: str) -> str:
    """The shell script the init service runs: the block scalar after ``- |`` under its ``command``."""
    init = re.search(r"^  airflow-init:\n(.*?)^  # `docker compose --profile debug", compose, flags=re.MULTILINE | re.DOTALL)
    assert init
    block = re.search(r"^      - \|\n((?:^        .*\n)+)", init.group(1), flags=re.MULTILINE)
    assert block
    return "".join(line[8:] + "\n" for line in block.group(1).splitlines())


def test_the_init_script_stops_at_its_first_failed_command(compose):
    """The other four services start only when the init exits 0 (``service_completed_successfully``). Without ``set -e`` a failed migration in ``/entrypoint`` is
    followed by a ``chown`` that succeeds, and the script exits 0 (review of U23, R3-airflow-init-script-swallows-failure)."""
    script = init_script(compose)
    assert script.splitlines()[0] == "set -euo pipefail"
    assert script.count("depends_on") == 0 and len(re.findall(r"condition: service_completed_successfully", compose)) == 4


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to run the init script")
def test_a_failed_migration_makes_the_init_exit_non_zero(compose, tmp_path):
    """Runs the script with ``/entrypoint`` replaced by a command that fails, as a failed ``airflow db migrate`` does, and ``chown`` by one that succeeds."""
    stub = 'failed_migration() { echo "migration failed" >&2; return 3; }\n'
    script = init_script(compose).replace("/opt/airflow", "./airflow").replace("/entrypoint", "failed_migration").replace("chown ", "true ")
    done = subprocess.run(["bash", "-c", stub + script], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert done.returncode == 3, done.stdout + done.stderr
    assert done.stderr.count("migration failed") == 1   # the second /entrypoint call never runs
    assert (tmp_path / "airflow" / "logs").is_dir()   # the commands before the failure did run


# --- H8: the advisories of the review of U23 -----------------------------------------------------------------------------------------------------------


def service_block(compose: str, name: str) -> str:
    """The text of one service of the compose file, comments included, up to the next service (or the end of ``services``)."""
    found = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z#]|^\S)", compose, flags=re.MULTILINE | re.DOTALL)
    assert found, name
    return found.group(1)


def test_the_plugins_are_read_only_and_only_what_airflow_writes_is_writable(compose):
    """R1-001: ``./plugins`` was writable by every Airflow container, and Airflow loads the code dropped there. Airflow does not write it, so it is read-only like
    ``./dags``; ``./logs`` and ``./config`` stay writable because Airflow writes them (the logs of the tasks, and ``airflow.cfg``, which it creates in the folder of
    ``AIRFLOW_CONFIG`` when it is missing), and the init no longer changes the owner of the read-only folders."""
    local = [line.split("-", 1)[1].strip() for line in code_lines(compose) if re.match(r"\s*-\s*\./", line)]
    assert "./plugins:/opt/airflow/plugins:ro" in local and "./dags:/opt/airflow/dags:ro" in local
    assert {m.split(":")[0] for m in local if not m.endswith(":ro")} == {"./logs", "./config"}
    assert "AIRFLOW_CONFIG: '/opt/airflow/config/airflow.cfg'" in compose
    [chown] = [line for line in init_script(compose).splitlines() if "chown" in line]
    assert "plugins" not in chown and "dags" not in chown and "logs" in chown and "config" in chown


def test_the_debug_service_says_it_runs_outside_the_guard_of_one_active_run(compose):
    """R4-airflow-cli-sqlite-writer-bypass: ``airflow dags test`` in the debug service runs outside the scheduler and its ``max_active_runs=1``, so next to a running
    scheduler there would be two writers of the SQLite warehouse. Its ``../data`` stays writable (``dags test`` writes the warehouse, and the Docker test runs it); the
    comment over the service says to stop the scheduler first."""
    text = compose[compose.index("  # `docker compose --profile debug"):]
    comment = " ".join(line.strip().lstrip("#").strip() for line in text.splitlines()[:8] if line.strip().startswith("#"))
    for needle in ("max_active_runs", "SQLite", "docker compose stop airflow-scheduler", "runbook"):
        assert needle in comment, needle


def test_the_compose_file_says_how_to_set_airflow_uid(compose):
    """R3-airflow-uid-default-ownership: every Airflow container runs as ``${AIRFLOW_UID:-50000}``; on Linux the folders the bind mounts create belong to that user.
    The compose file says to set it to the user's own id (the line for ``.env.example`` is the owner's: this unit does not touch that file)."""
    common = re.search(r"^x-airflow-common:\n(.*?)^services:", compose, flags=re.MULTILINE | re.DOTALL).group(1)
    assert 'user: "${AIRFLOW_UID:-50000}:0"' in common
    comments = " ".join(line.strip() for line in common.splitlines() if line.strip().startswith("#"))
    assert "AIRFLOW_UID" in comments and "id -u" in comments and "Linux" in comments


def test_the_userinfo_detector_reads_the_real_connection_string_whole(compose):
    """R3-userinfo-regex-blind-spot: the password of ``scheme://user:password@`` was ``[^@/\\s]+``, so the real ``${LATEDEL_DB_PASSWORD:?message}``, whose message has
    spaces and ``/``, never matched: the check passed without looking at the real string. Now the interpolation is read whole and is the required variable."""
    [connection] = [v for k, v in values_of(compose) if k == "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"]
    [match] = list(USERINFO.finditer(connection))
    assert match.group("user") == "airflow" and REQUIRED.fullmatch(match.group("password"))
    assert match.group("password").startswith("${LATEDEL_DB_PASSWORD:?") and " " in match.group("password") and "/" in match.group("password")


@pytest.mark.parametrize("literal", ["hunter2", "${LATEDEL_DB_PASSWORD:-airflow}", "${LATEDEL_DB_PASSWORD}x", "pa%2Fss"])
def test_a_literal_password_in_the_real_connection_string_is_found(compose, literal):
    [connection] = [v for k, v in values_of(compose) if k == "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN"]
    password = re.search(r"airflow:(\$\{LATEDEL_DB_PASSWORD:\?[^}]*\})@postgres", connection).group(1)
    mutated = compose.replace(f"airflow:{password}@postgres", f"airflow:{literal}@postgres", 1)
    assert mutated != compose
    assert any("a password in a connection string" in p or "a secret with a default" in p or "used without ':?'" in p for p in find_literal_secrets(mutated)), literal
