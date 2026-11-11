"""The measures and the pages of the semantic model: every DAX reference exists and the pages are laid out on the grid (U18, AC24, docs/plan.md section 17).

No measure is executed here (there is no DAX engine); what is checked is what can be checked: that every ``table[column]`` and every ``[Measure]`` a measure or a visual
names exists, that the text a measure compares a column with is a value the exported column takes, and that the visuals of each page fit the 12 x 9 grid without overlapping.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pandas as pd
import pyarrow.parquet as pq
import pytest

from latedel.dashboard.semantic_model import (
    MODEL,
    PAGE_COLUMNS,
    PAGE_ROWS,
    TABLES,
    Measure,
    Page,
    SemanticModel,
    Visual,
    arrow_schema,
    dax_column_refs,
    dax_measure_refs,
    validate_model,
)

PLAN_MEASURES = (
    "Fecha de cálculo elegida", "Pedidos", "Pedidos puntuados", "Alertas", "Alertado (%)", "Riesgo medio", "Retrasos esperados", "Alertas abiertas a la fecha",
    "Pedidos cerrados", "Pedidos tarde", "Tasa de retraso observada", "Alertas cerradas", "Alertas que llegaron tarde", "Tarde sin alerta", "Precisión de las alertas",
    "Recall de las alertas", "Umbral", "Beneficio neto estimado (u.)", "Precisión mensual", "Recall mensual", "Retrasos esperados en el mes", "Retrasos observados en el mes",
    "Veredicto", "Última fecha de cálculo", "Reglas bloqueantes fallidas",
)
PAGE_NAMES = ("Alertas", "Dónde se traban", "Seguimiento", "Validación del modelo", "Calidad y corridas")


def page(name: str) -> Page:
    return next(p for p in MODEL.pages if p.name_es == name)


def visual(page_name: str, title: str) -> Visual:
    return next(v for v in page(page_name).visuals if v.title_es == title)


@pytest.fixture(scope="module")
def frames(star_export) -> dict[str, pd.DataFrame]:
    """The exported tables of the fixture, to compare the text that the measures use with the values the columns take."""
    return {t.name: pq.read_table(star_export.out_dir / f"{t.name}.parquet").to_pandas() for t in MODEL.tables}


# --- the DAX ---------------------------------------------------------------------------------------------------------------------------


def test_every_dax_reference_exists():
    """AC24: every ``table[column]`` of every measure is a column of the model and every ``[Measure]`` is a measure of it."""
    columns = {(t.name, c) for t in MODEL.tables for c in t.column_names}
    measures = {m.name_es for m in MODEL.measures}
    assert len(MODEL.measures) >= len(PLAN_MEASURES)
    for m in MODEL.measures:
        assert dax_column_refs(m.dax) <= columns, (m.name_es, dax_column_refs(m.dax) - columns)
        assert dax_measure_refs(m.dax) - {m.name_es} <= measures, (m.name_es, dax_measure_refs(m.dax) - measures)
    assert validate_model(MODEL) == []


def test_the_dax_references_are_read_without_mistaking_measures_for_columns():
    dax = 'DIVIDE ( [Alertas], CALCULATE ( [Pedidos (u.)], dim_geo[market] = "x" ) ) + SUM ( fact_order[risk] )'
    assert dax_column_refs(dax) == {("dim_geo", "market"), ("fact_order", "risk")}
    assert dax_measure_refs(dax) == {"Alertas", "Pedidos (u.)"}
    assert dax_column_refs("1 + 2") == set() and dax_measure_refs("1 + 2") == set()


def test_the_measures_of_the_plan_exist_with_the_dax_of_the_plan():
    names = [m.name_es for m in MODEL.measures]
    assert set(PLAN_MEASURES) <= set(names) and len(set(names)) == len(names)
    assert MODEL.measure("Fecha de cálculo elegida").dax == (
        "IF ( ISFILTERED ( dim_as_of[as_of_key] ),\n"
        "    SELECTEDVALUE ( dim_as_of[as_of_date] ),\n"
        '    CALCULATE ( MAX ( dim_pipeline_run[as_of_date] ), dim_pipeline_run[status] = "ok" ) )'
    )
    assert MODEL.measure("Pedidos").dax == "COUNTROWS ( fact_order )"
    assert MODEL.measure("Pedidos puntuados").dax == "CALCULATE ( COUNTROWS ( fact_order ), NOT ISBLANK ( fact_order[risk] ) )"
    assert MODEL.measure("Alertas").dax == "CALCULATE ( COUNTROWS ( fact_order ), fact_order[is_alert] = TRUE () )"
    assert MODEL.measure("Alertado (%)").dax == "DIVIDE ( [Alertas], [Pedidos puntuados] )"
    assert MODEL.measure("Retrasos esperados").dax == "SUM ( fact_order[risk] )"
    assert MODEL.measure("Alertas abiertas a la fecha").dax == (
        "VAR d = [Fecha de cálculo elegida]\n"
        "RETURN CALCULATE ( [Alertas], fact_order[scored_as_of_date] <= d,\n"
        "    FILTER ( fact_order, ISBLANK ( fact_order[outcome_known_date] ) || fact_order[outcome_known_date] > d ) )"
    )
    assert MODEL.measure("Tasa de retraso observada").dax == "DIVIDE ( [Pedidos tarde], [Pedidos cerrados] )"
    assert MODEL.measure("Recall de las alertas").dax == "DIVIDE ( [Alertas que llegaron tarde], [Alertas que llegaron tarde] + [Tarde sin alerta] )"
    assert MODEL.measure("Beneficio neto estimado (u.)").dax == (
        "[Alertas que llegaron tarde] * SELECTEDVALUE ( dim_model[late_cost] ) * SELECTEDVALUE ( dim_model[effect] )\n"
        "    - [Alertas cerradas] * SELECTEDVALUE ( dim_model[alert_cost] )"
    )
    assert MODEL.measure("Precisión mensual").dax == "DIVIDE ( SUM ( fact_tracking_monthly[alerts_late] ), SUM ( fact_tracking_monthly[closed_alerts] ) )"
    assert MODEL.measure("Recall mensual").dax == (
        "DIVIDE ( SUM ( fact_tracking_monthly[alerts_late] ),\n    SUM ( fact_tracking_monthly[alerts_late] ) + SUM ( fact_tracking_monthly[late_missed] ) )"
    )
    assert MODEL.measure("Reglas bloqueantes fallidas").dax == (
        'CALCULATE ( COUNTROWS ( fact_validation ), fact_validation[severity] = "bloqueante", fact_validation[status] = "falla" )'
    )
    assert MODEL.measure("Veredicto").dax == "SELECTEDVALUE ( fact_evaluation_summary[verdict_es] )"


def test_every_measure_has_a_format_a_description_and_balanced_brackets():
    for m in MODEL.measures:
        assert m.format_string and m.description_es.endswith("."), m.name_es
        for open_, close in ("()", "[]"):
            assert m.dax.count(open_) == m.dax.count(close), (m.name_es, open_)
        assert m.dax.count('"') % 2 == 0, m.name_es


def test_the_text_a_measure_compares_a_column_with_is_a_value_the_column_takes(frames):
    """``fact_order[outcome_status] = "closed"`` would silently be empty if the exported value were ``Closed``: compare each literal with the data."""
    literals = {(t, c, v) for m in MODEL.measures for t, c, v in re.findall(r'\b([a-z_]+)\[([a-z_0-9]+)\]\s*=\s*"([^"]*)"', m.dax)}
    assert literals >= {
        ("dim_pipeline_run", "status", "ok"), ("fact_order", "outcome_status", "closed"), ("fact_order", "outcome_status", "cancelled"),
        ("fact_validation", "severity", "bloqueante"), ("fact_validation", "status", "falla"),
    }
    for table, column, value in sorted(literals):
        assert value in set(frames[table][column].dropna()), f'{table}[{column}] = "{value}" is not a value of the exported column'


def test_the_boolean_tests_of_the_measures_are_on_boolean_columns():
    """``fact_order[is_alert] = TRUE ()`` only means something on a boolean column."""
    for table, column in {(t, c) for m in MODEL.measures for t, c in re.findall(r"\b([a-z_]+)\[([a-z_0-9]+)\]\s*=\s*(?:TRUE|FALSE) \(\)", m.dax)}:
        assert MODEL.table(table).column(column).dtype == "bool", (table, column)


# --- the pages -------------------------------------------------------------------------------------------------------------------------


def test_there_are_five_pages_in_the_order_of_the_plan():
    assert tuple(p.name_es for p in MODEL.pages) == PAGE_NAMES


def test_every_visual_is_inside_the_12_by_9_grid_without_overlapping_another():
    assert (PAGE_COLUMNS, PAGE_ROWS) == (12, 9)
    for p in MODEL.pages:
        taken: set[tuple[int, int]] = set()
        for v in p.visuals:
            assert 0 <= v.x and v.x + v.w <= PAGE_COLUMNS and 0 <= v.y and v.y + v.h <= PAGE_ROWS and v.w >= 1 and v.h >= 1, (p.name_es, v.title_es)
            cells = {(x, y) for x in range(v.x, v.x + v.w) for y in range(v.y, v.y + v.h)}
            assert not taken & cells, (p.name_es, v.title_es)
            taken |= cells


def test_every_field_of_every_visual_is_a_column_or_a_measure_of_the_model():
    columns = {f"{t.name}[{c}]" for t in MODEL.tables for c in t.column_names}
    measures = {f"[{m.name_es}]" for m in MODEL.measures}
    for p in MODEL.pages:
        for v in p.visuals:
            assert set(v.fields) <= columns | measures, (p.name_es, v.title_es, set(v.fields) - columns - measures)
            if v.kind != "textbox":
                assert v.fields, (p.name_es, v.title_es)
        titles = [v.title_es for v in p.visuals]
        assert len(titles) == len(set(titles)), f"{p.name_es}: two visuals with the same title"


def test_every_measure_is_used_by_a_page_or_by_another_measure():
    used = {f[1:-1] for p in MODEL.pages for v in p.visuals for f in v.fields if f.startswith("[")}
    used |= {r for m in MODEL.measures for r in dax_measure_refs(m.dax)}
    unused = [m.name_es for m in MODEL.measures if m.name_es not in used]
    assert unused == [], f"measures nobody shows or uses: {unused}"


def test_the_alerts_page_has_the_slicers_cards_table_and_charts_of_the_plan():
    visuals = page("Alertas").visuals
    assert [v.fields for v in visuals if v.kind == "slicer"] == [("dim_as_of[as_of_label]",), ("dim_shipping_mode[mode_es]",), ("dim_geo[market]",)]
    cards = [v.fields[0] for v in visuals if v.kind == "card"]
    assert cards[:6] == ["[Pedidos puntuados]", "[Alertas]", "[Alertado (%)]", "[Retrasos esperados]", "[Umbral]", "[Alertas abiertas a la fecha]"]
    assert "[Costos supuestos]" in cards, "the threshold is shown with the costs it rests on, as assumptions"
    table = next(v for v in visuals if v.kind == "table")
    assert table.title_es.startswith("Alertas abiertas a la fecha") and "fact_order[order_id]" in table.fields and "fact_order[risk]" in table.fields
    assert {"dim_shipping_mode[mode_es]", "dim_geo[market]", "dim_geo[order_region]", "dim_geo[order_country]", "dim_product[product_name]"} <= set(table.fields)
    assert visual("Alertas", "Alertas por modo de envío").fields == ("dim_shipping_mode[mode_es]", "[Alertas]")
    assert visual("Alertas", "Alertado (%) por mercado y modo").kind == "matrix"


def test_the_where_they_get_stuck_page_shows_the_mode_by_market_the_noon_cut_and_the_days():
    visuals = page("Dónde se traban").visuals
    assert visual("Dónde se traban", "Tasa de retraso observada por modo de envío y mercado").fields == ("dim_shipping_mode[mode_es]", "dim_geo[market]", "[Tasa de retraso observada]")
    heat = visual("Dónde se traban", "Tasa de retraso observada por modo de envío y hora del pedido")
    assert heat.kind == "matrix" and "fact_order[order_hour]" in heat.fields and "[Tasa de retraso observada]" in heat.fields
    assert {v.fields[0] for v in visuals if v.kind == "barChart"} == {"dim_geo[order_region]", "dim_product[department_name]"}
    assert visual("Dónde se traban", "Días reales y programados por modo de envío").fields == (
        "dim_shipping_mode[mode_es]", "[Días reales medios]", "[Días programados]")


def test_the_follow_up_page_has_the_monthly_precision_recall_expected_and_observed_and_the_break():
    assert visual("Seguimiento", "Precisión y recall mensuales de las alertas cerradas").fields == (
        "dim_month[year_month_label]", "[Precisión mensual]", "[Recall mensual]")
    assert visual("Seguimiento", "Retrasos esperados y observados por mes").fields == (
        "dim_month[year_month_label]", "[Retrasos esperados en el mes]", "[Retrasos observados en el mes]")
    assert visual("Seguimiento", "Alertado (%) por mes").fields[-1] == "[Alertado mensual (%)]"
    assert [v.fields[0] for v in page("Seguimiento").visuals if v.kind == "card"] == [
        "[Precisión de las alertas]", "[Recall de las alertas]", "[Beneficio neto estimado (u.)]", "[Riesgo medio]"]
    note = [v for v in page("Seguimiento").visuals if v.kind == "textbox"]
    assert len(note) == 1 and "2017-10" in note[0].title_es, "the change of shape of the data is marked on the page"


def test_the_model_validation_page_has_the_criteria_the_curves_the_leakage_and_the_importance():
    visuals = page("Validación del modelo").visuals
    criteria = next(v for v in visuals if v.kind == "table")
    assert criteria.title_es == "Criterios C01 a C10"
    assert criteria.fields == (
        "fact_evaluation_summary[criterion_id]", "fact_evaluation_summary[name_es]", "fact_evaluation_summary[threshold_es]", "fact_evaluation_summary[seen_by_research]",
        "fact_evaluation_summary[seen_by_planner]", "fact_evaluation_summary[verdict_es]",
    )
    assert visual("Validación del modelo", "Veredicto").fields == ("[Veredicto]",)
    assert visual("Validación del modelo", "Curva ROC del modelo, B2 y B5 en la prueba").fields == ("fact_roc[fpr]", "fact_roc[tpr]", "fact_roc[method]")
    assert visual("Validación del modelo", "Calibración").fields == ("fact_calibration[p_mean]", "fact_calibration[observed]", "fact_calibration[method]")
    assert visual("Validación del modelo", "Curva de costo: beneficio por 1.000 pedidos según el punto de equilibrio").fields == (
        "fact_cost_sensitivity[break_even]", "fact_cost_sensitivity[nb_per_1000]", "fact_cost_sensitivity[method]")
    assert visual("Validación del modelo", "AUC en validación con cada columna que delata el resultado: por qué quedan fuera").fields == (
        "fact_leakage[column_es]", "fact_leakage[auc_with_mode_valid]")
    assert visual("Validación del modelo", "Importancia por permutación").fields == ("fact_importance[feature_es]", "fact_importance[auc_drop]")


def test_the_quality_page_has_the_five_cards_the_rules_the_runs_and_a_run_slicer():
    visuals = page("Calidad y corridas").visuals
    assert [v.fields[0] for v in visuals if v.kind == "card"] == [
        "[Última fecha de cálculo]", "[Estado de la última corrida]", "[Reglas bloqueantes fallidas]", "[Pedidos]", "[Pedidos cancelados]"]
    rules = visual("Calidad y corridas", "Reglas de validación")
    assert rules.fields == (
        "fact_validation[rule_id]", "fact_validation[name_es]", "fact_validation[severity]", "fact_validation[rows_affected]", "fact_validation[orders_affected]",
        "fact_validation[status]",
    )
    assert visual("Calidad y corridas", "Corrida").fields == ("dim_pipeline_run[run_key]",)
    assert visual("Calidad y corridas", "Corridas").kind == "table"


def test_the_pages_never_name_a_personal_column_or_a_customer_identifier():
    fields = {f for p in MODEL.pages for v in p.visuals for f in v.fields} | {f"{t}[{c}]" for m in MODEL.measures for t, c in dax_column_refs(m.dax)}
    assert not [f for f in fields if re.search(r"customer|email|password|street|zip|latitude|longitude", f, re.I)], "no personal column in a visual or a measure"


# --- the check of the model itself ----------------------------------------------------------------------------------------------------------


def test_the_model_check_refuses_every_way_a_measure_or_a_page_can_be_wrong():
    good = Visual("card", "Pedidos", ("[Pedidos]",), 0, 0, 2, 1)

    def problems(**changes) -> list[str]:
        return validate_model(replace(MODEL, **changes))

    with_measure = lambda m: problems(measures=(*MODEL.measures, m))                 # noqa: E731
    with_visual = lambda v: problems(pages=(*MODEL.pages[:-1], Page("Extra", (v,))))  # noqa: E731
    assert any("fact_order[no_such] does not exist" in p for p in with_measure(Measure("X", "SUM ( fact_order[no_such] )", "0", "Una medida.")))
    assert any("the measure [Nope] does not exist" in p for p in with_measure(Measure("X", "[Nope] + 1", "0", "Una medida.")))
    assert any("unbalanced" in p for p in with_measure(Measure("X", "SUM ( fact_order[risk]", "0", "Una medida.")))
    assert any("unbalanced" in p for p in with_measure(Measure("X", 'CALCULATE ( [Pedidos], fact_order[block] = "test )', "0", "Una medida.")))
    assert any("needs a format and a description" in p for p in with_measure(Measure("X", "[Pedidos]", "", "Una medida.")))
    assert any("appears more than once" in p for p in with_measure(MODEL.measures[0]))
    assert any("unknown measure [Nope]" in p for p in with_visual(Visual("card", "Nope", ("[Nope]",), 0, 0, 2, 1)))
    assert any("unknown column fact_order[no_such]" in p for p in with_visual(Visual("table", "Nope", ("fact_order[no_such]",), 0, 0, 2, 1)))
    assert any("malformed field" in p for p in with_visual(Visual("table", "Nope", ("fact_order.risk",), 0, 0, 2, 1)))
    assert any("outside the 12 x 9 grid" in p for p in with_visual(Visual("card", "Wide", ("[Pedidos]",), 11, 0, 2, 1)))
    assert any("outside the 12 x 9 grid" in p for p in with_visual(Visual("card", "Low", ("[Pedidos]",), 0, 8, 2, 2)))
    assert any("outside the 12 x 9 grid" in p for p in with_visual(Visual("card", "Flat", ("[Pedidos]",), 0, 0, 0, 1)))
    page_with_two = Page("Extra", (good, Visual("card", "Pedidos 2", ("[Pedidos]",), 1, 0, 2, 1)))
    assert any("overlaps Pedidos" in p for p in problems(pages=(page_with_two,)))
    assert problems(pages=(Page("Extra", (good, Visual("card", "Pedidos 2", ("[Pedidos]",), 2, 0, 2, 1))),)) == []


def test_a_model_without_measures_or_pages_still_has_the_tables_the_relationships_and_the_schemas_of_u17():
    """The form U17 gave the model keeps working: a ``SemanticModel`` of tables and relationships alone is valid, and the schemas of the tables did not move."""
    bare = SemanticModel(TABLES, MODEL.relationships)
    assert bare.measures == () and bare.pages == () and validate_model(bare) == []
    assert len(MODEL.tables) == 20 and [r.rel_id for r in MODEL.relationships] == [f"R{i:02d}" for i in range(1, 10)]
    order = MODEL.table("fact_order")
    assert [f.name for f in arrow_schema(order)][:3] == ["order_id", "order_date_key", "mode_key"] and arrow_schema(order).field("order_id").nullable is False
    assert arrow_schema(order).field("risk").nullable is True
