"""The command line: exit codes, incompatible options that stop before anything is written, and the effect of every option (U22, AC29, decision D73).

Exit codes: 0 ok (also a run with no delivery left), 1 a task failed, 2 usage or configuration (also a task refused because its run is closed), 3 the delivery or the
source was rejected. The commands run in this process (``main(argv)``) over copies of the runs that ``conftest.py`` builds once per session; the quick mode as separate
processes is in ``tests/e2e/test_quick_mode.py``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from latedel import cli, pipeline
from latedel.config import load_config
from latedel.deliveries import delivery_plan, mark_status, next_pending, publish
from latedel.ingest import interim_fingerprint, read_interim
from tests.conftest import relocate
from tests.fixtures.helpers import make_cfg, snapshot

FIRST, SECOND = "L2017-01-07", "M2017-01"


@pytest.fixture()
def project(pipeline_source, fixture_csv, tmp_path):
    """A project folder whose source is already read; returns ``(root, config file)``."""
    root = tmp_path / "p"
    relocate(pipeline_source, root, fixture_csv)
    return root, root / "config" / "test.toml"


@pytest.fixture()
def launched(pipeline_closed, fixture_csv, tmp_path):
    """A project folder where the launch run is closed; returns ``(root, config file)``."""
    root = tmp_path / "launched"
    relocate(pipeline_closed, root, fixture_csv)
    return root, root / "config" / "test.toml"


def main(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def task_lines(out: str) -> list[str]:
    return [line.split()[0] for line in out.splitlines() if line.strip() and not line.startswith(("entrega", "corrida")) and line.split()[0] in pipeline.TASK_IDS]


def mark_all_loaded(config_file: Path) -> None:
    """Publish and mark loaded every delivery of the plan, in the manifest (what a project that ran its whole history leaves there)."""
    cfg = load_config(config_file)
    frames, _report = read_interim(cfg.paths.interim_dir)
    folder, sha = cfg.paths.raw_dir / "deliveries", interim_fingerprint(cfg.paths.interim_dir)
    for spec in delivery_plan(cfg.deliveries):
        publish(spec, frames, folder, source_sha256=sha)
        mark_status(folder / "manifest.json", spec.delivery_id, "loaded", "earlier")


# --- AC29: incompatible options exit with 2 before writing anything ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["run", "--only", "load", "--from", "load", "--config", "{config}"], "--only"),
        (["run", "--run-key", "mine", "--config", "{config}"], "--run-key"),
        (["run", "--from", "validate", "--config", "{config}"], "--run-key"),   # a new run would fail on its first task and leave a failed state file
        (["run", "--only", "load", "--config", "{config}"], "--run-key"),
        (["run", "--only", "deliver", "validate", "--config", "{config}"], "--run-key"),
        (["replay", "--until", "2017-03", "--config", "{config}"], "--until"),
        (["replay", "--until", "June", "--config", "{config}"], "--until"),
        (["replay", "--until", "M2019-01", "--config", "{config}"], "M2019-01"),
        (["run", "--only", "nonsense", "--config", "{config}"], "nonsense"),
        (["run", "--from", "nonsense", "--config", "{config}"], "nonsense"),
        (["task", "nonsense", "--config", "{config}"], "nonsense"),
        (["nonsense"], "nonsense"),
        (["run", "--config", "{missing}"], "missing.toml"),
        (["make-fixture", "{out}", "--orders", "5"], "n_orders"),
        (["make-fixture", "{out}", "--seed", "-1"], "seed"),
        (["make-fixture", "{out}", "--signal", "loud"], "loud"),
    ],
)
def test_incompatible_options_exit_2_before_writing(project, capsys, argv, needle):
    root, config = project
    before = snapshot(root)
    words = [a.replace("{config}", str(config)).replace("{missing}", str(root / "missing.toml")).replace("{out}", str(root / "out.csv")) for a in argv]
    code, out, err = main(capsys, *words)
    assert code == 2 and out == ""
    assert needle in err and "Traceback" not in err
    assert snapshot(root) == before   # not even the state file of a run


def test_until_before_the_next_pending_delivery_is_a_usage_error_that_writes_nothing(launched, capsys):
    root, config = launched
    before = snapshot(root)
    code, out, err = main(capsys, "replay", "--until", FIRST, "--config", config)
    assert code == 2 and out == "" and SECOND in err and snapshot(root) == before


# --- the exit codes ------------------------------------------------------------------------------------------------------------------------


def test_exit_0_for_a_task_and_for_a_run_with_nothing_left_to_deliver(project, capsys):
    root, config = project
    code, out, err = main(capsys, "run", "--only", "fetch_source", "deliver", "--config", config, "--run-key", "one")
    assert code == 0 and err == ""
    assert task_lines(out) == ["fetch_source", "deliver"]
    assert f"entrega {FIRST} (hasta 2017-01-07)" in out
    mark_all_loaded(config)
    code, out, err = main(capsys, "run", "--config", config)
    assert code == 0 and "No hay entregas pendientes" in out and err == ""   # the exit is still 0


def test_exit_3_when_the_source_is_rejected_and_nothing_is_written(launched, capsys, malformed):
    root, config = launched
    other = malformed("bad_date")
    shutil.copy(config, root / "config" / "good.toml")
    config.write_text(config.read_text(encoding="utf-8").replace("local_path = ", f'local_path = "{Path(other).as_posix()}"\n#', 1), encoding="utf-8")
    before = snapshot(root / "raw")
    code, out, err = main(capsys, "run", "--config", config)
    assert code == 3 and "rechazad" in err and "V01" in err and out == ""
    assert snapshot(root / "raw") == before and len(list((root / "interim" / "runs").glob("*.json"))) == 2   # only the state file of the rejected run was added
    assert [p.stem for p in (root / "interim" / "runs").glob("*.json") if json.loads(p.read_text(encoding="utf-8"))["status"] == "rejected"]


def test_exit_1_when_a_task_fails_and_the_traceback_only_with_dash_v(project, capsys):
    root, config = project
    quiet = main(capsys, "task", "validate", "--config", config, "--run-key", "quiet")   # nothing delivered in this run: RunStateError
    assert quiet[0] == 1 and "falló: RunStateError" in quiet[2] and "Traceback" not in quiet[2]
    loud = main(capsys, "task", "validate", "--config", config, "--run-key", "loud", "-v")
    assert loud[0] == 1 and loud[2].count("Traceback (most recent call last)") == 1


def test_exit_2_for_a_run_that_is_closed_and_not_a_byte_changes(launched, capsys):
    """``run --run-key <closed run> --from deliver`` once loaded the next delivery under the closed run's key (B1 of project 02)."""
    root, config = launched
    before = snapshot(root)
    for argv in (["--from", "deliver"], ["--only", "deliver"], ["--from", "report"]):
        code, out, err = main(capsys, "run", "--config", config, "--run-key", "r1", *argv)
        assert code == 2 and out == "" and "r1" in err and "cerrada" in err and "falló" not in err and "Traceback" not in err
    code, out, err = main(capsys, "task", "deliver", "--config", config, "--run-key", "r1")
    assert code == 2 and out == "" and "ok" in err
    assert snapshot(root) == before   # the state file of the closed run, the manifest, the deliveries, the warehouse: untouched


