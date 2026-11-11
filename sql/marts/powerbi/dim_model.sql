-- The frozen model and the costs its threshold was chosen with. ref_seal (loaded by the export from the seal file) says whether the registered configuration is the sealed one.
DROP TABLE IF EXISTS star_dim_model;

CREATE TABLE star_dim_model AS
SELECT
    r.model_version AS model_version,
    r.feature_set AS feature_set,
    'tasa de aprendizaje ' || json_extract(r.params_json, '$.learning_rate') || ', ' || json_extract(r.params_json, '$.max_leaf_nodes') || ' hojas, '
        || json_extract(r.params_json, '$.min_samples_leaf') || ' muestras por hoja' AS params_es,
    r.calibration AS calibration,
    r.threshold AS threshold,
    CAST(json_extract(r.costs_json, '$.alert_cost') AS REAL) AS alert_cost,
    CAST(json_extract(r.costs_json, '$.late_cost') AS REAL) AS late_cost,
    CAST(json_extract(r.costs_json, '$.effect') AS REAL) AS effect,
    CAST(json_extract(r.costs_json, '$.alert_cost') AS REAL)
        / (CAST(json_extract(r.costs_json, '$.late_cost') AS REAL) * CAST(json_extract(r.costs_json, '$.effect') AS REAL)) AS break_even,
    r.trained_as_of AS trained_as_of,
    r.config_sha256 AS config_sha256,
    COALESCE(s.seal_state, 'none') AS seal_state
FROM model_registry AS r
LEFT JOIN ref_seal AS s ON s.model_version = r.model_version
ORDER BY r.model_version;
