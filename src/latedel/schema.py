"""The contract of the source file and of the canonical tables: the only place that lists columns (D06, D07).

The 53 columns of ``DataCoSupplyChainDataset.csv`` are split into four lists that cover them without overlap (a test checks it):

- ``PERSONAL_COLUMNS`` (15): used only by the checks of the reader (V06, V07) and never written anywhere (D07);
- ``LEAKING_COLUMNS`` (8): known only after the order was created; they go to the outcomes stream and nowhere else (D06);
- ``DUPLICATE_OR_CONSTANT_COLUMNS`` (7): a copy of another column (``DUPLICATE_COLUMNS``, 4), or a constant, an empty column or a URL (``CONSTANT_COLUMNS``, 3); not carried;
- ``ORDER_TIME_COLUMNS`` (23): what is known when the order is created; the allowed list of the orders and lines streams.

``SOURCE_TO_ORDER`` and ``SOURCE_TO_LINE`` give the internal name (English, snake case) of each column that reaches ``orders`` and ``order_lines``.
Two columns of ``ORDER_TIME_COLUMNS`` are allowed but not carried, because the tables of the plan hold the names and not the codes (decision D39).
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa

SOURCE_COLUMNS: tuple[str, ...] = (
    "Type", "Days for shipping (real)", "Days for shipment (scheduled)", "Benefit per order", "Sales per customer", "Delivery Status",
    "Late_delivery_risk", "Category Id", "Category Name", "Customer City", "Customer Country", "Customer Email", "Customer Fname", "Customer Id",
    "Customer Lname", "Customer Password", "Customer Segment", "Customer State", "Customer Street", "Customer Zipcode", "Department Id",
    "Department Name", "Latitude", "Longitude", "Market", "Order City", "Order Country", "Order Customer Id", "order date (DateOrders)", "Order Id",
    "Order Item Cardprod Id", "Order Item Discount", "Order Item Discount Rate", "Order Item Id", "Order Item Product Price", "Order Item Profit Ratio",
    "Order Item Quantity", "Sales", "Order Item Total", "Order Profit Per Order", "Order Region", "Order State", "Order Status", "Order Zipcode",
    "Product Card Id", "Product Category Id", "Product Description", "Product Image", "Product Name", "Product Price", "Product Status",
    "shipping date (DateOrders)", "Shipping Mode",
)

PERSONAL_COLUMNS: tuple[str, ...] = (
    "Customer Email", "Customer Password", "Customer Fname", "Customer Lname", "Customer Street", "Customer Zipcode", "Customer City", "Customer State",
    "Customer Id", "Order Customer Id", "Order City", "Order State", "Order Zipcode", "Latitude", "Longitude",
)

LEAKING_COLUMNS: tuple[str, ...] = (
    "Late_delivery_risk",                                                              # the target
    "Delivery Status", "Days for shipping (real)", "shipping date (DateOrders)", "Order Status",   # known after the order
    "Benefit per order", "Order Profit Per Order", "Order Item Profit Ratio",          # moment not established: kept out as a precaution
)

DUPLICATE_COLUMNS: tuple[str, ...] = (
    "Sales per customer",          # = Order Item Total
    "Order Item Product Price",    # = Product Price
    "Product Card Id",             # = Order Item Cardprod Id
    "Product Category Id",         # = Category Id
)

CONSTANT_COLUMNS: tuple[str, ...] = (
    "Product Status",              # constant 0
    "Product Description",         # empty
    "Product Image",               # a URL
)

# The two kinds by name (leakage.json says which is which): their order inside a list never decides what a column is (H5, R2-2).
DUPLICATE_OR_CONSTANT_COLUMNS: tuple[str, ...] = DUPLICATE_COLUMNS + CONSTANT_COLUMNS

ORDER_TIME_COLUMNS: tuple[str, ...] = (
    "Type", "Days for shipment (scheduled)", "Category Id", "Category Name", "Customer Country", "Customer Segment", "Department Id", "Department Name",
    "Market", "Order Country", "order date (DateOrders)", "Order Id", "Order Item Cardprod Id", "Order Item Discount", "Order Item Discount Rate",
    "Order Item Id", "Order Item Quantity", "Sales", "Order Item Total", "Order Region", "Product Name", "Product Price", "Shipping Mode",
)

# the 20 columns that the profile of the source measured constant inside an order (V07)
ORDER_LEVEL_COLUMNS: tuple[str, ...] = (
    "Shipping Mode", "Type", "Market", "Order Region", "Order Country", "Order City", "Order State", "Customer Segment", "Customer Country", "Customer Id",
    "order date (DateOrders)", "shipping date (DateOrders)", "Days for shipment (scheduled)", "Days for shipping (real)", "Late_delivery_risk",
    "Delivery Status", "Order Status", "Latitude", "Longitude", "Order Zipcode",
)

# the pairs of columns that repeat each other (V12)
IDENTICAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("Benefit per order", "Order Profit Per Order"), ("Sales per customer", "Order Item Total"), ("Order Item Product Price", "Product Price"),
    ("Product Card Id", "Order Item Cardprod Id"), ("Product Category Id", "Category Id"), ("Order Customer Id", "Customer Id"),
)

SOURCE_TO_ORDER: dict[str, str] = {
    "Order Id": "order_id", "order date (DateOrders)": "order_ts", "Shipping Mode": "shipping_mode", "Days for shipment (scheduled)": "scheduled_days",
    "Type": "payment_type", "Market": "market", "Order Region": "order_region", "Order Country": "order_country",
    "Customer Segment": "customer_segment", "Customer Country": "customer_country",
}
SOURCE_TO_LINE: dict[str, str] = {
    "Order Item Id": "line_id", "Order Id": "order_id", "Department Name": "department_name", "Category Name": "category_name",
    "Order Item Cardprod Id": "product_card_id", "Product Name": "product_name", "Order Item Quantity": "quantity", "Product Price": "product_price",
    "Sales": "sales", "Order Item Discount": "discount", "Order Item Discount Rate": "discount_rate", "Order Item Total": "item_total",
}
SOURCE_TO_OUTCOME: dict[str, str] = {
    "Delivery Status": "delivery_status", "Order Status": "order_status", "Days for shipping (real)": "real_days",
    "shipping date (DateOrders)": "shipping_ts", "Late_delivery_risk": "source_label",
    "Benefit per order": "benefit_total", "Order Item Profit Ratio": "profit_ratio_mean",
}
CANCELLED_STATUS = "Shipping canceled"
MASKED = "XXXXXXXXX"
SCHEDULED_DAYS_BY_MODE: dict[str, int] = {"Same Day": 0, "First Class": 1, "Second Class": 2, "Standard Class": 4}
ATTRIBUTION = (
    "Constante, Fabian; Silva, Fernando; Pereira, António (2019), 'DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS', "
    "Mendeley Data, v5, http://doi.org/10.17632/8gx2fvg2k6.5 (CC BY 4.0)"
)

ORDERS_SCHEMA = pa.schema([
    ("order_id", pa.int64()), ("order_ts", pa.string()), ("order_date", pa.date32()), ("regime", pa.string()), ("shipping_mode", pa.string()),
    ("scheduled_days", pa.int64()), ("payment_type", pa.string()), ("market", pa.string()), ("order_region", pa.string()),
    ("order_country", pa.string()), ("customer_segment", pa.string()), ("customer_country", pa.string()),
])
ORDER_LINES_SCHEMA = pa.schema([
    ("line_id", pa.int64()), ("order_id", pa.int64()), ("department_name", pa.string()), ("category_name", pa.string()), ("product_card_id", pa.int64()),
    ("product_name", pa.string()), ("quantity", pa.int64()), ("product_price", pa.float64()), ("sales", pa.float64()), ("discount", pa.float64()),
    ("discount_rate", pa.float64()), ("item_total", pa.float64()),
])
OUTCOMES_SCHEMA = pa.schema([
    ("order_id", pa.int64()), ("outcome_known_date", pa.date32()), ("delivery_status", pa.string()), ("order_status", pa.string()),
    ("real_days", pa.int64()), ("shipping_ts", pa.string()), ("source_label", pa.int64()), ("is_cancelled", pa.bool_()),
    ("benefit_total", pa.float64()), ("profit_ratio_mean", pa.float64()),
])


class SourceError(RuntimeError):
    """A rule of the source (V01 to V09) failed; ``problems`` says what, one dict per failing item (never with the value of a personal column)."""

    def __init__(self, rule_id: str, message: str, problems: list[dict[str, Any]] | None = None) -> None:
        super().__init__(f"{rule_id}: {message}")
        self.rule_id = rule_id
        self.problems = problems if problems is not None else []