# --- every documented option changes the result ---------------------------------------------------------------------------------------------------


def option_strings() -> set[str]:
    """Every option of every command of the parser (the ones argparse adds, ``-h``, left out) and the positional arguments, as ``command option``."""
    parser = cli.build_parser()
    found = set()
    for name, sub in parser._subparsers._group_actions[0].choices.items():
        for action in sub._actions:
            if action.option_strings and action.option_strings != ["-h", "--help"]:
                found.add(f"{name} {max(action.option_strings, key=len)}")
            elif not action.option_strings and action.dest != "help":
                found.add(f"{name} {action.dest}")
    return found


def effect_config(project, capsys, tmp_path, monkeypatch):
    """``--config`` chooses the project: another file writes in another folder, and the default (the environment variable) writes in the first."""
    root, config = project
    other = tmp_path / "other"
    relocate(root, other, Path(load_config(config).source.local_path))
    shutil.rmtree(other / "interim" / "runs", ignore_errors=True)
    monkeypatch.setenv("LATEDEL_CONFIG", str(config))
    before_root, before_other = snapshot(root), snapshot(other)
    assert main(capsys, "run", "--only", "fetch_source", "deliver", "--run-key", "by_env")[0] == 0
    assert snapshot(other) == before_other and snapshot(root) != before_root   # the default is $LATEDEL_CONFIG
    mid = snapshot(root)
    assert main(capsys, "run", "--only", "fetch_source", "deliver", "--run-key", "by_option", "--config", other / "config" / "test.toml")[0] == 0
    assert snapshot(root) == mid and snapshot(other) != before_other   # --config wins over the variable


