"""Source ingestion: safe reading, data minimisation and the canonical streams (RF03 to RF05, V03 to V09).

The CSV is untrusted input and holds personal data. ``read_source`` reads it line by line, in bytes, so that every refusal can say which line it is:

- V03: the encoding is read strictly (cp1252), with no byte-order mark, no NUL byte and no line ending other than CRLF or LF;
- V04: the header has the 53 columns, with their exact names and in their order;
- V05: every row has 53 fields and every number and date converts, and the day counts are not negative (nothing is tolerated: a short row is a defect, not a
  missing value);
- V06: the email and the password are masked in every row;
- V07: ``Order Item Id`` is unique and each order has one value in each of the 20 columns that are constant inside an order;
- V08: in the orders that are not cancelled, ``Late_delivery_risk`` is "real days above scheduled days";
- V09: an outcome is known at most ``purge_days - 1`` days after the order, so that the purge of the split is enough.

The first rule that fails, in that order, is raised as a ``SourceError`` that lists the problems. A refusal never carries the value of a personal column
(decision D39), and reading never writes anything. The 15 personal columns exist only inside the loop that reads a row: what leaves it is the orders,
the lines and the outcomes (``SourceFrames``), built from the allowed columns, and a ``SourceReport`` with the counts that the later rules (V10 to V18)
and the reports quote. ``write_interim`` writes the three streams as Parquet, atomically, with the fingerprint of the source.

``download_file`` and ``fetch_source`` come after the reader. The download follows redirects itself, one hop at a time, and checks every address
(the first one and each ``Location``) before connecting to it: HTTPS, a host of ``source.allowed_hosts``, the default port, at most
``source.max_redirects`` redirects and at most ``source.max_bytes`` bytes (V02). The bytes go to a staging file of their own while the SHA-256 is computed, and
reach their name only if the hash is the pinned one (V01). ``fetch_source`` is the first task of the pipeline and runs all of it, once per fingerprint.
"""

from __future__ import annotations

import csv
import hashlib
import http.client
import json
import logging
import math
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from latedel.config import PipelineConfig, host_allowed, url_host
from latedel.files import atomic_path, sha256_file, write_atomic
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
_NONNEGATIVE = re.compile(r"[0-9]{1,18}", re.ASCII)
_DECIMAL = re.compile(r"-?[0-9]{1,30}(?:\.[0-9]{1,30})?(?:[eE][-+]?[0-9]{1,3})?", re.ASCII)
_STAMP = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4}) ([0-9]{1,2}):([0-9]{2})", re.ASCII)

