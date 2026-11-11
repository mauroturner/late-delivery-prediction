-- Violations: a score of an order created before the test starts. The monthly deliveries bring only orders from the day after the launch, so such a score would be the
-- model scoring what it was trained or selected on. Parameters: :test_start.
SELECT s.order_id, o.order_date
FROM model_scores AS s
JOIN raw_orders AS o ON o.order_id = s.order_id
WHERE o.order_date < :test_start;
