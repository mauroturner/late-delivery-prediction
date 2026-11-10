"""The synthetic fixture (U02): the measured mechanics, the sentinels, the planted signal and one malformed variant per rule (D34).

Everything here reads the fixture with the standard library only, so these tests do not depend on the reader of the project (U03).
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.fixtures.make_fixture import HEADER, MALFORMED_KINDS, MALFORMED_RULES, make_fixture, make_malformed

REAL_HEADER = (
    "Type,Days for shipping (real),Days for shipment (scheduled),Benefit per order,Sales per customer,Delivery Status,Late_delivery_risk,"
    "Category Id,Category Name,Customer City,Customer Country,Customer Email,Customer Fname,Customer Id,Customer Lname,Customer Password,"
    "Customer Segment,Customer State,Customer Street,Customer Zipcode,Department Id,Department Name,Latitude,Longitude,Market,Order City,"
    "Order Country,Order Customer Id,order date (DateOrders),Order Id,Order Item Cardprod Id,Order Item Discount,Order Item Discount Rate,"
    "Order Item Id,Order Item Product Price,Order Item Profit Ratio,Order Item Quantity,Sales,Order Item Total,Order Profit Per Order,"
    "Order Region,Order State,Order Status,Order Zipcode,Product Card Id,Product Category Id,Product Description,Product Image,Product Name,"
    "Product Price,Product Status,shipping date (DateOrders),Shipping Mode"
).split(",")

DATE_FORMAT = "%m/%d/%Y %H:%M"
SCHEDULED = {"Same Day": 0, "First Class": 1, "Second Class": 2, "Standard Class": 4}
BREAK = datetime(2017, 10, 1)
ORDER_LEVEL = [
    "Shipping Mode", "Type", "Market", "Order Region", "Order Country", "Order City", "Order State", "Customer Segment", "Customer Country", "Customer Id",
    "order date (DateOrders)", "shipping date (DateOrders)", "Days for shipment (scheduled)", "Days for shipping (real)", "Late_delivery_risk",
    "Delivery Status", "Order Status", "Latitude", "Longitude", "Order Zipcode",
]
PERSONAL = [
    "Customer Email", "Customer Password", "Customer Fname", "Customer Lname", "Customer Street", "Customer Zipcode", "Customer City", "Customer State",
    "Customer Id", "Order Customer Id", "Order City", "Order State", "Order Zipcode", "Latitude", "Longitude",
]
IDENTICAL_PAIRS = [
    ("Benefit per order", "Order Profit Per Order"), ("Sales per customer", "Order Item Total"), ("Order Item Product Price", "Product Price"),
    ("Product Card Id", "Order Item Cardprod Id"), ("Product Category Id", "Category Id"), ("Order Customer Id", "Customer Id"),
]


def rows_of(path: Path) -> list[dict[str, str]]:
    """Independent reader: cp1252, strict, the csv module, and a check of the 53 fields of every row."""
    text = path.read_bytes().decode("cp1252")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader)
    assert header == REAL_HEADER
    out = []
    for number, fields in enumerate(reader, start=2):
        assert len(fields) == 53, f"line {number} has {len(fields)} fields"
        out.append(dict(zip(header, fields, strict=True)))
    return out


def when(value: str) -> datetime:
    return datetime.strptime(value, DATE_FORMAT)


@pytest.fixture(scope="module")
def truth(tmp_path_factory):
    return make_fixture(tmp_path_factory.mktemp("fx") / "fixture.csv", n_orders=3000, seed=0)


@pytest.fixture(scope="module")
def rows(truth):
    return rows_of(truth.path)


@pytest.fixture(scope="module")
def by_order(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[int(row["Order Id"])].append(row)
    return grouped


# --- the file: format and header -----------------------------------------------------------------------------------


def test_the_header_is_the_one_of_the_source():
    assert list(HEADER) == REAL_HEADER and len(HEADER) == 53


def test_the_file_is_cp1252_with_crlf_and_neither_bom_nor_nul(truth):
    raw = truth.path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\x00" not in raw
    assert raw.count(b"\r\n") == raw.count(b"\n") == truth.n_lines + 1 and raw.endswith(b"\r\n")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")  # like the source, whose first non-ASCII byte makes UTF-8 fail
    assert "México" in raw.decode("cp1252")
    assert truth.size_bytes == len(raw) and truth.sha256 == hashlib.sha256(raw).hexdigest()


# --- the truth agrees with the file ---------------------------------------------------------------------------------


def test_truth_counts_match_the_csv_and_the_mechanics_hold(truth, rows, by_order):
    assert truth.n_lines == len(rows) and truth.n_orders == len(by_order) == 3000
    assert set(by_order) == set(truth.orders)
    assert Counter(len(v) for v in by_order.values()) == Counter(o.n_lines for o in truth.orders.values())
    assert len({row["Order Item Id"] for row in rows}) == len(rows), "the line key is unique"

    cancelled = {oid for oid, lines in by_order.items() if lines[0]["Delivery Status"] == "Shipping canceled"}
    assert cancelled == {oid for oid, o in truth.orders.items() if o.cancelled}
    assert len(cancelled) == truth.cancelled_orders == round(0.04 * 3000)
    assert truth.cancelled_lines == sum(len(by_order[o]) for o in cancelled)

    start, end = datetime(2015, 1, 1), datetime(2018, 2, 1)
    for oid, lines in by_order.items():
        first, expected = lines[0], truth.orders[oid]
        for column in ORDER_LEVEL:
            assert len({line[column] for line in lines}) == 1, f"order {oid}: {column} changes inside the order"
        ordered, shipped = when(first["order date (DateOrders)"]), when(first["shipping date (DateOrders)"])
        assert start <= ordered < end
        mode = first["Shipping Mode"]
        delta = shipped - ordered
        if mode == "Same Day":
            assert delta == timedelta(hours=12)
        elif mode == "First Class":
            assert delta == timedelta(hours=48)
        else:
            assert mode in ("Second Class", "Standard Class") and delta in {timedelta(hours=24 * k) for k in range(2, 7)}
        real = (shipped.date() - ordered.date()).days
        assert int(first["Days for shipping (real)"]) == real == expected.real_days
        assert int(first["Days for shipment (scheduled)"]) == SCHEDULED[mode] == expected.scheduled_days
        label = int(first["Late_delivery_risk"])
        if oid in cancelled:
            assert label == 0 and expected.late is None and first["Order Status"] in ("CANCELED", "SUSPECTED_FRAUD")
        else:
            assert label == int(real > SCHEDULED[mode]) and expected.late is bool(label)
            status = first["Delivery Status"]
            assert status == ("Late delivery" if real > SCHEDULED[mode] else "Advance shipping" if real < SCHEDULED[mode] else "Shipping on time")
            assert first["Order Status"] not in ("CANCELED", "SUSPECTED_FRAUD")
        # lines per order: 1 to 5 before October 2017, almost always 1 after
        if ordered < BREAK:
            assert 1 <= len(lines) <= 5 and expected.regime == "pre_break"
        else:
            assert len(lines) <= 2 and expected.regime == "post_break"


def test_the_label_rates_follow_the_measured_mechanics(truth, by_order):
    """Rates of the source's training block (requirements 4): First 1.0, Second 0.80, Same Day 0.47 (the noon cut), Standard 0.40."""
    late, total = Counter(), Counter()
    for oid, order in truth.orders.items():
        if order.cancelled:
            continue
        total[order.mode] += 1
        late[order.mode] += order.late
    rates = {mode: late[mode] / total[mode] for mode in total}
    assert rates["First Class"] == 1.0
    assert rates["Second Class"] == pytest.approx(0.80, abs=0.04)
    assert rates["Standard Class"] == pytest.approx(0.40, abs=0.03)
    assert rates["Same Day"] == pytest.approx(0.50, abs=0.12)
    share = {mode: total[mode] / sum(total.values()) for mode in total}
    assert share["Standard Class"] == pytest.approx(0.597, abs=0.04) and share["Same Day"] == pytest.approx(0.054, abs=0.03)


