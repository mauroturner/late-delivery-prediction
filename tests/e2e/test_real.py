"""The first complete run on the real file (U25): the counts, the seal and the summary that the run left in the project's versioned outputs.

This test does not run the flow: it reads what ``python -m latedel replay`` followed by ``python -m latedel run`` (the run with no delivery left) wrote with the full
configuration, so it fails while those outputs are missing. It checks the real counts of AC06, AC08 and AC15, that the seal, the evaluation summary, the site files and
the Parquet tables say the same thing, the file caps (RNF10) and the times and memory peaks that the pipeline recorded for each run and each task, against RNF02 and
RNF03 (AC33). Nothing here computes a metric of the test block: every figure of the model is read from what the sealed run wrote, never recomputed (D17).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from latedel.dashboard.semantic_model import MODEL
from latedel.export import MAX_PARQUET_BYTES, verify_star_schema
from latedel.registry import read_seal
from latedel.site_export import MANIFEST_FILE, SITE_FILES, verify_site
from latedel.sysinfo import POSIX_COUNTER, WINDOWS_COUNTER

pytestmark = pytest.mark.real_data

# requirements.md, section 4 and AC06: the canonical read of the real file
LINES, ORDERS, CANCELLED_ORDERS, CANCELLED_LINES, TASK_ORDERS = 180_519, 65_752, 2_855, 7_754, 62_897
# requirements.md, section 4 and AC15: the blocks of the split, non-cancelled orders, their lines and the cancelled orders of each
BLOCK_ORDERS = {"train": 29_894, "purge_1": 375, "valid_v1": 4_649, "valid_v2": 5_032, "purge_2": 377, "test": 14_527, "post_break": 8_043}
BLOCK_LINES = {"train": 89_531, "purge_1": 1_150, "valid": 29_199, "purge_2": 1_094, "test": 43_598, "post_break": 8_193}
BLOCK_CANCELLED = {"train": 1_355, "purge_1": 22, "valid": 436, "purge_2": 12, "test": 670, "post_break": 360}
DELIVERIES = 15                                   # the launch, 13 monthly deliveries and the close (D09)
LAUNCH_AS_OF, TEST_AS_OF = "2017-01-07", "2017-10-31"   # the test is evaluated by M2017-10, the first delivery on or after its end plus the purge (plan section 3)
COSTS = {"alert_cost": 5.0, "late_cost": 20.0, "effect": 0.5}   # D14: the defaults the test is evaluated with
SEALED_CRITERIA = tuple(f"C{i:02d}" for i in range(1, 10))      # C10 is informative and not sealed
CRITERION_FIELDS = ("value", "baseline_value", "ci_low", "ci_high")
SEALED_METHODS = ("model", "b2_mode", "b5_mode_noon")
# RNF02 and RNF03, the limits the requirements set on this machine. Nothing is timed or measured here: the test compares them with the times and peaks the sealed run
# recorded (dim_pipeline_run.parquet and the state file of each run), so it gives the same verdict on any machine (review of U25, R3-hardcoded-thresholds; D83)
MAX_REPLAY_SECONDS, MAX_LAUNCH_SECONDS, MAX_MONTHLY_SECONDS = 600, 180, 60
MAX_WORKING_SET_MB = 1.5 * 1024                   # 1.5 GB, in the megabytes of sysinfo (1 MB = 1,048,576 bytes)


def _need(path: Path) -> Path:
    assert path.is_file(), f"{path} is missing: the outputs of the real run are not there (python -m latedel replay, then python -m latedel run)"
    return path


def _json(path: Path) -> dict:
    return json.loads(_need(path).read_text(encoding="utf-8"))


def _table(outputs: Path, name: str):
    return pq.read_table(_need(outputs / f"{name}.parquet")).to_pandas()


def _round(value, decimals: int):
    if value is None or isinstance(value, bool):
        return value
    return None if isinstance(value, float) and math.isnan(value) else round(float(value), decimals)


def _seconds(row) -> float:
    return (datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])).total_seconds()


def test_real_counts_seal_and_summary_consistency(cfg):
    paths = cfg.paths
    outputs, site = Path(paths.outputs_dir), Path(paths.site_dir)
    # every versioned output of the run is there (plan section 2)
    for table in MODEL.tables:
        _need(outputs / f"{table.name}.parquet")
    for name in (*SITE_FILES, MANIFEST_FILE):
        _need(site / name)
    for path in (paths.dashboard_html, paths.seal, Path(paths.reports_dir) / "evaluation_summary.json", Path(paths.reports_dir) / "evaluation_summary.md",
                 Path(paths.reports_dir) / "validation" / "latest.json", Path(paths.reports_dir) / "validation" / "latest.md"):
        _need(Path(path))
    docs = {name: _json(site / name) for name in SITE_FILES}
    summary, rules, algorithms = docs["summary.json"], docs["validation_rules.json"], docs["algorithms.json"]
    evaluation = _json(Path(paths.reports_dir) / "evaluation_summary.json")
    fact_order = _table(outputs, "fact_order")

    # AC06: the canonical read of the real file
    headline, totals = summary["headline"], rules["totals"]
    assert (headline["rows"], headline["orders"], headline["orders_cancelled"], headline["orders_task"]) == (LINES, ORDERS, CANCELLED_ORDERS, TASK_ORDERS)
    assert (totals["rows_raw"], totals["orders_raw"], totals["orders_cancelled"], totals["rows_cancelled"], totals["orders_task"]) == (
        LINES, ORDERS, CANCELLED_ORDERS, CANCELLED_LINES, TASK_ORDERS)
    assert (totals["deliveries_loaded"], totals["deliveries_rejected"], totals["personal_columns_dropped"], totals["leaking_columns_set_aside"]) == (DELIVERIES, 0, 15, 8)
    assert len(fact_order) == ORDERS and fact_order["order_id"].is_unique and int(fact_order["n_lines"].sum()) == LINES
    cancelled = fact_order[fact_order["outcome_status"] == "cancelled"]
    assert len(cancelled) == CANCELLED_ORDERS and int(cancelled["n_lines"].sum()) == CANCELLED_LINES
    assert (fact_order["outcome_status"] == "open").sum() == 0, "after the close every outcome is known"

    # AC08: the label is real days above scheduled days in every non-cancelled order (V08), and every outcome is known within the purge (V09)
    by_rule = {r["rule_id"]: r for r in rules["rules"]}
    for rule_id in ("V08", "V09"):
        assert (by_rule[rule_id]["status"], by_rule[rule_id]["rows"], by_rule[rule_id]["orders"]) == ("pass", 0, 0), by_rule[rule_id]
    assert not any(r["status"] == "fail" and r["severity"] == "blocking" for r in rules["rules"])
    # I02 and I04 say the follow-up of the whole period once, as alerts.json does (summed over the runs they said 66,761 and 3,156 alerts)
    alerts = docs["alerts.json"]
    assert (by_rule["I02"]["rows"], by_rule["I02"]["orders"]) == (sum(m["alerts"] for m in alerts["monthly"]), sum(m["orders"] for m in alerts["monthly"]))
    assert by_rule["I04"]["rows"] == alerts["cancelled"]["alerts_on_later_cancelled"]
    closed = fact_order[fact_order["outcome_status"] == "closed"]
    assert len(closed) == TASK_ORDERS and closed["is_late"].notna().all() and closed["real_days"].notna().all()
    assert headline["late_share_task"] == round(int(closed["is_late"].sum()) / TASK_ORDERS, 3)

    # AC15: the blocks with their exact edges, the purged orders in no block, every label of training known before validation and of validation before the launch
    split = {b["block"]: b for b in algorithms["split"]}
    assert {block: split[block]["orders"] for block in BLOCK_ORDERS} == BLOCK_ORDERS
    assert set(fact_order["block"]) == set(BLOCK_ORDERS), "no order outside the plan's blocks"
    assert closed.groupby("block").size().to_dict() == BLOCK_ORDERS
    merged = fact_order.assign(block=fact_order["block"].replace({"valid_v1": "valid", "valid_v2": "valid"}))
    assert merged[merged["outcome_status"] == "closed"].groupby("block")["n_lines"].sum().to_dict() == BLOCK_LINES
    assert merged[merged["outcome_status"] == "cancelled"].groupby("block").size().to_dict() == BLOCK_CANCELLED
    order_date = fact_order["order_date_key"].astype(str).str.replace(r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", regex=True)
    s, day = cfg.split, timedelta(days=1)
    edges = {
        "train": (s.train_start, s.train_end), "purge_1": (s.train_end + day, s.valid_start - day), "valid_v1": (s.valid_start, s.valid_v2_start - day),
        "valid_v2": (s.valid_v2_start, s.valid_end), "purge_2": (s.valid_end + day, s.test_start - day), "test": (s.test_start, s.test_end),
        "post_break": (s.post_break_start, s.post_break_end),
    }
    assert list(edges) == list(BLOCK_ORDERS) and (s.valid_start - s.train_end).days - 1 == (s.test_start - s.valid_end).days - 1 == s.purge_days
    for block, (first, last) in edges.items():
        dates = order_date[fact_order["block"] == block]
        assert (dates.min(), dates.max()) == (first.isoformat(), last.isoformat()), block
        assert split[block]["start"] == first.isoformat() and split[block]["end"] == last.isoformat()
    known = closed.assign(known=closed["outcome_known_date"].astype(str).str[:10])
    assert known.loc[known["block"] == "train", "known"].max() < s.valid_start.isoformat()
    assert known.loc[known["block"].isin(["valid_v1", "valid_v2"]), "known"].max() <= LAUNCH_AS_OF

    # the seal: the launch and the test, each written once, with the default costs and the pinned source
    seal = read_seal(Path(paths.seal))
    assert seal is not None and set(seal) == {"version", "launch", "test"}
    launch, test = seal["launch"], seal["test"]
    assert launch["as_of"] == LAUNCH_AS_OF and launch["source_sha256"] == cfg.source.accepted_sha256 and launch["costs"] == COSTS
    assert test["as_of"] == TEST_AS_OF and test["config_sha256"] == launch["config_sha256"]
    assert [c["id"] for c in test["criteria"]] == list(SEALED_CRITERIA) and set(test["metrics"]) == set(SEALED_METHODS)
    runs = _table(outputs, "dim_pipeline_run").sort_values("started_at").reset_index(drop=True)
    run_of = dict(zip(runs["delivery_id"], runs["run_key"]))
    assert launch["run_key"] == run_of["L2017-01-07"] and test["run_key"] == run_of["M2017-10"]
    assert summary["evaluation_status"] == "sealed" and summary["status"] == "ok"
    assert summary["seal"] == {"config_sha256": launch["config_sha256"], "launch_run_key": launch["run_key"], "test_run_key": test["run_key"], "test_as_of": TEST_AS_OF}
    model = _table(outputs, "dim_model")
    assert len(model) == 1 and model["config_sha256"].iloc[0] == launch["config_sha256"] and model["seal_state"].iloc[0] == "sealed"
    assert headline["threshold"] == round(launch["threshold"], 2) == round(float(model["threshold"].iloc[0]), 2)
    assert {k: headline[k] for k in COSTS} == COSTS and evaluation["costs"] == COSTS

    # the summary is the seal: the same criteria and metrics of the test, in the report, the site file and the Parquet table
    assert evaluation["state"] == "evaluated" and evaluation["post_seal"] == {"criteria": [], "metrics": {}}
    assert evaluation["run_key"] == summary["run_key"] == run_of["C2018-02"]
    reported = {c["id"]: c for c in evaluation["criteria"]}
    published = {c["id"]: c for c in summary["criteria"]}
    stored = _table(outputs, "fact_evaluation_summary")
    stored = {r["criterion_id"]: r for r in stored[stored["seal_state"] == "sealed"].to_dict("records")}
    assert list(reported) == list(published) == [*SEALED_CRITERIA, "C10"] and set(stored) == set(reported)
    for sealed in test["criteria"]:
        cid = sealed["id"]
        assert reported[cid]["passed"] is sealed["passed"] is published[cid]["passed"] and isinstance(sealed["passed"], bool), cid
        assert bool(stored[cid]["passed"]) is sealed["passed"], cid
        for key in CRITERION_FIELDS:
            assert reported[cid][key] == _round(sealed[key], 6), (cid, key)
            assert published[cid][key] == _round(sealed[key], 4), (cid, key)
        says = reported[cid]["verdict_es"]
        assert ("no supera" in says) is (not sealed["passed"]) and published[cid]["verdict_es"] == says == stored[cid]["verdict_es"], cid
    assert reported["C10"]["passed"] is None and published["C10"]["passed"] is None
    assert evaluation["failed"] == [c["id"] for c in test["criteria"] if not c["passed"]]
    markdown = _need(Path(paths.reports_dir) / "evaluation_summary.md").read_text(encoding="utf-8")
    assert all(cid in markdown for cid in evaluation["failed"]) and ("no supera" in markdown or not evaluation["failed"])
    for method in SEALED_METHODS:
        assert set(evaluation["metrics"]["test"][method]) == set(test["metrics"][method]), method
        for metric, value in test["metrics"][method].items():
            assert evaluation["metrics"]["test"][method][metric] == _round(value, 6), (method, metric)
        assert headline["auc"][method] == _round(test["metrics"][method]["auc"], 3)
        assert headline["net_benefit_per_1000"][method] == _round(test["metrics"][method]["nb_per_1000"], 1)
    assert (headline["test_alert_share"], headline["test_precision"], headline["test_recall"]) == tuple(
        _round(test["metrics"]["model"][k], 3) for k in ("flagged_share", "precision", "recall"))
    assert "post_break" in evaluation["metrics"] and set(evaluation["metrics"]["post_break"]) >= set(SEALED_METHODS), "the close evaluated the post-break block (C10)"

    # AC33 and RNF10: each Parquet under its cap, the site files under theirs and one export, the star schema read back without a problem
    sizes = {p.name: p.stat().st_size for p in outputs.glob("*.parquet")}
    assert len(sizes) == len(MODEL.tables) and max(sizes.values()) <= MAX_PARQUET_BYTES, sizes
    assert sum((site / name).stat().st_size for name in SITE_FILES) <= cfg.site.max_total_bytes
    assert verify_site(site) == [] and verify_star_schema(outputs, MODEL) == []

    # AC33, RNF02 and RNF03: the 15 deliveries and the run with no delivery left, timed and measured by the pipeline itself, each memory figure with its counter
    assert list(runs["status"]) == ["ok"] * DELIVERIES + ["no_delivery"], list(runs["status"])
    assert list(runs["delivery_id"].iloc[:DELIVERIES]) == ["L2017-01-07", *[f"M2017-{m:02d}" for m in range(1, 13)], "M2018-01", "C2018-02"]
    replay = (datetime.fromisoformat(runs["finished_at"].iloc[-1]) - datetime.fromisoformat(runs["started_at"].iloc[0])).total_seconds()
    assert replay <= MAX_REPLAY_SECONDS, f"the complete replay took {replay} s"
    durations = {row["delivery_id"]: _seconds(row) for row in runs.iloc[:DELIVERIES].to_dict("records")}
    assert durations["L2017-01-07"] <= MAX_LAUNCH_SECONDS, durations
    assert all(seconds <= MAX_MONTHLY_SECONDS for delivery, seconds in durations.items() if delivery.startswith("M")), durations
    ok = runs.iloc[:DELIVERIES]
    assert ok["peak_working_set_mb"].notna().all() and ok["peak_working_set_mb"].max() <= MAX_WORKING_SET_MB, list(ok["peak_working_set_mb"])
    counters = set()
    for run_key in ok["run_key"]:
        state = _json(Path(paths.interim_dir) / "runs" / f"{run_key}.json")
        assert state["status"] == "ok" and set(state["tasks"]) >= {"fetch_source", "deliver", "load", "report"}, run_key
        for task_id, task in state["tasks"].items():
            assert task["seconds"] >= 0 and 0 < task["peak_working_set_mb"] <= MAX_WORKING_SET_MB, (run_key, task_id, task)
            counters.add(task["memory_counter"])
    assert counters in ({WINDOWS_COUNTER}, {POSIX_COUNTER}), counters