# the day counts are whole numbers of days that cannot be below zero (V05); a minus sign is a defect of the file (H1, R3-negative-day-counts-not-rejected)
NONNEGATIVE_COLUMNS = ("Days for shipping (real)", "Days for shipment (scheduled)")
INT_COLUMNS = (
    "Late_delivery_risk", "Category Id", "Customer Id", "Department Id", "Order Customer Id",
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
    [(_COLUMN_INDEX[name], name, "nonnegative") for name in NONNEGATIVE_COLUMNS]
    + [(_COLUMN_INDEX[name], name, "int") for name in INT_COLUMNS]
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
    # rule id -> {"rows": lines, "orders": orders} that the warning and information rules V10 to V14 and V17 affect (decision D43)
    affected: dict[str, dict[str, int]] = field(default_factory=dict)


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
        elif key[0] == "negative":
            parts.append(f"{item['count']} valor(es) negativo(s) en «{key[1]}», que cuenta días (por ejemplo, {_clean(first['value'])!r} {where})")
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
    pair_lines: set[int] = set()
    pair_orders: set[int] = set()
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
                if kind == "nonnegative":
                    ok = _NONNEGATIVE.fullmatch(value) is not None
                    if not ok and _INT.fullmatch(value) is not None:     # a whole number with a minus sign
                        clean = False
                        v05.add(("negative", name), line, {"value": value}, kind="negative", column=name)
                        continue
                elif kind == "int":
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
                    pair_lines.add(item_id)
                    pair_orders.add(order_id)
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

    frames, report = _build(cfg, size, first, kept, order_of_line, pair_discrepancies, len(pair_lines), len(pair_orders))
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
           pair_discrepancies: int, pair_lines: int = 0, pair_orders: int = 0) -> tuple[SourceFrames, SourceReport]:
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
    has_trailing = trimmed != lines["product_name"]
    trailing_lines = int(has_trailing.sum())
    trailing_orders = int(lines.loc[has_trailing, "order_id"].nunique())
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
    off_schedule_mask = orders["shipping_mode"].map(SCHEDULED_DAYS_BY_MODE) != orders["scheduled_days"]
    off_schedule = int(off_schedule_mask.sum())
    gap = sales_gap | total_gap
    post_break_ids = set(orders.loc[orders["regime"] == "post_break", "order_id"])
    affected = {
        "V10": {"rows": int(lines["order_id"].isin(set(orders.loc[off_schedule_mask, "order_id"])).sum()), "orders": off_schedule},
        "V11": {"rows": cancelled_lines, "orders": n_cancelled},
        "V12": {"rows": pair_lines, "orders": pair_orders},
        "V13": {"rows": int(gap.sum()), "orders": int(lines.loc[gap, "order_id"].nunique())},
        "V14": {"rows": trailing_lines, "orders": trailing_orders},
        "V17": {"rows": int(lines["order_id"].isin(post_break_ids).sum()), "orders": len(post_break_ids)},
    }
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
        constant_or_duplicate_columns=len(DUPLICATE_OR_CONSTANT_COLUMNS), counts=counts, affected=affected,
    )
    return SourceFrames(orders, lines, outcomes), report


# --- the canonical files --------------------------------------------------------------------------------------------------

INTERIM_FILES = (("orders", ORDERS_SCHEMA), ("order_lines", ORDER_LINES_SCHEMA), ("outcomes", OUTCOMES_SCHEMA))


def _report_json(report: SourceReport) -> bytes:
    document = asdict(report)
    document["first_order_date"] = report.first_order_date.isoformat()
    document["last_order_date"] = report.last_order_date.isoformat()
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


INTERIM_MANIFEST = "interim_manifest.json"
INTERIM_MANIFEST_VERSION = 1
_HEX64 = re.compile(r"[0-9a-f]{64}")


class InterimError(RuntimeError):
    """The three interim files are not one consistent set: one is missing or changed, or the manifest that names them is missing or not theirs."""


