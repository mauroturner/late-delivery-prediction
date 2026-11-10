"""The variables of the model and their guards against leakage (U10, item: the first thing the independent evaluation probes): AC13, D06 and D11.

``build_features`` receives the orders and the lines as they are known when the order is created and returns one row per order with exactly the columns of a
set. Nothing that is known only afterwards, and nothing personal, can reach the matrix: the inputs are checked against the allowed list, the output against
the set, the constants against both, and the code of the variables never reads an outcome table (a static test reads it).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pandas as pd
import pytest

from latedel.features import (
    CATEGORICAL,
    FEATURE_SETS,
    OUTCOME_NAMES,
    LeakageError,
    build_features,
    check_feature_matrix,
    read_order_frames,
)
from latedel.schema import LEAKING_COLUMNS, ORDER_LINES_SCHEMA, ORDERS_SCHEMA, PERSONAL_COLUMNS

PROJECT = Path(__file__).resolve().parents[2]
F0 = ("shipping_mode", "scheduled_days")
F1 = F0 + ("payment_type", "market", "order_region", "order_country", "customer_segment", "customer_country", "main_department", "main_category", "main_product",
           "n_lines", "n_departments", "units", "gross_sales", "item_total", "discount", "max_discount_rate")
F2 = F1 + ("order_hour", "order_weekday", "order_day", "order_month")


def hand_case() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Three orders written by hand, with their lines. The expected features are written below from these numbers, not computed from the code."""
    orders = pd.DataFrame({
        "order_id": [30, 10, 20],                                   # not sorted: the matrix is by order_id
        "order_ts": ["2017-03-05T09:15", "2016-12-30T23:59", "2017-02-14T12:00"],       # a Sunday, a Friday and a Tuesday
        "order_date": pd.to_datetime(["2017-03-05", "2016-12-30", "2017-02-14"]),
        "regime": ["pre_break"] * 3,
        "shipping_mode": ["Same Day", "Standard Class", "First Class"],
        "scheduled_days": [0, 4, 1],
        "payment_type": ["DEBIT", "CASH", "TRANSFER"],
        "market": ["LATAM", "Europe", "USCA"],
        "order_region": ["Caribbean", "Western Europe", "US Center"],
        "order_country": ["Cuba", "Francia", "Estados Unidos"],
        "customer_segment": ["Consumer", "Corporate", "Home Office"],
        "customer_country": ["EE. UU.", "Puerto Rico", "EE. UU."],
    })
    lines = pd.DataFrame({
        "line_id": [5, 4, 9, 2, 8, 7],
        "order_id": [10, 10, 10, 20, 30, 30],
        "department_name": ["Fitness", "Footwear", "Golf", "Apparel", "Outdoors", "Outdoors"],
        "category_name": ["C5", "C4", "C9", "C2", "C8", "C7"],
        "product_card_id": [1, 2, 3, 4, 5, 6],
        "product_name": ["P5", "P4", "P9", "P2", "P8", "P7"],
        "quantity": [1, 2, 3, 4, 1, 1],
        "product_price": [10.0, 15.0, 10.0, 25.0, 5.0, 5.0],
        "sales": [10.0, 30.0, 30.0, 100.0, 5.0, 5.0],
        "discount": [1.0, 3.0, 0.0, 10.0, 0.5, 0.0],
        "discount_rate": [0.1, 0.1, 0.0, 0.1, 0.1, 0.0],
        "item_total": [9.0, 27.0, 30.0, 90.0, 4.5, 5.0],
    })
    return orders, lines


EXPECTED = {   # order_id -> the values the plan defines (section 8), by hand
    10: dict(main_department="Footwear", main_category="C4", main_product="P4", n_lines=3, n_departments=3, units=6, gross_sales=70.0, item_total=66.0, discount=4.0,
             max_discount_rate=0.1, order_hour=23, order_weekday=4, order_day=30, order_month=12),      # the tie of 30.0 goes to line 4, the lowest id
    20: dict(main_department="Apparel", main_category="C2", main_product="P2", n_lines=1, n_departments=1, units=4, gross_sales=100.0, item_total=90.0, discount=10.0,
             max_discount_rate=0.1, order_hour=12, order_weekday=1, order_day=14, order_month=2),
    30: dict(main_department="Outdoors", main_category="C7", main_product="P7", n_lines=2, n_departments=1, units=2, gross_sales=10.0, item_total=9.5, discount=0.5,
             max_discount_rate=0.1, order_hour=9, order_weekday=6, order_day=5, order_month=3),         # a tie of 5.0: line 7, not line 8
}


