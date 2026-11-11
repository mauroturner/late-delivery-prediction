"""The warehouse (U08): the runner, the schema, the load of a delivery and the staging tables (RF09, AC11 and AC12, decision D20).

A load is one transaction: the rows of the delivery, its record, the staging tables and the controls. A delivery that is empty, already loaded, repeats a key
or fails a control leaves the SQLite file as it was, byte for byte. The truth of the contents is computed from the frames of the fixture in the tests.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from latedel import warehouse
from latedel.deliveries import DeliveryFrames, delivery_plan, publish, read_delivery
from latedel.schema import PERSONAL_COLUMNS
from latedel.validate import make_result, validate_delivery, validate_source, with_results
from latedel.warehouse import (
    CheckFailedError,
    DeliveryAlreadyLoadedError,
    EmptyDeliveryError,
    LoadResult,
    SchemaDriftError,
    apply_schema,
    connect,
    load_delivery,
    record_run,
    record_validation_results,
    run_checks,
    run_sql_file,
    split_statements,
)
from tests.fixtures.helpers import file_digest, load_through

TABLES = ("raw_orders", "raw_order_lines", "raw_outcomes", "deliveries", "pipeline_runs", "validation_results", "stg_orders", "stg_order_lines", "stg_outcomes")


def dump(conn, table: str) -> list[tuple]:
    return conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def with_extra_checks(tmp_path: Path, monkeypatch, **files: str) -> Path:
    """A copy of the project's sql folder with extra control files, which the runner then uses."""
    target = tmp_path / "sql"
    shutil.copytree(warehouse.SQL_DIR, target)
    (target / "checks" / "load").mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (target / "checks" / "load" / f"{name}.sql").write_text(text, encoding="utf-8")
    monkeypatch.setattr(warehouse, "SQL_DIR", target)
    return target


FAILS = "-- Violations: always one.\nSELECT order_id FROM raw_orders ORDER BY order_id LIMIT 1;\n"


# --- a failing control undoes the load (AC12) ----------------------------------------------------------------------------------


def test_failed_check_rolls_back_a_first_load(cfg, delivered, tmp_path, monkeypatch):
    folder, plan, entries = delivered
    path = tmp_path / "w.sqlite"
    conn = connect(path)
    before = file_digest(path)
    with_extra_checks(tmp_path, monkeypatch, chk_always_fails=FAILS)
    with pytest.raises(CheckFailedError) as error:
        load_delivery(conn, read_delivery(folder, entries[0]), plan[0], "run-1", cfg, sha256_json=entries[0].sha256_json)
    failing = [c for c in error.value.checks if c.violations]
    assert [(c.check_id, c.violations) for c in failing] == [("chk_always_fails", 1)] and failing[0].sample == [(failing[0].sample[0][0],)]
    assert "chk_always_fails" in str(error.value)
    assert not conn.in_transaction
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0, "not even the tables of the schema remain"
    conn.close()
    assert file_digest(path) == before


def test_a_failed_check_leaves_a_warehouse_with_data_byte_for_byte_as_it_was(cfg, delivered, tmp_path, monkeypatch):
    folder, plan, entries = delivered
    path = tmp_path / "w.sqlite"
    conn = connect(path)
    load_through(conn, delivered, cfg, 2)
    conn.close()
    before = file_digest(path)
    with_extra_checks(tmp_path, monkeypatch, chk_always_fails=FAILS)
    conn = connect(path)
    with pytest.raises(CheckFailedError):
        load_delivery(conn, read_delivery(folder, entries[2]), plan[2], "run-3", cfg, sha256_json=entries[2].sha256_json)
    assert conn.execute("SELECT delivery_id FROM deliveries ORDER BY 1").fetchall() == [("L2017-01-07",), ("M2017-01",)]
    conn.close()
    assert file_digest(path) == before


