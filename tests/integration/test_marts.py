"""The operational marts and the controls of the load (U09): the main line of each order, the labelled orders and the twelve controls (AC12, AC15 in part).

``mart_labelled_orders`` is the only source of labels of the model and it is rebuilt for a calculation date: an order enters it only with the outcome known on
that date (RNF06). The expectations are computed in the tests from the frames of the fixture, independently of the SQL, and from hand-made rows for the edges.
"""

from __future__ import annotations

import shutil
from datetime import date, timedelta

import pandas as pd
import pytest

from latedel import warehouse
from latedel.deliveries import delivery_plan, publish, read_delivery
from latedel.warehouse import CheckFailedError, apply_schema, connect, load_delivery, rebuild_at, run_checks, sql_params
from tests.fixtures.helpers import file_digest, load_through, rebuild_layers

CHECKS = [
    "chk_label_definition", "chk_labelled_has_a_label", "chk_labelled_one_per_order", "chk_line_has_order", "chk_main_line_one_per_order", "chk_nothing_after_as_of", "chk_one_mode_per_order",
    "chk_outcome_has_order", "chk_outcome_lag_within_purge", "chk_outcome_not_before_order", "chk_outcome_unique", "chk_raw_order_unique",
]
ORDER_COLUMNS = "order_id, delivery_id, order_ts, order_date, regime, shipping_mode, scheduled_days, payment_type, market, order_region, order_country, customer_segment, customer_country"


def expected_block(day: date, cfg) -> str:
    """The block of an order, written independently of the SQL from the table of the requirements (section 4)."""
    s = cfg.split
    if s.train_start <= day <= s.train_end:
        return "train"
    if s.train_end < day < s.valid_start:
        return "purge_1"
    if s.valid_start <= day < s.valid_v2_start:
        return "valid_v1"
    if s.valid_v2_start <= day <= s.valid_end:
        return "valid_v2"
    if s.valid_end < day < s.test_start:
        return "purge_2"
    if s.test_start <= day <= s.test_end:
        return "test"
    if s.post_break_start <= day <= s.post_break_end:
        return "post_break"
    return "outside"


# --- hand-made warehouses ---------------------------------------------------------------------------------------------------------


def scratch(tmp_path, cfg, name: str = "s.sqlite"):
    """An empty warehouse with its schema and its (empty) marts."""
    conn = connect(tmp_path / name)
    apply_schema(conn)
    rebuild_at(conn, date(2018, 2, 7), cfg)
    return conn


def add_order(conn, order_id: int, day: date, mode: str = "First Class", scheduled: int = 1, hour: int = 9) -> None:
    conn.execute(
        f"INSERT INTO raw_orders ({ORDER_COLUMNS}) VALUES (?, 'D', ?, ?, ?, ?, ?, 'DEBIT', 'LATAM', 'Central America', 'Mexico', 'Consumer', 'EE. UU.')",
        (order_id, f"{day.isoformat()}T{hour:02d}:00", day.isoformat(), "post_break" if day >= date(2017, 10, 1) else "pre_break", mode, scheduled),
    )


def add_line(conn, line_id: int, order_id: int, sales: float = 10.0) -> None:
    conn.execute(
        "INSERT INTO raw_order_lines (line_id, order_id, delivery_id, department_name, category_name, product_card_id, product_name, quantity, product_price, sales, "
        "discount, discount_rate, item_total) VALUES (?, ?, 'D', 'Fitness', 'Cleats', 1000, 'Fixture Product', 1, ?, ?, 0, 0, ?)", (line_id, order_id, sales, sales, sales))


def add_outcome(conn, order_id: int, known: date, real_days: int = 2, label: int | None = None, cancelled: int = 0, scheduled: int = 1) -> None:
    label = int(real_days > scheduled) if label is None else label
    conn.execute(
        "INSERT INTO raw_outcomes (order_id, delivery_id, outcome_known_date, delivery_status, order_status, real_days, shipping_ts, source_label, is_cancelled, "
        "benefit_total, profit_ratio_mean) VALUES (?, 'D', ?, 'Late delivery', 'COMPLETE', ?, '2017-01-01T00:00', ?, ?, 1.0, 0.1)", (order_id, known.isoformat(), real_days, label, cancelled))


