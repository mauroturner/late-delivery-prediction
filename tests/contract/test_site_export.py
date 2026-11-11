"""The five JSON files the personal site copies (U20, AC26, docs/plan.md section 18, decision D22).

The schema is written here with dataclasses of the test's own: a file with a key more or fewer than the contract is an error, so a change of shape is a change of this
test (the site consumes the files, item 3 of the risk list). The figures are compared with the Parquet files of the star schema exported from the same warehouse and the
same run, computed here again from the frames and never with the module under test. The tests write the files only into temporary folders.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import types
import typing
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from latedel.export import ExportMeta, build_powerbi_marts
from latedel.registry import read_seal
from latedel.schema import ATTRIBUTION, DUPLICATE_OR_CONSTANT_COLUMNS, LEAKING_COLUMNS, PERSONAL_COLUMNS
from latedel.site_export import MANIFEST_FILE, SITE_FILES, SiteExportError, export_site, export_summary
from latedel.tracking import track
from latedel.validate import RULES
from latedel.warehouse import record_run

META = ExportMeta(run_key="run-C2018-02", as_of="2018-02-07", source_sha256="ab" * 32)


# --- the contract ---------------------------------------------------------------------------------------------------------------------------


@dataclass
class Doc:
    schema_version: int
    run_key: str
    as_of: str
    source_sha256: str
    attribution: str


@dataclass
class Triple:
    model: float | None
    b2_mode: float | None
    b5_mode_noon: float | None


@dataclass
class Headline:
    rows: int
    orders: int
    orders_cancelled: int
    orders_task: int
    test_orders: int
    period_start: str
    period_end: str
    late_share_task: float | None
    threshold: float | None
    break_even: float | None
    alert_cost: float
    late_cost: float
    effect: float
    test_alert_share: float | None
    test_precision: float | None
    test_recall: float | None
    auc: Triple
    net_benefit_per_1000: Triple


@dataclass
class CriterionRow:
    id: str
    name_es: str
    passed: bool | None
    value: float | None
    baseline_value: float | None
    ci_low: float | None
    ci_high: float | None
    threshold_es: str
    seen_by_research: str
    seen_by_planner: str
    verdict_es: str


@dataclass
class SealBlock:
    config_sha256: str | None
    launch_run_key: str | None
    test_run_key: str | None
    test_as_of: str | None


@dataclass
class SummaryDoc(Doc):
    status: str
    evaluation_status: str
    headline: Headline
    criteria: list[CriterionRow]
    seal: SealBlock


@dataclass
class RuleRow:
    rule_id: str
    name_es: str
    severity: str
    scope: str
    status: str
    rows: int
    orders: int


@dataclass
class Totals:
    rows_raw: int
    orders_raw: int
    orders_cancelled: int
    rows_cancelled: int
    orders_task: int
    personal_columns_dropped: int
    leaking_columns_set_aside: int
    deliveries_loaded: int
    deliveries_rejected: int


@dataclass
class ValidationDoc(Doc):
    rules: list[RuleRow]
    totals: Totals


@dataclass
class ColumnRow:
    column: str
    column_es: str
    kind: str
    known_at_es: str
    auc_with_mode_valid: float | None
    note_es: str


@dataclass
class Reference:
    auc_mode_only_valid: float | None
    auc_selected_model_valid: float | None


@dataclass
class LeakageDoc(Doc):
    columns: list[ColumnRow]
    reference: Reference


@dataclass
class SplitRow:
    block: str
    label_es: str
    start: str
    end: str
    suppressed: bool
    orders: int | None
    late_share: float | None


@dataclass
class ModeRate:
    mode: str
    mode_es: str
    scheduled_days: int
    suppressed: bool
    train_orders: int | None
    train_late_rate: float | None


@dataclass
class HourRow:
    mode: str
    hour: int
    suppressed: bool
    train_orders: int | None
    train_late_rate: float | None


@dataclass
class SelectionRow:
    feature_set: str
    learning_rate: float
    max_leaf_nodes: int
    min_samples_leaf: int
    log_loss_v1: float
    chosen: bool


@dataclass
class MethodRow:
    method: str
    label_es: str
    scope: str
    auc: float | None
    pr_auc: float | None
    brier: float | None
    threshold: float | None
    flagged_share: float | None
    precision: float | None
    recall: float | None
    net_benefit_per_1000: float | None


@dataclass
class RocRow:
    method: str
    fpr: float
    tpr: float


@dataclass
class CalibrationRow:
    bin: int
    suppressed: bool
    orders: int | None
    p_mean: float | None
    observed: float | None


@dataclass
class CostRow:
    break_even: float
    threshold_model: float | None
    nb_model: float | None
    nb_b2_mode: float | None
    nb_b5_mode_noon: float | None
    flagged_share_model: float | None


@dataclass
class ThresholdBlock:
    value: float
    break_even: float
    alert_cost: float
    late_cost: float
    effect: float
    chosen_on: str


@dataclass
class ImportanceRow:
    feature: str
    feature_es: str
    auc_drop: float


@dataclass
class ExampleRow:
    """One example of a cell of B5. The six details (month, hour, weekday, market, lines, real days) are null when publishing them would leave the example shared by
    fewer than ``min_cell_orders`` orders of the test (H5, D77)."""

    label: str
    rule_es: str
    month: str | None
    mode: str
    mode_es: str
    scheduled_days: int
    order_hour: int | None
    weekday_es: str | None
    market: str | None
    n_lines: int | None
    risk: float
    is_alert: bool
    outcome: str
    real_days: int | None


@dataclass
class AlgorithmsDoc(Doc):
    split: list[SplitRow]
    mode_rates: list[ModeRate]
    hour_profile: list[HourRow]
    selection: list[SelectionRow]
    methods: list[MethodRow]
    roc: list[RocRow]
    calibration: list[CalibrationRow]
    cost_curve: list[CostRow]
    threshold: ThresholdBlock | None
    importance: list[ImportanceRow]
    examples: list[ExampleRow]


@dataclass
class MonthRow:
    month: str
    regime: str | None
    suppressed: bool
    orders: int | None
    alerts: int | None
    alert_share: float | None
    closed_orders: int | None
    late_observed: int | None
    late_expected: float | None
    closed_alerts: int | None
    alerts_late: int | None
    precision: float | None
    recall: float | None
    cancelled_orders: int | None


@dataclass
class ByMode:
    mode: str
    mode_es: str
    suppressed: bool
    orders: int | None
    alerts: int | None
    precision: float | None
    recall: float | None


@dataclass
class ByMarket:
    market: str
    suppressed: bool
    orders: int | None
    alerts: int | None
    late_rate_observed: float | None


@dataclass
class Cancelled:
    alerts_on_later_cancelled: int
    share_of_alerts: float | None


@dataclass
class AlertsDoc(Doc):
    threshold: float | None
    model_version: str | None
    monthly: list[MonthRow]
    by_mode: list[ByMode]
    by_market: list[ByMarket]
    cancelled: Cancelled


DOCS = {"summary.json": SummaryDoc, "validation_rules.json": ValidationDoc, "leakage.json": LeakageDoc, "algorithms.json": AlgorithmsDoc, "alerts.json": AlertsDoc}


def _convert(tp, value, path: str):
    """`value` as the type `tp`, strictly: an unknown or missing key, a wrong type or a bool where a number goes is an error."""
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        options = typing.get_args(tp)
        if value is None:
            assert type(None) in options, f"{path}: null where the contract has no null"
            return None
        [inner] = [o for o in options if o is not type(None)]
        return _convert(inner, value, path)
    if origin is list:
        assert isinstance(value, list), f"{path}: not a list"
        return [_convert(typing.get_args(tp)[0], item, f"{path}[{i}]") for i, item in enumerate(value)]
    if is_dataclass(tp):
        assert isinstance(value, dict), f"{path}: not an object"
        names = {f.name for f in fields(tp)}
        assert set(value) == names, f"{path}: keys differ from the contract: {sorted(set(value) ^ names)}"
        hints = typing.get_type_hints(tp)
        return tp(**{name: _convert(hints[name], value[name], f"{path}.{name}") for name in names})
    if tp is bool:
        assert isinstance(value, bool), f"{path}: not a bool"
    elif tp is int:
        assert isinstance(value, int) and not isinstance(value, bool), f"{path}: not an integer"
    elif tp is float:
        assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{path}: not a number"
        assert math.isfinite(value), f"{path}: not finite"
    elif tp is str:
        assert isinstance(value, str), f"{path}: not a string"
    return value


def parse(name: str, folder: Path):
    return _convert(DOCS[name], json.loads((folder / name).read_text(encoding="utf-8")), name)


def all_docs(folder: Path) -> dict[str, object]:
    return {name: parse(name, folder) for name in SITE_FILES}


# --- fixtures ---------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def site(fixture_site, star_export) -> Path:
    """The five files of the fixture warehouse, written once per session by ``fixture_site`` with this module's ``META`` (no test writes into the folder)."""
    assert star_export.meta == META
    return fixture_site