def test_a_check_with_no_violations_lets_the_load_through_and_gets_the_named_parameters(cfg, delivered, tmp_path, monkeypatch):
    with_extra_checks(tmp_path, monkeypatch, chk_param="SELECT 1 WHERE :purge_days <> 7 OR :as_of <> '2017-01-07';\n", chk_clean="SELECT order_id FROM raw_orders WHERE 0;\n")
    conn = connect(tmp_path / "w.sqlite")
    result = load_through(conn, delivered, cfg, 1)
    assert {c.check_id: c.violations for c in result.checks if c.check_id in ("chk_clean", "chk_param")} == {"chk_clean": 0, "chk_param": 0}


# --- the load itself: rows, record, staging (AC12) -------------------------------------------------------------------------------


def test_every_delivery_loads_with_its_rows_and_is_recorded(cfg, delivered, tmp_path):
    folder, plan, entries = delivered
    conn = connect(tmp_path / "w.sqlite")
    totals = {"orders": 0, "lines": 0, "outcomes": 0}
    for index, (spec, entry) in enumerate(zip(plan, entries, strict=True)):
        frames = read_delivery(folder, entry)
        result = load_delivery(conn, frames, spec, f"run-{index}", cfg, sha256_json=entry.sha256_json)
        assert isinstance(result, LoadResult) and result.delivery_id == spec.delivery_id and result.as_of == spec.as_of
        assert (result.orders, result.lines, result.outcomes) == (len(frames.orders), len(frames.order_lines), len(frames.outcomes))
        assert all(c.violations == 0 for c in result.checks), "every control is at zero"
        for key, table, frame in (("orders", "raw_orders", frames.orders), ("lines", "raw_order_lines", frames.order_lines), ("outcomes", "raw_outcomes", frames.outcomes)):
            totals[key] += len(frame)
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE delivery_id = ?", (spec.delivery_id,)).fetchone()[0] == len(frame)
        row = conn.execute("SELECT kind, orders_from, orders_to, outcomes_from, outcomes_to, as_of_date, orders, lines, outcomes, sha256_json, status, run_key "
                           "FROM deliveries WHERE delivery_id = ?", (spec.delivery_id,)).fetchone()
        assert row == (spec.kind, spec.orders_from and spec.orders_from.isoformat(), spec.orders_to and spec.orders_to.isoformat(), spec.outcomes_from.isoformat(),
                       spec.outcomes_to.isoformat(), spec.as_of.isoformat(), len(frames.orders), len(frames.order_lines), len(frames.outcomes),
                       entry.sha256_json, "loaded", f"run-{index}")
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("raw_orders", "raw_order_lines", "raw_outcomes")] == [totals["orders"], totals["lines"], totals["outcomes"]]
    assert totals["orders"] == totals["outcomes"] == 3000 and conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 15


def test_the_raw_tables_hold_the_values_of_the_frames(cfg, fixture_read, loaded_copy):
    conn = loaded_copy(15, "w.sqlite")
    frames = fixture_read[0]
    orders = pd.read_sql_query("SELECT * FROM raw_orders ORDER BY order_id", conn).drop(columns="delivery_id")
    expected = frames.orders.copy()
    expected["order_date"] = expected["order_date"].dt.strftime("%Y-%m-%d")
    pd.testing.assert_frame_equal(orders, expected.sort_values("order_id").reset_index(drop=True), check_dtype=False)
    lines = pd.read_sql_query("SELECT * FROM raw_order_lines ORDER BY line_id", conn).drop(columns="delivery_id")
    pd.testing.assert_frame_equal(lines, frames.order_lines.sort_values("line_id").reset_index(drop=True), check_dtype=False)
    outcomes = pd.read_sql_query("SELECT * FROM raw_outcomes ORDER BY order_id", conn).drop(columns="delivery_id")
    expected = frames.outcomes.copy()
    expected["outcome_known_date"] = expected["outcome_known_date"].dt.strftime("%Y-%m-%d")
    expected["is_cancelled"] = expected["is_cancelled"].astype(int)
    pd.testing.assert_frame_equal(outcomes, expected.sort_values("order_id").reset_index(drop=True), check_dtype=False)


