"""The follow-up on the real launch warehouse (U16). Nothing is scored at the launch, so there is nothing to follow: any count of closed alerts on the real test months
would be a metric on the real test block, which only U25's first evaluation may compute (D17)."""

from __future__ import annotations

import pytest

from latedel.tracking import TRACKING_COLUMNS, track

pytestmark = pytest.mark.real_data


def test_the_launch_of_the_real_file_has_nothing_to_follow(real_launch_conn, cfg):
    result = track(real_launch_conn, cfg.deliveries.launch_date)
    assert result.frame.empty and list(result.frame.columns) == list(TRACKING_COLUMNS)
    assert real_launch_conn.execute("SELECT COUNT(*) FROM tracking_monthly").fetchone()[0] == 0
    assert real_launch_conn.execute("SELECT COUNT(*) FROM model_scores").fetchone()[0] == 0
    assert [(r.rule_id, r.rows_affected, r.orders_affected) for r in result.rules] == [("I02", 0, 0), ("I04", 0, 0)]
