"""The tasks, the state of a run and the orderly close of a run (U21, AC11, AC27, AC28, AC34, decisions D35 and D72).

The runs below walk the whole flow over the synthetic fixture (local source, the real warehouse, the real exports). The expensive ones are built once per session in
``conftest.py`` and copied: ``pipeline_interrupted`` is the launch run killed after its export, ``pipeline_closed`` the same run resumed to the end and
``pipeline_two_runs`` the launch and the delivery ``M2017-01``. A test that changes things works on a copy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from latedel import pipeline
from latedel.deliveries import delivery_plan, mark_status, publish, read_manifest
from latedel.ingest import read_interim
from latedel.pipeline import CLOSING_STEPS, TASK_IDS, RunStateError, run_all, run_task
from latedel.validate import ValidationError
from tests.conftest import relocate
from tests.fixtures.helpers import make_cfg, snapshot

FIRST = "L2017-01-07"
SECOND = "M2017-01"
THROUGH_EXPORT = list(TASK_IDS[: TASK_IDS.index("export") + 1])


# --- helpers -------------------------------------------------------------------------------------------------------------------------


def rows(config, query: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(f"{config.paths.warehouse.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def run_row(config, run_key: str) -> tuple:
    [row] = rows(config, "SELECT status, finished_at, message, orders_scored, alerts, peak_working_set_mb, peak_commit_mb FROM pipeline_runs WHERE run_key = ?", (run_key,))
    return row


def exported_runs(config) -> dict[str, str]:
    table = pd.read_parquet(config.paths.outputs_dir / "dim_pipeline_run.parquet")
    return dict(zip(table["run_key"], table["status"], strict=True))


def summary(config) -> dict:
    return json.loads((config.paths.site_dir / "summary.json").read_text(encoding="utf-8"))


def entry(config, delivery_id: str):
    [found] = [e for e in read_manifest(config.paths.raw_dir / "deliveries" / "manifest.json") if e.delivery_id == delivery_id]
    return found


def state(config, run_key: str) -> dict:
    return json.loads((config.paths.interim_dir / "runs" / f"{run_key}.json").read_text(encoding="utf-8"))


def outputs(config) -> dict[str, str]:
    """The SHA-256 of the warehouse and of every file the flow writes outside the interim folder (the files a rejected or refused run must not touch): the exports, the
    HTML, the reports, the seal and the model artifact. The validation reports are left out: a rejection writes its own (that is its record); a refused task writes nothing,
    and the tests of the refusals compare the reports folder whole."""
    files = {"warehouse": hashlib.sha256(config.paths.warehouse.read_bytes()).hexdigest()} if config.paths.warehouse.exists() else {}
    for name, folder in (("powerbi", config.paths.outputs_dir), ("site", config.paths.site_dir), ("reports", config.paths.reports_dir), ("models", config.paths.models_dir)):
        files.update({f"{name}/{k}": v for k, v in snapshot(folder).items() if not k.startswith("validation/")})
    html = config.paths.dashboard_html
    return files | ({"html": hashlib.sha256(html.read_bytes()).hexdigest()} if html.exists() else {})


def records(config, run_key: str) -> dict:
    """Every record that says what a run is: its row in SQLite, its row in the exported run table, its state file, the manifest and the summary (as bytes)."""
    [row] = rows(config, "SELECT * FROM pipeline_runs WHERE run_key = ?", (run_key,))
    exported = pd.read_parquet(config.paths.outputs_dir / "dim_pipeline_run.parquet")
    return {
        "sqlite": row, "exported": exported[exported["run_key"] == run_key].to_json(), "state": (config.paths.interim_dir / "runs" / f"{run_key}.json").read_bytes(),
        "manifest": (config.paths.raw_dir / "deliveries" / "manifest.json").read_bytes(), "summary": (config.paths.site_dir / "summary.json").read_bytes(),
    }


class Killed(BaseException):
    """What a process killed in the middle of a step looks like to the code that follows it: nothing runs after it (not even the failure handler)."""


def kill_the_process_after(monkeypatch, step: str) -> None:
    """Make the process die right after the closing step `step` ends: the next step (or, after the last one, the save of the task) raises ``Killed``, and the failure
    handler, which a dead process would not run, does nothing."""
    monkeypatch.setattr(pipeline, "_record_failure", lambda *args, **kwargs: None)

    def die(*args, **kwargs):
        raise Killed(f"killed after {step}")

    later = CLOSING_STEPS[CLOSING_STEPS.index(step) + 1 :]
    if later:
        monkeypatch.setitem(pipeline._CLOSERS, later[0], die)
    else:
        monkeypatch.setattr(pipeline, "_save_task", only_the_report(die))


def only_the_report(replacement):
    """A stand-in for ``_save_task`` that acts on the task ``report`` (the last one) and saves every other task as usual."""
    real = pipeline._save_task

    def save(ctx, task_id, *args, **kwargs):
        return replacement(ctx, task_id, *args, **kwargs) if task_id == "report" else real(ctx, task_id, *args, **kwargs)

    return save


# --- AC27: the state of the run ----------------------------------------------------------------------------------------------------------


def test_run_interrupted_after_export_is_not_ok(pipeline_interrupted, fixture_csv):
    config = make_cfg(pipeline_interrupted, fixture_csv)
    assert (config.paths.outputs_dir / "fact_order.parquet").is_file() and (config.paths.site_dir / "algorithms.json").is_file()
    status, finished, _message, _scored, _alerts, _working, _commit = run_row(config, "r1")
    assert (status, finished) == ("running", None)
    assert exported_runs(config) == {"r1": "running"}
    assert summary(config)["status"] == "running" and summary(config)["run_key"] == "r1"
    assert state(config, "r1")["status"] == "running" and "report" not in state(config, "r1")["tasks"]
    assert entry(config, FIRST).status == "published" and entry(config, FIRST).run_key is None
    assert not config.paths.dashboard_html.exists() or ">ok<" not in config.paths.dashboard_html.read_text(encoding="utf-8")
    assert [s for (s,) in rows(config, "SELECT status FROM pipeline_runs")] == ["running"]


def test_resuming_the_interrupted_run_closes_it_everywhere(pipeline_closed, fixture_csv):
    config = make_cfg(pipeline_closed, fixture_csv)
    status, finished, message, scored, alerts, working, commit = run_row(config, "r1")
    assert (status, message) == ("ok", "ok") and finished is not None and (scored, alerts) == (0, 0) and working > 0  # the launch scores no order
    assert commit is None or commit >= working   # the committed memory exists on Windows only and is informative
    assert exported_runs(config) == {"r1": "ok"}
    assert summary(config)["status"] == "ok"
    html = config.paths.dashboard_html.read_text(encoding="utf-8")
    assert "r1" in html and ">ok<" in html
    assert (entry(config, FIRST).status, entry(config, FIRST).run_key) == ("loaded", "r1")
    assert state(config, "r1")["status"] == "ok"
    assert (config.paths.reports_dir / "evaluation_summary.json").is_file() and (config.paths.reports_dir / "validation" / "latest.json").is_file()


def test_a_monthly_run_records_the_orders_it_scored_and_its_alerts(pipeline_two_runs, fixture_csv):
    config = make_cfg(pipeline_two_runs, fixture_csv)
    scored, alerts = rows(config, "SELECT COUNT(*), SUM(is_alert) FROM model_scores")[0]
    status, _finished, _message, run_scored, run_alerts, _w, _c = run_row(config, "r2")
    assert status == "ok" and scored > 0 and (run_scored, run_alerts) == (scored, alerts)
    assert state(config, "r2")["tasks"]["score"]["scored"] == scored
    assert state(config, "r1")["tasks"]["score"]["skipped"] == "launch"   # the launch is history for the model, not new orders
    assert exported_runs(config) == {"r1": "ok", "r2": "ok"} and summary(config)["run_key"] == "r2"


def test_the_run_closes_in_the_order_of_closing_steps(pipeline_interrupted, fixture_csv, tmp_path, monkeypatch):
    """SQLite first, then the run table alone, the summary alone, the HTML, the manifest, and last the state file (D72): what each write finds when it starts."""
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    events: list[tuple] = []

    def probe(name: str):
        real = pipeline._CLOSERS[name]

        def wrapper(ctx):
            html = config.paths.dashboard_html
            events.append((name, run_row(config, "r1")[0], exported_runs(config)["r1"], summary(config)["status"], html.exists() and ">ok<" in html.read_text(encoding="utf-8"),
                           entry(config, FIRST).status, state(config, "r1")["status"]))
            return real(ctx)

        monkeypatch.setitem(pipeline._CLOSERS, name, wrapper)

    for name in CLOSING_STEPS:
        probe(name)
    run_all(config, start="render_dashboard", run_key="r1")
    assert tuple(e[0] for e in events) == CLOSING_STEPS == ("sqlite", "run_table", "summary", "html", "manifest")
    assert events == [
        ("sqlite", "running", "running", "running", False, "published", "running"),
        ("run_table", "ok", "running", "running", False, "published", "running"),
        ("summary", "ok", "ok", "running", False, "published", "running"),
        ("html", "ok", "ok", "ok", False, "published", "running"),
        ("manifest", "ok", "ok", "ok", True, "published", "running"),
    ]
    assert (entry(config, FIRST).status, state(config, "r1")["status"]) == ("loaded", "ok")   # the state file is the last to say it


# --- the crash windows between the closing writes (item 4; the way D62 tested the seal's) ---------------------------------------------------------


WINDOWS = {   # after this step ends and the process dies: SQLite, run table, summary, HTML says ok, manifest, state file
    "sqlite": ("ok", "running", "running", False, "published", "running"),
    "run_table": ("ok", "ok", "running", False, "published", "running"),
    "summary": ("ok", "ok", "ok", False, "published", "running"),
    "html": ("ok", "ok", "ok", True, "published", "running"),
    "manifest": ("ok", "ok", "ok", True, "loaded", "running"),
}


def what_the_files_say(config) -> tuple:
    html = config.paths.dashboard_html
    return (
        run_row(config, "r1")[0], exported_runs(config)["r1"], summary(config)["status"], html.exists() and ">ok<" in html.read_text(encoding="utf-8"),
        entry(config, FIRST).status, state(config, "r1")["status"],
    )


@pytest.mark.parametrize("step", CLOSING_STEPS)
def test_a_process_killed_between_two_closing_writes_leaves_a_window_that_repeating_report_closes(pipeline_interrupted, pipeline_closed, fixture_csv, tmp_path, monkeypatch, step):
    """Whatever the step after which the process dies, the state file still says ``running`` (so the run is not closed and ``report`` may run again), every file that
    was not written yet says what it said, and repeating ``report`` leaves the same final state as a run that was never interrupted."""
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    with monkeypatch.context() as killed:
        kill_the_process_after(killed, step)
        with pytest.raises(Killed):
            run_all(config, start="render_dashboard", run_key="r1")
    assert what_the_files_say(config) == WINDOWS[step]
    # the delivery is still pending for any other run only if the manifest does not say loaded; either way the run itself is open and resumable
    run_all(config, start="report", run_key="r1")
    reference = make_cfg(pipeline_closed, fixture_csv)
    assert what_the_files_say(config) == what_the_files_say(reference) == ("ok", "ok", "ok", True, "loaded", "ok")
    assert without_run_fields(summary(config)) == without_run_fields(summary(reference))
    assert (entry(config, FIRST).run_key, run_row(config, "r1")[2]) == ("r1", "ok")
    expected = pd.read_parquet(reference.paths.outputs_dir / "dim_pipeline_run.parquet").drop(columns=["started_at", "finished_at", "peak_working_set_mb", "peak_commit_mb"], errors="ignore")
    got = pd.read_parquet(config.paths.outputs_dir / "dim_pipeline_run.parquet").drop(columns=["started_at", "finished_at", "peak_working_set_mb", "peak_commit_mb"], errors="ignore")
    pd.testing.assert_frame_equal(got.reset_index(drop=True), expected.reset_index(drop=True), check_like=True)


@pytest.mark.parametrize("step", CLOSING_STEPS[:-1])
def test_an_error_between_two_closing_writes_marks_the_run_failed_in_every_file_that_exists(pipeline_interrupted, fixture_csv, tmp_path, monkeypatch, step):
    """The same windows with an exception instead of a kill: nothing keeps saying ``ok``, SQLite, the run table and the summary say ``failed``, the delivery is not marked
    loaded, and the run resumes from ``report``."""
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)

    def boom(*args, **kwargs):
        raise RuntimeError(f"boom after {step}")

    with monkeypatch.context() as failing:
        failing.setitem(pipeline._CLOSERS, CLOSING_STEPS[CLOSING_STEPS.index(step) + 1], boom)
        with pytest.raises(RuntimeError, match="boom after"):
            run_all(config, start="render_dashboard", run_key="r1")
    status, finished, message, *_ = run_row(config, "r1")
    assert status == "failed" and finished is not None and "boom after" in message
    assert exported_runs(config) == {"r1": "failed"} and summary(config)["status"] == "failed"
    assert state(config, "r1")["status"] == "failed" and "boom after" in state(config, "r1")["tasks"]["report"]["traceback"]
    html = config.paths.dashboard_html.read_text(encoding="utf-8")
    assert ">failed<" in html and ">ok<" not in html
    assert entry(config, FIRST).status == "published"   # the manifest is the last write of the close: it did not happen
    run_all(config, start="report", run_key="r1")
    assert what_the_files_say(config) == ("ok", "ok", "ok", True, "loaded", "ok")


def test_an_error_saving_the_state_file_after_the_close_leaves_every_file_ok_and_the_run_open_for_report(pipeline_interrupted, fixture_csv, tmp_path, monkeypatch):
    """The state file is the last write: when it is the one that fails, the close itself is complete (all five steps), so no file is rewritten as ``failed`` (the manifest
    already says ``loaded`` and cannot be taken back); the state file keeps ``running`` and repeating ``report`` records the end (D72)."""
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)

    def disk_full(*args, **kwargs):
        raise OSError("no space left on the device")

    with monkeypatch.context() as failing:
        failing.setattr(pipeline, "_save_task", only_the_report(disk_full))
        with pytest.raises(OSError, match="no space"):
            run_all(config, start="render_dashboard", run_key="r1")
    assert what_the_files_say(config) == WINDOWS["manifest"]
    run_all(config, start="report", run_key="r1")
    assert what_the_files_say(config) == ("ok", "ok", "ok", True, "loaded", "ok")


def test_a_no_delivery_run_updates_only_the_exported_run_table_and_the_html(pipeline_closed, fixture_csv, tmp_path):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    stop_the_plan_at_no_delivery(config)
    before = outputs(config)
    done = run_all(config, run_key="r2")
    assert [d["task"] for d in done] == ["fetch_source", "deliver"] and done[-1]["result"]["delivered"] is False
    assert run_row(config, "r2")[0] == "no_delivery" and run_row(config, "r2")[1] is not None
    assert exported_runs(config) == {"r1": "ok", "r2": "no_delivery"}
    after = outputs(config)
    changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    assert changed == {"warehouse", "powerbi/dim_pipeline_run.parquet", "html"}   # the HTML shows the runs, so it says this one too
    html = config.paths.dashboard_html.read_text(encoding="utf-8")
    assert "r2" in html and "no_delivery" in html and "r1" in html
    assert state(config, "r2")["status"] == "no_delivery"


def stop_the_plan_at_no_delivery(config) -> None:
    """Mark every delivery of the plan as loaded in the manifest, so that the next run finds nothing to deliver."""
    frames, _report = read_interim(config.paths.interim_dir)
    folder = config.paths.raw_dir / "deliveries"
    for spec in delivery_plan(config.deliveries):
        publish(spec, frames, folder, source_sha256=state(config, "r1")["tasks"]["fetch_source"]["sha256"])
        if (entry_ := [e for e in read_manifest(folder / "manifest.json") if e.delivery_id == spec.delivery_id][0]).status != "loaded":
            mark_status(folder / "manifest.json", spec.delivery_id, "loaded", entry_.run_key or "earlier")


# --- AC11: a rejection changes nothing ---------------------------------------------------------------------------------------------------


def tamper_published(config, delivery_id: str) -> None:
    """Drop one order from the published ``orders.parquet`` of the delivery: its rows and its SHA-256 are no longer the manifest's (V24)."""
    path = config.paths.raw_dir / "deliveries" / delivery_id / "orders.parquet"
    table = pq.read_table(path)
    pq.write_table(pa.Table.from_pandas(table.to_pandas().iloc[1:], schema=table.schema, preserve_index=False).replace_schema_metadata(table.schema.metadata), path)


