"""The documentation is checked like code (AC23, AC35; plan sections 21 and 22).

U26: the README has the headings of the numbered outline (ESTRUCTURA-PROYECTOS.md, at the portfolio's root) in their order and its results block is the summary the sealed run wrote, never a copy by hand; the README and the model
card say the unfavourable verdicts (C03 and C05) as the summary does, and the model card has the contents the plan lists. These tests read the versioned Markdown and the
versioned reports (``reports/`` holds the outputs of U25) and run nothing, so they cost the suite a few milliseconds.

U27: every decision has its seven fields; the test count the documents give is the one of the collection; every memory figure names its counter; the dictionary covers
the 53 columns of the source with the destination ``schema.py`` gives them and its tables are the ones ``docs-sync`` writes from the semantic model; the runbook has R1 to
R13 and everything the reviews left for it; the step by step names every unit and cites commits that exist; the links resolve; and the root rows of project 03 carry the
real figures (plan, section 27).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from latedel.reporting import RESULTS_END, RESULTS_START, read_block

README_TITLE = "# Gradient boosting calibrado para la predicción de entregas tardías"
README_HEADINGS = [   # the numbered outline of the portfolio's ESTRUCTURA-PROYECTOS.md, then the appendices (AC35)
    "1. Resumen ejecutivo", "2. Datasets y preparación", "3. Arquitectura y metodología", "4. Producto final", "5. Conclusión",
    "Resultados completos de la corrida", "Cómo correrlo de punta a punta", "Paso a paso", "Fuente y licencia",
]
README_SUBSECTIONS = [
    "1.1 Problema de negocio y solución", "1.2 Criterios de éxito e impacto",
    "2.1 Fuentes, alcance y granularidad", "2.2 Saneamiento y reglas de calidad",
    "3.1 Fuente e ingesta", "3.2 Orquestación y transformación", "3.3 Almacenamiento y modelado de datos",
    "3.4 Experimentación y Machine Learning", "3.5 Consumo y gobernanza",
    "4.1 Entregables operativos", "4.2 Lectura de resultados actuales",
    "5.1 Evaluación frente a criterios de éxito", "5.2 Limitaciones metodológicas", "5.3 Trabajo futuro",
]
README_ML_SUBSECTIONS = [
    "3.4.1 Ingeniería de características y línea base (baseline)", "3.4.2 Decisiones de modelado y ablación (ADR)",
    "3.4.3 Protocolo de validación (backtesting / cross-validation)",
]
READING = ("### 5.1 Evaluación frente a criterios de éxito", "### 5.2 Limitaciones metodológicas")   # where the README reads the results
MODEL_CARD_HEADINGS = [   # what plan section 22 lists for the model card, in this order
    "Uso previsto y no previsto", "Datos", "Momento de la predicción", "Variables", "Partición", "Métodos", "Calibración", "Umbral y costos supuestos",
    "Criterios y resultados", "Rendimiento", "Limitaciones", "Reproducibilidad",
]


def root_document(project_root: Path, name: str) -> str:
    """A document at the portfolio's root. The project is also published on its own repository, where the test that reads it is skipped."""
    path = project_root.parent / name
    if not path.is_file():
        pytest.skip(f"{name} is at the root of the portfolio, which is not here")
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme(project_root: Path) -> str:
    return (project_root / "README.es.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def model_card(project_root: Path) -> str:
    return (project_root / "docs" / "model-card.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def summary(project_root: Path) -> dict:
    return json.loads((project_root / "reports" / "evaluation_summary.json").read_text(encoding="utf-8"))


def failed_criteria(summary: dict) -> list[dict]:
    return [c for c in summary["criteria"] if c["passed"] is False]


def test_readme_headings_in_order_and_results_block_matches_summary(project_root: Path, readme: str, summary: dict) -> None:
    check_the_english_readme(project_root, readme)
    assert readme.startswith(README_TITLE + "\n")
    assert re.findall(r"^## (.+)$", readme, flags=re.MULTILINE) == README_HEADINGS
    assert re.findall(r"^### (\d.+)$", readme, flags=re.MULTILINE) == README_SUBSECTIONS
    assert re.findall(r"^#### (\d.+)$", readme, flags=re.MULTILINE) == README_ML_SUBSECTIONS
    assert readme.count(RESULTS_START) == 1 and readme.count(RESULTS_END) == 1
    block = read_block(project_root / "README.es.md")
    assert block == (project_root / "reports" / "evaluation_summary.md").read_text(encoding="utf-8").strip("\n")   # what docs-sync copies, byte for byte
    # the generated block is an appendix: after the five sections, before the commands
    assert readme.index(RESULTS_START) > readme.index("## Resultados completos de la corrida") and readme.index(RESULTS_END) < readme.index("## Cómo correrlo de punta a punta")
    # AC23: the summary is unfavourable on two criteria, and the README says «no supera» for each, with its number, outside the block too (in 5.1)
    failed = failed_criteria(summary)
    assert [c["id"] for c in failed] == summary["failed"] == ["C03", "C05"]
    reading = readme.split(READING[0])[1].split(READING[1])[0]
    assert RESULTS_START not in reading
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
    """RNF14 and RNF09: the check of the corrections of the independent evaluation, the ``.pbix`` and the scheduled Airflow run on the real file are not done, and the README says so; the citation of
    the source is complete; no private address is published."""
    text = readme.lower()
    assert "evaluación independiente" in text and "todavía no" in text and ".pbix" in text
    # S104 (f) of the site: no section about AI and no agent or model credited (a new test would change the stated count of the suite)
    assert "Con IA, de punta a punta" not in readme
    assert not re.search(r"\bagentes?\b|\b(?:Haiku|Opus|Sonnet)\b|Gentle AI|\bIA\b", readme)
    tail = readme.split("## Fuente y licencia")[1]
    for piece in ("Constante, Fabian; Silva, Fernando; Pereira, António (2019)", "DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS", "Mendeley Data, v5",
                  "http://doi.org/10.17632/8gx2fvg2k6.5", "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"):
        assert piece in tail, piece
    assert "@gmail" not in readme


# --- U27: the decisions, the stated test count, the memory figures, the dictionary, the runbook, the step by step and the root rows (AC35) -------------------

COLUMNS_START, COLUMNS_END = "<!-- columns:start -->", "<!-- columns:end -->"
DECISION_FIELDS = ("Contexto", "Opciones", "Decisión", "Motivo", "Afecta", "Quién", "Estado")   # AC35: every decision, D01 to the last
COUNTERS = ("PeakWorkingSetSize", "PeakPagefileUsage", "ru_maxrss", "conjunto de trabajo", "memoria comprometida")   # by its API name or by the name of what it counts
MEMORY_FIGURE = re.compile(r"\d[\d.]*(?:,\d+)?\s?(?:MB|GB|MiB|GiB)\b")
# a sentence that gives a peak or a limit of memory (the size of a file in MB is not a memory figure)
MEMORY_WORDS = re.compile(r"pico|peak_|conjunto de trabajo|comprometid|l[íi]mites? de[^.]*memoria|memoria[^.]*(?:l[íi]mite|tope)", re.IGNORECASE)
SENTENCE_END = re.compile(r"(?<=\.)\s+")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
UNITS = [f"U{n:02d}" for n in range(1, 28)] + [f"H{n}" for n in range(1, 10)]


def read(project_root: Path, name: str) -> str:
    return (project_root / "docs" / name).read_text(encoding="utf-8")


def root_rows(project_root: Path) -> str:
    """The section of project 03 in the root ``SITE-UPDATES.md``, up to the next project or the end."""
    text = root_document(project_root, "SITE-UPDATES.md")
    assert text.count("\n## 03 ") == 1
    return re.split(r"\n## 0\d ", text.split("\n## 03 ")[1])[0]


def destination(column: str) -> str:
    """Where ``schema.py`` sends a column of the source, in the words the dictionary uses."""
    from latedel import schema

    for kind, columns in (("personal", schema.PERSONAL_COLUMNS), ("resultado", schema.LEAKING_COLUMNS), ("repetida", schema.DUPLICATE_COLUMNS),
                          ("constante", schema.CONSTANT_COLUMNS)):
        if column in columns:
            return kind
    in_order, in_line = column in schema.SOURCE_TO_ORDER, column in schema.SOURCE_TO_LINE
    if in_order or in_line:
        return "pedido y línea" if in_order and in_line else "pedido" if in_order else "línea"
    assert column in schema.ORDER_TIME_COLUMNS, column
    return "permitida, no llevada"


def fast_suite_count(request: pytest.FixtureRequest, project_root: Path) -> int:
    """The number of tests of the fast suite. When this session is the fast suite itself (the test paths of the configuration, its default markers and no selection),
    its own collection; otherwise ``pytest --collect-only`` in another process, which costs about 3 s (the suite has about 5 s to spare under its 150 s)."""
    option = request.config.option
    whole = (
        request.config.args_source == pytest.Config.ArgsSource.TESTPATHS and not option.keyword and not option.deselect and not getattr(option, "lf", False)
        and option.markexpr == "not real_data and not docker"
    )
    if whole:
        return len(request.session.items)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=project_root, capture_output=True, text=True, timeout=180, encoding="utf-8",
    )
    found = re.search(r"^(\d+)(?:/\d+)? tests? collected", done.stdout, flags=re.MULTILINE)
    assert found, done.stdout[-500:] + done.stderr[-500:]
    return int(found.group(1))


def test_every_decision_has_seven_fields(project_root: Path) -> None:
    text = read(project_root, "decisions.md")
    entries = re.split(r"^## (?=D\d+ )", text, flags=re.MULTILINE)[1:]
    numbers = [int(re.match(r"D(\d+)", e).group(1)) for e in entries]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) >= 85   # no gap and no repeat, up to U27's own
    missing = [f"D{n:02d}: {field}" for n, entry in zip(numbers, entries, strict=True) for field in DECISION_FIELDS if f"- **{field}**: " not in entry]
    assert missing == []
    states = {f"D{n:02d}": re.search(r"^- \*\*Estado\*\*: (.+)$", e, flags=re.MULTILINE).group(1) for n, e in zip(numbers, entries, strict=True)}
    assert [d for d, s in states.items() if not re.match(r"(vigente|pendiente del dueño|reemplazada por D\d+)", s)] == []


