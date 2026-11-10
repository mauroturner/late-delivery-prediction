"""H2, R3-helpers-explicit-transaction-nesting: ``mark_loaded`` works the same inside a transaction of its caller and outside any."""

from __future__ import annotations

import sqlite3

import pytest

from latedel.deliveries import read_delivery
from tests.fixtures.helpers import mark_loaded, minimal_warehouse


def counts(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("deliveries", "raw_orders", "raw_order_lines", "raw_outcomes")}


def loaded(delivered, index: int):
    folder, plan, entries = delivered
    return plan[index], read_delivery(folder, entries[index])


def test_outside_a_transaction_the_rows_are_committed_in_one_step(delivered):
    spec, frames = loaded(delivered, 1)
    conn = minimal_warehouse()
    mark_loaded(conn, spec, frames)
    assert not conn.in_transaction
    assert counts(conn) == {"deliveries": 1, "raw_orders": len(frames.orders), "raw_order_lines": len(frames.order_lines), "raw_outcomes": len(frames.outcomes)}


def test_inside_a_transaction_of_the_caller_it_neither_fails_nor_closes_it(delivered):
    spec, frames = loaded(delivered, 1)
    conn = minimal_warehouse()
    conn.execute("BEGIN")
    mark_loaded(conn, spec, frames)
    assert conn.in_transaction, "the transaction of the caller is still open"
    assert counts(conn)["raw_orders"] == len(frames.orders)
    conn.execute("ROLLBACK")
    assert counts(conn) == {"deliveries": 0, "raw_orders": 0, "raw_order_lines": 0, "raw_outcomes": 0}, "and the caller can undo it"


def test_several_calls_in_one_transaction_of_the_caller_commit_together(delivered):
    conn = minimal_warehouse()
    conn.execute("BEGIN")
    for index in (0, 1):
        mark_loaded(conn, *loaded(delivered, index))
    conn.execute("COMMIT")
    assert counts(conn)["deliveries"] == 2


@pytest.mark.parametrize("nested", [False, True])
def test_a_failure_undoes_only_its_own_rows(delivered, nested):
    first, second = loaded(delivered, 1), loaded(delivered, 1)
    conn = minimal_warehouse()
    mark_loaded(conn, *first)
    before = counts(conn)
    if nested:
        conn.execute("BEGIN")
    with pytest.raises(sqlite3.IntegrityError):
        mark_loaded(conn, *second)                    # the same delivery again: the first insert fails
    assert counts(conn) == before
    assert conn.in_transaction is nested
    if nested:
        conn.execute("ROLLBACK")
