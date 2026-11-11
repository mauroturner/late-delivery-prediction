"""The variables of the model and their guards against leakage (RF11, AC13, decisions D06 and D11).

``build_features`` receives the orders and the lines as they are known when the order is created and returns one row per order with exactly the columns of a
feature set (F0, F1 or F2). Three things keep what is known afterwards, and what is personal, out of the matrix, and each is checked by a test that tries to
get around it:

- the **inputs** are checked against the allowed list, which is the schema of the two streams (``ORDERS_SCHEMA`` and ``ORDER_LINES_SCHEMA``): a column of a
  result, a personal column or any other that is not on the list is a ``LeakageError``, whatever its spelling;
- the **sets** are checked at every call: each of their columns must be an allowed column of the orders or one derived here from allowed columns;
- the **output** is checked against the set (``check_feature_matrix``): exactly its columns, in its order, one row per order.

This module imports nothing of the project but the schema, so it cannot read a warehouse, a file of the source or a delivery; ``read_order_frames`` takes a
connection and reads the two staging tables of the orders and nothing else. A static test reads this file and fails if it names an outcome table.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import pandas as pd

from latedel.schema import LEAKING_COLUMNS, ORDER_LINES_SCHEMA, ORDERS_SCHEMA, PERSONAL_COLUMNS

FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "f0": ("shipping_mode", "scheduled_days"),
}
FEATURE_SETS["f1"] = FEATURE_SETS["f0"] + (
    "payment_type", "market", "order_region", "order_country", "customer_segment", "customer_country", "main_department", "main_category", "main_product",
    "n_lines", "n_departments", "units", "gross_sales", "item_total", "discount", "max_discount_rate",
)
FEATURE_SETS["f2"] = FEATURE_SETS["f1"] + ("order_hour", "order_weekday", "order_day", "order_month")

CATEGORICAL: frozenset[str] = frozenset({
    "shipping_mode", "payment_type", "market", "order_region", "order_country", "customer_segment", "customer_country", "main_department", "main_category",
    "main_product",
})

# the internal names of what is known only after the order is created (the streams of outcomes); the source spellings are LEAKING_COLUMNS
OUTCOME_NAMES: frozenset[str] = frozenset({
    "is_late", "real_days", "shipping_ts", "outcome_known_date", "delivery_status", "order_status", "is_cancelled", "benefit_total", "profit_ratio_mean",
    "source_label",
})

ALLOWED_ORDER_COLUMNS: tuple[str, ...] = tuple(ORDERS_SCHEMA.names)
ALLOWED_LINE_COLUMNS: tuple[str, ...] = tuple(ORDER_LINES_SCHEMA.names)
# the variables that are not a column of the orders: they are built here from allowed columns of the lines or from the timestamp of the order
DERIVED: frozenset[str] = frozenset({
    "main_department", "main_category", "main_product", "n_lines", "n_departments", "units", "gross_sales", "item_total", "discount", "max_discount_rate",
    "order_hour", "order_weekday", "order_day", "order_month",
})
_TIME_FEATURES = {"order_hour": "hour", "order_weekday": "dayofweek", "order_day": "day", "order_month": "month"}
_LINE_FEATURES = DERIVED - set(_TIME_FEATURES)
_MAIN = {"main_department": "department_name", "main_category": "category_name", "main_product": "product_name"}
# the columns of the lines that each variable built from the lines reads
_LINE_INPUTS = {
    "n_lines": ["line_id"], "n_departments": ["department_name"], "units": ["quantity"], "gross_sales": ["sales"], "item_total": ["item_total"], "discount": ["discount"],
    "max_discount_rate": ["discount_rate"],
}
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M"


class LeakageError(RuntimeError):
    """Something that is known only after the order is created, something personal, or something not on the allowed list tried to reach the variables."""


def _norm(name: str) -> str:
    """A name without case, spaces, underscores or any other mark: ``Real_Days``, ``real days`` and ``realdays`` are the same name."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


_RESULT = {_norm(n) for n in (*LEAKING_COLUMNS, *OUTCOME_NAMES)}
_PERSONAL = {_norm(n) for n in PERSONAL_COLUMNS}


def _forbidden(name: str) -> str | None:
    """``"resultado"`` or ``"personal"`` if `name`, in any spelling, is a column of that kind; None otherwise."""
    key = _norm(name)
    if key in _RESULT:
        return "resultado"
    if key in _PERSONAL:
        return "personal"
    return None