def test_stated_test_count_matches_collection(request: pytest.FixtureRequest, project_root: Path, readme: str, model_card: str) -> None:
    """B3 of project 02: the documents said one test count after a correction had changed it. The count is that of the collection, in the README, the model card and
    the root rows; the seconds next to it are measured and written by hand (AC36)."""
    stated = f"{fast_suite_count(request, project_root):,}".replace(",", ".") + " pruebas"
    assert re.findall(r"\b\d{1,3}(?:\.\d{3})* pruebas\b", readme) == [stated]   # the README states one count: this one
    [suite] = re.findall(r"^- \*\*AC36, la suite rápida\*\*.*$", model_card, flags=re.MULTILINE)
    assert f"para {stated}" in suite
    assert stated in root_rows(project_root)


def test_every_memory_figure_names_its_counter(project_root: Path, readme: str, model_card: str) -> None:
    """Lesson A3 of project 02 (RNF03): a memory figure without its counter cannot be compared with another. Every sentence of the documents that gives a peak or a limit
    of memory in MB or GB names the counter, by its API name (``PeakWorkingSetSize``, ``PeakPagefileUsage``, ``ru_maxrss``) or by what it counts (the working set, the
    committed memory)."""
    documents = {"README.md": readme, "docs/model-card.md": model_card, "SITE-UPDATES.md (03)": root_rows(project_root)}
    for name in ("data-dictionary.md", "runbook.md", "paso-a-paso.md", "review-advisories.md", "decisions.md"):
        documents[f"docs/{name}"] = read(project_root, name)
    figures, unnamed = 0, []
    for name, text in documents.items():
        for line in text.splitlines():
            for sentence in SENTENCE_END.split(line):
                if MEMORY_FIGURE.search(sentence) and MEMORY_WORDS.search(sentence):
                    figures += 1
                    if not any(counter in sentence for counter in COUNTERS):
                        unnamed.append(f"{name}: {sentence.strip()[:160]}")
    assert unnamed == []
    assert figures >= 10   # the documents do state memory figures: the check is not vacuous


