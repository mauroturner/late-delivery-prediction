-- Violations: a line whose order is not in raw_orders.
SELECT l.line_id, l.order_id
FROM raw_order_lines AS l
LEFT JOIN raw_orders AS o ON o.order_id = l.order_id
WHERE o.order_id IS NULL;