def _refusal(name: str, kind: str, where: str) -> LeakageError:
    if kind == "resultado":
        return LeakageError(f"la columna {name!r} de {where} es de resultado (se conoce después de crear el pedido) y no puede entrar en las variables")
    if kind == "personal":
        return LeakageError(f"la columna {name!r} de {where} es personal y no puede entrar en las variables")
    return LeakageError(f"la columna {name!r} de {where} no está en la lista permitida ({', '.join(ORDERS_SCHEMA.names if where == 'los pedidos' else ORDER_LINES_SCHEMA.names)})")


def _check_sets() -> None:
    """Every column of every set is an allowed column of the orders or a variable derived here, and none is a result or a personal column."""
    allowed = set(ALLOWED_ORDER_COLUMNS) | DERIVED
    for set_name, columns in FEATURE_SETS.items():
        if len(set(columns)) != len(columns):
            raise LeakageError(f"el conjunto {set_name} repite una columna")
        for column in columns:
            kind = _forbidden(column)
            if kind is not None:
                raise LeakageError(f"el conjunto {set_name} incluye {column!r}, que es una columna de {kind}")
            if column not in allowed:
                raise LeakageError(f"el conjunto {set_name} incluye {column!r}, que ni es una columna permitida de los pedidos ni se deriva de ellas")


def _check_inputs(frame: Any, allowed: tuple[str, ...], where: str) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{where}: se esperaba un DataFrame y se recibió {type(frame).__name__}")
    names = list(frame.columns)
    if not all(isinstance(name, str) for name in names):
        raise LeakageError(f"los nombres de las columnas de {where} deben ser texto; se recibió {[n for n in names if not isinstance(n, str)][:3]!r}")
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise LeakageError(f"la columna {name!r} de {where} está repetida")
        seen.add(name)
    for name in names:
        if name not in allowed:
            raise _refusal(name, _forbidden(name) or "otra", where)


def check_feature_matrix(matrix: pd.DataFrame, feature_set: str) -> None:
    """The output guard (M02): exactly the columns of the set in its order, none of a result or personal, and one row per order."""
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"conjunto de variables desconocido: {feature_set!r}; se esperaba uno de {', '.join(FEATURE_SETS)}")
    for name in matrix.columns:
        kind = _forbidden(str(name))
        if kind is not None:
            raise LeakageError(f"la matriz tiene la columna {name!r}, que es una columna de {kind}")
    if tuple(matrix.columns) != FEATURE_SETS[feature_set]:
        raise LeakageError(
            f"las columnas de la matriz deben ser exactamente las del conjunto {feature_set} y en su orden: {list(FEATURE_SETS[feature_set])}; "
            f"se recibió {list(matrix.columns)}"
        )
    if not matrix.index.is_unique:
        raise LeakageError("la matriz debe tener una fila por pedido y hay pedidos repetidos")


def _typed(series: pd.Series) -> pd.Series:
    if pd.api.types.is_integer_dtype(series):
        return series.astype("int64")
    if pd.api.types.is_float_dtype(series):
        return series.astype("float64")
    return series