def test_the_noon_cut_of_same_day_has_no_exception(truth, by_order):
    before, from_noon = 0, 0
    for oid, order in truth.orders.items():
        if order.mode != "Same Day":
            continue
        hour = when(by_order[oid][0]["order date (DateOrders)"]).hour
        real = order.real_days
        assert real == (0 if hour < 12 else 1), f"order {oid} at hour {hour}"
        before += hour < 12
        from_noon += hour >= 12
    assert before > 20 and from_noon > 20
    assert truth.cases["same_day_before_noon_order_id"] in truth.orders and truth.cases["same_day_from_noon_order_id"] in truth.orders
    assert truth.orders[truth.cases["same_day_before_noon_order_id"]].late is False
    assert truth.orders[truth.cases["same_day_from_noon_order_id"]].late is True


def test_columns_that_repeat_are_identical_and_prices_add_up(rows):
    for a, b in IDENTICAL_PAIRS:
        assert all(row[a] == row[b] for row in rows), f"{a} differs from {b}"
    for row in rows:
        price, quantity, sales = float(row["Product Price"]), int(row["Order Item Quantity"]), float(row["Sales"])
        assert 1 <= quantity <= 5 and sales == pytest.approx(price * quantity, abs=0.005)
        assert float(row["Order Item Total"]) == pytest.approx(sales - float(row["Order Item Discount"]), abs=0.005)
    assert {row["Product Status"] for row in rows} == {"0"} and {row["Product Description"] for row in rows} == {""}


