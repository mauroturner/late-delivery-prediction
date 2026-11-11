-- Violations: an order that appears more than once in fact_order, or an order of the warehouse that does not appear (the fact has one row per order).
SELECT 'duplicate' AS problem, CAST(order_id AS TEXT) AS detail
FROM star_fact_order
GROUP BY order_id
HAVING COUNT(*) > 1
UNION ALL
SELECT 'missing_order', CAST(order_id AS TEXT)
FROM raw_orders
WHERE order_id NOT IN (SELECT order_id FROM star_fact_order);
