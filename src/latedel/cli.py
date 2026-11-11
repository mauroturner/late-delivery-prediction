"""Command line of the pipeline: ``python -m latedel <command>`` (plan section 19, decisions D35 and D73).

``run`` and ``replay`` walk the same ``pipeline.TASKS`` as the Airflow DAG, in the same order and with the same functions, so Windows can run the whole flow without Airflow.
Exit codes: 0 ok (also a run with no delivery left), 1 a task failed, 2 wrong usage or configuration (also a task refused because its run is closed, or because another
process holds the writer lock of the data folder, D82), 3 the delivery or the source was rejected by the validation (the warehouse and the exports are as they were). An option that cannot be honoured is refused before anything is written; none is
ignored in silence (AC29). Every relative path of ``--config`` and of ``make-fixture`` is resolved against the project folder, never against the working directory.
"""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

from latedel.config import DEFAULT_PROJECT_ROOT, ENV_CONFIG, load_config
from latedel.pipeline import TASK_IDS, RunRefusedError, UsageError, replay, run_all, run_task
from latedel.reporting import sync_results
from latedel.validate import ValidationError

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REJECTED = 0, 1, 2, 3
SUMMARY_MD = "evaluation_summary.md"
SIGNALS = ("none", "standard_market")


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, help=f"archivo de configuración (por defecto ${ENV_CONFIG} o config/pipeline.toml); una ruta relativa se toma desde la carpeta del proyecto")
    common.add_argument("-v", "--verbose", action="store_true", help="muestra el detalle de cada paso y la traza de la tarea que falla")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m latedel", description="Pedidos que van a llegar tarde, avisados antes de que lleguen.")
    commands = parser.add_subparsers(dest="command", required=True)
    common = _common()

    run = commands.add_parser("run", parents=[common], help="una corrida: la siguiente entrega, con las tareas " + ", ".join(TASK_IDS))
    run.add_argument("--only", nargs="+", choices=TASK_IDS, metavar="TAREA", help="corre solo estas tareas de la corrida")
    run.add_argument("--from", dest="start", choices=TASK_IDS, metavar="TAREA", help="sigue la corrida desde esta tarea")
    run.add_argument("--run-key", help="la corrida que se continúa (hace falta con --only o --from si no empiezan por fetch_source)")

    replay_ = commands.add_parser("replay", parents=[common], help="corridas seguidas, una por entrega pendiente, hasta agotarlas o hasta una entrega")
    replay_.add_argument("--until", metavar="ENTREGA", help="la última entrega que se corre, por su identificador (por ejemplo M2017-03; por defecto, todas las pendientes)")

    task = commands.add_parser("task", parents=[common], help="una tarea de una corrida")
    task.add_argument("task", choices=TASK_IDS, metavar="TAREA")
    task.add_argument("--run-key", help="la corrida a la que pertenece (una nueva si se omite)")

    commands.add_parser("figures", parents=[common], help="dibuja las figuras de la documentación (docs/figures/*.png) desde los archivos del sitio de la última corrida")

    commands.add_parser(
        "docs-sync", parents=[common],
        help="copia el resumen de resultados (reports/evaluation_summary.md) en el README y las tablas del modelo en el diccionario de datos (docs/data-dictionary.md), si existe",
    )

    fixture = commands.add_parser("make-fixture", help="escribe una fuente sintética para pruebas y modo rápido")
    fixture.add_argument("output", type=Path, metavar="SALIDA.csv")
    fixture.add_argument("--orders", type=int, default=3000, help="pedidos simulados (por defecto 3000)")
    fixture.add_argument("--seed", type=int, default=0, help="semilla de la simulación (por defecto 0)")
    fixture.add_argument("--signal", choices=SIGNALS, default="none", help="standard_market: los Standard Class de un mercado llegan tarde más seguido (control positivo)")
    return parser


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


def _fail(text: str) -> None:
    sys.stderr.write(text + "\n")


def _brief(result: dict) -> str:
    """The few figures of a task result worth a line."""
    keys = ("delivered", "delivery_id", "orders", "scored", "alerts", "built", "resumed", "mode", "seal_state", "test_evaluated", "bytes", "run_status")
    return ", ".join(f"{k}={result[k]}" for k in keys if k in result)


class _LineFormatter(logging.Formatter):
    """One line per record: the traceback of a failed task is printed by ``main`` with ``-v``, not by the log, so it appears once."""

    def formatException(self, ei) -> str:
        return ""


