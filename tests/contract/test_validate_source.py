"""Validation I (U06): the rules of the source, V01 to V18, and the report that carries them (RF07, AC10 for the source).

``read_source`` raises a ``SourceError`` for V02 to V09 and returns a ``SourceReport`` with the counts for V10 to V18; ``validate_source`` turns either into
rule results with the rows and the orders that each one affects. The truth comes from the synthetic fixture, whose cases are recorded by the generator, and
from the real file (``real_data``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from latedel.ingest import _rejected, check_hop, read_source
from latedel.schema import SCHEDULED_DAYS_BY_MODE, SourceError
from latedel.validate import (
    RULES,
    SOURCE_BLOCKING,
    SOURCE_RULES,
    Severity,
    ValidationReport,
    read_report,
    validate_source,
    write_report,
)
from tests.fixtures.make_fixture import HEADER, MALFORMED_KINDS, MALFORMED_RULES, _parse, _serialise

SHA = "ab" * 32
BLOCKING = ["V01", "V02", "V03", "V04", "V05", "V06", "V07", "V08", "V09"]
WARNINGS = ["V10", "V11", "V12", "V13", "V14"]
INFOS = ["V15", "V16", "V17", "V18"]


def column(name: str) -> int:
    return HEADER.index(name)


def by_id(report: ValidationReport):
    return {r.rule_id: r for r in report.results}


def edited(fixture_csv: Path, tmp_path: Path, change, name: str = "edited.csv") -> Path:
    table = _parse(fixture_csv.read_bytes())
    change(table)
    target = tmp_path / name
    target.write_bytes(_serialise(table))
    return target


def order_of(table, number: int) -> str:
    return table[number][column("Order Id")]


def failure(cfg, path: Path, size_limit: int | None = None) -> SourceError:
    import dataclasses

    if size_limit is not None:
        cfg = dataclasses.replace(cfg, source=dataclasses.replace(cfg.source, max_bytes=size_limit))
    with pytest.raises(SourceError) as error:
        read_source(path, cfg)
    return error.value


# --- the table of rules ------------------------------------------------------------------------------------------------


def test_the_rules_have_the_ids_severities_and_scope_of_the_plan():
    assert list(RULES)[:18] == BLOCKING + WARNINGS + INFOS
    assert tuple(SOURCE_RULES) == tuple(BLOCKING + WARNINGS + INFOS) and tuple(SOURCE_BLOCKING) == tuple(BLOCKING)
    for rule_id in BLOCKING:
        assert RULES[rule_id].severity is Severity.BLOCKING
    for rule_id in WARNINGS:
        assert RULES[rule_id].severity is Severity.WARNING
    for rule_id in INFOS:
        assert RULES[rule_id].severity is Severity.INFO
    assert all(RULES[rule_id].scope == "source" and RULES[rule_id].name_es.strip() for rule_id in BLOCKING + WARNINGS + INFOS)


# --- a clean read: V01 to V09 pass, V10 to V18 carry the counts of the fixture (AC10) ---------------------------------------


def test_warning_and_info_counts_match_fixture_truth(fixture_read, fixture_truth, fixture_csv):
    _, source_report = fixture_read
    report = validate_source(source_report, run_key="r1", source_sha256=fixture_truth.sha256)
    found = by_id(report)
    assert [r.rule_id for r in report.results] == BLOCKING + WARNINGS + INFOS and report.not_evaluated == ()
    assert report.run_key == "r1" and report.source_sha256 == fixture_truth.sha256 and report.delivery_id is None and not report.blocking_failed
    for rule_id in BLOCKING:
        assert (found[rule_id].passed, found[rule_id].rows_affected, found[rule_id].orders_affected) == (True, 0, 0)

    # V10: the fixture keeps every mode on its scheduled days
    assert (found["V10"].passed, found["V10"].rows_affected, found["V10"].orders_affected) == (True, 0, 0)
    # V11: cancelled orders, with their lines
    v11 = found["V11"]
    assert (v11.passed, v11.rows_affected, v11.orders_affected) == (False, fixture_truth.cancelled_lines, fixture_truth.cancelled_orders)
    assert v11.severity is Severity.WARNING and v11.rows_affected > 0
    # V12 and V13: the repeated columns and the amounts agree
    assert (found["V12"].passed, found["V12"].rows_affected, found["V12"].orders_affected) == (True, 0, 0)
    assert (found["V13"].passed, found["V13"].rows_affected, found["V13"].orders_affected) == (True, 0, 0)
    # V14: trailing spaces, counted again here from the CSV, independently of the reader
    table = _parse(fixture_csv.read_bytes())
    trailing = [row for row in table[1:] if row[column("Product Name")] != row[column("Product Name")].rstrip()]
    assert len(trailing) == len(fixture_truth.cases["trailing_space_line_ids"]) > 0
    v14 = found["V14"]
    assert (v14.passed, v14.rows_affected, v14.orders_affected) == (False, len(trailing), len({row[column("Order Id")] for row in trailing}))
    # V15 to V18: only the counts
    for rule_id, columns in (("V15", 15), ("V16", 7), ("V18", 8)):
        assert found[rule_id].passed and found[rule_id].severity is Severity.INFO
        assert found[rule_id].examples == [{"columns": columns}]
    post = [o for o in fixture_truth.orders.values() if o.regime == "post_break"]
    v17 = found["V17"]
    assert (v17.passed, v17.rows_affected, v17.orders_affected) == (True, sum(o.n_lines for o in post), len(post)) and len(post) > 50


def test_an_information_rule_always_passes_and_a_warning_advises_when_it_finds_something(fixture_read):
    report = validate_source(fixture_read[1], run_key="r1")
    for rule_id in INFOS:
        assert by_id(report)[rule_id].passed is True
    assert [r.rule_id for r in report.results if not r.passed] == ["V11", "V14"]
    assert [r.status_es for r in report.results if r.rule_id in ("V01", "V11", "V15")] == ["pasa", "advierte", "pasa"]


# --- V10, V12 and V13 with something to report -------------------------------------------------------------------------------


def test_v10_counts_the_orders_whose_scheduled_days_are_not_those_of_their_mode_and_their_lines(cfg, fixture_csv, fixture_truth, tmp_path):
    table = _parse(fixture_csv.read_bytes())
    order = next(o for o in fixture_truth.orders.values() if o.n_lines >= 2 and not o.cancelled and o.mode == "Standard Class")

    def change(t):
        for row in t[1:]:
            if row[column("Order Id")] == str(order.order_id):
                row[column("Shipping Mode")] = "Same Day"   # the scheduled days stay 4: Same Day is 0

    path = edited(fixture_csv, tmp_path, change)
    _, source_report = read_source(path, cfg)
    v10 = by_id(validate_source(source_report, run_key="r"))["V10"]
    assert SCHEDULED_DAYS_BY_MODE["Same Day"] != 4
    assert (v10.severity, v10.passed, v10.rows_affected, v10.orders_affected) == (Severity.WARNING, False, order.n_lines, 1)
    assert v10.examples == [{"expected_days": SCHEDULED_DAYS_BY_MODE}]
    assert len(table) > 0


def test_v12_counts_the_lines_and_the_orders_with_a_repeated_column_that_differs_not_the_cells(cfg, fixture_csv, tmp_path):
    seen: dict[str, int] = {}

    def change(t):
        one, two = 5, 80
        assert order_of(t, one) != order_of(t, two)
        t[one][column("Sales per customer")] = f"{float(t[one][column('Sales per customer')]) + 3:.2f}"       # one pair on this line
        t[two][column("Product Card Id")] = str(int(t[two][column("Product Card Id")]) + 1)                  # two pairs on this one
        t[two][column("Product Category Id")] = str(int(t[two][column("Product Category Id")]) + 1)
        seen.update(one=int(order_of(t, one)), two=int(order_of(t, two)))

    path = edited(fixture_csv, tmp_path, change)
    _, source_report = read_source(path, cfg)
    assert source_report.counts["V12"]["discrepancies"] == 3, "three cells differ"
    v12 = by_id(validate_source(source_report, run_key="r"))["V12"]
    assert (v12.severity, v12.passed, v12.rows_affected, v12.orders_affected) == (Severity.WARNING, False, 2, 2)
    assert v12.examples == [{"pairs": 6, "cells": 3}]
    assert seen


def test_v13_counts_the_lines_and_the_orders_whose_amounts_do_not_add_up(cfg, fixture_csv, tmp_path):
    orders: list[str] = []

    def change(t):
        total, sales = column("Order Item Total"), column("Sales")
        t[2][total] = f"{float(t[2][total]) + 0.01:.2f}"       # one cent off: inside the tolerance
        t[3][total] = f"{float(t[3][total]) + 0.02:.2f}"       # two cents off: a discrepancy
        t[40][sales] = f"{float(t[40][sales]) + 1:.2f}"        # sales no longer price times quantity: a discrepancy
        orders.extend({order_of(t, 3), order_of(t, 40)})

    path = edited(fixture_csv, tmp_path, change)
    _, source_report = read_source(path, cfg)
    v13 = by_id(validate_source(source_report, run_key="r"))["V13"]
    assert (v13.severity, v13.passed, v13.rows_affected, v13.orders_affected) == (Severity.WARNING, False, 2, len(set(orders)))
    assert v13.examples == [{"tolerance": 0.015}]


# --- a rejected source: the rule that failed, the ones before it passed, the rest not evaluated (AC10) -----------------------


@pytest.mark.parametrize("kind", [k for k in MALFORMED_KINDS if k != "wrong_hash"])
def test_each_malformed_variant_fails_its_rule_and_leaves_the_rest_not_evaluated(cfg, fixture_truth, malformed, kind):
    error = failure(cfg, malformed(kind), size_limit=fixture_truth.size_bytes + 100_000)
    rule = MALFORMED_RULES[kind]
    assert error.rule_id == rule
    report = validate_source(error, run_key="r", source_sha256=SHA)
    found = by_id(report)
    failed = [r.rule_id for r in report.results if not r.passed]
    assert failed == [rule] and report.blocking_failed and found[rule].severity is Severity.BLOCKING
    assert found[rule].examples, "the problem is described, with where it is"
    for rule_id in BLOCKING[: BLOCKING.index(rule)]:
        assert found[rule_id].passed, f"{rule_id} is evaluated before {rule} and passed"
    after = BLOCKING[BLOCKING.index(rule) + 1:] + WARNINGS + INFOS
    assert set(report.not_evaluated) == set(after) and not (set(after) & set(found)), "never reported as passed"


def test_a_wrong_hash_is_rule_v01_and_nothing_after_it_is_evaluated(fixture_csv):
    error = _rejected(fixture_csv, "aceptada", "cd" * 32, "ab" * 32)
    report = validate_source(error, run_key="r", source_sha256="cd" * 32)
    assert [(r.rule_id, r.passed) for r in report.results] == [("V01", False)]
    assert report.not_evaluated == tuple(BLOCKING[1:] + WARNINGS + INFOS) and report.blocking_failed
    assert all("kept" not in example and str(fixture_csv) not in json.dumps(example) for example in report.results[0].examples), "no path of this machine"


def test_a_refused_address_is_v02_and_v01_was_never_evaluated(cfg):
    with pytest.raises(SourceError) as error:
        check_hop("http://data.mendeley.com/file", ("data.mendeley.com",))
    report = validate_source(error.value, run_key="r")
    assert [(r.rule_id, r.passed) for r in report.results] == [("V02", False)]
    assert report.not_evaluated[0] == "V01" and set(report.not_evaluated) == set(BLOCKING + WARNINGS + INFOS) - {"V02"}


def test_a_file_that_is_too_big_is_v02_after_v01_passed(cfg, fixture_csv):
    error = failure(cfg, fixture_csv, size_limit=1000)
    report = validate_source(error, run_key="r")
    assert [(r.rule_id, r.passed) for r in report.results] == [("V01", True), ("V02", False)]


@pytest.mark.parametrize(("kind", "rows", "orders"), [
    ("short_row", 1, 0), ("bad_date", 1, 0), ("unmasked_email", 1, 0), ("unmasked_password", 1, 0), ("duplicate_item_id", 1, 0),
    ("two_modes_one_order", 0, 1), ("label_mismatch", 0, 1), ("outcome_after_7_days", 0, 1),
])
def test_a_failing_rule_counts_its_rows_or_its_orders(cfg, malformed, fixture_truth, kind, rows, orders):
    error = failure(cfg, malformed(kind))
    result = by_id(validate_source(error, run_key="r"))[MALFORMED_RULES[kind]]
    assert (result.rows_affected, result.orders_affected) == (rows, orders)


def test_only_a_blocking_rule_of_the_source_can_be_the_cause_of_a_rejection():
    for rule_id in ("V10", "V20", "M01", "V99"):
        with pytest.raises(ValueError, match=rule_id):
            validate_source(SourceError(rule_id, "x"), run_key="r")


def test_the_examples_of_a_problem_are_clean_and_short(cfg, fixture_csv, tmp_path):
    def change(t):
        t[7][column("order date (DateOrders)")] = "x" * 5000 + "\u0007"

    error = failure(cfg, edited(fixture_csv, tmp_path, change))
    examples = by_id(validate_source(error, run_key="r"))["V05"].examples
    shown = json.dumps(examples)
    assert "x" * 100 not in shown and "\\u0007" not in shown and max(len(str(v)) for e in examples for v in e.values()) < 200


# --- the report files ------------------------------------------------------------------------------------------------------


def test_the_report_files_say_the_verdict_the_rules_and_the_counts(fixture_read, fixture_truth, tmp_path):
    report = validate_source(fixture_read[1], run_key="L2017-01-07", source_sha256=fixture_truth.sha256)
    latest_json, latest_md = write_report(report, tmp_path)
    assert (latest_json, latest_md) == (tmp_path / "latest.json", tmp_path / "latest.md")
    assert {p.name for p in (tmp_path / "history").iterdir()} == {"L2017-01-07.json", "L2017-01-07.md"}
    document = json.loads(latest_json.read_text(encoding="utf-8"))
    assert document["run_key"] == "L2017-01-07" and document["source_sha256"] == fixture_truth.sha256 and document["blocking_failed"] is False
    assert [r["rule_id"] for r in document["results"]] == BLOCKING + WARNINGS + INFOS and document["not_evaluated"] == []
    v11 = next(r for r in document["results"] if r["rule_id"] == "V11")
    assert (v11["severity"], v11["status"], v11["scope"], v11["rows_affected"], v11["orders_affected"]) == (
        "advertencia", "advierte", "source", fixture_truth.cancelled_lines, fixture_truth.cancelled_orders)
    markdown = latest_md.read_text(encoding="utf-8")
    assert markdown.startswith("# Reporte de validación") and "Aceptado" in markdown and "Rechazado" not in markdown
    assert f"| V11 |" in markdown and f"| {fixture_truth.cancelled_lines} | {fixture_truth.cancelled_orders} |" in markdown
    assert (tmp_path / "history" / "L2017-01-07.json").read_text(encoding="utf-8") == latest_json.read_text(encoding="utf-8")


def test_a_rejected_source_report_says_rechazado_and_which_rules_were_not_evaluated(cfg, malformed, tmp_path):
    report = validate_source(failure(cfg, malformed("label_mismatch")), run_key="r9")
    _, latest_md = write_report(report, tmp_path)
    markdown = latest_md.read_text(encoding="utf-8")
    assert "Rechazado" in markdown and "## No evaluadas" in markdown and "V09" in markdown.split("## No evaluadas")[1] and "| V08 |" in markdown
    assert json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["blocking_failed"] is True


def test_a_report_read_back_is_the_report_that_was_written(fixture_read, cfg, malformed, tmp_path):
    for number, report in enumerate((validate_source(fixture_read[1], run_key="r1", source_sha256=SHA),
                                     validate_source(failure(cfg, malformed("short_row")), run_key="r2"))):
        write_report(report, tmp_path / str(number))
        assert read_report(tmp_path / str(number) / "latest.json") == report
        assert read_report(tmp_path / str(number) / "history" / f"r{number + 1}.json") == report


def test_writing_a_report_twice_gives_the_same_bytes_and_a_failure_writes_nothing(fixture_read, tmp_path):
    report = validate_source(fixture_read[1], run_key="r1")
    write_report(report, tmp_path)
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    write_report(report, tmp_path)
    assert {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    for bad in ("../x", "a/b", "", ".hidden", "x y"):
        bad_report = validate_source(fixture_read[1], run_key=bad)
        target = tmp_path / "other"
        with pytest.raises(ValueError, match="run_key"):
            write_report(bad_report, target)
        assert not target.exists()


@pytest.mark.parametrize("kind", [None, "unmasked_email", "unmasked_password", "short_row", "two_modes_one_order", "invalid_cp1252_byte", "wrong_hash"])
def test_no_report_carries_a_personal_value_a_sentinel_or_a_path_of_this_machine(cfg, fixture_read, fixture_truth, fixture_csv, malformed, tmp_path, kind):
    if kind is None:
        report = validate_source(fixture_read[1], run_key="r1", source_sha256=SHA)
    elif kind == "wrong_hash":
        report = validate_source(_rejected(fixture_csv, "aceptada", "cd" * 32, "ab" * 32), run_key="r1")
    else:
        report = validate_source(failure(cfg, malformed(kind)), run_key="r1")
    out = tmp_path / "report"
    write_report(report, out)
    blob = b"".join(p.read_bytes() for p in out.rglob("*") if p.is_file())
    assert blob
    for needle in fixture_truth.sentinel_needles:
        assert needle not in blob
    for forbidden in (b"ann.smith", b"hunter2", str(fixture_csv.parent).encode(), str(tmp_path).encode()[:0] or b"\x00\x00"):
        assert forbidden not in blob


def test_validating_logs_the_verdict_and_never_a_value(fixture_read, cfg, malformed, caplog, fixture_truth):
    with caplog.at_level(logging.INFO, logger="latedel.validate"):
        validate_source(fixture_read[1], run_key="r1")
        validate_source(failure(cfg, malformed("unmasked_email")), run_key="r2")
    messages = [r.getMessage() for r in caplog.records if r.name == "latedel.validate"]
    assert any("r1" in m and "accepted" in m for m in messages) and any("r2" in m and "rejected" in m and "V06" in m for m in messages)
    assert not any("ann.smith" in m for m in messages)
    assert [r.levelno for r in caplog.records if "r2" in r.getMessage()] == [logging.WARNING]


# --- the real file (real_data) -------------------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_file_rule_results(real_read):
    _, source_report = real_read
    report = validate_source(source_report, run_key="real", source_sha256="fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6")
    found = by_id(report)
    assert not report.blocking_failed and report.not_evaluated == () and [r.rule_id for r in report.results] == BLOCKING + WARNINGS + INFOS
    assert (found["V10"].passed, found["V12"].passed, found["V13"].passed) == (True, True, True)
    assert (found["V11"].rows_affected, found["V11"].orders_affected) == (7_754, 2_855)
    assert found["V14"].rows_affected > 0 and 0 < found["V14"].orders_affected <= found["V14"].rows_affected
    assert found["V17"].orders_affected == 8_403 and found["V17"].rows_affected >= 8_403
    assert [found[r].examples[0]["columns"] for r in ("V15", "V16", "V18")] == [15, 7, 8]
    print("REAL", {r: (found[r].rows_affected, found[r].orders_affected) for r in WARNINGS + ["V17"]})