def test_email_and_password_are_masked_and_the_other_personal_columns_hold_sentinels(truth, rows):
    assert {row["Customer Email"] for row in rows} == {"XXXXXXXXX"} and {row["Customer Password"] for row in rows} == {"XXXXXXXXX"}
    assert {row["Latitude"] for row in rows} == {"77.777777"} and {row["Longitude"] for row in rows} == {"-77.777777"}
    for column, prefix in (("Customer Fname", "ZZSENTINELNAME"), ("Customer Street", "ZZSENTINEL STREET "), ("Customer Zipcode", "ZZ"), ("Customer City", "ZZ"),
                           ("Customer State", "ZZ"), ("Order City", "ZZ"), ("Order State", "ZZ")):
        assert all(row[column].startswith(prefix) for row in rows), column
    assert all(row["Customer Lname"].startswith("ZZSENTINELLAST") or row["Customer Lname"] == "" for row in rows)
    assert all(row["Order Zipcode"] == "" or row["Order Zipcode"].startswith("ZZ") for row in rows)
    assert 0.05 < sum(row["Order Zipcode"] != "" for row in rows) / len(rows) < 0.30, "mostly empty, like the source"
    assert all(int(row["Customer Id"]) >= 9_100_000 for row in rows)


def test_sentinels_do_not_collide_with_order_or_line_identifiers(truth, rows):
    customers = {int(row["Customer Id"]) for row in rows}
    orders = {int(row["Order Id"]) for row in rows}
    lines = {int(row["Order Item Id"]) for row in rows}
    assert not customers & orders and not customers & lines and not orders & lines
    assert max(orders) < 9_100_000 and max(lines) < 9_100_000
    assert truth.sentinel_customer_ids == frozenset(customers)
    raw = truth.path.read_bytes()
    assert truth.sentinel_needles and all(needle in raw for needle in truth.sentinel_needles)
    assert b"ZZSENTINEL" in truth.sentinel_needles and b"77.777777" in truth.sentinel_needles
    # no sentinel text sits in a column that the project keeps
    keep = ["Type", "Market", "Order Region", "Order Country", "Customer Segment", "Customer Country", "Category Name", "Department Name", "Product Name", "Shipping Mode"]
    for row in rows:
        for column in keep:
            assert "ZZSENTINEL" not in row[column] and "77.777777" not in row[column], column


def test_special_products_and_their_cases(truth, rows):
    by_line = {int(row["Order Item Id"]): row for row in rows}
    script = by_line[truth.cases["script_product_line_id"]]
    assert script["Product Name"] == "<script>alert(1)</script>"
    comma = by_line[truth.cases["comma_quote_line_id"]]
    assert comma["Product Name"] == 'Bag, "Large" Duffel'
    trailing = [by_line[i] for i in truth.cases["trailing_space_line_ids"]]
    assert trailing and all(row["Product Name"].endswith(" ") and row["Product Name"].strip() != row["Product Name"] for row in trailing)
    assert sum(row["Product Name"] != row["Product Name"].rstrip() for row in rows) == len(truth.cases["trailing_space_line_ids"])
    assert truth.expected["V14"]["lines"] == len(trailing)
    names = {row["Product Name"] for row in rows}
    assert len(names) >= 20 and all(row["Product Image"].startswith("http://") for row in rows)