def test_loading_from_scratch_twice_gives_the_same_content(cfg, delivered, tmp_path, loaded_masters, loaded_copy):
    """Two loads of the 15 deliveries from an empty warehouse give the same rows: this one, and the session's master, which was loaded from an empty warehouse too, in
    steps (a copy of a smaller master and the deliveries after it), before this test. Each master is the warehouse a load from scratch gives (A1: one load, not two)."""
    first = connect(tmp_path / "a.sqlite")
    load_through(first, delivered, cfg, 15)
    assert len(dump(first, "stg_orders")) == 3000
    loaded_masters(5)
    master = loaded_copy(15, "master.sqlite")
    for table in TABLES:
        assert dump(master, table) == dump(first, table), table


def test_a_delivery_that_is_empty_or_already_loaded_changes_nothing(cfg, delivered, tmp_path):
    folder, plan, entries = delivered
    path = tmp_path / "w.sqlite"
    conn = connect(path)
    load_through(conn, delivered, cfg, 2)
    conn.close()
    before = file_digest(path)
    conn = connect(path)
    full = read_delivery(folder, entries[2])
    empty = DeliveryFrames(full.orders.iloc[0:0], full.order_lines.iloc[0:0], full.outcomes.iloc[0:0])
    with pytest.raises(EmptyDeliveryError, match="M2017-02"):
        load_delivery(conn, empty, plan[2], "run-x", cfg, sha256_json="ab" * 32)
    with pytest.raises(DeliveryAlreadyLoadedError, match="M2017-01"):
        load_delivery(conn, read_delivery(folder, entries[1]), plan[1], "run-y", cfg, sha256_json=entries[1].sha256_json)
    conn.close()
    assert file_digest(path) == before


def test_a_repeated_key_rolls_the_whole_load_back(cfg, delivered, tmp_path):
    folder, plan, entries = delivered
    path = tmp_path / "w.sqlite"
    conn = connect(path)
    load_through(conn, delivered, cfg, 1)
    conn.close()
    before = file_digest(path)
    conn = connect(path)
    frames = read_delivery(folder, entries[1])
    bad = dataclasses.replace(frames, outcomes=pd.concat([frames.outcomes, frames.outcomes.head(1)], ignore_index=True))
    with pytest.raises(sqlite3.IntegrityError):
        load_delivery(conn, bad, plan[1], "run-2", cfg, sha256_json=entries[1].sha256_json)
    assert not conn.in_transaction
    conn.close()
    assert file_digest(path) == before


def test_a_missing_value_is_refused_not_stored_as_null(cfg, delivered, tmp_path):
    folder, plan, entries = delivered
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 1)
    frames = read_delivery(folder, entries[1])
    lines = frames.order_lines.copy()
    lines.loc[lines.index[0], "sales"] = float("nan")
    with pytest.raises(sqlite3.IntegrityError, match="sales"):
        load_delivery(conn, dataclasses.replace(frames, order_lines=lines), plan[1], "run-2", cfg, sha256_json=entries[1].sha256_json)
    assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1


# --- the staging tables: up to the calculation date, outcomes only when known ------------------------------------------------------------------------------