def effect_config_relative(project, capsys, tmp_path, monkeypatch):
    """A relative ``--config`` is looked up from the project folder, not from the working directory."""
    root, _config = project
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(cli, "DEFAULT_PROJECT_ROOT", root)
    code, out, err = main(capsys, "task", "fetch_source", "--config", "config/test.toml", "--run-key", "rel")
    assert code == 0 and err == "" and (root / "interim" / "runs" / "rel.json").is_file() and list(elsewhere.iterdir()) == []


def effect_verbose(project, capsys, tmp_path, monkeypatch):
    root, config = project
    quiet = main(capsys, "run", "--only", "fetch_source", "--config", config, "--run-key", "q")
    loud = main(capsys, "run", "--only", "fetch_source", "--config", config, "--run-key", "l", "-v")
    assert quiet[2] == "" and "INFO latedel.pipeline: task fetch_source start" in loud[2] and loud[0] == quiet[0] == 0


def effect_only(project, capsys, tmp_path, monkeypatch):
    root, config = project
    code, out, _ = main(capsys, "run", "--only", "fetch_source", "deliver", "--config", config, "--run-key", "mine")
    state = root / "interim" / "runs" / "mine.json"
    assert code == 0 and task_lines(out) == ["fetch_source", "deliver"] and list(json.loads(state.read_text(encoding="utf-8"))["tasks"]) == ["fetch_source", "deliver"]


def effect_from(launched, capsys, tmp_path, monkeypatch):
    root, config = launched    # after the launch, so that the run that continues is a monthly one (the launch trains a model and takes seconds more)
    assert main(capsys, "run", "--only", "fetch_source", "deliver", "--config", config, "--run-key", "mine")[0] == 0
    code, out, _ = main(capsys, "run", "--from", "validate", "--config", config, "--run-key", "mine")
    assert code == 0 and task_lines(out) == list(pipeline.TASK_IDS[2:])   # it continued the run, from validate, with the delivery of its own state
    state = json.loads((root / "interim" / "runs" / "mine.json").read_text(encoding="utf-8"))
    assert state["status"] == "ok" and state["tasks"]["deliver"]["delivery_id"] == SECOND


def effect_run_key(project, capsys, tmp_path, monkeypatch):
    root, config = project
    assert main(capsys, "run", "--only", "fetch_source", "--config", config, "--run-key", "alpha")[0] == 0
    assert main(capsys, "run", "--only", "fetch_source", "--config", config, "--run-key", "beta")[0] == 0
    assert sorted(p.stem for p in (root / "interim" / "runs").glob("*.json")) == ["alpha", "beta"]


def effect_task_run_key(project, capsys, tmp_path, monkeypatch):
    root, config = project
    code, out, _ = main(capsys, "task", "fetch_source", "--config", config, "--run-key", "solo")
    assert code == 0 and out.startswith("fetch_source:") and "built=False" in out
    assert list(json.loads((root / "interim" / "runs" / "solo.json").read_text(encoding="utf-8"))["tasks"]) == ["fetch_source"]


