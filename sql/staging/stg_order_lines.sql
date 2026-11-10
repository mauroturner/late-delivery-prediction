-- Rebuilds stg_order_lines: the lines of the orders in stg_orders (docs/plan.md section 7). Names no outcome table either. Parameters: none.

DELETE FROM stg_order_lines;

INSERT INTO stg_order_lines (
    line_id, order_id, department_name, category_name, product_card_id, product_name, quantity, product_price, sales, discount, discount_rate, item_total
)
SELECT
    l.line_id, l.order_id, l.department_name, l.category_name, l.product_card_id, l.product_name, l.quantity, l.product_price, l.sales, l.discount,
    l.discount_rate, l.item_total
FROM raw_order_lines AS l
JOIN stg_orders AS o ON o.order_id = l.order_id
ORDER BY l.line_id;