@pytest.mark.parametrize("upto", [1, 2, 6, 14, 15])
def test_staging_holds_only_what_is_known_on_the_calculation_date(cfg, delivered, fixture_read, upto, loaded_masters, loaded_copy):
    conn = loaded_copy(upto, "w.sqlite")         # the first `upto` deliveries loaded through the runner, and below the result of the last load
    result = loaded_masters(upto)[1]
    as_of = delivered[1][upto - 1].as_of
    frames = fixture_read[0]
    orders = frames.orders[frames.orders["order_date"] <= pd.Timestamp(as_of)]
    known = frames.outcomes[(frames.outcomes["outcome_known_date"] <= pd.Timestamp(as_of)) & frames.outcomes["order_id"].isin(orders["order_id"])]
    lines = frames.order_lines[frames.order_lines["order_id"].isin(orders["order_id"])]
    assert result.staged == {"orders": len(orders), "order_lines": len(lines), "outcomes": len(known)}
    assert conn.execute("SELECT MAX(order_date) FROM stg_orders").fetchone()[0] <= as_of.isoformat()
    assert conn.execute("SELECT MAX(outcome_known_date) FROM stg_outcomes").fetchone()[0] <= as_of.isoformat()
    stg = pd.read_sql_query("SELECT order_id, is_late FROM stg_outcomes ORDER BY order_id", conn).merge(
        frames.orders[["order_id", "scheduled_days"]], on="order_id").merge(frames.outcomes[["order_id", "real_days", "is_cancelled"]], on="order_id")
    expected = (stg["real_days"] > stg["scheduled_days"]).astype(float).where(~stg["is_cancelled"])
    assert stg["is_late"].astype(float).fillna(-1).tolist() == expected.fillna(-1).tolist(), "is_late is real days above scheduled days, empty when cancelled"
    assert stg["is_late"].isna().sum() == stg["is_cancelled"].sum() and stg["is_late"].isna().any() == bool(stg["is_cancelled"].any())


