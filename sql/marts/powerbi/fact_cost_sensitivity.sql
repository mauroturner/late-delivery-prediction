-- The net benefit of each method at each break-even of the declared range. Parameters: none.
DROP TABLE IF EXISTS star_fact_cost_sensitivity;

CREATE TABLE star_fact_cost_sensitivity AS
SELECT scope, break_even, method, threshold, nb_per_1000, flagged_share, seal_state
FROM eval_sensitivity
ORDER BY scope, break_even, method, seal_state;
