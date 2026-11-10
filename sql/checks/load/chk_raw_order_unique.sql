-- Violations: an order_id that appears more than once in raw_orders (the primary key also forbids it; this keeps the rule visible if a table is ever rebuilt without one).
SELECT order_id, COUNT(*) AS n_rows
FROM raw_orders
GROUP BY order_id
HAVING COUNT(*) > 1;
