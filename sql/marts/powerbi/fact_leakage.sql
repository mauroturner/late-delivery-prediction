-- What each column that gives the result away does to the AUC on V2, next to the shipping mode alone. Parameters: none.
DROP TABLE IF EXISTS star_fact_leakage;

CREATE TABLE star_fact_leakage AS
SELECT source_column, column_es, kind, known_at_es, auc_with_mode_valid, note_es
FROM eval_leakage
ORDER BY sort_order;
