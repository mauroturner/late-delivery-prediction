"""Small frames with the answer written by hand, for the baselines, the net benefit and the threshold (docs/plan.md, section 21)."""

from __future__ import annotations

import pandas as pd

# (mode, scheduled days, hour of the order, is_late) of the 24 training orders of the baseline case: the rates below are counted by hand from this list.
#   First Class     4 orders, 4 late                      -> 1.0
#   Second Class    4 orders, 3 late                      -> 0.75
#   Same Day        6 orders: 3 before noon, none late;  3 from noon, all late -> mode 0.5, before 0.0, from 1.0
#   Standard Class  10 orders, 4 late                      -> 0.4
TRAIN_ROWS = (
    [("First Class", 1, 9, 1)] * 4
    + [("Second Class", 2, 10, 1)] * 3 + [("Second Class", 2, 10, 0)]
    + [("Same Day", 0, 9, 0), ("Same Day", 0, 10, 0), ("Same Day", 0, 11, 0), ("Same Day", 0, 12, 1), ("Same Day", 0, 15, 1), ("Same Day", 0, 18, 1)]
    + [("Standard Class", 4, 13, 1)] * 4 + [("Standard Class", 4, 13, 0)] * 6
)
HAND_B2 = {"First Class": 1.0, "Second Class": 0.75, "Same Day": 0.5, "Standard Class": 0.4}
HAND_B5 = {
    ("First Class", "before"): 1.0, ("Second Class", "before"): 0.75, ("Same Day", "before"): 0.0, ("Same Day", "from"): 1.0, ("Standard Class", "from"): 0.4,
}
HAND_TRAIN_ORDERS = 24
HAND_TRAIN_LATE_RATE = 14 / 24


def baseline_case(*, other_label: int = 0) -> pd.DataFrame:
    """The labelled orders of the hand case: the training block above, plus orders of every other block whose label is always `other_label` (0 or 1).

    Changing `other_label` changes every label outside the training block: the rates of every baseline must not move.
    """
    rows = []
    order_id = 1
    for mode, scheduled, hour, late in TRAIN_ROWS:
        rows.append((order_id, f"2015-03-10T{hour:02d}:30", "2015-03-10", mode, scheduled, late, "train"))
        order_id += 1
    others = (
        ("valid_v1", "2016-08-01"), ("valid_v2", "2016-11-01"), ("purge_1", "2016-07-03"), ("purge_2", "2017-01-03"), ("test", "2017-03-01"), ("post_break", "2017-11-01"),
    )
    for block, day in others:
        for mode, scheduled, hour, _late in TRAIN_ROWS[::3]:
            rows.append((order_id, f"{day}T{hour:02d}:30", day, mode, scheduled, other_label, block))
            order_id += 1
    frame = pd.DataFrame(rows, columns=["order_id", "order_ts", "order_date", "shipping_mode", "scheduled_days", "is_late", "block"])
    frame["order_date"] = pd.to_datetime(frame["order_date"])
    frame["outcome_known_date"] = frame["order_date"] + pd.Timedelta(days=3)
    frame["regime"] = "pre_break"
    return frame[["order_id", "order_ts", "order_date", "regime", "shipping_mode", "scheduled_days", "outcome_known_date", "is_late", "block"]]