def violating(conn, cfg, as_of: date) -> dict[str, int]:
    """The controls with violations for the date, from ``run_checks`` as the load runs them."""
    return {c.check_id: c.violations for c in run_checks(conn, "load", sql_params(cfg, as_of)) if c.violations}


# --- the labelled orders at a date exclude every outcome that is not known yet (AC12, AC15) ----------------------------------------


@pytest.mark.parametrize("as_of", [date(2017, 1, 7), date(2017, 1, 20), date(2017, 3, 31), date(2017, 10, 7), date(2018, 2, 7)])
def test_labelled_orders_at_as_of_exclude_later_outcomes(cfg, delivered, fixture_read, tmp_path, as_of):
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 15)                  # the warehouse holds everything: the date alone decides what is labelled
    rebuild_at(conn, as_of, cfg)
    frames = fixture_read[0]
    merged = frames.orders.merge(frames.outcomes, on="order_id")
    task = merged[~merged["is_cancelled"]]
    expected = task[(task["order_date"] <= pd.Timestamp(as_of)) & (task["outcome_known_date"] <= pd.Timestamp(as_of))]
    later = task[(task["order_date"] <= pd.Timestamp(as_of)) & (task["outcome_known_date"] > pd.Timestamp(as_of))]
    mart = pd.read_sql_query("SELECT * FROM mart_labelled_orders ORDER BY order_id", conn)
    assert mart["order_id"].tolist() == sorted(expected["order_id"])
    assert (mart["outcome_known_date"] <= as_of.isoformat()).all() and (mart["order_date"] <= as_of.isoformat()).all()
    assert set(later["order_id"]).isdisjoint(mart["order_id"]), "an order whose outcome is not known on the date is not labelled"
    by_id = expected.set_index("order_id")
    assert mart["is_late"].tolist() == [int(by_id.loc[o, "real_days"] > by_id.loc[o, "scheduled_days"]) for o in mart["order_id"]]
    assert mart["block"].tolist() == [expected_block(date.fromisoformat(d), cfg) for d in mart["order_date"]]
    assert not set(frames.outcomes[frames.outcomes["is_cancelled"]]["order_id"]) & set(mart["order_id"]), "a cancelled order has no label of the task"
    assert mart.columns.tolist() == ["order_id", "order_ts", "order_date", "regime", "shipping_mode", "scheduled_days", "outcome_known_date", "is_late", "block"]


