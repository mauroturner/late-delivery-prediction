-- Violations: an order with more than one score, or a score of an order that is not in the warehouse (RF17).
SELECT 'duplicate' AS problem, CAST(order_id AS TEXT) AS detail
FROM model_scores
GROUP BY order_id
HAVING COUNT(*) > 1
UNION ALL
SELECT 'no_order', CAST(order_id AS TEXT)
FROM model_scores
WHERE order_id NOT IN (SELECT order_id FROM raw_orders);
