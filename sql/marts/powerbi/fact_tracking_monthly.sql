-- The monthly follow-up of the alerts as of the last calculation date that ``track`` computed. Parameters: none.
DROP TABLE IF EXISTS star_fact_tracking_monthly;

CREATE TABLE star_fact_tracking_monthly AS
SELECT
    CAST(replace(order_month, '-', '') AS INTEGER) AS month_key,
    shipping_mode AS mode_key,
    orders_scored, alerts, closed_orders, closed_alerts, alerts_late, late_closed, late_missed, late_expected, cancelled_orders, alerts_on_cancelled
FROM tracking_monthly
WHERE as_of_date = (SELECT MAX(as_of_date) FROM tracking_monthly)
ORDER BY month_key, mode_key;
