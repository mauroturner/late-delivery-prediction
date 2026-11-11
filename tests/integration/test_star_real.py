"""The star schema on the whole real file, without a model (U17, RNF10): every delivery is loaded and exported, nothing is scored and no metric is computed.

The order fact holds the real outcomes, as the dashboard will, but this test counts rows, keys and bytes only: the first evaluation of the real test block is U25's (D17).
"""

from __future__ import annotations

import pyarrow.parquet as pq
import pytest

from latedel.dashboard.semantic_model import MODEL
from latedel.deliveries import delivery_plan, publish, read_delivery
from latedel.export import MAX_PARQUET_BYTES, ExportMeta, export_star_schema, verify_star_schema
from latedel.warehouse import connect, load_delivery

pytestmark = pytest.mark.real_data


def test_the_whole_real_file_exports_a_valid_star_under_the_size_cap(cfg, real_read, tmp_path):
    folder = tmp_path / "deliveries"
    conn = connect(tmp_path / "real.sqlite")
    for spec in delivery_plan(cfg.deliveries):
        entry = publish(spec, real_read[0], folder, source_sha256=cfg.source.accepted_sha256)
        load_delivery(conn, read_delivery(folder, entry), spec, f"real-{spec.delivery_id}", cfg, sha256_json=entry.sha256_json)
    paths = export_star_schema(conn, tmp_path / "powerbi", MODEL, ExportMeta("real", "2018-02-07", cfg.source.accepted_sha256), cfg)
    assert verify_star_schema(tmp_path / "powerbi", MODEL) == []
    rows = {p.stem: pq.ParquetFile(p).metadata.num_rows for p in paths}
    sizes = {p.stem: p.stat().st_size for p in paths}
    assert rows["fact_order"] == 65_752 and rows["dim_date"] == 1_134 and rows["dim_month"] == 38 and rows["dim_shipping_mode"] == 4 and rows["dim_as_of"] == 15
    assert max(sizes.values()) < MAX_PARQUET_BYTES and max(sizes, key=sizes.get) == "fact_order"
    print("REAL STAR rows", rows, "bytes", sizes, "total", sum(sizes.values()))
    conn.close()