def write_interim(frames: SourceFrames, report: SourceReport, interim_dir: Path, source_sha256: str) -> list[Path]:
    """Write ``orders.parquet``, ``order_lines.parquet`` and ``outcomes.parquet`` to `interim_dir`, each atomically, and then the manifest of their hashes.

    The three tables are built (and so checked against their Arrow schemas) before any file is touched: a table that does not convert leaves the folder as it
    was. There is no personal column in any of them, and the metadata holds only the fingerprint, the attribution and the report's counts.

    Each rename is atomic but the four together are not, so the manifest (``interim_manifest.json``: the SHA-256 of each file and the fingerprint of the source)
    is written last and every reader checks it: a failure between two renames leaves files that do not match the manifest, which is a set that is refused
    (``interim_fingerprint`` is None, ``read_interim`` raises ``InterimError``) and rebuilt, never a mix of old and new tables that is read (decision D50).
    Returns the paths of the three Parquet files.
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
    manifest = {"version": INTERIM_MANIFEST_VERSION, "source_sha256": source_sha256, "files": {name: sha256_file(interim_dir / f"{name}.parquet") for name, _s in INTERIM_FILES}}
    with atomic_path(interim_dir / INTERIM_MANIFEST) as temporary:
        temporary.write_bytes((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return written


# --- the interim files as a unit: manifest, fingerprint and report ----------------------------------------------------------


def _metadata(path: Path) -> dict[bytes, bytes]:
    try:
        return pq.read_schema(Path(path)).metadata or {}
    except (OSError, pa.ArrowException):
        return {}


def _interim_problem(interim_dir: Path) -> str | None:
    """Why the files of `interim_dir` are not one set that the manifest names, or None if they are."""
    folder = Path(interim_dir)
    path = folder / INTERIM_MANIFEST
    if not path.is_file():
        return f"falta {INTERIM_MANIFEST} en {folder}: los archivos intermedios no son un conjunto completo"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        files = document["files"]
        valid = (
            isinstance(document, dict) and set(document) == {"version", "source_sha256", "files"} and document["version"] == INTERIM_MANIFEST_VERSION
            and isinstance(document["source_sha256"], str) and _HEX64.fullmatch(document["source_sha256"]) is not None
            and isinstance(files, dict) and set(files) == {name for name, _s in INTERIM_FILES}
            and all(isinstance(v, str) and _HEX64.fullmatch(v) for v in files.values())
        )
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        return f"{INTERIM_MANIFEST} no es un manifiesto válido de los archivos intermedios"
    for name, _schema in INTERIM_FILES:
        target = folder / f"{name}.parquet"
        if not target.is_file():
            return f"falta {target.name}, que el manifiesto nombra"
        if sha256_file(target) != files[name]:
            return f"{target.name} no coincide con la huella que dice el manifiesto: es de otra escritura"
        if _metadata(target).get(FINGERPRINT_KEY) != document["source_sha256"].encode("ascii"):
            return f"{target.name} no es de la fuente que dice el manifiesto"
    return None


def interim_state(interim_dir: Path) -> tuple[str, SourceReport] | None:
    """``(fingerprint, report)`` of the interim set, read once, or None if it is not a consistent set with a report of the current layout.

    None when the manifest or a file is missing or is not the one the manifest names, and when the stored report is missing or of an older layout
    (decision D43): in every case the streams are rebuilt from the CSV.
    """
    if _interim_problem(interim_dir) is not None:
        return None
    report = load_source_report(interim_dir)
    if report is None:
        return None
    fingerprint = _metadata(Path(interim_dir) / "orders.parquet").get(FINGERPRINT_KEY)
    return (fingerprint.decode("ascii"), report) if fingerprint is not None else None


def interim_fingerprint(interim_dir: Path) -> str | None:
    """The SHA-256 of the CSV that the three streams were built from, or None if they are not one consistent set (see ``interim_state``)."""
    state = interim_state(interim_dir)
    return None if state is None else state[0]


def read_interim(interim_dir: Path) -> tuple[SourceFrames, SourceReport]:
    """The three streams and the report of a consistent interim set; ``InterimError`` if the files are not the set that the manifest names."""
    problem = _interim_problem(interim_dir)
    if problem is not None:
        raise InterimError(problem)
    report = load_source_report(interim_dir)
    if report is None:
        raise InterimError(f"el reporte de la fuente guardado en {interim_dir} falta o es de un diseño anterior")
    tables = {}
    for name, _schema in INTERIM_FILES:
        frame = pq.read_table(Path(interim_dir) / f"{name}.parquet").to_pandas()
        for column in ("order_date", "outcome_known_date"):
            if column in frame.columns:
                frame[column] = frame[column].astype("datetime64[ns]")
        tables[name] = frame
    return SourceFrames(tables["orders"], tables["order_lines"], tables["outcomes"]), report


def load_source_report(interim_dir: Path) -> SourceReport | None:
    """The ``SourceReport`` stored with the streams, or None if there is none."""
    raw = _metadata(Path(interim_dir) / "orders.parquet").get(SOURCE_REPORT_KEY)
    if raw is None:
        return None
    document = json.loads(raw)
    if "affected" not in document:  # written before the rows and orders of each rule were counted (decision D43)
        return None
    document["first_order_date"] = date.fromisoformat(document["first_order_date"])
    document["last_order_date"] = date.fromisoformat(document["last_order_date"])
    return SourceReport(**document)


# --- the download (V01, V02) ---------------------------------------------------------------------------------------------------

USER_AGENT = "latedel-pipeline/0.1"
MANIFEST_NAME = "source_manifest.json"
MANIFEST_VERSION = 1
STAGING_ATTEMPTS = 100            # a clash of random 64-bit names is not expected; the loop is for the day it happens
REDIRECT_CODES = (301, 302, 303, 307, 308)
HTTPS_PORT = 443


class DownloadError(RuntimeError):
    """The download did not end with the whole file (network, status, timeout, truncation). Not a rule: it can be retried."""


class DownloadDeadlineError(DownloadError):
    """The download did not finish within ``source.max_seconds``. Like any transport failure it can be retried; unlike a slow read it is not left to the
    read timeout: a source that drips bytes never trips that one (decision D49)."""


class _LocalFault(Exception):
    """Carries an ``OSError`` that happened on this machine making or writing the staging file, so that it is not mistaken for a transport failure."""


def _refuse(message: str, **details: Any) -> SourceError:
    return SourceError("V02", message, [{"rule_id": "V02", **details}])


def _shown(url: str) -> str:
    """An address without its query: signed links carry their signature there, and a signature does not belong in a message or a log."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<dirección ilegible>"
    text = f"{parts.scheme}://{parts.hostname or ''}{parts.path}"
    text = text[:120] + "..." if len(text) > 120 else text
    return text + ("?..." if parts.query else "")