def test_expected_counts_of_each_rule(truth, rows, by_order):
    expected = truth.expected
    assert expected["V10"] == {"orders_off_schedule": 0}
    assert expected["V11"] == {"orders": truth.cancelled_orders, "lines": truth.cancelled_lines}
    assert expected["V12"] == {"pairs": 6, "discrepancies": 0}
    assert expected["V13"] == {"discrepancies": 0}
    assert expected["V15"] == {"columns": 15} and expected["V16"] == {"columns": 7} and expected["V18"] == {"columns": 8}
    post = sum(1 for oid, lines in by_order.items() if when(lines[0]["order date (DateOrders)"]) >= BREAK)
    assert expected["V17"]["post_break_orders"] == post > 50
    assert expected["rows"] == {
        "lines": len(rows), "orders": len(by_order), "cancelled_orders": truth.cancelled_orders,
        "cancelled_lines": truth.cancelled_lines, "task_orders": len(by_order) - truth.cancelled_orders,
    }
    multi = truth.cases["multi_line_order_id"]
    assert len(by_order[multi]) >= 2 and truth.cases["post_break_order_id"] in by_order
    assert truth.orders[truth.cases["cancelled_order_id"]].cancelled


def test_the_calendar_covers_the_whole_period(truth, by_order):
    dates = sorted(when(lines[0]["order date (DateOrders)"]) for lines in by_order.values())
    assert dates[0] < datetime(2015, 1, 8) and dates[-1] > datetime(2018, 1, 24)
    months = {(d.year, d.month) for d in dates}
    assert len(months) == 37, "every month from 2015-01 to 2018-01 has orders"


# --- determinism and arguments --------------------------------------------------------------------------------------


def test_same_seed_same_bytes_and_other_seed_other_bytes(tmp_path):
    a = make_fixture(tmp_path / "a.csv", n_orders=400, seed=3)
    b = make_fixture(tmp_path / "b.csv", n_orders=400, seed=3)
    c = make_fixture(tmp_path / "c.csv", n_orders=400, seed=4)
    assert a.path.read_bytes() == b.path.read_bytes() and a.sha256 == b.sha256
    assert a.sha256 != c.sha256
    assert a.orders == b.orders and a.cases == b.cases and a.expected == b.expected


def test_arguments_are_checked_before_writing(tmp_path):
    for kwargs in ({"n_orders": 99}, {"n_orders": 0}, {"signal": "everywhere"}, {"seed": -1}):
        with pytest.raises(ValueError):
            make_fixture(tmp_path / "x.csv", **kwargs)
        assert not (tmp_path / "x.csv").exists()


def test_the_output_folder_is_created_and_the_file_is_the_only_thing_written(tmp_path):
    make_fixture(tmp_path / "deep" / "er" / "f.csv", n_orders=100, seed=1)
    assert [p.name for p in (tmp_path / "deep" / "er").iterdir()] == ["f.csv"]


# --- the planted signal (positive control) ---------------------------------------------------------------------------


def test_standard_market_signal_raises_the_late_rate_of_one_market_only(tmp_path):
    none = make_fixture(tmp_path / "n.csv", n_orders=6000, seed=0, signal="none")
    planted = make_fixture(tmp_path / "s.csv", n_orders=6000, seed=0, signal="standard_market")

    def rate(truth, in_market: bool) -> float:
        picked = [o for o in truth.orders.values() if o.mode == "Standard Class" and not o.cancelled and (o.market == "LATAM") == in_market]
        return sum(o.late for o in picked) / len(picked)

    assert rate(none, True) == pytest.approx(0.40, abs=0.05) and rate(none, False) == pytest.approx(0.40, abs=0.05)
    assert rate(planted, True) == pytest.approx(0.70, abs=0.05)
    assert rate(planted, False) == pytest.approx(0.40, abs=0.05)
    # the mechanics still hold with the signal: real days are the calendar difference and the label follows them
    for order in planted.orders.values():
        if order.mode == "Standard Class" and not order.cancelled:
            assert order.late == (order.real_days > 4)
    other = [o for o in planted.orders.values() if o.mode != "Standard Class"]
    assert other and all(o.market in {"Europe", "LATAM", "Africa", "Pacific Asia", "USCA"} for o in other)