def test_staging_is_rebuilt_by_each_load_and_never_keeps_an_older_date(cfg, delivered, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    folder, plan, entries = delivered
    sizes = []
    for index in range(3):
        load_delivery(conn, read_delivery(folder, entries[index]), plan[index], f"run-{index}", cfg, sha256_json=entries[index].sha256_json)
        sizes.append(conn.execute("SELECT COUNT(*) FROM stg_orders").fetchone()[0])
    assert sizes == sorted(sizes) and len(set(sizes)) == 3
    assert conn.execute("SELECT COUNT(*) FROM stg_orders").fetchone()[0] == conn.execute("SELECT COUNT(*) FROM raw_orders").fetchone()[0]


@pytest.mark.parametrize("as_of", [date(2017, 1, 7), date(2017, 2, 15), date(2017, 3, 31)])
def test_the_staging_files_filter_by_the_date_they_are_given_whatever_the_warehouse_holds(cfg, fixture_read, as_of, loaded_copy):
    conn = loaded_copy(15, "w.sqlite")          # everything is loaded; the staging is rebuilt for an earlier date
    conn.execute("BEGIN IMMEDIATE")
    for name in warehouse.STAGING:
        run_sql_file(conn, warehouse.SQL_DIR / name, warehouse.sql_params(cfg, as_of))
    conn.execute("COMMIT")
    frames = fixture_read[0]
    orders = frames.orders[frames.orders["order_date"] <= pd.Timestamp(as_of)]
    known = frames.outcomes[(frames.outcomes["outcome_known_date"] <= pd.Timestamp(as_of)) & frames.outcomes["order_id"].isin(orders["order_id"])]
    assert [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("stg_orders", "stg_order_lines", "stg_outcomes")] == [
        len(orders), int(frames.order_lines["order_id"].isin(orders["order_id"]).sum()), len(known)]
    assert conn.execute("SELECT MAX(outcome_known_date) FROM stg_outcomes").fetchone()[0] <= as_of.isoformat()
    assert conn.execute("SELECT COUNT(*) FROM raw_outcomes").fetchone()[0] == 3000, "the raw tables are untouched"
    assert set(frames.outcomes[frames.outcomes["outcome_known_date"] > pd.Timestamp(as_of)]["order_id"]).isdisjoint(
        r[0] for r in conn.execute("SELECT order_id FROM stg_outcomes"))


def test_the_staging_tables_of_the_orders_have_no_outcome_column(cfg, delivered, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 1)
    outcome_names = {"is_late", "real_days", "shipping_ts", "outcome_known_date", "delivery_status", "order_status", "is_cancelled", "benefit_total",
                     "profit_ratio_mean", "source_label"}
    for table in ("raw_orders", "raw_order_lines", "stg_orders", "stg_order_lines"):
        names = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert not names & outcome_names, table


# --- no personal data in the file (AC04) ------------------------------------------------------------------------------------------------------------------


def test_no_personal_column_or_sentinel_in_the_warehouse_file(fixture_truth, loaded_db):
    path = loaded_db(15)                                  # the file the runner wrote for the 15 deliveries; this test only reads it
    blob = path.read_bytes()
    assert all(needle not in blob for needle in fixture_truth.sentinel_needles)
    tables = {row[0] for row in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(TABLES) <= tables
    columns = {row[1].lower() for t in tables for row in sqlite3.connect(path).execute(f'PRAGMA table_info("{t}")')}
    personal = {c.lower().replace(" ", "_") for c in PERSONAL_COLUMNS}
    assert not columns & personal


# --- the runner ----------------------------------------------------------------------------------------------------------------------------------------


def test_the_connection_is_autocommit_with_the_pragmas_of_the_project(tmp_path):
    conn = connect(tmp_path / "deep" / "er" / "w.sqlite")
    assert conn.isolation_level is None
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == warehouse.BUSY_TIMEOUT_MS


def test_split_statements_keeps_semicolons_inside_strings_comments_and_triggers():
    text = (
        "-- a comment; with a semicolon\nCREATE TABLE t (a TEXT);\n"
        "INSERT INTO t VALUES ('x;y');\n/* block; comment */\n"
        "CREATE TRIGGER trg BEFORE UPDATE ON t BEGIN SELECT RAISE(ABORT, 'no; update'); END;\n"
        "SELECT 1"
    )
    statements = split_statements(text)
    assert len(statements) == 4 and statements[1] == "INSERT INTO t VALUES ('x;y');" and statements[3] == "SELECT 1"
    assert "CREATE TABLE t (a TEXT);" in statements[0] and "CREATE TRIGGER trg" in statements[2] and statements[2].endswith("END;")
    assert "no; update" in statements[2], "the trigger is one statement"
    assert split_statements("-- nothing\n;;\n/* but comments */") == []


def test_run_sql_file_never_commits_on_its_own(tmp_path):
    sql = tmp_path / "x.sql"
    sql.write_text("CREATE TABLE t (a INTEGER);\nINSERT INTO t VALUES (:v);\nINSERT INTO t VALUES (:v + 1);\n", encoding="utf-8")
    conn = connect(tmp_path / "w.sqlite")
    conn.execute("BEGIN IMMEDIATE")
    run_sql_file(conn, sql, {"v": 5, "unused": 1})
    assert conn.in_transaction and conn.execute("SELECT a FROM t ORDER BY a").fetchall() == [(5,), (6,)]
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0, "a rollback undoes the whole file: executescript() would have committed"


def test_run_checks_counts_violations_samples_them_and_binds_parameters(cfg, tmp_path, monkeypatch):
    conn = connect(tmp_path / "w.sqlite")
    apply_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    warehouse._build_layers(conn, warehouse.sql_params(cfg, date(2017, 1, 7)))
    conn.execute("COMMIT")
    conn.executemany("INSERT INTO raw_orders VALUES (?, 'd', 't', '2017-01-01', 'pre_break', 'First Class', 1, 'p', 'm', 'r', 'c', 's', 'cc')", [(i,) for i in range(1, 9)])
    with_extra_checks(
        tmp_path, monkeypatch, chk_many="SELECT order_id FROM raw_orders WHERE order_id > :limit;\n", chk_none="SELECT order_id FROM raw_orders WHERE order_id > 100;\n")
    results = run_checks(conn, "load", {"limit": 2, "purge_days": 7, "as_of": "2017-01-07"})
    by_name = {c.check_id: c for c in results}
    assert by_name["chk_many"].violations == 6 and len(by_name["chk_many"].sample) == warehouse.SAMPLE_LIMIT == 5 and by_name["chk_none"].violations == 0
    assert by_name["chk_none"].sample == [] and [c.check_id for c in results] == sorted(by_name)


def test_run_checks_refuses_an_unknown_group_and_a_file_with_two_statements(cfg, tmp_path, monkeypatch):
    conn = connect(tmp_path / "w.sqlite")
    apply_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    warehouse._build_layers(conn, warehouse.sql_params(cfg, date(2017, 1, 7)))
    conn.execute("COMMIT")
    with pytest.raises(ValueError, match="grupo de controles desconocido: 'sales'"):
        run_checks(conn, "sales")  # type: ignore[arg-type]
    with_extra_checks(tmp_path, monkeypatch, chk_two="SELECT 1 WHERE 0; SELECT 2 WHERE 0;\n")
    with pytest.raises(ValueError, match=r"chk_two\.sql debe tener exactamente un SELECT"):
        run_checks(conn, "load", warehouse.sql_params(cfg, date(2017, 1, 7)))


def test_the_schema_is_idempotent_keeps_the_data_and_refuses_a_table_that_drifted(tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    apply_schema(conn)
    conn.execute("INSERT INTO deliveries VALUES ('D1', 'monthly', NULL, NULL, '2017-01-01', '2017-01-31', '2017-01-31', 0, 0, 0, 'x', 'loaded', 'r')")
    apply_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
    conn.execute("DROP TABLE validation_results")
    conn.execute("CREATE TABLE validation_results (run_key TEXT, rule_id TEXT, name_es TEXT, severity TEXT, passed INTEGER, extra TEXT)")
    with pytest.raises(SchemaDriftError, match="validation_results") as error:
        apply_schema(conn)
    assert set(error.value.missing) >= {"scope", "rows_affected", "orders_affected", "examples_json"} and error.value.unexpected == ("extra",)
    assert "R10" in str(error.value)


def test_the_schema_checks_are_enforced_by_the_database(tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    apply_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO deliveries VALUES ('D', 'weekly', NULL, NULL, 'a', 'b', 'c', 0, 0, 0, 'x', 'loaded', NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO deliveries VALUES ('D', 'monthly', NULL, NULL, 'a', 'b', 'c', 0, 0, 0, 'x', 'done', NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO pipeline_runs (run_key, status) VALUES ('r', 'unknown')")


# --- the records of the runs (RNF07) -----------------------------------------------------------------------------------------------------------------------


def test_record_run_inserts_then_updates_only_the_columns_given(tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    apply_schema(conn)
    record_run(conn, {"run_key": "r1", "status": "running", "delivery_id": "M2017-01", "started_at": "2017-02-01T00:00:00", "orders_delivery": 80})
    record_run(conn, {"run_key": "r1", "status": "ok", "finished_at": "2017-02-01T00:01:00", "alerts": 12, "peak_commit_mb": None})
    row = conn.execute("SELECT status, delivery_id, started_at, finished_at, orders_delivery, alerts, peak_commit_mb FROM pipeline_runs").fetchone()
    assert row == ("ok", "M2017-01", "2017-02-01T00:00:00", "2017-02-01T00:01:00", 80, 12, None)
    with pytest.raises(ValueError, match="run_key"):
        record_run(conn, {"status": "ok"})
    with pytest.raises(ValueError, match="bogus"):
        record_run(conn, {"run_key": "r1", "bogus": 1})
    with pytest.raises(sqlite3.IntegrityError):
        record_run(conn, {"run_key": "r2", "status": "weird"})


def test_validation_results_are_stored_per_run_and_replaced_on_a_repeat(cfg, delivered, fixture_read, tmp_path):
    folder, plan, entries = delivered
    conn = connect(tmp_path / "w.sqlite")
    apply_schema(conn)
    source = validate_source(fixture_read[1], run_key="r1")
    report = with_results(validate_delivery(read_delivery(folder, entries[0]), plan[0], conn, cfg, entry=entries[0], folder=folder / "L2017-01-07", run_key="r1"), [make_result("M05", False)])
    record_validation_results(conn, source)
    record_validation_results(conn, report)
    rows = {r[0]: r for r in conn.execute("SELECT rule_id, severity, scope, passed, rows_affected, orders_affected, examples_json FROM validation_results WHERE run_key = 'r1'")}
    assert set(rows) == {r.rule_id for r in source.results} | {r.rule_id for r in report.results}
    assert rows["V11"][1:4] == ("advertencia", "source", 0) and rows["V11"][4] == fixture_read[1].affected["V11"]["rows"]
    assert rows["M05"][1:4] == ("advertencia", "model", 0) and rows["V20"][1:4] == ("bloqueante", "delivery", 1)
    assert json.loads(rows["V15"][6]) == [{"columns": 15}]
    record_validation_results(conn, source)
    assert conn.execute("SELECT COUNT(*) FROM validation_results WHERE run_key = 'r1'").fetchone()[0] == len(rows), "the same rules again replace the same rows"


# --- the validation reads the real schema the same way -------------------------------------------------------------------------------------------------------


def test_the_rules_of_the_delivery_see_a_loaded_delivery_in_the_real_schema(cfg, delivered, tmp_path):
    folder, plan, entries = delivered
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 2)
    frames = read_delivery(folder, entries[1])
    report = validate_delivery(frames, plan[1], conn, cfg, entry=entries[1], folder=folder / "M2017-01", run_key="again")
    found = {r.rule_id: r for r in report.results}
    ids = set(frames.orders["order_id"]) | set(frames.order_lines["order_id"]) | set(frames.outcomes["order_id"])
    assert not found["V22"].passed and found["V20"].examples[0]["reason"] == "already_loaded" and found["V22"].orders_affected == len(ids)
    nxt = validate_delivery(read_delivery(folder, entries[2]), plan[2], conn, cfg, entry=entries[2], folder=folder / "M2017-02", run_key="next")
    assert not nxt.blocking_failed and {r.rule_id: r for r in nxt.results}["I01"].orders_affected == entries[2].orders


# --- the real file (real_data) -------------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_file_the_fifteen_deliveries_load_and_the_controls_are_at_zero(cfg, real_read, tmp_path):
    frames, _report = real_read
    plan = delivery_plan(cfg.deliveries)
    conn = connect(tmp_path / "real.sqlite")
    started = time.perf_counter()
    last = None
    for spec in plan:
        entry = publish(spec, frames, tmp_path / "deliveries", source_sha256="fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6")
        last = load_delivery(conn, read_delivery(tmp_path / "deliveries", entry), spec, f"real-{spec.delivery_id}", cfg, sha256_json=entry.sha256_json)
        assert all(c.violations == 0 for c in last.checks)
    elapsed = time.perf_counter() - started
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("raw_orders", "raw_order_lines", "raw_outcomes", "stg_orders", "stg_order_lines", "stg_outcomes")}
    assert counts == {"raw_orders": 65_752, "raw_order_lines": 180_519, "raw_outcomes": 65_752, "stg_orders": 65_752, "stg_order_lines": 180_519, "stg_outcomes": 65_752}
    assert conn.execute("SELECT COUNT(*) FROM stg_outcomes WHERE is_late IS NULL").fetchone()[0] == 2_855
    late = conn.execute("SELECT COUNT(*) FROM stg_outcomes WHERE is_late = 1").fetchone()[0]
    assert late > 0 and last is not None and last.as_of == date(2018, 2, 7)
    print("REAL", counts, "late", late, "seconds", round(elapsed, 1))
