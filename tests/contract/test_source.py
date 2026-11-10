"""The file contract, safe reading and data minimisation (U03, item 2: the file is untrusted and holds personal data): AC03 to AC08.

The reader is checked against the synthetic fixture, whose truth is known, and against the real CSV (``real_data``).
"""

from __future__ import annotations

import csv
import dataclasses
import io
import logging
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from latedel.ingest import SourceFrames, read_source, write_interim
from latedel.schema import (
    DUPLICATE_OR_CONSTANT_COLUMNS,
    LEAKING_COLUMNS,
    ORDER_LEVEL_COLUMNS,
    ORDER_LINES_SCHEMA,
    ORDER_TIME_COLUMNS,
    ORDERS_SCHEMA,
    OUTCOMES_SCHEMA,
    PERSONAL_COLUMNS,
    SOURCE_COLUMNS,
    SOURCE_TO_LINE,
    SOURCE_TO_ORDER,
    SourceError,
)
from tests.fixtures.make_fixture import HEADER, MALFORMED_KINDS, MALFORMED_RULES, _parse, _serialise

SHA = "ab" * 32
ORDER_COLUMNS = ["order_id", "order_ts", "order_date", "regime", "shipping_mode", "scheduled_days", "payment_type", "market", "order_region",
                 "order_country", "customer_segment", "customer_country"]
LINE_COLUMNS = ["line_id", "order_id", "department_name", "category_name", "product_card_id", "product_name", "quantity", "product_price", "sales", "discount",
                "discount_rate", "item_total"]
OUTCOME_COLUMNS = ["order_id", "outcome_known_date", "delivery_status", "order_status", "real_days", "shipping_ts", "source_label", "is_cancelled",
                   "benefit_total", "profit_ratio_mean"]


def with_limit(cfg, max_bytes: int):
    return dataclasses.replace(cfg, source=dataclasses.replace(cfg.source, max_bytes=max_bytes))


def edited(fixture_csv: Path, tmp_path: Path, change, name: str = "edited.csv") -> Path:
    """A copy of the fixture whose parsed table `change(table)` has modified."""
    table = _parse(fixture_csv.read_bytes())
    change(table)
    target = tmp_path / name
    target.write_bytes(_serialise(table))
    return target


def column(name: str) -> int:
    return HEADER.index(name)


# --- the four lists of columns (D06, D07) ----------------------------------------------------------------------------


def test_the_four_lists_cover_the_53_columns_without_overlap():
    lists = [PERSONAL_COLUMNS, LEAKING_COLUMNS, DUPLICATE_OR_CONSTANT_COLUMNS, ORDER_TIME_COLUMNS]
    assert [len(x) for x in lists] == [15, 8, 7, 23]
    flat = [name for group in lists for name in group]
    assert len(flat) == len(set(flat)) == 53 and set(flat) == set(SOURCE_COLUMNS) and len(SOURCE_COLUMNS) == 53
    assert tuple(HEADER) == SOURCE_COLUMNS
    assert len(ORDER_LEVEL_COLUMNS) == 20 and set(ORDER_LEVEL_COLUMNS) <= set(SOURCE_COLUMNS)


def test_what_reaches_orders_and_lines_comes_only_from_the_allowed_list():
    assert set(SOURCE_TO_ORDER) <= set(ORDER_TIME_COLUMNS) and set(SOURCE_TO_LINE) <= set(ORDER_TIME_COLUMNS) | {"Product Price"}
    assert not (set(SOURCE_TO_ORDER) | set(SOURCE_TO_LINE)) & (set(PERSONAL_COLUMNS) | set(LEAKING_COLUMNS) | set(DUPLICATE_OR_CONSTANT_COLUMNS))
    assert [f.name for f in ORDERS_SCHEMA] == ORDER_COLUMNS and [f.name for f in ORDER_LINES_SCHEMA] == LINE_COLUMNS
    assert [f.name for f in OUTCOMES_SCHEMA] == OUTCOME_COLUMNS


# --- the valid fixture is read as its truth says (AC06) ---------------------------------------------------------------


