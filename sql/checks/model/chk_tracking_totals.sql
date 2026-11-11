-- Violations: the follow-up of the last date does not add up to the scores. Its scored orders and its alerts must be the rows of model_scores recorded up to that date,
-- and there must be a follow-up if there are scores.
WITH latest AS (SELECT MAX(as_of_date) AS day FROM tracking_monthly),
tracked AS (
    SELECT COALESCE(SUM(orders_scored), 0) AS orders_scored, COALESCE(SUM(alerts), 0) AS alerts
    FROM tracking_monthly
    WHERE as_of_date = (SELECT day FROM latest)
),
scored AS (
    SELECT COUNT(*) AS orders_scored, COALESCE(SUM(is_alert), 0) AS alerts
    FROM model_scores
    WHERE (SELECT day FROM latest) IS NOT NULL AND as_of_date <= (SELECT day FROM latest)      -- with no follow-up there is no date to count up to: no score is counted here, and no_follow_up below says so
)
SELECT 'orders_scored' AS problem, tracked.orders_scored AS tracked, scored.orders_scored AS scored FROM tracked, scored WHERE tracked.orders_scored <> scored.orders_scored
UNION ALL
SELECT 'alerts', tracked.alerts, scored.alerts FROM tracked, scored WHERE tracked.alerts <> scored.alerts
UNION ALL
SELECT 'no_follow_up', 0, COUNT(*) FROM model_scores WHERE (SELECT COUNT(*) FROM tracking_monthly) = 0 HAVING COUNT(*) > 0;
