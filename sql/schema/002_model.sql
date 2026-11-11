-- Tables of the model (docs/plan.md, section 5): the registry of the frozen model, the rates of the baselines and the scores. Safe to run repeatedly: nothing is dropped
-- or migrated. The tables of the follow-up (tracking_monthly) and of the evaluation (eval_*, feature_importance) were added by the units that first used them (U15, U16).
-- Dates are ISO text (YYYY-MM-DD).

-- The model trained at the launch: where its artifact is, the SHA-256 that the artifact must have to be loaded (M03), what was chosen and with what costs.
-- A row is never replaced or deleted: the model is frozen (decision D17).
CREATE TABLE IF NOT EXISTS model_registry (
    model_version       TEXT PRIMARY KEY,
    trained_as_of       TEXT NOT NULL,
    artifact_path       TEXT NOT NULL,   -- relative to the project folder
    artifact_sha256     TEXT NOT NULL,
    feature_set         TEXT NOT NULL,
    params_json         TEXT NOT NULL,
    calibration         TEXT NOT NULL,
    threshold           REAL NOT NULL,
    costs_json          TEXT NOT NULL,
    config_sha256       TEXT NOT NULL,
    selection_json      TEXT NOT NULL,   -- the log loss on V1 of each candidate
    valid_v2_json       TEXT NOT NULL    -- the figures on V2, and the fingerprint of its scores
);