def build_features(orders: pd.DataFrame, lines: pd.DataFrame, feature_set: str) -> pd.DataFrame:
    """The variables of `feature_set` for each order of `orders`, indexed by ``order_id`` in ascending order.

    `orders` and `lines` have only columns of the allowed list (``LeakageError`` otherwise, for a result, a personal or any other column). The variables are the
    order's own columns, the sums, the count and the largest of its lines, the department, category and product of its main line (the most sales, and the
    lowest ``line_id`` on a tie) and, in F2, the hour, weekday, day and month of its creation. The inputs are not modified.
    """
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"conjunto de variables desconocido: {feature_set!r}; se esperaba uno de {', '.join(FEATURE_SETS)}")
    _check_sets()
    _check_inputs(orders, ALLOWED_ORDER_COLUMNS, "los pedidos")
    _check_inputs(lines, ALLOWED_LINE_COLUMNS, "las líneas")
    columns = FEATURE_SETS[feature_set]
    wanted_lines = [c for c in columns if c in _LINE_FEATURES]
    needed_orders = ["order_id", *[c for c in columns if c in ALLOWED_ORDER_COLUMNS]]
    if any(c in _TIME_FEATURES for c in columns):
        needed_orders.append("order_ts")
    needed_lines = ["order_id"]
    for column in wanted_lines:
        needed_lines += _LINE_INPUTS.get(column, [])
        if column in _MAIN:
            needed_lines += [_MAIN[column], "sales", "line_id"]
    missing_orders = [c for c in dict.fromkeys(needed_orders) if c not in orders.columns]
    missing_lines = [c for c in dict.fromkeys(needed_lines) if wanted_lines and c not in lines.columns]
    if missing_orders or missing_lines:
        raise ValueError(f"faltan columnas para el conjunto {feature_set}: pedidos {missing_orders}, líneas {missing_lines}")
    if orders["order_id"].duplicated().any():
        raise ValueError(f"hay pedidos con order_id repetido en los pedidos (por ejemplo, {int(orders['order_id'][orders['order_id'].duplicated()].iloc[0])})")

    base = orders.set_index("order_id").sort_index()
    result = pd.DataFrame(index=base.index)
    for column in columns:
        if column in ALLOWED_ORDER_COLUMNS:
            result[column] = _typed(base[column])

    if wanted_lines:
        stray = ~lines["order_id"].isin(base.index)
        if stray.any():
            raise ValueError(f"hay líneas sin pedido (por ejemplo, la línea {int(lines.loc[stray, 'line_id'].iloc[0])} del pedido {int(lines.loc[stray, 'order_id'].iloc[0])})")
        grouped = lines.groupby("order_id")
        aggregates = {
            "n_lines": grouped["line_id"].size(), "n_departments": grouped["department_name"].nunique(), "units": grouped["quantity"].sum(),
            "gross_sales": grouped["sales"].sum(), "item_total": grouped["item_total"].sum(), "discount": grouped["discount"].sum(),
            "max_discount_rate": grouped["discount_rate"].max(),
        }
        main = lines.sort_values(["order_id", "sales", "line_id"], ascending=[True, False, True], kind="stable").groupby("order_id").head(1).set_index("order_id")
        without = base.index.difference(grouped.size().index)
        if len(without):
            raise ValueError(f"hay pedidos sin líneas (por ejemplo, el pedido {int(without[0])}), y el conjunto {feature_set} las usa")
        for column in columns:
            if column in aggregates:
                result[column] = _typed(aggregates[column].reindex(base.index))
            elif column in _MAIN:
                result[column] = main[_MAIN[column]].reindex(base.index)
    time_columns = [c for c in columns if c in _TIME_FEATURES]
    if time_columns:
        stamps = pd.to_datetime(base["order_ts"], format=_TIMESTAMP_FORMAT)
        for column in time_columns:
            result[column] = getattr(stamps.dt, _TIME_FEATURES[column]).astype("int64")

    result = result[list(columns)]
    result.index.name = "order_id"
    check_feature_matrix(result, feature_set)
    return result


def read_order_frames(conn: sqlite3.Connection, delivery_id: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(orders, lines)`` as the warehouse staged them for its calculation date: the two staging tables of the orders, whole, and nothing else.

    With `delivery_id`, only the orders that came in that delivery and their lines (selected in SQL by ``raw_orders.delivery_id``, which is a column of the orders and
    not of any outcome): the same rows as the whole frames restricted to those orders, so that scoring one delivery does not read everything accumulated (H3).
    """
    order_columns, line_columns = ", ".join(ORDERS_SCHEMA.names), ", ".join(ORDER_LINES_SCHEMA.names)
    if delivery_id is None:
        order_rows = conn.execute(f"SELECT {order_columns} FROM stg_orders ORDER BY order_id").fetchall()
        line_rows = conn.execute(f"SELECT {line_columns} FROM stg_order_lines ORDER BY line_id").fetchall()
    else:
        mine = "SELECT order_id FROM raw_orders WHERE delivery_id = ?"
        order_rows = conn.execute(f"SELECT {order_columns} FROM stg_orders WHERE order_id IN ({mine}) ORDER BY order_id", (delivery_id,)).fetchall()
        line_rows = conn.execute(f"SELECT {line_columns} FROM stg_order_lines WHERE order_id IN ({mine}) ORDER BY line_id", (delivery_id,)).fetchall()
    orders = pd.DataFrame(order_rows, columns=ORDERS_SCHEMA.names)
    lines = pd.DataFrame(line_rows, columns=ORDER_LINES_SCHEMA.names)
    orders["order_date"] = pd.to_datetime(orders["order_date"], format="%Y-%m-%d")
    return orders, lines