def check_hop(url: str, allowed_hosts: tuple[str, ...] | list[str], insecure_hosts: frozenset[str] = frozenset(), hop: int = 0) -> str:
    """Check an address before connecting to it and return its host; refuse it (V02) otherwise.

    An address is accepted when it is plain (no user information, spaces, control characters or backslashes), its scheme is ``https`` on the default port
    (``http`` and any port only for a host in `insecure_hosts`, which only a test passes) and its host is one of `allowed_hosts`. The first address and
    every ``Location`` of a redirect go through here, so a redirect cannot lead anywhere that the first address could not.
    """
    text = url if isinstance(url, str) else ""
    where = {"hop": hop, "url": _shown(text) if text else ""}
    if not text or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "\\" for ch in text):
        raise _refuse(f"la dirección {where['url']!r} no es una dirección que se pueda seguir (vacía, con espacios, caracteres de control o barras invertidas).", **where)
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        raise _refuse(f"la dirección {where['url']!r} no es una dirección que se pueda seguir.", **where) from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise _refuse(f"el esquema {scheme!r} de {where['url']} no se admite: solo https.", scheme=scheme, **where)
    host = url_host(text)
    if host is None:
        raise _refuse(f"la dirección {where['url']!r} no es una dirección que se pueda seguir (usuario en la dirección, sin host o con un puerto ilegible).", **where)
    insecure = host in insecure_hosts
    if scheme == "http" and not insecure:
        raise _refuse(f"la dirección {where['url']} no es HTTPS: cada salto de la descarga debe serlo.", host=host, scheme=scheme, **where)
    if not insecure and parts.port not in (None, HTTPS_PORT):
        raise _refuse(f"la dirección {where['url']} usa el puerto {parts.port}; solo se admite el puerto {HTTPS_PORT}.", host=host, port=parts.port, **where)
    if not host_allowed(host, allowed_hosts):
        raise _refuse(
            f"el host {host!r} de {where['url']} no está en la lista de hosts permitidos {list(allowed_hosts)} ([source] allowed_hosts). "
            "Si es el depósito al que Mendeley redirige y cambió, ver docs/runbook.md, R2 y R11.", host=host, **where,
        )
    return host


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect inside urllib: the download does it, one checked hop at a time."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # urllib's signature
        return None


DEADLINE_SLACK_S = 0.05    # a socket timeout can fire a few milliseconds before the time it was given (Windows ticks every 15.6 ms): that is still the deadline


