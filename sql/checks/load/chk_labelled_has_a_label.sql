-- Violations: a labelled order without a label of the task (a cancelled order has none and must not be in the mart).
SELECT order_id, is_late
FROM mart_labelled_orders
WHERE is_late IS NULL OR is_late NOT IN (0, 1);
