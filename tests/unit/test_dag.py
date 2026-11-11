"""The Airflow DAG without Airflow: its text with ``ast`` and its wiring with a stand-in for ``airflow.sdk`` (U23, AC31)."""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from latedel.pipeline import TASK_IDS, TASKS

DAG_FILE = Path(__file__).resolve().parents[2] / "airflow" / "dags" / "latedel_monthly.py"


def keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {k.arg: k.value for k in call.keywords if k.arg}


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(DAG_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dag_call(tree: ast.Module) -> ast.Call:
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "DAG"]
    assert len(calls) == 1
    return calls[0]


# --- the text of the file ------------------------------------------------------------------------------------------


def test_the_dag_has_the_id_the_monthly_schedule_no_catchup_and_one_active_run_and_task(dag_call: ast.Call) -> None:
    options = keywords(dag_call)
    assert ast.literal_eval(options["dag_id"]) == "latedel_monthly"
    assert ast.literal_eval(options["schedule"]) == "0 6 2 * *"   # the 2nd of every month, 06:00 UTC
    assert ast.literal_eval(options["catchup"]) is False
    assert ast.literal_eval(options["max_active_runs"]) == 1 and ast.literal_eval(options["max_active_tasks"]) == 1
    assert ast.literal_eval(options["tags"]) == ["latedel"]
    assert ast.literal_eval(options["default_args"]) == {"retry_exponential_backoff": True}
    start = options["start_date"]
    assert isinstance(start, ast.Call) and [ast.literal_eval(a) for a in start.args] == [2026, 11, 1]


def test_it_uses_the_airflow_3_sdk_and_the_functions_of_the_pipeline(tree: ast.Module) -> None:
    imports = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert {("airflow.sdk", "DAG"), ("airflow.sdk", "task"), ("latedel.pipeline", "TASKS"), ("latedel.pipeline", "run_task")} <= imports


def test_the_tasks_come_from_the_task_list_and_are_chained_in_its_order_not_typed_in_the_dag(tree: ast.Module) -> None:
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)]
    assert any(isinstance(n.iter, ast.Name) and n.iter.id == "TASKS" for n in loops)
    chained = [n for loop in loops for n in ast.walk(loop) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.RShift)]
    assert chained, "the tasks are chained with >> inside the loop over TASKS"
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not strings & set(TASK_IDS)   # no task id is written in the DAG


def test_the_options_of_each_task_come_from_its_spec_and_the_delivery_is_a_short_circuit() -> None:
    text = DAG_FILE.read_text(encoding="utf-8")
    for fragment in ("spec.retries", "spec.retry_delay_min", "spec.timeout_min", "spec.short_circuit", "task.short_circuit", "run_without_retrying_a_rejection(spec.task_id, run_id)", '["delivered"]'):
        assert fragment in text, fragment


# --- the wiring, with a stand-in for airflow.sdk ------------------------------------------------------------------------


class Node:
    def __init__(self, function, options):
        self.function, self.options, self.downstream = function, options, []

    def __rshift__(self, other):
        self.downstream.append(other)
        return other


class FakeDag:
    instances: list[FakeDag] = []

    def __init__(self, **options):
        self.options, self.nodes = options, []
        FakeDag.instances.append(self)

    def __enter__(self):
        FakeDag.current = self
        return self

    def __exit__(self, *exc):
        return False


class FakeFailException(Exception):
    """Stand-in for ``airflow.sdk.exceptions.AirflowFailException``: the task fails at once, with no retry."""


class FakeTask:
    def _decorator(self, kind, options):
        def wrap(function):
            def build():
                node = Node(function, {"kind": kind, **options})
                FakeDag.current.nodes.append(node)
                return node

            return build

        return wrap

    def __call__(self, **options):
        return self._decorator("task", options)

    def short_circuit(self, **options):
        return self._decorator("short_circuit", options)


