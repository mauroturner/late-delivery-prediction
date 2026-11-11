"""The semantic model of the Power BI report: the single source of truth for its tables and relationships (D21).

The tables with their columns and the relationships (all one-to-many, single direction, active) live here, and the Arrow schema of every Parquet file, the
data dictionary and the HTML design all come from this module, so a change of a column is made once and the tests that read it (keys, foreign keys, schemas) keep
them honest. The measures and the pages arrive with U18. Names of tables, columns and files are in English; labels and descriptions are in Spanish (D26).

Dimensions have a unique key; facts have a grain (``Table.key``). ``dim_as_of``, ``dim_model`` and the facts of the evaluation have no relationship: they feed their
own visuals and measures (docs/plan.md section 16).

U18 adds the DAX measures and the five pages with their visuals on a grid of 12 columns by 9 rows (docs/plan.md section 17), in the form of project 02's model:
``Measure``, ``Visual`` and ``Page``. A measure names its columns as ``table[column]`` and the other measures as ``[Measure]``; ``validate_model`` checks that every one
exists, that every field of every visual resolves and that the visuals of a page stay on the grid without overlapping. No measure is executed in this project (there is no
DAX engine here); the HTML design says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa

ARROW_TYPES = {
    "int32": pa.int32(), "int64": pa.int64(), "float64": pa.float64(), "string": pa.string(), "bool": pa.bool_(), "date": pa.date32(),
}
PAGE_COLUMNS, PAGE_ROWS = 12, 9          # 12 columns x 9 rows of equal cells fill a 16:9 canvas of 1280 x 720 px (106.7 x 80 px)
PAGE_WIDTH_PX, PAGE_HEIGHT_PX = 1280, 720
YYYYMMDD, YYYYMM = "AAAAMMDD", "AAAAMM"
U = "u."          # the generic units of cost (D14)


@dataclass(frozen=True)
class Column:
    name: str
    dtype: str  # a key of ARROW_TYPES
    description_es: str
    unit: str | None = None


@dataclass(frozen=True)
class Table:
    name: str
    kind: Literal["dimension", "fact"]
    key: tuple[str, ...]  # a dimension's unique key or a fact's grain
    columns: tuple[Column, ...]
    description_es: str = ""

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


@dataclass(frozen=True)
class Relationship:
    rel_id: str
    dim_table: str
    dim_column: str
    fact_table: str
    fact_column: str
    cardinality: str = "one_to_many"
    cross_filter: str = "single"
    active: bool = True


@dataclass(frozen=True)
class Measure:
    name_es: str
    dax: str
    format_string: str
    description_es: str


@dataclass(frozen=True)
class Visual:
    kind: str
    title_es: str
    fields: tuple[str, ...]  # "table[column]" or "[Measure]"
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Page:
    name_es: str
    visuals: tuple[Visual, ...]


@dataclass(frozen=True)
class SemanticModel:
    tables: tuple[Table, ...]
    relationships: tuple[Relationship, ...]
    measures: tuple[Measure, ...] = ()
    pages: tuple[Page, ...] = ()

    def table(self, name: str) -> Table:
        return next(t for t in self.tables if t.name == name)

    def measure(self, name: str) -> Measure:
        return next(m for m in self.measures if m.name_es == name)


def _c(name: str, dtype: str, description_es: str, unit: str | None = None) -> Column:
    return Column(name, dtype, description_es, unit)


# --- dimensions -------------------------------------------------------------------------------------------------

DIM_DATE = Table("dim_date", "dimension", ("date_key",), (
    _c("date_key", "int32", "Fecha del pedido como número (clave).", YYYYMMDD),
    _c("date", "date", "La fecha."),
    _c("year", "int32", "Año."),
    _c("month", "int32", "Mes del año (1 a 12)."),
    _c("month_key", "int32", "Año y mes.", YYYYMM),
    _c("month_name_es", "string", "Nombre del mes en español."),
    _c("weekday", "int32", "Día de la semana (0 es lunes)."),
    _c("weekday_es", "string", "Nombre del día de la semana en español."),
    _c("block_es", "string", "Bloque de la partición en el tiempo al que pertenece la fecha (entrenamiento, purga, validación, prueba o posterior al cambio de forma)."),
    _c("is_test", "bool", "Verdadero si la fecha es del bloque de la prueba."),
), "Un día por fila, del primer pedido al cierre. Sirve para filtrar los pedidos por la fecha en que se crearon.")

DIM_MONTH = Table("dim_month", "dimension", ("month_key",), (
    _c("month_key", "int32", "Año y mes (clave).", YYYYMM),
    _c("month_start", "date", "Primer día del mes."),
    _c("year_month_label", "string", "El mes escrito como AAAA-MM."),
    _c("block_es", "string", "Bloque de la partición en el que cae el primer día del mes."),
    _c("regime", "string", "pre_break antes del cambio de forma de los datos (2017-10) y post_break desde entonces."),
), "Un mes por fila; es el eje del seguimiento mensual.")

DIM_SHIPPING_MODE = Table("dim_shipping_mode", "dimension", ("mode_key",), (
    _c("mode_key", "string", "Modo de envío tal como viene de la fuente (clave)."),
    _c("mode_es", "string", "Nombre del modo en español."),
    _c("scheduled_days", "int32", "Días programados que fija la fuente para el modo.", "días"),
    _c("train_late_rate", "float64", "Tasa de retraso del modo en el bloque de entrenamiento: la regla B2.", "proporción"),
    _c("sort_order", "int32", "Orden de aparición, de menos a más días programados."),
), "Los cuatro modos de envío con la tasa de retraso del entrenamiento; vacía si todavía no hay modelo registrado.")

DIM_GEO = Table("dim_geo", "dimension", ("geo_key",), (
    _c("geo_key", "int32", "Número de la combinación de mercado, región y país (clave sustituta)."),
    _c("market", "string", "Mercado."),
    _c("order_region", "string", "Región del pedido."),
    _c("order_country", "string", "País del pedido."),
), "Una fila por combinación de mercado, región y país que tiene pedidos. No hay ciudades, estados, códigos postales ni coordenadas: se descartan en la lectura (D07).")

DIM_PRODUCT = Table("dim_product", "dimension", ("product_key",), (
    _c("product_key", "int32", "Número de tarjeta del producto de la fuente (clave)."),
    _c("product_name", "string", "Nombre del producto, sin espacios finales."),
    _c("category_name", "string", "Categoría del producto."),
    _c("department_name", "string", "Departamento del producto."),
), "Un producto por fila.")

DIM_SEGMENT = Table("dim_segment", "dimension", ("segment_key",), (
    _c("segment_key", "string", "Segmento del cliente tal como viene de la fuente (clave)."),
    _c("segment_es", "string", "Nombre del segmento en español."),
), "Los segmentos de cliente de la fuente.")

DIM_PAYMENT = Table("dim_payment", "dimension", ("payment_key",), (
    _c("payment_key", "string", "Tipo de pago tal como viene de la fuente (clave)."),
    _c("payment_es", "string", "Nombre del tipo de pago en español."),
), "Los tipos de pago de la fuente.")

DIM_MODEL = Table("dim_model", "dimension", ("model_version",), (
    _c("model_version", "string", "Versión del modelo congelado (clave)."),
    _c("feature_set", "string", "Conjunto de variables elegido en V1 (f0, f1 o f2)."),
    _c("params_es", "string", "Parámetros del candidato elegido, en palabras."),
    _c("calibration", "string", "Método de calibración (isotónica)."),
    _c("threshold", "float64", "Umbral de alerta elegido en V2 por beneficio neto.", "probabilidad"),
    _c("alert_cost", "float64", "Costo supuesto de una alerta.", U),
    _c("late_cost", "float64", "Costo supuesto de un pedido que llega tarde.", U),
    _c("effect", "float64", "Parte del costo del retraso que evita una alerta (supuesto).", "proporción"),
    _c("break_even", "float64", "Punto de equilibrio: costo de la alerta dividido por costo del retraso por efecto.", "probabilidad"),
    _c("trained_as_of", "date", "Fecha de cálculo del lanzamiento en que se entrenó."),
    _c("config_sha256", "string", "Huella SHA-256 de la configuración del lanzamiento."),
    _c("seal_state", "string", "sealed si la configuración es la que consta en el sello; post_seal si es otra; none si todavía no hay sello."),
), "El modelo congelado en el lanzamiento y los costos supuestos con que se eligió su umbral. Sin relaciones: alimenta tarjetas y medidas.")

DIM_PIPELINE_RUN = Table("dim_pipeline_run", "dimension", ("run_key",), (
    _c("run_key", "string", "Clave de la corrida (clave)."),
    _c("started_at", "string", "Inicio de la corrida."),
    _c("finished_at", "string", "Fin de la corrida; vacío mientras corre."),
    _c("status", "string", "running, ok, failed, rejected o no_delivery."),
    _c("delivery_id", "string", "Entrega que cargó la corrida."),
    _c("as_of_date", "date", "Fecha de cálculo de la corrida."),
    _c("source_sha256", "string", "Huella SHA-256 del archivo de la fuente."),
    _c("orders_delivery", "int64", "Pedidos de la entrega."),
    _c("outcomes_delivery", "int64", "Resultados de la entrega."),
    _c("orders_scored", "int64", "Pedidos puntuados por la corrida."),
    _c("alerts", "int64", "Alertas emitidas por la corrida."),
    _c("peak_working_set_mb", "float64", "Pico del conjunto de trabajo del proceso (PeakWorkingSetSize en Windows, ru_maxrss en Linux).", "MB"),
    _c("peak_commit_mb", "float64", "Pico de memoria comprometida (PeakPagefileUsage); vacío fuera de Windows.", "MB"),
    _c("message", "string", "Mensaje de la corrida."),
    _c("as_of_key", "int32", "Fecha de cálculo como número.", YYYYMMDD),
), "Una fila por corrida con el estado con que la dejó SQLite.")

DIM_AS_OF = Table("dim_as_of", "dimension", ("as_of_key",), (
    _c("as_of_key", "int32", "Fecha de cálculo como número (clave).", YYYYMMDD),
    _c("as_of_date", "date", "Fecha de cálculo de una entrega cargada."),
    _c("as_of_label", "string", "Entrega y fecha, para el segmentador."),
), "Tabla de parámetro: una fila por entrega cargada. Elige la fecha de cálculo en las medidas; no tiene relaciones.")

# --- facts ---------------------------------------------------------------------------------------------------------------

FACT_ORDER = Table("fact_order", "fact", ("order_id",), (
    _c("order_id", "int64", "Número de pedido de la fuente (grano). Es la clave con la que un equipo actúa sobre una alerta (D32)."),
    _c("order_date_key", "int32", "Fecha del pedido.", YYYYMMDD),
    _c("mode_key", "string", "Modo de envío."),
    _c("geo_key", "int32", "Mercado, región y país del pedido."),
    _c("main_product_key", "int32", "Producto de la línea principal del pedido (la de más ventas)."),
    _c("segment_key", "string", "Segmento del cliente."),
    _c("payment_key", "string", "Tipo de pago."),
    _c("order_ts", "string", "Fecha y hora de creación, sin zona."),
    _c("order_hour", "int32", "Hora del pedido (0 a 23)."),
    _c("order_weekday", "int32", "Día de la semana del pedido (0 es lunes)."),
    _c("n_lines", "int32", "Líneas del pedido."),
    _c("units", "int32", "Unidades del pedido.", "unidades"),
    _c("gross_sales", "float64", "Ventas del pedido, suma de las líneas.", "moneda del archivo"),
    _c("item_total", "float64", "Total del pedido, suma de las líneas.", "moneda del archivo"),
    _c("discount", "float64", "Descuento del pedido, suma de las líneas.", "moneda del archivo"),
    _c("block", "string", "Bloque de la partición: train, purge_1, valid_v1, valid_v2, purge_2, test, post_break o outside."),
    _c("regime", "string", "pre_break o post_break."),
    _c("outcome_status", "string", "open si el resultado todavía no se conoce, closed si llegó, cancelled si el pedido se canceló."),
    _c("outcome_known_date", "date", "Fecha en que se conoció el resultado; vacía mientras está abierto."),
    _c("real_days", "int32", "Días reales de envío; vacíos si está abierto o cancelado.", "días"),
    _c("is_late", "bool", "Verdadero si los días reales superan a los programados; vacío si está abierto o cancelado."),
    _c("risk", "float64", "Probabilidad calibrada de llegar tarde, calculada al crear el pedido; vacía si el pedido no se puntuó (los del lanzamiento).", "probabilidad"),
    _c("is_alert", "bool", "Verdadero si el riesgo alcanza el umbral; vacío si no se puntuó."),
    _c("threshold", "float64", "Umbral con que se marcó.", "probabilidad"),
    _c("scored_as_of_date", "date", "Fecha de cálculo en que se puntuó."),
    _c("model_version", "string", "Versión del modelo que puntuó."),
    _c("run_key", "string", "Corrida que puntuó."),
), "Un pedido por fila, con el puntaje y el resultado donde existen. El puntaje no cambia nunca después de emitido.")

FACT_TRACKING_MONTHLY = Table("fact_tracking_monthly", "fact", ("month_key", "mode_key"), (
    _c("month_key", "int32", "Mes del pedido.", YYYYMM),
    _c("mode_key", "string", "Modo de envío."),
    _c("orders_scored", "int32", "Pedidos puntuados."),
    _c("alerts", "int32", "Alertas emitidas."),
    _c("closed_orders", "int32", "Pedidos puntuados con resultado conocido que no se cancelaron."),
    _c("closed_alerts", "int32", "Alertas sobre pedidos cerrados."),
    _c("alerts_late", "int32", "Alertas cerradas que llegaron tarde."),
    _c("late_closed", "int32", "Pedidos cerrados que llegaron tarde."),
    _c("late_missed", "int32", "Pedidos cerrados que llegaron tarde sin alerta."),
    _c("late_expected", "float64", "Retrasos que el modelo esperaba: suma del riesgo de los pedidos cerrados.", "pedidos"),
    _c("cancelled_orders", "int32", "Pedidos puntuados que se cancelaron después."),
    _c("alerts_on_cancelled", "int32", "Alertas sobre pedidos que se cancelaron después (I04)."),
), "Seguimiento mensual de las alertas cerradas a la última fecha de cálculo, por mes del pedido y modo.")

FACT_METHOD_METRICS = Table("fact_method_metrics", "fact", ("scope", "method", "seal_state"), (
    _c("scope", "string", "valid_v2, test o post_break."),
    _c("method", "string", "model, b0_all_late, b1_first_class, b2_mode, b3_scheduled_le2, b4_standard_only o b5_mode_noon."),
    _c("label_es", "string", "Nombre del método en español."),
    _c("auc", "float64", "Área bajo la curva ROC, con los empates por mitades."),
    _c("pr_auc", "float64", "Precisión promedio."),
    _c("brier", "float64", "Error cuadrático medio de la probabilidad; vacío en las reglas sin probabilidad."),
    _c("ece", "float64", "Error de calibración esperado con 10 intervalos; vacío en las reglas sin probabilidad."),
    _c("threshold", "float64", "Umbral con que el método marca.", "probabilidad"),
    _c("flagged_share", "float64", "Parte de los pedidos que marca.", "proporción"),
    _c("precision", "float64", "De los pedidos marcados, parte que llegó tarde; vacía si no marca ninguno.", "proporción"),
    _c("recall", "float64", "De los pedidos tarde, parte que marca.", "proporción"),
    _c("nb_total", "float64", "Beneficio neto total con los costos supuestos.", U),
    _c("nb_per_1000", "float64", "Beneficio neto por cada 1.000 pedidos.", U),
    _c("seal_state", "string", "sealed si se calculó con la configuración sellada; post_seal si con otra."),
), "Las métricas de cada método en cada bloque evaluado.")

FACT_EVALUATION_SUMMARY = Table("fact_evaluation_summary", "fact", ("criterion_id", "seal_state"), (
    _c("criterion_id", "string", "C01 a C10."),
    _c("name_es", "string", "Qué mide el criterio."),
    _c("value", "float64", "Valor del modelo."),
    _c("baseline_value", "float64", "Valor con el que se compara."),
    _c("ci_low", "float64", "Límite inferior del intervalo del 95 % (C02 y C03)."),
    _c("ci_high", "float64", "Límite superior del intervalo del 95 % (C02 y C03)."),
    _c("threshold_es", "string", "Umbral preregistrado, en palabras."),
    _c("passed", "bool", "Verdadero si se cumple; vacío en C10, que no tiene umbral."),
    _c("seen_by_research", "string", "Si la investigación había visto este número antes de fijar el umbral: sí, en parte o no."),
    _c("seen_by_planner", "string", "Si el agente de planificación lo había visto: sí, en parte o no."),
    _c("verdict_es", "string", "El veredicto, con su número; dice «supera» o «no supera»."),
    _c("seal_state", "string", "sealed o post_seal."),
), "Los criterios C01 a C10 con su veredicto.")

FACT_ROC = Table("fact_roc", "fact", ("scope", "method", "seal_state", "point"), (
    _c("scope", "string", "valid_v2, test o post_break."),
    _c("method", "string", "model, b2_mode o b5_mode_noon."),
    _c("seal_state", "string", "sealed o post_seal."),
    _c("point", "int32", "Orden del punto en la curva."),
    _c("fpr", "float64", "Tasa de falsos positivos.", "proporción"),
    _c("tpr", "float64", "Tasa de verdaderos positivos.", "proporción"),
), "La curva ROC de cada método, reducida a unos 200 puntos.")

FACT_CALIBRATION = Table("fact_calibration", "fact", ("scope", "method", "seal_state", "bin"), (
    _c("scope", "string", "valid_v2, test o post_break."),
    _c("method", "string", "model, b2_mode o b5_mode_noon."),
    _c("seal_state", "string", "sealed o post_seal."),
    _c("bin", "int32", "Intervalo de probabilidad (0 a 9)."),
    _c("p_mean", "float64", "Probabilidad media del intervalo.", "probabilidad"),
    _c("observed", "float64", "Tasa de retraso observada en el intervalo.", "proporción"),
    _c("orders", "int32", "Pedidos del intervalo."),
), "La calibración de cada método por intervalos de igual ancho.")

FACT_COST_SENSITIVITY = Table("fact_cost_sensitivity", "fact", ("scope", "break_even", "method", "seal_state"), (
    _c("scope", "string", "valid_v2 o test."),
    _c("break_even", "float64", "Punto de equilibrio del costo, de 0,10 a 0,90.", "probabilidad"),
    _c("method", "string", "model, b2_mode o b5_mode_noon."),
    _c("threshold", "float64", "Umbral elegido en V2 para ese punto de equilibrio.", "probabilidad"),
    _c("nb_per_1000", "float64", "Beneficio neto por cada 1.000 pedidos.", U),
    _c("flagged_share", "float64", "Parte de los pedidos que marca.", "proporción"),
    _c("seal_state", "string", "sealed o post_seal."),
), "Cómo cambia el beneficio neto, y la decisión, si cambian los costos supuestos.")

FACT_LEAKAGE = Table("fact_leakage", "fact", ("source_column",), (
    _c("source_column", "string", "Columna de la fuente (grano); shipping_mode es la referencia."),
    _c("column_es", "string", "Nombre en español."),
    _c("kind", "string", "reference, outcome o profit."),
    _c("known_at_es", "string", "Cuándo se conoce la columna."),
    _c("auc_with_mode_valid", "float64", "AUC en V2 de un clasificador entrenado con el modo de envío y esta columna."),
    _c("note_es", "string", "Por qué queda fuera del modelo."),
), "Qué le haría al AUC cada columna que delata el resultado.")

FACT_IMPORTANCE = Table("fact_importance", "fact", ("feature",), (
    _c("feature", "string", "Variable del modelo (grano)."),
    _c("feature_es", "string", "Nombre en español."),
    _c("auc_drop", "float64", "Caída media del AUC en V2 al permutar la variable."),
), "Importancia por permutación de las variables del modelo en uso, medida en V2.")

FACT_VALIDATION = Table("fact_validation", "fact", ("run_key", "rule_id"), (
    _c("run_key", "string", "Corrida."),
    _c("rule_id", "string", "Identificador de la regla (V01, M03, I02...)."),
    _c("name_es", "string", "Qué comprueba la regla."),
    _c("severity", "string", "bloqueante, advertencia o informativa."),
    _c("scope", "string", "source, delivery o model."),
    _c("status", "string", "pasa, falla o advierte."),
    _c("rows_affected", "int64", "Filas afectadas.", "filas"),
    _c("orders_affected", "int64", "Pedidos afectados.", "pedidos"),
), "Una fila por corrida y regla de validación.")

TABLES = (
    DIM_DATE, DIM_MONTH, DIM_SHIPPING_MODE, DIM_GEO, DIM_PRODUCT, DIM_SEGMENT, DIM_PAYMENT, DIM_MODEL, DIM_PIPELINE_RUN, DIM_AS_OF,
    FACT_ORDER, FACT_TRACKING_MONTHLY, FACT_METHOD_METRICS, FACT_EVALUATION_SUMMARY, FACT_ROC, FACT_CALIBRATION, FACT_COST_SENSITIVITY, FACT_LEAKAGE, FACT_IMPORTANCE,
    FACT_VALIDATION,
)

# R01..R09 (docs/plan.md section 16): dimension column -> fact column
_PAIRS = (
    ("dim_date", "date_key", "fact_order", "order_date_key"),
    ("dim_shipping_mode", "mode_key", "fact_order", "mode_key"),
    ("dim_shipping_mode", "mode_key", "fact_tracking_monthly", "mode_key"),
    ("dim_geo", "geo_key", "fact_order", "geo_key"),
    ("dim_product", "product_key", "fact_order", "main_product_key"),
    ("dim_segment", "segment_key", "fact_order", "segment_key"),
    ("dim_payment", "payment_key", "fact_order", "payment_key"),
    ("dim_month", "month_key", "fact_tracking_monthly", "month_key"),
    ("dim_pipeline_run", "run_key", "fact_validation", "run_key"),
)
RELATIONSHIPS = tuple(Relationship(f"R{i:02d}", *pair) for i, pair in enumerate(_PAIRS, start=1))

# --- measures (docs/plan.md section 17) -------------------------------------------------------------------------------------------------------

INTEGER, PERCENT, DECIMAL_1, DECIMAL_2, DATE_FORMAT, TEXT_FORMAT = "#,0", "0.0%", "0.0", "0.00", "yyyy-mm-dd", "@"

MEASURES: tuple[Measure, ...] = (
    Measure(
        "Fecha de cálculo elegida",
        "IF ( ISFILTERED ( dim_as_of[as_of_key] ),\n"
        "    SELECTEDVALUE ( dim_as_of[as_of_date] ),\n"
        '    CALCULATE ( MAX ( dim_pipeline_run[as_of_date] ), dim_pipeline_run[status] = "ok" ) )',
        DATE_FORMAT, "La fecha de cálculo del segmentador o, si no se eligió ninguna, la de la última corrida terminada bien. Las alertas abiertas la usan.",
    ),
    Measure("Pedidos", "COUNTROWS ( fact_order )", INTEGER, "Pedidos de las entregas cargadas, con o sin puntaje."),
    Measure(
        "Pedidos puntuados", "CALCULATE ( COUNTROWS ( fact_order ), NOT ISBLANK ( fact_order[risk] ) )", INTEGER,
        "Pedidos con un riesgo calculado al crearse; los del lanzamiento no se puntúan.",
    ),
    Measure("Alertas", "CALCULATE ( COUNTROWS ( fact_order ), fact_order[is_alert] = TRUE () )", INTEGER, "Pedidos cuyo riesgo alcanzó el umbral cuando se crearon."),
    Measure("Alertado (%)", "DIVIDE ( [Alertas], [Pedidos puntuados] )", PERCENT, "Parte de los pedidos puntuados que quedó con alerta."),
    Measure("Riesgo medio", "AVERAGE ( fact_order[risk] )", PERCENT, "Probabilidad media de llegar tarde de los pedidos puntuados."),
    Measure("Retrasos esperados", "SUM ( fact_order[risk] )", DECIMAL_1, "Pedidos que el modelo esperaba que llegaran tarde: la suma de los riesgos."),
    Measure(
        "Alertas abiertas a la fecha",
        "VAR d = [Fecha de cálculo elegida]\n"
        "RETURN CALCULATE ( [Alertas], fact_order[scored_as_of_date] <= d,\n"
        "    FILTER ( fact_order, ISBLANK ( fact_order[outcome_known_date] ) || fact_order[outcome_known_date] > d ) )",
        INTEGER, "Alertas emitidas hasta la fecha elegida cuyo resultado todavía no se conocía ese día.",
    ),
    Measure("Pedidos cerrados", 'CALCULATE ( COUNTROWS ( fact_order ), fact_order[outcome_status] = "closed" )', INTEGER, "Pedidos con resultado conocido que no se cancelaron."),
    Measure("Pedidos tarde", "CALCULATE ( COUNTROWS ( fact_order ), fact_order[is_late] = TRUE () )", INTEGER, "Pedidos cerrados cuyos días reales superan a los programados."),
    Measure("Tasa de retraso observada", "DIVIDE ( [Pedidos tarde], [Pedidos cerrados] )", PERCENT, "Parte de los pedidos cerrados que llegó tarde."),
    Measure("Alertas cerradas", 'CALCULATE ( [Alertas], fact_order[outcome_status] = "closed" )', INTEGER, "Alertas sobre pedidos que ya tienen resultado."),
    Measure("Alertas que llegaron tarde", "CALCULATE ( [Alertas], fact_order[is_late] = TRUE () )", INTEGER, "Alertas sobre pedidos que llegaron tarde: las que acertaron."),
    Measure("Tarde sin alerta", "CALCULATE ( [Pedidos tarde], fact_order[is_alert] = FALSE () )", INTEGER, "Pedidos puntuados que llegaron tarde sin que se los marcara."),
    Measure("Precisión de las alertas", "DIVIDE ( [Alertas que llegaron tarde], [Alertas cerradas] )", PERCENT, "De las alertas cerradas, parte que llegó tarde."),
    Measure(
        "Recall de las alertas", "DIVIDE ( [Alertas que llegaron tarde], [Alertas que llegaron tarde] + [Tarde sin alerta] )", PERCENT,
        "De los pedidos puntuados que llegaron tarde, parte que tenía alerta.",
    ),
    Measure("Umbral", "SELECTEDVALUE ( dim_model[threshold] )", DECIMAL_2, "Probabilidad desde la cual un pedido queda con alerta; se eligió en V2 por beneficio neto."),
    Measure(
        "Costos supuestos",
        '"Costos supuestos, no medidos: alerta " & FORMAT ( SELECTEDVALUE ( dim_model[alert_cost] ), "0.0" ) & " u., retraso " & '
        'FORMAT ( SELECTEDVALUE ( dim_model[late_cost] ), "0.0" ) & " u., efecto " & FORMAT ( SELECTEDVALUE ( dim_model[effect] ), "0.00" )',
        TEXT_FORMAT, "Los costos con que se eligió el umbral, dichos como supuestos: los datos no traen costos.",
    ),
    Measure(
        "Beneficio neto estimado (u.)",
        "[Alertas que llegaron tarde] * SELECTEDVALUE ( dim_model[late_cost] ) * SELECTEDVALUE ( dim_model[effect] )\n"
        "    - [Alertas cerradas] * SELECTEDVALUE ( dim_model[alert_cost] )",
        DECIMAL_1, "Beneficio de las alertas cerradas con los costos supuestos, en unidades de costo genéricas.",
    ),
    Measure(
        "Precisión mensual", "DIVIDE ( SUM ( fact_tracking_monthly[alerts_late] ), SUM ( fact_tracking_monthly[closed_alerts] ) )", PERCENT,
        "Precisión de las alertas cerradas, por mes del pedido.",
    ),
    Measure(
        "Recall mensual",
        "DIVIDE ( SUM ( fact_tracking_monthly[alerts_late] ),\n    SUM ( fact_tracking_monthly[alerts_late] ) + SUM ( fact_tracking_monthly[late_missed] ) )",
        PERCENT, "Recall de las alertas cerradas, por mes del pedido.",
    ),
    Measure("Retrasos esperados en el mes", "SUM ( fact_tracking_monthly[late_expected] )", DECIMAL_1, "Suma del riesgo de los pedidos cerrados del mes."),
    Measure("Retrasos observados en el mes", "SUM ( fact_tracking_monthly[late_closed] )", INTEGER, "Pedidos cerrados del mes que llegaron tarde."),
    Measure(
        "Alertado mensual (%)", "DIVIDE ( SUM ( fact_tracking_monthly[alerts] ), SUM ( fact_tracking_monthly[orders_scored] ) )", PERCENT,
        "Parte de los pedidos puntuados del mes que quedó con alerta.",
    ),
    Measure("Días reales medios", "AVERAGE ( fact_order[real_days] )", DECIMAL_1, "Días reales de envío de los pedidos cerrados."),
    Measure("Días programados", "AVERAGE ( dim_shipping_mode[scheduled_days] )", DECIMAL_1, "Días programados que fija la fuente para el modo de envío."),
    Measure("Veredicto", "SELECTEDVALUE ( fact_evaluation_summary[verdict_es] )", TEXT_FORMAT, "El veredicto del criterio elegido, con su número; dice «supera» o «no supera»."),
    Measure("Última fecha de cálculo", 'CALCULATE ( MAX ( dim_pipeline_run[as_of_date] ), dim_pipeline_run[status] = "ok" )', DATE_FORMAT, "La fecha de cálculo de la última corrida terminada bien."),
    Measure(
        "Estado de la última corrida",
        "VAR k = CALCULATE ( MAX ( dim_pipeline_run[as_of_key] ), REMOVEFILTERS ( dim_pipeline_run ) )\n"
        "RETURN CALCULATE ( SELECTEDVALUE ( dim_pipeline_run[status] ), REMOVEFILTERS ( dim_pipeline_run ), dim_pipeline_run[as_of_key] = k )",
        TEXT_FORMAT, "El estado (running, ok, failed…) de la corrida con la fecha de cálculo más reciente.",
    ),
    Measure(
        "Reglas bloqueantes fallidas",
        'CALCULATE ( COUNTROWS ( fact_validation ), fact_validation[severity] = "bloqueante", fact_validation[status] = "falla" )', INTEGER,
        "Reglas bloqueantes que fallaron en las corridas elegidas.",
    ),
    Measure("Pedidos cancelados", 'CALCULATE ( COUNTROWS ( fact_order ), fact_order[outcome_status] = "cancelled" )', INTEGER, "Pedidos que se cancelaron después de crearse; están fuera de la tarea."),
)

# --- pages: visuals on a 12 x 9 grid (x, y, width, height in cells; docs/plan.md section 17) ------------------------------------------------------------

_OPEN_ALERTS = (
    "fact_order[order_id]", "dim_date[date]", "dim_shipping_mode[mode_es]", "dim_geo[market]", "dim_geo[order_region]", "dim_geo[order_country]", "dim_product[product_name]",
    "fact_order[risk]",
)

PAGES: tuple[Page, ...] = (
    Page("Alertas", (
        Visual("slicer", "Fecha de cálculo", ("dim_as_of[as_of_label]",), 0, 0, 4, 1),
        Visual("slicer", "Modo de envío", ("dim_shipping_mode[mode_es]",), 4, 0, 4, 1),
        Visual("slicer", "Mercado", ("dim_geo[market]",), 8, 0, 4, 1),
        Visual("card", "Pedidos puntuados", ("[Pedidos puntuados]",), 0, 1, 2, 1),
        Visual("card", "Alertas", ("[Alertas]",), 2, 1, 2, 1),
        Visual("card", "Alertado (%)", ("[Alertado (%)]",), 4, 1, 2, 1),
        Visual("card", "Retrasos esperados", ("[Retrasos esperados]",), 6, 1, 2, 1),
        Visual("card", "Umbral", ("[Umbral]",), 8, 1, 2, 1),
        Visual("card", "Alertas abiertas a la fecha", ("[Alertas abiertas a la fecha]",), 10, 1, 2, 1),
        Visual("card", "Costos supuestos", ("[Costos supuestos]",), 0, 2, 12, 1),
        Visual("table", "Alertas abiertas a la fecha, ordenadas por riesgo", _OPEN_ALERTS, 0, 3, 7, 6),
        Visual("barChart", "Alertas por modo de envío", ("dim_shipping_mode[mode_es]", "[Alertas]"), 7, 3, 5, 3),
        Visual("matrix", "Alertado (%) por mercado y modo", ("dim_geo[market]", "dim_shipping_mode[mode_es]", "[Alertado (%)]"), 7, 6, 5, 3),
    )),
    Page("Dónde se traban", (
        Visual("matrix", "Tasa de retraso observada por modo de envío y mercado", ("dim_shipping_mode[mode_es]", "dim_geo[market]", "[Tasa de retraso observada]"), 0, 0, 6, 3),
        Visual("matrix", "Tasa de retraso observada por modo de envío y hora del pedido", ("dim_shipping_mode[mode_es]", "fact_order[order_hour]", "[Tasa de retraso observada]"), 6, 0, 6, 3),
        Visual("barChart", "Tasa de retraso observada por región", ("dim_geo[order_region]", "[Tasa de retraso observada]"), 0, 3, 6, 3),
        Visual("barChart", "Tasa de retraso observada por departamento", ("dim_product[department_name]", "[Tasa de retraso observada]"), 6, 3, 6, 3),
        Visual("clusteredColumnChart", "Días reales y programados por modo de envío", ("dim_shipping_mode[mode_es]", "[Días reales medios]", "[Días programados]"), 0, 6, 12, 3),
    )),
    Page("Seguimiento", (
        Visual("lineChart", "Precisión y recall mensuales de las alertas cerradas", ("dim_month[year_month_label]", "[Precisión mensual]", "[Recall mensual]"), 0, 0, 6, 4),
        Visual("clusteredColumnChart", "Retrasos esperados y observados por mes", (
            "dim_month[year_month_label]", "[Retrasos esperados en el mes]", "[Retrasos observados en el mes]",
        ), 6, 0, 6, 4),
        Visual("card", "Precisión de las alertas", ("[Precisión de las alertas]",), 0, 4, 3, 1),
        Visual("card", "Recall de las alertas", ("[Recall de las alertas]",), 3, 4, 3, 1),
        Visual("card", "Beneficio neto estimado (con costos supuestos)", ("[Beneficio neto estimado (u.)]",), 6, 4, 3, 1),
        Visual("card", "Riesgo medio", ("[Riesgo medio]",), 9, 4, 3, 1),
        Visual("lineChart", "Alertado (%) por mes", ("dim_month[year_month_label]", "[Alertado mensual (%)]"), 0, 5, 12, 2),
        Visual("textbox", "Desde 2017-10 los datos cambian de forma (un pedido por cliente y una línea por pedido): los meses posteriores se leen aparte.", (), 0, 7, 12, 2),
    )),
    Page("Validación del modelo", (
        Visual("table", "Criterios C01 a C10", (
            "fact_evaluation_summary[criterion_id]", "fact_evaluation_summary[name_es]", "fact_evaluation_summary[threshold_es]", "fact_evaluation_summary[seen_by_research]",
            "fact_evaluation_summary[seen_by_planner]", "fact_evaluation_summary[verdict_es]",
        ), 0, 0, 8, 3),
        Visual("card", "Veredicto", ("[Veredicto]",), 8, 0, 4, 3),
        Visual("lineChart", "Curva ROC del modelo, B2 y B5 en la prueba", ("fact_roc[fpr]", "fact_roc[tpr]", "fact_roc[method]"), 0, 3, 4, 3),
        Visual("lineChart", "Calibración", ("fact_calibration[p_mean]", "fact_calibration[observed]", "fact_calibration[method]"), 4, 3, 4, 3),
        Visual("lineChart", "Curva de costo: beneficio por 1.000 pedidos según el punto de equilibrio", (
            "fact_cost_sensitivity[break_even]", "fact_cost_sensitivity[nb_per_1000]", "fact_cost_sensitivity[method]",
        ), 8, 3, 4, 3),
        Visual("barChart", "AUC en validación con cada columna que delata el resultado: por qué quedan fuera", (
            "fact_leakage[column_es]", "fact_leakage[auc_with_mode_valid]",
        ), 0, 6, 6, 3),
        Visual("barChart", "Importancia por permutación", ("fact_importance[feature_es]", "fact_importance[auc_drop]"), 6, 6, 6, 3),
    )),
    Page("Calidad y corridas", (
        Visual("card", "Última fecha de cálculo", ("[Última fecha de cálculo]",), 0, 0, 3, 1),
        Visual("card", "Estado de la última corrida", ("[Estado de la última corrida]",), 3, 0, 3, 1),
        Visual("card", "Reglas bloqueantes fallidas", ("[Reglas bloqueantes fallidas]",), 6, 0, 2, 1),
        Visual("card", "Pedidos", ("[Pedidos]",), 8, 0, 2, 1),
        Visual("card", "Pedidos cancelados", ("[Pedidos cancelados]",), 10, 0, 2, 1),
        Visual("table", "Reglas de validación", (
            "fact_validation[rule_id]", "fact_validation[name_es]", "fact_validation[severity]", "fact_validation[rows_affected]", "fact_validation[orders_affected]",
            "fact_validation[status]",
        ), 0, 1, 7, 4),
        Visual("slicer", "Corrida", ("dim_pipeline_run[run_key]",), 7, 1, 5, 1),
        Visual("textbox", "Una regla bloqueante que falla rechaza la entrega: nada se carga. Las advertencias se cuentan y siguen; las informativas solo se cuentan.", (), 7, 2, 5, 3),
        Visual("table", "Corridas", (
            "dim_pipeline_run[run_key]", "dim_pipeline_run[started_at]", "dim_pipeline_run[status]", "dim_pipeline_run[delivery_id]", "dim_pipeline_run[as_of_date]",
            "dim_pipeline_run[orders_delivery]", "dim_pipeline_run[orders_scored]", "dim_pipeline_run[alerts]", "dim_pipeline_run[peak_working_set_mb]",
        ), 0, 5, 12, 4),
    )),
)

MODEL = SemanticModel(TABLES, RELATIONSHIPS, MEASURES, PAGES)


def arrow_schema(table: Table) -> pa.Schema:
    """The Arrow schema of a table: every column is nullable except the key (a dimension's key, a fact's grain)."""
    return pa.schema([pa.field(c.name, ARROW_TYPES[c.dtype], nullable=c.name not in table.key) for c in table.columns])


_REF = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\[([A-Za-z_][A-Za-z0-9_]*)\]")
_MEASURE_REF = re.compile(r"(?<![A-Za-z0-9_\]])\[([^\[\]]+)\]")


def dax_column_refs(dax: str) -> set[tuple[str, str]]:
    """The ``table[column]`` references of a DAX expression."""
    return set(_REF.findall(dax))


def dax_measure_refs(dax: str) -> set[str]:
    """The names of the measures a DAX expression names as ``[Measure]`` (a bracket not attached to a table)."""
    return set(_MEASURE_REF.findall(dax))


def validate_model(model: SemanticModel) -> list[str]:
    """Every way the model breaks its own rules (empty when it keeps them): tables, columns, keys, relationships and their types."""
    problems: list[str] = []
    names = [t.name for t in model.tables]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"the table {name} appears more than once")
    for table in model.tables:
        columns = table.column_names
        for column in sorted({c for c in columns if columns.count(c) > 1}):
            problems.append(f"{table.name}[{column}] appears more than once")
        for column in table.columns:
            if column.dtype not in ARROW_TYPES:
                problems.append(f"{table.name}[{column.name}] has the unknown type {column.dtype}")
        missing = [k for k in table.key if k not in columns]
        if missing or not table.key:
            problems.append(f"{table.name}: the key {list(table.key)} is not made of its columns")
        if table.kind not in ("dimension", "fact"):
            problems.append(f"{table.name} is neither a dimension nor a fact")
    by_name = {t.name: t for t in model.tables}
    seen_ids: set[str] = set()
    for r in model.relationships:
        label = f"{r.rel_id} {r.dim_table}[{r.dim_column}] -> {r.fact_table}[{r.fact_column}]"
        if r.rel_id in seen_ids:
            problems.append(f"{r.rel_id} appears more than once")
        seen_ids.add(r.rel_id)
        dim, fact = by_name.get(r.dim_table), by_name.get(r.fact_table)
        if dim is None or fact is None:
            problems.append(f"{label}: the table does not exist")
            continue
        if dim.kind != "dimension" or fact.kind != "fact":
            problems.append(f"{label}: the one side must be a dimension and the many side a fact")
        if r.dim_column not in dim.column_names or r.fact_column not in fact.column_names:
            problems.append(f"{label}: the column does not exist")
            continue
        if dim.key != (r.dim_column,):
            problems.append(f"{label}: the one side must be the unique key of {dim.name}")
        if dim.column(r.dim_column).dtype != fact.column(r.fact_column).dtype:
            problems.append(f"{label}: the two columns have different types")
        if (r.cardinality, r.cross_filter, r.active) != ("one_to_many", "single", True):
            problems.append(f"{label}: must be one to many, with a single filter direction, and active")
    problems.extend(_measure_problems(model, by_name))
    problems.extend(_page_problems(model, by_name))
    return problems


def _field_problem(model: SemanticModel, field: str) -> str | None:
    """Why a field of a visual does not resolve (``table[column]`` or ``[Measure]``), or None."""
    if field.startswith("["):
        name = field[1:-1] if field.endswith("]") else field
        return None if any(m.name_es == name for m in model.measures) else f"unknown measure {field}"
    match = _REF.fullmatch(field)
    if match is None:
        return f"malformed field {field!r}"
    table, column = match.groups()
    return None if any(t.name == table and column in t.column_names for t in model.tables) else f"unknown column {field}"


def _measure_problems(model: SemanticModel, tables: dict[str, Table]) -> list[str]:
    problems: list[str] = []
    names = [m.name_es for m in model.measures]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(f"the measure [{name}] appears more than once")
    for m in model.measures:
        if m.dax.count("(") != m.dax.count(")") or m.dax.count('"') % 2:
            problems.append(f"[{m.name_es}]: unbalanced parentheses or quotes in the DAX")
        for table, column in sorted(dax_column_refs(m.dax)):
            if table not in tables or column not in tables[table].column_names:
                problems.append(f"[{m.name_es}]: {table}[{column}] does not exist")
        for ref in sorted(dax_measure_refs(m.dax) - {m.name_es}):
            if ref not in names:
                problems.append(f"[{m.name_es}]: the measure [{ref}] does not exist")
        if not m.description_es or not m.format_string:
            problems.append(f"[{m.name_es}]: needs a format and a description")
    return problems


def _page_problems(model: SemanticModel, tables: dict[str, Table]) -> list[str]:
    problems: list[str] = []
    for page in model.pages:
        taken: dict[tuple[int, int], str] = {}
        for v in page.visuals:
            label = f"{page.name_es}: {v.title_es}"
            for field in v.fields:
                if (why := _field_problem(model, field)) is not None:
                    problems.append(f"{label}: {why}")
            if v.w < 1 or v.h < 1 or v.x < 0 or v.y < 0 or v.x + v.w > PAGE_COLUMNS or v.y + v.h > PAGE_ROWS:
                problems.append(f"{label}: outside the {PAGE_COLUMNS} x {PAGE_ROWS} grid")
                continue
            cells = [(cx, cy) for cx in range(v.x, v.x + v.w) for cy in range(v.y, v.y + v.h)]
            clash = next((taken[c] for c in cells if c in taken), None)
            if clash is not None:
                problems.append(f"{label}: overlaps {clash}")
            taken.update({c: v.title_es for c in cells})
    return problems
