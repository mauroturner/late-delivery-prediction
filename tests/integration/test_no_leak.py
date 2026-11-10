"""No leakage by perturbation (U10, AC14): the variables of an order cannot depend on anything that is known only afterwards.

Three probes, from the file to the matrix: (1) every outcome column of the source is changed and the orders and lines that the reader returns, and so the
variables, are the same; (2) every outcome in the warehouse is changed and the variables built from the staging tables are the same; (3) the outcomes that are
not known on a date, and the orders created after it (deleted, changed and added), change nothing that is computed for that date. The model-level versions
of (3), for the scores, the thresholds and the models of a date, join this file with the units that make them (U12 to U14).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from latedel.deliveries import delivery_plan, publish, read_delivery
from latedel.features import build_features, read_order_frames
from latedel.ingest import read_source
from latedel.warehouse import connect, load_delivery, rebuild_at
from tests.fixtures.helpers import load_through, rebuild_layers
from tests.fixtures.make_fixture import HEADER, _parse, _serialise, _stamp

SETS = ("f0", "f1", "f2")


def matrices(conn) -> dict[str, pd.DataFrame]:
    orders, lines = read_order_frames(conn)
    return {name: build_features(orders, lines, name) for name in SETS}


def digest(frame: pd.DataFrame) -> int:
    return int(pd.util.hash_pandas_object(frame, index=True).sum())


def assert_same(before: dict[str, pd.DataFrame], after: dict[str, pd.DataFrame]) -> None:
    for name in SETS:
        pd.testing.assert_frame_equal(before[name], after[name], check_exact=True)
        assert digest(before[name]) == digest(after[name])


# --- (1) from the file: every outcome column changed, the same orders and lines --------------------------------------------------


def perturb_csv(source, target) -> None:
    """The fixture with every column that is known after the order changed, keeping the file valid (the label is still real days above scheduled days)."""
    table = _parse(source.read_bytes())
    ix = HEADER.index
    for row in table[1:]:
        ordered = datetime.strptime(row[ix("order date (DateOrders)")], "%m/%d/%Y %H:%M")
        cancelled = row[ix("Delivery Status")] == "Shipping canceled"
        days = 1 if cancelled else 3
        row[ix("Days for shipping (real)")] = str(days)
        row[ix("shipping date (DateOrders)")] = _stamp(ordered + timedelta(days=days))
        row[ix("Late_delivery_risk")] = "0" if cancelled else str(int(days > int(row[ix("Days for shipment (scheduled)")])))
        row[ix("Delivery Status")] = "Shipping canceled" if cancelled else "Shipping on time"
        row[ix("Order Status")] = "CANCELED" if cancelled else "CLOSED"
        row[ix("Benefit per order")] = row[ix("Order Profit Per Order")] = "123.45"
        row[ix("Order Item Profit Ratio")] = "0.5"
    target.write_bytes(_serialise(table))


def test_perturbing_every_outcome_changes_no_feature(cfg, fixture_csv, fixture_read, tmp_path):
    perturbed = tmp_path / "perturbed.csv"
    perturb_csv(fixture_csv, perturbed)
    frames, _report = read_source(perturbed, cfg)
    original = fixture_read[0]
    assert not frames.outcomes[["real_days", "source_label", "delivery_status", "order_status", "shipping_ts", "benefit_total", "profit_ratio_mean"]].equals(
        original.outcomes[["real_days", "source_label", "delivery_status", "order_status", "shipping_ts", "benefit_total", "profit_ratio_mean"]]), "the probe changes the outcomes"
    assert (frames.outcomes["real_days"] != original.outcomes["real_days"]).sum() > 1000
    pd.testing.assert_frame_equal(frames.orders, original.orders, check_exact=True)
    pd.testing.assert_frame_equal(frames.order_lines, original.order_lines, check_exact=True)
    for name in SETS:
        pd.testing.assert_frame_equal(build_features(frames.orders, frames.order_lines, name), build_features(original.orders, original.order_lines, name), check_exact=True)


# --- (2) from the warehouse: every outcome row changed, the same variables -----------------------------------------------------------


PERTURB = (
    "UPDATE raw_outcomes SET source_label = 1 - source_label, real_days = real_days + 40, delivery_status = 'Perturbed', order_status = 'PERTURBED', "
    "shipping_ts = '2099-01-01T00:00', benefit_total = -benefit_total - 7, profit_ratio_mean = 9.9, is_cancelled = 1 - is_cancelled"
)


def test_perturbing_every_outcome_in_the_warehouse_changes_no_feature(cfg, delivered, tmp_path):
    conn = connect(tmp_path / "w.sqlite")
    load_through(conn, delivered, cfg, 15)
    last = delivered[1][-1].as_of
    before = matrices(conn)
    labels = conn.execute("SELECT SUM(is_late), COUNT(*) FROM stg_outcomes").fetchone()
    conn.execute(PERTURB)
    rebuild_layers(conn, cfg, last)
    assert conn.execute("SELECT SUM(is_late), COUNT(*) FROM stg_outcomes").fetchone() != labels, "the staged outcomes did change"
    assert conn.execute("SELECT COUNT(*) FROM raw_outcomes WHERE real_days >= 40").fetchone()[0] == 3000
    assert_same(before, matrices(conn))
    conn.execute("UPDATE raw_outcomes SET outcome_known_date = date(outcome_known_date, '+4000 days')")       # and when the outcomes are known as well
    rebuild_layers(conn, cfg, last)
    assert conn.execute("SELECT COUNT(*) FROM stg_outcomes").fetchone()[0] == 0
    assert_same(before, matrices(conn))


# --- (3) from the date: later outcomes and later orders change nothing computed for the date -----------------------------------------------


def tamper_after(conn, day: date) -> dict[str, int]:
    """Change the warehouse after `day`: delete, change and add orders created after it, and change the outcomes still unknown on it. Stays valid for the controls."""
    d = day.isoformat()
    later = [r[0] for r in conn.execute("SELECT order_id FROM raw_orders WHERE order_date > ? ORDER BY order_id", (d,))]
    deleted, changed = later[::3], [o for i, o in enumerate(later) if i % 3]
    for order in deleted:
        conn.execute("DELETE FROM raw_order_lines WHERE order_id = ?", (order,))
        conn.execute("DELETE FROM raw_outcomes WHERE order_id = ?", (order,))
        conn.execute("DELETE FROM raw_orders WHERE order_id = ?", (order,))
    for order in changed:
        conn.execute("UPDATE raw_orders SET shipping_mode = 'Same Day', payment_type = 'CASH', market = 'ZZ', order_ts = substr(order_ts, 1, 11) || '23:59' WHERE order_id = ?", (order,))
        conn.execute("UPDATE raw_order_lines SET sales = sales * 10, quantity = quantity + 5 WHERE order_id = ?", (order,))
    for number in range(5):
        order, on = 900_000 + number, day + timedelta(days=1 + number)
        conn.execute(
            "INSERT INTO raw_orders VALUES (?, 'X', ?, ?, 'pre_break', 'First Class', 1, 'DEBIT', 'LATAM', 'Caribbean', 'Cuba', 'Consumer', 'EE. UU.')",
            (order, f"{on.isoformat()}T10:00", on.isoformat()))
        conn.execute("INSERT INTO raw_order_lines VALUES (?, ?, 'X', 'Fitness', 'Cleats', 1, 'New', 1, 5.0, 5.0, 0.0, 0.0, 5.0)", (900_000 + number, order))
        conn.execute("INSERT INTO raw_outcomes VALUES (?, 'X', ?, 'Late delivery', 'COMPLETE', 2, '2099-01-01T10:00', 1, 0, 1.0, 0.1)", (order, (on + timedelta(days=2)).isoformat()))
    unknown = conn.execute(
        "UPDATE raw_outcomes SET real_days = 5, source_label = CASE WHEN 5 > (SELECT scheduled_days FROM raw_orders o WHERE o.order_id = raw_outcomes.order_id) THEN 1 ELSE 0 END "
        "WHERE outcome_known_date > ? AND order_id IN (SELECT order_id FROM raw_orders WHERE order_date <= ?)", (d, d)).rowcount
    return {"deleted": len(deleted), "changed": len(changed), "added": 5, "unknown_outcomes": unknown}


@pytest.mark.parametrize("upto", [4, 5, 9])
def test_unknown_outcomes_and_later_orders_change_nothing_at_as_of(cfg, delivered, tmp_path, upto):
    as_of = delivered[1][upto - 1].as_of
    clean = connect(tmp_path / "clean.sqlite")
    load_through(clean, delivered, cfg, upto)                         # the warehouse as it was on the date
    messy = connect(tmp_path / "messy.sqlite")
    load_through(messy, delivered, cfg, 15)                           # the warehouse today, with everything that happened later
    done = tamper_after(messy, as_of)
    assert done["deleted"] > 20 and done["changed"] > 40 and done["unknown_outcomes"] > 0 and messy.execute("SELECT COUNT(*) FROM raw_orders").fetchone()[0] != 3000
    rebuild_at(messy, as_of, cfg)
    for table in ("stg_orders", "stg_order_lines", "stg_outcomes", "mart_labelled_orders", "mart_main_line"):
        assert messy.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall() == clean.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall(), table
    assert_same(matrices(clean), matrices(messy))
    assert messy.execute("SELECT COUNT(*) FROM stg_orders WHERE order_id >= 900000").fetchone()[0] == 0


def test_the_same_tampering_does_change_the_variables_when_the_date_is_the_last_one(cfg, delivered, tmp_path):
    """The control of the probe above: the tampering does change what is computed when the date lets it in, so the equality of the probe is not trivial."""
    clean = connect(tmp_path / "clean.sqlite")
    load_through(clean, delivered, cfg, 5)
    messy = connect(tmp_path / "messy.sqlite")
    load_through(messy, delivered, cfg, 15)
    tamper_after(messy, delivered[1][4].as_of)
    rebuild_at(messy, delivered[1][-1].as_of, cfg)
    assert messy.execute("SELECT COUNT(*) FROM stg_orders").fetchone()[0] > clean.execute("SELECT COUNT(*) FROM stg_orders").fetchone()[0]
    assert len(matrices(messy)["f2"]) != len(matrices(clean)["f2"])


# --- the real file (real_data) -----------------------------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_file_variables_do_not_change_when_every_outcome_does(cfg, real_read, tmp_path):
    frames, _report = real_read
    conn = connect(tmp_path / "real.sqlite")
    for spec in delivery_plan(cfg.deliveries):
        entry = publish(spec, frames, tmp_path / "deliveries", source_sha256="fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6")
        load_delivery(conn, read_delivery(tmp_path / "deliveries", entry), spec, f"real-{spec.delivery_id}", cfg, sha256_json=entry.sha256_json)
    last = delivery_plan(cfg.deliveries)[-1].as_of
    before = matrices(conn)
    assert before["f2"].shape == (65_752, 22) and before["f1"].shape == (65_752, 18) and before["f0"].shape == (65_752, 2)
    assert not before["f2"].isna().any().any()
    labels = conn.execute("SELECT SUM(is_late) FROM stg_outcomes").fetchone()[0]
    conn.execute(PERTURB)
    rebuild_layers(conn, cfg, last)
    assert conn.execute("SELECT SUM(is_late) FROM stg_outcomes").fetchone()[0] != labels
    after = matrices(conn)
    assert_same(before, after)
    print("REAL features", {name: before[name].shape for name in SETS}, "hash f2", digest(before["f2"]), "identical after changing",
          conn.execute("SELECT COUNT(*) FROM raw_outcomes").fetchone()[0], "outcomes")
