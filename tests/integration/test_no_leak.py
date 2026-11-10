"""No leakage by perturbation (U10, AC14): the variables of an order cannot depend on anything that is known only afterwards.

Three probes, from the file to the matrix: (1) every outcome column of the source is changed and the orders and lines that the reader returns, and so the
variables, are the same; (2) every outcome in the warehouse is changed and the variables built from the staging tables are the same; (3) the outcomes that are
not known on a date, and the orders created after it (deleted, changed and added), change nothing that is computed for that date. The model-level versions
of (3) join this file with the units that make them: the model and its calibration (U12); the threshold and its sensitivity (U13); the scores and the flags (U14).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from latedel.baselines import baseline_rates
from latedel.deliveries import delivery_plan, publish, read_delivery
from latedel.features import build_features, read_order_frames
from latedel.ingest import read_source
from latedel.model import predict_risk, train_model
from latedel.split import blocks, labelled_orders
from latedel.threshold import sensitivity, v2_threshold
from latedel.warehouse import connect, load_delivery, rebuild_at
from tests.fixtures.helpers import load_through, rebuild_layers
from tests.fixtures.make_fixture import HEADER, _parse, _serialise, _stamp
from tests.fixtures.model_helpers import small_cfg

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
    """Change the warehouse after `day`: delete, change and add orders created after it, and change the outcomes still unknown on it. Stays valid for the controls.

    Set-based, in the rows that a one-by-one loop over the orders created after `day` (every third one deleted, the others changed) would touch.
    """
    d = day.isoformat()
    conn.execute("DROP TABLE IF EXISTS temp.later")
    conn.execute("CREATE TEMP TABLE later AS SELECT order_id, (ROW_NUMBER() OVER (ORDER BY order_id)) - 1 AS position FROM raw_orders WHERE order_date > ?", (d,))
    deleted = conn.execute("SELECT COUNT(*) FROM later WHERE position % 3 = 0").fetchone()[0]
    changed = conn.execute("SELECT COUNT(*) FROM later WHERE position % 3 != 0").fetchone()[0]
    for table in ("raw_order_lines", "raw_outcomes", "raw_orders"):
        conn.execute(f"DELETE FROM {table} WHERE order_id IN (SELECT order_id FROM later WHERE position % 3 = 0)")
    conn.execute(
        "UPDATE raw_orders SET shipping_mode = 'Same Day', payment_type = 'CASH', market = 'ZZ', order_ts = substr(order_ts, 1, 11) || '23:59' "
        "WHERE order_id IN (SELECT order_id FROM later WHERE position % 3 != 0)")
    conn.execute("UPDATE raw_order_lines SET sales = sales * 10, quantity = quantity + 5 WHERE order_id IN (SELECT order_id FROM later WHERE position % 3 != 0)")
    conn.execute("DROP TABLE temp.later")
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
    return {"deleted": deleted, "changed": changed, "added": 5, "unknown_outcomes": unknown}


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


# --- (3) at the level of the model: nothing later than the date reaches the selection, the calibration or the threshold (D47, U12 and U13) --------------------------------


def trained_at(conn, as_of, cfg):
    rebuild_at(conn, as_of, cfg)
    return train_model(conn, as_of, cfg)


@pytest.fixture(scope="module")
def launch_pair(cfg, delivered, tmp_path_factory):
    """The warehouse as it was at the launch (`clean`) and the one of today with everything later altered (`messy`), each with the model trained for the launch."""
    config = small_cfg(cfg)
    as_of = delivered[1][0].as_of                                     # the launch: the date at which the model is trained
    folder = tmp_path_factory.mktemp("launch_pair")
    clean = connect(folder / "clean.sqlite")
    load_through(clean, delivered, config, 1)                         # the warehouse as it was on the date
    messy = connect(folder / "messy.sqlite")
    load_through(messy, delivered, config, 15)                        # the warehouse today, with everything that happened later
    done = tamper_after(messy, as_of)
    assert done["deleted"] > 20 and done["unknown_outcomes"] > 0
    yield SimpleNamespace(config=config, as_of=as_of, clean=clean, messy=messy, a=trained_at(clean, as_of, config), b=trained_at(messy, as_of, config))
    clean.close()
    messy.close()


def test_the_selection_and_calibration_of_a_date_do_not_depend_on_later_data(launch_pair):
    a, b = launch_pair.a, launch_pair.b
    assert a.selection.chosen == b.selection.chosen and a.selection.losses == b.selection.losses, "the losses of the selection, exactly"
    pd.testing.assert_frame_equal(a.valid_v2, b.valid_v2, check_exact=True)              # the calibrated risks of V2: model and calibrator
    probe = matrices(launch_pair.clean)[a.model.feature_set].loc[a.valid_v2["order_id"]]
    np.testing.assert_array_equal(predict_risk(a.model, probe), predict_risk(b.model, probe))
    np.testing.assert_array_equal(a.model.calibrated.calibrated_classifiers_[0].calibrators[0].X_thresholds_, b.model.calibrated.calibrated_classifiers_[0].calibrators[0].X_thresholds_)
    np.testing.assert_array_equal(a.model.calibrated.calibrated_classifiers_[0].calibrators[0].y_thresholds_, b.model.calibrated.calibrated_classifiers_[0].calibrators[0].y_thresholds_)


def test_the_threshold_and_the_sensitivity_of_a_date_do_not_depend_on_later_data(launch_pair):
    config = launch_pair.config
    a, b = launch_pair.a, launch_pair.b
    assert v2_threshold(a.valid_v2, config) == v2_threshold(b.valid_v2, config)
    tables = []
    for part, conn in ((a, launch_pair.clean), (b, launch_pair.messy)):
        labelled = labelled_orders(conn, launch_pair.as_of, config)
        v2 = blocks(labelled)["valid_v2"].set_index("order_id").loc[part.valid_v2["order_id"]].reset_index()
        rules = baseline_rates(labelled, config).scores(v2)
        scores = {"model": part.valid_v2["risk"].to_numpy(), "b2_mode": rules["b2_mode"], "b5_mode_noon": rules["b5_mode_noon"]}
        tables.append(sensitivity(scores, part.valid_v2["is_late"].to_numpy(), None, None, config))
    pd.testing.assert_frame_equal(tables[0], tables[1], check_exact=True)


def test_changing_a_label_that_is_known_on_the_date_does_change_the_model_and_the_threshold(cfg, delivered, tmp_path):
    """The control of the probes above: what the date does let in moves the model and the threshold, so their equality is not trivial."""
    config = small_cfg(cfg)
    as_of = delivered[1][0].as_of
    clean = connect(tmp_path / "clean.sqlite")
    load_through(clean, delivered, config, 1)
    changed = connect(tmp_path / "changed.sqlite")
    load_through(changed, delivered, config, 1)
    flipped = changed.execute(
        "UPDATE raw_outcomes SET real_days = CASE WHEN real_days > (SELECT scheduled_days FROM raw_orders o WHERE o.order_id = raw_outcomes.order_id) THEN 0 ELSE 6 END, "
        "source_label = 1 - source_label WHERE is_cancelled = 0 AND order_id IN (SELECT order_id FROM raw_orders WHERE order_date BETWEEN '2016-07-08' AND '2016-09-30')").rowcount
    assert flipped > 100
    a, b = trained_at(clean, as_of, config), trained_at(changed, as_of, config)
    assert a.selection.losses != b.selection.losses and not a.valid_v2["risk"].equals(b.valid_v2["risk"])
    # the labels of V2 alone (the model is the same) move the threshold
    flipped_v2 = changed.execute(
        "UPDATE raw_outcomes SET real_days = CASE WHEN real_days > (SELECT scheduled_days FROM raw_orders o WHERE o.order_id = raw_outcomes.order_id) THEN 0 ELSE 6 END, "
        "source_label = 1 - source_label WHERE is_cancelled = 0 AND order_id IN (SELECT order_id FROM raw_orders WHERE order_date BETWEEN '2016-10-01' AND '2016-12-31')").rowcount
    assert flipped_v2 > 100
    c = trained_at(changed, as_of, config)
    assert v2_threshold(c.valid_v2, config) != v2_threshold(b.valid_v2, config)


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
