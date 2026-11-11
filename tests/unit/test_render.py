"""The HTML design of the report: every page, visual, measure and relationship, nothing external, real numbers, hidden small cells, escaped text (U19, AC25)."""

from __future__ import annotations

import dataclasses
import html as html_lib
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import pytest

from latedel.dashboard.render import render_design
from latedel.dashboard.semantic_model import MODEL, Page, Visual
from latedel.export import ExportMeta
from latedel.schema import ATTRIBUTION

META = ExportMeta(run_key="run-C2018-02", as_of="2018-02-07", source_sha256="ab" * 32)
EVIL_PRODUCT = "<script>alert(1)</script>"
EVIL_REGION = "<img src=x onerror=alert(1)>"
EVIL_MARKET = '"><b>market</b>'


@pytest.fixture(scope="module")
def outputs(star_export) -> Path:
    return star_export.out_dir


@pytest.fixture(scope="module")
def design(tmp_path_factory, outputs) -> tuple[Path, str]:
    path = render_design(MODEL, outputs, tmp_path_factory.mktemp("design") / "dashboard" / "powerbi-design.html", META)
    return path, path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frames(outputs) -> dict[str, pd.DataFrame]:
    return {t.name: pd.read_parquet(outputs / f"{t.name}.parquet") for t in MODEL.tables}


@pytest.fixture(scope="module")
def evil(tmp_path_factory, outputs) -> tuple[Path, str]:
    """The same outputs with text that would run if it were put in a page as it is: in a product, a region and a market."""
    folder = tmp_path_factory.mktemp("evil_outputs")
    for path in outputs.glob("*.parquet"):
        shutil.copy(path, folder / path.name)
    product = pd.read_parquet(folder / "dim_product.parquet")
    product["product_name"] = EVIL_PRODUCT
    product.to_parquet(folder / "dim_product.parquet", index=False)
    geo = pd.read_parquet(folder / "dim_geo.parquet")
    geo["order_region"], geo["market"] = EVIL_REGION, EVIL_MARKET
    geo.to_parquet(folder / "dim_geo.parquet", index=False)
    path = render_design(MODEL, folder, tmp_path_factory.mktemp("evil_design") / "evil.html", META, min_cell_orders=1)
    return path, path.read_text(encoding="utf-8")