def test_the_dictionary_covers_every_source_column_and_its_tables_are_the_generated_ones(project_root: Path) -> None:
    import pyarrow.parquet as pq

    from latedel.dashboard.semantic_model import MODEL
    from latedel.docs import render_dictionary_tables
    from latedel.schema import SOURCE_COLUMNS

    text = read(project_root, "data-dictionary.md")
    source = text.split("## Archivo de origen")[1].split("\n## ")[0]
    rows = dict(re.findall(r"^\| `([^`]+)` \| ([^|]+?) \| ", source, flags=re.MULTILINE))
    assert list(rows) == list(SOURCE_COLUMNS)   # the 53 columns, in the order of the file
    assert rows == {column: destination(column) for column in SOURCE_COLUMNS}   # with the destination schema.py gives each one
    assert text.count(COLUMNS_START) == 1 and text.count(COLUMNS_END) == 1
    block = text.split(COLUMNS_START)[1].split(COLUMNS_END)[0]
    assert block.strip() == render_dictionary_tables(MODEL).strip()   # what docs-sync writes
    for table in MODEL.tables:
        section = block.split(f"### `{table.name}`\n")[1].split("\n### ")[0]
        documented = re.findall(r"^\| `([a-z_0-9]+)` \| [^|]+ \| ([^|]+) \| ", section, flags=re.MULTILINE)
        assert tuple(name for name, _unit in documented) == table.column_names and all(unit.strip() for _name, unit in documented), table.name
        assert pq.read_schema(project_root / "outputs" / "powerbi" / f"{table.name}.parquet").names == list(table.column_names), table.name


