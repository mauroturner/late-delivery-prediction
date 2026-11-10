"""Deliveries in two streams: the plan, the next pending one, publication by date and the manifest (RF06, D09).

The source is one static file, so the monthly flow is simulated (D09). A delivery brings two things that are published as separate files and never mixed:

- the **orders** and their **lines**, which are what is known when an order is created: each order travels in the delivery whose period of orders contains
  its creation date;
- the **outcomes**, which are what is known afterwards: each outcome travels in the delivery whose period of outcomes contains the date on which it became
  known (``outcome_known_date``), at most six days after the order.

The plan is fixed by the calendar of ``[deliveries]``: a launch that holds everything known on ``launch_date``, one delivery per month until
``last_order_date`` and a close that brings only outcomes, up to ``close_date``. Each period starts the day after the previous one ends, so every order and
every outcome belongs to exactly one delivery. ``data/raw/deliveries/manifest.json`` lists the published deliveries in order with their period, rows,
status and a SHA-256 of the three files; a republication of the same content changes nothing, and a different content for a published delivery is refused.
"""

from __future__ import annotations

import calendar
import hashlib
import io
import json
import logging
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from latedel.config import DeliveriesConfig
from latedel.files import atomic_path, write_atomic
from latedel.ingest import SourceFrames
from latedel.schema import ATTRIBUTION, ORDER_LINES_SCHEMA, ORDERS_SCHEMA, OUTCOMES_SCHEMA

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
DELIVERY_FILES = ("orders", "order_lines", "outcomes")
STATUSES = ("published", "loaded", "rejected")
_SCHEMAS = {"orders": ORDERS_SCHEMA, "order_lines": ORDER_LINES_SCHEMA, "outcomes": OUTCOMES_SCHEMA}
_DATE_COLUMNS = {"orders": ("order_date",), "order_lines": (), "outcomes": ("outcome_known_date",)}


class DeliveryError(ValueError):
    """A delivery or the manifest is not what it should be."""


class DeliveryOrderError(DeliveryError):
    """A delivery was published out of order (V20 is the same rule, checked again by the validation)."""


class DeliveryConflictError(DeliveryError):
    """A published delivery would be rewritten with other content."""


@dataclass(frozen=True)
class DeliverySpec:
    """One delivery of the plan. ``orders_from`` and ``orders_to`` are None for the close, which brings no orders."""

    delivery_id: str
    kind: str                  # "launch", "monthly" or "close"
    orders_from: date | None
    orders_to: date | None
    outcomes_from: date
    outcomes_to: date
    as_of: date


@dataclass(frozen=True)
class ManifestEntry:
    """A published delivery: its period, rows, the SHA-256 of its three files, its status and the run that loaded or rejected it."""

    delivery_id: str
    kind: str
    orders_from: str | None
    orders_to: str | None
    outcomes_from: str
    outcomes_to: str
    as_of: str
    orders: int
    lines: int
    outcomes: int
    sha256_json: str
    status: str
    run_key: str | None


@dataclass(frozen=True)
class DeliveryFrames:
    """The three tables of one delivery, as read back from its files."""

    orders: pd.DataFrame
    order_lines: pd.DataFrame
    outcomes: pd.DataFrame


# --- the plan ---------------------------------------------------------------------------------------------------------


def delivery_plan(cfg: DeliveriesConfig) -> list[DeliverySpec]:
    """The fixed list of deliveries: the launch, one per month after it, and the close."""
    day = timedelta(days=1)
    plan = [DeliverySpec(f"L{cfg.launch_date.isoformat()}", "launch", cfg.first_order_date, cfg.launch_date, cfg.first_order_date, cfg.launch_date, cfg.launch_date)]
    start = cfg.launch_date + day
    while start <= cfg.last_order_date:
        end = min(date(start.year, start.month, calendar.monthrange(start.year, start.month)[1]), cfg.last_order_date)
        plan.append(DeliverySpec(f"M{start:%Y-%m}", "monthly", start, end, start, end, end))
        start = end + day
    plan.append(DeliverySpec(f"C{cfg.close_date:%Y-%m}", "close", None, None, cfg.last_order_date + day, cfg.close_date, cfg.close_date))
    return plan


def check_coverage(frames: SourceFrames, plan: list[DeliverySpec]) -> None:
    """Refuse frames that have an order or an outcome dated outside the plan: publishing by date would drop it without a word."""
    first, last_orders = plan[0].orders_from, max(s.orders_to for s in plan if s.orders_to is not None)
    dates = frames.orders["order_date"]
    outside = int(((dates < pd.Timestamp(first)) | (dates > pd.Timestamp(last_orders))).sum())
    if outside:
        raise DeliveryError(f"{outside} {'pedido tiene' if outside == 1 else 'pedidos tienen'} una fecha fuera del plan ({first} a {last_orders}).")
    known = frames.outcomes["outcome_known_date"]
    start, end = plan[0].outcomes_from, plan[-1].outcomes_to
    outside = int(((known < pd.Timestamp(start)) | (known > pd.Timestamp(end))).sum())
    if outside:
        raise DeliveryError(f"{outside} {'resultado se conoce' if outside == 1 else 'resultados se conocen'} en una fecha fuera del plan ({start} a {end}).")