@pytest.fixture(scope="module")
def docs(site) -> dict[str, object]:
    return all_docs(site)


@pytest.fixture(scope="module")
def frames(star_export) -> dict[str, pd.DataFrame]:
    return {p.stem: pd.read_parquet(p) for p in star_export.out_dir.glob("*.parquet")}


def approx(value, digits):
    return pytest.approx(value, abs=0.6 * 10 ** -digits)


def expected_hidden(counts: list[int], minimum: int, total_published: bool = True) -> list[bool]:
    """Which cells of one breakdown are hidden, written again here (D07 and the secondary suppression of D77): a cell under the minimum (an empty one too) and, when
    that leaves exactly one hidden cell in a breakdown whose total is published somewhere, the smallest shown cell (the first one on a tie); in a breakdown whose own
    total is hidden, the smallest shown cell if no cell is hidden."""
    hidden = [n < max(minimum, 1) for n in counts]
    if (total_published and sum(hidden) == 1) or (not total_published and not any(hidden)):
        shown = [(n, i) for i, (n, h) in enumerate(zip(counts, hidden, strict=True)) if not h]
        if shown:
            hidden[min(shown)[1]] = True
    return hidden


def walk(node, path=""):
    """Every ``(path, key, value)`` of a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from walk(item, f"{path}[{i}]")


def objects(node, path=""):
    """Every ``(path, object)`` of a JSON document."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from objects(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from objects(item, f"{path}[{i}]")


