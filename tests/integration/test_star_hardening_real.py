"""H4 on the real file: the facts that the advisories of U17 asked to check (R3-dim-product-attr-mismatch, R3-geo-null-join). Counts only; no metric."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.real_data


def test_no_real_product_card_has_two_names_categories_or_departments(real_read):
    lines = real_read[0].order_lines
    cards = lines.groupby("product_card_id")
    assert cards.ngroups == 118
    for column in ("product_name", "category_name", "department_name"):
        assert int((cards[column].nunique() > 1).sum()) == 0, f"a card with more than one {column}"
    assert int(cards[["product_name", "category_name", "department_name"]].apply(lambda d: len(d.drop_duplicates())).gt(1).sum()) == 0, "so the three attributes of a card always come from one row"


def test_no_real_order_lacks_a_market_a_region_or_a_country(real_read):
    orders = real_read[0].orders
    assert len(orders) == 65_752
    for column in ("market", "order_region", "order_country"):
        assert int(orders[column].isna().sum()) == 0 and int((orders[column].astype(str).str.strip() == "").sum()) == 0, column
