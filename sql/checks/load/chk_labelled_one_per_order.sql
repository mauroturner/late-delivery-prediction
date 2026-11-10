-- Violations: an order that appears more than once in mart_labelled_orders.
SELECT order_id, COUNT(*) AS n_rows
FROM mart_labelled_orders
GROUP BY order_id
HAVING COUNT(*) > 1;