def text_of(html: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", html))


class Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def parsed(html: str) -> Collector:
    collector = Collector()
    collector.feed(html)
    return collector


def page_block(html: str, number: int) -> str:
    start = html.index(f'id="pagina-{number}"')
    end = html.index(f'id="pagina-{number + 1}"') if number < len(MODEL.pages) else html.index('id="fuente"')
    return html[start:end]


def cards_of(block: str) -> dict[str, str]:
    return dict(re.findall(r'<b>([^<]+)</b><span>Tarjeta</span></header><div class="body"><div class="big">([^<]*)</div>', block))


def thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


# --- the contract of the spec -----------------------------------------------------------------------------------------------------------


def test_html_has_every_page_and_escapes_data_text(design, evil) -> None:
    """AC25: all the pages, visuals, measures and relationships of the specification are in the file, under 2 MB, and the text of the data is text."""
    path, html = design
    assert path.stat().st_size < 2 * 1024 * 1024
    assert len(re.findall(r'<div class="canvas"', html)) == len(MODEL.pages) == 5
    for number, page in enumerate(MODEL.pages, start=1):
        block = page_block(html, number)
        assert f"Página {number}: {page.name_es}" in block and block.count('<section class="visual"') == len(page.visuals)
        for v in page.visuals:
            assert f"<b>{html_lib.escape(v.title_es)}</b>" in block, (page.name_es, v.title_es)
    measures = html[html.index('id="medidas"'):html.index('id="paginas"')]
    for m in MODEL.measures:
        assert f"<h3>{html_lib.escape(m.name_es)}</h3>" in measures and html_lib.escape(m.dax) in measures, m.name_es
    relationships = html[html.index('id="relaciones"'):html.index('id="consultas"')]
    for r in MODEL.relationships:
        assert r.rel_id in relationships and f"{r.dim_table}[{r.dim_column}]" in relationships and f"{r.fact_table}[{r.fact_column}]" in relationships
    # the data text: put as a product, a region and a market, it appears as text and never as markup
    _, hostile = evil
    assert html_lib.escape(EVIL_PRODUCT) in hostile and html_lib.escape(EVIL_REGION) in hostile and html_lib.escape(EVIL_MARKET) in hostile, "the hostile text is shown"
    tags = parsed(hostile).tags
    assert not [t for t, _ in tags if t in ("script", "img")], "no tag came from the data"
    assert not [k for _, a in tags for k in a if k.startswith("on")], "no handler came from the data"
    assert "<script" not in hostile and "<img" not in hostile and "<b>market" not in hostile and 'market</b>' not in hostile.replace(html_lib.escape(EVIL_MARKET), "")


# --- the file ------------------------------------------------------------------------------------------------------------------------


def test_the_file_is_one_html_document_in_spanish(design) -> None:
    path, html = design
    assert path.name == "powerbi-design.html"
    assert html.startswith("<!doctype html>") and '<html lang="es">' in html and "<title>Diseño del tablero de Power BI" in html
    assert 'name="viewport"' in html and html.count("<h1>") == 1
    assert [p.name for p in path.parent.iterdir()] == ["powerbi-design.html"]  # written atomically: no temporary file


def test_nothing_is_loaded_from_outside_and_there_are_no_scripts(design) -> None:
    _, html = design
    tags = parsed(html).tags
    assert not [t for t, _ in tags if t in ("script", "link", "iframe", "img", "object", "embed", "video", "audio", "source")]
    for tag, attrs in tags:
        for name in ("src", "href", "action", "data", "poster", "srcset"):
            value = attrs.get(name)
            assert value is None or value.startswith("#"), (tag, name, value)  # only links inside the page
    assert "@import" not in html and not re.search(r"url\((?!#)", html)
    outside = html.replace("http://www.w3.org/2000/svg", "").replace(html_lib.escape(ATTRIBUTION), "")
    assert "http://" not in outside and "https://" not in outside, "the only address is the dataset's DOI, inside the attribution, as text"
    assert "http://doi.org/10.17632/8gx2fvg2k6.5" in html
    assert not [k for _, a in tags for k in a if k.startswith("on")]  # no inline handlers


def test_the_attribution_the_run_and_the_calculation_date_are_stated(design) -> None:
    _, html = design
    text = re.sub(r"\s+", " ", text_of(html))
    assert ATTRIBUTION in text and "CC BY 4.0" in text and "Constante" in text
    assert "Corrida run-C2018-02" in text and "ab" * 6 in text and "Fecha de cálculo 2018-02-07" in text


def test_rendering_twice_gives_the_same_bytes(design, outputs, tmp_path) -> None:
    again = render_design(MODEL, outputs, tmp_path / "again.html", META)
    assert again.read_bytes() == design[0].read_bytes()


def test_it_says_that_no_measure_was_executed_what_is_left_to_verify_and_that_the_costs_are_assumed(design) -> None:
    _, html = design
    text = re.sub(r"\s+", " ", text_of(html))
    assert "Ninguna medida se ejecutó" in text and "KEEPFILTERS" in text and "Alertas abiertas a la fecha" in text
    assert "Los costos son supuestos" in text and "los datos no traen costos" in text


# --- the content of the specification ---------------------------------------------------------------------------------------------------


def test_it_has_the_steps_and_every_section(design) -> None:
    _, html = design
    for key in ("armado", "modelo", "relaciones", "consultas", "medidas", "paginas", "fuente"):
        assert f'id="{key}"' in html and f'href="#{key}"' in html
    text = text_of(html)
    for fragment in ("CarpetaSalidas", "tabla de fechas", "dim_date", "_Medidas", "uno a varios", "1280", "16:9", "dim_as_of"):
        assert fragment in text
    steps = html[html.index('id="armado"'):html.index("</ol>")]
    assert steps.count("<li>") == 9 and "las 9 relaciones" in steps and "las 5 páginas" in steps and "(20)" in steps


def test_every_table_has_its_power_query(design) -> None:
    _, html = design
    for table in MODEL.tables:
        assert f"<h3>{table.name}</h3>" in html
        code = html_lib.escape(f'Parquet.Document(File.Contents(CarpetaSalidas & "{chr(92)}{table.name}.parquet"))')
        assert code in html, table.name
    flat = html.replace("&quot;", '"')
    assert 'Table.TransformColumnTypes(Origen, {{"date", type date}})' in flat
    assert 'Table.TransformColumnTypes(Origen, {{"trained_as_of", type date}})' in flat
    assert 'Table.TransformColumnTypes(Origen, {{"outcome_known_date", type date}, {"scored_as_of_date", type date}})' in flat or "scored_as_of_date" in flat


def test_every_relationship_is_listed_with_its_cardinality_and_direction(design) -> None:
    _, html = design
    section = html[html.index('id="relaciones"'):html.index('id="consultas"')]
    for r in MODEL.relationships:
        row = re.search(rf"<tr><td>{r.rel_id}</td>(.*?)</tr>", section)
        assert row, r.rel_id
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(1))
        assert cells[0] == f"{r.dim_table}[{r.dim_column}]" and cells[2] == f"{r.fact_table}[{r.fact_column}]"
        assert "Uno a varios (1:*)" in cells and "Única: de la dimensión al hecho" in cells and cells[-1] == "Sí"
    assert section.count("<tr>") == len(MODEL.relationships) + 1  # plus the header


