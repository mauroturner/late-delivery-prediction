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
    """Record `spec` and the ids of its `frames` (a ``DeliveryFrames``) as loaded in a minimal warehouse."""
    conn.execute("BEGIN")    # one transaction: on a file, one commit per row would take seconds
    conn.execute("INSERT INTO deliveries VALUES (?, ?, ?, ?)", (spec.delivery_id, spec.outcomes_from.isoformat(), spec.outcomes_to.isoformat(), status))
    conn.executemany("INSERT INTO raw_orders VALUES (?)", [(int(i),) for i in frames.orders["order_id"]])
    conn.executemany("INSERT INTO raw_order_lines VALUES (?, ?)", [(int(a), int(b)) for a, b in zip(frames.order_lines["line_id"], frames.order_lines["order_id"], strict=True)])
    conn.executemany("INSERT INTO raw_outcomes VALUES (?)", [(int(i),) for i in frames.outcomes["order_id"]])
    conn.execute("COMMIT")


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
