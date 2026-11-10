-- Violations: a row of the staging tables or of the labelled orders that is dated after the calculation date: an order created after it, or an outcome
-- known after it. Nothing that is computed for a date may see what happens later (RNF06). Parameters: :as_of.
SELECT 'stg_orders' AS source_table, order_id, order_date AS day
FROM stg_orders
WHERE order_date > :as_of
UNION ALL
SELECT 'stg_outcomes', order_id, outcome_known_date
FROM stg_outcomes
WHERE outcome_known_date > :as_of
UNION ALL
SELECT 'mart_labelled_orders', order_id, MAX(order_date, outcome_known_date)
FROM mart_labelled_orders
WHERE order_date > :as_of OR outcome_known_date > :as_of;