def test_the_diagram_draws_every_table_and_every_relationship_with_its_ends(design) -> None:
    _, html = design
    diagram = html[html.index('class="model-svg"'):html.index("</svg>", html.index('class="model-svg"'))]
    for table in MODEL.tables:
        assert f">{table.name}</text>" in diagram
    assert diagram.count('class="rel"') == len(MODEL.relationships) == 9
    assert diagram.count('marker-end="url(#arrow)"') == 9
    assert len(re.findall(r'class="card"[^>]*>1<', diagram)) == 9 and len(re.findall(r'class="card"[^>]*>\*<', diagram)) == 9
    for r in MODEL.relationships:
        assert f"{r.rel_id}: {r.dim_table}[{r.dim_column}] 1 → * {r.fact_table}[{r.fact_column}]" in diagram
    assert "10 dimensiones y 10 hechos" in diagram


def test_every_measure_appears_with_its_dax_format_and_description(design) -> None:
    _, html = design
    section = html[html.index('id="medidas"'):html.index('id="paginas"')]
    plain = html_lib.unescape(section)
    for m in MODEL.measures:
        assert f"{m.name_es} =\n{m.dax}" in plain
        assert m.description_es in plain and f"<code>{html_lib.escape(m.format_string)}</code>" in section
    assert len(re.findall(r"<h3>", section)) == len(MODEL.measures) == 31


def test_the_five_pages_have_their_visuals_placed_on_the_grid(design) -> None:
    _, html = design
    for i, page in enumerate(MODEL.pages, start=1):
        block = page_block(html, i)
        for v in page.visuals:
            assert f'style="grid-column:{v.x + 1} / span {v.w};grid-row:{v.y + 1} / span {v.h}"' in block, v.title_es
            assert f'aria-label="{html_lib.escape(v.title_es)}"' in block
            fields = ", ".join(v.fields) or "—"
            assert html_lib.escape(fields) in block  # the placement table names the fields
            assert f"x {v.x}, y {v.y}, ancho {v.w}, alto {v.h}" in block
    css = html[:html.index("</style>")]
    assert "repeat(12,1fr)" in css and "repeat(9,1fr)" in css and "aspect-ratio:16/9" in css


