"""DAG ``latedel_monthly``: the eleven tasks of ``latedel.pipeline.TASKS``, wired in their order.

The DAG only wires: each task calls ``latedel.pipeline.run_task(task_id, run_id)``, the same function the command line runs, so Airflow and ``python -m latedel run`` do the
same work in the same order. The delivery is a short circuit: with no delivery left to load the run ends there (as ``no_delivery``) and the rest is skipped. It runs once a
month, on the 2nd (the delivery of the month before is ready), with one active run and one active task at a time, because the warehouse is a SQLite file with a single writer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from airflow.sdk import DAG, task
from airflow.sdk.exceptions import AirflowFailException

from latedel.features import LeakageError
from latedel.pipeline import TASKS, RunRefusedError, TaskSpec, run_task, task_summary
from latedel.validate import ValidationError


def run_without_retrying_a_rejection(task_id: str, run_id: str | None) -> dict:
    """``run_task``, except that a rejection, a leak or a refusal fails the task at once: Airflow retries any other exception, but a rule that failed on the same file (V01
    on the registered CSV, for example), a column that leaks the outcome or a run that is closed fails the same way every time, and the retries of ``fetch_source`` took about
    30 minutes to show it. Errors of the network or of the disk keep their retries."""
    try:
        return run_task(task_id, run_id)
    except (ValidationError, LeakageError, RunRefusedError) as error:
        raise AirflowFailException(str(error)) from error


def build_task(spec: TaskSpec):
    """One Airflow task for a ``TaskSpec`` (a function of its own, so the loop variable is never captured)."""
    options = {
        "task_id": spec.task_id,
        "retries": spec.retries,
        "retry_delay": timedelta(minutes=spec.retry_delay_min),
        "execution_timeout": timedelta(minutes=spec.timeout_min),
    }
    if spec.short_circuit:

        @task.short_circuit(**options)
        def step(run_id=None) -> bool:
            return bool(run_without_retrying_a_rejection(spec.task_id, run_id)["delivered"])

    else:

        @task(**options)
        def step(run_id=None) -> dict:
            # the whole result stays in the run's state file; XCom gets a small flat summary (counts, times) for the Airflow UI
            return task_summary(spec.task_id, run_without_retrying_a_rejection(spec.task_id, run_id))

    return step()


with DAG(
    dag_id="latedel_monthly",
    schedule="0 6 2 * *",  # the 2nd of every month at 06:00 UTC
    start_date=datetime(2026, 11, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={"retry_exponential_backoff": True},
    tags=["latedel"],
    doc_md=__doc__,
) as dag:
    previous = None
    for spec in TASKS:
        current = build_task(spec)
        if previous is not None:
            previous >> current
        previous = current
