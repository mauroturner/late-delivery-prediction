"""The synthetic fixture: a CSV with the 53 columns of the DataCo file, the mechanics measured on it, planted sentinels and malformed variants (D34).

Nothing here comes from the real file. Every name, street, zip code, coordinate and customer identifier in the personal columns is a sentinel
that cannot be mistaken for a real value: names are ``ZZSENTINEL...``, the coordinates are 77.777777 and -77.777777, and customer identifiers start at
9,100,000, a range that order identifiers (from 10,000) and line identifiers (from 1,000,000) never reach. A test that looks for those bytes in the
files of the pipeline therefore proves that a personal column did not travel, instead of proving that a column name is absent.

The mechanics are those measured on the training block of the source (requirements.md, section 4):

- the shipping timestamp is the order timestamp plus exactly 12 h (Same Day), 48 h (First Class), or 48 to 144 h in steps of 24 h (Second and Standard Class);
- the real days are the difference in calendar days between the shipping date and the order date, so a Same Day order placed before 12:00 arrives the
  same day (on time) and one placed from 12:00 arrives the next day (late);
- the scheduled days are 0, 1, 2 and 4 by mode and the label is "real days above scheduled days";
- 1 to 5 lines per order before October 2017 and almost always one afterwards; 4 % of the orders are cancelled as a whole.

``signal="standard_market"`` plants one effect that the order-time columns can find: Standard Class orders of the LATAM market are late with probability
0.7 instead of 0.4 (the positive control of AC17). The random stream is the same with and without it, so the two fixtures differ only in that effect.
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

HEADER: tuple[str, ...] = tuple((
    "Type,Days for shipping (real),Days for shipment (scheduled),Benefit per order,Sales per customer,Delivery Status,Late_delivery_risk,"
    "Category Id,Category Name,Customer City,Customer Country,Customer Email,Customer Fname,Customer Id,Customer Lname,Customer Password,"
    "Customer Segment,Customer State,Customer Street,Customer Zipcode,Department Id,Department Name,Latitude,Longitude,Market,Order City,"
    "Order Country,Order Customer Id,order date (DateOrders),Order Id,Order Item Cardprod Id,Order Item Discount,Order Item Discount Rate,"
    "Order Item Id,Order Item Product Price,Order Item Profit Ratio,Order Item Quantity,Sales,Order Item Total,Order Profit Per Order,"
    "Order Region,Order State,Order Status,Order Zipcode,Product Card Id,Product Category Id,Product Description,Product Image,Product Name,"
    "Product Price,Product Status,shipping date (DateOrders),Shipping Mode"
).split(","))

ENCODING = "cp1252"
FIRST_DAY = date(2015, 1, 1)
LAST_DAY = date(2018, 1, 31)
BREAK_DATE = date(2017, 10, 1)
FIRST_ORDER_ID = 10_000
FIRST_LINE_ID = 1_000_000
FIRST_CUSTOMER_ID = 9_100_000
MAX_ORDERS = 500_000
MIN_ORDERS = 100
CANCELLED_SHARE = 0.04

MODES = ("Standard Class", "Second Class", "First Class", "Same Day")
MODE_WEIGHTS = (0.597, 0.195, 0.154, 0.054)
SCHEDULED_DAYS = {"Same Day": 0, "First Class": 1, "Second Class": 2, "Standard Class": 4}
PAYMENT_TYPES = ("DEBIT", "TRANSFER", "PAYMENT", "CASH")
OPEN_ORDER_STATUSES = ("COMPLETE", "CLOSED", "PENDING", "PENDING_PAYMENT", "PROCESSING", "ON_HOLD", "PAYMENT_REVIEW")
CANCELLED_ORDER_STATUSES = ("CANCELED", "SUSPECTED_FRAUD")
DISCOUNT_HUNDREDTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 22, 25)
SIGNALS = ("none", "standard_market")
SIGNAL_MARKET = "LATAM"
SIGNAL_LATE_RATE = 0.7

# region -> (market, countries); some names carry accents, so the file is not valid UTF-8, like the source
REGIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Western Europe": ("Europe", ("Francia", "Alemania")),
    "Central America": ("LATAM", ("México", "Honduras")),
    "South America": ("LATAM", ("Perú", "Brasil")),
    "Caribbean": ("LATAM", ("Cuba",)),
    "West Africa": ("Africa", ("Nigeria", "Ghana")),
    "Southeast Asia": ("Pacific Asia", ("Indonesia", "Tailandia")),
    "Oceania": ("Pacific Asia", ("Australia",)),
    "US Center": ("USCA", ("Estados Unidos",)),
}
CATEGORIES = (
    (73, "Sporting Goods", 2, "Fitness"), (17, "Cleats", 3, "Footwear"), (29, "Shop By Sport", 4, "Golf"),
    (24, "Women's Apparel", 5, "Apparel"), (46, "Indoor/Outdoor Games", 6, "Outdoors"), (9, "Cardio Equipment", 7, "Fan Shop"),
)
PRICES_CENTS = (999, 1599, 2499, 3999, 5999, 7999, 12999, 19999, 32775, 49999)

SCRIPT_NAME = "<script>alert(1)</script>"
TRAILING_NAME = "Fixture Trailing Product "
COMMA_NAME = 'Bag, "Large" Duffel'

SENTINEL_NEEDLES: tuple[bytes, ...] = (
    b"ZZSENTINEL", b"ZZSENTINELNAME", b"ZZSENTINELLAST", b"ZZSENTINEL STREET ", b"ZZSENTINELZIP", b"ZZSENTINELCITY", b"ZZSENTINELSTATE",
    b"ZZSENTINELOCITY", b"ZZSENTINELOSTATE", b"ZZSENTINELOZIP", b"77.777777", b"-77.777777",
)


@dataclass(frozen=True)
class OrderTruth:
    """What the generator decided for one order (the file holds the same, which the tests read independently)."""

    order_id: int
    order_ts: str            # ISO, minute precision, no zone
    shipping_ts: str
    mode: str
    scheduled_days: int
    real_days: int
    late: bool | None        # None for a cancelled order, which has no label of the task (D05)
    cancelled: bool
    n_lines: int
    market: str
    regime: str              # "pre_break" before 2017-10-01, "post_break" from then on
    customer_id: int


@dataclass(frozen=True)
class FixtureTruth:
    """The fixture and what is in it: the path, the counts, each order, where each special case is and the count each rule must find."""

    path: Path
    seed: int
    signal: str
    n_orders: int
    n_lines: int
    n_customers: int
    cancelled_orders: int
    cancelled_lines: int
    size_bytes: int
    sha256: str
    orders: dict[int, OrderTruth]
    cases: dict[str, object]
    expected: dict[str, dict[str, int]]
    sentinel_needles: tuple[bytes, ...]
    sentinel_customer_ids: frozenset[int]


def _money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, rest = divmod(abs(cents), 100)
    return f"{sign}{whole}.{rest:02d}"


def _stamp(moment: datetime) -> str:
    """The source's format: month, day and hour without padding (``1/31/2018 22:56``)."""
    return f"{moment.month}/{moment.day}/{moment.year} {moment.hour}:{moment.minute:02d}"


