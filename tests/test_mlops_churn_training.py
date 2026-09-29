"""
Tests for src/mlops/churn_training.py (DECISIONS.md D57) — PSI math, and an
end-to-end sweep logged to a throwaway MLflow file store. Synthetic
transactions (the committed fixture has only 4 merchants — too few to
stratify-split). Skipped if the `mlops` extra isn't installed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from src.mlops.churn_training import drift_report, psi


def _synthetic_transactions(n_merchants: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ref = pd.Timestamp("2025-09-30")
    rows = []
    for m in range(n_merchants):
        churn = int(rng.random() < 0.25)
        n_tx = rng.integers(3, 15)
        for _ in range(n_tx):
            rows.append(
                {
                    "transaction_id": len(rows),
                    "merchant_id": 1000 + m,
                    "transaction_date": ref - pd.Timedelta(days=int(rng.integers(0, 365))),
                    "amount": float(rng.gamma(2, 50 if churn else 80)),
                    "status": "approved" if rng.random() < (0.8 if churn else 0.95) else "declined",
                    "channel": rng.choice(["ecom", "pos"]),
                    "reference_date": ref,
                    "fla_churn90": churn,
                    "last_complaint_date": ref - pd.Timedelta(days=int(rng.integers(1, 200))) if churn else pd.NaT,
                    "segment": rng.choice(["SMB", "Enterprise"]),
                    "mcc": rng.choice(["5812", "5411"]),
                }
            )
    return pd.DataFrame(rows)


def test_psi_zero_for_identical_and_large_for_shifted() -> None:
    rng = np.random.default_rng(1)
    a = pd.Series(rng.normal(0, 1, 5000))
    assert psi(a, a) == pytest.approx(0, abs=1e-9)
    assert psi(a, pd.Series(rng.normal(0, 1, 5000))) < 0.05
    assert psi(a, pd.Series(rng.normal(1.5, 1, 5000))) > 0.2


def test_psi_categorical_and_null_shift() -> None:
    ref = pd.Series(["SMB"] * 80 + ["Enterprise"] * 20)
    assert psi(ref, ref) == pytest.approx(0, abs=1e-9)
    assert psi(ref, pd.Series(["SMB"] * 20 + ["Enterprise"] * 80)) > 0.2
    num = pd.Series(np.arange(100, dtype=float))
    assert psi(num, num.where(num > 50)) > 0.2  # half the values go missing


def test_drift_report_flags_shifted_feature() -> None:
    from src.mlops.churn_training import build_training_frame

    X, _ = build_training_frame(_synthetic_transactions())
    shifted = X.copy()
    shifted["tpv_total"] = shifted["tpv_total"] * 10
    report = drift_report(X, shifted)
    assert report["features"]["tpv_total"]["status"] == "alert"
    assert "tpv_total" in report["features_in_alert"]
    assert report["features"]["segment"]["status"] == "ok"


def test_train_and_log_end_to_end(tmp_path) -> None:
    mlflow = pytest.importorskip("mlflow")
    from src.mlops.churn_training import train_and_log

    grid = [
        {"n_estimators": 20, "learning_rate": 0.1, "num_leaves": 7, "min_child_samples": 5},
        {"n_estimators": 20, "learning_rate": 0.1, "num_leaves": 3, "min_child_samples": 5},
    ]
    result = train_and_log(
        _synthetic_transactions(), tracking_uri=f"file:{tmp_path}/mlruns", experiment="test", grid=grid
    )
    assert result.best_params in grid
    assert 0 <= result.best_metrics["roc_auc"] <= 1

    client = mlflow.tracking.MlflowClient()
    parent = client.get_run(result.parent_run_id)
    assert "test_pr_auc" in parent.data.metrics
    assert "drift_max_psi" in parent.data.metrics
    children = client.search_runs(
        [parent.info.experiment_id], filter_string=f"tags.mlflow.parentRunId = '{result.parent_run_id}'"
    )
    assert len(children) == len(grid)
    assert all("val_pr_auc" in c.data.metrics for c in children)
    artifacts = {a.path for a in client.list_artifacts(result.parent_run_id)}
    assert {"drift_report.json", "feature_importance_gain.csv"} <= artifacts

    # Trusted types are recorded with the model at save time (skops), so
    # loading needs no pickle and no extra trust arguments.
    model = mlflow.sklearn.load_model(result.model_uri)
    assert hasattr(model, "predict_proba")