def test_fixture_is_read_into_three_streams_with_the_truth_counts(fixture_read, fixture_truth):
    frames, report = fixture_read
    assert isinstance(frames, SourceFrames)
    assert list(frames.orders.columns) == ORDER_COLUMNS and list(frames.order_lines.columns) == LINE_COLUMNS and list(frames.outcomes.columns) == OUTCOME_COLUMNS
    assert len(frames.order_lines) == fixture_truth.n_lines
    assert len(frames.orders) == len(frames.outcomes) == fixture_truth.n_orders
    assert frames.orders["order_id"].is_unique and frames.order_lines["line_id"].is_unique and frames.outcomes["order_id"].is_unique
    assert frames.orders["order_id"].is_monotonic_increasing and frames.outcomes["order_id"].is_monotonic_increasing
    assert list(frames.order_lines.sort_values(["order_id", "line_id"]).index) == list(frames.order_lines.index)
    assert int(frames.outcomes["is_cancelled"].sum()) == fixture_truth.cancelled_orders
    assert report.counts == fixture_truth.expected, "every count a later rule needs is the truth of the fixture"
    assert report.lines == fixture_truth.n_lines and report.orders == fixture_truth.n_orders and report.size_bytes == fixture_truth.size_bytes
    assert report.cancelled_orders == fixture_truth.cancelled_orders and report.task_orders == fixture_truth.n_orders - fixture_truth.cancelled_orders
    assert (report.personal_columns_dropped, report.leaking_columns_moved, report.constant_or_duplicate_columns) == (15, 8, 7)
    assert report.max_outcome_lag_days <= 6


def test_every_order_equals_what_the_generator_decided(fixture_read, fixture_truth):
    frames, _ = fixture_read
    orders = frames.orders.set_index("order_id")
    outcomes = frames.outcomes.set_index("order_id")
    lines = frames.order_lines.groupby("order_id").size()
    for order_id, truth in fixture_truth.orders.items():
        row, outcome = orders.loc[order_id], outcomes.loc[order_id]
        assert row["order_ts"] == truth.order_ts and row["shipping_mode"] == truth.mode and row["scheduled_days"] == truth.scheduled_days
        assert row["market"] == truth.market and row["regime"] == truth.regime and lines[order_id] == truth.n_lines
        assert str(row["order_date"])[:10] == truth.order_ts[:10]
        assert outcome["real_days"] == truth.real_days and outcome["shipping_ts"] == truth.shipping_ts and bool(outcome["is_cancelled"]) is truth.cancelled
        assert outcome["source_label"] == (0 if truth.cancelled else int(truth.late))
        known = pd.Timestamp(truth.order_ts[:10]) + pd.Timedelta(days=truth.real_days)
        assert pd.Timestamp(outcome["outcome_known_date"]) == max(known, pd.Timestamp(truth.shipping_ts[:10]))


def test_dtypes_follow_the_arrow_schemas(fixture_read):
    frames, _ = fixture_read
    assert str(frames.orders["order_date"].dtype).startswith("datetime64") and str(frames.outcomes["outcome_known_date"].dtype).startswith("datetime64")
    assert frames.orders["order_id"].dtype == "int64" and frames.order_lines["quantity"].dtype == "int64" and frames.order_lines["sales"].dtype == "float64"
    assert frames.outcomes["is_cancelled"].dtype == bool and frames.outcomes["source_label"].dtype == "int64"
    assert frames.orders["regime"].isin(["pre_break", "post_break"]).all()
    for frame in (frames.orders, frames.order_lines, frames.outcomes):
        assert not frame.isna().any().any()


def test_product_names_are_trimmed_and_text_is_kept_as_is(fixture_read, fixture_truth):
    frames, report = fixture_read
    lines = frames.order_lines.set_index("line_id")
    assert lines["product_name"].map(lambda s: s == s.strip()).all()
    assert report.counts["V14"]["lines"] == len(fixture_truth.cases["trailing_space_line_ids"]) > 0
    assert all(lines.loc[i, "product_name"] == "Fixture Trailing Product" for i in fixture_truth.cases["trailing_space_line_ids"])
    assert lines.loc[fixture_truth.cases["script_product_line_id"], "product_name"] == "<script>alert(1)</script>"
    assert lines.loc[fixture_truth.cases["comma_quote_line_id"], "product_name"] == 'Bag, "Large" Duffel'
    assert "México" in set(frames.orders["order_country"])