def test_a_rejected_delivery_changes_no_byte_of_the_warehouse_or_the_exports(pipeline_closed, fixture_csv, tmp_path):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    run_all(config, only=["fetch_source", "deliver"], run_key="r2")
    tamper_published(config, SECOND)
    before = outputs(config)
    with pytest.raises(ValidationError) as caught:
        run_all(config, start="validate", run_key="r2")
    assert "V24" in [r.rule_id for r in caught.value.report.results if r.severity.value == "blocking" and not r.passed]
    assert outputs(config) == before   # the warehouse, every Parquet, the HTML, the site JSON, the reports and the seal
    assert [k for (k,) in rows(config, "SELECT run_key FROM pipeline_runs")] == ["r1"]
    assert state(config, "r2")["status"] == "rejected" and entry(config, SECOND).status == "rejected" and entry(config, FIRST).status == "loaded"
    report = json.loads((config.paths.reports_dir / "validation" / "history" / "r2.json").read_text(encoding="utf-8"))
    assert report["blocking_failed"] is True


def test_a_source_that_is_not_the_registered_one_is_rejected_before_anything_is_written(pipeline_closed, fixture_csv, tmp_path, malformed):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    other = malformed("bad_date")
    config = make_cfg(tmp_path / "p", other)
    before = outputs(config)
    with pytest.raises(ValidationError) as caught:
        run_all(config, run_key="r2")
    assert [r.rule_id for r in caught.value.report.results if not r.passed and r.severity.value == "blocking"] == ["V01"]
    assert outputs(config) == before   # nothing exported or stored changed
    assert [k for (k,) in rows(config, "SELECT run_key FROM pipeline_runs")] == ["r1"]
    assert state(config, "r2")["status"] == "rejected"