def _configure_logging(verbose: bool) -> None:
    """Log to the standard error with one line per record, from the package's own logger (so that a second call in the same process, as the tests make, replaces the
    handler of the first and ``-v`` always takes effect): information with ``-v``, warnings and errors without it."""
    package = logging.getLogger("latedel")
    for handler in [h for h in package.handlers if getattr(h, "_latedel_cli", False)]:
        package.removeHandler(handler)
    handler = logging.StreamHandler()
    handler._latedel_cli = True   # type: ignore[attr-defined]
    handler.setFormatter(_LineFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    package.addHandler(handler)
    package.setLevel(logging.INFO if verbose else logging.WARNING)


def _check_run_options(args: argparse.Namespace) -> None:
    """The combinations of ``run`` that cannot do what they say, refused before a state file is created."""
    if args.only and args.start is not None:
        raise UsageError("--only y --from no se combinan: elija las tareas o desde cuál seguir")
    if args.run_key and not args.only and args.start is None:
        raise UsageError("--run-key necesita --only o --from: una corrida completa nueva elige su propia clave")
    first = next(t for t in TASK_IDS if t in args.only) if args.only else (args.start or TASK_IDS[0])
    if args.run_key is None and first != TASK_IDS[0]:   # a new run has nothing from the tasks before it: it would fail on its first task
        raise UsageError(
            f"una corrida nueva empieza por {TASK_IDS[0]}, no por {first}: indique con --run-key la corrida que continúa "
            "(sin ella no hay resultado de las tareas anteriores)"
        )


def _show(task_id: str, result: dict) -> None:
    if task_id == "deliver" and result.get("delivered"):
        _say(f"entrega {result['delivery_id']} (hasta {result['period_end']})")
    _say(f"{task_id:<18} {result.get('seconds', 0):>8.1f} s  {_brief(result)}")


def _make_fixture(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(DEFAULT_PROJECT_ROOT))   # the generator lives with the tests, next to src/
    from tests.fixtures.make_fixture import make_fixture

    output = args.output if args.output.is_absolute() else DEFAULT_PROJECT_ROOT / args.output   # like the paths of the configuration: against the project, not the working directory
    truth = make_fixture(output, n_orders=args.orders, seed=args.seed, signal=args.signal)
    _say(f"escribí {truth.path}: {truth.n_lines} líneas de {truth.n_orders} pedidos simulados (semilla {args.seed}, señal {args.signal})")
    return EXIT_OK


def _execute(args: argparse.Namespace, cfg) -> int:
    if args.command == "docs-sync":
        summary = Path(cfg.paths.reports_dir) / SUMMARY_MD
        if not summary.is_file():
            raise FileNotFoundError(f"No existe {summary}: corra la tarea report antes de docs-sync")
        if not Path(cfg.paths.readme).is_file():   # the quick mode has no README of its own (A4 of the evaluation of eef9bea)
            raise UsageError(
                f"docs-sync copia el resumen de resultados en el README de la configuración, {cfg.paths.readme}, y ese archivo no existe: el modo rápido no tiene "
                f"README, y su resumen queda en {summary}. No se escribió nada."
            )
        changed = sync_results(cfg.paths.readme, summary)
        _say(f"{cfg.paths.readme.name} actualizado" if changed else f"{cfg.paths.readme.name} ya estaba al día")
        from latedel.dashboard.semantic_model import MODEL
        from latedel.docs import dictionary_path, sync_dictionary

        dictionary = dictionary_path(cfg.paths.figures_dir)   # docs/ in the project; the quick mode has none, and then only the README is written, as before U27
        if dictionary.is_file():
            _say(f"{dictionary.name} actualizado" if sync_dictionary(dictionary, MODEL) else f"{dictionary.name} ya estaba al día")
        return EXIT_OK
    if args.command == "figures":
        from latedel.figures import make_figures   # matplotlib takes about a second to import: only the command that draws pays for it

        for path in make_figures(cfg.paths.site_dir, cfg.paths.figures_dir):
            _say(f"escribí {path}")
        return EXIT_OK
    if args.command == "task":
        result = run_task(args.task, args.run_key, cfg)
        _say(f"{args.task}: {_brief(result)}")
        return EXIT_OK
    if args.command == "replay":
        runs = replay(cfg, args.until, on_task=_show)
        _say(("1 corrida" if len(runs) == 1 else f"{len(runs)} corridas") if runs else "No hay entregas pendientes: nada que correr")
        return EXIT_OK
    outcome = run_all(cfg, only=args.only, start=args.start, run_key=args.run_key, on_task=_show)
    if outcome and not outcome[-1]["result"].get("delivered", True):
        _say(outcome[-1]["result"]["message"])
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as stop:   # argparse exits on a wrong option (2) and on --help (0)
        return int(stop.code or 0)
    _configure_logging(getattr(args, "verbose", False))
    if args.command == "make-fixture":
        try:
            return _make_fixture(args)
        except (ValueError, OSError) as error:   # a count or a seed the generator refuses, or a path that cannot be written
            _fail(f"error: {error}")
            return EXIT_USAGE
    try:
        if args.command == "run":
            _check_run_options(args)
        cfg = load_config(args.config if args.config is None or args.config.is_absolute() else DEFAULT_PROJECT_ROOT / args.config)
    except (ValueError, FileNotFoundError) as error:   # a wrong option or configuration: nothing has been written
        _fail(f"error: {error}")
        return EXIT_USAGE
    try:
        return _execute(args, cfg)
    except ValidationError as error:
        _fail(f"rechazada: {error}")
        return EXIT_REJECTED
    except (FileNotFoundError, UsageError, RunRefusedError) as error:   # refused before anything ran or was written (a closed run, a data folder another process holds)
        _fail(f"error: {error}")
        return EXIT_USAGE
    except Exception as error:   # a task failed: its record is in the state of the run
        _fail(f"falló: {type(error).__name__}: {error}")
        if getattr(args, "verbose", False):
            _fail("".join(traceback.format_exception(error)).rstrip())
        return EXIT_FAILED


__all__ = ["build_parser", "main"]