# --- malformed variants: one per rule that blocks ---------------------------------------------------------------------


def test_every_malformed_kind_has_its_rule_and_the_list_is_the_plans():
    assert set(MALFORMED_KINDS) == {
        "wrong_hash", "extra_column", "missing_column", "renamed_column", "short_row", "nul_byte", "bom", "invalid_cp1252_byte", "bad_date",
        "unmasked_email", "unmasked_password", "two_modes_one_order", "duplicate_item_id", "label_mismatch", "outcome_after_7_days", "oversize",
    }
    assert {kind: MALFORMED_RULES[kind] for kind in MALFORMED_KINDS} == {
        "wrong_hash": "V01", "oversize": "V02", "nul_byte": "V03", "bom": "V03", "invalid_cp1252_byte": "V03",
        "extra_column": "V04", "missing_column": "V04", "renamed_column": "V04", "short_row": "V05", "bad_date": "V05",
        "unmasked_email": "V06", "unmasked_password": "V06", "two_modes_one_order": "V07", "duplicate_item_id": "V07",
        "label_mismatch": "V08", "outcome_after_7_days": "V09",
    }


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return make_fixture(tmp_path_factory.mktemp("base") / "base.csv", n_orders=300, seed=7)


def variant(base, kind, tmp_path) -> Path:
    before = base.path.read_bytes()
    out = make_malformed(base.path, kind, tmp_path / f"{kind}.csv")
    assert out == tmp_path / f"{kind}.csv" and out.exists()
    assert base.path.read_bytes() == before, "the base file is never modified"
    assert out.read_bytes() != before
    return out


def test_unknown_malformed_kind_is_refused(base, tmp_path):
    with pytest.raises(ValueError, match="nonsense"):
        make_malformed(base.path, "nonsense", tmp_path / "x.csv")
    assert not (tmp_path / "x.csv").exists()


def test_wrong_hash_stays_valid_in_every_other_way(base, tmp_path):
    out = variant(base, "wrong_hash", tmp_path)
    assert hashlib.sha256(out.read_bytes()).hexdigest() != base.sha256
    assert len(rows_of(out)) == base.n_lines and out.stat().st_size >= base.size_bytes - 20


def test_extra_missing_and_renamed_columns_change_the_header_only_as_named(base, tmp_path):
    for kind, check in (
        ("extra_column", lambda header: len(header) == 54 and header[:53] == REAL_HEADER),
        ("missing_column", lambda header: header == REAL_HEADER[:-1]),
        ("renamed_column", lambda header: len(header) == 53 and sum(a != b for a, b in zip(header, REAL_HEADER, strict=True)) == 1),
    ):
        out = variant(base, kind, tmp_path)
        reader = csv.reader(io.StringIO(out.read_bytes().decode("cp1252"), newline=""))
        header = next(reader)
        assert check(header), kind
        widths = Counter(len(r) for r in reader)
        assert widths == {len(header): base.n_lines}, f"{kind}: every row follows the changed header"


def test_short_row_has_exactly_one_row_of_52_fields(base, tmp_path):
    out = variant(base, "short_row", tmp_path)
    reader = csv.reader(io.StringIO(out.read_bytes().decode("cp1252"), newline=""))
    next(reader)
    assert Counter(len(r) for r in reader) == {53: base.n_lines - 1, 52: 1}