def test_a_rejected_first_run_never_creates_the_warehouse_or_the_exports(fixture_csv, tmp_path, malformed):
    config = make_cfg(tmp_path / "p", malformed("missing_column"))
    with pytest.raises(ValidationError) as caught:
        run_all(config, run_key="r1")
    assert "V04" in [r.rule_id for r in caught.value.report.results if not r.passed]
    assert not config.paths.warehouse.exists() and not config.paths.outputs_dir.exists() and not config.paths.site_dir.exists()
    assert not config.paths.dashboard_html.exists() and not config.paths.seal.exists() and not config.paths.models_dir.exists()
    assert state(config, "r1")["status"] == "rejected"


def test_a_new_run_refuses_a_delivery_that_a_run_without_a_close_already_loaded(pipeline_interrupted, fixture_csv, tmp_path):
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    before = outputs(config)
    with pytest.raises(RunStateError, match=r"r1.*--run-key r1"):
        run_all(config, run_key="r2")
    assert outputs(config) == before and [k for (k,) in rows(config, "SELECT run_key FROM pipeline_runs")] == ["r1"]


# --- AC34: the same content twice ----------------------------------------------------------------------------------------------------------

RUN_COLUMNS = {"run_key", "launch_run_key", "test_run_key", "started_at", "finished_at", "peak_working_set_mb", "peak_commit_mb", "message", "seconds", "run_status", "latedel.run_key"}