# --- the named test -----------------------------------------------------------------------------------------------------------------------------


def test_no_identifier_or_sentinel_and_small_cells_suppressed(star_export, site, frames, fixture_truth, tmp_path):
    """AC26: no order number, customer identifier or personal value in any file, and no aggregate cell under the minimum size with its value."""
    assert sorted(p.name for p in site.iterdir()) == sorted([*SITE_FILES, MANIFEST_FILE])
    raw = b"".join((site / name).read_bytes() for name in [*SITE_FILES, MANIFEST_FILE])
    for needle in fixture_truth.sentinel_needles:
        assert needle not in raw, "a sentinel of a personal column reached the files"
    order_ids = set(map(str, frames["fact_order"]["order_id"]))
    for name in SITE_FILES:
        document = json.loads((site / name).read_text(encoding="utf-8"))
        for _, key, value in walk(document):
            assert key != "order_id" and "customer" not in key.lower() and "email" not in key.lower() and "password" not in key.lower(), (name, key)
            assert not (isinstance(value, str) and value in order_ids and len(value) >= 5), (name, key, "the text of an order number")
    # the minimum size: with the real one nothing small is shown, with a bigger one more is hidden, and a hidden cell has no value at all
    minimum = star_export.cfg.site.min_cell_orders
    assert minimum == 20
    for size in (minimum, 120, 10**9):
        cfg = replace(star_export.cfg, site=replace(star_export.cfg.site, min_cell_orders=size))
        folder = site if size == minimum else tmp_path / f"min{size}"      # the files with the minimum of the configuration are the module's
        if size != minimum:
            export_site(star_export.conn, folder, META, cfg)
        cells = 0
        for name in SITE_FILES:
            for path, value in objects(json.loads((folder / name).read_text(encoding="utf-8"))):
                if "suppressed" in value:
                    cells += 1
                    counts = [value[k] for k in ("orders", "train_orders") if k in value and value[k] is not None]
                    others = {k: v for k, v in value.items() if k not in ("suppressed", "mode", "mode_es", "market", "month", "regime", "block", "label_es", "start", "end", "hour", "bin", "scheduled_days") and v is not None}
                    if value["suppressed"]:
                        assert not others, (name, path, "a hidden cell still has a value", others)
                    else:
                        assert all(c >= size for c in counts), (name, path, "a cell under the minimum is shown", counts)
        assert cells > 100
        if size == 10**9:
            hidden = [v for p, k, v in walk(json.loads((folder / "alerts.json").read_text(encoding="utf-8"))) if k == "suppressed"]
            assert hidden and all(hidden), "with a minimum no cell reaches, every cell is hidden"


# --- the shape of the files ---------------------------------------------------------------------------------------------------------------------


def test_the_five_files_keep_the_schema_of_the_contract(docs, site, star_export):
    assert set(docs) == set(SITE_FILES)
    for name, doc in docs.items():
        assert doc.schema_version == 1 and doc.run_key == META.run_key and doc.as_of == META.as_of and doc.source_sha256 == "ab" * 32, name
        assert doc.attribution == ATTRIBUTION and "CC BY 4.0" in doc.attribution and "Constante, Fabian" in doc.attribution
        text = (site / name).read_text(encoding="utf-8")
        assert text == json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True, indent=1) + "\n", f"{name}: the writing of the contract"
        assert not text.startswith("﻿") and "\r" not in text
    assert sum((site / n).stat().st_size for n in SITE_FILES) <= star_export.cfg.site.max_total_bytes == 204800


def test_the_files_are_the_same_bytes_every_time_and_leave_no_temporary_file(star_export, site, tmp_path):
    export_site(star_export.conn, tmp_path / "again", META, star_export.cfg)
    for name in [*SITE_FILES, MANIFEST_FILE]:
        assert (tmp_path / "again" / name).read_bytes() == (site / name).read_bytes(), name
    assert sorted(p.name for p in (tmp_path / "again").iterdir()) == sorted([*SITE_FILES, MANIFEST_FILE])


# --- the figures are the ones of the Parquet files -----------------------------------------------------------------------------------------------


