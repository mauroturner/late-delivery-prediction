"""Source ingestion: safe reading, data minimisation and the canonical streams (RF03 to RF05, V03 to V09).

The CSV is untrusted input and holds personal data. ``read_source`` reads it line by line, in bytes, so that every refusal can say which line it is:

- V03: the encoding is read strictly (cp1252), with no byte-order mark, no NUL byte and no line ending other than CRLF or LF;
- V04: the header has the 53 columns, with their exact names and in their order;
- V05: every row has 53 fields and every number and date converts (nothing is tolerated: a short row is a defect, not a missing value);
- V06: the email and the password are masked in every row;
- V07: ``Order Item Id`` is unique and each order has one value in each of the 20 columns that are constant inside an order;
- V08: in the orders that are not cancelled, ``Late_delivery_risk`` is "real days above scheduled days";
- V09: an outcome is known at most ``purge_days - 1`` days after the order, so that the purge of the split is enough.

The first rule that fails, in that order, is raised as a ``SourceError`` that lists the problems. A refusal never carries the value of a personal column
(decision D39), and reading never writes anything. The 15 personal columns exist only inside the loop that reads a row: what leaves it is the orders,
the lines and the outcomes (``SourceFrames``), built from the allowed columns, and a ``SourceReport`` with the counts that the later rules (V10 to V18)
and the reports quote. ``write_interim`` writes the three streams as Parquet, atomically, with the fingerprint of the source.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from latedel.config import PipelineConfig
from latedel.files import atomic_path
from latedel.schema import (
    ATTRIBUTION,
    CANCELLED_STATUS,
    DUPLICATE_OR_CONSTANT_COLUMNS,
    IDENTICAL_PAIRS,
    LEAKING_COLUMNS,
    MASKED,
    ORDER_LEVEL_COLUMNS,
    ORDER_LINES_SCHEMA,
    ORDERS_SCHEMA,
    OUTCOMES_SCHEMA,
    PERSONAL_COLUMNS,
    SCHEDULED_DAYS_BY_MODE,
    SOURCE_COLUMNS,
    SourceError,
)

logger = logging.getLogger(__name__)

MAX_LINE_BYTES = 1 << 20          # no physical line of the source comes near this; a longer one is a defect (V05)
MAX_PROBLEM_ROWS = 10_000         # after this many bad rows the scan stops: the file is refused anyway
EXAMPLES = 5                      # examples kept for each kind of problem
MONEY_TOLERANCE = 0.015           # V13: amounts are rounded to the cent in the source; the largest gap measured on it is 0.010014 (decision D39)
FINGERPRINT_KEY = b"latedel.source_sha256"
ATTRIBUTION_KEY = b"latedel.attribution"
SOURCE_REPORT_KEY = b"latedel.source_report"

_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_INT = re.compile(r"-?[0-9]{1,18}", re.ASCII)
_DECIMAL = re.compile(r"-?[0-9]{1,30}(?:\.[0-9]{1,30})?(?:[eE][-+]?[0-9]{1,3})?", re.ASCII)
_STAMP = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4}) ([0-9]{1,2}):([0-9]{2})", re.ASCII)

INT_COLUMNS = (
    "Days for shipping (real)", "Days for shipment (scheduled)", "Late_delivery_risk", "Category Id", "Customer Id", "Department Id", "Order Customer Id",
    "Order Id", "Order Item Cardprod Id", "Order Item Id", "Order Item Quantity", "Product Card Id", "Product Category Id", "Product Status",
)
DECIMAL_COLUMNS = (
    "Benefit per order", "Sales per customer", "Latitude", "Longitude", "Order Item Discount", "Order Item Discount Rate", "Order Item Product Price",
    "Order Item Profit Ratio", "Sales", "Order Item Total", "Order Profit Per Order", "Product Price",
)
DATE_COLUMNS = ("order date (DateOrders)", "shipping date (DateOrders)")

_COLUMN_INDEX = {name: i for i, name in enumerate(SOURCE_COLUMNS)}
_ORDER_LEVEL_INDEX = tuple(_COLUMN_INDEX[name] for name in ORDER_LEVEL_COLUMNS)
_ORDER_LEVEL_POSITION = {name: i for i, name in enumerate(ORDER_LEVEL_COLUMNS)}
_CHECKS = tuple(
    [(_COLUMN_INDEX[name], name, "int") for name in INT_COLUMNS]
    + [(_COLUMN_INDEX[name], name, "decimal") for name in DECIMAL_COLUMNS]
    + [(_COLUMN_INDEX[name], name, "date") for name in DATE_COLUMNS]
)
_PERSONAL = frozenset(PERSONAL_COLUMNS)
_PAIR_INDEX = tuple((_COLUMN_INDEX[a], _COLUMN_INDEX[b]) for a, b in IDENTICAL_PAIRS)

# the kept columns of a line, in the order in which they are collected
_LINE_SOURCE = (
    "Order Item Id", "Order Id", "Department Name", "Category Name", "Order Item Cardprod Id", "Product Name", "Order Item Quantity", "Product Price",
    "Sales", "Order Item Discount", "Order Item Discount Rate", "Order Item Total", "Benefit per order", "Order Item Profit Ratio",
)
_LINE_INDEX = tuple(_COLUMN_INDEX[name] for name in _LINE_SOURCE)


@dataclass(frozen=True)
class SourceFrames:
    """The three streams read from the source: what is known when an order is created (orders and lines) and what is known afterwards (outcomes)."""

    orders: pd.DataFrame
    order_lines: pd.DataFrame
    outcomes: pd.DataFrame


@dataclass(frozen=True)
class SourceReport:
    """What the canonical read found; the validation (V10 to V18, I05) and the reports quote it. Every count is of lines or orders, never of people."""

    size_bytes: int
    lines: int
    orders: int
    cancelled_orders: int
    cancelled_lines: int
    task_orders: int
    first_order_date: date
    last_order_date: date
    max_outcome_lag_days: int
    personal_columns_dropped: int
    leaking_columns_moved: int
    constant_or_duplicate_columns: int
    counts: dict[str, dict[str, int]]


# --- reading -----------------------------------------------------------------------------------------------------


def _clean(text: str, limit: int = 60) -> str:
    """A name taken from the untrusted header, safe to show: printable ASCII only and short."""
    shown = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)
    return shown if len(shown) <= limit else shown[:limit] + "..."


def _valid_stamp(value: str) -> bool:
    match = _STAMP.fullmatch(value)
    if match is None:
        return False
    month, day, year, hour, minute = (int(g) for g in match.groups())
    try:
        datetime(year, month, day, hour, minute)
    except ValueError:
        return False
    return True


def _valid_decimal(value: str) -> bool:
    if _DECIMAL.fullmatch(value) is None:
        return False
    return "e" not in value and "E" not in value or math.isfinite(float(value))


def _physical_lines(raw, encoding: str):
    """Yield the decoded physical lines of a binary file; refuse (V03, V05) a NUL byte, a lone CR, an undecodable byte or an enormous line."""
    number = 0
    while True:
        chunk = raw.readline(MAX_LINE_BYTES + 1)
        if not chunk:
            return
        number += 1
        if len(chunk) > MAX_LINE_BYTES:
            raise SourceError("V05", f"la línea {number} tiene más de {MAX_LINE_BYTES} bytes; no es una fila de la fuente.",
                              [{"rule_id": "V05", "kind": "line_too_long", "line": number, "count": 1, "examples": [{"line": number}]}])
        if b"\x00" in chunk:
            raise SourceError("V03", f"el archivo contiene un byte nulo en la línea {number}.",
                              [{"rule_id": "V03", "kind": "nul_byte", "line": number}])
        body = chunk[:-2] if chunk.endswith(b"\r\n") else chunk[:-1] if chunk.endswith(b"\n") else chunk
        if b"\r" in body:
            raise SourceError("V03", f"la línea {number} tiene un retorno de carro (CR) que no es parte de un CRLF; solo se admiten CRLF y LF.",
                              [{"rule_id": "V03", "kind": "lone_cr", "line": number}])
        try:
            yield chunk.decode(encoding, "strict")
        except UnicodeDecodeError as error:
            byte = f"0x{chunk[error.start]:02x}"
            raise SourceError(
                "V03", f"el archivo no es {encoding} estricto: el byte {byte} de la línea {number} (posición {error.start + 1} de la línea) no es válido.",
                [{"rule_id": "V03", "kind": "invalid_byte", "line": number, "byte": byte, "position": error.start + 1}],
            ) from None


class _Problems:
    """Problems of one rule grouped by kind, each with its count and a few examples; no example ever carries the value of a personal column."""

    def __init__(self) -> None:
        self.items: dict[tuple, dict[str, Any]] = {}

    def add(self, key: tuple, line: int, example: dict[str, Any] | None = None, **base: Any) -> None:
        item = self.items.get(key)
        if item is None:
            item = self.items[key] = {**base, "count": 0, "examples": []}
        item["count"] += 1
        if len(item["examples"]) < EXAMPLES:
            item["examples"].append({"line": line, **(example or {})})

    def __bool__(self) -> bool:
        return bool(self.items)

    def listed(self, rule_id: str) -> list[dict[str, Any]]:
        return [{"rule_id": rule_id, **item} for item in self.items.values()]


def _check_header(header: list[str] | None) -> None:
    expected = list(SOURCE_COLUMNS)
    if header is None:
        raise SourceError("V04", "el archivo está vacío: no tiene encabezado.", [{"rule_id": "V04", "kind": "empty"}])
    if header == expected:
        return
    missing = [name for name in expected if name not in header]
    unexpected = [name for name in header if name not in set(expected)]
    shown_missing, shown_unexpected = [_clean(n) for n in missing[:10]], [_clean(n) for n in unexpected[:10]]
    detail = f"el encabezado tiene {len(header)} columnas y se esperaban {len(expected)}"
    if missing or unexpected:
        if missing:
            detail += f"; faltan {shown_missing}"
        if unexpected:
            detail += f"; sobran {shown_unexpected}"
    elif len(header) == len(expected) and sorted(header) == sorted(expected):
        detail += "; están todas, pero en otro orden"
    else:
        detail += "; hay columnas repetidas"
    raise SourceError("V04", detail + ".", [{"rule_id": "V04", "kind": "header", "missing": shown_missing, "unexpected": shown_unexpected,
                                              "columns": len(header)}])


def _v05_message(problems: _Problems) -> str:
    parts = []
    for key, item in problems.items.items():
        first = item["examples"][0]
        where = f"en la línea {first['line']}"
        if key[0] == "field_count":
            parts.append(f"{item['count']} fila(s) tienen {key[1]} campos y se esperaban {len(SOURCE_COLUMNS)} (por ejemplo, {where})")
        elif key[0] == "column":
            if key[1] in _PERSONAL:
                parts.append(f"{item['count']} valor(es) de «{key[1]}» no convierten ({where}; el valor de una columna personal no se muestra)")
            else:
                parts.append(f"{item['count']} valor(es) de «{key[1]}» no convierten (por ejemplo, {_clean(first['value'])!r} {where})")
        else:
            parts.append(f"{item['count']} fila(s) no se pueden leer: {item['message']} (por ejemplo, {where})")
    return "; ".join(parts) + "."


def _csv_problem(error: csv.Error) -> tuple[str, str]:
    text = str(error)
    if "field larger than field limit" in text:
        return "field_limit", f"un campo supera el límite de {csv.field_size_limit()} caracteres"
    if "expected after" in text or "unexpected end of data" in text or "new-line character seen" in text:
        return "quotes", "comillas mal puestas"
    return "csv", "el formato de la fila no es válido"


def read_source(path: Path, cfg: PipelineConfig) -> tuple[SourceFrames, SourceReport]:
    """Read and check the source CSV and return its three streams without the personal columns.

    Raises ``SourceError`` for V02 (size), V03 to V09 (see the module docstring), ``FileNotFoundError`` if the file is not there.
    Nothing is written. The caller checks the SHA-256 (V01) before it trusts the file; this function checks the content.
    """
    path = Path(path)
    size = path.stat().st_size
    source = cfg.source
    if size > source.max_bytes:
        raise SourceError("V02", f"el archivo pesa {size} bytes, por encima del límite de {source.max_bytes} bytes.",
                          [{"rule_id": "V02", "kind": "size", "size_bytes": size, "max_bytes": source.max_bytes}])

    v05, v06, v07 = _Problems(), _Problems(), _Problems()
    first: dict[int, tuple[str, ...]] = {}
    item_ids: set[int] = set()
    kept: list[list[str]] = [[] for _ in _LINE_INDEX]
    order_of_line: list[int] = []
    differing: dict[int, set[str]] = {}
    pair_discrepancies = 0
    rows = 0
    bad_rows = 0
    i_order, i_item = _COLUMN_INDEX["Order Id"], _COLUMN_INDEX["Order Item Id"]
    i_email, i_password = _COLUMN_INDEX["Customer Email"], _COLUMN_INDEX["Customer Password"]
    n_fields = len(SOURCE_COLUMNS)

    with path.open("rb") as raw:
        if raw.read(4).startswith(_BOMS):
            raise SourceError("V03", "el archivo empieza con una marca de orden de bytes (BOM); la fuente no la tiene.",
                              [{"rule_id": "V03", "kind": "bom"}])
        raw.seek(0)
        reader = csv.reader(_physical_lines(raw, source.encoding), strict=True)
        try:
            header = next(reader, None)
        except csv.Error:
            raise SourceError("V04", "el encabezado no se puede leer como CSV.", [{"rule_id": "V04", "kind": "unreadable"}]) from None
        _check_header(header)

        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error as error:
                kind, message = _csv_problem(error)
                v05.add(("csv", kind), reader.line_num, kind=kind, message=message)
                bad_rows += 1
                if bad_rows >= MAX_PROBLEM_ROWS:
                    break
                continue
            rows += 1
            line = reader.line_num
            if len(row) != n_fields:
                v05.add(("field_count", len(row)), line, kind="field_count", fields=len(row))
                bad_rows += 1
                if bad_rows >= MAX_PROBLEM_ROWS:
                    break
                continue

            clean = True
            for index, name, kind in _CHECKS:
                value = row[index]
                if kind == "int":
                    ok = _INT.fullmatch(value) is not None
                elif kind == "decimal":
                    ok = _valid_decimal(value)
                else:
                    ok = _valid_stamp(value)
                if not ok:
                    clean = False
                    v05.add(("column", name), line, {} if name in _PERSONAL else {"value": value}, kind="column", column=name)
            if not clean:
                bad_rows += 1
                if bad_rows >= MAX_PROBLEM_ROWS:
                    break
                continue

            if row[i_email] != MASKED:
                v06.add(("V06", "Customer Email"), line, kind="unmasked", column="Customer Email")
            if row[i_password] != MASKED:
                v06.add(("V06", "Customer Password"), line, kind="unmasked", column="Customer Password")

            order_id = int(row[i_order])
            values = tuple(row[i] for i in _ORDER_LEVEL_INDEX)
            known = first.get(order_id)
            if known is None:
                first[order_id] = values
            elif known != values:
                differing.setdefault(order_id, set()).update(ORDER_LEVEL_COLUMNS[k] for k in range(len(values)) if known[k] != values[k])
            item_id = int(row[i_item])
            if item_id in item_ids:
                v07.add(("duplicate_item_id",), line, {"line_id": item_id}, kind="duplicate_item_id", column="Order Item Id")
            else:
                item_ids.add(item_id)
            for a, b in _PAIR_INDEX:
                if row[a] != row[b] and float(row[a]) != float(row[b]):
                    pair_discrepancies += 1
            if not (v05 or v06 or v07 or differing):
                for store, index in zip(kept, _LINE_INDEX, strict=True):
                    store.append(row[index])
                order_of_line.append(order_id)

    if rows == 0 and not v05:
        raise SourceError("V05", "el archivo tiene el encabezado pero no tiene filas de datos.", [{"rule_id": "V05", "kind": "no_rows", "count": 0}])
    if v05:
        suffix = f" Se dejó de revisar tras {MAX_PROBLEM_ROWS} filas con problemas." if bad_rows >= MAX_PROBLEM_ROWS else ""
        raise SourceError("V05", _v05_message(v05) + suffix, v05.listed("V05"))
    if v06:
        parts = [f"«{item['column']}» no está enmascarada en {item['count']} fila(s) (por ejemplo, en la línea {item['examples'][0]['line']})"
                 for item in v06.items.values()]
        raise SourceError("V06", "; ".join(parts) + "; no se procesa una fuente con datos personales sin enmascarar.", v06.listed("V06"))
    if v07 or differing:
        raise _v07_error(v07, differing)

    frames, report = _build(cfg, size, first, kept, order_of_line, pair_discrepancies)
    logger.info(
        "read source: %d lines in %d orders (%d cancelled), %d bytes; %d personal columns dropped",
        report.lines, report.orders, report.cancelled_orders, report.size_bytes, report.personal_columns_dropped,
    )
    return frames, report


def _v07_error(v07: _Problems, differing: dict[int, set[str]]) -> SourceError:
    problems: list[dict[str, Any]] = []
    parts: list[str] = []
    if differing:
        columns = sorted({c for names in differing.values() for c in names})
        ordered = sorted(differing)[:EXAMPLES]
        problems.append({
            "rule_id": "V07", "kind": "order_level_column", "orders": len(differing), "count": len(differing), "columns": columns,
            "examples": [{"order_id": o, "columns": sorted(differing[o])} for o in ordered],
        })
        first_order = ordered[0]
        parts.append(f"{len(differing)} pedido(s) tienen más de un valor dentro del pedido en {columns} (por ejemplo, la columna «{sorted(differing[first_order])[0]}» "
                     f"en el pedido {first_order})")
    for item in v07.items.values():
        problems.append({"rule_id": "V07", **item})
        parts.append(f"«Order Item Id» tiene {item['count']} valor(es) repetido(s) (por ejemplo, el {item['examples'][0]['line_id']} en la línea {item['examples'][0]['line']})")
    return SourceError("V07", "; ".join(parts) + ".", problems)


# --- the canonical streams ---------------------------------------------------------------------------------------------


def _stamps(values: list[str] | pd.Series) -> pd.Series:
    return pd.to_datetime(pd.Series(values), format="%m/%d/%Y %H:%M")


def _build(cfg: PipelineConfig, size: int, first: dict[int, tuple[str, ...]], kept: list[list[str]], order_of_line: list[int],
           pair_discrepancies: int) -> tuple[SourceFrames, SourceReport]:
    position = _ORDER_LEVEL_POSITION
    ids = np.fromiter(first.keys(), dtype=np.int64, count=len(first))
    values = list(first.values())

    def col(name: str) -> list[str]:
        k = position[name]
        return [v[k] for v in values]

    ordered = _stamps(col("order date (DateOrders)"))
    shipped = _stamps(col("shipping date (DateOrders)"))
    order_date = ordered.dt.normalize()
    real_days = np.array(col("Days for shipping (real)"), dtype=np.int64)
    scheduled = np.array(col("Days for shipment (scheduled)"), dtype=np.int64)
    label = np.array(col("Late_delivery_risk"), dtype=np.int64)
    status = pd.Series(col("Delivery Status"), dtype=object)
    cancelled = (status == CANCELLED_STATUS).to_numpy()
    known = np.maximum(order_date + pd.to_timedelta(real_days, unit="D"), shipped.dt.normalize())

    post_break = pd.Timestamp(cfg.split.post_break_start)
    orders = pd.DataFrame({
        "order_id": ids,
        "order_ts": ordered.dt.strftime("%Y-%m-%dT%H:%M"),
        "order_date": order_date,
        "regime": np.where(order_date >= post_break, "post_break", "pre_break"),
        "shipping_mode": col("Shipping Mode"),
        "scheduled_days": scheduled,
        "payment_type": col("Type"),
        "market": col("Market"),
        "order_region": col("Order Region"),
        "order_country": col("Order Country"),
        "customer_segment": col("Customer Segment"),
        "customer_country": col("Customer Country"),
    })

    lines = pd.DataFrame({
        "line_id": np.array(kept[0], dtype=np.int64), "order_id": np.array(order_of_line, dtype=np.int64),
        "department_name": kept[2], "category_name": kept[3], "product_card_id": np.array(kept[4], dtype=np.int64), "product_name": kept[5],
        "quantity": np.array(kept[6], dtype=np.int64), "product_price": np.array(kept[7], dtype=np.float64), "sales": np.array(kept[8], dtype=np.float64),
        "discount": np.array(kept[9], dtype=np.float64), "discount_rate": np.array(kept[10], dtype=np.float64),
        "item_total": np.array(kept[11], dtype=np.float64),
    })
    benefit = pd.DataFrame({"order_id": lines["order_id"], "benefit": np.array(kept[12], dtype=np.float64), "ratio": np.array(kept[13], dtype=np.float64)})
    trimmed = lines["product_name"].str.rstrip()
    trailing_lines = int((trimmed != lines["product_name"]).sum())
    lines["product_name"] = trimmed
    lines = lines.sort_values(["order_id", "line_id"], kind="stable").reset_index(drop=True)
    sums = benefit.groupby("order_id", sort=True).agg(benefit_total=("benefit", "sum"), profit_ratio_mean=("ratio", "mean"))

    outcomes = pd.DataFrame({
        "order_id": ids,
        "outcome_known_date": known,
        "delivery_status": status,
        "order_status": col("Order Status"),
        "real_days": real_days,
        "shipping_ts": shipped.dt.strftime("%Y-%m-%dT%H:%M"),
        "source_label": label,
        "is_cancelled": cancelled,
    })
    outcomes["benefit_total"] = sums["benefit_total"].reindex(outcomes["order_id"]).to_numpy()
    outcomes["profit_ratio_mean"] = sums["profit_ratio_mean"].reindex(outcomes["order_id"]).to_numpy()
    orders = orders.sort_values("order_id", kind="stable").reset_index(drop=True)
    outcomes = outcomes.sort_values("order_id", kind="stable").reset_index(drop=True)

    # V08: the label is "real days above scheduled days" in the orders that are not cancelled
    merged = outcomes.merge(orders[["order_id", "scheduled_days", "order_date"]], on="order_id", how="left")
    task = merged[~merged["is_cancelled"]]
    wrong = task[task["source_label"] != (task["real_days"] > task["scheduled_days"]).astype(np.int64)]
    if len(wrong):
        shown = wrong.head(EXAMPLES)
        examples = [{"order_id": int(r.order_id), "label": int(r.source_label), "real_days": int(r.real_days), "scheduled_days": int(r.scheduled_days)}
                    for r in shown.itertuples()]
        raise SourceError(
            "V08", f"{len(wrong)} pedido(s) no cancelados tienen una etiqueta que no es «días reales mayores que los programados» "
                   f"(por ejemplo, el pedido {examples[0]['order_id']}).",
            [{"rule_id": "V08", "kind": "label", "count": len(wrong), "examples": examples}],
        )

    # V09: an outcome is known within purge_days - 1 days of the order
    max_allowed = cfg.split.purge_days - 1
    lag = (merged["outcome_known_date"] - merged["order_date"]).dt.days
    late_known = merged[lag > max_allowed]
    if len(late_known):
        examples = [{"order_id": int(o), "lag_days": int(g)} for o, g in zip(late_known["order_id"].head(EXAMPLES), lag[lag > max_allowed].head(EXAMPLES), strict=True)]
        raise SourceError(
            "V09", f"{len(late_known)} pedido(s) se conocen más de {max_allowed} días después de crearse (por ejemplo, el pedido {examples[0]['order_id']} "
                   f"a los {examples[0]['lag_days']} días); la purga de {cfg.split.purge_days} días del corte no alcanzaría.",
            [{"rule_id": "V09", "kind": "lag", "count": len(late_known), "max_allowed_days": max_allowed, "examples": examples}],
        )

    n_cancelled = int(outcomes["is_cancelled"].sum())
    cancelled_ids = set(outcomes.loc[outcomes["is_cancelled"], "order_id"])
    cancelled_lines = int(lines["order_id"].isin(cancelled_ids).sum())
    sales_gap = (lines["product_price"] * lines["quantity"] - lines["sales"]).abs() > MONEY_TOLERANCE
    total_gap = (lines["sales"] - lines["discount"] - lines["item_total"]).abs() > MONEY_TOLERANCE
    off_schedule = int((orders["shipping_mode"].map(SCHEDULED_DAYS_BY_MODE) != orders["scheduled_days"]).sum())
    counts = {
        "rows": {"lines": len(lines), "orders": len(orders), "cancelled_orders": n_cancelled, "cancelled_lines": cancelled_lines,
                 "task_orders": len(orders) - n_cancelled},
        "V10": {"orders_off_schedule": off_schedule},
        "V11": {"orders": n_cancelled, "lines": cancelled_lines},
        "V12": {"pairs": len(IDENTICAL_PAIRS), "discrepancies": pair_discrepancies},
        "V13": {"discrepancies": int((sales_gap | total_gap).sum())},
        "V14": {"lines": trailing_lines},
        "V15": {"columns": len(PERSONAL_COLUMNS)},
        "V16": {"columns": len(DUPLICATE_OR_CONSTANT_COLUMNS)},
        "V17": {"post_break_orders": int((orders["regime"] == "post_break").sum())},
        "V18": {"columns": len(LEAKING_COLUMNS)},
    }
    report = SourceReport(
        size_bytes=size, lines=len(lines), orders=len(orders), cancelled_orders=n_cancelled, cancelled_lines=cancelled_lines,
        task_orders=len(orders) - n_cancelled, first_order_date=orders["order_date"].min().date(), last_order_date=orders["order_date"].max().date(),
        max_outcome_lag_days=int(lag.max()), personal_columns_dropped=len(PERSONAL_COLUMNS), leaking_columns_moved=len(LEAKING_COLUMNS),
        constant_or_duplicate_columns=len(DUPLICATE_OR_CONSTANT_COLUMNS), counts=counts,
    )
    return SourceFrames(orders, lines, outcomes), report


# --- the canonical files --------------------------------------------------------------------------------------------------

INTERIM_FILES = (("orders", ORDERS_SCHEMA), ("order_lines", ORDER_LINES_SCHEMA), ("outcomes", OUTCOMES_SCHEMA))


def _report_json(report: SourceReport) -> bytes:
    document = asdict(report)
    document["first_order_date"] = report.first_order_date.isoformat()
    document["last_order_date"] = report.last_order_date.isoformat()
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_interim(frames: SourceFrames, report: SourceReport, interim_dir: Path, source_sha256: str) -> list[Path]:
    """Write ``orders.parquet``, ``order_lines.parquet`` and ``outcomes.parquet`` to `interim_dir`, each atomically, with the fingerprint of the source.

    The three tables are built (and so checked against their Arrow schemas) before any file is touched: a table that does not convert leaves the folder as it
    was. There is no personal column in any of them, and the metadata holds only the fingerprint, the attribution and the report's counts.
    """
    interim_dir = Path(interim_dir)
    metadata = {FINGERPRINT_KEY: source_sha256.encode("ascii"), ATTRIBUTION_KEY: ATTRIBUTION.encode("utf-8"), SOURCE_REPORT_KEY: _report_json(report)}
    frames_by_name = {"orders": frames.orders, "order_lines": frames.order_lines, "outcomes": frames.outcomes}
    tables = {}
    for name, schema in INTERIM_FILES:
        table = pa.Table.from_pandas(frames_by_name[name], schema=schema, preserve_index=False)
        tables[name] = table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata})
    written = []
    for name, _schema in INTERIM_FILES:
        target = interim_dir / f"{name}.parquet"
        with atomic_path(target) as temporary:
            pq.write_table(tables[name], temporary)
        written.append(target)
    return written