def without_run_fields(value):
    """A JSON document without the fields that name the run (its key, times, memory and seconds)."""
    if isinstance(value, dict):
        return {k: without_run_fields(v) for k, v in value.items() if k not in RUN_COLUMNS}
    if isinstance(value, list):
        return [without_run_fields(v) for v in value]
    return value


def content(config) -> dict[str, object]:
    """What the exports say, without the columns of the run: every Parquet as sorted rows and every JSON of the site as a document."""
    found: dict[str, object] = {}
    for path in sorted(config.paths.outputs_dir.glob("*.parquet")):
        frame = pq.read_table(path).to_pandas()
        if path.stem == "dim_pipeline_run":
            found[path.name] = len(frame)    # this table is the run: only how many rows it has
            continue
        frame = frame.drop(columns=[c for c in RUN_COLUMNS if c in frame.columns])
        found[path.name] = frame.sort_values(list(frame.columns), kind="stable").reset_index(drop=True).to_json()
    for path in sorted(config.paths.site_dir.glob("*.json")):
        found[path.name] = json.dumps(without_run_fields(json.loads(path.read_text(encoding="utf-8"))), sort_keys=True)
    return found


def test_two_replays_give_the_same_content_in_every_parquet_and_json(pipeline_two_runs, fixture_csv, tmp_path):
    from latedel.pipeline import replay

    second = make_cfg(tmp_path / "p", fixture_csv)
    assert len(replay(second, until=SECOND)) == 2
    first = make_cfg(pipeline_two_runs, fixture_csv)
    a, b = content(first), content(second)
    assert a.keys() == b.keys() and len(a) >= 25   # 20 tables and 5 site files
    assert [name for name in a if a[name] != b[name]] == []
    seals = [without_run_fields(json.loads(c.paths.seal.read_text(encoding="utf-8"))) for c in (first, second)]
    assert seals[0] == seals[1] and (first.paths.models_dir / "..").exists()