def _catalogue() -> list[tuple[int, str, int, str, int, str, int]]:
    """Thirty products: card id, name, category id and name, department id and name, price in cents. 0, 1 and 2 are the special ones."""
    names = [SCRIPT_NAME, TRAILING_NAME, COMMA_NAME] + [f"Fixture Product {n:02d}" for n in range(27)]
    products = []
    for index, name in enumerate(names):
        category_id, category, department_id, department = CATEGORIES[index % len(CATEGORIES)]
        products.append((1000 + index, name, category_id, category, department_id, department, PRICES_CENTS[index % len(PRICES_CENTS)]))
    return products


def _image(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "+" for ch in name.strip()).strip("+")
    return f"http://images.example.invalid/{slug}" + (" " if name != name.rstrip() else "")


def make_fixture(path: Path, n_orders: int = 3000, seed: int = 0, signal: str = "none") -> FixtureTruth:
    """Write the fixture CSV to `path` (cp1252, CRLF, the 53 columns) and return what is in it.

    The same arguments always give the same bytes. `n_orders` is at least 100 so that every special case exists.
    """
    if not isinstance(n_orders, int) or not MIN_ORDERS <= n_orders <= MAX_ORDERS:
        raise ValueError(f"n_orders: se esperaba un entero entre {MIN_ORDERS} y {MAX_ORDERS}, se recibió {n_orders!r}")
    if not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed: se esperaba un entero de 0 o más, se recibió {seed!r}")
    if signal not in SIGNALS:
        raise ValueError(f"signal: se esperaba uno de {SIGNALS}, se recibió {signal!r}")
    path = Path(path)

    rng = random.Random(seed)
    products = _catalogue()
    weights = [0.3, 0.3, 0.3] + [1.0] * 27
    regions = list(REGIONS)
    n_customers = max(10, n_orders // 3)
    customer_segment = [rng.choice(("Consumer", "Corporate", "Home Office")) for _ in range(n_customers)]
    customer_country = [rng.choice(("EE. UU.", "Puerto Rico")) for _ in range(n_customers)]
    n_days = (LAST_DAY - FIRST_DAY).days + 1
    post_break_start = (BREAK_DATE - FIRST_DAY).days

    # forced cases (indices 0 to 10), so that every special case exists whatever `n_orders` is
    forced_days = {0: 0, 1: n_days - 1, 7: post_break_start + 4, 8: 400}
    forced_modes = {2: "Same Day", 3: "Same Day", 4: "First Class", 5: "Second Class", 6: "Standard Class"}
    forced_hours = {2: 9, 3: 15}
    cancelled_index = set(rng.sample(range(11, n_orders), round(CANCELLED_SHARE * n_orders)))

    drafts = []
    for i in range(n_orders):
        day = forced_days.get(i, rng.randrange(n_days))
        hour, minute = rng.randrange(24), rng.randrange(60)
        hour = forced_hours.get(i, hour)
        mode = rng.choices(MODES, MODE_WEIGHTS)[0]
        mode = forced_modes.get(i, mode)
        region = rng.choice(regions)
        country = rng.choice(REGIONS[region][1])
        # the same draws whatever the signal, so that two fixtures of one seed differ only in the planted effect
        plain, high, low, pick = rng.randrange(2, 7), rng.choice((5, 6)), rng.choice((2, 3, 4)), rng.random()
        lines_pre, lines_post = rng.randint(1, 5), 1 + (rng.random() < 0.01)
        open_status, closed_status = rng.choice(OPEN_ORDER_STATUSES), rng.choice(CANCELLED_ORDER_STATUSES)
        payment, customer, zip_draw = rng.choice(PAYMENT_TYPES), rng.randrange(n_customers), rng.random()
        moment = datetime.combine(FIRST_DAY + timedelta(days=day), datetime.min.time()).replace(hour=hour, minute=minute)
        post_break = moment.date() >= BREAK_DATE
        n_lines = lines_post if post_break else lines_pre
        if i == 8:
            n_lines = max(n_lines, 3)
        if mode == "Same Day":
            shift = 12
        elif mode == "First Class":
            shift = 48
        else:
            k = plain
            if signal == "standard_market" and mode == "Standard Class" and REGIONS[region][0] == SIGNAL_MARKET:
                k = high if pick < SIGNAL_LATE_RATE else low
            shift = 24 * k
        drafts.append({
            "index": i, "moment": moment, "mode": mode, "region": region, "country": country, "shift": shift, "n_lines": n_lines,
            "status": closed_status if i in cancelled_index else open_status, "cancelled": i in cancelled_index, "payment": payment,
            "customer": customer, "has_zip": zip_draw < 0.14,
        })

    drafts.sort(key=lambda d: (d["moment"], d["index"]))
    for rank, draft in enumerate(drafts):
        draft["order_id"] = FIRST_ORDER_ID + rank
    by_index = {d["index"]: d for d in drafts}

    rows: list[list[str]] = []
    orders: dict[int, OrderTruth] = {}
    cases: dict[str, object] = {"trailing_space_line_ids": []}
    trailing_lines = 0
    line_id = FIRST_LINE_ID
    cancelled_lines = 0
    for draft in drafts:
        order_id, moment, mode = draft["order_id"], draft["moment"], draft["mode"]
        shipped = moment + timedelta(hours=draft["shift"])
        real = (shipped.date() - moment.date()).days
        scheduled = SCHEDULED_DAYS[mode]
        late = real > scheduled
        market = REGIONS[draft["region"]][0]
        customer = draft["customer"]
        customer_id = FIRST_CUSTOMER_ID + customer
        if draft["cancelled"]:
            label, delivery = 0, "Shipping canceled"
        else:
            label = int(late)
            delivery = "Late delivery" if real > scheduled else "Advance shipping" if real < scheduled else "Shipping on time"
        orders[order_id] = OrderTruth(
            order_id=order_id, order_ts=moment.isoformat(timespec="minutes"), shipping_ts=shipped.isoformat(timespec="minutes"), mode=mode,
            scheduled_days=scheduled, real_days=real, late=None if draft["cancelled"] else late, cancelled=draft["cancelled"], n_lines=draft["n_lines"],
            market=market, regime="post_break" if moment.date() >= BREAK_DATE else "pre_break", customer_id=customer_id,
        )
        lname = "" if customer % 97 == 0 else f"ZZSENTINELLAST{customer:05d}"
        order_zip = f"ZZSENTINELOZIP{order_id}" if draft["has_zip"] else ""
        for position in range(draft["n_lines"]):
            forced = {0: 0, 10: 2}.get(draft["index"]) if position == 0 else None
            pick = forced if forced is not None else rng.choices(range(len(products)), weights)[0]
            card, name, category_id, category, department_id, department, price = products[pick]
            quantity, rate, profit = rng.randint(1, 5), rng.choice(DISCOUNT_HUNDREDTHS), rng.randint(-30000, 12000)
            sales = price * quantity
            discount = (sales * rate + 50) // 100
            total = sales - discount
            ratio = f"{profit / sales:.2f}"
            row = {
                "Type": draft["payment"], "Days for shipping (real)": str(real), "Days for shipment (scheduled)": str(scheduled),
                "Benefit per order": _money(profit), "Sales per customer": _money(total), "Delivery Status": delivery, "Late_delivery_risk": str(label),
                "Category Id": str(category_id), "Category Name": category, "Customer City": f"ZZSENTINELCITY{customer:05d}",
                "Customer Country": customer_country[customer], "Customer Email": "XXXXXXXXX", "Customer Fname": f"ZZSENTINELNAME{customer:05d}",
                "Customer Id": str(customer_id), "Customer Lname": lname, "Customer Password": "XXXXXXXXX",
                "Customer Segment": customer_segment[customer], "Customer State": f"ZZSENTINELSTATE{customer:05d}",
                "Customer Street": f"ZZSENTINEL STREET {customer:05d}", "Customer Zipcode": f"ZZSENTINELZIP{customer:05d}",
                "Department Id": str(department_id), "Department Name": department, "Latitude": "77.777777", "Longitude": "-77.777777", "Market": market,
                "Order City": f"ZZSENTINELOCITY{order_id}", "Order Country": draft["country"], "Order Customer Id": str(customer_id),
                "order date (DateOrders)": _stamp(moment), "Order Id": str(order_id), "Order Item Cardprod Id": str(card),
                "Order Item Discount": _money(discount), "Order Item Discount Rate": f"{rate / 100:.2f}", "Order Item Id": str(line_id),
                "Order Item Product Price": _money(price), "Order Item Profit Ratio": ratio, "Order Item Quantity": str(quantity), "Sales": _money(sales),
                "Order Item Total": _money(total), "Order Profit Per Order": _money(profit), "Order Region": draft["region"],
                "Order State": f"ZZSENTINELOSTATE{order_id}", "Order Status": draft["status"], "Order Zipcode": order_zip, "Product Card Id": str(card),
                "Product Category Id": str(category_id), "Product Description": "", "Product Image": _image(name), "Product Name": name,
                "Product Price": _money(price), "Product Status": "0", "shipping date (DateOrders)": _stamp(shipped), "Shipping Mode": mode,
            }
            rows.append([row[column] for column in HEADER])
            if pick == 0:
                cases.setdefault("script_product_line_id", line_id)
                cases.setdefault("script_product_order_id", order_id)
            elif pick == 2:
                cases.setdefault("comma_quote_line_id", line_id)
            if name != name.rstrip():
                cases["trailing_space_line_ids"].append(line_id)  # type: ignore[union-attr]
                trailing_lines += 1
            if draft["cancelled"]:
                cancelled_lines += 1
            line_id += 1

    cases["same_day_before_noon_order_id"] = by_index[2]["order_id"]
    cases["same_day_from_noon_order_id"] = by_index[3]["order_id"]
    cases["post_break_order_id"] = by_index[7]["order_id"]
    cases["multi_line_order_id"] = by_index[8]["order_id"]
    cases["cancelled_order_id"] = min(o.order_id for o in orders.values() if o.cancelled)

    rows.reverse()  # newest order first, like the source; the lines of one order keep ascending item numbers
    ordered_rows: list[list[str]] = []
    position = 0
    order_column, item_column = HEADER.index("Order Id"), HEADER.index("Order Item Id")
    while position < len(rows):
        end = position
        while end < len(rows) and rows[end][order_column] == rows[position][order_column]:
            end += 1
        ordered_rows.extend(sorted(rows[position:end], key=lambda r: int(r[item_column])))
        position = end

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADER)
    writer.writerows(ordered_rows)
    payload = buffer.getvalue().encode(ENCODING)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

    n_cancelled = sum(1 for o in orders.values() if o.cancelled)
    expected = {
        "rows": {"lines": len(ordered_rows), "orders": len(orders), "cancelled_orders": n_cancelled, "cancelled_lines": cancelled_lines,
                 "task_orders": len(orders) - n_cancelled},
        "V10": {"orders_off_schedule": 0},
        "V11": {"orders": n_cancelled, "lines": cancelled_lines},
        "V12": {"pairs": 6, "discrepancies": 0},
        "V13": {"discrepancies": 0},
        "V14": {"lines": trailing_lines},
        "V15": {"columns": 15},
        "V16": {"columns": 7},
        "V17": {"post_break_orders": sum(1 for o in orders.values() if o.regime == "post_break")},
        "V18": {"columns": 8},
    }
    return FixtureTruth(
        path=path, seed=seed, signal=signal, n_orders=len(orders), n_lines=len(ordered_rows), n_customers=n_customers, cancelled_orders=n_cancelled,
        cancelled_lines=cancelled_lines, size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(), orders=orders, cases=cases, expected=expected,
        sentinel_needles=SENTINEL_NEEDLES, sentinel_customer_ids=frozenset(o.customer_id for o in orders.values()),
    )