def effect_task(project, capsys, tmp_path, monkeypatch):
    root, config = project
    assert main(capsys, "task", "fetch_source", "--config", config, "--run-key", "a")[0] == 0
    code, out, _ = main(capsys, "task", "deliver", "--config", config, "--run-key", "a")
    assert code == 0 and out.startswith("deliver:") and f"delivery_id={FIRST}" in out
    assert list(json.loads((root / "interim" / "runs" / "a.json").read_text(encoding="utf-8"))["tasks"]) == ["fetch_source", "deliver"]


def effect_until(launched, capsys, tmp_path, monkeypatch):
    root, config = launched
    code, out, err = main(capsys, "replay", "--until", SECOND, "--config", config)
    assert code == 0 and err == "" and "1 corrida" in out and f"entrega {SECOND}" in out and task_lines(out) == list(pipeline.TASK_IDS)
    assert next_pending(root / "raw" / "deliveries" / "manifest.json", delivery_plan(load_config(config).deliveries)).delivery_id == "M2017-02"   # and it went no further


def effect_docs_sync(project, capsys, tmp_path, monkeypatch):
    root, config = project
    (root / "README.md").write_text("# P\n\n<!-- results:start -->\nPendiente\n<!-- results:end -->\n\nFin\n", encoding="utf-8")
    (root / "reports").mkdir()
    (root / "reports" / "evaluation_summary.md").write_text("| C01 | cumple |\n", encoding="utf-8")
    code, out, err = main(capsys, "docs-sync", "--config", config)
    text = (root / "README.md").read_text(encoding="utf-8")
    assert code == 0 and "actualizado" in out and "| C01 | cumple |" in text and "Pendiente" not in text and text.endswith("Fin\n")


def made(capsys, tmp_path, name: str, *options) -> bytes:
    code, out, err = main(capsys, "make-fixture", tmp_path / name, *options)
    assert code == 0 and err == "" and name in out
    return (tmp_path / name).read_bytes()


def effect_orders(project, capsys, tmp_path, monkeypatch):
    base, more = made(capsys, tmp_path, "a.csv", "--orders", 150), made(capsys, tmp_path, "b.csv", "--orders", 250)
    assert base != more and len(more) > len(base) and made(capsys, tmp_path, "c.csv", "--orders", 150) == base   # same options, same bytes


def effect_seed(project, capsys, tmp_path, monkeypatch):
    base, other = made(capsys, tmp_path, "a.csv", "--orders", 150, "--seed", 1), made(capsys, tmp_path, "b.csv", "--orders", 150, "--seed", 2)
    assert base != other and made(capsys, tmp_path, "c.csv", "--orders", 150, "--seed", 1) == base


def effect_signal(project, capsys, tmp_path, monkeypatch):
    base, planted = made(capsys, tmp_path, "a.csv", "--orders", 400), made(capsys, tmp_path, "b.csv", "--orders", 400, "--signal", "standard_market")
    assert base != planted


def effect_output(project, capsys, tmp_path, monkeypatch):
    """A relative output is written under the project folder and not under the working directory; an absolute one stays where it says."""
    root, _config = project
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(cli, "DEFAULT_PROJECT_ROOT", root)
    assert main(capsys, "make-fixture", "data/quick/f.csv", "--orders", 120)[0] == 0
    assert (root / "data" / "quick" / "f.csv").is_file() and list(elsewhere.iterdir()) == []
    absolute = tmp_path / "given" / "g.csv"
    assert main(capsys, "make-fixture", absolute, "--orders", 120)[0] == 0 and absolute.is_file()


def effect_figures_config(project, capsys, tmp_path, monkeypatch, fixture_site):
    """``figures`` reads the site files and writes the figures of the project of ``--config``: with no run yet it says what to run and writes nothing."""
    root, config = project
    missing = main(capsys, "figures", "--config", config)
    assert missing[0] == 2 and "algorithms.json" in missing[2] and "python -m latedel run" in missing[2] and not (root / "figures").exists()
    shutil.copytree(fixture_site, root / "outputs" / "site")
    code, out, err = main(capsys, "figures", "--config", config)
    names = ["split.png", "mode_rates.png", "noon_cutoff.png", "leakage.png", "methods.png", "cost_curve.png", "alerts.png"]
    assert code == 0 and err == "" and [Path(line.split(" ", 1)[1]).name for line in out.splitlines()] == names
    assert sorted(p.name for p in (root / "figures").iterdir()) == sorted(names) and all(line.startswith("escribí ") for line in out.splitlines())