CREATE TRIGGER IF NOT EXISTS model_registry_no_update BEFORE UPDATE ON model_registry
BEGIN
    SELECT RAISE(ABORT, 'model_registry is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_registry_no_delete BEFORE DELETE ON model_registry
BEGIN
    SELECT RAISE(ABORT, 'model_registry is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_registry_no_replace BEFORE INSERT ON model_registry
WHEN EXISTS (SELECT 1 FROM model_registry WHERE model_version = NEW.model_version)
BEGIN
    SELECT RAISE(ABORT, 'model_registry is insert-only');
END;

-- The rates of B2 and B5 fitted on the training block at the launch (cell: the mode, or the mode and "before" or "from" the noon hour).
CREATE TABLE IF NOT EXISTS baseline_rates (
    baseline            TEXT NOT NULL,
    cell                TEXT NOT NULL,
    rate                REAL NOT NULL,
    train_orders        INTEGER NOT NULL,
    PRIMARY KEY (baseline, cell)
);

-- One score per new order, never changed: insert-only. Three triggers abort an update, a delete and an insert of an order that is already scored; the third exists
-- because INSERT OR REPLACE and REPLACE delete the old row without running the delete trigger while recursive_triggers is off (project 02, its D44).
CREATE TABLE IF NOT EXISTS model_scores (
    order_id            INTEGER PRIMARY KEY,
    as_of_date          TEXT NOT NULL,
    run_key             TEXT NOT NULL,
    model_version       TEXT NOT NULL,
    risk                REAL NOT NULL CHECK (risk >= 0 AND risk <= 1),
    threshold           REAL NOT NULL,
    is_alert            INTEGER NOT NULL CHECK (is_alert IN (0, 1))
);

CREATE TRIGGER IF NOT EXISTS model_scores_no_update BEFORE UPDATE ON model_scores
BEGIN
    SELECT RAISE(ABORT, 'model_scores is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_scores_no_delete BEFORE DELETE ON model_scores
BEGIN
    SELECT RAISE(ABORT, 'model_scores is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_scores_no_replace BEFORE INSERT ON model_scores
WHEN EXISTS (SELECT 1 FROM model_scores WHERE order_id = NEW.order_id)
BEGIN
    SELECT RAISE(ABORT, 'model_scores is insert-only');
END;

-- The tables of the evaluation (U15, docs/plan.md section 5). `seal_state` says whether a row was computed with the sealed configuration ('sealed') or with another
-- one ('post_seal', reported apart and never replacing a sealed row, D17). The sealed rows of the test are written once; every other row of a scope is replaced when
-- the same evaluation is repeated.
CREATE TABLE IF NOT EXISTS eval_metrics (
    scope               TEXT NOT NULL CHECK (scope IN ('valid_v2', 'test', 'post_break')),
    method              TEXT NOT NULL,
    metric              TEXT NOT NULL,
    seal_state          TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    value               REAL,            -- NULL where the metric is not defined (no order flagged, no late order)
    PRIMARY KEY (scope, method, metric, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_bootstrap (
    comparison          TEXT NOT NULL,   -- for example model_vs_b2_mode
    seal_state          TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    diff                REAL,
    ci_low              REAL,
    ci_high             REAL,
    resamples           INTEGER NOT NULL,
    discarded           INTEGER NOT NULL,
    seed                INTEGER NOT NULL,
    PRIMARY KEY (comparison, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_criteria (
    criterion_id        TEXT NOT NULL,
    seal_state          TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    scope               TEXT NOT NULL CHECK (scope IN ('test', 'post_break')),
    name_es             TEXT NOT NULL,
    value               REAL,
    baseline_value      REAL,
    ci_low              REAL,
    ci_high             REAL,
    threshold_es        TEXT NOT NULL,
    passed              INTEGER CHECK (passed IN (0, 1)),   -- NULL for C10, which has no threshold
    seen_by_research    TEXT NOT NULL,
    seen_by_planner     TEXT NOT NULL,
    verdict_es          TEXT NOT NULL,
    PRIMARY KEY (criterion_id, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_sensitivity (
    break_even          REAL NOT NULL,
    method              TEXT NOT NULL,
    scope               TEXT NOT NULL CHECK (scope IN ('valid_v2', 'test')),
    seal_state          TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    threshold           REAL NOT NULL,
    nb_total            REAL NOT NULL,
    nb_per_1000         REAL NOT NULL,
    flagged_share       REAL NOT NULL,
    orders              INTEGER NOT NULL,
    PRIMARY KEY (break_even, method, scope, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_roc (
    scope               TEXT NOT NULL CHECK (scope IN ('valid_v2', 'test', 'post_break')),
    method              TEXT NOT NULL,
    seal_state          TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    point               INTEGER NOT NULL,
    fpr                 REAL NOT NULL,
    tpr                 REAL NOT NULL,
    PRIMARY KEY (scope, method, seal_state, point)
);

CREATE TABLE IF NOT EXISTS eval_calibration (
    scope               TEXT NOT NULL CHECK (scope IN ('valid_v2', 'test', 'post_break')),
    method              TEXT NOT NULL,
    seal_state          TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    bin                 INTEGER NOT NULL,
    p_mean              REAL NOT NULL,
    observed            REAL NOT NULL,
    orders              INTEGER NOT NULL,
    PRIMARY KEY (scope, method, seal_state, bin)
);

-- What each column that gives the result away does to the AUC on V2, next to the reference of the shipping mode alone (written at the launch, D31).
CREATE TABLE IF NOT EXISTS eval_leakage (
    source_column       TEXT PRIMARY KEY,
    column_es           TEXT NOT NULL,
    kind                TEXT NOT NULL,   -- reference, target-derived, outcome or profit
    known_at_es         TEXT NOT NULL,
    auc_with_mode_valid REAL NOT NULL,
    note_es             TEXT NOT NULL,
    sort_order          INTEGER NOT NULL
);

-- Permutation importance of the variables of the model in use on V2 (written at the launch).
CREATE TABLE IF NOT EXISTS feature_importance (
    feature             TEXT PRIMARY KEY,
    feature_es          TEXT NOT NULL,
    auc_drop            REAL NOT NULL,
    sort_order          INTEGER NOT NULL
);

-- The monthly follow-up of the alerts (U16, docs/plan.md section 15): for a calculation date, one row per month of the order and shipping mode with what the frozen
-- model scored and what became of its alerts once the outcomes were known. Rows of a date are replaced when that date is computed again; other dates are not touched.
CREATE TABLE IF NOT EXISTS tracking_monthly (
    as_of_date          TEXT NOT NULL,
    order_month         TEXT NOT NULL,   -- YYYY-MM of the order
    shipping_mode       TEXT NOT NULL,
    orders_scored       INTEGER NOT NULL,
    alerts              INTEGER NOT NULL,
    closed_orders       INTEGER NOT NULL,   -- scored orders whose outcome is known and is not a cancellation
    closed_alerts       INTEGER NOT NULL,
    alerts_late         INTEGER NOT NULL,   -- closed alerts that arrived late
    late_closed         INTEGER NOT NULL,   -- closed orders that arrived late
    late_missed         INTEGER NOT NULL,   -- closed late orders that had no alert
    late_expected       REAL NOT NULL,      -- sum of the risk of the closed orders: the delays the model expected
    cancelled_orders    INTEGER NOT NULL,
    alerts_on_cancelled INTEGER NOT NULL,   -- I04
    PRIMARY KEY (as_of_date, order_month, shipping_mode)
);
