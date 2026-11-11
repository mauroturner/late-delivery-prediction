"""The HTML design of the Power BI report, generated from the semantic model and the exported Parquet (RF22, AC25, decision D21).

One self-contained page (inline CSS, inline SVG, no scripts, no fonts or images from the network) with the steps to build the report in Power BI Desktop, the diagram
of the model, the nine relationships, the Power Query code of each table, the DAX measures and the five pages placed on a 12 x 9 grid, each visual with a preview drawn
from the real tables. Nothing here is typed by hand twice: change ``semantic_model.py`` and render again. Adapted from project 02 (it is not imported from it).

Two rules for what a preview shows. Every text that comes from the data goes through ``html.escape`` (a product called ``<script>`` is text). And an aggregate cell with
fewer orders than ``min_cell_orders`` is hidden, with no number (D07): the previews that group the orders by market, region, department, hour or month show a dash there.
"""

from __future__ import annotations

import logging
from html import escape
from pathlib import Path

import pandas as pd

from latedel.dashboard import svg
from latedel.dashboard.semantic_model import (
    PAGE_COLUMNS,
    PAGE_HEIGHT_PX,
    PAGE_ROWS,
    PAGE_WIDTH_PX,
    Page,
    SemanticModel,
    Visual,
    dax_measure_refs,
    validate_model,
)
from latedel.export import ExportMeta
from latedel.files import write_atomic

logger = logging.getLogger(__name__)

