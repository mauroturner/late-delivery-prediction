"""Deliveries in two streams (U05): the plan, the next pending one, publication by date and the manifest (RF06, D09, AC09)."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from latedel.config import DeliveriesConfig
from latedel.deliveries import (
    DELIVERY_FILES,
    DeliveryConflictError,
    DeliveryError,
    DeliveryFrames,
    DeliveryOrderError,
    DeliverySpec,
    ManifestEntry,
    check_coverage,
    delivery_digest,
    delivery_plan,
    mark_status,
    next_pending,
    publish,
    read_delivery,
    read_manifest,
)
from latedel.ingest import SourceFrames
from latedel.schema import ORDER_LINES_SCHEMA, ORDERS_SCHEMA, OUTCOMES_SCHEMA

IDS = ["L2017-01-07"] + [f"M2017-{m:02d}" for m in range(1, 13)] + ["M2018-01", "C2018-02"]
SHA = "ab" * 32


def plan_for(cfg):
    return delivery_plan(cfg.deliveries)


def publish_all(plan, frames, folder, upto=None):
    return [publish(spec, frames, folder, source_sha256=SHA) for spec in plan[:upto]]


def snapshot(folder: Path) -> dict[str, str]:
    if not folder.exists():
        return {}
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.rglob("*")) if p.is_file()}


# --- the plan (D09) --------------------------------------------------------------------------------------------------


def test_the_plan_has_a_launch_thirteen_monthly_deliveries_and_a_close(cfg):
    plan = plan_for(cfg)
    assert [s.delivery_id for s in plan] == IDS and len(plan) == 15
    assert [s.kind for s in plan] == ["launch"] + ["monthly"] * 13 + ["close"]
    launch, first, second, last_monthly, close = plan[0], plan[1], plan[2], plan[13], plan[14]
    assert (launch.orders_from, launch.orders_to, launch.outcomes_from, launch.outcomes_to, launch.as_of) == (
        date(2015, 1, 1), date(2017, 1, 7), date(2015, 1, 1), date(2017, 1, 7), date(2017, 1, 7))
    assert (first.orders_from, first.orders_to, first.as_of) == (date(2017, 1, 8), date(2017, 1, 31), date(2017, 1, 31))
    assert (second.orders_from, second.orders_to, second.as_of) == (date(2017, 2, 1), date(2017, 2, 28), date(2017, 2, 28))
    assert (last_monthly.orders_from, last_monthly.orders_to, last_monthly.as_of) == (date(2018, 1, 1), date(2018, 1, 31), date(2018, 1, 31))
    assert (close.orders_from, close.orders_to, close.outcomes_from, close.outcomes_to, close.as_of) == (None, None, date(2018, 2, 1), date(2018, 2, 7), date(2018, 2, 7))
    for spec in plan[1:14]:
        assert (spec.outcomes_from, spec.outcomes_to) == (spec.orders_from, spec.orders_to), "a monthly delivery brings the orders and the outcomes of its month"


def test_each_period_starts_the_day_after_the_one_before_ends_and_they_do_not_overlap(cfg):
    plan = plan_for(cfg)
    for before, after in zip(plan, plan[1:], strict=False):
        assert after.outcomes_from == before.outcomes_to + timedelta(days=1)
        if after.orders_from is not None:
            assert after.orders_from == before.orders_to + timedelta(days=1)
        assert after.as_of == after.outcomes_to > before.as_of
    assert plan[0].orders_from == cfg.deliveries.first_order_date and plan[-2].orders_to == cfg.deliveries.last_order_date
    assert plan[-1].as_of == cfg.deliveries.close_date


def test_the_plan_follows_the_calendar_of_the_configuration(cfg):
    other = DeliveriesConfig(first_order_date=date(2015, 1, 1), launch_date=date(2015, 3, 10), last_order_date=date(2015, 5, 20), close_date=date(2015, 5, 27))
    plan = delivery_plan(other)
    assert [s.delivery_id for s in plan] == ["L2015-03-10", "M2015-03", "M2015-04", "M2015-05", "C2015-05"]
    assert [(s.orders_from, s.orders_to) for s in plan[1:4]] == [
        (date(2015, 3, 11), date(2015, 3, 31)), (date(2015, 4, 1), date(2015, 4, 30)), (date(2015, 5, 1), date(2015, 5, 20))]
    assert (plan[-1].outcomes_from, plan[-1].outcomes_to) == (date(2015, 5, 21), date(2015, 5, 27))
    leap = DeliveriesConfig(first_order_date=date(2016, 1, 1), launch_date=date(2016, 1, 31), last_order_date=date(2016, 3, 15), close_date=date(2016, 3, 22))
    assert [(s.orders_from, s.orders_to) for s in delivery_plan(leap)[1:3]] == [(date(2016, 2, 1), date(2016, 2, 29)), (date(2016, 3, 1), date(2016, 3, 15))]


# --- publication: each order and each outcome in the delivery of its date (AC09) ---------------------------------------


def test_each_outcome_goes_to_the_delivery_of_its_known_date(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    entries = publish_all(plan, frames, tmp_path / "deliveries")
    orders_in, outcomes_in, lines_in = {}, {}, {}
    for spec, entry in zip(plan, entries, strict=True):
        delivery = read_delivery(tmp_path / "deliveries", entry)
        for order_id, order_date in zip(delivery.orders["order_id"], delivery.orders["order_date"], strict=True):
            assert spec.orders_from <= order_date.date() <= spec.orders_to
            assert order_id not in orders_in, "an order is published once"
            orders_in[order_id] = spec.delivery_id
        for order_id, known in zip(delivery.outcomes["order_id"], delivery.outcomes["outcome_known_date"], strict=True):
            assert spec.outcomes_from <= known.date() <= spec.outcomes_to
            assert order_id not in outcomes_in, "an outcome is published once"
            outcomes_in[order_id] = spec.delivery_id
        for line_id in delivery.order_lines["line_id"]:
            assert line_id not in lines_in
            lines_in[line_id] = spec.delivery_id
        assert set(delivery.order_lines["order_id"]) <= set(delivery.orders["order_id"]), "the lines of an order travel with the order"
    assert set(orders_in) == set(frames.orders["order_id"]) and set(outcomes_in) == set(frames.outcomes["order_id"])
    assert set(lines_in) == set(frames.order_lines["line_id"])
    ids = {spec.delivery_id: i for i, spec in enumerate(plan)}
    assert all(ids[orders_in[o]] <= ids[outcomes_in[o]] for o in orders_in), "no outcome arrives before its order"
    # the delivery of an outcome is the one that contains its known date, recomputed here without the module
    known = frames.outcomes.set_index("order_id")["outcome_known_date"]
    for order_id, delivery_id in outcomes_in.items():
        spec = plan[ids[delivery_id]]
        assert spec.outcomes_from <= known[order_id].date() <= spec.outcomes_to


def test_the_entry_says_period_rows_and_a_hash_that_can_be_recomputed(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    folder = tmp_path / "deliveries"
    entries = publish_all(plan, frames, folder)
    assert sum(e.orders for e in entries) == len(frames.orders) and sum(e.outcomes for e in entries) == len(frames.outcomes)
    assert sum(e.lines for e in entries) == len(frames.order_lines)
    assert entries[-1].orders == 0 and entries[-1].lines == 0 and entries[-1].outcomes > 0, "the close brings outcomes only"
    for spec, entry in zip(plan, entries, strict=True):
        assert entry == dataclasses.replace(entry, status="published", run_key=None) and isinstance(entry, ManifestEntry)
        assert (entry.delivery_id, entry.kind, entry.as_of) == (spec.delivery_id, spec.kind, spec.as_of.isoformat())
        assert entry.orders_from == (spec.orders_from.isoformat() if spec.orders_from else None)
        assert entry.outcomes_from == spec.outcomes_from.isoformat() and entry.outcomes_to == spec.outcomes_to.isoformat()
        hashes = {name: hashlib.sha256((folder / spec.delivery_id / f"{name}.parquet").read_bytes()).hexdigest() for name in DELIVERY_FILES}
        assert entry.sha256_json == hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        digest, rows = delivery_digest(folder / spec.delivery_id)
        assert digest == entry.sha256_json and rows == {"orders": entry.orders, "order_lines": entry.lines, "outcomes": entry.outcomes}
        assert sorted(p.name for p in (folder / spec.delivery_id).iterdir()) == ["order_lines.parquet", "orders.parquet", "outcomes.parquet"]


def test_manifest_has_the_versioned_format_and_lists_the_deliveries_in_order(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    folder = tmp_path / "deliveries"
    plan = plan_for(cfg)
    assert read_manifest(folder / "manifest.json") == []
    publish_all(plan, frames, folder, upto=3)
    document = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert document["version"] == 1 and set(document) == {"version", "entries"}
    assert [e["delivery_id"] for e in document["entries"]] == IDS[:3]
    assert [e.delivery_id for e in read_manifest(folder / "manifest.json")] == IDS[:3]
    assert sorted(p.name for p in folder.iterdir()) == sorted(["manifest.json", *IDS[:3]]), "no temporary file is left"


def test_published_files_have_the_schemas_the_attribution_and_the_fingerprint(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    entry = publish(plan[0], frames, tmp_path, source_sha256=SHA)
    for name, schema in (("orders", ORDERS_SCHEMA), ("order_lines", ORDER_LINES_SCHEMA), ("outcomes", OUTCOMES_SCHEMA)):
        file = pq.ParquetFile(tmp_path / entry.delivery_id / f"{name}.parquet")
        assert [(f.name, str(f.type)) for f in file.schema_arrow] == [(f.name, str(f.type)) for f in schema]
        meta = file.schema_arrow.metadata
        assert meta[b"latedel.delivery_id"] == b"L2017-01-07" and meta[b"latedel.source_sha256"] == SHA.encode() and b"Mendeley Data, v5" in meta[b"latedel.attribution"]


def test_the_two_streams_never_share_a_file_or_a_column(cfg, fixture_read, tmp_path, fixture_truth):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    publish_all(plan, frames, tmp_path, upto=4)
    outcome_only = {"real_days", "shipping_ts", "source_label", "delivery_status", "order_status", "benefit_total", "profit_ratio_mean", "outcome_known_date", "is_cancelled"}
    for spec in plan[:4]:
        folder = tmp_path / spec.delivery_id
        assert not outcome_only & set(pq.read_schema(folder / "orders.parquet").names)
        assert not outcome_only & set(pq.read_schema(folder / "order_lines.parquet").names)
        for path in folder.iterdir():
            blob = path.read_bytes()
            assert not any(needle in blob for needle in fixture_truth.sentinel_needles)


def test_read_delivery_returns_what_was_published_and_nothing_else(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    entries = publish_all(plan, frames, tmp_path)
    delivery = read_delivery(tmp_path, entries[3])
    assert isinstance(delivery, DeliveryFrames)
    spec = plan[3]
    mask = (frames.orders["order_date"].dt.date >= spec.orders_from) & (frames.orders["order_date"].dt.date <= spec.orders_to)
    pd.testing.assert_frame_equal(delivery.orders.reset_index(drop=True), frames.orders[mask].reset_index(drop=True))
    assert len(delivery.order_lines) == int(frames.order_lines["order_id"].isin(set(frames.orders.loc[mask, "order_id"])).sum())
    assert list(delivery.orders.columns) == list(frames.orders.columns) and list(delivery.outcomes.columns) == list(frames.outcomes.columns)


def test_read_delivery_refuses_files_that_are_not_the_manifests(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    entry = publish(plan_for(cfg)[0], frames, tmp_path)
    path = tmp_path / entry.delivery_id / "outcomes.parquet"
    original = path.read_bytes()
    path.write_bytes(original + b"\x00")
    with pytest.raises(DeliveryError, match="huella"):
        read_delivery(tmp_path, entry)
    path.write_bytes(original)
    assert read_delivery(tmp_path, entry).outcomes.shape[0] == entry.outcomes
    path.unlink()
    with pytest.raises(DeliveryError, match="falta"):
        read_delivery(tmp_path, entry)


# --- republishing and order ------------------------------------------------------------------------------------------


def test_republishing_the_same_delivery_changes_nothing(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    first = publish_all(plan, frames, tmp_path, upto=2)
    before = snapshot(tmp_path)
    again = publish(plan[1], frames, tmp_path, source_sha256=SHA)
    assert again == first[1] and snapshot(tmp_path) == before
    assert [e.delivery_id for e in read_manifest(tmp_path / "manifest.json")] == IDS[:2], "no duplicate entry"
    mark_status(tmp_path / "manifest.json", plan[1].delivery_id, "loaded", "run-1")
    loaded = snapshot(tmp_path)
    assert publish(plan[1], frames, tmp_path, source_sha256=SHA).status == "loaded" and snapshot(tmp_path) == loaded


def test_republishing_with_other_content_is_refused_and_changes_nothing(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    publish_all(plan, frames, tmp_path, upto=2)
    before = snapshot(tmp_path)
    changed = SourceFrames(frames.orders, frames.order_lines, frames.outcomes.assign(real_days=frames.outcomes["real_days"] + 1))
    with pytest.raises(DeliveryConflictError, match=r"M2017-01.*otro contenido"):
        publish(plan[1], changed, tmp_path, source_sha256=SHA)
    assert snapshot(tmp_path) == before
    mark_status(tmp_path / "manifest.json", "M2017-01", "loaded", "run-1")
    before = snapshot(tmp_path)
    with pytest.raises(DeliveryConflictError):
        publish(plan[1], changed, tmp_path, source_sha256=SHA)
    assert snapshot(tmp_path) == before, "a loaded delivery is never rewritten"


def test_a_delivery_out_of_order_is_refused_before_writing_anything(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    with pytest.raises(DeliveryOrderError, match="lanzamiento"):
        publish(plan[1], frames, tmp_path)
    assert snapshot(tmp_path) == {} and not tmp_path.joinpath("M2017-01").exists()
    publish(plan[0], frames, tmp_path)
    before = snapshot(tmp_path)
    for skipped in (plan[2], plan[14]):
        with pytest.raises(DeliveryOrderError, match="contigua|siguiente"):
            publish(skipped, frames, tmp_path)
    assert snapshot(tmp_path) == before


def test_a_close_without_orders_is_a_valid_delivery_with_empty_order_files(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    entries = publish_all(plan, frames, tmp_path)
    close = read_delivery(tmp_path, entries[-1])
    assert len(close.orders) == 0 and len(close.order_lines) == 0 and len(close.outcomes) == entries[-1].outcomes > 0
    assert list(close.orders.columns) == list(frames.orders.columns) and close.orders["order_date"].dtype == frames.orders["order_date"].dtype
    assert pq.ParquetFile(tmp_path / "C2018-02" / "orders.parquet").metadata.num_rows == 0


# --- the next pending delivery ------------------------------------------------------------------------------------------


def test_next_pending_follows_the_manifest(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    manifest = tmp_path / "manifest.json"
    assert next_pending(manifest, plan) == plan[0], "no manifest: the launch"
    publish(plan[0], frames, tmp_path)
    assert next_pending(manifest, plan) == plan[0], "published but not loaded: it is still the one to run (a resume)"
    mark_status(manifest, "L2017-01-07", "loaded", "run-0")
    assert next_pending(manifest, plan) == plan[1]
    publish(plan[1], frames, tmp_path)
    mark_status(manifest, "M2017-01", "rejected", "run-1")
    assert next_pending(manifest, plan) == plan[1], "a rejected delivery blocks the ones after it"
    mark_status(manifest, "M2017-01", "loaded", "run-1")
    for spec in plan[2:]:
        publish(spec, frames, tmp_path)
        mark_status(manifest, spec.delivery_id, "loaded", f"run-{spec.delivery_id}")
    assert next_pending(manifest, plan) is None, "everything loaded: nothing pending"


def test_a_manifest_that_is_not_a_prefix_of_the_plan_is_refused(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    publish_all(plan, frames, tmp_path, upto=3)
    manifest = tmp_path / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["entries"][1], document["entries"][2] = document["entries"][2], document["entries"][1]
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(DeliveryError, match="no sigue el plan"):
        next_pending(manifest, plan)
    document["entries"] = [dict(document["entries"][0], delivery_id="X2099-01")]
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(DeliveryError, match="no sigue el plan"):
        next_pending(manifest, plan)


@pytest.mark.parametrize("broken", [
    "not json", "[]", '{"version": 2, "entries": []}', '{"version": 1}', '{"version": 1, "entries": [{"delivery_id": "L"}]}',
    '{"version": 1, "entries": "x"}',
])
def test_a_damaged_manifest_is_refused(tmp_path, broken):
    path = tmp_path / "manifest.json"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(DeliveryError, match="manifiesto"):
        read_manifest(path)


def test_mark_status_checks_the_id_the_status_and_the_direction(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    publish(plan_for(cfg)[0], frames, tmp_path)
    manifest = tmp_path / "manifest.json"
    with pytest.raises(DeliveryError, match="X1"):
        mark_status(manifest, "X1", "loaded", "r")
    with pytest.raises(DeliveryError, match="estado"):
        mark_status(manifest, "L2017-01-07", "done", "r")
    mark_status(manifest, "L2017-01-07", "rejected", "run-a")
    entry = read_manifest(manifest)[0]
    assert (entry.status, entry.run_key) == ("rejected", "run-a")
    mark_status(manifest, "L2017-01-07", "loaded", "run-b")
    with pytest.raises(DeliveryError, match="cargada"):
        mark_status(manifest, "L2017-01-07", "published", None)
    assert read_manifest(manifest)[0].status == "loaded"


# --- coverage: nothing may fall outside the plan --------------------------------------------------------------------------


def test_check_coverage_accepts_the_fixture_and_refuses_orders_and_outcomes_outside_the_plan(cfg, fixture_read):
    frames, _ = fixture_read
    plan = plan_for(cfg)
    check_coverage(frames, plan)
    late = frames.orders.copy()
    late.loc[late.index[0], "order_date"] = pd.Timestamp("2018-03-01")
    with pytest.raises(DeliveryError, match=r"1 pedido.*fuera del plan"):
        check_coverage(SourceFrames(late, frames.order_lines, frames.outcomes), plan)
    early = frames.orders.copy()
    early.loc[early.index[:2], "order_date"] = pd.Timestamp("2014-12-31")
    with pytest.raises(DeliveryError, match=r"2 pedidos.*fuera del plan"):
        check_coverage(SourceFrames(early, frames.order_lines, frames.outcomes), plan)
    far = frames.outcomes.copy()
    far.loc[far.index[0], "outcome_known_date"] = pd.Timestamp("2018-02-08")
    with pytest.raises(DeliveryError, match=r"1 resultado.*fuera del plan"):
        check_coverage(SourceFrames(frames.orders, frames.order_lines, far), plan)


def test_publish_is_total_only_after_check_coverage_so_the_fixture_loses_nothing(cfg, fixture_read, tmp_path):
    frames, _ = fixture_read
    entries = publish_all(plan_for(cfg), frames, tmp_path)
    assert sum(e.orders for e in entries) == len(frames.orders)


# --- the real source (AC09), marked real_data ------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_deliveries_cover_every_order_line_and_outcome_exactly_once(cfg, real_read, tmp_path):
    frames, report = real_read
    plan = plan_for(cfg)
    check_coverage(frames, plan)
    entries = publish_all(plan, frames, tmp_path)
    assert len(entries) == 15
    assert sum(e.orders for e in entries) == report.orders == 65_752
    assert sum(e.lines for e in entries) == report.lines == 180_519
    assert sum(e.outcomes for e in entries) == 65_752
    launch = entries[0]
    assert launch.orders == int((frames.orders["order_date"].dt.date <= date(2017, 1, 7)).sum())
    assert entries[-1].orders == 0 and entries[-1].outcomes > 0
    print("DELIVERIES:", [(e.delivery_id, e.orders, e.lines, e.outcomes) for e in entries])
    for spec, entry in zip(plan, entries, strict=True):
        delivery = read_delivery(tmp_path, entry)
        assert delivery.orders["order_date"].map(lambda d: spec.orders_from <= d.date() <= spec.orders_to).all() if spec.kind != "close" else True
        assert delivery.outcomes["outcome_known_date"].map(lambda d: spec.outcomes_from <= d.date() <= spec.outcomes_to).all()
