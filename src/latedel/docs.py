"""The tables of the data dictionary, written from the semantic model by ``python -m latedel docs-sync`` (plan section 22, AC35).

The model (``latedel.dashboard.semantic_model.MODEL``) is the specification the export checks every Parquet file against; this module writes one section per exported table,
with every column, its type, its unit and its meaning, between the ``columns`` markers of ``docs/data-dictionary.md``, with the same ``sync_block`` that writes the results
block of the README. A test compares the block with this rendering and every Parquet file of the real run with the columns it lists.
"""

from __future__ import annotations

from pathlib import Path

from latedel.dashboard.semantic_model import SemanticModel
from latedel.reporting import sync_block

COLUMNS_START = "<!-- columns:start -->"
COLUMNS_END = "<!-- columns:end -->"
DICTIONARY_NAME = "data-dictionary.md"
KIND_ES = {"dimension": "Dimensión", "fact": "Hecho"}
KEY_ES = {"dimension": "Clave", "fact": "Grano"}
DTYPE_ES = {"int32": "entero 32", "int64": "entero 64", "float64": "decimal", "string": "texto", "bool": "verdadero/falso", "date": "fecha"}
NO_UNIT = "—"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_dictionary_tables(model: SemanticModel) -> str:
    """One section per exported table, in the order of the model: what it is, its key or grain, and every column with its type, unit and meaning."""
    lines: list[str] = []
    for table in model.tables:
        keys = ", ".join(f"`{k}`" for k in table.key)
        lines += [f"### `{table.name}`", "", f"{KIND_ES[table.kind]}. {_cell(table.description_es)} {KEY_ES[table.kind]}: {keys}.", ""]
        lines += ["| Columna | Tipo | Unidad | Significado |", "| --- | --- | --- | --- |"]
        lines += [f"| `{c.name}` | {DTYPE_ES[c.dtype]} | {_cell(c.unit or NO_UNIT)} | {_cell(c.description_es)} |" for c in table.columns]
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def dictionary_path(figures_dir: Path) -> Path:
    """Where the dictionary lives: next to the figures folder (``docs/`` in the project, ``data/quick/`` in quick mode, where there is none)."""
    return Path(figures_dir).parent / DICTIONARY_NAME


def sync_dictionary(dictionary: Path, model: SemanticModel) -> bool:
    """Write the tables between the ``columns`` markers of the dictionary; True if the file changed (a second run with the same model changes nothing)."""
    return sync_block(Path(dictionary), COLUMNS_START, COLUMNS_END, render_dictionary_tables(model))