# --- the wrapper and the entry points -------------------------------------------------------------------------------------------------------


def test_every_task_records_its_seconds_and_the_peak_memory_of_the_process_with_its_counter(pipeline_closed, fixture_csv):
    tasks = state(make_cfg(pipeline_closed, fixture_csv), "r1")["tasks"]
    assert list(tasks) == list(TASK_IDS)
    for task_id, result in tasks.items():
        assert result["seconds"] >= 0 and result["peak_working_set_mb"] > 0, task_id
        assert result["memory_counter"] in ("PeakWorkingSetSize", "ru_maxrss"), task_id
    peaks = [t["peak_working_set_mb"] for t in tasks.values()]
    assert peaks == sorted(peaks)   # a peak never falls


def test_run_task_runs_one_task_of_a_run_and_keeps_its_result_for_the_next(pipeline_source, fixture_csv, tmp_path):
    config = relocate(pipeline_source, tmp_path / "p", fixture_csv)
    first = run_task("fetch_source", "solo", config)
    assert first["built"] is False and first["orders"] > 0
    delivered = run_task("deliver", "solo", config)
    assert delivered["delivered"] is True and delivered["delivery_id"] == FIRST and delivered["as_of"] == "2017-01-07"
    assert list(state(config, "solo")["tasks"]) == ["fetch_source", "deliver"]
    validated = run_task("validate", "solo", config)   # it found the delivery in the state of its run
    assert validated["delivery_id"] == FIRST and validated["orders"] == delivered["orders"]


def test_unknown_tasks_and_incompatible_selections_are_usage_errors_that_write_nothing(pipeline_source, fixture_csv, tmp_path):
    config = relocate(pipeline_source, tmp_path / "p", fixture_csv)
    before = snapshot(tmp_path / "p")
    for call in (
        lambda: run_all(config, only=["load"], start="load", run_key="x"), lambda: run_all(config, only=["nonsense"], run_key="x"),
        lambda: run_all(config, start="nonsense", run_key="x"), lambda: run_all(config, only=[], run_key="x"), lambda: run_task("nonsense", "x", config),
    ):
        with pytest.raises(pipeline.UsageError):
            call()
    assert snapshot(tmp_path / "p") == before


def test_the_tasks_are_those_of_the_plan_in_its_order():
    assert TASK_IDS == (
        "fetch_source", "deliver", "validate", "load", "fit_model", "score", "track", "evaluate", "export", "render_dashboard", "report",
    )
    assert [(t.retries, t.retry_delay_min, t.timeout_min, t.short_circuit) for t in pipeline.TASKS] == [
        (3, 10, 20, False), (0, 0, 5, True), (0, 0, 10, False), (1, 5, 20, False), (1, 5, 20, False), (1, 5, 10, False), (1, 5, 10, False), (1, 5, 20, False),
        (1, 5, 10, False), (1, 5, 10, False), (1, 5, 5, False),
    ]


def test_a_load_retried_after_it_committed_only_records_the_run(pipeline_interrupted, fixture_csv, tmp_path):
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    before = rows(config, "SELECT COUNT(*) FROM raw_orders")[0][0]
    again = run_task("load", "r1", config)
    assert again["resumed"] is True and rows(config, "SELECT COUNT(*) FROM raw_orders")[0][0] == before
    assert run_row(config, "r1")[0] == "running"


def test_a_run_key_that_is_empty_once_made_safe_is_refused():
    assert pipeline.make_run_key("manual__2026-11-01T06:00:00+00:00") == "manual__2026-11-01T06_00_00_00_00"
    with pytest.raises(ValueError, match="vacía"):
        pipeline.make_run_key("///")


def test_a_task_after_a_run_with_no_delivery_is_refused_with_the_reason_not_with_a_key_error(pipeline_closed, fixture_csv, tmp_path):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    stop_the_plan_at_no_delivery(config)
    run_all(config, only=["fetch_source", "deliver"], run_key="r2")
    state_before = (config.paths.interim_dir / "runs" / "r2.json").read_bytes()
    with pytest.raises(pipeline.RunRefusedError, match=r"r2.*cerrada.*no_delivery.*validate"):
        run_task("validate", "r2", config)
    assert (config.paths.interim_dir / "runs" / "r2.json").read_bytes() == state_before


