"""The demonstration of the leakage (U15, decision D31): what each column that gives the result away does to the AUC on V2, and that it cannot write.

Run on the synthetic launch warehouse; the same table on the real file is in ``tests/integration/test_evaluation_real.py`` (training and V2 only).
"""

from __future__ import annotations

import ast
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from latedel import leakage_demo
from latedel.leakage_demo import COLUMNS, leakage_table
from latedel.schema import LEAKING_COLUMNS
from tests.fixtures.helpers import file_digest


@pytest.fixture(scope="module")
def fast_cfg(cfg):
    """The default configuration with 30 trees: what these tests look at is the ranking of the columns, which a few trees already show."""
    return replace(cfg, model=replace(cfg.model, max_iter=30))


def model_tables(conn: sqlite3.Connection):
    return [conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in ("model_registry", "model_scores", "baseline_rates")]


def test_every_column_that_gives_the_result_away_is_measured_next_to_the_reference_of_the_mode(launch_conn, fast_cfg):
    cfg = fast_cfg
    table = leakage_table(launch_conn, cfg)
    assert list(table.columns) == ["source_column", "column_es", "kind", "known_at_es", "auc_with_mode_valid", "note_es", "sort_order"]
    assert table["source_column"].iloc[0] == "shipping_mode" and table["kind"].iloc[0] == "reference" and list(table["sort_order"]) == list(range(len(table)))
    assert set(table["source_column"]) == {"shipping_mode", *LEAKING_COLUMNS} - {"Late_delivery_risk"}, "all the columns of the leak list but the target"
    assert len(table) == 1 + len(LEAKING_COLUMNS) - 1 and table["source_column"].is_unique
    auc = dict(zip(table["source_column"], table["auc_with_mode_valid"], strict=True))
    reference = auc.pop("shipping_mode")
    assert 0.5 < reference < 0.9, "the mode alone is a real but modest rule"
    # the real days and the status of the delivery are the answer written another way; the shipping date is the order date plus the days
    for column in ("Days for shipping (real)", "Delivery Status", "shipping date (DateOrders)"):
        assert auc[column] > 0.95 > reference, column
    ranked = list(table["auc_with_mode_valid"].iloc[1:])
    assert ranked == sorted(ranked, reverse=True), "after the reference, the highest AUC first"
    assert auc["Benefit per order"] == auc["Order Profit Per Order"], "the two profit columns are one stored column (V12)"
    assert all(isinstance(t, str) and t for t in table["note_es"]) and set(table["kind"]) == {"reference", "outcome", "profit"}
    again = leakage_table(launch_conn, cfg)
    assert again.equals(table), "deterministic"


def test_the_demonstration_cannot_write_and_leaves_the_connection_as_it_found_it(launch_conn, fast_cfg, monkeypatch):
    cfg = fast_cfg
    path = Path(launch_conn.execute("PRAGMA database_list").fetchone()[2])
    before_tables, before_file = model_tables(launch_conn), file_digest(path)
    leakage_table(launch_conn, cfg)
    assert launch_conn.execute("PRAGMA query_only").fetchone()[0] == 0, "the read-only mode is turned off again"
    assert model_tables(launch_conn) == before_tables and file_digest(path) == before_file

    # during the call the connection refuses a write
    original = leakage_demo._auc
    seen = {}

    def spy(frame, cfg, extra):
        seen["query_only"] = launch_conn.execute("PRAGMA query_only").fetchone()[0]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            launch_conn.execute("INSERT INTO baseline_rates VALUES ('x', 'y', 0.5, 1)")
        return original(frame, cfg, extra)

    monkeypatch.setattr(leakage_demo, "_auc", spy)
    leakage_table(launch_conn, cfg)
    assert seen["query_only"] == 1 and model_tables(launch_conn) == before_tables

    # and a failure inside also restores the mode
    def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(leakage_demo, "_auc", boom)
    with pytest.raises(RuntimeError, match="boom"):
        leakage_table(launch_conn, cfg)
    assert launch_conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_the_module_imports_neither_the_registry_nor_the_scoring_and_contains_no_write_statement():
    source = Path(leakage_demo.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in imported if m.startswith(("latedel.registry", "latedel.scoring", "latedel.launch"))}
    for word in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "executemany"):
        assert word not in source, word


def test_the_list_of_columns_is_exactly_the_leak_list_without_the_target():
    assert {c.source for c in COLUMNS} == set(LEAKING_COLUMNS) - {"Late_delivery_risk"} and len(COLUMNS) == len(LEAKING_COLUMNS) - 1
    assert {c.stored_as for c in COLUMNS} <= {"delivery_status", "real_days", "shipping_hours", "order_status", "benefit_total", "profit_ratio_mean"}
