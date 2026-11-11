-- The ROC curve of each method in each evaluated block. Parameters: none.
DROP TABLE IF EXISTS star_fact_roc;

CREATE TABLE star_fact_roc AS
SELECT scope, method, seal_state, point, fpr, tpr
FROM eval_roc
ORDER BY scope, method, seal_state, point;
