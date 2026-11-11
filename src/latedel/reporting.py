"""The honest summary of the evaluation and its copy into the README (RF20, AC23, decisions D16 and D58).

``load_summary`` reads what ``evaluation`` stored (the criteria and the metrics, sealed first and, apart, whatever was computed with another configuration) and
``write_summary`` writes ``reports/evaluation_summary.json`` and ``.md`` from it. The summary is built from the stored tables and not from a fresh computation,
so a monthly run after the test was sealed prints the same text without touching the test. Every criterion that fails says «no supera» with its numbers; before the
test is evaluated the summary says so, shows the figures of V2 labelled as validation, and leaves the criteria pending. After the seal, a warehouse that has no sealed
test (a replay from scratch with other costs, the README's own path for them) takes the sealed criteria and metrics from the seal, says so, and shows what it computed
below them as post-seal, with its own costs (B3 of the evaluation of ``eef9bea``, decision D88). The seal is only read.

``sync_block`` copies the Markdown between two markers of a file (the README, ``<!-- results:start -->`` and ``<!-- results:end -->``): the block of results is
copied, never written by hand, and a test compares it with the summary.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from latedel.config import PipelineConfig
from latedel.deliveries import delivery_plan
from latedel.evaluation import METHOD_LABELS_ES, Criterion, criteria_from_seal, es, load_criteria, load_metrics
from latedel.files import write_atomic
from latedel.registry import read_seal

RESULTS_START = "<!-- results:start -->"
RESULTS_END = "<!-- results:end -->"
SUMMARY_VERSION = 1
SCOPE_TITLES_ES = {"valid_v2": "Validación (V2), no es la prueba", "test": "Prueba (2017-01-08 a 2017-09-30)", "post_break": "Tramo posterior al cambio de forma (informativo)"}
TABLE_METHODS = ("model", "b2_mode", "b5_mode_noon")
Metrics = dict[str, dict[str, dict[str, float | None]]]


@dataclass(frozen=True)
class Summary:
    """What the summary says: the sealed criteria and metrics, the ones computed with another configuration (``post_seal``) and the costs they used.

    ``costs`` are the sealed ones once there is a seal; ``post_seal_costs`` the costs of this run's configuration when they are other. ``sealed_from_seal`` says that the
    sealed criteria and test metrics were read from the seal, because this warehouse has no sealed test.
    """

    run_key: str
    as_of: date
    pending_delivery: str                       # the delivery that evaluates the test, for the message while it is pending
    costs: dict[str, float]
    criteria: list[Criterion] = field(default_factory=list)
    metrics: Metrics = field(default_factory=dict)
    post_seal_criteria: list[Criterion] = field(default_factory=list)
    post_seal_metrics: Metrics = field(default_factory=dict)
    sealed_from_seal: bool = False
    post_seal_costs: dict[str, float] | None = None

    @property
    def test_evaluated(self) -> bool:
        return any(c.criterion_id == "C01" for c in self.criteria)

    @property
    def failed(self) -> list[Criterion]:
        return [c for c in self.criteria if c.passed is False]


def pending_delivery(cfg: PipelineConfig) -> str:
    """The id (without its letter) of the first monthly delivery that is on or after the end of the test plus the purge: the one that evaluates it."""
    limit = cfg.split.test_end + timedelta(days=cfg.split.purge_days)
    return next(s.delivery_id[1:] for s in delivery_plan(cfg.deliveries) if s.kind == "monthly" and s.as_of >= limit)


def load_summary(conn: sqlite3.Connection, cfg: PipelineConfig, *, run_key: str, as_of: date) -> Summary:
    """The summary from the stored evaluation of the warehouse; after the seal, a warehouse without the sealed test takes it from the seal (B3)."""
    criteria, metrics = load_criteria(conn, "sealed"), load_metrics(conn, "sealed")
    own = {"alert_cost": cfg.costs.alert_cost, "late_cost": cfg.costs.late_cost, "effect": cfg.costs.effect}
    seal = read_seal(cfg.paths.seal)
    sealed_costs = dict(seal["launch"]["costs"]) if seal is not None else own
    from_seal = seal is not None and seal.get("test") is not None and not any(c.criterion_id == "C01" for c in criteria)
    if from_seal:   # only what the seal holds is the sealed result: the criteria C01-C09 and the metrics of the test
        criteria, metrics = criteria_from_seal(seal), {"test": {name: dict(values) for name, values in seal["test"]["metrics"].items()}}
    return Summary(
        run_key, as_of, pending_delivery(cfg), sealed_costs, criteria, metrics, load_criteria(conn, "post_seal"), load_metrics(conn, "post_seal"),
        sealed_from_seal=from_seal, post_seal_costs=None if own == sealed_costs else own,
    )


# --- Markdown ------------------------------------------------------------------------------------------------------------------------------------------


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _scope_table(scope: str, metrics: Metrics) -> list[str]:
    methods = metrics.get(scope, {})
    if not methods:
        return []
    lines = [f"**{SCOPE_TITLES_ES[scope]}**", "", _row(["Método", "AUC", "Brier", "ECE", "Marcados (%)", "Precisión", "Recall", "BN por 1.000 pedidos (u.)"]), _row(["---"] * 8)]
    for name in TABLE_METHODS:
        m = methods.get(name)
        if m is None:
            continue
        share = None if m.get("flagged_share") is None else m["flagged_share"] * 100
        lines.append(_row([
            METHOD_LABELS_ES[name], es(m.get("auc")), es(m.get("brier"), 4), es(m.get("ece")), es(share, 1), es(m.get("precision")), es(m.get("recall")), es(m.get("nb_per_1000"), 1),
        ]))
    return [*lines, ""]


def _criteria_table(criteria: list[Criterion]) -> list[str]:
    lines = [_row(["Criterio", "Qué mide", "Veredicto"]), _row(["---"] * 3)]
    lines += [_row([c.criterion_id, c.name_es, c.verdict_es]) for c in criteria]
    return [*lines, ""]


def _costs_es(costs: dict[str, float]) -> str:
    return (
        f"alerta {es(costs['alert_cost'], 1)}, retraso {es(costs['late_cost'], 1)}, efecto {es(costs['effect'], 2)} "
        f"(punto de equilibrio {es(costs['alert_cost'] / (costs['late_cost'] * costs['effect']), 2)})"
    )


def results_markdown(summary: Summary) -> str:
    """The Markdown block of results (the one the README copies)."""
    declared = (
        f"Costos supuestos y declarados, en unidades genéricas: {_costs_es(summary.costs)}. Los criterios y sus umbrales se fijaron antes de construir (`docs/requirements.md`, sección 8.1)."
    )
    lines = [f"Evaluación al {summary.as_of.isoformat()} (corrida `{summary.run_key}`).", ""]
    if not summary.test_evaluated:
        lines += [
            f"La prueba se evalúa con la entrega de {summary.pending_delivery}: hasta entonces los criterios C01 a C10 quedan pendientes. "
            "Las cifras que siguen son de validación (V2) y no dicen nada de la prueba.",
            "", declared, "",
        ]
        lines += _scope_table("valid_v2", summary.metrics)
    else:
        scored = [c for c in summary.criteria if c.passed is not None]
        met, failed = [c for c in scored if c.passed], [c for c in scored if not c.passed]
        if failed:
            lines += [f"El modelo supera {len(met)} de {len(scored)} criterios con umbral y no supera {_ids(failed)}.", ""]
        else:
            lines += [f"El modelo supera {len(scored)} de {len(scored)} criterios con umbral.", ""]
        lines += [declared, ""]
        if summary.sealed_from_seal:
            other = any(c.criterion_id == "C01" for c in summary.post_seal_criteria)
            why = "se calculó con otra configuración, que va debajo como posterior al sello" if other else f"todavía no llegó a la entrega que la evalúa ({summary.pending_delivery})"
            lines += [f"Los criterios C01 a C09 y las cifras de la prueba son los que constan en el sello (`model_seal.json`): este almacén no tiene la prueba sellada porque {why}.", ""]
        lines += _criteria_table(summary.criteria)
        for scope in ("test", "post_break", "valid_v2"):
            lines += _scope_table(scope, summary.metrics)
    if summary.post_seal_criteria or summary.post_seal_metrics:
        lines += [
            "**Posterior al sello**: calculado con otra configuración que la sellada en el lanzamiento. Se informa aparte y no reemplaza lo sellado.", "",
        ]
        if summary.post_seal_costs is not None:
            lines += [f"Costos de esta configuración: {_costs_es(summary.post_seal_costs)}.", ""]
        if summary.post_seal_criteria:
            lines += _criteria_table(summary.post_seal_criteria)
        for scope in ("test", "post_break", "valid_v2"):
            lines += _scope_table(scope, summary.post_seal_metrics)
    return "\n".join(lines).rstrip("\n") + "\n"


def _ids(criteria: list[Criterion]) -> str:
    ids = [c.criterion_id for c in criteria]
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " ni " + ids[-1]


# --- JSON and files ----------------------------------------------------------------------------------------------------------------------------------


def _clean(value: Any) -> Any:
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 6)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _criterion_json(c: Criterion) -> dict[str, Any]:
    return {
        "id": c.criterion_id, "name_es": c.name_es, "value": c.value, "baseline_value": c.baseline_value, "ci_low": c.ci_low, "ci_high": c.ci_high, "threshold_es": c.threshold_es,
        "passed": c.passed, "seen_by_research": c.seen_by_research, "seen_by_planner": c.seen_by_planner, "verdict_es": c.verdict_es,
    }


def summary_json(summary: Summary) -> dict[str, Any]:
    """The summary as a JSON-ready dictionary: no order number, no personal data, only the criteria and the aggregate metrics."""
    document = {
        "version": SUMMARY_VERSION, "run_key": summary.run_key, "as_of": summary.as_of.isoformat(), "state": "evaluated" if summary.test_evaluated else "pending",
        "pending_delivery": None if summary.test_evaluated else summary.pending_delivery, "costs": summary.costs,
        "failed": [c.criterion_id for c in summary.failed], "criteria": [_criterion_json(c) for c in summary.criteria], "metrics": summary.metrics,
        "post_seal": {"criteria": [_criterion_json(c) for c in summary.post_seal_criteria], "metrics": summary.post_seal_metrics},
    }
    if summary.sealed_from_seal:      # the two keys appear only when they say something: the summary of a sealed run is the same as before B3
        document["sealed_from"] = "model_seal.json"
    if summary.post_seal_costs is not None:
        document["post_seal"]["costs"] = summary.post_seal_costs
    return _clean(document)


def write_summary(summary: Summary, reports_dir: Path) -> tuple[Path, Path]:
    """Write ``evaluation_summary.json`` (sorted keys) and ``evaluation_summary.md`` to `reports_dir`, each atomically; returns both paths."""
    folder = Path(reports_dir)
    json_path = write_atomic(folder / "evaluation_summary.json", json.dumps(summary_json(summary), sort_keys=True, indent=1, ensure_ascii=False) + "\n")
    md_path = write_atomic(folder / "evaluation_summary.md", results_markdown(summary))
    return json_path, md_path


def sync_block(path: Path, start_marker: str, end_marker: str, content: str) -> bool:
    """Replace what lies between the two markers of a file with `content`; True if the file changed.

    The file must have exactly one start marker followed by exactly one end marker, otherwise nothing is written. Repeating it with the same content changes nothing.
    """
    path = Path(path)
    text = path.read_bytes().decode("utf-8")
    if text.count(start_marker) != 1 or text.count(end_marker) != 1 or text.index(start_marker) > text.index(end_marker):
        raise ValueError(f"{path.name} necesita exactamente una marca {start_marker!r} seguida de una marca {end_marker!r}")
    head_end = text.index(start_marker) + len(start_marker)
    updated = text[:head_end] + "\n" + content.strip("\n") + "\n" + text[text.index(end_marker) :]
    if updated == text:
        return False
    write_atomic(path, updated)
    return True


def read_block(path: Path, start_marker: str = RESULTS_START, end_marker: str = RESULTS_END) -> str:
    """What lies between the two markers of a file, without the surrounding blank lines."""
    text = Path(path).read_bytes().decode("utf-8")
    if text.count(start_marker) != 1 or text.count(end_marker) != 1 or text.index(start_marker) > text.index(end_marker):
        raise ValueError(f"{Path(path).name} necesita exactamente una marca {start_marker!r} seguida de una marca {end_marker!r}")
    return text[text.index(start_marker) + len(start_marker) : text.index(end_marker)].strip("\n")


def sync_results(readme: Path, summary_md: Path) -> bool:
    """The docs-sync of the results block: copy the Markdown of the summary between the markers of `readme`. True if the README changed."""
    return sync_block(readme, RESULTS_START, RESULTS_END, Path(summary_md).read_bytes().decode("utf-8"))
