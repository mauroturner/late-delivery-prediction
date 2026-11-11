"""Small helpers shared by the tests that need a warehouse with deliveries in it."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

# The part of the warehouse that the delivery rules read (docs/plan.md, section 5): used by the tests of the rules before the runner of U08 exists
# and by the ones that need a warehouse with an exact content without loading it through the runner.
MINIMAL_DDL = (
    "CREATE TABLE deliveries (delivery_id TEXT PRIMARY KEY, outcomes_from TEXT, outcomes_to TEXT, status TEXT)",
    "CREATE TABLE raw_orders (order_id INTEGER PRIMARY KEY)",
    "CREATE TABLE raw_order_lines (line_id INTEGER PRIMARY KEY, order_id INTEGER)",
    "CREATE TABLE raw_outcomes (order_id INTEGER PRIMARY KEY)",
)


def minimal_warehouse(path: Path | str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    for statement in MINIMAL_DDL:
        conn.execute(statement)
    return conn


def mark_loaded(conn: sqlite3.Connection, spec, frames, status: str = "loaded") -> None:
    """Record `spec` and the ids of its `frames` (a ``DeliveryFrames``) as loaded in a minimal warehouse.

    One savepoint: outside a transaction it is one transaction (on a file, one commit per row would take seconds); inside the transaction of the caller it nests
    in it, leaves it open, and a failure undoes only these rows.
    """
    conn.execute("SAVEPOINT mark_loaded")
    try:
        conn.execute("INSERT INTO deliveries VALUES (?, ?, ?, ?)", (spec.delivery_id, spec.outcomes_from.isoformat(), spec.outcomes_to.isoformat(), status))
        conn.executemany("INSERT INTO raw_orders VALUES (?)", [(int(i),) for i in frames.orders["order_id"]])
        conn.executemany("INSERT INTO raw_order_lines VALUES (?, ?)", [(int(a), int(b)) for a, b in zip(frames.order_lines["line_id"], frames.order_lines["order_id"], strict=True)])
        conn.executemany("INSERT INTO raw_outcomes VALUES (?)", [(int(i),) for i in frames.outcomes["order_id"]])
    except BaseException:
        conn.execute("ROLLBACK TO mark_loaded")
        conn.execute("RELEASE mark_loaded")
        raise
    conn.execute("RELEASE mark_loaded")


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_through(conn, delivered, cfg, upto: int):
    """Load the deliveries 0..upto-1 of the published fixture (``delivered``) through the runner of the warehouse; return the last ``LoadResult``."""
    from latedel.deliveries import read_delivery
    from latedel.warehouse import load_delivery

    folder, plan, entries = delivered
    result = None
    for index in range(upto):
        result = load_delivery(conn, read_delivery(folder, entries[index]), plan[index], f"run-{plan[index].delivery_id}", cfg, sha256_json=entries[index].sha256_json)
    return result


def rebuild_layers(conn, cfg, as_of) -> None:
    """The staging tables and the marts for the date, without the controls, so that a test can look at what they would say."""
    from latedel import warehouse

    conn.execute("BEGIN IMMEDIATE")
    warehouse._build_layers(conn, warehouse.sql_params(cfg, as_of))
    conn.execute("COMMIT")


# --- a project folder for the tests of the pipeline ---------------------------------------------------------------------------------------------


def snapshot(folder: Path) -> dict[str, str]:
    """The SHA-256 of every file under `folder`, by relative path (empty for a folder that does not exist): what a refused or rejected run must leave as it was."""
    folder = Path(folder)
    if not folder.exists():
        return {}
    return {p.relative_to(folder).as_posix(): file_digest(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def write_config(root: Path, csv_path: Path, *, sets: tuple[str, ...] = ("f2",), extra: str = "") -> Path:
    """``root/config/test.toml``: the real configuration with a local source, one point of the model grid with 30 boosting iterations (the tests of the pipeline are about
    what the tasks do with the model, not about what it learns), a small bootstrap and every path under `root`.

    The project folder is the parent of the folder that holds the main configuration file (decision D24), so the relative paths resolve to `root` whatever the working
    directory. `extra` is appended (TOML text) for the tests that change another key.
    """
    from latedel.config import DEFAULT_CONFIG

    path = Path(root) / "config" / "test.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'extends = "{DEFAULT_CONFIG.as_posix()}"\n\n[source]\nmode = "local"\nurl = ""\nlocal_path = "{Path(csv_path).as_posix()}"\naccepted_sha256 = ""\n\n'
        f'[features]\ncandidate_sets = {list(sets)!r}\n\n'.replace("'", '"')
        + '[model]\nlearning_rate = [0.1]\nmax_leaf_nodes = [15]\nmin_samples_leaf = [20]\nmax_iter = 30\n\n[evaluation]\nbootstrap_resamples = 50\nimportance_repeats = 2\n\n'
        '[paths]\nraw_dir = "raw"\ninterim_dir = "interim"\nwarehouse = "wh.sqlite"\nmodels_dir = "models"\noutputs_dir = "outputs/powerbi"\nsite_dir = "outputs/site"\n'
        'reports_dir = "reports"\nseal = "reports/model_seal.json"\ndashboard_html = "dashboard/powerbi-design.html"\nreadme = "README.md"\nfigures_dir = "figures"\n' + extra,
        encoding="utf-8",
    )
    return path


def make_cfg(root: Path, csv_path: Path, **kwargs):
    """The configuration of ``write_config`` loaded."""
    from latedel.config import load_config

    return load_config(write_config(root, csv_path, **kwargs))
