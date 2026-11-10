-- Rebuilds stg_orders from raw_orders up to the calculation date (docs/plan.md section 7). It has no outcome column, and this file names no outcome table:
-- a test reads it to prove it (AC13). Parameters: :as_of (ISO date).

DELETE FROM stg_orders;

INSERT INTO stg_orders (
    order_id, order_ts, order_date, regime, shipping_mode, scheduled_days, payment_type, market, order_region, order_country, customer_segment, customer_country
)
SELECT
    order_id, order_ts, order_date, regime, shipping_mode, scheduled_days, payment_type, market, order_region, order_country, customer_segment, customer_country
FROM raw_orders
WHERE order_date <= :as_of
ORDER BY order_id;
