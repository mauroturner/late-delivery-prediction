"""The semantic model of the Power BI report: the single source of truth for its tables and relationships (D21).

The tables with their columns and the relationships (all one-to-many, single direction, active) live here, and the Arrow schema of every Parquet file, the
data dictionary and the HTML design all come from this module, so a change of a column is made once and the tests that read it (keys, foreign keys, schemas) keep
them honest. The measures and the pages arrive with U18. Names of tables, columns and files are in English; labels and descriptions are in Spanish (D26).

Dimensions have a unique key; facts have a grain (``Table.key``). ``dim_as_of``, ``dim_model`` and the facts of the evaluation have no relationship: they feed their
own visuals and measures (docs/plan.md section 16).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import pyarrow as pa

ARROW_TYPES = {
    "int32": pa.int32(), "int64": pa.int64(), "float64": pa.float64(), "string": pa.string(), "bool": pa.bool_(), "date": pa.date32(),
}
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
class SemanticModel:
    tables: tuple[Table, ...]
    relationships: tuple[Relationship, ...]

    def table(self, name: str) -> Table:
        return next(t for t in self.tables if t.name == name)


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

MODEL = SemanticModel(TABLES, RELATIONSHIPS)


def arrow_schema(table: Table) -> pa.Schema:
    """The Arrow schema of a table: every column is nullable except the key (a dimension's key, a fact's grain)."""
    return pa.schema([pa.field(c.name, ARROW_TYPES[c.dtype], nullable=c.name not in table.key) for c in table.columns])


_REF = re.compile(r"(\w+)\[(\w+)\]")


def dax_column_refs(dax: str) -> set[tuple[str, str]]:
    """The ``table[column]`` references of a DAX expression."""
    return set(_REF.findall(dax))


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
    return problems