def test_benefit_and_profit_are_summed_and_averaged_per_order_in_the_outcomes_stream(fixture_csv, fixture_read):
    table = _parse(fixture_csv.read_bytes())
    totals: dict[int, float] = {}
    ratios: dict[int, list[float]] = {}
    for row in table[1:]:
        order = int(row[column("Order Id")])
        totals[order] = totals.get(order, 0.0) + float(row[column("Benefit per order")])
        ratios.setdefault(order, []).append(float(row[column("Order Item Profit Ratio")]))
    outcomes = fixture_read[0].outcomes.set_index("order_id")
    for order, total in list(totals.items())[:200]:
        assert outcomes.loc[order, "benefit_total"] == pytest.approx(total, abs=1e-6)
        assert outcomes.loc[order, "profit_ratio_mean"] == pytest.approx(sum(ratios[order]) / len(ratios[order]), abs=1e-9)


def test_v13_counts_the_lines_whose_amounts_do_not_add_up_to_more_than_a_cent_and_a_half(cfg, fixture_csv, tmp_path):
    def change(table):
        total = column("Order Item Total")
        table[2][total] = f"{float(table[2][total]) + 0.01:.2f}"     # one cent off: the source itself has gaps of up to 0.0100135
        table[3][total] = f"{float(table[3][total]) + 0.02:.2f}"     # two cents off: a discrepancy
        sales = column("Sales")
        table[4][sales] = f"{float(table[4][sales]) + 1:.2f}"        # the sales no longer equal price times quantity: a discrepancy

    _, report = read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert report.counts["V13"] == {"discrepancies": 2}


def test_a_file_with_lf_endings_gives_the_same_frames(cfg, fixture_csv, fixture_read, tmp_path):
    lf = tmp_path / "lf.csv"
    lf.write_bytes(fixture_csv.read_bytes().replace(b"\r\n", b"\n"))
    frames, report = read_source(lf, cfg)
    pd.testing.assert_frame_equal(frames.orders, fixture_read[0].orders)
    pd.testing.assert_frame_equal(frames.outcomes, fixture_read[0].outcomes)
    assert report.counts == fixture_read[1].counts


def test_reading_writes_nothing_and_leaves_the_global_csv_limit_alone(cfg, fixture_csv, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "f.csv").write_bytes(fixture_csv.read_bytes())
    before = sorted(p.name for p in folder.iterdir())
    limit = csv.field_size_limit()
    read_source(folder / "f.csv", cfg)
    assert sorted(p.name for p in folder.iterdir()) == before and csv.field_size_limit() == limit == 131072
    assert [p for p in tmp_path.iterdir() if p.name != "in"] == []


# --- data minimisation (AC04, RNF12, D07) ------------------------------------------------------------------------------


def test_personal_columns_never_reach_interim_files(cfg, fixture_read, fixture_truth, tmp_path):
    frames, report = fixture_read
    interim = tmp_path / "data" / "interim"
    written = write_interim(frames, report, interim, SHA)
    assert sorted(p.name for p in written) == ["order_lines.parquet", "orders.parquet", "outcomes.parquet"]
    assert sorted(p.name for p in interim.iterdir()) == ["order_lines.parquet", "orders.parquet", "outcomes.parquet"], "no temporary file is left"

    blobs = {p.name: p.read_bytes() for p in interim.iterdir()}
    for name, blob in blobs.items():
        for needle in fixture_truth.sentinel_needles:
            assert needle not in blob, f"{needle!r} found in {name}"
        for customer_id in fixture_truth.sentinel_customer_ids:
            assert str(customer_id).encode() not in blob, f"customer id {customer_id} found in {name}"
        for column_name in PERSONAL_COLUMNS:
            assert column_name.encode() not in blob, f"personal column {column_name!r} named in {name}"
    # nor in the data frames the reader returned
    for frame in (frames.orders, frames.order_lines, frames.outcomes):
        assert not {c.lower().replace(" ", "_") for c in PERSONAL_COLUMNS} & set(frame.columns)
        text = frame.astype(str).to_csv()
        assert all(needle.decode() not in text for needle in fixture_truth.sentinel_needles)
    # and the leaking columns live in the outcomes stream only
    assert not {"real_days", "shipping_ts", "source_label", "delivery_status", "order_status", "benefit_total"} & (set(frames.orders) | set(frames.order_lines))