# --- malformed variants ---------------------------------------------------------------------------------------------

MALFORMED_RULES: dict[str, str] = {
    "wrong_hash": "V01", "oversize": "V02", "nul_byte": "V03", "bom": "V03", "invalid_cp1252_byte": "V03",
    "extra_column": "V04", "missing_column": "V04", "renamed_column": "V04", "short_row": "V05", "bad_date": "V05",
    "unmasked_email": "V06", "unmasked_password": "V06", "two_modes_one_order": "V07", "duplicate_item_id": "V07",
    "label_mismatch": "V08", "outcome_after_7_days": "V09",
}
MALFORMED_KINDS: tuple[str, ...] = tuple(MALFORMED_RULES)
_BOM = b"\xef\xbb\xbf"


def _parse(raw: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(raw.decode(ENCODING), newline="")))


def _serialise(table: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\r\n").writerows(table)
    return buffer.getvalue().encode(ENCODING)


def _column(name: str) -> int:
    return HEADER.index(name)


def _first_order(table: list[list[str]], *, min_lines: int = 1, cancelled: bool = False) -> list[int]:
    """Row numbers (in `table`, header included) of the lines of the first order with `min_lines` lines or more that is, or is not, cancelled."""
    groups: dict[str, list[int]] = {}
    for number, row in enumerate(table[1:], start=1):
        groups.setdefault(row[_column("Order Id")], []).append(number)
    for lines in groups.values():
        status = table[lines[0]][_column("Delivery Status")] == "Shipping canceled"
        if len(lines) >= min_lines and status == cancelled:
            return lines
    raise ValueError("el archivo base no tiene un pedido que sirva para esta variante")


def make_malformed(base: Path, kind: str, out: Path) -> Path:
    """Write a copy of the valid fixture `base` to `out` with exactly one defect, the one `kind` names, and return `out`.

    ``MALFORMED_RULES[kind]`` is the rule that must reject the copy. The base file is never modified.
    """
    if kind not in MALFORMED_RULES:
        raise ValueError(f"kind: se esperaba uno de {list(MALFORMED_KINDS)}, se recibió {kind!r}")
    raw = Path(base).read_bytes()

    if kind == "bom":
        payload = _BOM + raw
    elif kind == "nul_byte":
        payload = raw.replace(b"Consumer", b"Consu\x00mer", 1)
        if payload == raw:
            payload = raw.replace(b"Corporate", b"Corpo\x00rate", 1)
    elif kind == "invalid_cp1252_byte":
        at = raw.index(b"ZZSENTINELNAME") + 2
        payload = raw[:at] + b"\x81" + raw[at + 1:]  # 0x81 has no meaning in cp1252
    else:
        table = _parse(raw)
        header, data = table[0], table[1:]
        middle = len(data) // 2
        if kind == "wrong_hash":
            ratio = _column("Order Item Profit Ratio")
            data[-1][ratio] = "0.01" if data[-1][ratio] != "0.01" else "0.02"
        elif kind == "extra_column":
            header.append("Extra Column")
            for row in data:
                row.append("x")
        elif kind == "missing_column":
            header.pop()
            for row in data:
                row.pop()
        elif kind == "renamed_column":
            header[_column("Market")] = "Markets"
        elif kind == "short_row":
            data[middle].pop()
        elif kind == "bad_date":
            data[middle][_column("order date (DateOrders)")] = "13/45/2017 99:99"
        elif kind == "unmasked_email":
            data[middle][_column("Customer Email")] = "ann.smith@example.com"
        elif kind == "unmasked_password":
            data[middle][_column("Customer Password")] = "hunter2"
        elif kind == "duplicate_item_id":
            data[middle][_column("Order Item Id")] = data[middle + 1][_column("Order Item Id")]
        elif kind == "oversize":  # ten long but valid product names (each under the csv module's field limit of 131072 characters)
            for row in data[middle:middle + 10]:
                row[_column("Product Name")] = "x" * 110_000
        else:
            lines = _first_order(table, min_lines=2 if kind == "two_modes_one_order" else 1)
            if kind == "two_modes_one_order":
                mode = table[lines[1]][_column("Shipping Mode")]
                table[lines[1]][_column("Shipping Mode")] = "First Class" if mode != "First Class" else "Second Class"
            elif kind == "label_mismatch":
                for number in lines:
                    label = table[number][_column("Late_delivery_risk")]
                    table[number][_column("Late_delivery_risk")] = "0" if label == "1" else "1"
            elif kind == "outcome_after_7_days":
                for number in lines:
                    row = table[number]
                    ordered = datetime.strptime(row[_column("order date (DateOrders)")], "%m/%d/%Y %H:%M")
                    row[_column("shipping date (DateOrders)")] = _stamp(ordered + timedelta(days=7))
                    row[_column("Days for shipping (real)")] = "7"
                    row[_column("Late_delivery_risk")] = "1"
                    row[_column("Delivery Status")] = "Late delivery"
        payload = _serialise(table)

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    return out