# --- the sets (D11) ----------------------------------------------------------------------------------------------------------


def test_the_three_sets_are_those_of_the_plan_and_are_nested():
    assert FEATURE_SETS == {"f0": F0, "f1": F1, "f2": F2}
    assert [len(FEATURE_SETS[name]) for name in ("f0", "f1", "f2")] == [2, 18, 22]
    assert FEATURE_SETS["f1"][: len(F0)] == F0 and FEATURE_SETS["f2"][: len(F1)] == F1
    assert CATEGORICAL == frozenset({"shipping_mode", "payment_type", "market", "order_region", "order_country", "customer_segment", "customer_country",
                                     "main_department", "main_category", "main_product"})
    assert CATEGORICAL <= set(F1)


def test_no_variable_is_an_outcome_or_a_personal_column():
    banned = {re.sub(r"[^a-z0-9]", "", name.lower()) for name in (*LEAKING_COLUMNS, *PERSONAL_COLUMNS, *OUTCOME_NAMES)}
    for name, columns in FEATURE_SETS.items():
        assert not {re.sub(r"[^a-z0-9]", "", c.lower()) for c in columns} & banned, name
    assert {"is_late", "real_days", "shipping_ts", "outcome_known_date", "delivery_status", "order_status", "is_cancelled", "benefit_total", "profit_ratio_mean",
            "source_label"} <= set(OUTCOME_NAMES)


# --- the values -------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["f0", "f1", "f2"])
def test_the_matrix_has_one_row_per_order_and_the_columns_of_the_set_in_order(name):
    orders, lines = hand_case()
    matrix = build_features(orders, lines, name)
    assert list(matrix.columns) == list(FEATURE_SETS[name]) and matrix.index.name == "order_id"
    assert matrix.index.tolist() == [10, 20, 30] and matrix.index.is_unique
    assert orders["order_id"].tolist() == [30, 10, 20], "the inputs are not modified"


def test_every_value_of_the_hand_case_is_the_one_written_by_hand():
    orders, lines = hand_case()
    matrix = build_features(orders, lines, "f2")
    base = orders.set_index("order_id")
    for order_id, expected in EXPECTED.items():
        row = matrix.loc[order_id]
        for column in F0 + F1[2:9]:
            if column in base.columns:
                assert row[column] == base.loc[order_id, column], (order_id, column)
        for column, value in expected.items():
            assert row[column] == pytest.approx(value), (order_id, column)
    assert matrix["scheduled_days"].tolist() == [4, 1, 0] and matrix["shipping_mode"].tolist() == ["Standard Class", "First Class", "Same Day"]
    assert str(matrix["order_hour"].dtype).startswith("int") and str(matrix["gross_sales"].dtype) == "float64"


def test_a_smaller_set_is_a_column_subset_of_the_larger_ones_with_the_same_values():
    orders, lines = hand_case()
    f0, f1, f2 = (build_features(orders, lines, name) for name in ("f0", "f1", "f2"))
    pd.testing.assert_frame_equal(f1[list(F0)], f0)
    pd.testing.assert_frame_equal(f2[list(F1)], f1)


def test_the_matrix_does_not_depend_on_the_order_of_the_rows_or_of_the_extra_allowed_columns():
    orders, lines = hand_case()
    expected = build_features(orders, lines, "f2")
    shuffled = build_features(orders.iloc[::-1].reset_index(drop=True), lines.sample(frac=1, random_state=3).reset_index(drop=True), "f2")
    pd.testing.assert_frame_equal(shuffled, expected)
    reduced = build_features(orders.drop(columns=["order_date", "regime"]), lines.drop(columns=["product_card_id"]), "f2")
    pd.testing.assert_frame_equal(reduced, expected)


def test_f0_needs_no_line_and_a_set_with_lines_needs_every_order_to_have_one():
    orders, lines = hand_case()
    assert build_features(orders, lines.iloc[0:0], "f0").shape == (3, 2)
    with pytest.raises(ValueError, match="sin líneas"):
        build_features(orders, lines[lines["order_id"] != 20], "f1")
    with pytest.raises(ValueError, match="sin pedido"):
        build_features(orders[orders["order_id"] != 20], lines, "f1")
    with pytest.raises(ValueError, match="repetido"):
        build_features(pd.concat([orders, orders.head(1)]), lines, "f1")