def effect_figures_verbose(project, capsys, tmp_path, monkeypatch, fixture_site):
    root, config = project
    shutil.copytree(fixture_site, root / "outputs" / "site")
    quiet = main(capsys, "figures", "--config", config)
    loud = main(capsys, "figures", "--config", config, "-v")
    assert quiet[0] == loud[0] == 0 and quiet[2] == "" and loud[2].count("INFO latedel.figures: figure ") == 7


EFFECTS = {
    "run --config": effect_config, "replay --config": effect_config_relative, "task --config": effect_config, "docs-sync --config": effect_docs_sync,
    "run --verbose": effect_verbose, "replay --verbose": effect_verbose, "task --verbose": effect_verbose, "docs-sync --verbose": effect_verbose,
    "run --only": effect_only, "run --from": effect_from, "run --run-key": effect_run_key, "task --run-key": effect_task_run_key, "task task": effect_task,
    "replay --until": effect_until, "make-fixture --orders": effect_orders, "make-fixture --seed": effect_seed, "make-fixture --signal": effect_signal,
    "make-fixture output": effect_output, "figures --config": effect_figures_config, "figures --verbose": effect_figures_verbose,
}


@pytest.mark.parametrize("option", sorted(EFFECTS))
def test_every_documented_option_changes_the_result(option, request, capsys, tmp_path, monkeypatch):
    """One check for each option of each command: with it the command does something that it does not do without it. No option is accepted and ignored."""
    effect = EFFECTS[option]
    extra = [request.getfixturevalue("fixture_site")] if effect in (effect_figures_config, effect_figures_verbose) else []
    effect(request.getfixturevalue("launched" if effect in (effect_until, effect_from) else "project"), capsys, tmp_path, monkeypatch, *extra)


def test_the_options_that_are_tested_are_the_options_of_the_parser():
    """A new option without a check of its effect fails here: the list above is the parser's."""
    assert option_strings() == set(EFFECTS)


# --- replay: its control flow, with a stand-in for one run -------------------------------------------------------------------------------------


def fake_run_all(calls: list[str], frames, folder):
    """A stand-in for ``run_all`` that loads the next pending delivery in the manifest and does nothing else (the control flow of ``replay`` only)."""

    def run(cfg, only=None, start=None, run_key=None, *, now=None, on_task=None):
        spec = next_pending(folder / "manifest.json", delivery_plan(cfg.deliveries))
        publish(spec, frames, folder, source_sha256=interim_fingerprint(cfg.paths.interim_dir))
        mark_status(folder / "manifest.json", spec.delivery_id, "loaded", "fake")
        calls.append(spec.delivery_id)
        result = {"delivered": True, "delivery_id": spec.delivery_id, "period_end": spec.outcomes_to.isoformat(), "as_of": spec.as_of.isoformat(), "seconds": 0.1}
        if on_task:
            on_task("deliver", result)
        return [{"task": "deliver", "result": result}]

    return run


def test_replay_runs_the_pending_deliveries_in_a_row_until_the_delivery_asked(project, capsys, monkeypatch):
    root, config = project
    cfg = load_config(config)
    frames, _report = read_interim(cfg.paths.interim_dir)
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "run_all", fake_run_all(calls, frames, cfg.paths.raw_dir / "deliveries"))
    assert len(pipeline.replay(cfg, until="M2017-02")) == 3 and calls == [FIRST, "M2017-01", "M2017-02"]
    code, out, err = main(capsys, "replay", "--config", config, "--until", "M2017-04")
    assert code == 0 and calls[3:] == ["M2017-03", "M2017-04"] and err == "" and "2 corridas" in out
    code, out, err = main(capsys, "replay", "--config", config)
    assert code == 0 and len(calls) == 15 and calls[5] == "M2017-05" and calls[-1] == "C2018-02" and f"{15 - 5} corridas" in out
    code, out, err = main(capsys, "replay", "--config", config)
    assert code == 0 and len(calls) == 15 and "No hay entregas pendientes" in out   # the plan is exhausted: nothing runs, nothing is recorded