def test_interim_parquet_files_have_the_declared_schemas_and_carry_the_fingerprint(fixture_read, tmp_path):
    frames, report = fixture_read
    write_interim(frames, report, tmp_path, SHA)
    for name, schema, frame in (("orders", ORDERS_SCHEMA, frames.orders), ("order_lines", ORDER_LINES_SCHEMA, frames.order_lines),
                                ("outcomes", OUTCOMES_SCHEMA, frames.outcomes)):
        file = pq.ParquetFile(tmp_path / f"{name}.parquet")
        assert [(f.name, str(f.type)) for f in file.schema_arrow] == [(f.name, str(f.type)) for f in schema]
        assert file.metadata.num_rows == len(frame)
        meta = file.schema_arrow.metadata
        assert meta[b"latedel.source_sha256"] == SHA.encode() and b"Mendeley Data, v5" in meta[b"latedel.attribution"]
    back = pq.read_table(tmp_path / "orders.parquet").to_pandas()
    assert back["order_id"].tolist() == frames.orders["order_id"].tolist() and back["order_ts"].tolist() == frames.orders["order_ts"].tolist()


def test_writing_again_replaces_the_files_and_a_failure_leaves_the_old_ones(fixture_read, tmp_path, monkeypatch):
    frames, report = fixture_read
    write_interim(frames, report, tmp_path, SHA)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    write_interim(frames, report, tmp_path, SHA)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before, "the same input gives the same bytes"
    broken = SourceFrames(frames.orders, frames.order_lines.assign(sales="not a number"), frames.outcomes)
    with pytest.raises(Exception):  # noqa: B017 - any failure of the writer: what matters is what is left on disk
        write_interim(broken, report, tmp_path, "cd" * 32)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before, "nothing changed and no temporary file remains"


# --- every malformed variant fails its rule and says where (AC03, AC05, AC07, AC08) ---------------------------------------


def test_each_malformed_variant_fails_its_rule(cfg, fixture_csv, fixture_truth, malformed, tmp_path):
    limited = with_limit(cfg, fixture_truth.size_bytes + 1000)
    assert read_source(fixture_csv, limited)[1].lines == fixture_truth.n_lines
    for kind in MALFORMED_KINDS:
        path = malformed(kind)
        before, listing = path.read_bytes(), sorted(p.name for p in tmp_path.iterdir())
        if kind == "wrong_hash":
            frames, _ = read_source(path, cfg)  # a different hash is rule V01, checked by fetch_source: the content itself is valid
            assert len(frames.orders) == fixture_truth.n_orders
            continue
        with pytest.raises(SourceError) as error:
            read_source(path, limited if kind == "oversize" else cfg)
        assert error.value.rule_id == MALFORMED_RULES[kind], f"{kind}: {error.value}"
        assert str(error.value).startswith(MALFORMED_RULES[kind] + ":")
        assert error.value.problems, f"{kind}: the problems are listed"
        assert path.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == listing, "the file is untouched and nothing was written"


def test_a_rejected_file_writes_nothing_to_interim(cfg, malformed, tmp_path):
    interim = tmp_path / "data" / "interim"
    for kind in ("short_row", "unmasked_email", "label_mismatch"):
        with pytest.raises(SourceError):
            read_source(malformed(kind), cfg)
    assert not interim.exists() and not list(tmp_path.glob("*.parquet"))


