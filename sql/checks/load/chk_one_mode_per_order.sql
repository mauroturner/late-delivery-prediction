-- Violations: an order of the staging table without a shipping mode, or listed more than once. The mode is constant inside an order (rule V07, checked while
-- the file is read, where the lines of an order are still together); what can still be checked on the staged orders is that each has exactly one mode.
SELECT order_id, shipping_mode
FROM stg_orders
WHERE shipping_mode IS NULL OR TRIM(shipping_mode) = ''
   OR order_id IN (SELECT order_id FROM stg_orders GROUP BY order_id HAVING COUNT(*) > 1);
