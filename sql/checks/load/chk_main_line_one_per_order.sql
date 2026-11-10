-- Violations: an order of the staging table that does not have exactly one row in mart_main_line (an order without lines has none).
SELECT o.order_id, COUNT(m.line_id) AS main_lines
FROM stg_orders AS o
LEFT JOIN mart_main_line AS m ON m.order_id = o.order_id
GROUP BY o.order_id
HAVING COUNT(m.line_id) <> 1;
