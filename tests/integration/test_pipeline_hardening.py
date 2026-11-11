"""H7: the advisories of the native review of U21 (the tasks, the state of a run and its close): a failure of the write that records a task's result, the retry and
timeout fields that only Airflow enforces, a refusal that could not be reached, the usage errors and the stops of ``replay``, and a real interruption.

Every claim was checked against the code first; the ones that were defects have a test that failed before the change, the ones that were only missing tests pass. The
runs are copies of the ones ``conftest.py`` builds once per session.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from latedel import pipeline
from latedel.deliveries import read_manifest
from latedel.pipeline import RunStateError, UsageError, replay, run_all, run_task
from latedel.validate import ValidationError
from tests.conftest import relocate
from tests.fixtures.helpers import snapshot

FIRST, SECOND, THIRD = "L2017-01-07", "M2017-01", "M2017-02"


def state(config, run_key: str) -> dict:
    return json.loads((config.paths.interim_dir / "runs" / f"{run_key}.json").read_text(encoding="utf-8"))


def run_row(config, run_key: str) -> tuple | None:
    conn = sqlite3.connect(f"{config.paths.warehouse.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT status, message FROM pipeline_runs WHERE run_key = ?", (run_key,)).fetchone()
    finally:
        conn.close()


def manifest(config) -> dict[str, tuple[str, str | None]]:
    return {e.delivery_id: (e.status, e.run_key) for e in read_manifest(config.paths.raw_dir / "deliveries" / "manifest.json")}


def exported_status(config, run_key: str) -> str:
    table = pq.read_table(config.paths.outputs_dir / "dim_pipeline_run.parquet").to_pandas()
    return dict(zip(table["run_key"], table["status"], strict=True))[run_key]


def save_fails_for(monkeypatch, task_id: str) -> None:
    """The write that records the result of `task_id` fails (a full disk); every other task saves as usual."""
    real = pipeline._save_task

    def save(ctx, task, *args, **kwargs):
        if task == task_id:
            raise OSError("no space left on the device")
        return real(ctx, task, *args, **kwargs)

    monkeypatch.setattr(pipeline, "_save_task", save)


# --- R4-save-task-outside-failure-handler --------------------------------------------------------------------------------------------------------


def test_a_failure_saving_the_result_of_a_middle_task_is_recorded_as_a_failure_and_the_run_resumes_from_it(pipeline_closed, fixture_csv, tmp_path, monkeypatch, caplog):
    """``load`` committed the delivery and then the write of its result failed: the run was left ``running`` in SQLite and in its state file, with no failure recorded
    anywhere. Now the failure handler records it (the state file, the row of SQLite, the exported run table), the caller sees the error of the save, the run resumes
    from that task with the same delivery, and a new run is refused because the delivery is in the warehouse without its run closed (D72)."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    with monkeypatch.context() as failing, caplog.at_level(logging.ERROR, logger="latedel.pipeline"):
        save_fails_for(failing, "load")
        with pytest.raises(OSError, match="no space"):
            run_all(config, run_key="r2")
    saved = state(config, "r2")
    assert saved["status"] == "failed" and saved["tasks"]["load"]["status"] == "failed" and "OSError: no space" in saved["tasks"]["load"]["error"]
    assert run_row(config, "r2") == ("failed", "load: OSError: no space left on the device") and exported_status(config, "r2") == "failed"
    assert any("load" in r.getMessage() and "failed" in r.getMessage() and r.exc_info and r.exc_info[0] is OSError for r in caplog.records)
    assert manifest(config)[SECOND] == ("published", None)
    with pytest.raises(RunStateError, match=r"r2.*--run-key r2"):
        run_all(config, run_key="r3")                                     # the delivery is loaded by r2, which has not closed: resume that one
    resumed = run_all(config, start="load", run_key="r2")
    assert resumed[0]["result"]["resumed"] is True and state(config, "r2")["status"] == "ok"
    assert run_row(config, "r2")[0] == "ok" and manifest(config)[SECOND] == ("loaded", "r2") and THIRD not in manifest(config)


