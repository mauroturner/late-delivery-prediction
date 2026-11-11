"""The documentation is checked like code (AC23, AC35; plan sections 21 and 22).

U26: the README has the headings of the plan in their order and its results block is the summary the sealed run wrote, never a copy by hand; the README and the model
card say the unfavourable verdicts (C03 and C05) as the summary does, and the model card has the contents the plan lists. These tests read the versioned Markdown and the
versioned reports (``reports/`` holds the outputs of U25) and run nothing, so they cost the suite a few milliseconds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from latedel.reporting import RESULTS_END, RESULTS_START, read_block

README_TITLE = "# Pedidos que van a llegar tarde, antes de que lleguen"
README_HEADINGS = [   # plan, section 22, in this order (AC35)
    "El problema", "La solución", "Los resultados", "En qué consiste", "Los datos", "Los algoritmos", "El producto", "La documentación", "Con IA, de punta a punta",
    "Para qué sirve", "Cómo correrlo de punta a punta", "Paso a paso", "Fuente y licencia",
]
MODEL_CARD_HEADINGS = [   # what plan section 22 lists for the model card, in this order
    "Uso previsto y no previsto", "Datos", "Momento de la predicción", "Variables", "Partición", "Métodos", "Calibración", "Umbral y costos supuestos",
    "Criterios y resultados", "Rendimiento", "Limitaciones", "Reproducibilidad",
]


@pytest.fixture(scope="module")
def readme(project_root: Path) -> str:
    return (project_root / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def model_card(project_root: Path) -> str:
    return (project_root / "docs" / "model-card.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def summary(project_root: Path) -> dict:
    return json.loads((project_root / "reports" / "evaluation_summary.json").read_text(encoding="utf-8"))


def failed_criteria(summary: dict) -> list[dict]:
    return [c for c in summary["criteria"] if c["passed"] is False]


def test_readme_headings_in_order_and_results_block_matches_summary(project_root: Path, readme: str, summary: dict) -> None:
    assert readme.startswith(README_TITLE + "\n")
    assert re.findall(r"^## (.+)$", readme, flags=re.MULTILINE) == README_HEADINGS
    assert readme.count(RESULTS_START) == 1 and readme.count(RESULTS_END) == 1
    block = read_block(project_root / "README.md")
    assert block == (project_root / "reports" / "evaluation_summary.md").read_text(encoding="utf-8").strip("\n")   # what docs-sync copies, byte for byte
    assert readme.index(RESULTS_START) > readme.index("## Los resultados") and readme.index(RESULTS_END) < readme.index("## En qué consiste")
    # AC23: the summary is unfavourable on two criteria, and the README says «no supera» for each, with its number, outside the block too
    failed = failed_criteria(summary)
    assert [c["id"] for c in failed] == summary["failed"] == ["C03", "C05"]
    reading = readme.split(RESULTS_END)[1].split("## En qué consiste")[0]
    for criterion in failed:
        assert criterion["verdict_es"] in block and "no supera" in criterion["verdict_es"]
        assert re.search(rf"\b{criterion['id']}\b[^\n]*no supera|no supera[^\n]*\b{criterion['id']}\b", reading), criterion["id"]
    assert summary["state"] == "evaluated" and summary["post_seal"] == {"criteria": [], "metrics": {}}   # the block is the sealed result, nothing after the seal


def test_the_model_card_has_the_contents_of_the_plan_and_says_the_unfavourable_verdicts(model_card: str, summary: dict) -> None:
    assert re.findall(r"^## (.+)$", model_card, flags=re.MULTILINE) == MODEL_CARD_HEADINGS
    for criterion in summary["criteria"]:
        assert criterion["verdict_es"] in model_card, criterion["id"]   # every verdict as the summary writes it, «no supera» included
    for limit in (
        "parecen generados", "corte del mediodía", "costos supuestos", "cancelados", "cambio de forma", "una sola vez", "FastAPI", "PeakWorkingSetSize",
        "PeakPagefileUsage", "peak_commit_mb",
    ):
        assert limit in model_card, limit
    assert re.search(r"suite rápida[^\n]*\d{3},\d+ s", model_card)   # AC36: measured and written down


def test_the_readme_says_what_is_not_done_and_gives_the_attribution(readme: str) -> None:
    """RNF14 and RNF09: the independent evaluation, the ``.pbix`` and the scheduled Airflow run on the real file are not done, and the README says so; the citation of
    the source is complete; no private address is published."""
    text = readme.lower()
    assert "evaluación independiente" in text and "todavía no" in text and ".pbix" in text
    tail = readme.split("## Fuente y licencia")[1]
    for piece in ("Constante, Fabian; Silva, Fernando; Pereira, António (2019)", "DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS", "Mendeley Data, v5",
                  "http://doi.org/10.17632/8gx2fvg2k6.5", "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"):
        assert piece in tail, piece
    assert "@gmail" not in readme