def _expired(deadline: float | None) -> bool:
    """Whether the overall deadline has passed, or is so close that a timeout that has just fired was cut by it and not by the read timeout."""
    return deadline is not None and time.perf_counter() >= deadline - DEADLINE_SLACK_S


def _deadline_error(max_seconds: float, received: int | None = None, *, host: str | None = None, hop: int | None = None) -> DownloadDeadlineError:
    """The error of the overall deadline. `host` says which host was in progress and `hop` (1-based) which hop of the redirects was being waited for, if it was
    one; with the bytes already received it is the host that sent them (R4-deadline-error-missing-hop-context)."""
    after = "" if received is None else f" tras recibir {received} bytes"
    if host is not None:
        after += f" de {host}" if hop is None else f" mientras se esperaba el salto {hop} ({host})"
    return DownloadDeadlineError(
        f"La descarga superó el tiempo máximo de {max_seconds:g} s (source.max_seconds){after}; se interrumpió y no se guardó nada. "
        "Si el origen es lento, se puede subir ese límite; si no responde, ver docs/runbook.md, R2."
    )


def _open(url: str, allowed_hosts, max_redirects: int, insecure: frozenset[str], timeout_s: int, deadline: float | None = None, max_seconds: float | None = None):
    """Open `url` and follow its redirects by hand; every address is checked before it is requested. Returns the open response.

    With a `deadline` (a ``time.perf_counter()`` value: on Windows with Python 3.11 ``time.monotonic()`` ticks every 15.6 ms) a hop that starts after it is not requested, and a hop that is cut by it, for the shorter time it was given,
    is a ``DownloadDeadlineError`` and not a timeout of the read.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for hop in range(max_redirects + 1):
        host = check_hop(current, allowed_hosts, insecure, hop)
        timeout = float(timeout_s)
        if deadline is not None:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise _deadline_error(max_seconds or 0.0, host=host, hop=hop + 1)
            timeout = max(0.001, min(timeout, remaining))
        logger.info("download hop %d: %s (%s)", hop + 1, host, urllib.parse.urlsplit(current).scheme)
        request = urllib.request.Request(current, headers={"User-Agent": USER_AGENT})
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            code = error.code
            location = error.headers.get("Location") if code in REDIRECT_CODES else None
            error.close()
            if code in REDIRECT_CODES:
                if not location:
                    raise _refuse(f"{_shown(current)} respondió HTTP {code} sin cabecera Location.", hop=hop + 1) from None
                if hop == max_redirects:
                    raise _refuse(f"la descarga pide más de {max_redirects} redirecciones (la {hop + 1} sale de {_shown(current)}); no se sigue.",
                                  hop=hop + 1, max_redirects=max_redirects) from None
                current = urllib.parse.urljoin(current, location)
                continue
            if code == 404:
                raise DownloadError(
                    f"No se encontró la fuente en {_shown(current)} (HTTP 404). El archivo pudo haberse movido o renombrado: ver docs/runbook.md, R11."
                ) from None
            raise DownloadError(f"{_shown(current)} respondió HTTP {code}.") from None
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            timed_out = isinstance(error, TimeoutError) or isinstance(reason, TimeoutError)
            if deadline is not None and (time.perf_counter() >= deadline or (timed_out and _expired(deadline))):
                raise _deadline_error(max_seconds or 0.0, host=host, hop=hop + 1) from error
            if timed_out:
                raise DownloadError(f"Se agotó el tiempo de espera de {timeout_s} s al conectar con {_shown(current)}.") from error
            raise DownloadError(f"No se pudo conectar con {_shown(current)}: {reason}.") from error
        if response.status != 200:
            response.close()
            raise DownloadError(f"{_shown(current)} respondió HTTP {response.status} y se esperaba 200.")
        return response
    raise AssertionError("unreachable: the last hop either returns or raises")  # pragma: no cover


def _create_staging(dest: Path) -> tuple[int, Path]:
    """Create the folder of `dest` and an empty staging file in it, exclusively and under a random name; return ``(descriptor, path)``.

    The file is created as ``open(path, "wb")`` would create it, asking for mode 0666 and leaving the umask to decide (``mkstemp`` would give 0600 and
    ``os.replace`` would carry that mode into `dest`). ``O_EXCL`` means an existing file is never reused and ``O_NOFOLLOW`` that a link planted under the chosen
    name is not followed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(STAGING_ATTEMPTS):
        path = dest.with_name(f"{dest.name}.{secrets.token_hex(8)}.staging")
        try:
            return os.open(path, flags, 0o666), path
        except FileExistsError:
            continue
    raise FileExistsError(f"No se pudo crear un archivo de preparación junto a {dest} tras {STAGING_ATTEMPTS} intentos")


