-- Violations: an outcome whose order is not in raw_orders.
SELECT r.order_id, r.outcome_known_date
FROM raw_outcomes AS r
LEFT JOIN raw_orders AS o ON o.order_id = r.order_id
WHERE o.order_id IS NULL;