def test_nul_bom_and_invalid_byte_are_single_byte_level_defects(base, tmp_path):
    original = base.path.read_bytes()
    nul = variant(base, "nul_byte", tmp_path).read_bytes()
    assert nul.count(b"\x00") == 1 and len(nul) == len(original) + 1
    bom = variant(base, "bom", tmp_path).read_bytes()
    assert bom == b"\xef\xbb\xbf" + original
    bad = variant(base, "invalid_cp1252_byte", tmp_path).read_bytes()
    assert len(bad) == len(original)
    with pytest.raises(UnicodeDecodeError):
        bad.decode("cp1252")
    assert sum(a != b for a, b in zip(bad, original, strict=True)) == 1


def test_bad_date_has_one_date_that_does_not_convert(base, tmp_path):
    out = variant(base, "bad_date", tmp_path)
    bad = 0
    for row in rows_of(out):
        for column in ("order date (DateOrders)", "shipping date (DateOrders)"):
            try:
                when(row[column])
            except ValueError:
                bad += 1
    assert bad == 1


def test_unmasked_email_and_password_change_one_row_each(base, tmp_path):
    email = rows_of(variant(base, "unmasked_email", tmp_path))
    assert sum(row["Customer Email"] != "XXXXXXXXX" for row in email) == 1 and all(row["Customer Password"] == "XXXXXXXXX" for row in email)
    password = rows_of(variant(base, "unmasked_password", tmp_path))
    assert sum(row["Customer Password"] != "XXXXXXXXX" for row in password) == 1 and all(row["Customer Email"] == "XXXXXXXXX" for row in password)


def test_two_modes_in_one_order_and_a_repeated_item_id_are_single_defects(base, tmp_path):
    grouped = defaultdict(set)
    for row in rows_of(variant(base, "two_modes_one_order", tmp_path)):
        grouped[row["Order Id"]].add(row["Shipping Mode"])
    assert [oid for oid, modes in grouped.items() if len(modes) > 1] and sum(len(m) > 1 for m in grouped.values()) == 1
    ids = Counter(row["Order Item Id"] for row in rows_of(variant(base, "duplicate_item_id", tmp_path)))
    assert list(ids.values()).count(2) == 1 and max(ids.values()) == 2


def test_label_mismatch_flips_one_orders_label_and_nothing_else(base, tmp_path):
    wrong = []
    grouped = defaultdict(list)
    for row in rows_of(variant(base, "label_mismatch", tmp_path)):
        grouped[row["Order Id"]].append(row)
    for oid, lines in grouped.items():
        first = lines[0]
        if first["Delivery Status"] == "Shipping canceled":
            continue
        if int(first["Late_delivery_risk"]) != int(int(first["Days for shipping (real)"]) > int(first["Days for shipment (scheduled)"])):
            wrong.append(oid)
            assert len({line["Late_delivery_risk"] for line in lines}) == 1, "the whole order carries the wrong label"
    assert len(wrong) == 1


def test_outcome_after_7_days_keeps_the_label_consistent(base, tmp_path):
    lags = []
    for row in rows_of(variant(base, "outcome_after_7_days", tmp_path)):
        ordered, shipped = when(row["order date (DateOrders)"]), when(row["shipping date (DateOrders)"])
        real = (shipped.date() - ordered.date()).days
        assert int(row["Days for shipping (real)"]) == real
        if row["Delivery Status"] != "Shipping canceled":
            assert int(row["Late_delivery_risk"]) == int(real > int(row["Days for shipment (scheduled)"]))
        lags.append(real)
    assert sorted(lags)[-1] >= 7 and sum(lag >= 7 for lag in lags) >= 1
    assert len({(r["Order Id"]) for r in rows_of(variant(base, "outcome_after_7_days", tmp_path)) if int(r["Days for shipping (real)"]) >= 7}) == 1


def test_oversize_adds_more_than_a_mebibyte_and_stays_otherwise_valid(base, tmp_path):
    out = variant(base, "oversize", tmp_path)
    assert out.stat().st_size >= base.size_bytes + 1024 * 1024
    assert len(rows_of(out)) == base.n_lines


@pytest.mark.real_data
def test_the_header_of_the_fixture_is_the_header_of_the_real_file(project_root):
    path = project_root / "data" / "raw" / "DataCoSupplyChainDataset.csv"
    with path.open("rb") as handle:
        first = handle.readline().decode("cp1252").rstrip("\r\n")
    assert first.split(",") == list(HEADER)
