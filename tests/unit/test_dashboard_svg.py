"""The small SVG charts of the dashboard design (U19): numbers in Spanish, escaped text, an empty chart that says so, a line that breaks at a missing value, and a heat
map whose hidden cells carry no number (D07)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from latedel.dashboard import svg


def parsed(markup: str) -> ET.Element:
    return ET.fromstring(markup)          # a chart must be well-formed XML, or an HTML parser would not read it as a drawing either


@pytest.mark.parametrize(("value", "digits", "expected"), [
    (0, 1, "0"), (12, 1, "12"), (12.5, 1, "12,5"), (0.125, 2, "0,12"), (123.4, 1, "123"), (1234, 1, "1.234"), (9999, 1, "9.999"), (12_000, 1, "12 mil"), (4_800_000, 1, "4,8 M"),
    (2_000_000_000, 1, "2 mil M"), (-12.5, 1, "-12,5"), (float("nan"), 1, "—"), (float("inf"), 1, "—"), (None, 1, "—"),
])
def test_numbers_are_written_in_spanish(value, digits, expected):
    assert svg.format_number(value, digits) == expected


def test_ticks_are_round_cover_the_range_and_survive_a_flat_or_broken_range():
    ticks = svg.nice_ticks(0, 87)
    assert ticks[0] <= 0 and ticks[-1] >= 87 and len(ticks) >= 2 and ticks == sorted(ticks)
    assert svg.nice_ticks(5, 5)[0] <= 5 <= svg.nice_ticks(5, 5)[-1]
    assert svg.nice_ticks(float("nan"), 3) == [0.0, 1.0]


@pytest.mark.parametrize("chart", [
    lambda: svg.line_chart({}, 300, 120, "Vacío"), lambda: svg.bar_chart([], 300, 120, "Vacío"), lambda: svg.grouped_bar_chart([], 300, 120, "Vacío"),
    lambda: svg.heatmap([], [], [], 300, 120, "Vacío"), lambda: svg.heatmap(["a"], ["b"], [[None]], 300, 120, "Vacío"),
    lambda: svg.bar_chart([("a", float("nan"))], 300, 120, "Vacío"),
])
def test_an_empty_chart_says_there_is_no_data_instead_of_drawing_nothing(chart):
    root = parsed(chart())
    assert "sin datos" in root.get("aria-label") and "Sin datos" in "".join(root.itertext())


def test_text_from_the_data_is_escaped_in_every_chart():
    evil = "<script>alert(1)</script>"
    charts = [
        svg.bar_chart([(evil, 3.0)], 300, 120, evil), svg.line_chart({evil: [(0, 1), (1, 2)]}, 300, 120, evil, x_ticks=[(0, evil)]),
        svg.grouped_bar_chart([(evil, {evil: 1.0})], 300, 120, evil), svg.heatmap([evil], [evil], [[1.0]], 300, 120, evil),
    ]
    for markup in charts:
        assert "<script" not in markup and "&lt;script&gt;" in markup
        parsed(markup)


def test_a_bar_chart_has_one_bar_and_one_value_per_row_and_scales_to_the_largest():
    markup = svg.bar_chart([("Standard Class", 40.0), ("First Class", 100.0)], 400, 100, "Tasas", unit="%")
    root = parsed(markup)
    assert markup.count("<path") == 2 and root.get("role") == "img" and root.get("aria-label") == "Tasas"
    assert "First Class: 100 %" in markup and "Standard Class: 40 %" in markup
    lengths = [float(m) for m in re.findall(r'd="M[\d.]+,[\d.]+ h([\d.]+)', markup)]
    assert lengths[0] < lengths[1] and lengths[0] == pytest.approx(lengths[1] * 0.4, rel=0.1)


def test_a_line_breaks_at_a_missing_value_instead_of_drawing_a_false_one():
    markup = svg.line_chart({"A": [(0, 1.0), (1, 2.0), (2, float("nan")), (3, 3.0), (4, 4.0)]}, 400, 120, "Rota")
    assert markup.count("<polyline") == 2, "two segments, none across the gap"
    assert "nan" not in markup.lower().replace("polyline", "")
    single = svg.line_chart({"A": [(0, 1.0), (1, float("nan")), (2, 3.0)]}, 400, 120, "Sola")
    assert single.count("<circle") >= 2 and "<polyline" not in single, "a lone point is a dot"


def test_two_series_get_a_legend_and_a_dashed_one_is_dashed():
    markup = svg.line_chart({"Modelo": [(0, 0), (1, 1)], "Diagonal": [(0, 0), (1, 1)]}, 400, 120, "ROC", dashed=("Diagonal",))
    assert "Modelo</text>" in markup and "Diagonal</text>" in markup and markup.count("stroke-dasharray") >= 2
    assert "<polyline" in markup and markup.count('stroke-dasharray="5 4"') == 1


def test_grouped_columns_draw_a_bar_for_each_value_and_skip_a_missing_one():
    markup = svg.grouped_bar_chart([("Same Day", {"Reales": 1.0, "Programados": 0.0}), ("First Class", {"Reales": 2.0, "Programados": float("nan")})], 400, 140, "Días")
    assert markup.count("<rect") == 3 and "Reales</text>" in markup and "Programados</text>" in markup
    parsed(markup)


def test_a_heat_map_hides_a_cell_without_its_number_and_colours_the_others_by_value():
    markup = svg.heatmap(["Same Day", "First Class"], ["0", "1"], [[0.2, None], [0.8, 0.4]], 300, 100, "Mapa", hidden_note="oculto: pocos pedidos")
    root = parsed(markup)
    assert markup.count("<rect") == 4 and markup.count('fill="url(#hidden-cell)"') == 1
    assert "Same Day, 1: oculto: pocos pedidos" in markup
    opacities = [float(x) for x in re.findall(r'fill-opacity="([\d.]+)"', markup)]
    assert opacities == pytest.approx([0.29, 0.93, 0.505], abs=0.01), "the colour follows the value: 0.2, 0.8 and 0.4 of a largest value of 0.8"
    assert "Same Day, 0: 0,2" in markup and "First Class, 0: 0,8" in markup
    assert root.get("aria-label") == "Mapa"


def test_a_heat_map_never_writes_the_value_of_a_hidden_cell():
    markup = svg.heatmap(["a"], ["x", "y"], [[None, 0.5]], 300, 80, "Mapa")
    assert markup.count("0,5") == 1, "only the visible cell has a number in its tooltip"
