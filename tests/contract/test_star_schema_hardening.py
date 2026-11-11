"""H4: the advisories of the native review of U17 (the star schema): one place for the edges of the split, the uniqueness control, the sentinel date of the follow-up
control, the three states of ``seal_state``, the attributes of a product from one row, and the geography that is never NULL.

Every claim was checked against the code first; the ones that were defects have a test that failed before the change, and the ones that were only missing tests pass.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from latedel.export import build_powerbi_marts
from latedel.registry import write_launch_seal
from latedel.warehouse import SQL_DIR, CheckFailedError, connect, run_checks
from tests.unit.test_seal import launch_for

POWERBI = SQL_DIR / "marts" / "powerbi"
CHECKS = SQL_DIR / "checks" / "model"
EDGES = (":train_start", ":train_end", ":valid_start", ":valid_v2_start", ":valid_end", ":test_start", ":test_end", ":post_break_start", ":post_break_end")
BLOCK_ES = {
    "train": "Entrenamiento", "purge_1": "Purga 1", "valid_v1": "Validación V1", "valid_v2": "Validación V2", "purge_2": "Purga 2", "test": "Prueba",
    "post_break": "Posterior al cambio de forma", "outside": "Fuera de los bloques",
}


def sql_of(directory: Path, name: str) -> str:
    return (directory / name).read_text(encoding="utf-8")


@pytest.fixture()
def private(star_warehouse, tmp_path):
    """A private copy of the warehouse of the fixture after everything the flow does around the model, not yet exported."""
    path = tmp_path / "wh.sqlite"
    shutil.copy(star_warehouse.db, path)
    conn = connect(path)
    yield conn, star_warehouse.cfg, tmp_path
    conn.close()


def expected_block(day: str, cfg) -> str:
    """The block of a date, from the table of the requirements (section 4), written here and not read from the SQL."""
    s, d = cfg.split, date.fromisoformat(day)
    if s.train_start <= d <= s.train_end:
        return "train"
    if s.train_end < d < s.valid_start:
        return "purge_1"
    if s.valid_start <= d < s.valid_v2_start:
        return "valid_v1"
    if s.valid_v2_start <= d <= s.valid_end:
        return "valid_v2"
    if s.valid_end < d < s.test_start:
        return "purge_2"
    if s.test_start <= d <= s.test_end:
        return "test"
    if s.post_break_start <= d <= s.post_break_end:
        return "post_break"
    return "outside"


# --- R2-duplicated-split-block-logic: the edges of the split live in one table ---------------------------------------------------------------


def test_the_edges_of_the_split_are_written_in_one_place_for_the_three_marts():
    """The seven-block test was a literal CASE in three files; now they join the one table that the export loads from ``[split]``."""
    for name in ("fact_order.sql", "dim_date.sql", "dim_month.sql"):
        text = sql_of(POWERBI, name)
        assert not [edge for edge in EDGES if edge in text], f"{name} still writes an edge of the split"
        assert "ref_block" in text, f"{name} does not join the table of blocks"


def test_every_order_every_day_and_every_month_is_in_the_block_of_the_split(private):
    conn, cfg, _tmp = private
    build_powerbi_marts(conn, cfg)
    for order_date, block in conn.execute("SELECT DISTINCT o.order_date, f.block FROM raw_orders AS o JOIN star_fact_order AS f ON f.order_id = o.order_id"):
        assert block == expected_block(order_date, cfg), order_date
    days = conn.execute("SELECT date, block_es, is_test FROM star_dim_date").fetchall()
    assert len(days) > 1000 and {expected_block(d, cfg) for d, _, _ in days} >= {"train", "purge_1", "valid_v1", "valid_v2", "purge_2", "test", "post_break"}
    for day, block_es, is_test in days:
        assert block_es == BLOCK_ES[expected_block(day, cfg)] and is_test == (expected_block(day, cfg) == "test"), day
    for month_start, block_es in conn.execute("SELECT month_start, block_es FROM star_dim_month"):
        assert block_es == BLOCK_ES[expected_block(month_start, cfg)], month_start


def test_moving_an_edge_of_the_split_moves_the_fact_the_date_and_the_month_together(private):
    conn, cfg, _tmp = private
    moved = replace(cfg, split=replace(cfg.split, valid_v2_start=cfg.split.valid_v2_start - timedelta(days=45)))
    build_powerbi_marts(conn, moved)
    assert moved.split.valid_v2_start != cfg.split.valid_v2_start
    edge = moved.split.valid_v2_start.isoformat()
    assert conn.execute("SELECT block_es FROM star_dim_date WHERE date = ?", (edge,)).fetchone()[0] == "Validación V2"
    assert conn.execute("SELECT block_es FROM star_dim_date WHERE date = ?", ((moved.split.valid_v2_start - timedelta(days=1)).isoformat(),)).fetchone()[0] == "Validación V1"
    in_v2 = conn.execute("SELECT COUNT(*) FROM star_fact_order WHERE block = 'valid_v2'").fetchone()[0]
    assert in_v2 == conn.execute(
        "SELECT COUNT(*) FROM raw_orders WHERE order_date >= ? AND order_date <= ?", (edge, moved.split.valid_end.isoformat())).fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM star_fact_order WHERE block = 'valid_v1'").fetchone()[0] == conn.execute(
        "SELECT COUNT(*) FROM raw_orders WHERE order_date >= ? AND order_date < ?", (moved.split.valid_start.isoformat(), edge)).fetchone()[0]
    for order_date, block in conn.execute("SELECT DISTINCT o.order_date, f.block FROM raw_orders AS o JOIN star_fact_order AS f ON f.order_id = o.order_id"):
        assert block == expected_block(order_date, moved), order_date
    for day, block_es in conn.execute("SELECT date, block_es FROM star_dim_date"):
        assert block_es == BLOCK_ES[expected_block(day, moved)], day


# --- R2-weekday-formula-duplicated ----------------------------------------------------------------------------------------------------------


def test_the_weekday_of_an_order_is_the_weekday_of_its_date_and_each_formula_points_to_the_other(private):
    """Kept in two files on purpose (SQLite has no function to share, and one is the calendar and the other the fact); a test ties them and a comment names the twin."""
    conn, cfg, _tmp = private
    build_powerbi_marts(conn, cfg)
    assert conn.execute(
        "SELECT COUNT(*) FROM star_fact_order AS f JOIN star_dim_date AS d ON d.date_key = f.order_date_key WHERE f.order_weekday <> d.weekday").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(DISTINCT order_weekday) FROM star_fact_order").fetchone()[0] == 7
    assert "fact_order.sql" in sql_of(POWERBI, "dim_date.sql") and "dim_date.sql" in sql_of(POWERBI, "fact_order.sql")


# --- R2-grain-concat-delimiter ---------------------------------------------------------------------------------------------------------------


def insert_like(conn, table: str, **changes) -> None:
    cursor = conn.execute(f"SELECT * FROM {table} LIMIT 1")
    names = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    values = [changes.get(n, v) for n, v in zip(names, row, strict=True)]
    conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})", values)


def keys_unique(conn):
    return next(c for c in run_checks(conn, "model", {"test_start": "2017-01-08"}) if c.check_id == "chk_star_keys_unique")


def test_the_uniqueness_control_compares_the_columns_and_names_the_repeated_key_without_ambiguity(private):
    """The grain is compared by its columns (``GROUP BY``): two different grains that would read the same once joined with ``/`` are not a repetition. What was ambiguous
    was the key shown for a real repetition; it is now a JSON array of the columns, which cannot be read two ways."""
    conn, cfg, _tmp = private
    build_powerbi_marts(conn, cfg)
    assert keys_unique(conn).violations == 0
    insert_like(conn, "star_fact_method_metrics", scope="a/b", method="c", seal_state="sealed")
    insert_like(conn, "star_fact_method_metrics", scope="a", method="b/c", seal_state="sealed")
    assert keys_unique(conn).violations == 0, "('a/b', 'c') and ('a', 'b/c') are two grains, not one"
    insert_like(conn, "star_fact_method_metrics", scope="a/b", method="c", seal_state="sealed")
    found = keys_unique(conn)
    assert found.violations == 1 and found.sample[0][0] == "fact_method_metrics"
    assert json.loads(found.sample[0][1]) == ["a/b", "c", "sealed"], "the repeated grain, column by column"


def test_the_key_of_a_numeric_grain_is_shown_as_a_json_array_too(private):
    conn, cfg, _tmp = private
    build_powerbi_marts(conn, cfg)
    insert_like(conn, "star_fact_cost_sensitivity")           # a second copy of the first row: a repeated grain with a number (break_even) in it
    found = keys_unique(conn)
    assert found.violations == 1 and found.sample[0][0] == "fact_cost_sensitivity"
    key = json.loads(found.sample[0][1])
    assert len(key) == 4 and isinstance(key[1], float) and key[0] in ("valid_v2", "test")


# --- R2-magic-sentinel-date and R3-tracking-sentinel-date ---------------------------------------------------------------------------------------


def tracking_problems(conn) -> dict[str, int]:
    """The rows of the control of the follow-up, read straight from its file (it reads ``tracking_monthly`` and ``model_scores`` only, not the built marts)."""
    rows = conn.execute(sql_of(CHECKS, "chk_tracking_totals.sql").rstrip().rstrip(";")).fetchall()
    return {problem: int(scored) for problem, _tracked, scored in rows}


def test_the_follow_up_control_has_no_sentinel_date_and_says_what_it_does_when_there_is_no_follow_up():
    text = sql_of(CHECKS, "chk_tracking_totals.sql")
    assert "0000-00-00" not in text, "no invented date to make a comparison fail"
    assert "IS NOT NULL" in text and "no_follow_up" in text


def test_the_follow_up_control_in_the_three_states_of_the_follow_up(private):
    conn, cfg, _tmp = private
    assert tracking_problems(conn) == {}, "a follow-up that adds up to the scores"
    conn.execute("DELETE FROM tracking_monthly")
    scored = conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0]
    assert scored > 0 and tracking_problems(conn) == {"no_follow_up": scored}, "scores and no follow-up: one problem, with the number of scores"
    conn.execute("DROP TRIGGER IF EXISTS model_scores_no_delete")
    conn.execute("DELETE FROM model_scores")
    assert tracking_problems(conn) == {}, "no scores and no follow-up is a warehouse before the first monthly delivery: nothing to say"


# --- R3-seal-state-coverage ---------------------------------------------------------------------------------------------------------------------


def seal_states(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT seal_state FROM star_dim_model ORDER BY model_version")]


def test_a_registered_model_is_sealed_post_seal_or_without_a_seal(private, tmp_path):
    conn, cfg, _tmp = private
    build_powerbi_marts(conn, cfg)
    assert seal_states(conn) == ["sealed"]
    another = tmp_path / "other_seal.json"
    write_launch_seal(another, launch_for(cfg, threshold=0.7))                          # a valid seal of another configuration than the registered one
    build_powerbi_marts(conn, replace(cfg, paths=replace(cfg.paths, seal=another)))
    assert seal_states(conn) == ["post_seal"], "the registered configuration is not the one in the seal"
    build_powerbi_marts(conn, replace(cfg, paths=replace(cfg.paths, seal=tmp_path / "no_seal_yet.json")))
    assert seal_states(conn) == ["none"], "a model registered before any seal file exists"
    assert conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0] == 1


# --- R3-dim-product-attr-mismatch -------------------------------------------------------------------------------------------------------------------


def add_line(conn, line_id: int, order_id: int, name: str, category: str, department: str, card: int = 7777) -> None:
    conn.execute(
        "INSERT INTO raw_order_lines VALUES (?, ?, 'M2018-01', ?, ?, ?, ?, 1, 10.0, 10.0, 0.0, 0.0, 10.0)", (line_id, order_id, department, category, card, name))


def add_order(conn, order_id: int, day: str) -> None:
    conn.execute("INSERT INTO raw_orders VALUES (?, 'M2018-01', ?, ?, 'post_break', 'Standard Class', 4, 'DEBIT', 'M', 'R', 'C', 'Consumer', 'C')", (order_id, f"{day}T09:00", day))


def test_a_product_takes_its_name_category_and_department_from_one_row(private):
    """Three independent ``MIN()`` gave a name and a category that never appeared together: ('Alpha', 'Alpha-cat', 'D0') out of ('Alpha', 'Zeta', 'D1') and ('Beta', 'Alpha-cat', 'D0')."""
    conn, cfg, _tmp = private
    add_order(conn, 910001, "2018-01-20")
    add_order(conn, 910002, "2018-01-21")
    add_line(conn, 9100011, 910001, "Alpha", "Zeta", "D1")
    add_line(conn, 9100021, 910002, "Beta", "Alpha-cat", "D0")
    build_powerbi_marts(conn, cfg)
    assert conn.execute("SELECT product_name, category_name, department_name FROM star_dim_product WHERE product_key = 7777").fetchone() == ("Alpha", "Zeta", "D1")
    names = conn.execute("SELECT COUNT(DISTINCT product_key) FROM star_dim_product").fetchone()[0]
    assert names == conn.execute("SELECT COUNT(DISTINCT product_card_id) FROM raw_order_lines").fetchone()[0], "still one row for each card"


def test_the_same_row_is_chosen_whatever_the_order_of_the_lines(private):
    conn, cfg, _tmp = private
    add_order(conn, 910003, "2018-01-22")
    add_order(conn, 910004, "2018-01-23")
    add_line(conn, 9100041, 910004, "Same", "Zeta", "D1", card=7778)                    # the line with the higher id is inserted first
    add_line(conn, 9100031, 910003, "Same", "Alpha-cat", "D0", card=7778)
    build_powerbi_marts(conn, cfg)
    assert conn.execute("SELECT product_name, category_name, department_name FROM star_dim_product WHERE product_key = 7778").fetchone() == ("Same", "Alpha-cat", "D0"), (
        "a tie of names is broken by the category, then by the department")


def test_a_card_with_one_value_in_every_column_keeps_it(private):
    conn, cfg, _tmp = private
    build_powerbi_marts(conn, cfg)
    for key, name, category, department in conn.execute("SELECT product_key, product_name, category_name, department_name FROM star_dim_product LIMIT 20").fetchall():
        rows = conn.execute("SELECT DISTINCT product_name, category_name, department_name FROM raw_order_lines WHERE product_card_id = ?", (key,)).fetchall()
        assert (name, category, department) in rows and len(rows) == 1, key


# --- R3-geo-null-join ---------------------------------------------------------------------------------------------------------------------------


def test_the_schema_refuses_an_order_without_geography_so_the_join_cannot_drop_it(private):
    conn, cfg, _tmp = private
    for column in ("market", "order_region", "order_country"):
        values = {"market": "M", "order_region": "R", "order_country": "C", column: None}
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            conn.execute("INSERT INTO raw_orders VALUES (910010, 'M2018-01', '2018-01-24T09:00', '2018-01-24', 'post_break', 'Standard Class', 4, 'DEBIT', ?, ?, ?, 'Consumer', 'C')",
                         (values["market"], values["order_region"], values["order_country"]))


def test_the_order_control_names_an_order_that_would_be_dropped_by_a_missing_geography():
    """If a NULL ever got in (a table rebuilt without its constraint), the join on equality would drop the order silently; the control now says why."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_orders (order_id INTEGER, market TEXT, order_region TEXT, order_country TEXT)")
    conn.execute("CREATE TABLE star_fact_order (order_id INTEGER)")
    conn.executemany("INSERT INTO raw_orders VALUES (?, ?, ?, ?)", [(1, "M", "R", "C"), (2, "M", None, "C"), (3, None, None, None)])
    conn.execute("INSERT INTO star_fact_order VALUES (1)")
    rows = conn.execute(sql_of(CHECKS, "chk_fact_order_unique.sql").rstrip().rstrip(";")).fetchall()
    assert sorted(rows) == [("geo_null", "2"), ("geo_null", "3"), ("missing_order", "2"), ("missing_order", "3")]
    conn.execute("INSERT INTO star_fact_order VALUES (2), (3)")
    assert sorted(conn.execute(sql_of(CHECKS, "chk_fact_order_unique.sql").rstrip().rstrip(";")).fetchall()) == [("geo_null", "2"), ("geo_null", "3")], (
        "an order with a missing geography is wrong even if some other join let it through")


def test_a_build_with_a_dropped_order_still_stops_and_says_which_control(private):
    conn, cfg, _tmp = private
    add_order(conn, 910020, "2018-01-25")                   # an order without lines does not reach the fact: the control of the orders stops the build
    with pytest.raises(CheckFailedError, match="chk_fact_order_unique"):
        build_powerbi_marts(conn, cfg)