def download_file(
    url: str, dest: Path, accepted_sha256: str, allowed_hosts: list[str] | tuple[str, ...], max_redirects: int, max_bytes: int, chunk_bytes: int,
    timeout_s: int, *, max_seconds: float | None = None, insecure_hosts: frozenset[str] | set[str] = frozenset(),
) -> tuple[Path, str]:
    """Download `url` to a staging file of its own, hashing it on the fly; move it to `dest` only if the SHA-256 is `accepted_sha256`. Returns ``(dest, sha256)``.

    Every address is checked before it is requested (V02, ``check_hop``), the length announced and the bytes received are held to `max_bytes` (V02) and a
    different hash (V01) keeps the whole download as ``<dest>.part`` (replacing an earlier one) and leaves `dest` exactly as it was. Nothing is created until
    the first response has arrived: a refused address leaves no folder, no staging file and no part. A transport failure, a status other than 200 or a body
    shorter than announced is a ``DownloadError`` after logging it. A destination folder or staging file that cannot be created and a chunk that cannot be
    written (a full disk) are local faults: each raises its own ``OSError``, logged with its traceback, and the staging file is removed.
    `max_seconds` is the overall deadline of the whole call, redirects included (None: no deadline, as before); it is checked before every hop, bounds the time
    that a hop may take and is checked after every piece that is read, so the longest the call can run past it is one piece of the read, and a piece that never
    comes is cut by the read timeout. Passing it ends the call with a ``DownloadDeadlineError`` and the staging file removed (decision D49). Every end of the
    call, whichever it is, is logged with the seconds it took. `insecure_hosts` lets a test use plain HTTP on named hosts (a local server); the pipeline never
    passes it.
    """
    started = time.perf_counter()
    deadline = None if max_seconds is None else started + max_seconds

    def took() -> float:
        return time.perf_counter() - started

    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    insecure = frozenset(host.lower() for host in insecure_hosts)
    digest = hashlib.sha256()
    received = 0
    staging: Path | None = None

    def discard() -> None:
        if staging is not None:
            staging.unlink(missing_ok=True)

    check_hop(url, allowed_hosts, insecure)  # before any connection: a refused address costs nothing
    sending: str | None = None   # the host that is sending the body, once there is a response
    try:
        with _open(url, allowed_hosts, max_redirects, insecure, timeout_s, deadline, max_seconds) as response:
            sending = url_host(response.geturl())
            length = response.headers.get("Content-Length", "")
            announced = int(length) if length.isascii() and length.isdigit() else None
            if announced is not None and announced > max_bytes:
                raise _refuse(f"la descarga anuncia {announced} bytes, por encima del límite de {max_bytes}.", announced=announced, max_bytes=max_bytes)
            try:
                descriptor, staging = _create_staging(dest)
            except OSError as local:  # the folder or the file could not be made on this machine: not a cut connection, and the cause is the error itself
                raise _LocalFault from local
            logger.info("download start: announced size %s to %s", announced, staging)
            with os.fdopen(descriptor, "wb") as handle:
                while chunk := response.read1(chunk_bytes):
                    received += len(chunk)
                    if deadline is not None and time.perf_counter() >= deadline:
                        raise _deadline_error(max_seconds or 0.0, received, host=sending)
                    if received > max_bytes:
                        raise _refuse(f"la respuesta supera el límite de {max_bytes} bytes.", received=received, max_bytes=max_bytes)
                    try:
                        handle.write(chunk)
                    except OSError as local:  # a full disk is not a cut connection, and retrying the download would not help
                        raise _LocalFault from local
                    digest.update(chunk)
        if announced is not None and received != announced:
            raise DownloadError(f"Se recibieron {received} bytes pero se anunciaron {announced}.")
    except _LocalFault as fault:
        cause = fault.__cause__
        discard()
        if staging is None:
            logger.warning("download could not start (local fault) after %.1f s: %s", took(), cause, exc_info=cause)
        else:
            logger.warning("download stopped by a local fault after %d bytes and %.1f s: %s", received, took(), cause, exc_info=cause)
        raise cause from None
    except SourceError as error:
        discard()
        logger.warning("download refused after %.1f s: %s", took(), error)
        raise
    except DownloadError as error:
        discard()
        logger.warning("download failed after %.1f s: %s", took(), error)
        raise
    except (TimeoutError, http.client.HTTPException, OSError) as error:
        discard()
        if isinstance(error, TimeoutError) and _expired(deadline):
            failure = _deadline_error(max_seconds or 0.0, received, host=sending)
        elif isinstance(error, TimeoutError):
            failure = DownloadError(f"Se agotó el tiempo de espera de {timeout_s} s al leer la respuesta.")
        else:
            failure = DownloadError(f"La conexión se cortó tras {received} bytes: {error}.")
        logger.warning("download failed after %.1f s: %s", took(), failure)
        raise failure from error
    except BaseException:
        discard()
        raise
    assert staging is not None  # a response was read to the end
    actual = digest.hexdigest()
    if actual != accepted_sha256:
        os.replace(staging, part)
        logger.error("download hash mismatch after %.1f s: got %s, accepted %s; kept %s", took(), actual, accepted_sha256, part)
        raise SourceError(
            "V01",
            f"la huella SHA-256 del CSV descargado es {actual} y la aceptada es {accepted_sha256}; se conservó {part} para inspeccionarlo y no se reemplazó {dest}.",
            [{"rule_id": "V01", "expected": accepted_sha256, "actual": actual, "kept": str(part)}],
        )
    os.replace(staging, dest)
    try:
        part.unlink(missing_ok=True)  # the accepted file supersedes what an earlier failure kept for inspection
    except OSError as error:  # the download is finished and verified: a stale part that cannot be cleared must not turn it into a failure
        logger.warning("download accepted but the earlier %s could not be removed: %s", part, error)
    logger.info("download end: %s, %d bytes, sha256 %s, in %.1f s", dest, received, actual, took())
    return dest, actual


