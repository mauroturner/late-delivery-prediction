"""The evaluation code on the real file, on the training and validation blocks only (U15, decision D17).

The real warehouse here holds the launch delivery: the test and post-break labels are physically absent, so nothing below can measure them. The first evaluation of
the real test is U25's and writes the seal.
"""

from __future__ import annotations

import shutil

import pytest

from latedel.baselines import baseline_rates
from latedel.evaluation import EvaluationError, block_frame, evaluate, evaluate_block, record_launch_tables, registered_baseline_rates
from latedel.features import FEATURE_SETS
from latedel.launch import load_registered_model
from latedel.registry import read_seal, valid_v2_scores_fingerprint
from latedel.split import labelled_orders
from latedel.warehouse import connect

pytestmark = pytest.mark.real_data


@pytest.fixture()
def real_conn(real_fitted, tmp_path):
    db = tmp_path / "real.sqlite"
    shutil.copyfile(real_fitted.db, db)
    conn = connect(db)
    yield conn
    conn.close()


def test_the_registered_rates_and_the_v2_figures_of_the_real_launch(real_fitted, real_conn):
    cfg, launch = real_fitted.cfg, real_fitted.cfg.deliveries.launch_date
    rates = registered_baseline_rates(real_conn, cfg)
    fitted = baseline_rates(labelled_orders(real_conn, launch, cfg), cfg)
    assert rates.b2.rates == fitted.b2.rates and rates.b5.rates == fitted.b5.rates and rates.train_orders == 29_894
    registered = load_registered_model(real_conn, cfg)
    v2 = block_frame(real_conn, "valid_v2", cfg, as_of=launch, registered=registered, rates=rates)
    assert len(v2) == 5_032
    assert valid_v2_scores_fingerprint(v2) == read_seal(cfg.paths.seal)["launch"]["valid_v2_scores_sha256"]
    result = evaluate_block(real_conn, "valid_v2", cfg, as_of=launch)
    metrics = result.metrics
    assert result.orders == 5_032 and set(result.sensitivity["scope"]) == {"valid_v2"} and len(result.sensitivity) == 17 * 3
    assert 0.74 < metrics["model"]["auc"] < 0.80 and metrics["b2_mode"]["auc"] < metrics["model"]["auc"], "V2: the model beats the rate by mode"
    print("REAL V2 auc", {k: round(v["auc"], 4) for k, v in metrics.items() if v["auc"] is not None}, "threshold", metrics["model"]["threshold"],
          "nb/1000", round(metrics["model"]["nb_per_1000"], 1))


def test_the_real_test_block_cannot_be_measured_from_the_launch_warehouse(real_fitted, real_conn):
    cfg, launch = real_fitted.cfg, real_fitted.cfg.deliveries.launch_date
    with pytest.raises(EvaluationError, match="no hay pedidos con etiqueta en el bloque test"):
        evaluate_block(real_conn, "test", cfg, as_of=launch)
    outcome = evaluate(real_conn, cfg, run_key="real-L", as_of=launch, config_sha256=real_fitted.result["config_sha256"])
    assert outcome.test is None and outcome.post_break is None and outcome.label == "sealed" and outcome.valid_v2.orders == 5_032
    assert real_conn.execute("SELECT COUNT(*) FROM eval_criteria").fetchone()[0] == 0
    assert read_seal(cfg.paths.seal).get("test") is None


def test_the_importance_and_the_leakage_of_the_real_launch(real_fitted, real_conn):
    cfg, launch = real_fitted.cfg, real_fitted.cfg.deliveries.launch_date
    counts = record_launch_tables(real_conn, cfg, as_of=launch)
    features = FEATURE_SETS[load_registered_model(real_conn, cfg).model.feature_set]
    assert counts == {"feature_importance": len(features), "eval_leakage": 8}
    top = real_conn.execute("SELECT feature FROM feature_importance ORDER BY sort_order LIMIT 1").fetchone()[0]
    assert top == "shipping_mode"
    leak = dict(real_conn.execute("SELECT source_column, auc_with_mode_valid FROM eval_leakage").fetchall())
    assert leak["Days for shipping (real)"] > 0.99 > leak["shipping_mode"] and leak["Delivery Status"] > 0.99
    print("REAL LEAKAGE", {k: round(v, 4) for k, v in leak.items()})
