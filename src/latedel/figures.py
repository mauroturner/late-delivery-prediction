"""Figures of the documentation, drawn with matplotlib from the site files of a run (plan section 2, U24).

Seven PNG files, always from the real outputs (``outputs/site/algorithms.json``, ``alerts.json`` and ``leakage.json``) and never from constants: how the time was split, the
late rate by shipping mode, the late rate of Same Day by the hour of the order (the noon cut), how much each column that gives the outcome away tells next to the mode alone,
the model against the two rules on the test, the net benefit at each break-even, and what the alerts did month by month. The files are deterministic (no timestamp in the
metadata), so a versioned figure changes only when its data does. Nothing is written unless every figure was drawn; before the test is evaluated the figures that need its
results say so instead of staying empty (the site never shows a number that the seal has not written, D17). Only the cells that the site files publish are drawn, so a
suppressed cell (fewer orders than ``min_cell_orders``) is a gap and never a number.
"""

from __future__ import annotations

import json
import logging
import textwrap
from datetime import date
from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter

from latedel.files import atomic_path

logger = logging.getLogger(__name__)

FIGURE_NAMES = ("split.png", "mode_rates.png", "noon_cutoff.png", "leakage.png", "methods.png", "cost_curve.png", "alerts.png")
SITE_FILES = {"algorithms": "algorithms.json", "alerts": "alerts.json", "leakage": "leakage.json"}
DPI = 130
INK, GRID, MUTED = "#1f2430", "#e4e6ea", "#5b6270"
ORANGE, BLUE, GREY, GOLD, GREEN, RED = "#eb6834", "#2a78d6", "#8a8f98", "#eda100", "#1baf7a", "#c43d3d"
PENDING = "La prueba se evalúa una sola vez, cuando sus resultados ya se conocen (entrega M2017-10)"
METHOD_COLORS = {"model": ORANGE, "b2_mode": GREY, "b5_mode_noon": BLUE}
METHOD_LABELS = {"model": "Modelo", "b2_mode": "Regla por modo (B2)", "b5_mode_noon": "Regla por modo y hora (B5)"}
BLOCK_COLORS = {"train": BLUE, "purge_1": GRID, "valid_v1": GOLD, "valid_v2": ORANGE, "purge_2": GRID, "test": GREEN, "post_break": GREY}
KIND_COLORS = {"leak": RED, "precaution": GOLD}
KIND_LABELS = {"leak": "Delata el resultado", "precaution": "Precaución (se conoce después)"}


# --- data -------------------------------------------------------------------------------------------------------


def load_site(site_dir: Path) -> dict:
    """``{"algorithms": ..., "alerts": ..., "leakage": ...}`` read from the site files of a run; a missing one is an error that says what to run."""
    out = {}
    for key, name in SITE_FILES.items():
        path = Path(site_dir) / name
        if not path.is_file():
            raise FileNotFoundError(f"No existe {path}: corra el flujo (python -m latedel run) antes de dibujar las figuras")
        out[key] = json.loads(path.read_text(encoding="utf-8"))
    return out


# --- drawing helpers --------------------------------------------------------------------------------------------


def _spanish(value: float, digits: int = 0) -> str:
    text = f"{value:,.{digits}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _style(ax, y_label: str | None = None) -> None:
    ax.set_facecolor("white")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    if y_label:
        ax.set_ylabel(y_label, color=MUTED, fontsize=9)


def _figure(width: float = 10, height: float = 4.8) -> Figure:
    fig = Figure(figsize=(width, height), dpi=DPI, facecolor="white")
    FigureCanvasAgg(fig)
    return fig


def _title(fig: Figure, title: str, subtitle: str) -> None:
    fig.text(0.01, 0.975, title, fontsize=13, fontweight="bold", color=INK, va="top")
    fig.text(0.01, 0.925, textwrap.fill(subtitle, 135), fontsize=9, color=MUTED, va="top", linespacing=1.4)   # at most two lines


