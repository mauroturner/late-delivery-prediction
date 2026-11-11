-- The permutation importance of the variables of the model in use on V2. Parameters: none.
DROP TABLE IF EXISTS star_fact_importance;

CREATE TABLE star_fact_importance AS
SELECT feature, feature_es, auc_drop
FROM feature_importance
ORDER BY sort_order;
