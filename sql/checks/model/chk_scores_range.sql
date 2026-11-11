-- Violations: a score whose risk is not a probability (null, below 0 or above 1) (M04).
SELECT order_id, risk
FROM model_scores
WHERE risk IS NULL OR risk < 0 OR risk > 1;