def test_a_replay_stops_if_a_run_does_not_load_its_delivery(project, monkeypatch):
    root, config = project
    monkeypatch.setattr(pipeline, "run_all", lambda cfg, **kwargs: [])
    with pytest.raises(pipeline.RunStateError, match=FIRST):
        pipeline.replay(load_config(config))


def test_a_selection_that_starts_at_the_first_task_needs_no_run_key(project, capsys):
    """A selection that cannot start a run is refused; one that starts at ``fetch_source`` still can."""
    root, config = project
    code, out, err = main(capsys, "run", "--only", "fetch_source", "deliver", "--config", config)
    assert code == 0 and err == "" and "fetch_source" in out
    assert len(list((root / "interim" / "runs").glob("*.json"))) == 1


def test_the_help_of_every_command_names_its_options_in_spanish(capsys):
    parser = cli.build_parser()
    for name, sub in parser._subparsers._group_actions[0].choices.items():
        text = sub.format_help()
        assert "python -m latedel" in text and re.search(r"[áéíóú]|[a-z]+ los |corrida|archivo|tarea|entrega|pedidos|resumen", text), name
    assert main(capsys, "--help")[0] == 0


# --- A2 and A4 of the evaluation of eef9bea (decision D90) ----------------------------------------------------------------------------------------------------


def test_a_relative_latedel_config_is_taken_from_the_project_folder_not_from_the_working_directory(project, capsys, tmp_path, monkeypatch):
    """A2: ``LATEDEL_CONFIG=config/pipeline.quick.toml`` was resolved against the working directory, so run from another copy's folder it silently drove that copy's
    ``data/quick``. A relative ``LATEDEL_CONFIG`` is taken from the project folder (``LATEDEL_HOME`` when it is set), as ``--config`` and every relative path of the
    configuration are (D24)."""
    root, config = project
    other = tmp_path / "elsewhere"
    relocate(root, other, Path(load_config(config).source.local_path))
    monkeypatch.chdir(other)                                   # the working directory has a config/test.toml of its own: it must not be the one used
    monkeypatch.delenv("LATEDEL_HOME", raising=False)
    monkeypatch.setenv("LATEDEL_CONFIG", "config/test.toml")
    before_root, before_other = snapshot(root), snapshot(other)
    code, _out, err = main(capsys, "run", "--only", "fetch_source", "deliver", "--run-key", "relative")
    assert code == 2 and str(cli.DEFAULT_PROJECT_ROOT / "config" / "test.toml") in err   # the project's own config folder, which has no such file
    assert snapshot(other) == before_other and snapshot(root) == before_root
    monkeypatch.setenv("LATEDEL_HOME", str(root))              # with LATEDEL_HOME, the file is taken from that folder
    assert main(capsys, "run", "--only", "fetch_source", "deliver", "--run-key", "relative")[0] == 0
    assert snapshot(other) == before_other and snapshot(root) != before_root


def test_docs_sync_with_a_configuration_without_a_readme_says_why_and_writes_nothing(project, capsys):
    """A4: ``docs-sync --config config/pipeline.quick.toml`` always exited 2 with the bare «[Errno 2] No such file or directory» of ``data/quick/README.md``. It still
    exits 2, before writing anything, and says that the configuration's README does not exist (the quick mode has none) and where the summary is."""
    root, config = project
    (root / "reports").mkdir()
    (root / "reports" / "evaluation_summary.md").write_text("| C01 | cumple |\n", encoding="utf-8")
    before = snapshot(root)
    code, out, err = main(capsys, "docs-sync", "--config", config)
    assert code == 2 and out == "" and "Errno" not in err
    assert "README" in err and "no existe" in err and "modo rápido" in err and "evaluation_summary.md" in err
    assert snapshot(root) == before