def test_summary_headline_is_computed_from_the_orders_the_model_and_the_evaluation(docs, frames, star_export):
    s: SummaryDoc = docs["summary.json"]
    order, model = frames["fact_order"], frames["dim_model"].iloc[0]
    cancelled = order["outcome_status"] == "cancelled"
    h = s.headline
    assert s.status == "running" and s.evaluation_status == "sealed"
    assert (h.rows, h.orders, h.orders_cancelled, h.orders_task) == (int(order["n_lines"].sum()), len(order), int(cancelled.sum()), int((~cancelled).sum()))
    assert h.test_orders == int(((order["block"] == "test") & ~cancelled).sum()) > 400
    assert h.period_start == "2015-01-01" and h.period_end == "2018-01-31"
    closed = order[order["outcome_status"] == "closed"]
    assert h.late_share_task == approx(closed["is_late"].astype(float).mean(), 3)
    assert (h.threshold, h.break_even) == (approx(model["threshold"], 2), approx(model["break_even"], 2))
    costs = star_export.cfg.costs
    assert (h.alert_cost, h.late_cost, h.effect) == (costs.alert_cost, costs.late_cost, costs.effect)
    metrics = frames["fact_method_metrics"]
    for method, field_ in (("model", "model"), ("b2_mode", "b2_mode"), ("b5_mode_noon", "b5_mode_noon")):
        row = metrics[(metrics["scope"] == "test") & (metrics["method"] == method) & (metrics["seal_state"] == "sealed")].iloc[0]
        assert getattr(h.auc, field_) == approx(row["auc"], 3) and getattr(h.net_benefit_per_1000, field_) == approx(row["nb_per_1000"], 1)
    row = metrics[(metrics["scope"] == "test") & (metrics["method"] == "model") & (metrics["seal_state"] == "sealed")].iloc[0]
    assert (h.test_alert_share, h.test_precision, h.test_recall) == (approx(row["flagged_share"], 3), approx(row["precision"], 3), approx(row["recall"], 3))


def test_summary_criteria_are_the_ten_of_the_seal_in_order_with_their_verdicts(docs, frames, star_export):
    s: SummaryDoc = docs["summary.json"]
    assert [c.id for c in s.criteria] == [f"C{i:02d}" for i in range(1, 11)]
    stored = frames["fact_evaluation_summary"]
    stored = stored[stored["seal_state"] == "sealed"].set_index("criterion_id")
    for c in s.criteria:
        row = stored.loc[c.id]
        assert c.name_es == row["name_es"] and c.verdict_es == row["verdict_es"] and c.threshold_es == row["threshold_es"]
        assert c.passed == (None if pd.isna(row["passed"]) else bool(row["passed"]))
        assert c.value == (None if pd.isna(row["value"]) else approx(row["value"], 4))
        assert (c.ci_low is None) == pd.isna(row["ci_low"]) and (c.seen_by_research, c.seen_by_planner) == (row["seen_by_research"], row["seen_by_planner"])
    assert next(c for c in s.criteria if c.id == "C10").passed is None
    assert all("supera" in c.verdict_es for c in s.criteria if c.id != "C10")
    seal = read_seal(star_export.cfg.paths.seal)
    assert s.seal == SealBlock(seal["launch"]["config_sha256"], seal["launch"]["run_key"], seal["test"]["run_key"], seal["test"]["as_of"])


def test_validation_rules_are_in_the_order_of_the_report_the_source_ones_read_once_and_the_others_summed(docs, frames):
    v: ValidationDoc = docs["validation_rules.json"]
    assert [r.rule_id for r in v.rules] == list(RULES)
    by_id = {r.rule_id: r for r in v.rules}
    for rule_id, rule in RULES.items():
        r = by_id[rule_id]
        assert (r.name_es, r.scope) == (rule.name_es, rule.scope) and r.severity == rule.severity.value and r.status in {"pass", "fail", "warn", "not_evaluated"}
    # the fixture warehouse recorded V11 (a warning that failed, 7 lines and 4 orders), V20, M05 and I02 (5 lines, 9 orders) for each of its first three runs
    assert (by_id["V11"].status, by_id["V11"].rows, by_id["V11"].orders) == ("warn", 7, 4), "a rule of the source describes one file: read once, not summed over three runs"
    # I02 and I04 count the follow-up of every month closed up to the run's date, so the latest run's count already holds the earlier ones: summed over the runs, the
    # real export said 66,761 alerts where alerts.json's months add up to 8,840 (found while building the site page). They are read once, from the latest loaded run.
    assert (by_id["I02"].status, by_id["I02"].rows, by_id["I02"].orders) == ("pass", 5, 9), "the follow-up of the latest run already counts the earlier months"
    assert by_id["V20"].status == "pass" and by_id["V01"].status == "not_evaluated" and by_id["V01"].rows == 0
    assert by_id["V24"].status == "not_evaluated", "the rejected run is not a loaded one: its failed rule is not in the totals"
    order = frames["fact_order"]
    t = v.totals
    cancelled = order["outcome_status"] == "cancelled"
    assert (t.rows_raw, t.orders_raw, t.orders_cancelled, t.orders_task) == (int(order["n_lines"].sum()), len(order), int(cancelled.sum()), int((~cancelled).sum()))
    assert t.rows_cancelled == int(order.loc[cancelled, "n_lines"].sum()) and (t.personal_columns_dropped, t.leaking_columns_set_aside) == (15, 8)
    assert t.deliveries_loaded == 15 and t.deliveries_rejected == 0