def test_v02_oversize_is_refused_before_reading(cfg, fixture_truth, malformed):
    path = malformed("oversize")
    limited = with_limit(cfg, fixture_truth.size_bytes + 1000)
    with pytest.raises(SourceError, match=r"^V02:.*límite") as error:
        read_source(path, limited)
    assert error.value.problems[0]["size_bytes"] == path.stat().st_size and error.value.problems[0]["max_bytes"] == limited.source.max_bytes
    assert read_source(path, with_limit(cfg, path.stat().st_size))[1].lines == fixture_truth.n_lines, "at the limit exactly it is read"


def test_v03_nul_bom_and_invalid_byte_say_what_and_where(cfg, malformed):
    with pytest.raises(SourceError, match=r"^V03:.*nulo.*línea \d+"):
        read_source(malformed("nul_byte"), cfg)
    with pytest.raises(SourceError, match=r"^V03:.*BOM"):
        read_source(malformed("bom"), cfg)
    with pytest.raises(SourceError, match=r"^V03:.*cp1252.*0x81.*línea \d+") as error:
        read_source(malformed("invalid_cp1252_byte"), cfg)
    assert error.value.problems[0]["line"] > 1


def test_v03_a_file_that_is_not_in_the_configured_encoding_is_refused(cfg, fixture_csv, tmp_path):
    utf16 = tmp_path / "u16.csv"
    utf16.write_bytes(fixture_csv.read_bytes().decode("cp1252").encode("utf-16"))
    with pytest.raises(SourceError, match=r"^V03:.*BOM"):
        read_source(utf16, cfg)


def test_v03_a_lone_carriage_return_is_not_a_line_ending(cfg, fixture_csv, tmp_path):
    path = tmp_path / "cr.csv"
    raw = fixture_csv.read_bytes()
    first = raw.index(b"\r\n")
    path.write_bytes(raw[:first] + b"\r" + raw[first + 2:])
    with pytest.raises(SourceError, match=r"^V03:.*CR"):
        read_source(path, cfg)


def test_v04_names_the_columns_that_differ(cfg, malformed):
    with pytest.raises(SourceError, match=r"^V04:.*54 columnas.*Extra Column") as error:
        read_source(malformed("extra_column"), cfg)
    assert error.value.problems[0]["unexpected"] == ["Extra Column"]
    with pytest.raises(SourceError, match=r"^V04:.*52 columnas.*Shipping Mode") as error:
        read_source(malformed("missing_column"), cfg)
    assert error.value.problems[0]["missing"] == ["Shipping Mode"]
    with pytest.raises(SourceError, match=r"^V04:.*Market.*Markets") as error:
        read_source(malformed("renamed_column"), cfg)
    assert error.value.problems[0]["missing"] == ["Market"] and error.value.problems[0]["unexpected"] == ["Markets"]


def test_v04_columns_in_another_order_and_an_empty_file(cfg, fixture_csv, tmp_path):
    def swap(table):
        table[0][0], table[0][1] = table[0][1], table[0][0]

    with pytest.raises(SourceError, match=r"^V04:.*orden"):
        read_source(edited(fixture_csv, tmp_path, swap), cfg)
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    with pytest.raises(SourceError, match=r"^V04:.*vacío"):
        read_source(empty, cfg)


def test_v05_a_header_without_rows_is_refused(cfg, tmp_path):
    only_header = tmp_path / "h.csv"
    only_header.write_bytes((",".join(HEADER) + "\r\n").encode("cp1252"))
    with pytest.raises(SourceError, match=r"^V05:.*no tiene filas"):
        read_source(only_header, cfg)


def test_v05_a_short_row_and_a_date_that_does_not_convert_say_where(cfg, malformed):
    with pytest.raises(SourceError, match=r"^V05:.*52 campos.*53.*línea \d+") as error:
        read_source(malformed("short_row"), cfg)
    assert error.value.problems[0]["count"] == 1
    with pytest.raises(SourceError, match=r"^V05:.*order date \(DateOrders\).*13/45/2017") as error:
        read_source(malformed("bad_date"), cfg)
    assert error.value.problems[0]["column"] == "order date (DateOrders)" and error.value.problems[0]["count"] == 1