def _pending(ax, text: str = PENDING, width: int = 70) -> None:
    ax.text(0.5, 0.5, textwrap.fill(text, width), ha="center", va="center", transform=ax.transAxes, color=MUTED, fontsize=11)
    ax.set_xticks([])
    ax.set_yticks([])


def _percent(value: float, _position=None) -> str:
    return f"{_spanish(value * 100)} %"


# --- the figures ------------------------------------------------------------------------------------------------


def draw_split(algorithms: dict) -> Figure:
    fig = _figure()
    _title(fig, "Cómo se partió el tiempo", "Siete bloques seguidos: se entrena, se elige y calibra en V1, se fija el umbral en V2 y la prueba se toca una sola vez. Entre dos bloques hay una purga de siete días.")
    ax = fig.add_axes([0.26, 0.13, 0.71, 0.7])
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    rows = algorithms["split"]
    for i, r in enumerate(rows):
        start, end = date.fromisoformat(r["start"]).toordinal(), date.fromisoformat(r["end"]).toordinal() + 1
        ax.barh(i, end - start, left=start, height=0.6, color=BLOCK_COLORS[r["block"]])
        text = "pocos pedidos (celda oculta)" if r["suppressed"] else f"{_spanish(r['orders'])} pedidos"
        ax.text(end + 6, i, text, va="center", fontsize=8, color=MUTED)
    ax.set_yticks(range(len(rows)), [textwrap.fill(r["label_es"], 30) for r in rows], fontsize=8)
    ax.invert_yaxis()
    first = date.fromisoformat(rows[0]["start"])
    last = date.fromisoformat(rows[-1]["end"])
    halves = [date(y, m, 1) for y in range(first.year, last.year + 1) for m in (1, 7) if first <= date(y, m, 1) <= last]   # every January and July in the span
    ax.set_xticks([d.toordinal() for d in halves], [d.strftime("%Y-%m") for d in halves], fontsize=8)
    ax.set_xlim(first.toordinal() - 10, last.toordinal() + 330)   # room for the order counts to the right of the last bar
    return fig


def draw_mode_rates(algorithms: dict) -> Figure:
    fig = _figure()
    _title(fig, "Tasa de retraso por modo de envío en el entrenamiento", "Parte de los pedidos de cada modo que llegó tarde, con los días programados de ese modo. Esta tasa es la regla simple (B2) contra la que se compara el modelo.")
    ax = fig.add_axes([0.08, 0.15, 0.9, 0.68])
    _style(ax, "Pedidos que llegaron tarde")
    rows = algorithms["mode_rates"]
    heights = [0.0 if r["suppressed"] else r["train_late_rate"] for r in rows]
    ax.bar(range(len(rows)), heights, 0.6, color=[GREY if r["suppressed"] else ORANGE for r in rows])
    ax.set_xticks(range(len(rows)), [f"{r['mode_es']}\n{r['scheduled_days']} días programados" for r in rows])
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(FuncFormatter(_percent))
    for i, r in enumerate(rows):
        label = "celda oculta" if r["suppressed"] else f"{_percent(r['train_late_rate'])} de {_spanish(r['train_orders'])}"
        ax.text(i, heights[i] + 0.02, label, ha="center", fontsize=8, color=MUTED)
    return fig


def draw_noon_cutoff(algorithms: dict) -> Figure:
    fig = _figure()
    _title(fig, "Same Day: la tasa de retraso según la hora del pedido", "Pedidos de Same Day del entrenamiento, por hora de entrada. La línea punteada es el corte del mediodía de la regla B5; una hora con pocos pedidos no se dibuja.")
    ax = fig.add_axes([0.08, 0.15, 0.9, 0.68])
    _style(ax, "Pedidos que llegaron tarde")
    rows = [r for r in algorithms["hour_profile"] if r["mode"] == "Same Day"]
    shown = [r for r in rows if not r["suppressed"]]
    ax.plot([r["hour"] for r in shown], [r["train_late_rate"] for r in shown], color=ORANGE, linewidth=2, marker="o", markersize=4, label="Tasa de retraso")
    ax.axvline(12, color=INK, linewidth=1, linestyle=":", label="Mediodía")
    ax.set_xlim(-0.5, 23.5)
    ax.set_ylim(-0.02, 1.05)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel(f"Hora del pedido ({len(rows) - len(shown)} de 24 horas sin dibujar por tener pocos pedidos)", color=MUTED, fontsize=9)
    ax.yaxis.set_major_formatter(FuncFormatter(_percent))
    ax.legend(frameon=False, fontsize=9, loc="center left")
    return fig