def test_leakage_names_every_column_that_is_not_an_input_and_never_a_value_of_a_personal_one(docs, frames):
    d: LeakageDoc = docs["leakage.json"]
    names = [c.column for c in d.columns]
    assert len(names) == len(set(names)) == 30 and set(names) == set(PERSONAL_COLUMNS) | set(LEAKING_COLUMNS) | set(DUPLICATE_OR_CONSTANT_COLUMNS)
    kinds = pd.Series([c.kind for c in d.columns]).value_counts().to_dict()
    assert kinds == {"personal": 15, "leak": 4, "duplicate": 4, "precaution": 3, "constant": 3, "target": 1}
    measured = frames["fact_leakage"].set_index("source_column")
    for c in d.columns:
        if c.kind in ("leak", "precaution"):
            assert c.auc_with_mode_valid == approx(measured.loc[c.column, "auc_with_mode_valid"], 3) and c.known_at_es and c.note_es
        else:
            assert c.auc_with_mode_valid is None, c.column
    assert d.reference.auc_mode_only_valid == approx(measured.loc["shipping_mode", "auc_with_mode_valid"], 3)
    valid = frames["fact_method_metrics"]
    assert d.reference.auc_selected_model_valid == approx(valid[(valid["scope"] == "valid_v2") & (valid["method"] == "model")].iloc[0]["auc"], 3)
    assert max(c.auc_with_mode_valid for c in d.columns if c.auc_with_mode_valid is not None) > 0.99 > d.reference.auc_selected_model_valid, "the columns that give the result away reach almost 1"


def test_the_split_the_mode_rates_and_the_hours_come_from_the_orders_of_the_training(docs, frames, star_export, tmp_path):
    a: AlgorithmsDoc = docs["algorithms.json"]
    order = frames["fact_order"]
    task = order[order["outcome_status"] != "cancelled"]
    assert [b.block for b in a.split] == ["train", "purge_1", "valid_v1", "valid_v2", "purge_2", "test", "post_break"]
    hidden = expected_hidden([int((task["block"] == b.block).sum()) for b in a.split], 20)
    assert [b.suppressed for b in a.split] == hidden
    s = star_export.cfg.split
    assert (a.split[0].start, a.split[0].end, a.split[5].start, a.split[5].end) == (s.train_start.isoformat(), s.train_end.isoformat(), s.test_start.isoformat(), s.test_end.isoformat())
    for b in a.split:
        mine = task[task["block"] == b.block]
        if b.suppressed:
            assert b.orders is None and b.late_share is None
            continue
        assert b.orders == len(mine)
        closed = mine[mine["outcome_status"] == "closed"]
        assert b.late_share == approx(closed["is_late"].astype(float).mean(), 3), b.block
    train = task[(task["block"] == "train") & (task["outcome_status"] == "closed")]
    modes = frames["dim_shipping_mode"].sort_values("sort_order")
    assert [m.mode for m in a.mode_rates] == list(modes["mode_key"])
    assert [m.suppressed for m in a.mode_rates] == expected_hidden([int((train["mode_key"] == m.mode).sum()) for m in a.mode_rates], 20)
    for m in a.mode_rates:
        mine = train[train["mode_key"] == m.mode]
        assert m.suppressed or (m.train_orders == len(mine) and m.train_late_rate == approx(mine["is_late"].astype(float).mean(), 3))
    assert len(a.hour_profile) == 96 and {h.hour for h in a.hour_profile} == set(range(24))
    for m in a.mode_rates:
        hours = [h for h in a.hour_profile if h.mode == m.mode]
        counts = [int(((train["mode_key"] == m.mode) & (train["order_hour"] == h.hour)).sum()) for h in hours]
        assert [h.suppressed for h in hours] == expected_hidden(counts, 20, total_published=not m.suppressed), m.mode
    for h in a.hour_profile:
        mine = train[(train["mode_key"] == h.mode) & (train["order_hour"] == h.hour)]
        if not h.suppressed:
            assert h.train_orders == len(mine) and h.train_late_rate == approx(mine["is_late"].astype(float).mean(), 3)
    # the hours of the fixture have few orders each: with a minimum of one order the cells show, and the noon cut of Same Day (D10) is there
    export_site(star_export.conn, tmp_path, META, replace(star_export.cfg, site=replace(star_export.cfg.site, min_cell_orders=1)))
    shown = parse("algorithms.json", tmp_path).hour_profile
    same_day = {h.hour: h for h in shown if h.mode == "Same Day" and not h.suppressed}
    assert len(same_day) >= 6, "the Same Day hours of the fixture have orders to show"
    assert all(h.train_late_rate == 0.0 for hour, h in same_day.items() if hour < 12) and all(h.train_late_rate == 1.0 for hour, h in same_day.items() if hour >= 12), "the noon cut"