@pytest.mark.parametrize(("name", "value"), [
    ("order date (DateOrders)", "2/30/2016 10:00"),     # not a day of the calendar
    ("order date (DateOrders)", "1/5/2016 24:00"),      # not an hour
    ("order date (DateOrders)", "1/5/2016 9:5"),        # minutes need two digits
    ("order date (DateOrders)", "2016-01-05 09:05"),    # another format
    ("shipping date (DateOrders)", ""),
    ("Order Item Quantity", "2.5"),
    ("Order Item Quantity", " 2"),
    ("Order Item Quantity", "1_0"),
    ("Order Item Quantity", "²"),                  # a superscript two: str.isdigit() is true and int() refuses it
    ("Sales", "nan"),
    ("Sales", "inf"),
    ("Sales", "1e999"),
    ("Sales", "12,5"),
    ("Sales", ""),
    ("Late_delivery_risk", "yes"),
    ("Order Id", "12a"),
    ("Product Price", "-"),
])
def test_v05_every_value_that_does_not_convert_is_refused(cfg, fixture_csv, tmp_path, name, value):
    def change(table):
        table[7][column(name)] = value

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert error.value.rule_id == "V05" and error.value.problems[0]["column"] == name


def test_v05_a_value_in_a_personal_column_is_refused_without_being_echoed(cfg, fixture_csv, tmp_path):
    secret = "12 Private Street, Springfield"

    def change(table):
        table[3][column("Latitude")] = secret
        table[4][column("Customer Id")] = secret

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert error.value.rule_id == "V05"
    assert secret not in str(error.value) and "Private" not in repr(error.value.problems)
    assert {p["column"] for p in error.value.problems} == {"Latitude", "Customer Id"}


def test_v05_a_field_over_the_csv_limit_and_a_stray_quote_are_rows_that_do_not_parse(cfg, fixture_csv, tmp_path):
    def long_field(table):
        table[9][column("Product Name")] = "x" * 140_000

    with pytest.raises(SourceError, match=r"^V05:.*campo"):
        read_source(edited(fixture_csv, tmp_path, long_field, "long.csv"), cfg)
    raw = fixture_csv.read_bytes()
    broken = raw.replace(b"Consumer", b'"Cons"umer', 1)
    path = tmp_path / "quote.csv"
    path.write_bytes(broken)
    with pytest.raises(SourceError, match=r"^V05:.*comillas"):
        read_source(path, cfg)
    assert csv.field_size_limit() == 131072


def test_v05_an_empty_line_inside_the_file_is_a_row_without_fields(cfg, fixture_csv, tmp_path):
    raw = fixture_csv.read_bytes()
    first = raw.index(b"\r\n") + 2
    second = raw.index(b"\r\n", first) + 2
    path = tmp_path / "blank.csv"
    path.write_bytes(raw[:second] + b"\r\n" + raw[second:])
    with pytest.raises(SourceError, match=r"^V05:.*0 campos"):
        read_source(path, cfg)


def test_v06_names_the_column_and_the_line_but_never_the_value(cfg, malformed):
    with pytest.raises(SourceError, match=r"^V06:.*Customer Email.*línea \d+") as error:
        read_source(malformed("unmasked_email"), cfg)
    assert "ann.smith" not in str(error.value) and "ann.smith" not in repr(error.value.problems)
    assert error.value.problems[0]["column"] == "Customer Email" and error.value.problems[0]["count"] == 1
    with pytest.raises(SourceError, match=r"^V06:.*Customer Password") as error:
        read_source(malformed("unmasked_password"), cfg)
    assert "hunter2" not in str(error.value) and "hunter2" not in repr(error.value.problems)


def test_v06_one_unmasked_row_among_thousands_is_enough_and_all_are_counted(cfg, fixture_csv, tmp_path):
    def change(table):
        for number in (5, 50, 500, 5000):
            table[number][column("Customer Email")] = f"person{number}@example.com"

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert error.value.rule_id == "V06" and error.value.problems[0]["count"] == 4 and len(error.value.problems[0]["examples"]) <= 5