@pytest.fixture()
def loaded_dag(monkeypatch):
    sdk = types.ModuleType("airflow.sdk")
    sdk.DAG, sdk.task = FakeDag, FakeTask()
    exceptions = types.ModuleType("airflow.sdk.exceptions")
    exceptions.AirflowFailException = FakeFailException
    monkeypatch.setitem(sys.modules, "airflow", types.ModuleType("airflow"))
    monkeypatch.setitem(sys.modules, "airflow.sdk", sdk)
    monkeypatch.setitem(sys.modules, "airflow.sdk.exceptions", exceptions)
    FakeDag.instances.clear()
    spec = importlib.util.spec_from_file_location("latedel_monthly_dag", DAG_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, FakeDag.instances[0]


def test_tasks_match_cli_order(loaded_dag) -> None:
    """The DAG and ``python -m latedel run`` walk the same tasks in the same order (AC31)."""
    from latedel.cli import build_parser

    _, dag = loaded_dag
    run = next(a for a in build_parser()._subparsers._group_actions[0].choices.values() if a.prog.endswith(" run"))
    only = next(a for a in run._actions if a.dest == "only")
    assert [n.options["task_id"] for n in dag.nodes] == list(only.choices) == list(TASK_IDS) == [t.task_id for t in TASKS]
    for before, after in zip(dag.nodes, dag.nodes[1:], strict=False):
        assert before.downstream == [after]   # each one depends on the one before it, in a line
    assert dag.nodes[-1].downstream == []


def test_the_dag_builds_one_task_per_spec_with_its_limits(loaded_dag) -> None:
    _, dag = loaded_dag
    assert len(dag.nodes) == 11
    for node, spec in zip(dag.nodes, TASKS, strict=True):
        assert node.options["retries"] == spec.retries
        assert node.options["retry_delay"] == timedelta(minutes=spec.retry_delay_min)
        assert node.options["execution_timeout"] == timedelta(minutes=spec.timeout_min)
        assert node.options["kind"] == ("short_circuit" if spec.short_circuit else "task")
    assert dag.options["start_date"] == datetime(2026, 11, 1, tzinfo=UTC)


def test_each_task_runs_its_own_pipeline_task_with_the_run_id_of_airflow(loaded_dag, monkeypatch) -> None:
    module, dag = loaded_dag
    calls = []

    def fake_run_task(task_id, run_id=None, cfg=None, **kwargs):
        calls.append((task_id, run_id))
        return {"delivered": task_id != "deliver", "rows": 7, "seconds": 1.5, "nested": {"left": "out"}}

    monkeypatch.setattr(module, "run_task", fake_run_task)
    results = [node.function(run_id="scheduled__x") for node in dag.nodes]
    assert calls == [(task_id, "scheduled__x") for task_id in TASK_IDS]   # not the last loop variable eleven times
    assert results[1] is False   # no delivery left stops the run: the short circuit gets False
    # the rest put a small summary in XCom: the task, its counts and times, nothing nested
    rest = [(task_id, r) for task_id, r in zip(TASK_IDS, results, strict=True) if task_id != "deliver"]
    assert all(r == {"task": task_id, "delivered": True, "rows": 7, "seconds": 1.5} for task_id, r in rest)


def test_a_rejection_a_leak_or_a_refusal_fails_the_task_at_once_and_a_transport_error_keeps_its_retries(loaded_dag, monkeypatch) -> None:
    """``fetch_source`` once retried a deterministic V01 rejection (3 retries with an exponential back-off, about 30 minutes) before showing it."""
    from types import SimpleNamespace

    from latedel.features import LeakageError
    from latedel.pipeline import RunRefusedError
    from latedel.validate import ValidationError

    module, dag = loaded_dag
    rejected = ValidationError(SimpleNamespace(run_key="manual__x", results=[]))

    def raising(error):
        def run(task_id, run_id=None, cfg=None, **kwargs):
            raise error

        return run

    monkeypatch.setattr(module, "run_task", raising(rejected))
    for node in dag.nodes:   # the short circuit and the plain tasks alike
        with pytest.raises(FakeFailException, match="manual__x") as caught:
            node.function(run_id="manual__x")
        assert caught.value.__cause__ is rejected
    for error, text in ((LeakageError("M01: AUC 0.99"), "M01"), (RunRefusedError("La corrida x está cerrada"), "cerrada")):   # as deterministic as a rejection
        monkeypatch.setattr(module, "run_task", raising(error))
        with pytest.raises(FakeFailException, match=text) as caught:
            dag.nodes[4].function(run_id="manual__x")
        assert caught.value.__cause__ is error
    unreachable = ConnectionError("the server did not answer")
    monkeypatch.setattr(module, "run_task", raising(unreachable))
    for node in dag.nodes:
        with pytest.raises(ConnectionError):   # not an AirflowFailException: Airflow retries it as the task's options say
            node.function(run_id="manual__x")


def test_the_docstring_names_the_short_circuit_task_and_the_status_the_pipeline_records(tree: ast.Module) -> None:
    """R3-no-delivery-docstring-mismatch (review of U23): the docstring said the run ends ``as no_delivery`` without naming the task; it names the task whose
    ``TaskSpec`` is the short circuit and the status of ``pipeline_runs`` that the pipeline records, both read here from the code."""
    from latedel.pipeline import CLOSED_STATUSES

    doc = ast.get_docstring(tree)
    [short] = [spec.task_id for spec in TASKS if spec.short_circuit]
    assert f"``{short}``" in doc and "short_circuit=True" in doc
    assert "``no_delivery``" in doc and "no_delivery" in CLOSED_STATUSES and "pipeline_runs" in doc