def test_an_unknown_set_and_missing_columns_are_value_errors_not_leaks():
    orders, lines = hand_case()
    for bad in ("F1", "f3", "", "f1 "):
        with pytest.raises(ValueError, match="conjunto"):
            build_features(orders, lines, bad)
    with pytest.raises(ValueError, match="faltan"):
        build_features(orders.drop(columns=["order_ts"]), lines, "f2")
    with pytest.raises(ValueError, match="faltan"):
        build_features(orders, lines.drop(columns=["sales"]), "f1")
    build_features(orders.drop(columns=["order_ts"]), lines, "f1")       # f1 does not use the timestamp


# --- the guards: what cannot reach the matrix (AC13) -------------------------------------------------------------------------------


def leaking_names() -> list[str]:
    internal = sorted(OUTCOME_NAMES)
    snake = [re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") for name in (*LEAKING_COLUMNS, *PERSONAL_COLUMNS)]
    return [*LEAKING_COLUMNS, *PERSONAL_COLUMNS, *internal, *snake, "Real_Days", " real_days", "real_days ", "REAL_DAYS", "is-late", "isLate"]


@pytest.mark.parametrize("frame", ["orders", "lines"])
def test_leaking_column_cannot_reach_the_feature_matrix(frame):
    orders, lines = hand_case()
    names = leaking_names()
    assert len(set(names)) > 40
    for name in names:
        bad_orders, bad_lines = orders.copy(), lines.copy()
        (bad_orders if frame == "orders" else bad_lines)[name] = 1
        for feature_set in ("f0", "f1", "f2"):
            with pytest.raises(LeakageError, match=re.escape(repr(name))):
                build_features(bad_orders, bad_lines, feature_set)


def test_a_column_that_is_not_on_the_allowed_list_is_refused_even_if_it_looks_harmless():
    orders, lines = hand_case()
    for name in ("extra", "department_name", "order_id2", "Order Id", ""):
        with pytest.raises(LeakageError, match="lista permitida"):
            build_features(orders.assign(**{name: 1}), lines, "f1")
    with pytest.raises(LeakageError, match="lista permitida"):
        build_features(orders, lines.assign(market="x"), "f1")                  # an order column in the lines frame
    with pytest.raises(LeakageError, match="lista permitida"):
        build_features(orders.assign(sales=1.0), lines, "f1")                    # a line column in the orders frame


def test_columns_that_are_not_plain_unique_text_are_refused():
    orders, lines = hand_case()
    with pytest.raises(LeakageError, match="texto"):
        build_features(orders.rename(columns={"market": 5}), lines, "f1")
    multi = orders.copy()
    multi.columns = pd.MultiIndex.from_tuples([("a", c) for c in multi.columns])
    with pytest.raises(LeakageError):
        build_features(multi, lines, "f1")
    duplicated = pd.concat([orders, orders[["market"]]], axis=1)
    with pytest.raises(LeakageError, match="repetida"):
        build_features(duplicated, lines, "f1")
    with pytest.raises(LeakageError, match="repetida"):
        build_features(orders, pd.concat([lines, lines[["sales"]]], axis=1), "f1")


def test_the_inputs_must_be_data_frames():
    orders, lines = hand_case()
    for bad in (None, {"order_id": [1]}, orders.to_numpy()):
        with pytest.raises(TypeError, match="DataFrame"):
            build_features(bad, lines, "f1")  # type: ignore[arg-type]


def test_a_refused_call_returns_nothing_and_leaves_the_inputs_as_they_were():
    orders, lines = hand_case()
    bad = orders.assign(is_late=1)
    before = bad.copy()
    with pytest.raises(LeakageError):
        build_features(bad, lines, "f2")
    pd.testing.assert_frame_equal(bad, before)


def test_the_constants_are_checked_at_every_call_so_a_leaking_set_cannot_be_used(monkeypatch):
    orders, lines = hand_case()
    for extra in ("real_days", "Late_delivery_risk", "customer_email", "is_late", "shipping_ts"):
        monkeypatch.setitem(FEATURE_SETS, "f1", (*F1, extra))
        with pytest.raises(LeakageError, match="conjunto"):
            build_features(orders, lines, "f1")
    monkeypatch.setitem(FEATURE_SETS, "f1", (*F1, "something_new"))
    with pytest.raises(LeakageError, match="conjunto"):
        build_features(orders, lines, "f1")                                       # a name that nothing derives is a leak until proven otherwise


def test_the_matrix_check_refuses_extra_missing_reordered_and_leaking_columns_and_repeated_rows():
    orders, lines = hand_case()
    good = build_features(orders, lines, "f1")
    check_feature_matrix(good, "f1")
    with pytest.raises(LeakageError, match="exactamente"):
        check_feature_matrix(good.assign(extra=1), "f1")
    with pytest.raises(LeakageError, match="resultado"):
        check_feature_matrix(good.assign(is_late=1), "f1")
    with pytest.raises(LeakageError, match="exactamente"):
        check_feature_matrix(good.drop(columns=["units"]), "f1")
    with pytest.raises(LeakageError, match="exactamente"):
        check_feature_matrix(good[list(reversed(good.columns))], "f1")
    with pytest.raises(LeakageError, match="exactamente"):
        check_feature_matrix(good, "f2")
    with pytest.raises(LeakageError, match="una fila por pedido"):
        check_feature_matrix(pd.concat([good, good.head(1)]), "f1")
    with pytest.raises(LeakageError, match="resultado"):
        check_feature_matrix(good.rename(columns={"units": "real_days"}), "f1")


# --- the code of the variables never reads an outcome table (AC13) -------------------------------------------------------------


OUTCOME_TABLES = ("stg_outcomes", "raw_outcomes", "mart_labelled_orders")


def names_found(text: str) -> list[str]:
    return [name for name in OUTCOME_TABLES if name in text.lower()]


def checked_files() -> list[Path]:
    """The code that builds or serves the variables: today the module of the variables and the two staging files; the scoring module joins when it exists."""
    files = [PROJECT / "src" / "latedel" / "features.py", PROJECT / "sql" / "staging" / "stg_orders.sql", PROJECT / "sql" / "staging" / "stg_order_lines.sql"]
    scoring = PROJECT / "src" / "latedel" / "scoring.py"
    return files + ([scoring] if scoring.exists() else [])


def test_feature_code_never_reads_outcome_tables():
    files = checked_files()
    assert len(files) >= 3 and all(f.is_file() and f.stat().st_size > 200 for f in files)
    for path in files:
        assert names_found(path.read_text(encoding="utf-8")) == [], f"{path.name} names an outcome table"


def test_the_static_check_would_see_a_planted_outcome_table():
    for planted in ("SELECT * FROM stg_outcomes", "conn.execute('select 1 from RAW_OUTCOMES')", "# reads mart_labelled_orders"):
        assert names_found(planted) != []
    assert names_found("SELECT * FROM stg_orders JOIN stg_order_lines") == []


def test_the_module_of_the_variables_imports_only_the_schema_of_the_project():
    tree = ast.parse((PROJECT / "src" / "latedel" / "features.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("latedel"):
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names if alias.name.startswith("latedel")}
    assert imported == {"latedel.schema"}, "no warehouse, no reader of the source, no deliveries: nothing that could hold an outcome"


def test_the_staging_tables_that_feed_the_variables_have_only_allowed_columns(cfg, delivered, tmp_path):
    from latedel.warehouse import connect
    from tests.fixtures.helpers import load_through

    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 1)
    assert [r[1] for r in conn.execute("PRAGMA table_info(stg_orders)")] == ORDERS_SCHEMA.names
    assert [r[1] for r in conn.execute("PRAGMA table_info(stg_order_lines)")] == ORDER_LINES_SCHEMA.names


def test_the_reader_of_the_inputs_runs_only_statements_on_the_two_staging_tables(cfg, delivered, tmp_path):
    from latedel.warehouse import connect
    from tests.fixtures.helpers import load_through

    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 3)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    orders, lines = read_order_frames(conn)
    conn.set_trace_callback(None)
    assert statements and all(names_found(s) == [] for s in statements)
    assert {m for s in statements for m in re.findall(r"(?:from|join)\s+(\w+)", s.lower())} == {"stg_orders", "stg_order_lines"}
    assert list(orders.columns) == ORDERS_SCHEMA.names and list(lines.columns) == ORDER_LINES_SCHEMA.names
    assert len(orders) == conn.execute("SELECT COUNT(*) FROM stg_orders").fetchone()[0]
    assert str(orders["order_date"].dtype).startswith("datetime64") and orders["order_id"].is_unique
    assert str(build_features(orders, lines, "f2").index.dtype) == "int64"