def draw_leakage(leakage: dict) -> Figure:
    fig = _figure()
    _title(fig, "Qué tanto delata cada columna el resultado", "AUC en V2 del modo de envío más esa columna. La línea es el AUC del modo de envío solo; cualquier columna que la supere por mucho se conoce después de crear el pedido y queda fuera del modelo.")
    ax = fig.add_axes([0.28, 0.13, 0.69, 0.7])
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    rows = [c for c in leakage["columns"] if c["auc_with_mode_valid"] is not None]
    reference = leakage["reference"]["auc_mode_only_valid"]
    positions = range(len(rows))
    ax.barh(list(positions), [r["auc_with_mode_valid"] for r in rows], 0.6, color=[KIND_COLORS[r["kind"]] for r in rows])
    ax.set_yticks(list(positions), [r["column_es"] for r in rows], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlim(0.5, 1.02)
    if reference is not None:
        ax.axvline(reference, color=INK, linewidth=1, linestyle="--")
        ax.text(reference, -0.75, f"Modo solo: {_spanish(reference, 3)}", fontsize=8, color=INK, ha="center")
    for i, r in enumerate(rows):
        ax.text(r["auc_with_mode_valid"] + 0.005, i, _spanish(r["auc_with_mode_valid"], 3), va="center", fontsize=8, color=MUTED)
    ax.set_xlabel("AUC en V2", color=MUTED, fontsize=9)
    kinds = sorted({r["kind"] for r in rows})
    ax.legend(handles=[Patch(color=KIND_COLORS[k], label=KIND_LABELS[k]) for k in kinds], frameon=False, fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2)
    return fig


def draw_methods(algorithms: dict) -> Figure:
    fig = _figure()
    _title(fig, "El modelo y las dos reglas, en la prueba", "AUC y beneficio neto por 1.000 pedidos, con los costos supuestos y el umbral de cada método elegido en V2. El beneficio neto no usa F1.")
    rows = [m for m in algorithms["methods"] if m["scope"] == "test"]
    axes = fig.subplots(1, 2, gridspec_kw={"left": 0.08, "right": 0.97, "top": 0.78, "bottom": 0.13, "wspace": 0.25})
    for ax, key, title, digits in ((axes[0], "auc", "AUC", 3), (axes[1], "net_benefit_per_1000", "Beneficio neto por 1.000 pedidos", 1)):
        _style(ax)
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        if not rows:
            _pending(ax, width=34)
            continue
        values = [m[key] if m[key] is not None else 0.0 for m in rows]
        ax.bar(range(len(rows)), values, 0.6, color=[METHOD_COLORS[m["method"]] for m in rows])
        ax.set_xticks(range(len(rows)), [METHOD_LABELS[m["method"]].replace(" (", "\n(") for m in rows], fontsize=8)
        for i, v in enumerate(values):
            ax.text(i, v, _spanish(v, digits), ha="center", va="bottom", fontsize=8, color=MUTED)
        if key == "auc":
            ax.set_ylim(0.5, max(values + [0.55]) + 0.05)
    return fig


def draw_cost_curve(algorithms: dict) -> Figure:
    fig = _figure()
    _title(fig, "El beneficio neto según cuánto cuesta una alerta frente a un retraso", "Beneficio neto por 1.000 pedidos de la prueba en cada punto de equilibrio entre 0,10 y 0,90; cada método con su propio umbral elegido en V2.")
    ax = fig.add_axes([0.08, 0.15, 0.9, 0.68])
    _style(ax, "Beneficio neto por 1.000 pedidos")
    rows = algorithms["cost_curve"]
    if not rows:
        _pending(ax)
        return fig
    for key, method in (("nb_model", "model"), ("nb_b2_mode", "b2_mode"), ("nb_b5_mode_noon", "b5_mode_noon")):
        ax.plot([r["break_even"] for r in rows], [r[key] for r in rows], color=METHOD_COLORS[method], linewidth=2, label=METHOD_LABELS[method])
    threshold = algorithms["threshold"]
    if threshold is not None:
        ax.axvline(threshold["break_even"], color=INK, linewidth=1, linestyle=":", label="Punto de equilibrio supuesto")
    ax.set_xlabel("Punto de equilibrio (costo de una alerta sobre lo que evita de un retraso)", color=MUTED, fontsize=9)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: _spanish(v, 2)))
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    return fig