def test_selection_threshold_and_importance_are_the_ones_the_launch_registered(docs, frames, star_export):
    a: AlgorithmsDoc = docs["algorithms.json"]
    registered = json.loads(star_export.conn.execute("SELECT selection_json FROM model_registry").fetchone()[0])
    assert len(a.selection) == len(registered) == 3 and sum(c.chosen for c in a.selection) == 1, "one row for each candidate of the configuration (the fixture has a grid of one point for each set)"
    chosen = next(c for c in a.selection if c.chosen)
    assert chosen.log_loss_v1 == min(c.log_loss_v1 for c in a.selection)
    model = frames["dim_model"].iloc[0]
    t = a.threshold
    assert t.chosen_on == "valid_v2" and t.value == approx(model["threshold"], 2) and t.break_even == approx(model["break_even"], 2)
    assert (t.alert_cost, t.late_cost, t.effect) == (approx(model["alert_cost"], 2), approx(model["late_cost"], 2), approx(model["effect"], 2))
    imp = frames["fact_importance"].sort_values(["auc_drop", "feature"], ascending=[False, True]).head(10)
    assert [i.feature for i in a.importance] == list(imp["feature"]) and [i.auc_drop for i in a.importance] == [approx(d, 4) for d in imp["auc_drop"]]
    assert 1 <= len(a.importance) <= 10 and [i.auc_drop for i in a.importance] == sorted((i.auc_drop for i in a.importance), reverse=True)


def test_methods_roc_calibration_and_cost_curve_are_the_ones_of_the_test(docs, frames, star_export):
    a: AlgorithmsDoc = docs["algorithms.json"]
    metrics = frames["fact_method_metrics"]
    assert {(m.scope, m.method) for m in a.methods} == {(s, m) for s in ("test", "post_break") for m in ("model", "b2_mode", "b5_mode_noon")}
    for m in a.methods:
        row = metrics[(metrics["scope"] == m.scope) & (metrics["method"] == m.method)].iloc[0]
        assert m.auc == approx(row["auc"], 3) and m.precision == approx(row["precision"], 3) and m.net_benefit_per_1000 == approx(row["nb_per_1000"], 1)
    roc = frames["fact_roc"]
    for method in ("model", "b2_mode", "b5_mode_noon"):
        mine = [r for r in a.roc if r.method == method]
        expected = roc[(roc["scope"] == "test") & (roc["method"] == method) & (roc["seal_state"] == "sealed")]
        assert len(mine) == len(expected) and 2 <= len(mine) <= star_export.cfg.evaluation.roc_points
        assert [r.fpr for r in mine] == sorted(r.fpr for r in mine)
    assert len(a.calibration) == star_export.cfg.evaluation.calibration_bins
    cal = frames["fact_calibration"]
    cal = cal[(cal["scope"] == "test") & (cal["method"] == "model") & (cal["seal_state"] == "sealed")].set_index("bin")
    assert [c.suppressed for c in a.calibration] == expected_hidden([int(cal.loc[c.bin, "orders"]) for c in a.calibration], 20)
    for c in a.calibration:
        if not c.suppressed:
            assert c.orders == cal.loc[c.bin, "orders"] and c.observed == approx(cal.loc[c.bin, "observed"], 3)
    assert len(a.cost_curve) == 17 and [c.break_even for c in a.cost_curve] == sorted(c.break_even for c in a.cost_curve)
    cost = frames["fact_cost_sensitivity"]
    cost = cost[(cost["scope"] == "test") & (cost["seal_state"] == "sealed")]
    for c in a.cost_curve:
        mine = cost[(cost["break_even"].round(2) == c.break_even)]
        assert c.nb_model == approx(mine[mine["method"] == "model"].iloc[0]["nb_per_1000"], 1) and c.nb_b2_mode == approx(mine[mine["method"] == "b2_mode"].iloc[0]["nb_per_1000"], 1)
        assert c.flagged_share_model == approx(mine[mine["method"] == "model"].iloc[0]["flagged_share"], 3)


