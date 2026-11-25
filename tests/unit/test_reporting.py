"""The summary of the evaluation and its copy into a README (U15, AC23, decisions D16 and D58).

The summaries here are written by hand so that the unfavourable case is exact: a criterion that fails says «no supera» with its numbers, in the summary and in the
README that copies it. The real README is U26's: the sync is tried on a temporary file and the project's README is checked to be untouched.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from latedel.evaluation import Criterion
from latedel.reporting import (
    RESULTS_END,
    RESULTS_START,
    Summary,
    pending_delivery,
    read_block,
    results_markdown,
    summary_json,
    sync_block,
    sync_results,
    write_summary,
)

README = f"# Proyecto\n\nTexto antes.\n\n## Los resultados\n\n{RESULTS_START}\nviejo\n{RESULTS_END}\n\n## Siguiente\n\nTexto después.\n"
COSTS = {"alert_cost": 5.0, "late_cost": 20.0, "effect": 0.5}


def criterion(criterion_id: str, passed: bool | None, verdict: str, *, name: str = "Nombre") -> Criterion:
    return Criterion(criterion_id, name, 0.5, 0.4, None, None, "umbral", passed, "no", "no", verdict)


def metrics(auc: float) -> dict:
    row = {"auc": auc, "brier": 0.2, "ece": 0.02, "flagged_share": 0.4, "precision": 0.8, "recall": 0.6, "nb_per_1000": 1500.4}
    return {"valid_v2": {"model": row, "b2_mode": {**row, "auc": auc - 0.03}, "b5_mode_noon": {**row, "auc": auc + 0.01}}}


def unfavourable() -> Summary:
    criteria = [
        criterion("C01", True, "El modelo supera a la regla por modo: AUC 0,770 frente a 0,740."),
        criterion("C03", False, "El modelo no supera a la regla de dos variables: la diferencia de AUC es 0,001, con un intervalo del 95 % de −0,004 a 0,006."),
        criterion("C05", False, "El modelo no supera a la regla de dos variables en beneficio neto con su propio umbral (0,80): 1.504,4 frente a 1.504,4 u. por cada 1.000 pedidos."),
        criterion("C10", None, "Informativo, sin umbral: el AUC del modelo es 0,700."),
    ]
    return Summary("run-M2017-10", date(2017, 10, 31), "2017-10", COSTS, criteria, {"test": metrics(0.77)["valid_v2"], **metrics(0.77)})


def test_unfavourable_summary_says_no_supera(tmp_path):
    """AC23: with an unfavourable summary the README says «no supera» for every criterion that fails, and only for those."""
    summary = unfavourable()
    json_path, md_path = write_summary(summary, tmp_path / "reports")
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8", newline="")
    assert sync_results(readme, md_path) is True

    block = read_block(readme)
    assert block == md_path.read_text(encoding="utf-8").strip("\n"), "the README block is the summary, copied"
    assert "no supera C03 ni C05" in block and "El modelo supera 1 de 3 criterios con umbral" in block
    verdicts = {line.split("|")[1].strip(): line for line in block.splitlines() if re.match(r"\| C\d\d \|", line)}
    assert set(verdicts) == {"C01", "C03", "C05", "C10"}
    assert "no supera" in verdicts["C03"] and "no supera" in verdicts["C05"]
    assert "no supera" not in verdicts["C01"] and "no supera" not in verdicts["C10"]

    document = json.loads(json_path.read_text(encoding="utf-8"))
    assert document["state"] == "evaluated" and document["failed"] == ["C03", "C05"] and document["version"] == 1
    assert [c["id"] for c in document["criteria"]] == ["C01", "C03", "C05", "C10"] and document["criteria"][3]["passed"] is None
    assert all("no supera" in c["verdict_es"] for c in document["criteria"] if c["passed"] is False)

    everything_passes = Summary("run", date(2017, 10, 31), "2017-10", COSTS, [criterion("C01", True, "El modelo supera a la regla por modo: AUC 0,770 frente a 0,740.")])
    assert "El modelo supera 1 de 1 criterios con umbral." in results_markdown(everything_passes)
    assert "no supera" not in results_markdown(everything_passes)


def test_the_costs_are_declared_next_to_the_results_with_their_break_even():
    text = results_markdown(unfavourable())
    assert "alerta 5,0, retraso 20,0, efecto 0,50 (punto de equilibrio 0,50)" in text and "se fijaron antes de construir" in text
    other = Summary("run", date(2017, 10, 31), "2017-10", {"alert_cost": 9.0, "late_cost": 20.0, "effect": 0.5}, [criterion("C01", True, "ok")])
    assert "punto de equilibrio 0,90" in results_markdown(other)


def test_before_the_test_is_evaluated_the_summary_says_when_and_shows_v2_labelled_as_validation():
    pending = Summary("run-L2017-01-07", date(2017, 1, 7), "2017-10", COSTS, [], metrics(0.7756))
    text = results_markdown(pending)
    assert "La prueba se evalúa con la entrega de 2017-10" in text and "quedan pendientes" in text
    assert "**Validación (V2), no es la prueba**" in text and "| Modelo | 0,776 |" in text and "supera" not in text.replace("quedan", "")
    document = summary_json(pending)
    assert document["state"] == "pending" and document["pending_delivery"] == "2017-10" and document["criteria"] == [] and document["failed"] == []
    assert "Prueba (" not in text


def test_what_was_computed_with_another_configuration_is_shown_apart_and_marked():
    summary = Summary(
        "run", date(2017, 10, 31), "2017-10", COSTS, [criterion("C01", True, "El modelo supera a la regla por modo.")], metrics(0.77),
        [criterion("C01", False, "El modelo no supera a la regla por modo.")], metrics(0.6),
    )
    text = results_markdown(summary)
    sealed_part, apart = text.split("**Posterior al sello**")
    assert "El modelo supera a la regla por modo." in sealed_part and "no supera" not in sealed_part
    assert "otra configuración que la sellada" in apart and "El modelo no supera a la regla por modo." in apart
    document = summary_json(summary)
    assert document["post_seal"]["criteria"][0]["passed"] is False and document["criteria"][0]["passed"] is True


def test_the_summary_is_deterministic_and_has_no_order_number_or_personal_data(tmp_path):
    first = write_summary(unfavourable(), tmp_path / "a")
    second = write_summary(unfavourable(), tmp_path / "b")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]
    assert [p.name for p in first] == ["evaluation_summary.json", "evaluation_summary.md"]
    assert not [p for p in (tmp_path / "a").iterdir() if p.suffix == ".tmp"], "no temporary file is left"
    text = first[0].read_text(encoding="utf-8")
    assert "order_id" not in text and "@" not in text and json.loads(text)["run_key"] == "run-M2017-10"


def test_sync_replaces_only_the_block_is_idempotent_and_refuses_broken_markers(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8", newline="")
    assert sync_block(readme, RESULTS_START, RESULTS_END, "nuevo\ncontenido\n") is True
    text = readme.read_text(encoding="utf-8")
    assert text == README.replace("viejo", "nuevo\ncontenido") and text.startswith("# Proyecto\n\nTexto antes.") and text.endswith("Texto después.\n")
    assert sync_block(readme, RESULTS_START, RESULTS_END, "nuevo\ncontenido") is False, "the same content changes nothing"
    assert read_block(readme) == "nuevo\ncontenido"
    for broken in (
        "sin marcas\n", f"{RESULTS_START}\nsolo el inicio\n", f"{RESULTS_END}\nal revés\n{RESULTS_START}\n", f"{RESULTS_START}\na\n{RESULTS_END}\n{RESULTS_START}\nb\n{RESULTS_END}\n",
    ):
        readme.write_text(broken, encoding="utf-8", newline="")
        with pytest.raises(ValueError, match="necesita exactamente una marca"):
            sync_block(readme, RESULTS_START, RESULTS_END, "x")
        with pytest.raises(ValueError, match="necesita exactamente una marca"):
            read_block(readme)
        assert readme.read_text(encoding="utf-8") == broken, "nothing is written when the markers are wrong"


def test_the_delivery_that_evaluates_the_test_is_the_first_monthly_one_after_its_end_plus_the_purge(cfg):
    assert pending_delivery(cfg) == "2017-10"


def test_the_sync_is_tried_on_a_temporary_file_and_the_real_readme_is_not_touched(project_root, tmp_path):
    """U26 owns the real block of the README (``python -m latedel docs-sync`` fills it from the sealed summary): a sync tried on a copy writes the copy and leaves the
    real README as it was. Since U26 the real README has its one pair of markers, so the copy is the real text as it is."""
    real = project_root / "README.es.md"
    before = real.read_bytes()
    assert before.count(RESULTS_START.encode()) == 1 and before.count(RESULTS_END.encode()) == 1
    _json, md = write_summary(unfavourable(), tmp_path / "reports")
    scratch = tmp_path / "README.md"
    scratch.write_bytes(before)
    assert sync_results(scratch, md) is True and scratch.read_bytes() != before
    assert real.read_bytes() == before