# --- the manifest ---------------------------------------------------------------------------------------------------------------


def read_manifest(path: Path) -> list[ManifestEntry]:
    """The entries of the manifest in order, or an empty list if there is none. Anything else than the exact format is a ``DeliveryError``."""
    path = Path(path)
    if not path.exists():
        return []
    names = {f.name for f in fields(ManifestEntry)}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("version") != MANIFEST_VERSION or set(document) != {"version", "entries"}:
            raise KeyError("versión o claves inesperadas")
        if not isinstance(document["entries"], list):
            raise KeyError("«entries» no es una lista")
        entries = []
        for raw in document["entries"]:
            if set(raw) != names:
                raise KeyError(f"claves distintas de las esperadas: {sorted(set(raw) ^ names)}")
            entry = ManifestEntry(**raw)
            if entry.status not in STATUSES:
                raise KeyError(f"estado desconocido {entry.status!r}")
            entries.append(entry)
        return entries
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise DeliveryError(f"{path} no es un manifiesto de entregas válido: {error}") from error


def write_manifest(path: Path, entries: list[ManifestEntry]) -> None:
    write_atomic(Path(path), json.dumps({"version": MANIFEST_VERSION, "entries": [asdict(e) for e in entries]}, indent=2) + "\n")


def next_pending(manifest: Path, plan: list[DeliverySpec]) -> DeliverySpec | None:
    """The first delivery of the plan that is not loaded yet, or None when all are. The manifest must list a prefix of the plan, in order.

    A delivery that is published but not loaded (an interrupted run) or rejected is the next one again: it is what a resume runs, and nothing after it may run first.
    """
    entries = read_manifest(manifest)
    expected = plan[: len(entries)]
    if len(entries) > len(plan) or [e.delivery_id for e in entries] != [s.delivery_id for s in expected] or any(
        (e.kind, e.orders_from, e.orders_to, e.outcomes_from, e.outcomes_to, e.as_of)
        != (s.kind, s.orders_from and s.orders_from.isoformat(), s.orders_to and s.orders_to.isoformat(), s.outcomes_from.isoformat(),
            s.outcomes_to.isoformat(), s.as_of.isoformat())
        for e, s in zip(entries, expected, strict=False)
    ):
        raise DeliveryError(f"el manifiesto {manifest} no sigue el plan de entregas: {[e.delivery_id for e in entries]} frente a {[s.delivery_id for s in plan]}.")
    for index, spec in enumerate(plan):
        if index < len(entries) and entries[index].status == "loaded":
            continue
        return spec
    return None


def mark_status(manifest_path: Path, delivery_id: str, status: str, run_key: str | None = None) -> ManifestEntry:
    """Set the status (and the run) of a published delivery. A loaded delivery stays loaded."""
    if status not in STATUSES:
        raise DeliveryError(f"estado desconocido {status!r}; los válidos son {list(STATUSES)}.")
    entries = read_manifest(manifest_path)
    for index, entry in enumerate(entries):
        if entry.delivery_id != delivery_id:
            continue
        if entry.status == "loaded" and (status != "loaded" or (run_key is not None and run_key != entry.run_key)):
            raise DeliveryError(f"la entrega {delivery_id} ya está cargada por la corrida {entry.run_key!r} y no cambia a {status!r}.")
        updated = replace(entry, status=status, run_key=run_key if run_key is not None else entry.run_key)
        entries[index] = updated
        write_manifest(manifest_path, entries)
        return updated
    raise DeliveryError(f"la entrega {delivery_id} no está en el manifiesto {manifest_path}.")


# --- publication --------------------------------------------------------------------------------------------------------------


def _digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _select(spec: DeliverySpec, frames: SourceFrames) -> dict[str, pd.DataFrame]:
    orders = frames.orders
    if spec.orders_from is None or spec.orders_to is None:
        chosen = orders.iloc[0:0]
    else:
        chosen = orders[(orders["order_date"] >= pd.Timestamp(spec.orders_from)) & (orders["order_date"] <= pd.Timestamp(spec.orders_to))]
    lines = frames.order_lines[frames.order_lines["order_id"].isin(set(chosen["order_id"]))]
    known = frames.outcomes["outcome_known_date"]
    outcomes = frames.outcomes[(known >= pd.Timestamp(spec.outcomes_from)) & (known <= pd.Timestamp(spec.outcomes_to))]
    return {"orders": chosen.reset_index(drop=True), "order_lines": lines.reset_index(drop=True), "outcomes": outcomes.reset_index(drop=True)}


def _payloads(spec: DeliverySpec, frames: SourceFrames, source_sha256: str | None) -> tuple[dict[str, bytes], dict[str, int]]:
    metadata = {b"latedel.delivery_id": spec.delivery_id.encode("ascii"), b"latedel.attribution": ATTRIBUTION.encode("utf-8")}
    if source_sha256:
        metadata[b"latedel.source_sha256"] = source_sha256.encode("ascii")
    payloads, rows = {}, {}
    for name, frame in _select(spec, frames).items():
        table = pa.Table.from_pandas(frame, schema=_SCHEMAS[name], preserve_index=False)
        buffer = io.BytesIO()
        pq.write_table(table.replace_schema_metadata({**(table.schema.metadata or {}), **metadata}), buffer)
        payloads[name], rows[name] = buffer.getvalue(), len(frame)
    return payloads, rows