def test_a_peak_that_cannot_be_measured_never_discards_the_result_of_a_task(monkeypatch, caplog):
    def broken():
        raise OSError("the system will not say")

    monkeypatch.setattr(pipeline, "peak_memory", broken)
    with caplog.at_level("WARNING", logger="latedel.pipeline"):
        assert pipeline._peak() is None
    assert any("could not measure the peak memory" in r.getMessage() and r.exc_info for r in caplog.records)


def test_the_summary_a_task_gives_to_the_orchestrator_is_small_flat_and_json():
    import math

    import numpy as np

    result = {"rows": np.int64(5), "bad": math.nan, "long": "x" * 500, "nested": {"a": 1}, "items": [1, 2], "ok": True, "none": None, "task": "other"}
    summary_ = pipeline.task_summary("score", result)
    assert summary_["task"] == "score" and list(summary_)[0] == "task"   # a key named like the task does not replace it
    assert summary_["rows"] == 5 and isinstance(summary_["rows"], int) and summary_["bad"] is None and summary_["ok"] is True and summary_["none"] is None
    assert len(summary_["long"]) == pipeline.SUMMARY_TEXT_CHARS and "nested" not in summary_ and "items" not in summary_
    json.dumps(summary_, allow_nan=False)


def test_the_state_file_is_written_whole_or_not_at_all(pipeline_source, fixture_csv, tmp_path, monkeypatch):
    config = relocate(pipeline_source, tmp_path / "p", fixture_csv)
    run_all(config, only=["fetch_source"], run_key="solo")
    path = config.paths.interim_dir / "runs" / "solo.json"
    good = path.read_bytes()

    def torn(target, data):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "write_atomic", torn)
    with pytest.raises(OSError):
        pipeline._write_state(path, {"status": "x"})
    assert path.read_bytes() == good and [p.name for p in path.parent.iterdir()] == ["solo.json"]


# --- AC28: a closed run is not reopened by another delivery ------------------------------------------------------------------------------


def attempts(config, task_id: str, how: str):
    return {
        "run_all_from": lambda: run_all(config, start=task_id, run_key="r1"), "run_all_only": lambda: run_all(config, only=[task_id], run_key="r1"),
        "run_task": lambda: run_task(task_id, "r1", config),
    }[how]


def test_closed_run_refuses_every_task_and_keeps_its_records(pipeline_closed, fixture_csv, tmp_path):
    """B1 of project 02: ``run --run-key <closed run> --from deliver`` loaded the next delivery under the closed run's key and overwrote its records. Every task, through
    every entry point, is refused before anything is written, and the records of the closed run are the same bytes (a refusal writes nothing, so one copy serves all)."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    before, kept = outputs(config), records(config, "r1")
    everything = (snapshot(config.paths.interim_dir), snapshot(config.paths.reports_dir), snapshot(config.paths.raw_dir))
    assert issubclass(pipeline.RunRefusedError, RunStateError)
    for task_id in TASK_IDS:
        for how in ("run_all_from", "run_all_only", "run_task"):
            with pytest.raises(pipeline.RunRefusedError, match=rf"r1.*cerrada.*ok.*{task_id}.*python -m latedel run") as refused:
                attempts(config, task_id, how)()
            assert "Traceback" not in str(refused.value)
            assert outputs(config) == before and records(config, "r1") == kept   # byte for byte
            assert (snapshot(config.paths.interim_dir), snapshot(config.paths.reports_dir), snapshot(config.paths.raw_dir)) == everything   # not even a validation report
    assert [e.delivery_id for e in read_manifest(config.paths.raw_dir / "deliveries" / "manifest.json")] == [FIRST]   # the next delivery was not published
    assert [k for (k,) in rows(config, "SELECT run_key FROM pipeline_runs")] == ["r1"]


def test_a_run_that_ended_without_a_delivery_refuses_every_task_again(pipeline_closed, fixture_csv, tmp_path):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    stop_the_plan_at_no_delivery(config)
    run_all(config, run_key="r2")   # ends as no_delivery
    before, kept = outputs(config), records(config, "r2")
    raw = snapshot(config.paths.raw_dir)
    for task_id in TASK_IDS:
        for call in (lambda t=task_id: run_task(t, "r2", config), lambda t=task_id: run_all(config, start=t, run_key="r2")):
            with pytest.raises(pipeline.RunRefusedError, match=r"r2.*no_delivery"):
                call()
    assert outputs(config) == before and records(config, "r2") == kept and snapshot(config.paths.raw_dir) == raw


def test_a_run_that_holds_one_delivery_does_not_take_another(pipeline_interrupted, fixture_csv, tmp_path):
    """The run ``r1`` holds the result of delivering the launch; if another run loaded it meanwhile, delivering again would swap it for M2017-01 under the same key."""
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    mark_status(config.paths.raw_dir / "deliveries" / "manifest.json", FIRST, "loaded", "other")
    before = outputs(config)
    saved = (config.paths.interim_dir / "runs" / "r1.json").read_bytes()
    with pytest.raises(pipeline.RunRefusedError, match=rf"r1.*{FIRST}.*{SECOND}"):
        run_task("deliver", "r1", config)
    assert outputs(config) == before and (config.paths.interim_dir / "runs" / "r1.json").read_bytes() == saved
    assert not (config.paths.raw_dir / "deliveries" / SECOND).exists()   # the next delivery was not even published


def test_a_run_that_is_still_open_can_deliver_its_own_delivery_again(pipeline_interrupted, fixture_csv, tmp_path):
    """The refusal does not touch a resume: the same run, with the same pending delivery, runs ``deliver`` again."""
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    again = run_task("deliver", "r1", config)
    assert again["delivered"] is True and again["delivery_id"] == FIRST
    assert run_row(config, "r1")[0] == "running"


MODEL_TASKS = ("fit_model", "score", "track", "evaluate")


def model_tables(config) -> dict[str, list]:
    return {t: rows(config, f"SELECT * FROM {t} ORDER BY 1") for t in ("model_registry", "model_scores", "tracking_monthly", "eval_metrics")}


def test_a_model_task_of_a_run_whose_delivery_is_not_loaded_is_refused_and_records_nothing(pipeline_closed, fixture_csv, tmp_path):
    """A model task calculates the latest loaded date: ``task score --run-key r2`` before ``r2`` loaded its delivery calculated the date of ``r1``."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    run_all(config, only=["fetch_source", "deliver"], run_key="r2")   # r2 holds M2017-01, which is not in the warehouse yet
    before, tables, saved = outputs(config), model_tables(config), (config.paths.interim_dir / "runs" / "r2.json").read_bytes()
    for task_id in MODEL_TASKS:
        with pytest.raises(pipeline.RunRefusedError, match=rf"r2.*{SECOND}.*2017-01-31.*2017-01-07.*{task_id}"):
            run_task(task_id, "r2", config)
        assert outputs(config) == before and model_tables(config) == tables
        assert (config.paths.interim_dir / "runs" / "r2.json").read_bytes() == saved   # no result of the older date in the run
    assert state(config, "r2")["status"] == "running"


