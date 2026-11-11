"""The star schema Power BI reads: tables, relationships, Parquet files, keys and referential integrity (U17, AC24, decision D21).

Contract tests over the warehouse of the synthetic fixture after everything the flow does around the model (``star_warehouse``): they read the Parquet files back with
pyarrow and check them against the semantic model, and against the warehouse tables they were built from by independent queries.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace
from datetime import date

import pandas as pd
import pyarrow.parquet as pq
import pytest

from latedel import export
from latedel.dashboard.semantic_model import ARROW_TYPES, MODEL, Relationship, SemanticModel, arrow_schema, validate_model
from latedel.export import (
    MAX_PARQUET_BYTES,
    METADATA_KEYS,
    ExportMeta,
    build_powerbi_marts,
    export_run_table,
    export_star_schema,
    verify_star_schema,
)
from latedel.features import _forbidden
from latedel.schema import ATTRIBUTION
from latedel.warehouse import CheckFailedError, connect, record_run, run_checks
from tests.fixtures.make_fixture import SENTINEL_NEEDLES

META = ExportMeta(run_key="run-C2018-02", as_of="2018-02-07", source_sha256="ab" * 32)
DIMENSIONS = {"dim_date", "dim_month", "dim_shipping_mode", "dim_geo", "dim_product", "dim_segment", "dim_payment", "dim_model", "dim_pipeline_run", "dim_as_of"}
FACTS = {
    "fact_order", "fact_tracking_monthly", "fact_method_metrics", "fact_evaluation_summary", "fact_roc", "fact_calibration", "fact_cost_sensitivity", "fact_leakage",
    "fact_importance", "fact_validation",
}
# The relationships of the plan (section 16): dimension column -> fact column.
PLANNED = [
    ("dim_date[date_key]", "fact_order[order_date_key]"), ("dim_shipping_mode[mode_key]", "fact_order[mode_key]"),
    ("dim_shipping_mode[mode_key]", "fact_tracking_monthly[mode_key]"), ("dim_geo[geo_key]", "fact_order[geo_key]"),
    ("dim_product[product_key]", "fact_order[main_product_key]"), ("dim_segment[segment_key]", "fact_order[segment_key]"),
    ("dim_payment[payment_key]", "fact_order[payment_key]"), ("dim_month[month_key]", "fact_tracking_monthly[month_key]"),
    ("dim_pipeline_run[run_key]", "fact_validation[run_key]"),
]


@pytest.fixture(scope="module")
def star(star_export):
    """The Parquet files exported from a copy of the warehouse (once per session, ``star_export``), and the connection to that copy."""
    assert star_export.meta == META
    return star_export.conn, star_export.out_dir, star_export.paths


@pytest.fixture(scope="module")
def frames(star):
    _conn, out_dir, _paths = star
    return {t.name: pq.read_table(out_dir / f"{t.name}.parquet").to_pandas() for t in MODEL.tables}


@pytest.fixture()
def private(star_warehouse, tmp_path):
    """A private copy of the warehouse (not yet exported) for the tests that damage it."""
    path = tmp_path / "wh.sqlite"
    shutil.copy(star_warehouse.db, path)
    conn = connect(path)
    yield conn, star_warehouse.cfg, tmp_path
    conn.close()


def star_tables(conn) -> dict[str, list]:
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'star_%' ORDER BY name")]
    return {n: conn.execute(f"SELECT * FROM {n} ORDER BY 1, 2").fetchall() for n in names}


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the contract ---------------------------------------------------------------------------------------------------------------


def test_every_relationship_is_one_to_many_single_and_keys_exist(star, frames):
    """AC24: keys unique and not null, every foreign key in its dimension, every relationship 1:* with one filter direction and active."""
    _conn, out_dir, _paths = star
    assert verify_star_schema(out_dir, MODEL) == []
    assert len(MODEL.relationships) == 9
    for r in MODEL.relationships:
        label = f"{r.rel_id} {r.dim_table}[{r.dim_column}] -> {r.fact_table}[{r.fact_column}]"
        assert (r.cardinality, r.cross_filter, r.active) == ("one_to_many", "single", True), label
        dimension, fact = frames[r.dim_table], frames[r.fact_table]
        assert dimension[r.dim_column].notna().all() and dimension[r.dim_column].is_unique, label
        assert len(fact) > 0, f"{label}: the fact is empty, so the check would be vacuous"
        assert fact[r.fact_column].notna().all(), label
        assert fact[r.fact_column].isin(set(dimension[r.dim_column])).all(), label


def test_the_tables_are_the_ten_dimensions_and_ten_facts_of_the_plan_with_its_nine_relationships():
    assert {t.name for t in MODEL.tables if t.kind == "dimension"} == DIMENSIONS and {t.name for t in MODEL.tables if t.kind == "fact"} == FACTS
    assert len(MODEL.tables) == 20 and [r.rel_id for r in MODEL.relationships] == [f"R{i:02d}" for i in range(1, 10)]
    assert [(f"{r.dim_table}[{r.dim_column}]", f"{r.fact_table}[{r.fact_column}]") for r in MODEL.relationships] == PLANNED
    assert validate_model(MODEL) == []
    # the tables with no relationship are the ones the plan says feed their own visuals and measures
    related = {r.dim_table for r in MODEL.relationships} | {r.fact_table for r in MODEL.relationships}
    assert {t.name for t in MODEL.tables} - related == {
        "dim_as_of", "dim_model", "fact_method_metrics", "fact_evaluation_summary", "fact_roc", "fact_calibration", "fact_cost_sensitivity", "fact_leakage", "fact_importance",
    }


def test_every_fact_has_the_grain_the_plan_names():
    grains = {
        "fact_order": ("order_id",), "fact_tracking_monthly": ("month_key", "mode_key"), "fact_method_metrics": ("scope", "method", "seal_state"),
        "fact_evaluation_summary": ("criterion_id", "seal_state"), "fact_roc": ("scope", "method", "seal_state", "point"),
        "fact_calibration": ("scope", "method", "seal_state", "bin"), "fact_cost_sensitivity": ("scope", "break_even", "method", "seal_state"),
        "fact_leakage": ("source_column",), "fact_importance": ("feature",), "fact_validation": ("run_key", "rule_id"),
    }
    assert {t.name: t.key for t in MODEL.tables if t.kind == "fact"} == grains
    assert {t.name: t.key for t in MODEL.tables if t.kind == "dimension"} == {
        "dim_date": ("date_key",), "dim_month": ("month_key",), "dim_shipping_mode": ("mode_key",), "dim_geo": ("geo_key",), "dim_product": ("product_key",),
        "dim_segment": ("segment_key",), "dim_payment": ("payment_key",), "dim_model": ("model_version",), "dim_pipeline_run": ("run_key",), "dim_as_of": ("as_of_key",),
    }


def test_the_columns_are_the_ones_of_the_plan_and_where_they_differ_it_is_a_recorded_addition():
    assert MODEL.table("fact_order").column_names == (
        "order_id", "order_date_key", "mode_key", "geo_key", "main_product_key", "segment_key", "payment_key", "order_ts", "order_hour", "order_weekday", "n_lines", "units",
        "gross_sales", "item_total", "discount", "block", "regime", "outcome_status", "outcome_known_date", "real_days", "is_late", "risk", "is_alert", "threshold",
        "scored_as_of_date", "model_version", "run_key",
    )
    assert MODEL.table("fact_tracking_monthly").column_names == (
        "month_key", "mode_key", "orders_scored", "alerts", "closed_orders", "closed_alerts", "alerts_late", "late_closed", "late_missed", "late_expected", "cancelled_orders",
        "alerts_on_cancelled",
    )
    assert MODEL.table("dim_model").column_names == (
        "model_version", "feature_set", "params_es", "calibration", "threshold", "alert_cost", "late_cost", "effect", "break_even", "trained_as_of", "config_sha256", "seal_state",
    )
    assert MODEL.table("dim_date").column_names == (
        "date_key", "date", "year", "month", "month_key", "month_name_es", "weekday", "weekday_es", "block_es", "is_test",
    )
    # the additions to the plan's columns (D60): the seal state and the scope that tell the rows of two analyses apart
    plan = {
        "fact_roc": ("scope", "method", "point", "fpr", "tpr"), "fact_calibration": ("scope", "method", "bin", "p_mean", "observed", "orders"),
        "fact_cost_sensitivity": ("break_even", "method", "threshold", "nb_per_1000", "flagged_share", "seal_state"),
        "fact_method_metrics": ("scope", "method", "label_es", "auc", "pr_auc", "brier", "ece", "threshold", "flagged_share", "precision", "recall", "nb_total", "nb_per_1000", "seal_state"),
        "fact_leakage": ("column_es", "kind", "known_at_es", "auc_with_mode_valid", "note_es"),
    }
    for name, columns in plan.items():
        assert set(columns) <= set(MODEL.table(name).column_names), name
    assert set(MODEL.table("fact_roc").column_names) - set(plan["fact_roc"]) == {"seal_state"}
    assert set(MODEL.table("fact_cost_sensitivity").column_names) - set(plan["fact_cost_sensitivity"]) == {"scope"}
    assert set(MODEL.table("fact_leakage").column_names) - set(plan["fact_leakage"]) == {"source_column"}, "the plan's `column` is `source_column`"


# --- the files ----------------------------------------------------------------------------------------------------------------


def test_every_parquet_has_the_schema_of_the_model_and_the_run_metadata_and_the_attribution(star):
    _conn, out_dir, paths = star
    assert [p.name for p in paths] == [f"{t.name}.parquet" for t in MODEL.tables]
    for table in MODEL.tables:
        file = pq.ParquetFile(out_dir / f"{table.name}.parquet")
        assert file.schema_arrow.equals(arrow_schema(table), check_metadata=False), table.name
        metadata = {k.decode(): v.decode() for k, v in file.schema_arrow.metadata.items()}
        assert {k: metadata[k] for k in METADATA_KEYS} == {
            "latedel.run_key": "run-C2018-02", "latedel.as_of": "2018-02-07", "latedel.source_sha256": "ab" * 32, "latedel.attribution": ATTRIBUTION,
        }
    assert "Constante" in ATTRIBUTION and "CC BY 4.0" in ATTRIBUTION and "10.17632/8gx2fvg2k6.5" in ATTRIBUTION
    assert not [p for p in out_dir.iterdir() if p.suffix != ".parquet"], "no temporary file is left"


def test_only_the_keys_are_non_nullable_and_the_types_are_the_planned_ones(star):
    _conn, out_dir, _paths = star
    for table in MODEL.tables:
        schema = pq.ParquetFile(out_dir / f"{table.name}.parquet").schema_arrow
        for column in table.columns:
            field = schema.field(column.name)
            assert field.type == ARROW_TYPES[column.dtype], f"{table.name}[{column.name}]"
            assert field.nullable == (column.name not in table.key), f"{table.name}[{column.name}]"
    assert MODEL.table("fact_order").column("order_date_key").dtype == "int32" and MODEL.table("fact_order").column("order_id").dtype == "int64"


def test_each_file_is_under_the_cap_and_the_largest_is_the_order_fact(star):
    _conn, out_dir, paths = star
    sizes = {p.name: p.stat().st_size for p in paths}
    assert max(sizes.values()) < MAX_PARQUET_BYTES and max(sizes, key=sizes.get) == "fact_order.parquet", sizes
    assert sum(sizes.values()) < 2 * 1024 * 1024, "the small synthetic fixture stays small"


def test_no_personal_column_and_no_sentinel_reaches_any_file(star):
    _conn, out_dir, _paths = star
    for table in MODEL.tables:
        for column in table.column_names:
            # the outcome columns are the point of the report (fact_order holds what happened); a personal column of the source is never there
            assert _forbidden(column) != "personal", f"{table.name}[{column}] is a personal column of the source"
    blob = b"".join(p.read_bytes() for p in out_dir.iterdir())
    assert all(needle not in blob for needle in SENTINEL_NEEDLES)


# --- the contents against the warehouse, by independent queries ----------------------------------------------------------------


def test_the_order_fact_has_one_row_per_order_with_the_score_and_the_outcome_where_they_exist(star, frames):
    conn, _out_dir, _paths = star
    fact = frames["fact_order"].set_index("order_id")
    orders = {r[0]: r for r in conn.execute("SELECT order_id, order_date, shipping_mode, scheduled_days, order_ts FROM raw_orders")}
    assert len(fact) == len(orders) == conn.execute("SELECT COUNT(*) FROM raw_orders").fetchone()[0] > 2000
    scores = {r[0]: r for r in conn.execute("SELECT order_id, risk, is_alert, threshold, as_of_date, model_version, run_key FROM model_scores")}
    outcomes = {r[0]: r for r in conn.execute("SELECT order_id, is_cancelled, real_days, outcome_known_date FROM raw_outcomes")}
    scored = fact[fact["risk"].notna()]
    assert set(scored.index) == set(scores) and 0 < len(scored) < len(fact), "the launch orders are not scored"
    for order_id in list(scores)[:200]:
        _o, risk, alert, threshold, as_of, version, run = scores[order_id]
        row = fact.loc[order_id]
        assert (row["risk"], bool(row["is_alert"]), row["threshold"], str(row["scored_as_of_date"]), row["model_version"], row["run_key"]) == (risk, bool(alert), threshold, as_of, version, run)
    status = fact["outcome_status"].value_counts().to_dict()
    cancelled = sum(1 for o in outcomes.values() if o[1] == 1)
    assert status.get("cancelled", 0) == cancelled > 0 and status.get("closed", 0) == len(outcomes) - cancelled and status.get("open", 0) == len(fact) - len(outcomes)
    closed = fact[fact["outcome_status"] == "closed"]
    expected_late = pd.Series({i: int(outcomes[i][2] > orders[i][3]) for i in closed.index})
    assert (closed["is_late"].astype(int) == expected_late).all() and closed["real_days"].notna().all()
    assert fact[fact["outcome_status"] != "closed"]["is_late"].isna().all() and fact[fact["outcome_status"] != "closed"]["real_days"].isna().all()
    assert (fact["order_date_key"] == pd.Series({i: int(orders[i][1].replace("-", "")) for i in fact.index})).all()
    # the block of every order is the block of its date, and the orders of the monthly deliveries are the ones with scores
    assert set(fact["block"]) <= {"train", "purge_1", "valid_v1", "valid_v2", "purge_2", "test", "post_break"}
    assert set(fact.loc[fact["risk"].notna(), "block"]) <= {"test", "post_break"} and (fact["regime"] == "post_break").sum() == (fact["block"] == "post_break").sum()
    # the main product is the line with the most sales (the lowest line on a tie)
    lines = pd.DataFrame(conn.execute("SELECT line_id, order_id, product_card_id, sales FROM raw_order_lines").fetchall(), columns=["line_id", "order_id", "product", "sales"])
    main = lines.sort_values(["order_id", "sales", "line_id"], ascending=[True, False, True]).groupby("order_id").head(1).set_index("order_id")["product"]
    assert (fact["main_product_key"] == main.reindex(fact.index)).all()
    totals = lines.groupby("order_id").size()
    assert (fact["n_lines"] == totals.reindex(fact.index)).all()
    hour = pd.Series({i: int(orders[i][4][11:13]) for i in fact.index})
    assert (fact["order_hour"] == hour).all()


def test_the_dimensions_cover_exactly_what_the_warehouse_has(star, frames):
    conn, _out_dir, _paths = star
    cfg = None
    assert set(frames["dim_shipping_mode"]["mode_key"]) == {r[0] for r in conn.execute("SELECT DISTINCT shipping_mode FROM raw_orders")}
    modes = frames["dim_shipping_mode"].set_index("mode_key")
    rates = dict(conn.execute("SELECT cell, rate FROM baseline_rates WHERE baseline = 'b2_mode'").fetchall())
    assert {k: v for k, v in modes["train_late_rate"].items()} == rates and modes.loc["Same Day", "scheduled_days"] == 0
    assert list(modes.sort_values("sort_order").index)[0] == "Same Day" and modes["mode_es"].notna().all()
    geo = frames["dim_geo"]
    assert len(geo) == conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT market, order_region, order_country FROM raw_orders)").fetchone()[0]
    assert sorted(geo["geo_key"]) == list(range(1, len(geo) + 1)) and not geo.duplicated(["market", "order_region", "order_country"]).any()
    assert set(frames["dim_product"]["product_key"]) == {r[0] for r in conn.execute("SELECT DISTINCT product_card_id FROM raw_order_lines")}
    assert (frames["dim_product"]["product_name"] == frames["dim_product"]["product_name"].str.strip()).all()
    assert any("<script>" in name for name in frames["dim_product"]["product_name"]), "a name with markup is kept as text for the HTML to escape (AC25)"
    assert set(frames["dim_segment"]["segment_key"]) == {r[0] for r in conn.execute("SELECT DISTINCT customer_segment FROM raw_orders")}
    assert set(frames["dim_payment"]["payment_key"]) == {r[0] for r in conn.execute("SELECT DISTINCT payment_type FROM raw_orders")}
    del cfg
    # the calendar runs from the first order to the close, one row a day, and the test days are the test block
    calendar = frames["dim_date"]
    assert calendar["date"].iloc[0] == date(2015, 1, 1) and calendar["date"].iloc[-1] == date(2018, 2, 7) and len(calendar) == (date(2018, 2, 7) - date(2015, 1, 1)).days + 1
    assert calendar["date_key"].is_unique and calendar.loc[calendar["is_test"], "date"].min() == date(2017, 1, 8) and calendar.loc[calendar["is_test"], "date"].max() == date(2017, 9, 30)
    assert calendar.set_index("date").loc[date(2017, 1, 1), "block_es"] == "Purga 2" and calendar.set_index("date").loc[date(2016, 8, 1), "block_es"] == "Validación V1"
    assert calendar.set_index("date").loc[date(2017, 1, 2), "weekday_es"] == "lunes" and calendar.set_index("date").loc[date(2017, 1, 2), "weekday"] == 0
    months = frames["dim_month"]
    assert list(months["year_month_label"].iloc[[0, -1]]) == ["2015-01", "2018-02"] and len(months) == 38 and set(months.loc[months["month_key"] >= 201710, "regime"]) == {"post_break"}
    as_of = frames["dim_as_of"]
    assert len(as_of) == 15 and list(as_of["as_of_label"].iloc[[0, -1]]) == ["2017-01-07 (L2017-01-07)", "2018-02-07 (C2018-02)"]


def test_the_model_dimension_says_the_costs_and_that_the_configuration_is_the_sealed_one(star, frames):
    conn, _out_dir, _paths = star
    model = frames["dim_model"]
    assert len(model) == 1 and model["model_version"].iloc[0] == "v1" and model["seal_state"].iloc[0] == "sealed"
    row = model.iloc[0]
    assert (row["alert_cost"], row["late_cost"], row["effect"], row["break_even"]) == (5.0, 20.0, 0.5, 0.5)
    assert row["threshold"] == conn.execute("SELECT threshold FROM model_registry").fetchone()[0] and row["params_es"].startswith("tasa de aprendizaje")
    assert row["config_sha256"] == conn.execute("SELECT config_sha256 FROM model_registry").fetchone()[0]


def test_the_follow_up_the_evaluation_and_the_validation_come_from_their_tables(star, frames):
    conn, _out_dir, _paths = star
    last = conn.execute("SELECT MAX(as_of_date) FROM tracking_monthly").fetchone()[0]
    tracking = frames["fact_tracking_monthly"]
    assert last == "2018-02-07" and int(tracking["orders_scored"].sum()) == conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0]
    assert int(tracking["alerts"].sum()) == conn.execute("SELECT SUM(is_alert) FROM model_scores").fetchone()[0]
    assert len(tracking) == conn.execute("SELECT COUNT(*) FROM tracking_monthly WHERE as_of_date = ?", (last,)).fetchone()[0]
    metrics = frames["fact_method_metrics"]
    assert set(metrics["scope"]) == {"valid_v2", "test", "post_break"} and set(metrics["seal_state"]) == {"sealed"} and metrics["label_es"].notna().all()
    stored = conn.execute("SELECT value FROM eval_metrics WHERE scope = 'test' AND method = 'model' AND metric = 'auc'").fetchone()[0]
    assert metrics[(metrics["scope"] == "test") & (metrics["method"] == "model")]["auc"].iloc[0] == stored
    summary = frames["fact_evaluation_summary"]
    assert list(summary["criterion_id"]) == [f"C{i:02d}" for i in range(1, 11)] and pd.isna(summary.loc[summary["criterion_id"] == "C10", "passed"].iloc[0])
    assert summary["passed"].dropna().map(type).eq(bool).all() or summary["passed"].dropna().isin([True, False]).all()
    assert len(frames["fact_cost_sensitivity"]) == 17 * 3 * 2 and set(frames["fact_cost_sensitivity"]["scope"]) == {"valid_v2", "test"}
    assert len(frames["fact_leakage"]) == 8 and len(frames["fact_importance"]) > 1
    validation = frames["fact_validation"]
    assert set(validation["status"]) == {"pasa", "falla", "advierte"} and len(validation) == conn.execute("SELECT COUNT(*) FROM validation_results").fetchone()[0]
    assert validation.set_index(["run_key", "rule_id"]).loc[("run-rejected", "V24"), "status"] == "falla"
    assert validation.set_index(["run_key", "rule_id"]).loc[("run-L2017-01-07", "V11"), "status"] == "advierte"
    runs = frames["dim_pipeline_run"].set_index("run_key")
    assert runs.loc["run-C2018-02", "status"] == "running" and runs.loc["run-M2017-03", "as_of_key"] == 20170331 and pd.isna(runs.loc["run-rejected", "as_of_key"])


# --- the controls, the rewrite of the run table and the refusals ----------------------------------------------------------------


def add_order(conn, order_id: int, day: str) -> None:
    """A valid order with one line and no outcome, in the warehouse as a delivery would have left it."""
    conn.execute("INSERT INTO raw_orders VALUES (?, 'M2018-01', ?, ?, 'post_break', 'Standard Class', 4, 'DEBIT', 'M', 'R', 'C', 'Consumer', 'C')", (order_id, f"{day}T09:00", day))
    conn.execute("INSERT INTO raw_order_lines VALUES (?, ?, 'M2018-01', 'D', 'C', 1, 'P', 1, 10.0, 10.0, 0.0, 0.0, 10.0)", (order_id * 10, order_id))


def test_a_control_that_finds_rows_stops_the_build_and_the_marts_keep_what_they_had(private):
    conn, cfg, _tmp = private
    build_powerbi_marts(conn, cfg)
    before = star_tables(conn)
    assert before["star_fact_order"] and before["star_dim_model"]
    add_order(conn, 900001, "2018-01-15")                        # an alert that is not "risk at or above the threshold" (a new score is allowed by the triggers)
    conn.execute("INSERT INTO model_scores VALUES (900001, '2018-02-07', 'x', 'v1', 0.2, 0.5, 1)")
    with pytest.raises(CheckFailedError, match="chk_alert_matches_threshold") as caught:
        build_powerbi_marts(conn, cfg)
    assert "chk_no_score_before_test_start" not in str(caught.value), "only the control of this defect fails"
    assert not conn.in_transaction and star_tables(conn) == before, "the marts are as they were"


def test_each_control_of_the_model_catches_its_own_defect(private):
    conn, cfg, _tmp = private

    def remove_scores(*order_ids: int) -> None:
        conn.execute("DROP TRIGGER IF EXISTS model_scores_no_delete")          # every build creates the schema again, triggers included
        conn.execute(f"DELETE FROM model_scores WHERE order_id IN ({', '.join(str(i) for i in order_ids)})")

    def fails(check: str) -> str:
        with pytest.raises(CheckFailedError) as caught:
            build_powerbi_marts(conn, cfg)
        assert check in str(caught.value), (check, str(caught.value)[:300])
        return str(caught.value)

    add_order(conn, 900002, "2016-05-05")                        # a score of an order created before the test starts
    conn.execute("INSERT INTO model_scores VALUES (900002, '2018-02-07', 'x', 'v1', 0.2, 0.5, 0)")
    assert "chk_alert_matches_threshold" not in fails("chk_no_score_before_test_start")
    remove_scores(900002)
    conn.execute("DELETE FROM raw_orders WHERE order_id = 900002")
    conn.execute("DELETE FROM raw_order_lines WHERE order_id = 900002")

    conn.execute("INSERT INTO model_scores VALUES (999999, '2018-02-07', 'x', 'v1', 0.2, 0.5, 0)")                 # a score of an order that is not in the warehouse
    fails("chk_one_score_per_order")
    remove_scores(999999)

    conn.execute("PRAGMA ignore_check_constraints = ON")                                                                # the table refuses it; the control is the second line
    add_order(conn, 900003, "2018-01-16")
    conn.execute("INSERT INTO model_scores VALUES (900003, '2018-02-07', 'x', 'v1', 1.5, 0.5, 1)")
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    fails("chk_scores_range")
    remove_scores(900003)

    conn.execute("UPDATE tracking_monthly SET orders_scored = orders_scored + 1 WHERE rowid = (SELECT MIN(rowid) FROM tracking_monthly WHERE as_of_date = '2018-02-07')")
    fails("chk_tracking_totals")
    conn.execute("UPDATE tracking_monthly SET orders_scored = orders_scored - 1 WHERE rowid = (SELECT MIN(rowid) FROM tracking_monthly WHERE as_of_date = '2018-02-07')")

    conn.execute("INSERT INTO validation_results VALUES ('run-ghost', 'V20', 'x', 'bloqueante', 'delivery', 1, 0, 0, '[]')")
    fails("chk_star_foreign_keys")
    conn.execute("DELETE FROM validation_results WHERE run_key = 'run-ghost'")

    add_order(conn, 900004, "2018-01-17")                        # an order with no lines does not reach the fact, and the fact must have one row per order
    conn.execute("DELETE FROM raw_order_lines WHERE order_id = 900004")
    fails("chk_fact_order_unique")
    conn.execute("DELETE FROM raw_orders WHERE order_id IN (900001, 900002, 900003, 900004)")
    conn.execute("DELETE FROM raw_order_lines WHERE order_id IN (900001, 900002, 900003, 900004)")

    build_powerbi_marts(conn, cfg)                               # with every defect removed the same warehouse builds
    assert conn.execute("SELECT COUNT(*) FROM star_fact_order").fetchone()[0] == conn.execute("SELECT COUNT(*) FROM raw_orders").fetchone()[0]
    # the controls of the keys and of the scores of the fact read the built tables: damage them and ask the controls directly
    conn.execute("INSERT INTO star_dim_segment SELECT * FROM star_dim_segment LIMIT 1")
    conn.execute("UPDATE star_fact_order SET risk = 0.123 WHERE order_id = (SELECT MIN(order_id) FROM star_fact_order WHERE risk IS NOT NULL)")
    results = {c.check_id: c.violations for c in run_checks(conn, "model", {"test_start": "2017-01-08"})}
    assert results["chk_star_keys_unique"] == 1 and results["chk_fact_order_scores"] == 1
    assert sum(results.values()) == 2, results


def test_a_loaded_order_whose_outcome_has_not_arrived_is_open_in_the_fact(private):
    conn, cfg, _tmp = private
    arrived = conn.execute("SELECT COUNT(*) FROM raw_outcomes WHERE outcome_known_date > '2018-01-31'").fetchone()[0]
    assert arrived > 0
    conn.execute("DELETE FROM raw_outcomes WHERE outcome_known_date > '2018-01-31'")
    build_powerbi_marts(conn, cfg)
    rows = conn.execute("SELECT outcome_status, COUNT(*), COUNT(outcome_known_date), COUNT(real_days), COUNT(is_late) FROM star_fact_order GROUP BY outcome_status").fetchall()
    open_row = next(r for r in rows if r[0] == "open")
    assert open_row == ("open", arrived, 0, 0, 0), "an open order has no known date, no real days and no label, and is not a cancellation"


def test_the_run_table_is_rewritten_alone_and_shows_the_status_sqlite_has(star_warehouse, tmp_path):
    path = tmp_path / "wh.sqlite"
    shutil.copy(star_warehouse.db, path)
    conn = connect(path)
    out = tmp_path / "powerbi"
    export_star_schema(conn, out, MODEL, META, star_warehouse.cfg)
    before = {p.name: digest(p) for p in out.iterdir()}
    assert pq.read_table(out / "dim_pipeline_run.parquet").to_pandas().set_index("run_key").loc["run-C2018-02", "status"] == "running"
    record_run(conn, {"run_key": "run-C2018-02", "status": "ok", "finished_at": "2018-02-07T08:05:00"})
    rewritten = export_run_table(conn, out, MODEL, META)
    after = {p.name: digest(p) for p in out.iterdir()}
    assert rewritten.name == "dim_pipeline_run.parquet" and {k for k in before if before[k] != after[k]} == {"dim_pipeline_run.parquet"}
    runs = pq.read_table(out / "dim_pipeline_run.parquet").to_pandas().set_index("run_key")
    assert runs.loc["run-C2018-02", "status"] == "ok" and runs.loc["run-C2018-02", "finished_at"] == "2018-02-07T08:05:00"
    assert verify_star_schema(out, MODEL) == []
    conn.close()


def test_a_file_over_the_cap_stops_the_export_before_anything_is_written(private, monkeypatch):
    conn, cfg, tmp = private
    monkeypatch.setattr(export, "MAX_PARQUET_BYTES", 2000)
    with pytest.raises(ValueError, match="pasan del tope"):
        export_star_schema(conn, tmp / "powerbi", MODEL, META, cfg)
    assert not (tmp / "powerbi").exists()


def test_an_incoherent_model_is_refused_before_the_warehouse_is_touched(private):
    conn, cfg, tmp = private
    broken = SemanticModel(MODEL.tables, (*MODEL.relationships[:-1], replace(MODEL.relationships[-1], cardinality="one_to_one")))
    with pytest.raises(ValueError, match="incoherente"):
        export_star_schema(conn, tmp / "powerbi", broken, META, cfg)
    assert not star_tables(conn) and not (tmp / "powerbi").exists()


def test_without_a_loaded_delivery_there_is_nothing_to_export(cfg, tmp_path):
    conn = connect(tmp_path / "empty.sqlite")
    with pytest.raises(RuntimeError, match="entrega cargada"):
        build_powerbi_marts(conn, cfg)
    conn.close()


def test_the_verification_names_every_way_the_files_break_the_contract(star, tmp_path):
    _conn, out_dir, _paths = star
    copy = tmp_path / "powerbi"
    shutil.copytree(out_dir, copy)
    (copy / "dim_segment.parquet").unlink()
    frame = pq.read_table(copy / "fact_order.parquet").to_pandas()
    frame.loc[frame.index[0], "geo_key"] = 999_999
    import pyarrow as pa

    pq.write_table(pa.Table.from_pandas(frame, schema=arrow_schema(MODEL.table("fact_order")), preserve_index=False).replace_schema_metadata({}), copy / "fact_order.parquet")
    problems = verify_star_schema(copy, MODEL)
    assert any("dim_segment: dim_segment.parquet does not exist" in p for p in problems)
    assert any("R04" in p and "1 values not in dim_geo" in p for p in problems) and any("fact_order: metadata" in p for p in problems)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (dict(cardinality="one_to_one"), "one to many"), (dict(cross_filter="both"), "single filter direction"), (dict(active=False), "active"),
        (dict(dim_column="nope"), "column does not exist"), (dict(fact_table="dim_date"), "many side a fact"), (dict(dim_table="nope"), "table does not exist"),
        (dict(fact_column="order_ts"), "different types"), (dict(dim_table="dim_geo", dim_column="market"), "unique key"),
    ],
)
def test_the_model_check_refuses_a_relationship_that_is_not_one_to_many_single_and_active(change, message):
    first = replace(MODEL.relationships[0], **change)
    problems = validate_model(SemanticModel(MODEL.tables, (first, *MODEL.relationships[1:])))
    assert any(message in p for p in problems), problems
    assert validate_model(MODEL) == []
    assert isinstance(Relationship("R99", "a", "b", "c", "d").cardinality, str)


def test_a_warehouse_with_only_the_launch_exports_its_orders_and_empty_facts_of_the_evaluation(launch_conn, cfg, tmp_path):
    """Before the first model there is nothing scored or evaluated: the files exist with their schema, the order fact has every order and the others are empty."""
    paths = export_star_schema(launch_conn, tmp_path / "powerbi", MODEL, META, cfg)
    assert len(paths) == 20 and verify_star_schema(tmp_path / "powerbi", MODEL) == []
    rows = {p.stem: pq.ParquetFile(p).metadata.num_rows for p in paths}
    assert rows["fact_order"] == launch_conn.execute("SELECT COUNT(*) FROM raw_orders").fetchone()[0] > 1000
    for empty in ("dim_model", "fact_tracking_monthly", "fact_method_metrics", "fact_evaluation_summary", "fact_roc", "fact_calibration", "fact_cost_sensitivity", "fact_leakage", "fact_importance", "fact_validation", "dim_pipeline_run"):
        assert rows[empty] == 0, empty
    fact = pq.read_table(tmp_path / "powerbi" / "fact_order.parquet").to_pandas()
    assert fact["risk"].isna().all() and fact["scored_as_of_date"].isna().all() and set(fact["outcome_status"]) == {"open", "closed", "cancelled"}, "the last days before the launch still have open orders"
    modes = pq.read_table(tmp_path / "powerbi" / "dim_shipping_mode.parquet").to_pandas()
    assert modes["train_late_rate"].isna().all(), "no model is registered, so there are no training rates"