def test_the_date_changes_the_labels_known_for_orders_already_in_the_warehouse(cfg, delivered, fixture_read, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 15)
    frames = fixture_read[0]
    merged = frames.orders.merge(frames.outcomes, on="order_id")
    pending = merged[(~merged["is_cancelled"]) & (merged["order_date"] <= pd.Timestamp("2017-03-15")) & (merged["outcome_known_date"] > pd.Timestamp("2017-03-15"))]
    assert len(pending) > 0, "some order of March 15 or before has its outcome after that date"
    order = int(pending["order_id"].iloc[0])
    rebuild_at(conn, date(2017, 3, 15), cfg)
    assert conn.execute("SELECT COUNT(*) FROM mart_labelled_orders WHERE order_id = ?", (order,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM stg_orders WHERE order_id = ?", (order,)).fetchone()[0] == 1, "the order itself is known"
    rebuild_at(conn, date(2017, 3, 31), cfg)
    assert conn.execute("SELECT COUNT(*) FROM mart_labelled_orders WHERE order_id = ?", (order,)).fetchone()[0] == 1


def test_every_label_of_the_training_block_is_known_before_the_validation_and_every_validation_label_before_the_launch(cfg, delivered, fixture_read, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 1)                   # the launch: the test block does not exist in the warehouse yet
    mart = pd.read_sql_query("SELECT * FROM mart_labelled_orders", conn)
    s = cfg.split
    assert set(mart["block"]) <= {"train", "purge_1", "valid_v1", "valid_v2", "purge_2"}
    assert mart[mart["block"] == "train"]["outcome_known_date"].max() < s.valid_start.isoformat()
    valid = mart[mart["block"].isin(["valid_v1", "valid_v2"])]
    assert valid["outcome_known_date"].max() <= cfg.deliveries.launch_date.isoformat()
    frames = fixture_read[0]
    task = frames.orders.merge(frames.outcomes, on="order_id")
    task = task[~task["is_cancelled"]]
    for block, first, last in (("train", s.train_start, s.train_end), ("valid_v1", s.valid_start, s.valid_v2_start - timedelta(days=1)), ("valid_v2", s.valid_v2_start, s.valid_end)):
        in_block = task[(task["order_date"] >= pd.Timestamp(first)) & (task["order_date"] <= pd.Timestamp(last))]
        assert (mart["block"] == block).sum() == len(in_block) > 0, f"every non-cancelled order of {block} is labelled at the launch"


def test_the_block_edges_are_exact_and_the_purges_belong_to_no_block(cfg, tmp_path):
    days = [
        (date(2014, 12, 31), "outside"), (date(2015, 1, 1), "train"), (date(2016, 6, 30), "train"), (date(2016, 7, 1), "purge_1"), (date(2016, 7, 7), "purge_1"),
        (date(2016, 7, 8), "valid_v1"), (date(2016, 9, 30), "valid_v1"), (date(2016, 10, 1), "valid_v2"), (date(2016, 12, 31), "valid_v2"),
        (date(2017, 1, 1), "purge_2"), (date(2017, 1, 7), "purge_2"), (date(2017, 1, 8), "test"), (date(2017, 9, 30), "test"), (date(2017, 10, 1), "post_break"),
        (date(2018, 1, 31), "post_break"), (date(2018, 2, 1), "outside"),
    ]
    conn = scratch(tmp_path, cfg)
    for number, (day, _block) in enumerate(days, start=1):
        add_order(conn, number, day)
        add_line(conn, 100 + number, number)
        add_outcome(conn, number, day + timedelta(days=2))
    rebuild_at(conn, date(2018, 2, 7), cfg)
    found = dict(conn.execute("SELECT order_date, block FROM mart_labelled_orders").fetchall())
    assert found == {day.isoformat(): block for day, block in days}
    assert [expected_block(day, cfg) for day, _ in days] == [block for _, block in days], "the expectation of the test agrees with its own table"
    assert {b for b in found.values() if b.startswith("purge") or b == "outside"} == {"purge_1", "purge_2", "outside"}


def test_a_cancelled_order_and_an_order_without_an_outcome_are_not_labelled_and_the_date_is_inclusive(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    for number in range(1, 5):
        add_order(conn, number, date(2017, 2, 5))
        add_line(conn, number, number)
    add_outcome(conn, 1, date(2017, 2, 10))                       # known exactly on the date: included
    add_outcome(conn, 2, date(2017, 2, 11))                       # known a day later: not yet
    add_outcome(conn, 3, date(2017, 2, 6), cancelled=1, real_days=0, label=0)
    rebuild_at(conn, date(2017, 2, 10), cfg)                      # order 4 has no outcome at all
    assert conn.execute("SELECT order_id, is_late FROM mart_labelled_orders").fetchall() == [(1, 1)]
    assert conn.execute("SELECT COUNT(*) FROM stg_outcomes").fetchone()[0] == 2 and conn.execute("SELECT is_late FROM stg_outcomes WHERE order_id = 3").fetchone() == (None,)


# --- the main line of an order ------------------------------------------------------------------------------------------------------------


def test_the_main_line_is_the_one_with_the_most_sales_and_the_lowest_id_on_a_tie(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    for number in (1, 2, 3):
        add_order(conn, number, date(2017, 2, 1))
    for line, order, sales in ((5, 1, 10.0), (4, 1, 30.0), (9, 1, 30.0), (7, 2, 3.5), (21, 3, 5.0), (20, 3, 5.0)):
        add_line(conn, line, order, sales)
    rebuild_at(conn, date(2017, 2, 28), cfg)
    assert conn.execute("SELECT order_id, line_id, sales FROM mart_main_line ORDER BY order_id").fetchall() == [(1, 4, 30.0), (2, 7, 3.5), (3, 20, 5.0)]


def test_the_main_line_of_every_order_of_the_fixture_matches_an_independent_computation(cfg, delivered, fixture_read, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 15)
    lines = fixture_read[0].order_lines.sort_values(["order_id", "sales", "line_id"], ascending=[True, False, True])
    expected = lines.groupby("order_id").head(1).sort_values("order_id")
    mart = pd.read_sql_query("SELECT order_id, line_id, department_name, category_name, product_name FROM mart_main_line ORDER BY order_id", conn)
    assert mart["order_id"].tolist() == expected["order_id"].tolist() == sorted(fixture_read[0].orders["order_id"])
    assert mart["line_id"].tolist() == expected["line_id"].tolist()
    assert mart["product_name"].tolist() == expected["product_name"].tolist()


# --- the controls: each one fires on its case and the loaded fixture passes all of them (AC12) -----------------------------------------


def test_the_load_has_exactly_these_controls_and_every_load_of_the_fixture_passes_them(cfg, delivered, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    folder, plan, entries = delivered
    for index in range(15):
        result = load_delivery(conn, read_delivery(folder, entries[index]), plan[index], f"run-{index}", cfg, sha256_json=entries[index].sha256_json)
        assert [c.check_id for c in result.checks] == CHECKS
        assert all(c.violations == 0 for c in result.checks)


def test_chk_raw_order_unique(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    conn.execute("DROP TABLE raw_orders")
    conn.execute("CREATE TABLE raw_orders (order_id INTEGER, delivery_id TEXT, order_ts TEXT, order_date TEXT, regime TEXT, shipping_mode TEXT, scheduled_days INTEGER, "
                 "payment_type TEXT, market TEXT, order_region TEXT, order_country TEXT, customer_segment TEXT, customer_country TEXT)")
    add_order(conn, 1, date(2017, 2, 1))
    assert violating(conn, cfg, date(2017, 2, 28)) == {}
    add_order(conn, 1, date(2017, 2, 1))
    assert violating(conn, cfg, date(2017, 2, 28)) == {"chk_raw_order_unique": 1}


def test_chk_line_has_order_and_chk_outcome_has_order(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    add_order(conn, 1, date(2017, 2, 1))
    add_line(conn, 1, 1)
    add_outcome(conn, 1, date(2017, 2, 3))
    assert violating(conn, cfg, date(2017, 2, 28)) == {}
    add_line(conn, 2, 99)
    add_outcome(conn, 98, date(2017, 2, 3))
    found = violating(conn, cfg, date(2017, 2, 28))
    assert found["chk_line_has_order"] == 1 and found["chk_outcome_has_order"] == 1
    assert [c.sample for c in run_checks(conn, "load", sql_params(cfg, date(2017, 2, 28))) if c.check_id == "chk_line_has_order"] == [[(2, 99)]]


def test_chk_outcome_unique(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    conn.execute("DROP TABLE raw_outcomes")
    conn.execute("CREATE TABLE raw_outcomes (order_id INTEGER, delivery_id TEXT, outcome_known_date TEXT, delivery_status TEXT, order_status TEXT, real_days INTEGER, "
                 "shipping_ts TEXT, source_label INTEGER, is_cancelled INTEGER, benefit_total REAL, profit_ratio_mean REAL)")
    add_order(conn, 1, date(2017, 2, 1))
    add_line(conn, 1, 1)
    add_outcome(conn, 1, date(2017, 2, 3))
    assert violating(conn, cfg, date(2017, 2, 28)) == {}
    add_outcome(conn, 1, date(2017, 2, 4))
    assert violating(conn, cfg, date(2017, 2, 28)) == {"chk_outcome_unique": 1}


def test_chk_outcome_not_before_order_and_chk_outcome_lag_within_purge(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    for number, (known, expect) in enumerate(((date(2017, 2, 1), None), (date(2017, 2, 7), None), (date(2017, 2, 8), "lag"), (date(2017, 1, 31), "before")), start=1):
        add_order(conn, number, date(2017, 2, 1))
        add_line(conn, number, number)
        add_outcome(conn, number, known)
        found = violating(conn, cfg, date(2017, 2, 28))
        if expect is None:
            assert found == {}, (number, found)
        elif expect == "lag":
            assert found == {"chk_outcome_lag_within_purge": 1}, "7 days after the order is one more than the purge covers (purge_days - 1 = 6)"
        else:
            assert found == {"chk_outcome_not_before_order": 1}, "known before the order: a negative lag is not a lag over the purge"
        if expect:
            conn.execute("DELETE FROM raw_outcomes WHERE order_id = ?", (number,))
    assert violating(conn, cfg, date(2017, 2, 28)) == {}


def test_chk_label_definition_ignores_cancelled_orders(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    add_order(conn, 1, date(2017, 2, 1))
    add_line(conn, 1, 1)
    add_outcome(conn, 1, date(2017, 2, 4), real_days=5, label=1)
    add_order(conn, 2, date(2017, 2, 1))
    add_line(conn, 2, 2)
    add_outcome(conn, 2, date(2017, 2, 4), real_days=0, label=1, cancelled=1)         # a cancelled order: whatever its label says
    assert violating(conn, cfg, date(2017, 2, 28)) == {}
    add_order(conn, 3, date(2017, 2, 1))
    add_line(conn, 3, 3)
    add_outcome(conn, 3, date(2017, 2, 4), real_days=5, label=0)                      # the label says on time and the days say late
    assert violating(conn, cfg, date(2017, 2, 28)) == {"chk_label_definition": 1}


def test_chk_one_mode_per_order_and_chk_main_line_one_per_order(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    add_order(conn, 1, date(2017, 2, 1), mode="")
    add_line(conn, 1, 1)
    add_order(conn, 2, date(2017, 2, 1))                           # an order without lines
    rebuild_layers(conn, cfg, date(2017, 2, 28))
    assert violating(conn, cfg, date(2017, 2, 28)) == {"chk_one_mode_per_order": 1, "chk_main_line_one_per_order": 1}


def test_chk_labelled_has_a_label(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    add_order(conn, 1, date(2017, 2, 1))
    add_line(conn, 1, 1)
    add_outcome(conn, 1, date(2017, 2, 4))
    rebuild_layers(conn, cfg, date(2017, 2, 10))
    assert violating(conn, cfg, date(2017, 2, 10)) == {}
    conn.execute("UPDATE mart_labelled_orders SET is_late = NULL")
    assert violating(conn, cfg, date(2017, 2, 10)) == {"chk_labelled_has_a_label": 1}


def test_chk_nothing_after_as_of_and_chk_labelled_one_per_order(cfg, tmp_path):
    conn = scratch(tmp_path, cfg)
    add_order(conn, 1, date(2017, 2, 1))
    add_line(conn, 1, 1)
    add_outcome(conn, 1, date(2017, 2, 4))
    rebuild_layers(conn, cfg, date(2017, 2, 10))
    assert violating(conn, cfg, date(2017, 2, 10)) == {}
    assert violating(conn, cfg, date(2017, 2, 3)) == {"chk_nothing_after_as_of": 2}, "an earlier date finds the outcome both in the staging and in the mart"
    assert violating(conn, cfg, date(2017, 1, 31)) == {"chk_nothing_after_as_of": 3}, "and the order too: one row of the staging orders, one of the outcomes and one of the mart"
    conn.execute("INSERT INTO mart_labelled_orders SELECT * FROM mart_labelled_orders")
    assert violating(conn, cfg, date(2017, 2, 10)) == {"chk_labelled_one_per_order": 1}


def test_a_load_whose_marts_break_a_control_is_undone_with_the_rest(cfg, delivered, tmp_path, monkeypatch):
    path = tmp_path / "w.sqlite"
    conn = connect(path)
    load_through(conn, delivered, cfg, 2)
    conn.close()
    before = file_digest(path)
    target = tmp_path / "sql"
    shutil.copytree(warehouse.SQL_DIR, target)
    (target / "marts" / "ops" / "mart_labelled_orders.sql").write_text(
        (target / "marts" / "ops" / "mart_labelled_orders.sql").read_text(encoding="utf-8").replace("WHERE r.is_cancelled = 0", "WHERE 1 = 1"), encoding="utf-8")
    monkeypatch.setattr(warehouse, "SQL_DIR", target)
    conn = connect(path)
    folder, plan, entries = delivered
    with pytest.raises(CheckFailedError) as error:
        load_delivery(conn, read_delivery(folder, entries[2]), plan[2], "run-3", cfg, sha256_json=entries[2].sha256_json)
    assert error.value.checks and conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 2
    conn.close()
    assert file_digest(path) == before


# --- rebuilding for a date leaves the raw tables alone and is idempotent ----------------------------------------------------------------------


def test_rebuilding_for_a_date_is_idempotent_and_never_touches_the_raw_tables(cfg, delivered, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 15)
    raw = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in ("raw_orders", "raw_order_lines", "raw_outcomes", "deliveries")}
    rebuild_at(conn, date(2017, 5, 31), cfg)
    first = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in ("stg_orders", "stg_outcomes", "mart_labelled_orders", "mart_main_line")}
    rebuild_at(conn, date(2017, 5, 31), cfg)
    assert {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in first} == first
    assert {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in raw} == raw
    assert not conn.in_transaction


# --- the real file (real_data) --------------------------------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_file_blocks_labels_and_main_lines_of_the_split(cfg, real_read, tmp_path):
    frames, _report = real_read
    plan = delivery_plan(cfg.deliveries)
    conn = connect(tmp_path / "real.sqlite")
    for spec in plan:
        entry = publish(spec, frames, tmp_path / "deliveries", source_sha256="fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6")
        result = load_delivery(conn, read_delivery(tmp_path / "deliveries", entry), spec, f"real-{spec.delivery_id}", cfg, sha256_json=entry.sha256_json)
        assert all(c.violations == 0 for c in result.checks) and len(result.checks) == 12
    blocks = dict(conn.execute("SELECT block, COUNT(*) FROM mart_labelled_orders GROUP BY block").fetchall())
    assert blocks == {"train": 29_894, "purge_1": 375, "valid_v1": 4_649, "valid_v2": 5_032, "purge_2": 377, "test": 14_527, "post_break": 8_043}, blocks
    assert sum(blocks.values()) == 62_897
    assert conn.execute("SELECT COUNT(*) FROM mart_main_line").fetchone()[0] == 65_752
    launch = date(2017, 1, 7)
    rebuild_at(conn, launch, cfg)
    at_launch = dict(conn.execute("SELECT block, COUNT(*) FROM mart_labelled_orders GROUP BY block").fetchall())
    assert set(at_launch) == {"train", "purge_1", "valid_v1", "valid_v2", "purge_2"}, "the test labels are not in the warehouse at the launch"
    assert at_launch["train"] == 29_894 and at_launch["valid_v1"] == 4_649 and at_launch["valid_v2"] == 5_032
    assert conn.execute("SELECT MAX(outcome_known_date) FROM mart_labelled_orders WHERE block = 'train'").fetchone()[0] < cfg.split.valid_start.isoformat()
    print("REAL blocks", blocks, "at launch", at_launch)