def publish(spec: DeliverySpec, frames: SourceFrames, out_dir: Path, *, source_sha256: str | None = None) -> ManifestEntry:
    """Write the three files of `spec` to ``out_dir/<delivery_id>/`` and list it in the manifest as ``published``; return its entry.

    The delivery must follow the one before it (the first one is the launch, each other one starts the day after the previous outcomes end), or a
    ``DeliveryOrderError`` is raised before anything is written. Publishing a delivery that is already in the manifest with the same content changes nothing and
    returns its entry; with other content it is a ``DeliveryConflictError`` (a published delivery, and above all a loaded one, is never rewritten).
    """
    out_dir = Path(out_dir)
    manifest = out_dir / MANIFEST_NAME
    entries = read_manifest(manifest)
    payloads, rows = _payloads(spec, frames, source_sha256)
    digest = _digest({name: hashlib.sha256(blob).hexdigest() for name, blob in payloads.items()})

    for entry in entries:
        if entry.delivery_id == spec.delivery_id:
            if entry.sha256_json != digest:
                raise DeliveryConflictError(f"la entrega {spec.delivery_id} ya está publicada ({entry.status}) con otro contenido; no se reescribe.")
            return entry
    if not entries:
        if spec.kind != "launch":
            raise DeliveryOrderError(f"la primera entrega debe ser el lanzamiento y esta es {spec.delivery_id}.")
    else:
        last = entries[-1]
        if spec.outcomes_from != date.fromisoformat(last.outcomes_to) + timedelta(days=1):
            raise DeliveryOrderError(f"la entrega {spec.delivery_id} no es la contigua a {last.delivery_id}: la siguiente empieza el día después de {last.outcomes_to}.")

    folder = out_dir / spec.delivery_id
    for name in DELIVERY_FILES:
        with atomic_path(folder / f"{name}.parquet") as temporary:
            temporary.write_bytes(payloads[name])
    entry = ManifestEntry(
        delivery_id=spec.delivery_id, kind=spec.kind, orders_from=spec.orders_from.isoformat() if spec.orders_from else None,
        orders_to=spec.orders_to.isoformat() if spec.orders_to else None, outcomes_from=spec.outcomes_from.isoformat(),
        outcomes_to=spec.outcomes_to.isoformat(), as_of=spec.as_of.isoformat(), orders=rows["orders"], lines=rows["order_lines"],
        outcomes=rows["outcomes"], sha256_json=digest, status="published", run_key=None,
    )
    write_manifest(manifest, [*entries, entry])
    logger.info("published delivery %s: %d orders, %d lines, %d outcomes", spec.delivery_id, entry.orders, entry.lines, entry.outcomes)
    return entry


# --- reading a published delivery back --------------------------------------------------------------------------------------------


def _file_hashes(folder: Path) -> dict[str, str]:
    hashes = {}
    for name in DELIVERY_FILES:
        path = Path(folder) / f"{name}.parquet"
        if not path.is_file():
            raise DeliveryError(f"falta {path}.")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _row_counts(folder: Path) -> dict[str, int]:
    rows = {}
    for name in DELIVERY_FILES:
        path = Path(folder) / f"{name}.parquet"
        try:
            rows[name] = pq.ParquetFile(path).metadata.num_rows
        except (OSError, pa.ArrowException) as error:
            raise DeliveryError(f"{path} no es un archivo Parquet válido: {error}") from error
    return rows


def delivery_digest(folder: Path) -> tuple[str, dict[str, int]]:
    """The SHA-256 of the three files of a delivery (as the manifest states it) and their row counts."""
    return _digest(_file_hashes(folder)), _row_counts(folder)


def read_delivery(out_dir: Path, entry: ManifestEntry) -> DeliveryFrames:
    """Read the three files of a delivery after checking them against its manifest entry (hash first, then rows)."""
    folder = Path(out_dir) / entry.delivery_id
    digest = _digest(_file_hashes(folder))
    if digest != entry.sha256_json:
        raise DeliveryError(f"la huella de la entrega {entry.delivery_id} ({digest}) no es la del manifiesto ({entry.sha256_json}).")
    rows = _row_counts(folder)
    if rows != {"orders": entry.orders, "order_lines": entry.lines, "outcomes": entry.outcomes}:
        raise DeliveryError(f"las filas de la entrega {entry.delivery_id} ({rows}) no son las del manifiesto.")
    tables: dict[str, Any] = {}
    for name in DELIVERY_FILES:
        frame = pq.read_table(folder / f"{name}.parquet").to_pandas()
        for column in _DATE_COLUMNS[name]:
            frame[column] = frame[column].astype("datetime64[ns]")
        tables[name] = frame
    return DeliveryFrames(**tables)
