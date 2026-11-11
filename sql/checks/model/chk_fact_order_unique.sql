-- Violations: an order that appears more than once in fact_order, an order of the warehouse that does not appear (the fact has one row per order), or an order with a
-- geography column that is NULL (the schema forbids it; the fact joins dim_geo on equality, which would drop such an order, so it is named here and not only as missing).
SELECT 'duplicate' AS problem, CAST(order_id AS TEXT) AS detail
FROM star_fact_order
GROUP BY order_id
HAVING COUNT(*) > 1
UNION ALL
SELECT 'missing_order', CAST(order_id AS TEXT)
FROM raw_orders
WHERE order_id NOT IN (SELECT order_id FROM star_fact_order)
UNION ALL
SELECT 'geo_null', CAST(order_id AS TEXT)
FROM raw_orders
WHERE market IS NULL OR order_region IS NULL OR order_country IS NULL;
