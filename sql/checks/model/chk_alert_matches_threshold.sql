-- Violations: an alert that is not "risk at or above the threshold" it was scored with (RF17).
SELECT order_id, risk, threshold, is_alert
FROM model_scores
WHERE is_alert <> (risk >= threshold);