def test_docs_sync_writes_the_dictionary_tables_and_a_second_run_changes_nothing(fixture_csv: Path, tmp_path: Path, capsys) -> None:
    from latedel import cli
    from latedel.dashboard.semantic_model import MODEL
    from latedel.docs import render_dictionary_tables
    from tests.fixtures.helpers import write_config

    config = write_config(tmp_path, fixture_csv)
    (tmp_path / "README.md").write_text(f"# P\n\n{RESULTS_START}\nPendiente\n{RESULTS_END}\n", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "evaluation_summary.md").write_text("| C01 | supera |\n", encoding="utf-8")
    dictionary = tmp_path / "data-dictionary.md"   # next to the figures folder of the configuration (`figures`), as docs/ is in the project
    dictionary.write_text(f"# D\n\n{COLUMNS_START}\nviejo\n{COLUMNS_END}\n\nal final\n", encoding="utf-8")
    assert cli.main(["docs-sync", "--config", str(config)]) == 0
    assert capsys.readouterr().out.splitlines() == ["README.md actualizado", "data-dictionary.md actualizado"]
    text = dictionary.read_text(encoding="utf-8")
    assert render_dictionary_tables(MODEL).strip() in text and "viejo" not in text and text.endswith("al final\n")
    assert "| C01 | supera |" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert cli.main(["docs-sync", "--config", str(config)]) == 0
    assert capsys.readouterr().out.splitlines() == ["README.md ya estaba al día", "data-dictionary.md ya estaba al día"]


def test_docs_sync_without_a_dictionary_still_syncs_only_the_readme(fixture_csv: Path, tmp_path: Path, capsys) -> None:
    """The command as it was before U27: with no dictionary next to the figures folder (the quick mode has none), it writes the README block and says only that."""
    from latedel import cli
    from tests.fixtures.helpers import write_config

    config = write_config(tmp_path, fixture_csv)
    (tmp_path / "README.md").write_text(f"# P\n\n{RESULTS_START}\nPendiente\n{RESULTS_END}\n", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "evaluation_summary.md").write_text("| C01 | supera |\n", encoding="utf-8")
    assert cli.main(["docs-sync", "--config", str(config)]) == 0
    assert capsys.readouterr().out.splitlines() == ["README.md actualizado"]
    assert not (tmp_path / "data-dictionary.md").exists()


def test_the_runbook_has_r1_to_r13_and_takes_in_every_line_left_for_it(project_root: Path) -> None:
    text = read(project_root, "runbook.md")
    entries = re.split(r"^## (?=R\d+ )", text, flags=re.MULTILINE)[1:]
    assert [entry.split()[0] for entry in entries] == [f"R{n}" for n in range(1, 14)]   # plan, section 22
    for entry in entries:
        assert "**Síntoma.**" in entry and "**Qué hacer.**" in entry, entry.splitlines()[0]
    left = read(project_root, "review-advisories.md").split("## Para el runbook (U27)")[1]
    items = [line for line in left.splitlines() if line.startswith("- **R")]
    assert len(items) >= 14
    for item in items:
        label = re.match(r"- \*\*(R\d+)", item).group(1)
        assert any(entry.startswith(f"{label} ") for entry in entries), label
        missing = [term for term in re.findall(r"`([^`]+)`", item) if term not in text]
        assert missing == [], f"{item[:60]}: {missing}"   # every name, path and command the item gives is in the runbook


def test_the_step_by_step_names_every_unit_and_cites_commits_that_exist(project_root: Path) -> None:
    text = read(project_root, "paso-a-paso.md")
    assert [unit for unit in UNITS if not re.search(rf"\b{unit}\b", text)] == []
    hashes = sorted(set(re.findall(r"`([0-9a-f]{7})`", text)))
    assert len(hashes) >= 42
    if shutil.which("git") is None or not (project_root.parent / ".git").exists():
        pytest.skip("there is no Git history to check the commits against")
    done = subprocess.run(["git", "cat-file", "--batch-check"], cwd=project_root, input="".join(f"{h}^{{commit}}\n" for h in hashes), capture_output=True, text=True)
    assert [line for line in done.stdout.splitlines() if line.endswith("missing")] == []