def test_v07_two_modes_in_an_order_and_a_repeated_item_id(cfg, malformed):
    with pytest.raises(SourceError, match=r"^V07:.*Shipping Mode.*pedido \d+") as error:
        read_source(malformed("two_modes_one_order"), cfg)
    problem = error.value.problems[0]
    assert problem["kind"] == "order_level_column" and problem["orders"] == 1 and "Shipping Mode" in problem["columns"]
    with pytest.raises(SourceError, match=r"^V07:.*Order Item Id.*repetid") as error:
        read_source(malformed("duplicate_item_id"), cfg)
    assert error.value.problems[0]["kind"] == "duplicate_item_id" and error.value.problems[0]["count"] == 1


def test_v07_a_change_in_a_personal_order_level_column_is_seen_and_not_echoed(cfg, fixture_csv, fixture_truth, tmp_path):
    order = str(fixture_truth.cases["multi_line_order_id"])

    def change(table):
        lines = [row for row in table[1:] if row[column("Order Id")] == order]
        assert len(lines) >= 2
        lines[1][column("Customer Id")] = "9999999"
        lines[1][column("Order Zipcode")] = "secret-zip-code"

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert error.value.rule_id == "V07"
    text = str(error.value) + repr(error.value.problems)
    assert "9999999" not in text and "secret-zip-code" not in text
    assert {"Customer Id", "Order Zipcode"} <= set(error.value.problems[0]["columns"])


def test_v08_names_the_order_with_the_wrong_label(cfg, malformed):
    with pytest.raises(SourceError, match=r"^V08:.*pedido \d+") as error:
        read_source(malformed("label_mismatch"), cfg)
    problem = error.value.problems[0]
    assert problem["count"] == 1 and problem["examples"][0]["order_id"] > 0 and problem["examples"][0]["label"] in (0, 1)


def test_v08_cancelled_orders_are_exempt_from_the_label_rule(cfg, fixture_csv, tmp_path, fixture_truth):
    cancelled = fixture_truth.cases["cancelled_order_id"]

    def change(table):
        for row in table[1:]:
            if int(row[column("Order Id")]) == cancelled:
                row[column("Late_delivery_risk")] = "1" if int(row[column("Days for shipping (real)")]) <= int(row[column("Days for shipment (scheduled)")]) else "0"

    frames, _ = read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert bool(frames.outcomes.set_index("order_id").loc[cancelled, "is_cancelled"])


def test_v09_names_the_order_and_the_lag(cfg, malformed):
    with pytest.raises(SourceError, match=r"^V09:.*pedido \d+.*7 días") as error:
        read_source(malformed("outcome_after_7_days"), cfg)
    problem = error.value.problems[0]
    assert problem["count"] == 1 and problem["examples"][0]["lag_days"] == 7 and problem["max_allowed_days"] == 6


def test_v09_follows_the_purge_days_of_the_configuration(cfg, fixture_csv, fixture_read):
    lag = fixture_read[1].max_outcome_lag_days
    assert lag == 6
    tighter = dataclasses.replace(cfg, split=dataclasses.replace(cfg.split, purge_days=lag))  # a lag of 6 no longer fits in a purge of 6
    with pytest.raises(SourceError, match=r"^V09:"):
        read_source(fixture_csv, tighter)


def test_the_first_rule_that_fails_is_the_one_reported(cfg, fixture_csv, tmp_path):
    def change(table):
        table[10][column("Customer Email")] = "a@b.c"                               # V06
        table[12][column("Shipping Mode")] = "First Class" if table[12][column("Shipping Mode")] != "First Class" else "Same Day"  # V07 if it shares an order

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, change), cfg)
    assert error.value.rule_id == "V06"

    def worse(table):
        table[10][column("Customer Email")] = "a@b.c"
        table[11].pop()                                                             # V05 outranks V06

    with pytest.raises(SourceError) as error:
        read_source(edited(fixture_csv, tmp_path, worse, "worse.csv"), cfg)
    assert error.value.rule_id == "V05"


def test_messages_never_carry_a_sentinel_whatever_the_variant(cfg, fixture_truth, malformed):
    limited = with_limit(cfg, fixture_truth.size_bytes + 1000)
    for kind in MALFORMED_KINDS:
        if kind == "wrong_hash":
            continue
        with pytest.raises(SourceError) as error:
            read_source(malformed(kind), limited if kind == "oversize" else cfg)
        text = str(error.value) + repr(error.value.problems)
        assert not any(needle.decode() in text for needle in fixture_truth.sentinel_needles), kind