def test_a_model_task_of_an_older_run_is_refused_when_a_later_delivery_is_loaded(pipeline_closed, fixture_csv, tmp_path):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    run_all(config, only=["fetch_source", "deliver", "validate", "load"], run_key="r2")   # the warehouse now holds up to 2017-01-31
    path = config.paths.interim_dir / "runs" / "r1.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"status": "ok"', '"status": "failed"', 1), encoding="utf-8")   # r1 open again, as if it had failed
    before, tables, saved = outputs(config), model_tables(config), path.read_bytes()
    for task_id in MODEL_TASKS:
        with pytest.raises(pipeline.RunRefusedError, match=rf"r1.*2017-01-07.*2017-01-31.*{task_id}"):
            run_task(task_id, "r1", config)
        assert outputs(config) == before and model_tables(config) == tables and path.read_bytes() == saved


def test_a_model_task_of_the_run_that_loaded_the_latest_delivery_still_runs(pipeline_interrupted, fixture_csv, tmp_path):
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    tracked = run_task("track", "r1", config)
    assert tracked["as_of"] == "2017-01-07" and state(config, "r1")["tasks"]["track"]["as_of"] == "2017-01-07"


# --- AC27: the failure handler's own writes do not replace the task's error ------------------------------------------------------------------


def boom(*args, **kwargs):
    raise RuntimeError("boom")


def failing_evaluate(monkeypatch):
    monkeypatch.setattr(pipeline, "evaluate_model", boom)


def log_of(caplog, text: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if text in r.getMessage()]


