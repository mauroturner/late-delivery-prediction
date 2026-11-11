-- Violations: the scored orders of fact_order are not exactly the rows of model_scores, with the same risk and alert.
SELECT 'score_count' AS problem, CAST((SELECT COUNT(*) FROM star_fact_order WHERE risk IS NOT NULL) AS TEXT) AS detail
WHERE (SELECT COUNT(*) FROM star_fact_order WHERE risk IS NOT NULL) <> (SELECT COUNT(*) FROM model_scores)
UNION ALL
SELECT 'score_differs', CAST(f.order_id AS TEXT)
FROM star_fact_order AS f
JOIN model_scores AS s ON s.order_id = f.order_id
WHERE f.risk <> s.risk OR f.is_alert <> s.is_alert OR f.threshold <> s.threshold;