# --- the previews are drawn from the real tables ----------------------------------------------------------------------------------------


def test_the_cards_of_the_alerts_page_are_the_figures_of_the_exported_orders(design, frames) -> None:
    _, html = design
    cards = cards_of(page_block(html, 1))
    order = frames["fact_order"]
    scored = order[order["risk"].notna()]
    alerts = int(scored["is_alert"].eq(True).sum())
    assert len(scored) > 400 and 0 < alerts < len(scored)
    assert cards["Pedidos puntuados"] == thousands(len(scored))
    assert cards["Alertas"] == thousands(alerts)
    assert cards["Alertado (%)"] == f"{alerts / len(scored) * 100:.1f} %".replace(".", ",")
    assert cards["Umbral"] == f"{float(frames['dim_model']['threshold'].iloc[0]):.2f}".rstrip("0").rstrip(".").replace(".", ",")
    assert cards["Costos supuestos"].startswith("Costos supuestos, no medidos: alerta 5 u., retraso 20 u., efecto 0,5")
    # the open alerts: issued up to the last day that scored orders, outcome not known that day
    last = pd.to_datetime(scored["scored_as_of_date"]).max()
    open_ = order[order["is_alert"].eq(True) & (pd.to_datetime(order["scored_as_of_date"]) <= last)
                  & (pd.to_datetime(order["outcome_known_date"]).isna() | (pd.to_datetime(order["outcome_known_date"]) > last))]
    assert len(open_) > 0, "the fixture has alerts that are still open on the last scoring day, so the table is not drawn empty"
    assert cards["Alertas abiertas a la fecha"] == thousands(len(open_))


def test_the_table_of_open_alerts_lists_the_highest_risks_first_and_only_open_alerts(design, frames) -> None:
    _, html = design
    block = page_block(html, 1)
    table = block[block.index("<table"):block.index("</table>")]
    rows = re.findall(r"<tr><td>(\d+)</td>.*?<td class=num>([\d,]+)</td></tr>", table)
    assert 3 <= len(rows) <= 9
    risks = [float(r.replace(",", ".")) for _, r in rows]
    assert risks == sorted(risks, reverse=True)
    order = frames["fact_order"].set_index("order_id")
    threshold = float(frames["dim_model"]["threshold"].iloc[0])
    for order_id, _ in rows:
        row = order.loc[int(order_id)]
        assert bool(row["is_alert"]) and float(row["risk"]) >= threshold - 1e-9


def test_the_cards_of_the_follow_up_and_the_quality_pages_come_from_their_tables(design, frames) -> None:
    _, html = design
    track = frames["fact_tracking_monthly"]
    follow = cards_of(page_block(html, 3))
    precision = track["alerts_late"].sum() / track["closed_alerts"].sum()
    recall = track["alerts_late"].sum() / (track["alerts_late"].sum() + track["late_missed"].sum())
    assert follow["Precisión de las alertas"] == f"{precision * 100:.1f} %".replace(".", ",")
    assert follow["Recall de las alertas"] == f"{recall * 100:.1f} %".replace(".", ",")
    quality = cards_of(page_block(html, 5))
    order = frames["fact_order"]
    assert quality["Pedidos"] == thousands(len(order))
    assert quality["Pedidos cancelados"] == thousands(int((order["outcome_status"] == "cancelled").sum()))
    runs = frames["dim_pipeline_run"]
    assert quality["Última fecha de cálculo"] == str(runs[runs["status"] == "ok"]["as_of_date"].max())
    assert quality["Reglas bloqueantes fallidas"] == str(int(((frames["fact_validation"]["severity"] == "bloqueante") & (frames["fact_validation"]["status"] == "falla")).sum()))