def test_reading_logs_counts_and_never_personal_values(cfg, fixture_csv, fixture_truth, caplog):
    with caplog.at_level(logging.DEBUG, logger="latedel"):
        read_source(fixture_csv, cfg)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert f"{fixture_truth.n_lines} lines" in text and f"{fixture_truth.n_orders} orders" in text
    assert not any(needle.decode() in text for needle in fixture_truth.sentinel_needles)


def test_the_file_must_exist(cfg, tmp_path):
    with pytest.raises(FileNotFoundError):
        read_source(tmp_path / "absent.csv", cfg)


# --- the real file (AC06, AC08), marked real_data ------------------------------------------------------------------------


@pytest.mark.real_data
def test_real_header_and_canonical_counts(real_read, project_root):
    frames, report = real_read
    assert report.lines == 180_519 and report.orders == 65_752
    assert report.cancelled_orders == 2_855 and report.cancelled_lines == 7_754 and report.task_orders == 62_897
    assert len(frames.order_lines) == 180_519 and len(frames.orders) == len(frames.outcomes) == 65_752
    assert report.size_bytes == 95_910_149
    assert report.counts["V17"]["post_break_orders"] == 8_403


@pytest.mark.real_data
def test_real_label_definition_and_lag(real_read):
    frames, report = real_read
    outcomes = frames.outcomes.merge(frames.orders[["order_id", "scheduled_days", "order_date"]], on="order_id")
    task = outcomes[~outcomes["is_cancelled"]]
    assert ((task["real_days"] > task["scheduled_days"]).astype(int) == task["source_label"]).all(), "AC08: the label is real days above scheduled days"
    lag = (outcomes["outcome_known_date"] - outcomes["order_date"]).dt.days
    assert report.max_outcome_lag_days == int(lag.max()) == 6
    assert report.first_order_date.isoformat() == "2015-01-01" and report.last_order_date.isoformat() == "2018-01-31"


@pytest.mark.real_data
def test_real_expected_counts_of_the_warning_and_information_rules(real_read):
    _, report = real_read
    counts = report.counts
    assert counts["V10"] == {"orders_off_schedule": 0} and counts["V12"] == {"pairs": 6, "discrepancies": 0} and counts["V13"] == {"discrepancies": 0}
    assert counts["V11"] == {"orders": 2_855, "lines": 7_754}
    assert counts["V15"] == {"columns": 15} and counts["V16"] == {"columns": 7} and counts["V18"] == {"columns": 8}
    assert counts["V14"]["lines"] > 0


@pytest.mark.real_data
def test_real_interim_files_hold_no_personal_column_and_no_row_of_one(real_read, tmp_path):
    frames, report = real_read
    write_interim(frames, report, tmp_path, "fa6d022ed437155e1a2f0378710602848703c8a7f203f7ff5d77805bf8480aa6")
    for path in tmp_path.iterdir():
        blob = path.read_bytes()
        for name in PERSONAL_COLUMNS:
            assert name.encode() not in blob
    names = pd.read_csv(Path(__file__).resolve().parents[2] / "data" / "raw" / "DataCoSupplyChainDataset.csv", encoding="cp1252", dtype=str, usecols=["Customer Fname", "Customer Lname"])
    common = {name for name in names["Customer Fname"].value_counts().index[:5]}
    text = "".join(pq.read_table(p).to_pandas().astype(str).to_csv() for p in tmp_path.iterdir() if p.name.startswith("order"))
    assert not any(f",{first}," in text for first in common), "the most frequent first names of the source appear nowhere in the interim files"


@pytest.mark.real_data
def test_real_cancelled_orders_are_the_same_by_delivery_status_and_by_order_status(real_read):
    frames, _ = real_read
    by_status = frames.outcomes["order_status"].isin(["CANCELED", "SUSPECTED_FRAUD"])
    assert (by_status == frames.outcomes["is_cancelled"]).all() and int(by_status.sum()) == 2_855