MIN_CELL_ORDERS = 20       # the default of [site] min_cell_orders; the task passes the configured value
CELL_W, CELL_H = PAGE_WIDTH_PX / PAGE_COLUMNS, PAGE_HEIGHT_PX / PAGE_ROWS
HEADER_PX = 26             # height of a visual's title row
TITLE = "Diseño del tablero de Power BI: pedidos que van a llegar tarde, antes de que lleguen"
SECTIONS = (
    ("armado", "Cómo armarlo en Power BI Desktop"),
    ("modelo", "El modelo en estrella"),
    ("relaciones", "Relaciones"),
    ("consultas", "Power Query: una consulta por tabla"),
    ("medidas", "Medidas DAX"),
    ("paginas", "Las cinco páginas"),
    ("fuente", "Fuente y licencia"),
)
KIND_ES = {
    "slicer": "Segmentador", "card": "Tarjeta", "lineChart": "Gráfico de líneas", "barChart": "Gráfico de barras", "clusteredColumnChart": "Columnas agrupadas",
    "matrix": "Matriz", "table": "Tabla", "textbox": "Cuadro de texto",
}
SHORT_METHOD = {"model": "Modelo", "b2_mode": "B2 por modo", "b5_mode_noon": "B5 por modo y hora"}
HIDDEN = "—"
CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#f3f2ef;--text:#0b0b0b;--text-2:#52514e;--muted:#6f6e69;--line:#d9d8d3;--accent:#256abf;
--series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;--series-4:#eda100;--series-5:#e87ba4;--series-6:#008300;--series-7:#6250d6;--series-8:#e34948}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--surface:#1a1a19;--panel:#242422;--text:#f0efec;--text-2:#c3c2b7;--muted:#a3a299;--line:#3a3a37;--accent:#6da7ec;
--series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--series-4:#c98500;--series-5:#d55181;--series-6:#008300;--series-7:#9085e9;--series-8:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--panel:#242422;--text:#f0efec;--text-2:#c3c2b7;--muted:#a3a299;--line:#3a3a37;--accent:#6da7ec;
--series-1:#3987e5;--series-2:#d95926;--series-3:#199e70;--series-4:#c98500;--series-5:#d55181;--series-6:#008300;--series-7:#9085e9;--series-8:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--text);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;line-height:1.25;margin:0 0 4px}h2{font-size:1.3rem;margin:48px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:1.05rem;margin:28px 0 8px}p,li{max-width:78ch}a{color:var(--accent)}
.lead{color:var(--text-2);margin:0 0 16px}.meta{display:flex;flex-wrap:wrap;gap:8px 24px;color:var(--text-2);font-size:.9rem;margin:12px 0}
nav ul{display:flex;flex-wrap:wrap;gap:4px 16px;padding:0;list-style:none;margin:16px 0}
code,pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.85rem}
pre{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px 12px;overflow-x:auto;margin:6px 0;white-space:pre}
table{border-collapse:collapse;width:100%;font-size:.88rem}th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{color:var(--text-2);font-weight:600;position:sticky;top:0;background:var(--surface)}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}.warn{border-left:3px solid var(--series-4);padding:4px 12px;background:var(--panel);margin:12px 0}
.model-svg{width:100%;height:auto;display:block}.model-svg .box{fill:var(--panel);stroke:var(--line)}.model-svg .dim{stroke:var(--series-1)}
.model-svg .fact{stroke:var(--series-2)}.model-svg text{fill:var(--text);font-size:12px}.model-svg .small{fill:var(--text-2);font-size:10px}
.model-svg .rel{stroke:var(--muted);stroke-width:1;fill:none;opacity:.55}.model-svg .card{font-weight:700;fill:var(--accent)}
.canvas-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--panel);padding:8px}
.canvas{display:grid;grid-template-columns:repeat(12,1fr);grid-template-rows:repeat(9,1fr);gap:6px;aspect-ratio:16/9;min-width:960px;max-width:1280px}
.visual{background:var(--surface);border:1px solid var(--line);border-radius:6px;overflow:hidden;display:flex;flex-direction:column;min-width:0;min-height:0}
.visual header{display:flex;justify-content:space-between;gap:6px;padding:2px 8px;font-size:.75rem;color:var(--text-2);height:26px;align-items:center}
.visual header b{color:var(--text);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.visual header span{white-space:nowrap;color:var(--muted)}
.visual .body{flex:1;min-height:0;padding:0 8px 3px;overflow:hidden;font-size:.75rem}.visual .body svg{max-width:100%;height:auto;display:block}
.big{font-size:1.2rem;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sub{color:var(--muted);font-size:.65rem;line-height:1.1}
.chips{display:flex;flex-wrap:nowrap;gap:3px;overflow:hidden}.chip{white-space:nowrap;border:1px solid var(--line);border-radius:10px;padding:0 6px;font-size:.7rem;color:var(--text-2)}
.visual table{font-size:.7rem}.visual th,.visual td{padding:1px 5px}.rows p{margin:1px 0;font-size:.75rem}
svg .svg-grid{stroke:var(--line);stroke-width:1}svg .svg-axis{stroke:var(--muted);stroke-width:1}svg .svg-reference{stroke:var(--text-2);stroke-width:1;stroke-dasharray:2 3}
svg .svg-tick,svg .svg-legend,svg .svg-note,svg .svg-value{fill:var(--text-2);font-size:10px}svg .svg-value{fill:var(--text)}svg .svg-on-fill{fill:var(--text);font-size:10px}
svg .svg-hit{fill:transparent}svg .svg-hit:hover{fill:var(--text);fill-opacity:.15}
.placement td:first-child{white-space:nowrap}
@media print{.canvas-wrap{break-inside:avoid}body{font-size:12px}}
"""


# --- data ---------------------------------------------------------------------------------------------------------


class _Data:
    """The exported tables and the figures the previews are drawn from (a missing file is an empty table with its columns)."""

    def __init__(self, outputs_dir: Path, model: SemanticModel, min_cell: int) -> None:
        self.dir = Path(outputs_dir)
        self.min_cell = min_cell
        self.t: dict[str, pd.DataFrame] = {}
        for table in model.tables:
            path = self.dir / f"{table.name}.parquet"
            self.t[table.name] = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=list(table.column_names))
        geo = self.t["dim_geo"].set_index("geo_key")
        product = self.t["dim_product"].set_index("product_key")
        modes = self.t["dim_shipping_mode"].sort_values("sort_order")
        self.mode_order = list(modes["mode_key"])
        self.mode_name = dict(zip(modes["mode_key"], modes["mode_es"], strict=True))
        order = self.t["fact_order"]
        self.order = order.assign(
            market=order["geo_key"].map(geo["market"]), region=order["geo_key"].map(geo["order_region"]), country=order["geo_key"].map(geo["order_country"]),
            product=order["main_product_key"].map(product["product_name"]), department=order["main_product_key"].map(product["department_name"]),
            is_late_b=order["is_late"].eq(True), is_alert_b=order["is_alert"].eq(True),
        )
        self.scored = self.order[self.order["risk"].notna()]
        self.closed = self.order[self.order["outcome_status"] == "closed"]
        asof = self.t["dim_as_of"]
        self.latest_label = str(asof.sort_values("as_of_key")["as_of_label"].iloc[-1]) if len(asof) else "—"
        scored_on = pd.to_datetime(self.scored["scored_as_of_date"]) if len(self.scored) else pd.Series(dtype="datetime64[ns]")
        self.latest_date = scored_on.max() if len(scored_on) else None      # the alerts are shown as the team saw them on the last day that scored orders
        self.model_row = self.t["dim_model"].iloc[0] if len(self.t["dim_model"]) else None

    def hidden(self, n: float) -> bool:
        """A cell of fewer orders than the minimum cell size is not shown (D07)."""
        return n < self.min_cell

    def preferred(self, name: str) -> pd.DataFrame:
        """The rows of an evaluation fact: the sealed ones if there are any, otherwise all of them."""
        frame = self.t[name]
        sealed = frame[frame["seal_state"] == "sealed"] if "seal_state" in frame else frame
        return sealed if len(sealed) else frame

    def scope(self, name: str) -> tuple[pd.DataFrame, str]:
        """The rows of an evaluation fact for the test if it was evaluated, otherwise for V2, with the Spanish name of that scope."""
        frame = self.preferred(name)
        for scope, label in (("test", "prueba"), ("valid_v2", "validación (V2), no es la prueba")):
            part = frame[frame["scope"] == scope] if "scope" in frame else frame.iloc[0:0]
            if len(part):
                return part, label
        return frame.iloc[0:0], "sin evaluar"


def _fmt(value, digits: int = 1) -> str:
    """A number in Spanish with at least one decimal where it is abbreviated (4,8 M, not 5 M)."""
    return svg.format_number(value, max(digits, 1))


def _pct(value, digits: int = 1) -> str:
    return HIDDEN if value is None or pd.isna(value) else f"{float(value) * 100:.{digits}f} %".replace(".", ",")


def _table_html(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    head = "".join(f'<th{" class=num" if i in numeric else ""}>{escape(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td{" class=num" if i in numeric else ""}>{escape(str(c))}</td>' for i, c in enumerate(row)) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body or '<tr><td>Sin datos</td></tr>'}</tbody></table>"


def _size(v: Visual) -> tuple[int, int]:
    """Pixels available for a visual's drawing (the canvas is 1280 x 720)."""
    return max(int(v.w * CELL_W - 22), 60), max(int(v.h * CELL_H - HEADER_PX - 12), 40)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _card(big: str, sub: str = "") -> str:
    return f'<div class="big">{escape(big)}</div><div class="sub">{escape(sub)}</div>'


def _note(text: str) -> str:
    return f'<div class="rows"><p>{escape(text)}</p></div>'


def _ticks(labels: list[str], per: int = 6) -> list[tuple[float, str]]:
    step = max(1, len(labels) // per)
    return [(float(i), label) for i, label in enumerate(labels) if i % step == 0]


# --- cells: counts and rates by group, hidden under the minimum size ----------------------------------------------------------


def _rates(data: _Data, frame: pd.DataFrame, keys: list[str], flag: str) -> pd.DataFrame:
    """Orders (``n``) and the share with `flag` (``rate``) by `keys`; ``rate`` is None where the cell has fewer orders than the minimum."""
    if frame.empty:
        return pd.DataFrame(columns=[*keys, "n", "rate"])
    grouped = frame.groupby(keys, dropna=False).agg(n=("order_id", "size"), hits=(flag, "sum")).reset_index()
    grouped["rate"] = [None if data.hidden(n) else hits / n for n, hits in zip(grouped["n"], grouped["hits"], strict=True)]
    return grouped


def _rate_matrix(data: _Data, frame: pd.DataFrame, row: str, col: str, flag: str, *, corner: str = "", row_names: dict | None = None, col_names: dict | None = None) -> str:
    """A table of the share with `flag` by `row` and `col`; a cell with fewer orders than the minimum shows a dash."""
    cells = _rates(data, frame, [row, col], flag)
    if cells.empty:
        return _table_html(["Sin datos"], [])
    lookup = {(getattr(r, row), getattr(r, col)): r.rate for r in cells.itertuples()}
    present = set(cells[row])
    rows = [k for k in (data.mode_order if row == "mode_key" else sorted(cells[row].dropna().unique())) if k in present]
    cols = [k for k in (data.mode_order if col == "mode_key" else sorted(cells[col].dropna().unique())) if k in set(cells[col])]
    row_names, col_names = row_names or {}, col_names or {}
    return _table_html(
        [corner, *[str(col_names.get(c, c)) for c in cols]], [[str(row_names.get(r, r)), *[_pct(lookup.get((r, c))) for c in cols]] for r in rows], set(range(1, len(cols) + 1)),
    )


def _rate_bars(data: _Data, v: Visual, frame: pd.DataFrame, key: str, flag: str, top: int = 8) -> str:
    w, h = _size(v)
    cells = _rates(data, frame, [key], flag)
    shown = cells[cells["rate"].notna()].sort_values(["rate", key], ascending=[False, True]).head(top)
    return svg.bar_chart([(str(k), float(r) * 100) for k, r in zip(shown[key], shown["rate"], strict=True)], w, h, v.title_es, unit="%")


# --- page 1, alerts ------------------------------------------------------------------------------------------------------------


def _slicer(data: _Data, field: str) -> str:
    table, column = field.split("[")[0], field.split("[")[1].rstrip("]")
    values = data.t[table][column].dropna() if column in data.t[table] else pd.Series(dtype=object)
    shown = [str(x) for x in values.drop_duplicates().astype(str).sort_values()][:4]
    chips = "".join(f'<span class="chip">{escape(x)}</span>' for x in shown)
    more = f'<span class="chip">+{values.nunique() - len(shown)}</span>' if values.nunique() > len(shown) else ""
    return f'<div class="chips">{chips or "Sin datos"}{more}</div>'


def _open_alerts(data: _Data) -> pd.DataFrame:
    """The alerts issued up to the last day that scored orders whose outcome was not known that day, highest risk first."""
    order = data.order
    if order.empty or data.latest_date is None:
        return order.iloc[0:0]
    scored_on = pd.to_datetime(order["scored_as_of_date"])
    known = pd.to_datetime(order["outcome_known_date"])
    mine = order[order["is_alert_b"] & (scored_on <= data.latest_date) & (known.isna() | (known > data.latest_date))]
    return mine.sort_values(["risk", "order_id"], ascending=[False, True])


def _alerts_table(data: _Data) -> str:
    dates = data.t["dim_date"].set_index("date_key")["date"]
    rows = [
        [str(r.order_id), str(dates.get(r.order_date_key, "")), data.mode_name.get(r.mode_key, r.mode_key), str(r.market), str(r.region), str(r.country),
         _shorten(str(r.product), 30), _fmt(float(r.risk), 2)]
        for r in _open_alerts(data).head(9).itertuples()
    ]
    return _table_html(["Pedido", "Fecha", "Modo", "Mercado", "Región", "País", "Producto principal", "Riesgo"], rows, {7})


def _alerts_by_mode(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    alerts = data.scored[data.scored["is_alert_b"]].groupby("mode_key").size()
    return svg.bar_chart([(data.mode_name.get(k, k), float(alerts.get(k, 0))) for k in data.mode_order if k in set(data.scored["mode_key"])], w, h, v.title_es, unit="alertas")


def _costs(data: _Data) -> str:
    m = data.model_row
    if m is None:
        return _card("Sin modelo registrado")
    text = f"Costos supuestos, no medidos: alerta {_fmt(float(m['alert_cost']), 1)} u., retraso {_fmt(float(m['late_cost']), 1)} u., efecto {_fmt(float(m['effect']), 2)}"
    return _card(text, f"punto de equilibrio {_fmt(float(m['break_even']), 2)}; los datos no traen costos")


# --- page 3, follow-up -----------------------------------------------------------------------------------------------------------


def _months(data: _Data) -> pd.DataFrame:
    """Sums of the follow-up by month of the order, with the month's label; the precision and the recall are None where the month has too few closed alerts."""
    track = data.t["fact_tracking_monthly"]
    if track.empty:
        return pd.DataFrame(columns=["month_key", "label"])
    labels = data.t["dim_month"].set_index("month_key")["year_month_label"]
    sums = track.drop(columns="mode_key").groupby("month_key").sum().reset_index()
    sums["label"] = sums["month_key"].map(labels)
    sums["precision"] = [None if data.hidden(c) else a / c for a, c in zip(sums["alerts_late"], sums["closed_alerts"], strict=True)]
    sums["recall"] = [None if data.hidden(a + m) else a / (a + m) for a, m in zip(sums["alerts_late"], sums["late_missed"], strict=True)]
    sums["alerted"] = [None if data.hidden(o) else a / o for a, o in zip(sums["alerts"], sums["orders_scored"], strict=True)]
    return sums.sort_values("month_key")


def _lines_by_month(data: _Data, v: Visual, series: dict[str, str], percent: bool) -> str:
    w, h = _size(v)
    months = _months(data)
    if months.empty:
        return svg.line_chart({}, w, h, v.title_es)
    scale = 100.0 if percent else 1.0
    lines = {name: [(float(i), float(x) * scale if x is not None and not pd.isna(x) else float("nan")) for i, x in enumerate(months[column])] for name, column in series.items()}
    return svg.line_chart(lines, w, h, v.title_es, x_ticks=_ticks([str(x) for x in months["label"]]), unit="%" if percent else "")


def _expected_observed(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    months = _months(data)
    groups = [(str(r.label), {"Esperados": float(r.late_expected), "Observados": float(r.late_closed)}) for r in months.itertuples()]
    return svg.grouped_bar_chart(groups[:: max(1, len(groups) // 12)], w, h, v.title_es)


def _follow_up_cards(data: _Data) -> dict[str, str]:
    track = data.t["fact_tracking_monthly"]
    if track.empty:
        return {}
    total = track.drop(columns="mode_key").sum()
    precision = total["alerts_late"] / total["closed_alerts"] if total["closed_alerts"] else None
    recall = total["alerts_late"] / (total["alerts_late"] + total["late_missed"]) if total["alerts_late"] + total["late_missed"] else None
    m = data.model_row
    benefit = None if m is None else total["alerts_late"] * float(m["late_cost"]) * float(m["effect"]) - total["closed_alerts"] * float(m["alert_cost"])
    return {
        "Precisión de las alertas": _card(_pct(precision), "alertas cerradas que llegaron tarde"),
        "Recall de las alertas": _card(_pct(recall), "pedidos tarde que tenían alerta"),
        "Beneficio neto estimado (con costos supuestos)": _card("—" if benefit is None else f"{_fmt(float(benefit), 1)} u.", "costos supuestos, no medidos"),
        "Riesgo medio": _card(_pct(data.scored["risk"].mean() if len(data.scored) else None), "de los pedidos puntuados"),
    }


# --- page 4, validation of the model ------------------------------------------------------------------------------------------------



def _calibration(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    part, scope = data.scope("fact_calibration")
    mine = part[part["method"] == "model"].sort_values("bin")
    if mine.empty:
        return svg.line_chart({}, w, h, v.title_es)
    points = [(float(r["p_mean"]), float("nan") if data.hidden(r["orders"]) or pd.isna(r["observed"]) else float(r["observed"])) for r in mine.to_dict("records")]
    return svg.line_chart({"Modelo": points, "Diagonal": [(0.0, 0.0), (1.0, 1.0)]}, w, h, f"{v.title_es} ({scope})", dashed=("Diagonal",))


def _cost_curve(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    part, scope = data.scope("fact_cost_sensitivity")
    series = {SHORT_METHOD[m]: [(float(r["break_even"]), float(r["nb_per_1000"])) for r in part[part["method"] == m].sort_values("break_even").to_dict("records")]
              for m in ("model", "b2_mode", "b5_mode_noon") if len(part[part["method"] == m])}
    return svg.line_chart(series, w, h, f"{v.title_es} ({scope})")


def _roc(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    part, scope = data.scope("fact_roc")
    series = {SHORT_METHOD[m]: [(float(r["fpr"]), float(r["tpr"])) for r in part[part["method"] == m].sort_values("point").to_dict("records")]
              for m in ("model", "b2_mode", "b5_mode_noon") if len(part[part["method"] == m])}
    if series:
        series["Diagonal"] = [(0.0, 0.0), (1.0, 1.0)]
    return svg.line_chart(series, w, h, f"{v.title_es} ({scope})", dashed=("Diagonal",))


def _criteria(data: _Data) -> pd.DataFrame:
    return data.preferred("fact_evaluation_summary").sort_values("criterion_id")


def _criteria_table(data: _Data) -> str:
    rows = [
        [r.criterion_id, _shorten(str(r.name_es), 44), _shorten(str(r.threshold_es), 40), str(r.seen_by_research), str(r.seen_by_planner), _shorten(str(r.verdict_es), 90)]
        for r in _criteria(data).itertuples()
    ]
    return _table_html(["ID", "Criterio", "Umbral", "Visto: investigación", "Visto: planificación", "Veredicto"], rows)


def _verdict(data: _Data) -> str:
    summary = _criteria(data)
    if summary.empty:
        return '<div class="sub">Sin veredicto: la prueba se evalúa con la entrega de 2017-10</div>'
    failed = summary[summary["passed"].eq(False)]
    row = failed.iloc[0] if len(failed) else summary.iloc[0]
    return f'<div class="rows"><p><b>{escape(str(row["criterion_id"]))}</b> (el primero que no se cumple, como ejemplo del criterio elegido)</p><p>{escape(str(row["verdict_es"]))}</p></div>'


def _leakage_bars(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    leak = data.t["fact_leakage"].sort_values("auc_with_mode_valid", ascending=False)
    return svg.bar_chart([(str(r.column_es), float(r.auc_with_mode_valid)) for r in leak.itertuples() if pd.notna(r.auc_with_mode_valid)][:8], w, h, v.title_es)


def _importance_bars(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    imp = data.t["fact_importance"].sort_values("auc_drop", ascending=False).head(8)
    return svg.bar_chart([(str(r.feature_es), float(r.auc_drop)) for r in imp.itertuples()], w, h, v.title_es)


# --- page 5, quality and runs ---------------------------------------------------------------------------------------------------------


def _latest_run(data: _Data) -> pd.Series | None:
    runs = data.t["dim_pipeline_run"]
    runs = runs[runs["as_of_key"].notna()] if len(runs) else runs
    return runs.sort_values(["as_of_key", "run_key"]).iloc[-1] if len(runs) else None


def _last_ok_date(data: _Data) -> tuple[str, str]:
    runs = data.t["dim_pipeline_run"]
    ok = runs[runs["status"] == "ok"] if len(runs) else runs
    if len(ok) and ok["as_of_date"].notna().any():
        return str(ok["as_of_date"].max()), "de la última corrida terminada bien"
    return data.latest_label, "de la última fecha de cálculo (aún no hay corridas terminadas)"


def _blocking_failed(data: _Data) -> str:
    v = data.t["fact_validation"]
    return str(int(((v["severity"] == "bloqueante") & (v["status"] == "falla")).sum())) if len(v) else "0"


def _rules_table(data: _Data) -> str:
    v, runs = data.t["fact_validation"], data.t["dim_pipeline_run"]
    if v.empty or runs.empty:
        return _table_html(["Regla"], [])
    keys = runs[runs["run_key"].isin(v["run_key"])].sort_values(["as_of_key", "run_key"])
    if keys.empty:
        return _table_html(["Regla"], [])
    latest = keys.iloc[-1]["run_key"]
    part = v[v["run_key"] == latest].sort_values("rule_id")
    rows = [[r.rule_id, _shorten(str(r.name_es), 56), r.severity, _fmt(r.rows_affected, 0), _fmt(r.orders_affected, 0), r.status] for r in part.itertuples()]
    return _table_html(["ID", "Regla", "Severidad", "Filas", "Pedidos", "Estado"], rows, {3, 4})


def _runs_table(data: _Data) -> str:
    runs = data.t["dim_pipeline_run"].sort_values(["as_of_key", "run_key"])
    rows = [[r.run_key, str(r.started_at), str(r.status), str(r.delivery_id), str(r.as_of_date), _fmt(r.orders_delivery, 0), _fmt(r.orders_scored, 0), _fmt(r.alerts, 0),
             _fmt(r.peak_working_set_mb)] for r in runs.tail(8).itertuples()]
    return _table_html(["Corrida", "Inicio", "Estado", "Entrega", "Fecha de cálculo", "Pedidos", "Puntuados", "Alertas", "Memoria (MB)"], rows, {5, 6, 7, 8})


# --- the preview of a visual ----------------------------------------------------------------------------------------------------------


def _preview(data: _Data, v: Visual) -> str:
    """The body of a visual: its drawing or figure from the real tables."""
    title, order = v.title_es, data.order
    if v.kind == "slicer":
        return _slicer(data, v.fields[0])
    if v.kind == "textbox":
        return _note(title)
    scored, closed = data.scored, data.closed
    alerts = int(scored["is_alert_b"].sum()) if len(scored) else 0
    when, when_note = _last_ok_date(data)
    run = _latest_run(data)
    cancelled = int((order["outcome_status"] == "cancelled").sum()) if len(order) else 0
    cards = {
        "Pedidos puntuados": lambda: _card(_fmt(float(len(scored)), 0), data.latest_label),
        "Alertas": lambda: _card(_fmt(float(alerts), 0), "al crearse el pedido"),
        "Alertado (%)": lambda: _card(_pct(alerts / len(scored)) if len(scored) else HIDDEN, "de los pedidos puntuados"),
        "Retrasos esperados": lambda: _card(_fmt(float(scored["risk"].sum()), 1), "suma de los riesgos"),
        "Umbral": lambda: _card(_fmt(float(data.model_row["threshold"]), 2) if data.model_row is not None else HIDDEN, "elegido en V2 por beneficio neto"),
        "Alertas abiertas a la fecha": lambda: _card(_fmt(float(len(_open_alerts(data))), 0), "al " + (str(data.latest_date.date()) if data.latest_date is not None else "—")),
        "Costos supuestos": lambda: _costs(data),
        "Veredicto": lambda: _verdict(data),
        "Última fecha de cálculo": lambda: _card(when, when_note),
        "Estado de la última corrida": lambda: _card(str(run["status"]) if run is not None else "—", str(run["run_key"]) if run is not None else "sin corridas registradas"),
        "Reglas bloqueantes fallidas": lambda: _card(_blocking_failed(data), "en las corridas filtradas"),
        "Pedidos": lambda: _card(_fmt(float(len(order)), 0), "de las entregas cargadas"),
        "Pedidos cancelados": lambda: _card(_fmt(float(cancelled), 0), "fuera de la tarea (D05)"),
        **{name: (lambda text=text: text) for name, text in _follow_up_cards(data).items()},
    }
    if v.kind == "card" and title in cards:
        return cards[title]()
    charts = {
        "Alertas abiertas a la fecha, ordenadas por riesgo": lambda: _alerts_table(data),
        "Alertas por modo de envío": lambda: _alerts_by_mode(data, v),
        "Alertado (%) por mercado y modo": lambda: _rate_matrix(data, scored, "market", "mode_key", "is_alert_b", corner="Mercado", col_names=data.mode_name),
        "Tasa de retraso observada por modo de envío y mercado": lambda: _rate_matrix(data, closed, "mode_key", "market", "is_late_b", corner="Modo", row_names=data.mode_name),
        "Tasa de retraso observada por modo de envío y hora del pedido": lambda: _hour_heatmap(data, v),
        "Tasa de retraso observada por región": lambda: _rate_bars(data, v, closed, "region", "is_late_b"),
        "Tasa de retraso observada por departamento": lambda: _rate_bars(data, v, closed, "department", "is_late_b"),
        "Días reales y programados por modo de envío": lambda: _days_by_mode(data, v),
        "Precisión y recall mensuales de las alertas cerradas": lambda: _lines_by_month(data, v, {"Precisión": "precision", "Recall": "recall"}, True),
        "Retrasos esperados y observados por mes": lambda: _expected_observed(data, v),
        "Alertado (%) por mes": lambda: _lines_by_month(data, v, {"Alertado": "alerted"}, True),
        "Criterios C01 a C10": lambda: _criteria_table(data),
        "Curva ROC del modelo, B2 y B5 en la prueba": lambda: _roc(data, v),
        "Calibración": lambda: _calibration(data, v),
        "Curva de costo: beneficio por 1.000 pedidos según el punto de equilibrio": lambda: _cost_curve(data, v),
        "AUC en validación con cada columna que delata el resultado: por qué quedan fuera": lambda: _leakage_bars(data, v),
        "Importancia por permutación": lambda: _importance_bars(data, v),
        "Reglas de validación": lambda: _rules_table(data),
        "Corridas": lambda: _runs_table(data),
    }
    if title in charts:
        return charts[title]()
    return f'<div class="sub">{escape(", ".join(v.fields))}</div>'


def _hour_heatmap(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    cells = _rates(data, data.closed, ["mode_key", "order_hour"], "is_late_b")
    lookup = {(r.mode_key, int(r.order_hour)): r.rate for r in cells.itertuples()}
    modes = [m for m in data.mode_order if m in set(cells["mode_key"])]
    values = [[lookup.get((m, hour)) for hour in range(24)] for m in modes]
    return svg.heatmap([data.mode_name.get(m, m) for m in modes], [str(hour) for hour in range(24)], values, w, h, v.title_es)


def _days_by_mode(data: _Data, v: Visual) -> str:
    w, h = _size(v)
    closed = data.closed
    if closed.empty:
        return svg.grouped_bar_chart([], w, h, v.title_es)
    real = closed.groupby("mode_key")["real_days"].mean()
    scheduled = data.t["dim_shipping_mode"].set_index("mode_key")["scheduled_days"]
    groups = [(data.mode_name.get(m, m), {"Reales": float(real.get(m, float("nan"))), "Programados": float(scheduled.get(m, float("nan")))}) for m in data.mode_order if m in real.index]
    return svg.grouped_bar_chart(groups, w, h, v.title_es)


# --- sections ---------------------------------------------------------------------------------------------------------------------------


def _steps(model: SemanticModel) -> str:
    steps = [
        "Instale Power BI Desktop (gratuito, solo para Windows) y abra un informe en blanco.",
        "Cree un parámetro de texto llamado <code>CarpetaSalidas</code> con la ruta de la carpeta <code>outputs\\powerbi</code> de este proyecto.",
        f"Cree una consulta por tabla ({len(model.tables)}) con el código de la sección «Power Query». Cada consulta carga un archivo Parquet; deje el nombre de la consulta igual al de la tabla.",
        f"En la vista de modelo, cree las {len(model.relationships)} relaciones de la tabla «Relaciones»: cardinalidad uno a varios (1:*), dirección del filtro única (de la dimensión al hecho), todas activas. "
        "No cree relaciones bidireccionales ni inactivas; <code>dim_as_of</code>, <code>dim_model</code> y los hechos de la evaluación no tienen relaciones.",
        "Marque <code>dim_date</code> como tabla de fechas con la columna <code>date</code>.",
        "Ordene <code>month_name_es</code> por <code>month</code>, <code>weekday_es</code> por <code>weekday</code>, <code>year_month_label</code> por <code>month_key</code>, "
        "<code>mode_es</code> por <code>sort_order</code> y <code>as_of_label</code> por <code>as_of_key</code>. Oculte las columnas de clave de los hechos.",
        "Cree una tabla vacía llamada <code>_Medidas</code> («Especificar datos» sin filas) y agregue en ella las medidas de la sección «Medidas DAX», con su formato.",
        f"Cree las {len(model.pages)} páginas a escala 16:9 (1280 × 720 px) y ubique cada visual según la grilla de 12 columnas de la sección «Las cinco páginas».",
        "Guarde el informe como <code>.pbix</code>. Cuando corra el flujo, vuelva a cargar con «Actualizar»: las consultas leen los mismos archivos.",
    ]
    return "<ol>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>"


def _diagram(model: SemanticModel) -> str:
    """Related dimensions above, the facts with relationships in the middle, the run table below, and the tables without relationships in a last row."""
    width, height = 1500, 700
    related = {r.dim_table for r in model.relationships} | {r.fact_table for r in model.relationships}
    top = [t.name for t in model.tables if t.kind == "dimension" and t.name in related and t.name != "dim_pipeline_run"]
    bottom = ["dim_pipeline_run"]
    facts = [t.name for t in model.tables if t.kind == "fact" and t.name in related]
    alone = [t.name for t in model.tables if t.name not in related]
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for names, y, w, h in ((top, 24, 150, 46), (facts, 250, 170, 56), (bottom, 420, 170, 46), (alone, 590, 138, 46)):
        gap = (width - w * len(names)) / (len(names) + 1)
        for i, name in enumerate(names):
            boxes[name] = (gap + i * (w + gap), y, w, h)
    incoming: dict[tuple[str, str], int] = {}
    totals_out: dict[str, int] = {}
    for r in model.relationships:
        side = "top" if r.dim_table in top else "bottom"
        incoming[(r.fact_table, side)] = incoming.get((r.fact_table, side), 0) + 1
        totals_out[r.dim_table] = totals_out.get(r.dim_table, 0) + 1
    dims = sum(1 for t in model.tables if t.kind == "dimension")
    nfacts = sum(1 for t in model.tables if t.kind == "fact")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" class="model-svg" viewBox="0 0 {width} {height}" role="img" aria-label="Diagrama del modelo en estrella: {dims} dimensiones y {nfacts} hechos">']
    out.append('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--muted)"/></marker></defs>')
    counters: dict[tuple[str, str], int] = {}
    outgoing: dict[str, int] = {}
    for r in model.relationships:
        side = "top" if r.dim_table in top else "bottom"
        dx, dy, dw, dh = boxes[r.dim_table]
        fx, fy, fw, fh = boxes[r.fact_table]
        k = outgoing[r.dim_table] = outgoing.get(r.dim_table, 0) + 1
        x1 = dx + dw * k / (totals_out[r.dim_table] + 1)
        y1 = dy + dh if side == "top" else dy
        n = incoming[(r.fact_table, side)]
        j = counters[(r.fact_table, side)] = counters.get((r.fact_table, side), 0) + 1
        x2 = fx + fw * j / (n + 1)
        y2 = fy if side == "top" else fy + fh
        out.append(
            f'<line class="rel" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" marker-end="url(#arrow)">'
            f'<title>{escape(r.rel_id)}: {escape(r.dim_table)}[{escape(r.dim_column)}] 1 → * {escape(r.fact_table)}[{escape(r.fact_column)}]</title></line>'
        )
        sign = 1 if side == "top" else -1
        out.append(f'<text class="card" x="{x1 + 3:.1f}" y="{y1 + sign * 13:.1f}">1</text>')
        out.append(f'<text class="card" x="{x2 + 3:.1f}" y="{y2 - sign * 6:.1f}">*</text>')
    for name, (x, y, w, h) in boxes.items():
        table = model.table(name)
        cls = "dim" if table.kind == "dimension" else "fact"
        size = ' font-size="10"' if len(name) > 18 else ""
        out.append(f'<rect class="box {cls}" x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="6"/>')
        out.append(f'<text x="{x + w / 2:.1f}" y="{y + h / 2 - 2:.1f}" text-anchor="middle"{size}>{escape(name)}</text>')
        key = ", ".join(table.key) if table.kind == "dimension" else f"{len(table.columns)} columnas"
        out.append(f'<text class="small" x="{x + w / 2:.1f}" y="{y + h / 2 + 12:.1f}" text-anchor="middle">{escape(key)}</text>')
    out.append(f'<text class="small" x="8" y="{570}">Sin relaciones: alimentan sus propios visuales y medidas.</text>')
    out.append(f'<text class="small" x="8" y="{height - 6}">Cada línea va de la dimensión («1») al hecho («*»); la flecha es la dirección del filtro, única y activa.</text>')
    out.append("</svg>")
    return "".join(out)


def _relationships(model: SemanticModel) -> str:
    rows = [
        [r.rel_id, f"{r.dim_table}[{r.dim_column}]", "1", f"{r.fact_table}[{r.fact_column}]", "*", "Uno a varios (1:*)",
         "Única: de la dimensión al hecho" if r.cross_filter == "single" else r.cross_filter, "Sí" if r.active else "No"]
        for r in model.relationships
    ]
    return _table_html(["ID", "Dimensión", "", "Hecho", "", "Cardinalidad", "Dirección del filtro", "Activa"], rows)


def _queries(model: SemanticModel) -> str:
    parts = []
    for t in model.tables:
        dates = [c.name for c in t.columns if c.dtype == "date"]
        steps = f'    Origen = Parquet.Document(File.Contents(CarpetaSalidas & "\\{t.name}.parquet"))'
        last = "Origen"
        if dates:
            casts = ", ".join(f'{{"{d}", type date}}' for d in dates)
            steps += f",\n    Tipos = Table.TransformColumnTypes(Origen, {{{casts}}})"
            last = "Tipos"
        code = f"let\n{steps}\nin\n    {last}"
        parts.append(f"<h3>{escape(t.name)}</h3><p>{escape(t.description_es)}</p><pre><code>{escape(code)}</code></pre>")
    return "".join(parts)


def _measures(model: SemanticModel) -> str:
    parts = []
    for m in model.measures:
        uses = sorted(dax_measure_refs(m.dax))
        used = f" Usa: {', '.join('[' + u + ']' for u in uses)}." if uses else ""
        parts.append(
            f"<h3>{escape(m.name_es)}</h3><p>{escape(m.description_es)}{escape(used)} Formato: <code>{escape(m.format_string)}</code>.</p>"
            f"<pre><code>{escape(m.name_es)} =\n{escape(m.dax)}</code></pre>"
        )
    return "".join(parts)


def _page_section(data: _Data, page: Page, number: int) -> str:
    visuals = []
    for v in page.visuals:
        style = f"grid-column:{v.x + 1} / span {v.w};grid-row:{v.y + 1} / span {v.h}"
        visuals.append(
            f'<section class="visual" style="{style}" aria-label="{escape(v.title_es)}"><header><b>{escape(v.title_es)}</b>'
            f'<span>{escape(KIND_ES.get(v.kind, v.kind))}</span></header><div class="body">{_preview(data, v)}</div></section>'
        )
    rows = [[KIND_ES.get(v.kind, v.kind), v.title_es, ", ".join(v.fields) or "—", f"x {v.x}, y {v.y}, ancho {v.w}, alto {v.h}"] for v in page.visuals]
    table = _table_html(["Visual", "Título", "Campos", "Posición (celdas)"], rows)
    return (
        f'<h3 id="pagina-{number}">Página {number}: {escape(page.name_es)}</h3>'
        f'<div class="canvas-wrap"><div class="canvas" role="group" aria-label="Página {escape(page.name_es)}">{"".join(visuals)}</div></div>'
        f'<details><summary>Posición, tipo y campos de cada visual</summary><div class="scroll placement">{table}</div></details>'
    )


def _page_html(model: SemanticModel, data: _Data, meta: ExportMeta) -> str:
    nav = "".join(f'<li><a href="#{key}">{escape(title)}</a></li>' for key, title in SECTIONS)
    pages = "".join(_page_section(data, p, i) for i, p in enumerate(model.pages, start=1))
    dims = sum(1 for t in model.tables if t.kind == "dimension")
    facts = sum(1 for t in model.tables if t.kind == "fact")
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(TITLE)}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>{escape(TITLE)}</h1>
<p class="lead">Guía para armar el informe en Power BI Desktop a partir de los archivos Parquet de <code>outputs/powerbi</code>: modelo, relaciones, consultas, medidas y las cinco páginas con sus visuales ubicados. Las vistas previas usan los números reales de esos archivos; las celdas con menos de {data.min_cell} pedidos se ocultan.</p>
<div class="meta"><span>Fecha de cálculo <b>{escape(meta.as_of)}</b></span><span>Corrida <code>{escape(meta.run_key)}</code></span><span>SHA-256 de la fuente <code>{escape(meta.source_sha256[:12])}</code></span></div>
<nav aria-label="Secciones"><ul>{nav}</ul></nav>
<h2 id="armado">{escape(SECTIONS[0][1])}</h2>
{_steps(model)}
<div class="warn">La conexión <code>Parquet.Document</code> es la que usa Power BI Desktop para leer archivos Parquet; si su versión no la ofrece, use «Obtener datos» con el conector Parquet y apunte a los mismos archivos.</div>
<div class="warn"><b>Qué se probó y qué no.</b> Las pruebas del proyecto comprueban que los archivos cumplen el modelo (claves únicas, claves foráneas en su dimensión, relaciones de uno a varios con filtro único) y que cada columna y cada medida que nombra el DAX existe. <b>Ninguna medida se ejecutó</b>: no hay un motor de DAX en el equipo que generó este documento. La primera vez que arme el informe, compare unas pocas cifras con las vistas previas de abajo, que salen de los mismos archivos. Pendiente de verificar: que <code>Alertas abiertas a la fecha</code> respete los segmentadores de modo y mercado con el <code>FILTER</code> sobre <code>fact_order</code>; si no los respeta, la alternativa es <code>KEEPFILTERS</code>.</div>
<div class="warn"><b>Los costos son supuestos.</b> El umbral de alerta se eligió con costos declarados (una alerta, un retraso y cuánto de él evita la alerta); los datos no traen costos. La tarjeta «Costos supuestos» de la página 1 los muestra junto al umbral.</div>
<h2 id="modelo">{escape(SECTIONS[1][1])}</h2>
<p>{dims} dimensiones y {facts} hechos. <code>dim_as_of</code> es una tabla de parámetro: elige la fecha de cálculo de las alertas abiertas. <code>dim_model</code> y los hechos de la evaluación (<code>fact_method_metrics</code>, <code>fact_evaluation_summary</code>, <code>fact_roc</code>, <code>fact_calibration</code>, <code>fact_cost_sensitivity</code>, <code>fact_leakage</code>, <code>fact_importance</code>) no tienen relaciones: cada uno alimenta sus propios visuales. La clave del pedido es el número de pedido de la fuente, que no identifica a ninguna persona; no hay ninguna columna de cliente en el modelo.</p>
{_diagram(model)}
<h2 id="relaciones">{escape(SECTIONS[2][1])}</h2>
<div class="scroll">{_relationships(model)}</div>
<h2 id="consultas">{escape(SECTIONS[3][1])}</h2>
{_queries(model)}
<h2 id="medidas">{escape(SECTIONS[4][1])}</h2>
<p>Todas van en la tabla <code>_Medidas</code>. Los nombres de las medidas y los textos están en español; las tablas y las columnas, en inglés.</p>
{_measures(model)}
<h2 id="paginas">{escape(SECTIONS[5][1])}</h2>
<p>Cada página es un lienzo de 1280 × 720 px (16:9) sobre una grilla de {PAGE_COLUMNS} columnas por {PAGE_ROWS} filas. La posición de cada visual está en celdas: <code>x</code> e <code>y</code> desde la esquina superior izquierda. Las vistas previas de la página 4 muestran la prueba si ya se evaluó y, si no, la validación (V2), rotulada.</p>
{pages}
<h2 id="fuente">{escape(SECTIONS[6][1])}</h2>
<p>{escape(meta.attribution)}</p>
<p>Datos de la corrida <code>{escape(meta.run_key)}</code>, con fecha de cálculo {escape(meta.as_of)}. Este documento se genera con <code>python -m latedel run</code> a partir de <code>dashboard/semantic_model.py</code>; no se edita a mano.</p>
</main>
</body>
</html>
"""


def render_design(model: SemanticModel, outputs_dir: Path, html_path: Path, meta: ExportMeta, *, min_cell_orders: int = MIN_CELL_ORDERS) -> Path:
    """Write the HTML design of the report to `html_path` (atomically) and return the path.

    `min_cell_orders` is the size under which an aggregate cell is hidden (``[site] min_cell_orders``).
    """
    problems = validate_model(model)
    if problems:
        raise ValueError(f"El modelo semántico es incoherente: {problems[0]}")
    if not isinstance(min_cell_orders, int) or isinstance(min_cell_orders, bool) or min_cell_orders < 1:
        raise ValueError(f"min_cell_orders debe ser un entero positivo; se recibió {min_cell_orders!r}")
    data = _Data(outputs_dir, model, min_cell_orders)
    html = _page_html(model, data, meta)
    path = write_atomic(html_path, html)
    logger.info("dashboard design written to %s (%d bytes)", path, len(html.encode("utf-8")))
    return path