def test_the_model_page_says_whether_it_draws_the_test_or_the_validation(design, frames) -> None:
    _, html = design
    block = page_block(html, 4)
    assert "(prueba)" in block, "the fixture evaluated the test at the close, so the curves are the test's"
    assert "C01" in block and "C10" in block and "supera" in block, "the criteria and their verdicts, with the word «supera» or «no supera»"
    assert all(name in block for name in ("Modelo", "B2 por modo", "B5 por modo y hora"))
    assert len(frames["fact_roc"]) > 0 and len(frames["fact_calibration"]) > 0


def test_a_cell_with_fewer_orders_than_the_minimum_shows_a_dash_and_no_number(outputs, tmp_path, frames) -> None:
    visible = render_design(MODEL, outputs, tmp_path / "visible.html", META, min_cell_orders=1).read_text(encoding="utf-8")
    hidden = render_design(MODEL, outputs, tmp_path / "hidden.html", META, min_cell_orders=10**9).read_text(encoding="utf-8")
    for markup in (visible, hidden):
        block = page_block(markup, 2)
        matrix = block[block.index("<table"):block.index("</table>")]
        rates = re.findall(r"<td class=num>([^<]*)</td>", matrix)
        assert rates
        if markup is hidden:
            assert set(rates) == {"—"}, "every cell is under the minimum: no rate is drawn"
        else:
            assert any(r.endswith("%") for r in rates)
    assert "fill-opacity" in page_block(visible, 2) and "fill-opacity" not in page_block(hidden, 2), "the heat map draws no cell under the minimum"
    assert not re.search(r"\d %</td>", page_block(hidden, 2)) and not re.search(r"\d %</td>", page_block(hidden, 1)), "no rate in a table of the hidden design"
    closed = frames["fact_order"][frames["fact_order"]["outcome_status"] == "closed"]
    assert len(closed) > 0
    # the figures of the visible matrix are the rates of the exported orders
    geo = frames["dim_geo"].set_index("geo_key")["market"]
    cells = closed.assign(market=closed["geo_key"].map(geo)).groupby(["mode_key", "market"]).agg(n=("order_id", "size"), late=("is_late", lambda s: int(s.eq(True).sum())))
    mode, market = cells.index[0]
    expected = f"{cells.iloc[0]['late'] / cells.iloc[0]['n'] * 100:.1f} %".replace(".", ",")
    assert expected in page_block(visible, 2)


def test_the_minimum_cell_size_is_the_one_asked_for_and_a_bad_one_is_refused(outputs, tmp_path) -> None:
    for bad in (0, -5, 2.5, True, "20"):
        with pytest.raises(ValueError, match="min_cell_orders"):
            render_design(MODEL, outputs, tmp_path / "bad.html", META, min_cell_orders=bad)
    assert not (tmp_path / "bad.html").exists()
    default = render_design(MODEL, outputs, tmp_path / "default.html", META).read_text(encoding="utf-8")
    assert "las celdas con menos de 20 pedidos se ocultan" in default
    assert "las celdas con menos de 50 pedidos se ocultan" in render_design(MODEL, outputs, tmp_path / "fifty.html", META, min_cell_orders=50).read_text(encoding="utf-8")


def test_outputs_that_are_not_there_give_empty_previews_and_the_whole_document(tmp_path) -> None:
    path = render_design(MODEL, tmp_path / "nothing", tmp_path / "empty.html", META)
    html = path.read_text(encoding="utf-8")
    assert len(re.findall(r'<div class="canvas"', html)) == 5 and "Sin datos" in html
    assert html.count('<section class="visual"') == sum(len(p.visuals) for p in MODEL.pages)


def test_an_incoherent_model_is_refused_before_anything_is_written(outputs, tmp_path) -> None:
    broken = dataclasses.replace(MODEL, pages=(*MODEL.pages, Page("Rota", (Visual("card", "Rota", ("[No existe]",), 0, 0, 2, 1),))))
    with pytest.raises(ValueError, match="incoherente"):
        render_design(broken, outputs, tmp_path / "x" / "broken.html", META)
    assert not (tmp_path / "x").exists()
