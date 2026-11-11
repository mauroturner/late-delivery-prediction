"""The site files on the whole real file, without a model (U20): every delivery is loaded, the star marts are built and the five files are written to a temporary folder.

Nothing is scored and nothing is evaluated, so the files hold counts and the pending state only: this test checks counts, shapes, bytes and that no result of the real test
block is published. The first evaluation of the real test block is U25's (D17).
"""

from __future__ import annotations

import json

import pytest

from latedel.deliveries import delivery_plan, publish, read_delivery
from latedel.export import ExportMeta, build_powerbi_marts
from latedel.site_export import MANIFEST_FILE, SITE_FILES, export_site, verify_site
from latedel.warehouse import connect, load_delivery, record_run

pytestmark = pytest.mark.real_data


def test_the_whole_real_file_gives_five_small_files_with_the_real_counts_and_no_result_of_the_test(cfg, real_read, tmp_path):
    folder = tmp_path / "deliveries"
    conn = connect(tmp_path / "real.sqlite")
    plan = delivery_plan(cfg.deliveries)
    for spec in plan:
        entry = publish(spec, real_read[0], folder, source_sha256=cfg.source.accepted_sha256)
        load_delivery(conn, read_delivery(folder, entry), spec, f"real-{spec.delivery_id}", cfg, sha256_json=entry.sha256_json)
    record_run(conn, {"run_key": "real", "started_at": "2018-02-07T08:00:00", "finished_at": None, "status": "running", "delivery_id": plan[-1].delivery_id, "as_of_date": "2018-02-07",
                      "source_sha256": cfg.source.accepted_sha256, "orders_delivery": 0, "outcomes_delivery": 0, "orders_scored": 0, "alerts": 0, "peak_working_set_mb": 1.0,
                      "peak_commit_mb": None, "message": ""})
    build_powerbi_marts(conn, cfg)
    paths = export_site(conn, tmp_path / "site", ExportMeta("real", "2018-02-07", cfg.source.accepted_sha256), cfg)
    assert [p.name for p in paths] == list(SITE_FILES) and sum(p.stat().st_size for p in paths) <= cfg.site.max_total_bytes
    docs = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in paths}
    summary, validation, algorithms = docs["summary.json"], docs["validation_rules.json"], docs["algorithms.json"]
    h = summary["headline"]
    assert (h["rows"], h["orders"], h["orders_cancelled"], h["orders_task"], h["test_orders"]) == (180_519, 65_752, 2_855, 62_897, 14_527)
    assert (h["period_start"], h["period_end"]) == ("2015-01-01", "2018-01-31")
    assert summary["evaluation_status"] == "pending" and all(c["passed"] is None for c in summary["criteria"]) and len(summary["criteria"]) == 10
    assert h["auc"] == {"model": None, "b2_mode": None, "b5_mode_noon": None} and h["test_precision"] is None
    assert algorithms["methods"] == [] and algorithms["roc"] == [] and algorithms["calibration"] == [] and algorithms["cost_curve"] == [] and algorithms["examples"] == []
    split = {b["block"]: b for b in algorithms["split"]}
    assert (split["train"]["orders"], split["test"]["orders"], split["post_break"]["orders"]) == (29_894, 14_527, 8_043)
    assert split["test"]["late_share"] is None and split["post_break"]["late_share"] is None
    assert len(algorithms["hour_profile"]) == 96 and len(algorithms["mode_rates"]) == 4
    totals = validation["totals"]
    assert (totals["rows_raw"], totals["orders_raw"], totals["orders_cancelled"], totals["rows_cancelled"], totals["orders_task"]) == (180_519, 65_752, 2_855, 7_754, 62_897)
    assert (totals["personal_columns_dropped"], totals["leaking_columns_set_aside"], totals["deliveries_loaded"], totals["deliveries_rejected"]) == (15, 8, 15, 0)
    print("REAL SITE bytes", {p.name: p.stat().st_size for p in paths}, "total", sum(p.stat().st_size for p in paths))
    text = "".join(p.read_text(encoding="utf-8") for p in paths)
    assert '"order_id"' not in text
    # H5 (D77): the five files and the manifest written last are one export; the hidden cells of the training block are counted (no result of the test is read)
    assert verify_site(tmp_path / "site") == [] and (tmp_path / "site" / MANIFEST_FILE).stat().st_size < 2000
    hidden = {name: sum(c["suppressed"] for c in algorithms[name]) for name in ("split", "mode_rates", "hour_profile")}
    print("REAL SITE hidden cells", hidden, "manifest bytes", (tmp_path / "site" / MANIFEST_FILE).stat().st_size)
    assert hidden["split"] == 0, "every block of the real file has hundreds of orders or more"
    assert hidden["mode_rates"] == 4, "without a launch there are no registered rates: the four rows are empty, and that hides no count of the hours"
    assert hidden["hour_profile"] == sum(c["train_orders"] is None and c["suppressed"] for c in algorithms["hour_profile"])
    conn.close()
