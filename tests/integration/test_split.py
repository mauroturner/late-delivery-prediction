"""The labelled orders of a calculation date and the baselines on the warehouse (U11, AC15, AC16): fixture and real file.

Expectations are computed from the frames of the fixture, independently of the SQL. The real-file tests build a warehouse with only the launch delivery, so the
test labels are not in it; they read the training block and counts, never a metric on the test block (D17).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from latedel.baselines import baseline_rates, same_day_region_check
from latedel.features import read_order_frames
from latedel.split import LABELLED_COLUMNS, StaleMartError, block, blocks, check_label_timing, labelled_orders
from latedel.warehouse import connect, rebuild_at
from tests.fixtures.helpers import file_digest, load_through


def test_labelled_orders_at_the_launch_are_the_non_cancelled_orders_with_a_known_outcome(cfg, launch_conn, fixture_read):
    labelled = labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    frames = fixture_read[0]
    merged = frames.orders.merge(frames.outcomes, on="order_id")
    task = merged[~merged["is_cancelled"] & (merged["outcome_known_date"] <= pd.Timestamp(cfg.deliveries.launch_date))]
    assert labelled.columns.tolist() == list(LABELLED_COLUMNS)
    assert labelled["order_id"].tolist() == sorted(task["order_id"]) and labelled["order_id"].is_unique
    by_id = task.set_index("order_id")
    assert labelled["is_late"].tolist() == [int(by_id.loc[o, "real_days"] > by_id.loc[o, "scheduled_days"]) for o in labelled["order_id"]]
    assert str(labelled["is_late"].dtype) == "int64" and str(labelled["order_date"].dtype).startswith("datetime64")
    assert str(labelled["outcome_known_date"].dtype).startswith("datetime64")
    assert set(labelled["block"]) == {"train", "purge_1", "valid_v1", "valid_v2", "purge_2"}, "the test block does not exist in the warehouse at the launch"


def test_the_block_of_every_labelled_order_is_the_one_the_sql_gave_it(cfg, launch_conn):
    labelled = labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    assert labelled["block"].tolist() == [block(d, cfg) for d in labelled["order_date"]]


def test_the_purged_orders_are_labelled_but_belong_to_no_block_of_the_model(cfg, launch_conn):
    labelled = labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    purged = labelled[labelled["block"].isin(["purge_1", "purge_2"])]
    assert (labelled["block"] == "purge_1").sum() > 0 and (labelled["block"] == "purge_2").sum() > 0, "the fixture has orders in both purges"
    taken = blocks(labelled)
    assert set(taken) == {"train", "valid_v1", "valid_v2"}
    assert not set(purged["order_id"]) & set().union(*(set(p["order_id"]) for p in taken.values()))
    assert sum(len(p) for p in taken.values()) + len(purged) == len(labelled)
    assert taken["train"]["order_date"].max() <= pd.Timestamp(cfg.split.train_end)
    assert taken["valid_v1"]["order_date"].min() >= pd.Timestamp(cfg.split.valid_start)


def test_every_label_is_known_in_time_for_the_next_block(cfg, launch_conn):
    check_label_timing(labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg), cfg)


def test_reading_the_labelled_orders_writes_nothing(cfg, launch_db, tmp_path):
    copy = tmp_path / "w.sqlite"
    copy.write_bytes(launch_db.read_bytes())
    conn = connect(copy)
    before = file_digest(copy)
    labelled_orders(conn, cfg.deliveries.launch_date, cfg)
    conn.close()
    assert file_digest(copy) == before


def test_a_mart_built_for_an_earlier_date_is_refused_not_read(cfg, launch_conn):
    rebuild_at(launch_conn, date(2016, 12, 31), cfg)
    with pytest.raises(StaleMartError, match="rebuild_at"):
        labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    rebuild_at(launch_conn, cfg.deliveries.launch_date, cfg)
    assert len(labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)) > 0


def test_a_mart_built_for_a_later_date_is_cut_back_to_the_date_asked_for(cfg, delivered, launch_conn, tmp_path):
    expected = labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    conn = connect(tmp_path / "full.sqlite")
    load_through(conn, delivered, cfg, 15)               # the mart now holds the test block and the labels known up to 2018-02-07
    assert conn.execute("SELECT COUNT(*) FROM mart_labelled_orders WHERE block = 'test'").fetchone()[0] > 0
    cut = labelled_orders(conn, cfg.deliveries.launch_date, cfg)
    pd.testing.assert_frame_equal(cut, expected, check_exact=True)
    assert "test" not in set(cut["block"])
    conn.close()


def test_the_baselines_of_the_fixture_come_from_its_training_block_only(cfg, launch_conn):
    labelled = labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    rates = baseline_rates(labelled, cfg)
    train = labelled[labelled["block"] == "train"]
    assert rates.train_orders == len(train)
    by_mode = train.groupby("shipping_mode")["is_late"].mean()
    assert {k[0]: v for k, v in rates.b2.rates.items()} == pytest.approx(by_mode.to_dict())
    changed = labelled.assign(is_late=lambda f: f["is_late"].where(f["block"] == "train", 1 - f["is_late"]))
    pd.testing.assert_frame_equal(baseline_rates(changed, cfg).table(), rates.table(), check_exact=True)
    assert rates.b2.rates[("First Class",)] == 1.0, "the mechanics of the source: First Class is always late"
    assert rates.b5.rates[("Same Day", "before")] == 0.0 and rates.b5.rates[("Same Day", "from")] == 1.0, "the noon cut of Same Day"


def test_the_region_check_runs_on_the_fixture_and_finds_no_effect_inside_the_hour(cfg, launch_conn):
    labelled = labelled_orders(launch_conn, cfg.deliveries.launch_date, cfg)
    orders, _lines = read_order_frames(launch_conn)
    check = same_day_region_check(labelled, orders, cfg)
    train = labelled[(labelled["block"] == "train") & (labelled["shipping_mode"] == "Same Day")]
    assert check.orders == len(train) > 50
    assert check.late_share_by_band == {"before": 0.0, "from": 1.0}
    assert check.controlled_dof == 0 and check.controlled_p == 1.0, "nothing is left for the region once the hour is known"


# --- the real file (real_data) -----------------------------------------------------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_file_blocks_at_the_launch_the_rates_of_the_training_block_and_the_region_check(cfg, real_launch_conn, real_read):
    labelled = labelled_orders(real_launch_conn, cfg.deliveries.launch_date, cfg)
    counts = labelled["block"].value_counts().to_dict()
    assert counts == {"train": 29_894, "valid_v2": 5_032, "valid_v1": 4_649, "purge_1": 375, "purge_2": 172}, counts
    check_label_timing(labelled, cfg)
    taken = blocks(labelled)
    assert set(taken) == {"train", "valid_v1", "valid_v2"} and sum(len(p) for p in taken.values()) == 39_575

    frames, _report = real_read
    task = frames.orders.merge(frames.outcomes[["order_id", "is_cancelled"]], on="order_id")
    task = task[~task["is_cancelled"]]
    every_block = task["order_date"].map(lambda d: block(d, cfg)).value_counts().to_dict()
    assert every_block == {"train": 29_894, "purge_1": 375, "valid_v1": 4_649, "valid_v2": 5_032, "purge_2": 377, "test": 14_527, "post_break": 8_043}, every_block

    rates = baseline_rates(labelled, cfg)
    b2 = {k[0]: round(v, 4) for k, v in rates.b2.rates.items()}
    assert b2 == {"First Class": 1.0, "Same Day": 0.4652, "Second Class": 0.8017, "Standard Class": 0.4013}, b2
    assert round(rates.train_late_rate, 4) == 0.5745 and rates.train_orders == 29_894
    assert rates.b5.rates[("Same Day", "before")] == 0.0 and rates.b5.rates[("Same Day", "from")] == 1.0
    assert rates.b5.orders[("Same Day", "before")] == 853 and rates.b5.orders[("Same Day", "from")] == 742

    orders, _lines = read_order_frames(real_launch_conn)
    check = same_day_region_check(labelled, orders, cfg)
    print("REAL baseline rates", rates.table().to_dict("records"))
    print("REAL region check", check)
    assert check.orders == 1_595 and check.regions == 14
    assert check.raw_p < 0.05, "the region effect of the profile is there before the hour is controlled"
    assert check.controlled_p == 1.0 and check.controlled_dof == 0, "and it is gone with the hour controlled: every stratum has a constant outcome"
