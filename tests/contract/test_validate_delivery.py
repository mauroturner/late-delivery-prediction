"""Validation II (U07): the rules of the delivery, the interface of the model rules and the informative counts (RF07, RF08, AC10, AC09).

A delivery is validated against the plan, against what the warehouse already holds and against its own manifest entry. The warehouse is only read. The tests
use a minimal warehouse (the part of the schema that the rules read) because the runner comes later (U08); the integration tests of the load repeat the
key cases on the real schema. The truth is computed in the tests from the frames, independently of the rules.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from latedel.deliveries import DeliveryFrames, delivery_plan, publish, read_delivery
from latedel.validate import (
    DELIVERY_RULES,
    MODEL_RULES,
    RULES,
    Severity,
    ValidationError,
    combine_reports,
    make_result,
    read_report,
    validate_delivery,
    validate_published_delivery,
    validate_source,
    with_results,
    write_report,
)
from tests.fixtures.helpers import file_digest, mark_loaded, minimal_warehouse

DELIVERY_BLOCKING = ["V20", "V21", "V22", "V23", "V24"]
INFORMATIVE = ["I01", "I03", "I05"]
MODEL_IDS = ["M01", "M02", "M03", "M04", "M05", "I02", "I04"]


def by_id(report):
    return {r.rule_id: r for r in report.results}


def frames_of(delivered, index: int) -> DeliveryFrames:
    folder, _plan, entries = delivered
    return read_delivery(folder, entries[index])


def warehouse_through(delivered, upto: int, path: Path | str = ":memory:"):
    """A minimal warehouse with the deliveries 0..upto-1 loaded."""
    conn = minimal_warehouse(path)
    folder, plan, entries = delivered
    for index in range(upto):
        mark_loaded(conn, plan[index], read_delivery(folder, entries[index]))
    return conn


def validate(cfg, delivered, index: int, conn, frames: DeliveryFrames | None = None, run_key: str = "r1"):
    folder, plan, entries = delivered
    frames = frames if frames is not None else read_delivery(folder, entries[index])
    return validate_delivery(frames, plan[index], conn, cfg, entry=entries[index], folder=folder / plan[index].delivery_id, run_key=run_key)


def rows_and_orders(frames: DeliveryFrames) -> tuple[int, int]:
    ids = set(frames.orders["order_id"]) | set(frames.order_lines["order_id"]) | set(frames.outcomes["order_id"])
    return len(frames.orders) + len(frames.order_lines) + len(frames.outcomes), len(ids)


# --- the table of rules --------------------------------------------------------------------------------------------------------


def test_the_delivery_and_model_rules_have_the_ids_severities_and_scope_of_the_plan():
    ids = list(RULES)
    assert ids[18:] == DELIVERY_BLOCKING + ["M01", "M02", "M03", "M04", "M05", "I01", "I02", "I03", "I04", "I05"]
    assert all(RULES[r].severity is Severity.BLOCKING and RULES[r].scope == "delivery" for r in DELIVERY_BLOCKING)
    assert all(RULES[r].severity is Severity.BLOCKING and RULES[r].scope == "model" for r in ("M01", "M02", "M03", "M04"))
    assert (RULES["M05"].severity, RULES["M05"].scope) == (Severity.WARNING, "model")
    assert [(RULES[r].severity, RULES[r].scope) for r in ("I01", "I02", "I03", "I04", "I05")] == [
        (Severity.INFO, "delivery"), (Severity.INFO, "model"), (Severity.INFO, "delivery"), (Severity.INFO, "model"), (Severity.INFO, "delivery")]
    assert tuple(DELIVERY_RULES) == tuple(DELIVERY_BLOCKING + INFORMATIVE) and tuple(MODEL_RULES) == tuple(MODEL_IDS)
    assert all(RULES[r].name_es.strip() for r in ids)


# --- the next delivery of the plan is valid (AC09, AC10) ----------------------------------------------------------------------------


def test_out_of_order_delivery_and_reloaded_order_are_blocking(cfg, delivered):
    folder, plan, entries = delivered
    conn = warehouse_through(delivered, 1)                                            # only the launch is loaded
    skipped = validate(cfg, delivered, 2, conn)                                       # M2017-02 without M2017-01
    v20 = by_id(skipped)["V20"]
    assert not v20.passed and v20.severity is Severity.BLOCKING and skipped.blocking_failed
    assert v20.examples[0]["reason"] == "previous_not_loaded" and v20.examples[0]["missing"] == ["M2017-01"]
    assert (v20.rows_affected, v20.orders_affected) == rows_and_orders(frames_of(delivered, 2))

    conn = warehouse_through(delivered, 2)                                            # the launch and M2017-01 are loaded
    again = validate(cfg, delivered, 1, conn)                                         # M2017-01 a second time
    found = by_id(again)
    frames = frames_of(delivered, 1)
    assert not found["V22"].passed and again.blocking_failed
    assert (found["V22"].rows_affected, found["V22"].orders_affected) == rows_and_orders(frames), "every order, line and outcome of it is loaded"
    assert {example["table"] for example in found["V22"].examples} <= {"orders", "order_lines", "outcomes"}
    assert all(example["reason"] == "loaded" for example in found["V22"].examples)
    assert found["V20"].examples[0]["reason"] == "already_loaded"
    assert [r.passed for r in again.results if r.rule_id in ("V21", "V23", "V24")] == [True, True, True], "the rest of the delivery is fine"


@pytest.mark.parametrize("index", [0, 1, 5, 13, 14])
def test_the_next_delivery_passes_every_blocking_rule(cfg, delivered, index):
    conn = warehouse_through(delivered, index)
    report = validate(cfg, delivered, index, conn)
    assert not report.blocking_failed and [r.rule_id for r in report.results] == DELIVERY_BLOCKING + INFORMATIVE
    assert all(r.passed and (r.rows_affected, r.orders_affected) == (0, 0) for r in report.results if r.rule_id in DELIVERY_BLOCKING)
    assert report.delivery_id == delivered[1][index].delivery_id and report.run_key == "r1" and report.source_sha256 == ""
    entry = delivered[2][index]
    assert report.rows_delivery == entry.orders + entry.lines + entry.outcomes
    assert report.not_evaluated == tuple(MODEL_IDS), "the rules of the model are evaluated by the steps of the model"


def test_the_launch_is_valid_against_a_warehouse_without_tables(cfg, delivered):
    import sqlite3

    conn = sqlite3.connect(":memory:", isolation_level=None)
    assert not validate(cfg, delivered, 0, conn).blocking_failed
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0, "validation creates nothing"


# --- V20: the delivery is the next one of the plan -------------------------------------------------------------------------------------


def test_v20_a_delivery_that_is_not_in_the_plan_or_has_other_periods_is_refused(cfg, delivered):
    folder, plan, entries = delivered
    conn = warehouse_through(delivered, 1)
    frames = frames_of(delivered, 1)
    stranger = dataclasses.replace(plan[1], delivery_id="M2099-01")
    report = validate_delivery(frames, stranger, conn, cfg, entry=entries[1], folder=folder / "M2017-01", run_key="r")
    assert by_id(report)["V20"].examples[0]["reason"] == "not_in_plan"
    shifted = dataclasses.replace(plan[1], outcomes_to=plan[1].outcomes_to + timedelta(days=1), as_of=plan[1].as_of + timedelta(days=1))
    report = validate_delivery(frames, shifted, conn, cfg, entry=entries[1], folder=folder / "M2017-01", run_key="r")
    example = by_id(report)["V20"].examples[0]
    assert example["reason"] == "other_period" and example["period"] != example["expected_period"]


def test_v20_the_period_must_start_the_day_after_the_one_the_warehouse_loaded(cfg, delivered):
    conn = warehouse_through(delivered, 1)
    conn.execute("UPDATE deliveries SET outcomes_to = '2017-01-09' WHERE delivery_id = 'L2017-01-07'")   # the warehouse says the launch ended later
    example = by_id(validate(cfg, delivered, 1, conn))["V20"].examples[0]
    assert example["reason"] == "not_contiguous" and example["previous"] == "L2017-01-07" and example["outcomes_to"] == "2017-01-09"


def test_v20_a_delivery_that_was_rejected_before_is_not_loaded_and_blocks_the_next(cfg, delivered):
    folder, plan, entries = delivered
    conn = minimal_warehouse()
    mark_loaded(conn, plan[0], read_delivery(folder, entries[0]), status="rejected")
    report = validate(cfg, delivered, 1, conn)
    assert by_id(report)["V20"].examples[0] == {"delivery_id": "M2017-01", "reason": "previous_not_loaded", "missing": ["L2017-01-07"]}


# --- V21: every date is inside the period of its stream ---------------------------------------------------------------------------------


def test_v21_an_order_or_an_outcome_dated_outside_its_period_is_refused(cfg, delivered):
    frames = frames_of(delivered, 1)
    conn = warehouse_through(delivered, 1)
    order = frames.order_lines.groupby("order_id").size().index[0]
    other = frames.outcomes[frames.outcomes["order_id"] != order]["order_id"].iloc[0]
    orders = frames.orders.copy()
    orders.loc[orders["order_id"] == order, "order_date"] = pd.Timestamp("2016-12-31")
    outcomes = frames.outcomes.copy()
    outcomes.loc[outcomes["order_id"] == other, "outcome_known_date"] = pd.Timestamp("2017-02-03")
    bad = dataclasses.replace(frames, orders=orders, outcomes=outcomes)
    report = validate(cfg, delivered, 1, conn, bad)
    v21 = by_id(report)["V21"]
    n_lines = int((frames.order_lines["order_id"] == order).sum())
    assert not v21.passed and report.blocking_failed
    assert (v21.rows_affected, v21.orders_affected) == (1 + n_lines + 1, 2)
    kinds = {(e["table"], e["order_id"]) for e in v21.examples}
    assert kinds == {("orders", int(order)), ("outcomes", int(other))}
    dates = {e["order_id"]: e.get("order_date") or e.get("outcome_known_date") for e in v21.examples}
    assert dates == {int(order): "2016-12-31", int(other): "2017-02-03"}


def test_v21_the_close_brings_no_orders_and_an_order_in_it_is_outside_its_period(cfg, delivered):
    conn = warehouse_through(delivered, 14)
    close = frames_of(delivered, 14)
    assert len(close.orders) == 0 and len(close.outcomes) > 0
    assert by_id(validate(cfg, delivered, 14, conn))["V21"].passed
    one = frames_of(delivered, 1)
    smuggled = dataclasses.replace(close, orders=one.orders.head(1))
    v21 = by_id(validate(cfg, delivered, 14, conn, smuggled))["V21"]
    assert not v21.passed and v21.orders_affected == 1 and v21.rows_affected >= 1


# --- V22: nothing is loaded twice or repeated ---------------------------------------------------------------------------------------------


def test_v22_repeated_ids_inside_the_delivery_are_refused_once_each(cfg, delivered):
    conn = warehouse_through(delivered, 1)
    frames = frames_of(delivered, 1)
    bad = dataclasses.replace(
        frames, orders=pd.concat([frames.orders, frames.orders.head(2)], ignore_index=True),
        order_lines=pd.concat([frames.order_lines, frames.order_lines.head(3)], ignore_index=True),
        outcomes=pd.concat([frames.outcomes, frames.outcomes.head(1)], ignore_index=True),
    )
    v22 = by_id(validate(cfg, delivered, 1, conn, bad))["V22"]
    ids = set(frames.orders["order_id"].head(2)) | set(frames.order_lines["order_id"].head(3)) | set(frames.outcomes["order_id"].head(1))
    assert not v22.passed and (v22.rows_affected, v22.orders_affected) == (2 + 3 + 1, len(ids))
    assert {e["reason"] for e in v22.examples} == {"repeated"} and {e["table"] for e in v22.examples} == {"orders", "order_lines", "outcomes"}
    assert {k for e in v22.examples for k in e} <= {"table", "reason", "order_id", "line_id"}


def test_v22_a_single_loaded_line_or_outcome_is_enough(cfg, delivered):
    conn = warehouse_through(delivered, 1)
    frames = frames_of(delivered, 1)
    line, outcome = int(frames.order_lines["line_id"].iloc[0]), int(frames.outcomes["order_id"].iloc[0])
    conn.execute("INSERT INTO raw_order_lines VALUES (?, ?)", (line, int(frames.order_lines["order_id"].iloc[0])))
    conn.execute("INSERT INTO raw_outcomes VALUES (?)", (outcome,))
    v22 = by_id(validate(cfg, delivered, 1, conn))["V22"]
    assert not v22.passed and v22.rows_affected == 2
    assert {(e["table"], e["reason"]) for e in v22.examples} == {("order_lines", "loaded"), ("outcomes", "loaded")}


# --- V23: lines and outcomes belong to an order ----------------------------------------------------------------------------------------------


def test_v23_lines_and_outcomes_of_an_unknown_order_are_refused_and_those_of_a_loaded_order_are_not(cfg, delivered):
    folder, plan, entries = delivered
    conn = warehouse_through(delivered, 1)
    frames = frames_of(delivered, 1)
    from_loaded = frames.outcomes[~frames.outcomes["order_id"].isin(set(frames.orders["order_id"]))]
    assert len(from_loaded) > 0, "an outcome can arrive in the month after its order"
    assert by_id(validate(cfg, delivered, 1, conn, frames))["V23"].passed

    order = int(frames.orders.sort_values("order_date")["order_id"].iloc[2])
    bad = dataclasses.replace(frames, orders=frames.orders[frames.orders["order_id"] != order])
    expected_rows = int((frames.order_lines["order_id"] == order).sum()) + int((frames.outcomes["order_id"] == order).sum())
    v23 = by_id(validate(cfg, delivered, 1, conn, bad))["V23"]
    assert not v23.passed and (v23.rows_affected, v23.orders_affected) == (expected_rows, 1)
    assert {e["table"] for e in v23.examples} <= {"order_lines", "outcomes"} and all(e["order_id"] == order for e in v23.examples)
    assert folder and plan and entries


# --- V24: not empty, and the rows and the SHA-256 are those of the manifest -------------------------------------------------------------------------


def test_v24_an_empty_delivery_is_refused(cfg, delivered):
    conn = warehouse_through(delivered, 14)
    close = frames_of(delivered, 14)
    empty = DeliveryFrames(close.orders.iloc[0:0], close.order_lines.iloc[0:0], close.outcomes.iloc[0:0])
    v24 = by_id(validate(cfg, delivered, 14, conn, empty))["V24"]
    assert not v24.passed and "empty" in v24.examples[0]["reasons"] and v24.examples[0]["rows"] == 0


def test_v24_files_that_are_not_the_ones_in_the_manifest_are_refused_with_the_reason(cfg, delivered, tmp_path):
    folder, plan, entries = delivered
    copy = tmp_path / "deliveries"
    shutil.copytree(folder, copy)
    shutil.copy(copy / "L2017-01-07" / "orders.parquet", copy / "M2017-01" / "orders.parquet")   # the launch's orders in the place of January's
    conn = warehouse_through(delivered, 1)
    report = validate_published_delivery(copy, entries[1], plan[1], conn, cfg, run_key="r")
    v24 = by_id(report)["V24"]
    assert not v24.passed and report.blocking_failed and v24.severity is Severity.BLOCKING
    assert set(v24.examples[0]["reasons"]) == {"rows_differ_from_manifest", "sha256_differs_from_manifest"}
    assert v24.examples[0]["manifest_rows"] == entries[1].orders + entries[1].lines + entries[1].outcomes
    assert v24.examples[0]["sha256"] != v24.examples[0]["manifest_sha256"]
    assert [r.rule_id for r in report.results] == DELIVERY_BLOCKING + INFORMATIVE, "every rule is still evaluated on what is there"


def test_v24_a_missing_or_unreadable_file_leaves_the_other_rules_not_evaluated(cfg, delivered, tmp_path):
    folder, plan, entries = delivered
    conn = warehouse_through(delivered, 1)
    for damage in ("missing", "garbage"):
        copy = tmp_path / damage
        shutil.copytree(folder, copy)
        target = copy / "M2017-01" / "outcomes.parquet"
        if damage == "missing":
            target.unlink()
        else:
            target.write_bytes(b"this is not parquet")
        report = validate_published_delivery(copy, entries[1], plan[1], conn, cfg, run_key="r")
        assert [(r.rule_id, r.passed) for r in report.results] == [("V24", False)] and report.blocking_failed
        assert report.results[0].examples[0]["reasons"] == ["unreadable_files"] and "message" in report.results[0].examples[0]
        assert set(report.not_evaluated) == (set(DELIVERY_BLOCKING) - {"V24"}) | set(INFORMATIVE) | set(MODEL_IDS)


def test_a_published_delivery_that_is_as_published_is_validated_like_a_read_delivery(cfg, delivered):
    folder, plan, entries = delivered
    conn = warehouse_through(delivered, 1)
    direct = validate(cfg, delivered, 1, conn)
    published = validate_published_delivery(folder, entries[1], plan[1], conn, cfg, run_key="r1")
    assert published == direct


# --- the informative counts: I01, I03, I05 ----------------------------------------------------------------------------------------------------------


def test_i01_i03_and_i05_count_new_orders_outcomes_that_arrived_and_the_post_break_orders(cfg, delivered):
    for index in (1, 10, 13):
        frames = frames_of(delivered, index)
        conn = warehouse_through(delivered, index)
        found = by_id(validate(cfg, delivered, index, conn))
        post = frames.orders[frames.orders["regime"] == "post_break"]
        assert (found["I01"].rows_affected, found["I01"].orders_affected) == (len(frames.order_lines), len(frames.orders))
        assert (found["I03"].rows_affected, found["I03"].orders_affected) == (len(frames.outcomes), len(frames.outcomes))
        assert (found["I05"].rows_affected, found["I05"].orders_affected) == (int(frames.order_lines["order_id"].isin(set(post["order_id"])).sum()), len(post))
        assert all(found[r].passed and found[r].severity is Severity.INFO for r in INFORMATIVE)
    last = by_id(validate(cfg, delivered, 13, warehouse_through(delivered, 13)))
    assert last["I05"].orders_affected == len(frames_of(delivered, 13).orders) > 0, "every order of January 2018 is after the change of shape"
    assert by_id(validate(cfg, delivered, 1, warehouse_through(delivered, 1)))["I05"].orders_affected == 0


def test_i01_counts_only_the_orders_that_are_not_loaded_yet_and_the_close_has_none(cfg, delivered):
    conn = warehouse_through(delivered, 2)
    assert by_id(validate(cfg, delivered, 1, conn))["I01"].orders_affected == 0, "a delivery that is loaded again brings no new orders"
    close = by_id(validate(cfg, delivered, 14, warehouse_through(delivered, 14)))
    assert close["I01"].orders_affected == 0 and close["I03"].orders_affected == len(frames_of(delivered, 14).outcomes) > 0


# --- the validation reads and does not write ----------------------------------------------------------------------------------------------------


def test_validation_never_writes_the_warehouse(cfg, delivered, tmp_path):
    path = tmp_path / "w.sqlite"
    conn = warehouse_through(delivered, 2, path)
    conn.close()
    before = file_digest(path)
    import sqlite3

    conn = sqlite3.connect(path, isolation_level=None)
    validate(cfg, delivered, 1, conn)
    validate(cfg, delivered, 2, conn)
    conn.close()
    assert file_digest(path) == before


def test_a_report_of_a_delivery_carries_only_ids_and_dates(cfg, delivered, fixture_truth, tmp_path):
    conn = warehouse_through(delivered, 2)
    report = validate(cfg, delivered, 1, conn)               # M2017-01 again: failing rules with examples
    write_report(report, tmp_path)
    blob = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert all(needle not in blob for needle in fixture_truth.sentinel_needles)
    allowed = {"table", "reason", "order_id", "line_id", "order_date", "outcome_known_date", "delivery_id", "missing", "plan", "period", "expected_period",
               "previous", "outcomes_to", "reasons", "rows", "manifest_rows", "sha256", "manifest_sha256", "message"}
    assert {key for r in report.results for e in r.examples for key in e} <= allowed
    assert read_report(tmp_path / "latest.json") == report and len(report.results) == len(DELIVERY_BLOCKING + INFORMATIVE)


# --- the rules of the model: an interface for the steps that come later --------------------------------------------------------------------------


def test_model_rules_join_the_report_in_their_place_and_a_failing_blocking_one_rejects(cfg, delivered, tmp_path):
    report = validate(cfg, delivered, 0, warehouse_through(delivered, 0))
    assert not report.blocking_failed and report.not_evaluated == tuple(MODEL_IDS)
    added = with_results(report, [make_result("M05", False, 0, 0, [{"reason": "other_config"}]), make_result("I02", True, 40, 40), make_result("M03", False, 0, 0, [{"reason": "x"}])])
    assert added.blocking_failed and set(added.not_evaluated) == {"M01", "M02", "M04", "I04"}
    assert [r.rule_id for r in added.results] == DELIVERY_BLOCKING + ["M03", "M05", "I01", "I02", "I03", "I05"]
    found = by_id(added)
    assert (found["M05"].status_es, found["M03"].status_es, found["I02"].status_es) == ("advierte", "falla", "pasa")
    assert found["I02"].passed and make_result("I04", False).passed, "an information rule only counts"
    write_report(added, tmp_path)
    assert read_report(tmp_path / "latest.json") == added
    text = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "| M03 |" in text and "Rechazado" in text
    assert "M03" in str(ValidationError(added)) and "M05" not in str(ValidationError(added))


def test_a_model_result_replaces_an_earlier_one_of_the_same_rule(cfg, delivered):
    report = validate(cfg, delivered, 0, warehouse_through(delivered, 0))
    first = with_results(report, [make_result("M01", False, examples=[{"auc": 0.97}])])
    second = with_results(first, [make_result("M01", True)])
    assert first.blocking_failed and not second.blocking_failed and sum(r.rule_id == "M01" for r in second.results) == 1


# --- the report of a run: the source and the delivery ------------------------------------------------------------------------------------------------


def test_combine_reports_puts_the_source_rules_before_the_delivery_ones(cfg, delivered, fixture_read, fixture_truth):
    source = validate_source(fixture_read[1], run_key="src", source_sha256=fixture_truth.sha256)
    delivery = validate(cfg, delivered, 0, warehouse_through(delivered, 0), run_key="L2017-01-07")
    combined = combine_reports(source, delivery)
    assert [r.rule_id for r in combined.results] == list(RULES)[:18] + DELIVERY_BLOCKING + INFORMATIVE
    assert (combined.run_key, combined.source_sha256, combined.delivery_id, combined.rows_delivery) == ("L2017-01-07", fixture_truth.sha256, "L2017-01-07", delivery.rows_delivery)
    assert combined.not_evaluated == tuple(MODEL_IDS) and not combined.blocking_failed
    assert combine_reports(source, with_results(delivery, [make_result("M03", False)])).blocking_failed


# --- the real file (real_data) ----------------------------------------------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_file_the_fifteen_deliveries_pass_in_order_and_add_up(cfg, real_read, tmp_path):
    frames, _report = real_read
    plan = delivery_plan(cfg.deliveries)
    conn = minimal_warehouse()
    totals = {"I01": 0, "I03": 0, "I05": 0}
    for spec in plan:
        entry = publish(spec, frames, tmp_path, source_sha256="fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6")
        report = validate_published_delivery(tmp_path, entry, spec, conn, cfg, run_key=spec.delivery_id)
        assert not report.blocking_failed, (spec.delivery_id, [r.rule_id for r in report.results if not r.passed])
        found = by_id(report)
        for key in totals:
            totals[key] += found[key].orders_affected
        mark_loaded(conn, spec, read_delivery(tmp_path, entry))
        if spec.delivery_id == "L2017-01-07":
            assert found["I01"].orders_affected == 42_152
    assert totals == {"I01": 65_752, "I03": 65_752, "I05": 8_403}
    again = validate_published_delivery(tmp_path, entry, plan[-1], conn, cfg, run_key="again")
    assert by_id(again)["V20"].examples[0]["reason"] == "already_loaded" and again.blocking_failed
    print("REAL", totals, "first delivery", date(2017, 1, 7))