def test_a_full_disk_that_also_fails_the_recording_of_the_failure_still_shows_the_error_of_the_save(pipeline_closed, fixture_csv, tmp_path, monkeypatch, caplog):
    """The review of H7 (R4-record-failure-cascading-write) said a full disk would also fail the writes of ``_record_failure`` and replace the error of the save. Each of
    those writes is tried on its own and logged (AC27): with the state file, the row of SQLite and the exported files all failing after the save of ``load`` fails, the
    caller still sees the error of the save and the log names the task, the save and each write that could not be done."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    real = pipeline._save_task

    def save(ctx, task, *args, **kwargs):
        if task != "load":
            return real(ctx, task, *args, **kwargs)
        for name in ("_write_state", "_record", "_refresh_or_mark_stale"):   # from now on the disk is full for every write
            monkeypatch.setattr(pipeline, name, lambda *a, **k: (_ for _ in ()).throw(OSError("still no space left on the device")))
        raise OSError("no space left on the device")

    with caplog.at_level(logging.WARNING, logger="latedel.pipeline"):
        monkeypatch.setattr(pipeline, "_save_task", save)
        with pytest.raises(OSError, match=r"^no space left on the device$"):
            run_all(config, run_key="r2")
    messages = [r.getMessage() for r in caplog.records]
    assert any("load failed" in m and "while saving its result" in m for m in messages)
    assert sum(m.startswith("could not record the failure of load") or m.startswith("could not refresh the exported files") for m in messages) == 3


def test_a_failure_saving_the_result_of_deliver_leaves_a_run_that_takes_the_same_delivery_again(pipeline_closed, fixture_csv, tmp_path, monkeypatch):
    """The review said a retried ``deliver`` would take the next delivery; it does not: the published delivery is still the pending one (``next_pending``), so the
    retry takes it again, and the failure is recorded in the state file (the run has no row in SQLite before ``load``)."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    with monkeypatch.context() as failing:
        save_fails_for(failing, "deliver")
        with pytest.raises(OSError, match="no space"):
            run_all(config, run_key="r2")
    assert state(config, "r2")["status"] == "failed" and state(config, "r2")["tasks"]["deliver"]["status"] == "failed"
    assert run_row(config, "r2") is None and manifest(config)[SECOND] == ("published", None)
    again = run_task("deliver", "r2", config)
    assert again["delivered"] is True and again["delivery_id"] == SECOND and THIRD not in manifest(config)


def test_a_failure_saving_the_result_of_report_leaves_the_close_complete_and_the_run_open_for_report(pipeline_rendered, fixture_csv, tmp_path, monkeypatch, caplog):
    """The save of ``report`` also moves the run to its final status. When it is the one that fails, the five closing writes are done (the manifest says ``loaded``
    and cannot be taken back), so nothing is rewritten as ``failed`` (D72); the error is logged with what to do, the run stays open in its state file, a second
    delivery in it is refused and repeating ``report`` records the end."""
    config = relocate(pipeline_rendered, tmp_path / "p", fixture_csv)
    with monkeypatch.context() as failing, caplog.at_level(logging.ERROR, logger="latedel.pipeline"):
        save_fails_for(failing, "report")
        with pytest.raises(OSError, match="no space"):
            run_all(config, start="report", run_key="r1")
    assert state(config, "r1")["status"] == "running" and "report" not in state(config, "r1")["tasks"]
    assert run_row(config, "r1")[0] == "ok" and exported_status(config, "r1") == "ok" and manifest(config)[FIRST] == ("loaded", "r1")
    logged = [r for r in caplog.records if r.levelno == logging.ERROR and "r1" in r.getMessage() and "--from report" in r.getMessage()]
    assert len(logged) == 1 and logged[0].exc_info and logged[0].exc_info[0] is OSError
    with pytest.raises(pipeline.RunRefusedError, match=rf"r1.*{FIRST}.*{SECOND}"):
        run_task("deliver", "r1", config)
    run_all(config, start="report", run_key="r1")
    assert state(config, "r1")["status"] == "ok" and state(config, "r1")["tasks"]["report"]["run_status"] == "ok"


def test_a_failure_saving_a_run_that_ended_without_a_delivery_leaves_it_to_end_again(pipeline_closed, fixture_csv, tmp_path, monkeypatch, caplog):
    """``deliver`` with nothing left records the run as ``no_delivery`` in SQLite before its result is saved: a save that fails then does not mark it ``failed`` (it
    ended), it says so in the log, and running the run again ends it the same way."""
    from tests.integration.test_pipeline import stop_the_plan_at_no_delivery

    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    stop_the_plan_at_no_delivery(config)
    with monkeypatch.context() as failing, caplog.at_level(logging.ERROR, logger="latedel.pipeline"):
        save_fails_for(failing, "deliver")
        with pytest.raises(OSError, match="no space"):
            run_all(config, run_key="r2")
    assert run_row(config, "r2")[0] == "no_delivery" and state(config, "r2")["status"] == "running"
    assert any("r2" in r.getMessage() and "no_delivery" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)
    done = run_all(config, run_key="r2")
    assert done[-1]["result"]["delivered"] is False and state(config, "r2")["status"] == "no_delivery" and run_row(config, "r2")[0] == "no_delivery"


# --- R4-unenforced-retry-timeout-fields: only Airflow enforces them ------------------------------------------------------------------------------


