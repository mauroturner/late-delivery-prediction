"""The split in time (U11, AC15): the block of an order, the blocks of the model, the purges and the timing of the labels.

The edges are written in the tests from the table of ``requirements.md`` (section 4), not read back from the code. The tests that go through the warehouse are in
``tests/integration/test_split.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from latedel import split
from latedel.split import SplitError, block, blocks, check_label_timing

EDGES = [
    ("2014-12-31", "outside"), ("2015-01-01", "train"), ("2016-06-30", "train"),
    ("2016-07-01", "purge_1"), ("2016-07-07", "purge_1"),
    ("2016-07-08", "valid_v1"), ("2016-09-30", "valid_v1"),
    ("2016-10-01", "valid_v2"), ("2016-12-31", "valid_v2"),
    ("2017-01-01", "purge_2"), ("2017-01-07", "purge_2"),
    ("2017-01-08", "test"), ("2017-09-30", "test"),
    ("2017-10-01", "post_break"), ("2018-01-31", "post_break"),
    ("2018-02-01", "outside"),
]


@pytest.mark.parametrize(("day", "expected"), EDGES)
def test_block_edges_are_exact_and_inclusive(cfg, day, expected):
    assert block(date.fromisoformat(day), cfg) == expected


def test_block_takes_a_date_a_datetime_a_timestamp_or_iso_text_and_the_split_section_alone(cfg):
    assert block("2016-07-08", cfg) == block(date(2016, 7, 8), cfg) == block(datetime(2016, 7, 8, 23, 59), cfg) == block(pd.Timestamp("2016-07-08 12:00"), cfg) == "valid_v1"
    assert block(date(2016, 7, 8), cfg.split) == "valid_v1"
    assert block("2016-07-08T09:30", cfg) == "valid_v1"


def test_the_edges_follow_the_configuration_and_are_not_written_in_the_code(cfg):
    moved = replace(cfg.split, train_end=date(2016, 6, 29), valid_start=date(2016, 7, 7))
    assert block(date(2016, 6, 30), moved) == "purge_1" and block(date(2016, 7, 7), moved) == "valid_v1"
    assert block(date(2016, 6, 30), cfg) == "train"


def test_purged_orders_are_in_no_block(cfg):
    days = [date(2016, 7, 1) + timedelta(days=i) for i in range(7)] + [date(2017, 1, 1) + timedelta(days=i) for i in range(7)] + [date(2015, 5, 1), date(2016, 8, 1), date(2017, 5, 1)]
    frame = pd.DataFrame({"order_id": range(len(days)), "order_date": days, "block": [block(d, cfg) for d in days]})
    purged = set(frame.loc[frame["block"].isin(["purge_1", "purge_2"]), "order_id"])
    assert len(purged) == 14, "the 14 days of the two purges are purged"
    taken = blocks(frame)
    assert set(taken) == {"train", "valid_v1", "test"}
    in_a_block = set().union(*(set(part["order_id"]) for part in taken.values()))
    assert in_a_block == set(frame["order_id"]) - purged
    assert not purged & in_a_block, "no purged order is in any block"
    assert all(set(part["block"]) == {name} for name, part in taken.items())


def test_outside_orders_are_in_no_block_either(cfg):
    frame = pd.DataFrame({"order_id": [1, 2, 3], "block": ["train", "outside", "test"]})
    assert set(blocks(frame)) == {"train", "test"}
    assert sorted(set().union(*(set(p["order_id"]) for p in blocks(frame).values()))) == [1, 3]


def test_blocks_needs_the_block_column():
    with pytest.raises(ValueError, match="block"):
        blocks(pd.DataFrame({"order_id": [1]}))


def test_the_names_of_the_blocks_and_the_ones_the_model_may_use_are_fixed():
    assert split.BLOCK_NAMES == ("train", "purge_1", "valid_v1", "valid_v2", "purge_2", "test", "post_break")
    assert split.MODEL_BLOCKS == ("train", "valid_v1", "valid_v2", "test", "post_break")
    assert not {"purge_1", "purge_2", "outside"} & set(split.MODEL_BLOCKS)


def test_block_counts_lists_every_block_and_outside_only_when_present():
    frame = pd.DataFrame({"block": ["train", "train", "purge_1", "test"]})
    assert split.block_counts(frame) == {"train": 2, "purge_1": 1, "valid_v1": 0, "valid_v2": 0, "purge_2": 0, "test": 1, "post_break": 0}
    assert split.block_counts(pd.DataFrame({"block": ["outside", "train"]}))["outside"] == 1


def _labelled(rows):
    frame = pd.DataFrame(rows, columns=["order_id", "block", "outcome_known_date"])
    frame["outcome_known_date"] = pd.to_datetime(frame["outcome_known_date"])
    return frame


def test_check_label_timing_accepts_labels_known_in_time(cfg):
    check_label_timing(_labelled([(1, "train", "2016-07-07"), (2, "valid_v1", "2016-12-01"), (3, "valid_v2", "2017-01-07"), (4, "purge_2", "2017-01-09"), (5, "test", "2017-05-01")]), cfg)


def test_check_label_timing_refuses_a_training_label_known_when_the_validation_starts(cfg):
    with pytest.raises(SplitError, match=r"entrenamiento.*2016-07-08"):
        check_label_timing(_labelled([(1, "train", "2016-07-08")]), cfg)


def test_check_label_timing_refuses_a_validation_label_known_after_the_launch(cfg):
    with pytest.raises(SplitError, match=r"validación.*2017-01-07"):
        check_label_timing(_labelled([(1, "valid_v2", "2017-01-08")]), cfg)
    with pytest.raises(SplitError, match="validación"):
        check_label_timing(_labelled([(1, "valid_v1", "2017-01-08")]), cfg)