def expected_examples(frames, noon: int = 12, minimum: int = 20) -> list[dict]:
    """The rule of D22, written again from the Parquet frames: for each B5 cell in a fixed order, the lower-median risk of the closed scored orders of the test."""
    order = frames["fact_order"]
    test = order[(order["block"] == "test") & (order["outcome_status"] == "closed") & order["risk"].notna()]
    cells = (("First Class", test["mode_key"] == "First Class"), ("Second Class", test["mode_key"] == "Second Class"), ("Standard Class", test["mode_key"] == "Standard Class"),
             ("Same Day", (test["mode_key"] == "Same Day") & (test["order_hour"] < noon)), ("Same Day", (test["mode_key"] == "Same Day") & (test["order_hour"] >= noon)))
    out = []
    for mode, mask in cells:
        cell = test[mask]
        if len(cell) < minimum:
            continue
        pick = cell.sort_values(["risk", "order_ts", "order_id"]).iloc[(len(cell) - 1) // 2]
        out.append({"mode": mode, "month": str(pick["order_ts"])[:7], "risk": float(pick["risk"]), "hour": int(pick["order_hour"]), "real_days": int(pick["real_days"]), "n_lines": int(pick["n_lines"])})
    return out


def test_the_examples_follow_the_fixed_rule_and_publish_only_the_month(docs, frames, site):
    a: AlgorithmsDoc = docs["algorithms.json"]
    expected = expected_examples(frames)
    assert len(expected) == len(a.examples) >= 4
    assert [e.label for e in a.examples] == [f"Pedido {c}" for c in "ABCDE"][: len(expected)]
    for got, want in zip(a.examples, expected, strict=True):
        assert got.mode == want["mode"] and got.risk == approx(want["risk"], 2) and got.outcome in ("tarde", "a tiempo")
        # a detail is the order's own value, or null when it would narrow the example below the minimum (the rule is pinned in test_site_export_hardening.py)
        for field, key in (("month", "month"), ("order_hour", "hour"), ("real_days", "real_days"), ("n_lines", "n_lines")):
            assert getattr(got, field) in (None, want[key]), (got.label, field)
        assert got.month is None or re.fullmatch(r"\d{4}-\d{2}", got.month)
    text = (site / "algorithms.json").read_text(encoding="utf-8")
    assert "order_ts" not in text and not re.search(r"\d{4}-\d{2}-\d{2}T", text), "no timestamp of an order"


def test_alerts_months_modes_and_markets_are_the_follow_up_of_the_last_date(docs, frames, star_export):
    al: AlertsDoc = docs["alerts.json"]
    model = frames["dim_model"].iloc[0]
    assert al.threshold == approx(model["threshold"], 2) and al.model_version == model["model_version"]
    track = frames["fact_tracking_monthly"]
    assert [m.month for m in al.monthly] == sorted({f"{k // 100:04d}-{k % 100:02d}" for k in track["month_key"]})
    assert [m.suppressed for m in al.monthly] == expected_hidden([int(track[track["month_key"] == int(m.month.replace("-", ""))]["orders_scored"].sum()) for m in al.monthly], 20)
    for m in al.monthly:
        key = int(m.month.replace("-", ""))
        part = track[track["month_key"] == key]
        scored = int(part["orders_scored"].sum())
        if m.suppressed:
            continue
        assert (m.orders, m.alerts, m.closed_orders, m.late_observed, m.closed_alerts, m.alerts_late, m.cancelled_orders) == (
            scored, int(part["alerts"].sum()), int(part["closed_orders"].sum()), int(part["late_closed"].sum()), int(part["closed_alerts"].sum()), int(part["alerts_late"].sum()),
            int(part["cancelled_orders"].sum()))
        assert m.alert_share == approx(m.alerts / scored, 3) and m.late_expected == approx(part["late_expected"].sum(), 1)
        assert m.precision == (approx(m.alerts_late / m.closed_alerts, 3) if m.closed_alerts else None)
        assert m.recall == approx(m.alerts_late / (m.alerts_late + int(part["late_missed"].sum())), 3)
        assert m.regime == ("post_break" if key >= 201710 else "pre_break")
    assert [b.mode for b in al.by_mode] == list(frames["dim_shipping_mode"].sort_values("sort_order")["mode_key"])
    assert [b.suppressed for b in al.by_mode] == expected_hidden([int(track[track["mode_key"] == b.mode]["orders_scored"].sum()) for b in al.by_mode], 20)
    for b in al.by_mode:
        if not b.suppressed:
            assert b.orders == int(track[track["mode_key"] == b.mode]["orders_scored"].sum())
    order = frames["fact_order"]
    scored = order[order["risk"].notna()]
    geo = frames["dim_geo"].set_index("geo_key")["market"]
    assert [b.suppressed for b in al.by_market] == expected_hidden([int((scored["geo_key"].map(geo) == b.market).sum()) for b in al.by_market], 20)
    for b in al.by_market:
        mine = scored[scored["geo_key"].map(geo) == b.market]
        if not b.suppressed:
            assert b.orders == len(mine) and b.alerts == int(mine["is_alert"].astype(bool).sum())
    assert sum(b.orders or 0 for b in al.by_market if not b.suppressed) <= len(scored)
    assert al.cancelled.alerts_on_later_cancelled == int(track["alerts_on_cancelled"].sum())
    assert al.cancelled.share_of_alerts == approx(track["alerts_on_cancelled"].sum() / track["alerts"].sum(), 3)


# --- before the test is evaluated -----------------------------------------------------------------------------------------------------------------


def test_before_the_test_is_evaluated_no_result_of_it_is_published(copy_history, tmp_path):
    """Nothing the seal has not written: the criteria are pending, the metrics, curves and examples are empty and the late share of the test block is null (D17)."""
    conn, cfg = copy_history("M2017-09")
    track(conn, date(2017, 9, 30))
    record_run(conn, {"run_key": "run-M2017-09", "started_at": "2017-09-30T08:00:00", "finished_at": None, "status": "running", "delivery_id": "M2017-09", "as_of_date": "2017-09-30",
                      "source_sha256": "ab" * 32, "orders_delivery": 1, "outcomes_delivery": 1, "orders_scored": 1, "alerts": 0, "peak_working_set_mb": 1.0, "peak_commit_mb": None, "message": ""})
    build_powerbi_marts(conn, cfg)
    meta = ExportMeta("run-M2017-09", "2017-09-30", "ab" * 32)
    export_site(conn, tmp_path, meta, cfg)
    docs = all_docs(tmp_path)
    s, a = docs["summary.json"], docs["algorithms.json"]
    assert s.evaluation_status == "pending" and s.status == "running"
    assert all(c.passed is None and c.value is None and c.ci_low is None and "Pendiente" in c.verdict_es and "2017-10" in c.verdict_es for c in s.criteria)
    assert len(s.criteria) == 10 and s.seal.test_run_key is None and s.seal.test_as_of is None
    h = s.headline
    assert (h.test_alert_share, h.test_precision, h.test_recall) == (None, None, None) and h.auc == Triple(None, None, None) and h.net_benefit_per_1000 == Triple(None, None, None)
    assert a.methods == [] and a.roc == [] and a.calibration == [] and a.cost_curve == [] and a.examples == []
    by_block = {b.block: b for b in a.split}
    assert by_block["test"].late_share is None and by_block["post_break"].late_share is None and by_block["train"].late_share is not None


# --- the refusals and the summary alone ----------------------------------------------------------------------------------------------------------


def test_without_the_model_marts_or_without_a_recorded_run_nothing_is_written(launch_conn, star_export, tmp_path):
    folder = tmp_path / "site"
    with pytest.raises(SiteExportError, match="export"):
        export_site(launch_conn, folder, META, star_export.cfg)
    with pytest.raises(SiteExportError, match="no está registrada"):
        export_site(star_export.conn, folder, ExportMeta("run-nobody", "2018-02-07", "ab" * 32), star_export.cfg)
    assert not folder.exists()


def test_files_over_the_size_cap_are_not_written_and_the_old_ones_stay(star_export, site, tmp_path):
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "summary.json").write_text('{"old": true}\n', encoding="utf-8")
    cfg = replace(star_export.cfg, site=replace(star_export.cfg.site, max_total_bytes=10_000))
    with pytest.raises(SiteExportError, match="tope"):
        export_site(star_export.conn, folder, META, cfg)
    assert [p.name for p in folder.iterdir()] == ["summary.json"] and (folder / "summary.json").read_text(encoding="utf-8") == '{"old": true}\n'
    total = sum((site / n).stat().st_size for n in SITE_FILES)
    export_site(star_export.conn, folder, META, replace(star_export.cfg, site=replace(star_export.cfg.site, max_total_bytes=total)))      # exactly at the cap: written
    assert sorted(p.name for p in folder.iterdir()) == sorted([*SITE_FILES, MANIFEST_FILE])      # the cap counts the five files the site publishes, not the manifest


def test_the_summary_alone_is_rewritten_with_the_status_of_the_run_and_leaves_the_other_four(star_export, site, tmp_path):
    folder = tmp_path / "site"
    export_site(star_export.conn, folder, META, star_export.cfg)
    before = {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in SITE_FILES}
    star_export.conn.execute("UPDATE pipeline_runs SET status = 'ok' WHERE run_key = ?", (META.run_key,))
    try:
        path = export_summary(star_export.conn, folder, META, star_export.cfg)
    finally:
        star_export.conn.execute("UPDATE pipeline_runs SET status = 'running' WHERE run_key = ?", (META.run_key,))
    assert path == folder / "summary.json" and parse("summary.json", folder).status == "ok"
    after = {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in SITE_FILES}
    assert [n for n in SITE_FILES if before[n] != after[n]] == ["summary.json"]
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["files"]["summary.json"]["sha256"] == after["summary.json"], "the manifest names the new summary"
    document = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    document["status"] = "running"
    assert json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1) + "\n" == (site / "summary.json").read_text(encoding="utf-8"), "only the status changed"