def draw_alerts(alerts: dict) -> Figure:
    fig = _figure()
    _title(fig, "Las alertas mes a mes", "Pedidos nuevos de cada mes con su alerta, y los retrasos que se esperaban según el riesgo contra los que se observaron una vez que llegaron los resultados.")
    axes = fig.subplots(1, 2, gridspec_kw={"left": 0.07, "right": 0.97, "top": 0.78, "bottom": 0.17, "wspace": 0.22})
    rows = alerts["monthly"]
    shown = [r for r in rows if not r["suppressed"]]
    months = [r["month"] for r in shown]
    _style(axes[0])
    axes[0].set_title("Alertas por mes", loc="left", fontsize=11, color=INK)
    _style(axes[1])
    axes[1].set_title("Retrasos esperados y observados", loc="left", fontsize=11, color=INK)
    if not shown:
        _pending(axes[0], "Todavía no hay pedidos puntuados", 34)
        _pending(axes[1], "Todavía no hay pedidos puntuados", 34)
        return fig
    positions = list(range(len(shown)))
    axes[0].bar(positions, [r["orders"] - r["alerts"] for r in shown], 0.7, color=GRID, label="Sin alerta")
    axes[0].bar(positions, [r["alerts"] for r in shown], 0.7, bottom=[r["orders"] - r["alerts"] for r in shown], color=ORANGE, label="Con alerta")
    axes[0].legend(frameon=False, fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2)
    axes[1].plot(positions, [r["late_expected"] for r in shown], color=BLUE, linewidth=2, linestyle="--", marker="o", markersize=3.5, label="Esperados")
    axes[1].plot(positions, [r["late_observed"] for r in shown], color=INK, linewidth=2, marker="o", markersize=3.5, label="Observados")
    axes[1].legend(frameon=False, fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=2)
    for ax in axes:
        ax.set_xticks(positions, months, rotation=60, ha="right", fontsize=7)
        ax.set_ylim(bottom=0)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: _spanish(v)))
    return fig


def draw_all(site: dict) -> dict[str, Figure]:
    """The seven figures, by file name, from the site data; no file is touched."""
    return {
        "split.png": draw_split(site["algorithms"]),
        "mode_rates.png": draw_mode_rates(site["algorithms"]),
        "noon_cutoff.png": draw_noon_cutoff(site["algorithms"]),
        "leakage.png": draw_leakage(site["leakage"]),
        "methods.png": draw_methods(site["algorithms"]),
        "cost_curve.png": draw_cost_curve(site["algorithms"]),
        "alerts.png": draw_alerts(site["alerts"]),
    }


# --- entry point ------------------------------------------------------------------------------------------------


def make_figures(site_dir: Path, out_dir: Path) -> list[Path]:
    """Draw the seven figures from the site files of `site_dir` and write them to `out_dir`; returns the paths in a fixed order.

    Nothing is written if a site file is missing or a figure fails to draw.
    """
    drawn = draw_all(load_site(site_dir))
    paths = []
    for name in FIGURE_NAMES:
        path = Path(out_dir) / name
        with atomic_path(path) as temporary:
            drawn[name].savefig(temporary, format="png", metadata={"Software": None})
        paths.append(path)
        logger.info("figure %s written", path)
    return paths