@pytest.mark.parametrize("failing_write", ["state_file", "sqlite_row", "exported_files"])
def test_failure_handler_write_errors_do_not_mask_the_task_error(pipeline_closed, fixture_csv, tmp_path, monkeypatch, caplog, failing_write):
    """The handler that records a failed task writes three things (the state file, the row of SQLite, the exported files) and each one can fail (a full disk, a locked
    database). None of them replaces the error of the task, each failure is logged once with its traceback, and the writes after it are still tried."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    failing_evaluate(monkeypatch)
    real_write, real_record = pipeline._write_state, pipeline._record

    def full_disk_for_the_failed_state(path, new):
        if new.get("status") == "failed":
            raise OSError("no space left on the device")
        real_write(path, new)

    def locked(ctx, **fields):
        if fields.get("status") == "failed":
            raise sqlite3.OperationalError("database is locked")
        real_record(ctx, **fields)

    def unwritable(*args, **kwargs):
        raise PermissionError("the file is open in another program")

    errors = {"state_file": OSError, "sqlite_row": sqlite3.OperationalError, "exported_files": PermissionError}
    if failing_write == "state_file":
        monkeypatch.setattr(pipeline, "_write_state", full_disk_for_the_failed_state)
    elif failing_write == "sqlite_row":
        monkeypatch.setattr(pipeline, "_record", locked)
    else:
        monkeypatch.setattr(pipeline, "_refresh_exports", unwritable)
    with caplog.at_level("WARNING", logger="latedel.pipeline"), pytest.raises(RuntimeError, match="boom"):   # not the OSError, the OperationalError or the PermissionError
        run_all(config, run_key="r2")
    lost = log_of(caplog, "could not")
    lost = [r for r in lost if r.exc_info is not None and r.exc_info[0] is errors[failing_write]]
    assert len(lost) >= 1 and all(r.exc_info[2] is not None for r in lost)   # logged with its traceback
    failed = [r for r in caplog.records if r.getMessage().startswith("task ") and "failed after" in r.getMessage()]
    assert len(failed) == 1 and failed[0].exc_info[0] is RuntimeError
    # the writes that did not fail went on
    if failing_write != "state_file":
        assert state(config, "r2")["status"] == "failed" and "boom" in state(config, "r2")["tasks"]["evaluate"]["error"]
    if failing_write != "sqlite_row":
        assert run_row(config, "r2")[0] == "failed"
    if failing_write == "sqlite_row":
        assert run_row(config, "r2")[0] == "running"   # the record could not be written; the state file still says what happened
    if failing_write == "state_file":
        assert exported_runs(config)["r2"] == "failed"
    if failing_write == "exported_files":
        assert state(config, "r2")["status"] == "failed"
    after = [r for r in caplog.records if r.getMessage().startswith("task ") and "failed after" in r.getMessage()]
    assert len(after) == 1


def test_a_failed_refresh_after_a_failure_keeps_the_original_error_logs_the_traceback_and_marks_the_exports_stale(pipeline_closed, fixture_csv, tmp_path, monkeypatch, caplog):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    failing_evaluate(monkeypatch)

    def cannot_write(*args, **kwargs):
        raise PermissionError("the file is open in another program")

    monkeypatch.setattr(pipeline, "export_run_table", cannot_write)
    with caplog.at_level("WARNING", logger="latedel.pipeline"), pytest.raises(RuntimeError, match="boom"):
        run_all(config, run_key="r2")
    assert "PermissionError" in state(config, "r2")["exports_stale"] and state(config, "r2")["status"] == "failed"
    assert any("could not refresh the exported files" in r.getMessage() and r.exc_info for r in caplog.records)
    monkeypatch.undo()
    run_task("evaluate", "r2", config)   # resuming the run refreshes the files again: a refresh that works clears the mark
    assert "exports_stale" not in state(config, "r2") and exported_runs(config)["r2"] == "running"


def test_the_very_first_run_failing_before_the_load_creates_no_exported_run_table(pipeline_source, fixture_csv, tmp_path, monkeypatch):
    config = relocate(pipeline_source, tmp_path / "p", fixture_csv)
    monkeypatch.setattr(pipeline, "check_coverage", boom)
    with pytest.raises(RuntimeError, match="boom"):
        run_all(config, run_key="r1")
    assert not config.paths.warehouse.exists() and not config.paths.outputs_dir.exists() and not config.paths.dashboard_html.exists()
    assert state(config, "r1")["status"] == "failed" and "boom" in state(config, "r1")["tasks"]["deliver"]["traceback"]


def test_the_fit_of_the_launch_writes_the_importance_and_the_leakage_tables_and_a_repetition_completes_them(pipeline_interrupted, fixture_csv, tmp_path, monkeypatch):
    config = relocate(pipeline_interrupted, tmp_path / "p", fixture_csv)
    assert rows(config, "SELECT COUNT(*) FROM feature_importance")[0][0] > 0 and rows(config, "SELECT COUNT(*) FROM eval_leakage")[0][0] > 0   # the launch wrote them
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "record_launch_tables", lambda conn, cfg, *, as_of: calls.append(as_of.isoformat()) or {"feature_importance": 1, "eval_leakage": 1})
    assert run_task("fit_model", "r1", config)["launch_tables"] == {} and calls == []   # tables there: a repetition does not write them again
    conn = sqlite3.connect(config.paths.warehouse)
    conn.execute("DELETE FROM feature_importance")      # a launch interrupted after registering the model and before the tables
    conn.commit()
    conn.close()
    again = run_task("fit_model", "r1", config)
    assert again["mode"] == "reuse" and again["launch_tables"] == {"feature_importance": 1, "eval_leakage": 1} and calls == ["2017-01-07"]


def test_the_model_rules_are_added_to_the_report_of_the_run(pipeline_two_runs, fixture_csv):
    config = make_cfg(pipeline_two_runs, fixture_csv)
    by_run = dict(rows(config, "SELECT run_key || ':' || rule_id, passed FROM validation_results WHERE rule_id IN ('M01', 'M02', 'M03', 'M04', 'M05', 'I02', 'I04')"))
    assert by_run == {"r1:M01": 1, "r1:M03": 1, "r1:M05": 1, "r1:I02": 1, "r1:I04": 1, "r2:M02": 1, "r2:M03": 1, "r2:M04": 1, "r2:M05": 1, "r2:I02": 1, "r2:I04": 1}
    latest = json.loads((config.paths.reports_dir / "validation" / "latest.json").read_text(encoding="utf-8"))
    assert latest["run_key"] == "r2" and "M04" not in latest["not_evaluated"] and "M01" in latest["not_evaluated"]