def test_the_command_line_runs_each_task_once_and_leaves_its_retries_to_airflow(pipeline_source, fixture_csv, tmp_path, monkeypatch):
    """``fetch_source`` has three retries in its ``TaskSpec``; the local runner runs it once and lets its error through (the DAG passes the retries, the retry delay and
    the timeout to its Airflow task: ``tests/unit/test_dag.py::test_the_dag_builds_one_task_per_spec_with_its_limits``)."""
    config = relocate(pipeline_source, tmp_path / "p", fixture_csv)
    calls = []

    def flaky(ctx):
        calls.append(ctx.run_key)
        raise ConnectionError("the network is down")

    assert pipeline._spec("fetch_source").retries == 3
    monkeypatch.setattr(pipeline, "TASKS", tuple(
        pipeline.TaskSpec(t.task_id, flaky, t.retries, t.retry_delay_min, t.timeout_min, t.short_circuit) if t.task_id == "fetch_source" else t for t in pipeline.TASKS))
    with pytest.raises(ConnectionError):
        run_task("fetch_source", "once", config)
    assert calls == ["once"] and state(config, "once")["status"] == "failed"


# --- R3-002: the usage errors and the stops of replay ---------------------------------------------------------------------------------------------


def test_replay_refuses_an_until_that_is_not_a_delivery_of_the_plan_and_writes_nothing(pipeline_closed, fixture_csv, tmp_path):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    before = snapshot(tmp_path / "p")
    with pytest.raises(UsageError, match=r"--until M2019-01.*no es una entrega del plan"):
        replay(config, until="M2019-01")
    assert snapshot(tmp_path / "p") == before


def test_replay_refuses_an_until_before_the_next_pending_delivery_and_writes_nothing(pipeline_closed, fixture_csv, tmp_path):
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    before = snapshot(tmp_path / "p")
    with pytest.raises(UsageError, match=rf"--until {FIRST} es anterior a la siguiente entrega pendiente, {SECOND}"):
        replay(config, until=FIRST)
    assert snapshot(tmp_path / "p") == before


def test_replay_stops_at_the_first_run_that_fails(pipeline_closed, fixture_csv, tmp_path, monkeypatch):
    """The run of ``M2017-01`` fails in ``evaluate``: the replay raises its error and runs nothing after it (``M2017-02`` is not published)."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "evaluate_model", boom)
    with pytest.raises(RuntimeError, match="boom"):
        replay(config, until=THIRD)
    assert manifest(config)[SECOND] == ("published", None) and THIRD not in manifest(config)
    assert [json.loads(p.read_text(encoding="utf-8"))["status"] for p in (config.paths.interim_dir / "runs").glob("*.json")].count("failed") == 1


def test_replay_stops_at_the_first_run_that_is_rejected(pipeline_closed, fixture_csv, tmp_path):
    """The delivery ``M2017-01`` was published and then damaged: its run is rejected (V24) and the replay stops with the rejection, before ``M2017-02``."""
    config = relocate(pipeline_closed, tmp_path / "p", fixture_csv)
    run_all(config, only=["fetch_source", "deliver"], run_key="publisher")
    path = config.paths.raw_dir / "deliveries" / SECOND / "orders.parquet"
    table = pq.read_table(path)
    pq.write_table(pa.Table.from_pandas(table.to_pandas().iloc[1:], schema=table.schema, preserve_index=False).replace_schema_metadata(table.schema.metadata), path)
    with pytest.raises(ValidationError) as rejected:
        replay(config, until=THIRD)
    assert "V24" in [r.rule_id for r in rejected.value.report.results if not r.passed]
    assert manifest(config)[SECOND][0] == "rejected" and THIRD not in manifest(config)


# --- R3-003: a real interruption is recorded as a failure ------------------------------------------------------------------------------------------


def test_a_real_keyboard_interrupt_in_a_task_is_recorded_as_a_failure_and_still_interrupts(pipeline_second_tracked, fixture_csv, tmp_path, monkeypatch):
    """Ctrl+C in the middle of ``evaluate``, with the real failure handler: the state file and the row of SQLite say ``failed`` with the interruption, the exported run
    table too, and the ``KeyboardInterrupt`` reaches the caller (the process stops; the run is open for a resume)."""
    config = relocate(pipeline_second_tracked, tmp_path / "p", fixture_csv)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline, "evaluate_model", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_all(config, start="evaluate", run_key="r2")
    saved = state(config, "r2")
    assert saved["status"] == "failed" and saved["tasks"]["evaluate"]["error"] == "evaluate: KeyboardInterrupt: " and "KeyboardInterrupt" in saved["tasks"]["evaluate"]["traceback"]
    assert run_row(config, "r2") == ("failed", "evaluate: KeyboardInterrupt: ") and exported_status(config, "r2") == "failed"
    monkeypatch.undo()
    run_all(config, start="evaluate", run_key="r2")
    assert state(config, "r2")["status"] == "ok" and run_row(config, "r2")[0] == "ok"