def test_relative_links_and_images_point_to_files_that_exist(project_root: Path) -> None:
    broken = []
    for document in [project_root / "README.md", project_root / "README.es.md", *sorted((project_root / "docs").glob("*.md"))]:
        text = document.read_text(encoding="utf-8")
        for target in [*LINK.findall(text), *IMAGE.findall(text)]:
            if not re.match(r"^(https?:|mailto:)", target) and not (document.parent / target).resolve().exists():
                broken.append(f"{document.name}: {target}")
    assert broken == []


def test_the_root_rows_of_project_03_carry_the_real_figures(project_root: Path) -> None:
    rows = root_rows(project_root)
    table = [line for line in rows.split("### ")[0].splitlines() if re.match(r"^\| \d+ \|", line)]
    assert [int(line.split("|")[1]) for line in table] == list(range(1, 15))   # plan, section 27: the fourteen rows
    for figure in ("180.519", "65.752", "62.897", "14.527", "0,773", "0,742", "0,774", "1.459,4", "1.467,3", "1.323,1", "0,66", "105,14 s", "921,6 MB", "2.824,1 MB"):
        assert figure in rows, figure
    assert rows.count("no supera") >= 2 and "C03" in rows and "C05" in rows and "@gmail" not in rows
    expansion = root_document(project_root, "LANDING-EXPANSION.md")
    marked = {line.split("|")[1].strip() for line in expansion.splitlines() if line.startswith("| ") and "03 Pedidos que llegan tarde" in line}
    assert marked == {"NumPy", "SciPy", "SQLite", "Apache Parquet con pyarrow", "matplotlib", "pytest", "PostgreSQL"}   # D27: the ones this project uses (SciPy too, B6), and no new row


def advisories(text: str) -> dict[tuple[str, str], str]:
    """Every advisory of the review file by (section, id), with its resolution: the bold words after «Resolución:» («corregido», «rechazado», «abierta»…), or an empty
    string when it has none. The section «Para el runbook» lists what the runbook says, not advisories."""
    found: dict[tuple[str, str], str] = {}
    for section in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title = section.splitlines()[0]
        if title.startswith("Para el runbook"):
            continue
        for block in re.split(r"^(?=- \*\*)", section, flags=re.MULTILINE)[1:]:
            ident = re.match(r"- \*\*([^*]+)\*\*", block).group(1)
            resolution = re.search(r"^  - Resolución: \*\*([^*]+)\*\*", block, flags=re.MULTILINE)
            found[(title, ident)] = resolution.group(1) if resolution else ""
    return found


def test_open_advisories_match_review_file(project_root: Path, readme: str) -> None:
    """AC35 and B5 of the evaluation of eef9bea (plan, section 21): every advisory of every review, and every finding of the independent evaluation, has its resolution in
    ``docs/review-advisories.md``; the advisories the README says are open are the ones that file calls open («abierta»), no more and no fewer."""
    found = advisories(read(project_root, "review-advisories.md"))
    assert len(found) >= 90 and [key for key, resolution in found.items() if not resolution] == []
    assert any(title.startswith("H9 ") for title, _ in found) and any(title.startswith("Evaluación independiente") for title, _ in found)
    opened = sorted(ident for (_title, ident), resolution in found.items() if resolution.startswith("abierta"))
    [stated] = re.findall(r"^Advertencias abiertas: (.+)$", readme, flags=re.MULTILINE)
    assert (sorted(re.findall(r"`([^`]+)`", stated.split(" (")[0])) or ["ninguna"]) == (opened or ["ninguna"]), stated


def check_the_english_readme(project_root: Path, readme: str) -> None:
    """README.md is the page of the project in English; README.es.md is the full one, which ``docs-sync`` and the other tests check."""
    english = (project_root / "README.md").read_text(encoding="utf-8")
    assert "**English** · [Español](README.es.md)" in english and "[English](README.md) · **Español**" in readme
    assert english.count("7 of 9 success criteria met") == 2 and "undefined" not in english and "@gmail" not in english
    for target in ("docs/screenshots/dashboard.png", "docs/screenshots/demo.png", "docs/model-card.md", "docs/evaluation.md"):
        assert f"]({target})" in english and (project_root / target).is_file(), target