# --- manifest of the source ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceManifest:
    """The CSV that the interim streams were built from (``<raw_dir>/source_manifest.json``)."""

    mode: str
    source: str  # the URL, or the local path
    sha256: str
    size: int
    fetched_at: str
    csv_path: str


def read_source_manifest(path: Path) -> SourceManifest | None:
    """The manifest, or None if there is none. Anything else than the exact format is a ``ValueError``."""
    path = Path(path)
    if not path.exists():
        return None
    names = {f.name for f in fields(SourceManifest)}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("version") != MANIFEST_VERSION or set(document) != {"version", "source"}:
            raise KeyError(f"versión o claves inesperadas: {sorted(document)}")
        raw = document["source"]
        if set(raw) != names:
            raise KeyError(f"claves distintas de las esperadas: {sorted(set(raw) ^ names)}")
        return SourceManifest(**raw)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"{path} no es un manifiesto de la fuente válido: {error}") from error


def write_source_manifest(path: Path, manifest: SourceManifest) -> None:
    write_atomic(Path(path), json.dumps({"version": MANIFEST_VERSION, "source": asdict(manifest)}, indent=2, ensure_ascii=False) + "\n")


# --- the first task ----------------------------------------------------------------------------------------------------------------


class _HasConfig(Protocol):
    cfg: PipelineConfig


