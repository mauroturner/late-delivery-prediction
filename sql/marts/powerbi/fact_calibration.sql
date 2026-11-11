-- The calibration by bin of each method in each evaluated block. Parameters: none.
DROP TABLE IF EXISTS star_fact_calibration;

CREATE TABLE star_fact_calibration AS
SELECT scope, method, seal_state, bin, p_mean, observed, orders
FROM eval_calibration
ORDER BY scope, method, seal_state, bin;
