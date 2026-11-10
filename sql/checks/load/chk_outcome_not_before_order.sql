-- Violations: an outcome known before its order was created.
SELECT r.order_id, o.order_date, r.outcome_known_date
FROM raw_outcomes AS r
JOIN raw_orders AS o ON o.order_id = r.order_id
WHERE r.outcome_known_date < o.order_date;