def _rejected(csv_path: Path, kind: str, actual: str, expected: str) -> SourceError:
    return SourceError(
        "V01",
        f"el CSV {csv_path} tiene la huella SHA-256 {actual} y la {kind} es {expected}; no se cambió nada (runbook R1).",
        [{"rule_id": "V01", "expected": expected, "actual": actual, "kept": str(csv_path)}],
    )


def fetch_source(ctx: _HasConfig, *, insecure_hosts: frozenset[str] | set[str] = frozenset()) -> dict[str, Any]:
    """Task 1: make sure the CSV is the pinned one, read it, and build the interim streams from it.

    http mode downloads the CSV only if it is not in ``raw_dir`` and checks its SHA-256 against ``accepted_sha256`` (V01); local mode registers the first
    SHA-256 it sees in the manifest, after the content has been read successfully, and rejects any other afterwards. Nothing is written when the source is
    rejected. The streams are rebuilt only when one is missing or they come from another file, so repeating the task changes nothing.
    `insecure_hosts` is for tests only (see ``download_file``).
    """
    cfg = ctx.cfg
    source, raw_dir, interim_dir = cfg.source, cfg.paths.raw_dir, cfg.paths.interim_dir
    if source.mode not in ("http", "local"):   # a configuration built without load_config skips its check: an unknown mode is not silently local
        raise ValueError(f"[source] mode: se esperaba 'http' o 'local', se recibió {source.mode!r}")
    manifest_path = raw_dir / MANIFEST_NAME
    downloaded = False
    download_seconds = 0.0

    if source.mode == "http":
        csv_path = raw_dir / source.file_name
        if csv_path.exists():
            digest = sha256_file(csv_path)
            if digest != source.accepted_sha256:
                raise _rejected(csv_path, "aceptada", digest, source.accepted_sha256)
        else:
            started = time.perf_counter()
            _, digest = download_file(
                source.url, csv_path, source.accepted_sha256, source.allowed_hosts, source.max_redirects, source.max_bytes, source.chunk_bytes,
                source.timeout_s, max_seconds=source.max_seconds, insecure_hosts=insecure_hosts,
            )
            download_seconds = round(time.perf_counter() - started, 3)
            downloaded = True
        location = source.url
    else:
        if source.local_path is None:
            raise ValueError("[source] local_path: con mode = 'local' hace falta la ruta del CSV")
        csv_path = source.local_path
        if not csv_path.is_file():
            raise FileNotFoundError(f"no existe el CSV local: {csv_path}")
        digest = sha256_file(csv_path)
        registered = read_source_manifest(manifest_path)
        if registered is not None and registered.sha256 != digest:
            raise _rejected(csv_path, "registrada", digest, registered.sha256)
        location = str(csv_path)

    size = csv_path.stat().st_size
    manifest = SourceManifest(
        mode=source.mode, source=location, sha256=digest, size=size, fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
        csv_path=csv_path.name if source.mode == "http" else str(csv_path),
    )
    result: dict[str, Any] = {"csv_path": str(csv_path), "sha256": digest, "size": size, "downloaded": downloaded, "download_seconds": download_seconds}

    state = interim_state(interim_dir)     # one read: the fingerprint and the report come from the same files (decision D50)
    if state is not None and state[0] == digest:
        report = state[1]
        current = read_source_manifest(manifest_path)
        if current is None or current.sha256 != digest:
            write_source_manifest(manifest_path, manifest)
        logger.info("interim streams already built from %s", digest)
        return result | {"built": False, "lines": report.lines, "orders": report.orders, "task_orders": report.task_orders}

    frames, report = read_source(csv_path, cfg)
    write_interim(frames, report, interim_dir, digest)
    write_source_manifest(manifest_path, manifest)
    return result | {"built": True, "lines": report.lines, "orders": report.orders, "task_orders": report.task_orders}
