"""
Churn-model retraining with MLflow tracking + feature-drift monitoring
(DECISIONS.md D57).

What the notebook (src/parte3_modeling.ipynb) does once, interactively,
made repeatable and auditable:
  - features from src/copilot/tools/risk.py:build_feature_matrix() — the
    exact function the serving path uses, so train/serve skew is
    structurally impossible rather than kept in sync by hand;
  - same preprocessing Pipeline, split, seed and metrics as notebook cells
    7-11 (the baseline config below reproduces its hyperparameters);
  - a small hyperparameter sweep, one nested MLflow run per config,
    selected on validation PR-AUC (the metric that matters at ~9% churn
    prevalence — ROC-AUC is logged but flatters imbalanced problems);
  - Population Stability Index per feature, reference (train) vs current
    (holdout by default, or a new snapshot via `current_df`), logged as
    metrics + a drift_report.json artifact;
  - the best pipeline logged as an MLflow model (skops format, signature +
    input example), optionally registered in the Model Registry.

Deliberately does NOT overwrite outputs/model.pkl — promoting a retrained
model into the served artifact is a separate, human decision (the sklearn
pickle-compatibility constraint in .github/dependabot.yml is exactly why).
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder, StandardScaler
from src.copilot.tools.risk import FEATURE_NAMES, FEATURES_CAT, FEATURES_NUM, build_feature_matrix

SEED = 42
TARGET = "fla_churn90"
PSI_WARN, PSI_ALERT = 0.1, 0.2  # standard credit-risk PSI thresholds

# Baseline = notebook cell 9's exact config, so run 0 is directly comparable
# to outputs/metrics.json. The rest probe capacity/regularization.
DEFAULT_GRID: list[dict[str, Any]] = [
    {"n_estimators": 400, "learning_rate": 0.05, "num_leaves": 31, "min_child_samples": 20},
    {"n_estimators": 300, "learning_rate": 0.03, "num_leaves": 15, "min_child_samples": 50},
    {"n_estimators": 600, "learning_rate": 0.02, "num_leaves": 7, "min_child_samples": 100},
    {"n_estimators": 200, "learning_rate": 0.05, "num_leaves": 63, "min_child_samples": 20},
]

# Types skops must be told to trust when loading the logged model back —
# everything else in the pipeline is on skops' default safe list.
SKOPS_TRUSTED_TYPES = [
    "collections.OrderedDict",
    "lightgbm.basic.Booster",
    "lightgbm.sklearn.LGBMClassifier",
    "numpy.dtype",
    "sklearn.compose._column_transformer._RemainderColsList",
]


def build_training_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    X = build_feature_matrix(df)
    # Counts are int for merchants active in a window, NaN for the rest —
    # float throughout so the MLflow signature doesn't declare int64 and
    # then reject the NaNs the imputer exists to handle.
    X[FEATURES_NUM] = X[FEATURES_NUM].astype(float)
    y = df.drop_duplicates("merchant_id").set_index("merchant_id")[TARGET].reindex(X.index).astype(int)
    return X, y


def make_pipeline(params: dict[str, Any], pos_weight: float) -> Pipeline:
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), FEATURES_NUM),
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("encode", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)),
                    ]
                ),
                FEATURES_CAT,
            ),
        ],
        remainder="drop",
    )
    clf = lgb.LGBMClassifier(
        scale_pos_weight=pos_weight,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        random_state=SEED,
        n_jobs=-1,
        verbose=-1,
        **params,
    )
    return Pipeline([("prep", pre), ("clf", clf)])


def recall_at_k(y_true: pd.Series, y_score: npt.NDArray[np.float64], k: float) -> float:
    """Same deterministic tie-breaking as notebook cell 11."""
    n = max(1, int(len(y_true) * k))
    order = np.lexsort((np.arange(len(y_score)), -np.asarray(y_score)))
    positives = y_true.sum()
    return float(y_true.iloc[order[:n]].sum() / positives) if positives else 0.0


def evaluate(y_true: pd.Series, y_score: npt.NDArray[np.float64]) -> dict[str, float]:
    both_classes = y_true.nunique() == 2
    return {
        "roc_auc": float(roc_auc_score(y_true, y_score)) if both_classes else float("nan"),
        "pr_auc": float(average_precision_score(y_true, y_score)) if both_classes else float("nan"),
        "brier_score": float(brier_score_loss(y_true, y_score)),
        "recall_at_5pct": recall_at_k(y_true, y_score, 0.05),
        "recall_at_10pct": recall_at_k(y_true, y_score, 0.10),
    }


def psi(reference: pd.Series, current: pd.Series, bins: int = 10) -> float:
    """Population Stability Index. Numeric: quantile bins from the
    reference (so each holds ~10% of it). Categorical: one bin per level.
    Missing values get their own bin — a jump in nulls is drift too."""
    eps = 1e-6
    if reference.dtype.kind in "biuf":
        edges = np.unique(np.nanquantile(reference.dropna(), np.linspace(0, 1, bins + 1)))
        if len(edges) < 2:
            return 0.0
        edges[0], edges[-1] = -np.inf, np.inf

        def dist(s: pd.Series) -> npt.NDArray[np.float64]:
            counts = np.histogram(s.dropna(), bins=edges)[0]
            return np.append(counts, s.isna().sum()) / max(len(s), 1)
    else:
        levels = sorted(set(reference.dropna().astype(str)) | set(current.dropna().astype(str)))

        def dist(s: pd.Series) -> npt.NDArray[np.float64]:
            vc = s.astype(str).where(s.notna(), "__nan__").value_counts()
            return np.array([vc.get(lv, 0) for lv in [*levels, "__nan__"]]) / max(len(s), 1)

    ref, cur = dist(reference) + eps, dist(current) + eps
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def drift_report(reference: pd.DataFrame, current: pd.DataFrame) -> dict[str, Any]:
    psis = {col: round(psi(reference[col], current[col]), 5) for col in FEATURE_NAMES}

    def status(v: float) -> str:
        return "alert" if v >= PSI_ALERT else "warn" if v >= PSI_WARN else "ok"

    return {
        "thresholds": {"warn": PSI_WARN, "alert": PSI_ALERT},
        "n_reference": len(reference),
        "n_current": len(current),
        "max_psi": max(psis.values()),
        "features_in_alert": sorted(k for k, v in psis.items() if status(v) == "alert"),
        "features": {k: {"psi": v, "status": status(v)} for k, v in psis.items()},
    }


@dataclass
class TrainingResult:
    parent_run_id: str
    best_run_id: str
    best_params: dict[str, Any]
    best_metrics: dict[str, float]
    drift: dict[str, Any]
    model_uri: str


def train_and_log(
    df: pd.DataFrame,
    *,
    tracking_uri: str | None = None,
    experiment: str = "merchant-churn",
    grid: Sequence[dict[str, Any]] = DEFAULT_GRID,
    current_df: pd.DataFrame | None = None,
    register_as: str | None = None,
    data_source: str = "unknown",
) -> TrainingResult:
    import mlflow
    import mlflow.sklearn
    from mlflow.models import infer_signature

    if not grid:
        raise ValueError("grid must contain at least one hyperparameter config")

    if tracking_uri:
        if tracking_uri.startswith("file:"):
            # MLflow 3 refuses the file store unless explicitly opted in;
            # it's the offline/CI path, the server is the real one.
            os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)

    X, y = build_training_frame(df)
    # Same 80/20 stratified split + seed as notebook cell 7 — test stays
    # untouched until the final report; the sweep selects on a validation
    # slice carved out of train, so model selection can't overfit test.
    X_train_full, X_test, y_train_full, y_test = train_test_split(X, y, test_size=0.2, random_state=SEED, stratify=y)
    X_train, X_val, y_train, y_val = train_test_split(
        X_train_full, y_train_full, test_size=0.25, random_state=SEED, stratify=y_train_full
    )
    pos_weight = float((y_train == 0).sum() / max((y_train == 1).sum(), 1))

    with mlflow.start_run(run_name="churn-sweep") as parent:
        mlflow.set_tags({"data_source": data_source, "target": TARGET, "selection_metric": "val_pr_auc"})
        mlflow.log_params(
            {
                "n_merchants": len(X),
                "n_train": len(X_train),
                "n_val": len(X_val),
                "n_test": len(X_test),
                "churn_rate": round(float(y.mean()), 5),
                "scale_pos_weight": round(pos_weight, 3),
                "grid_size": len(grid),
            }
        )

        best: tuple[float, str, dict[str, Any]] | None = None
        for i, params in enumerate(grid):
            with mlflow.start_run(run_name=f"config-{i}", nested=True) as child:
                mlflow.log_params(params)
                pipe = make_pipeline(params, pos_weight).fit(X_train, y_train)
                val_metrics = evaluate(y_val, pipe.predict_proba(X_val)[:, 1])
                mlflow.log_metrics({f"val_{k}": v for k, v in val_metrics.items()})
                score = val_metrics["pr_auc"]
                score = -1.0 if np.isnan(score) else score
                if best is None or score > best[0]:
                    best = (score, child.info.run_id, params)

        if best is None:  # unreachable: grid is validated non-empty above
            raise RuntimeError("hyperparameter sweep produced no runs")
        _, best_run_id, best_params = best
        # Refit the winner on train+val, report once on the untouched test set.
        final = make_pipeline(best_params, pos_weight).fit(X_train_full, y_train_full)
        test_metrics = evaluate(y_test, final.predict_proba(X_test)[:, 1])
        mlflow.log_params({f"best_{k}": v for k, v in best_params.items()})
        mlflow.log_metrics({f"test_{k}": v for k, v in test_metrics.items()})

        current = build_feature_matrix(current_df) if current_df is not None else X_test
        drift = drift_report(X_train_full, current)
        mlflow.log_metrics({f"drift_psi_{k}": v["psi"] for k, v in drift["features"].items()})
        mlflow.log_metric("drift_max_psi", drift["max_psi"])
        mlflow.log_metric("drift_features_in_alert", len(drift["features_in_alert"]))

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "drift_report.json"
            report.write_text(json.dumps(drift, indent=2))
            mlflow.log_artifact(str(report))
            importance = pd.DataFrame(
                {"feature": FEATURE_NAMES, "gain": final.named_steps["clf"].booster_.feature_importance("gain")}
            ).sort_values("gain", ascending=False)
            imp_path = Path(tmp) / "feature_importance_gain.csv"
            importance.to_csv(imp_path, index=False)
            mlflow.log_artifact(str(imp_path))

        example = X_train_full.head(5)
        info = mlflow.sklearn.log_model(
            final,
            name="model",
            signature=infer_signature(example, final.predict_proba(example)[:, 1]),
            input_example=example,
            registered_model_name=register_as,
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
        )

    return TrainingResult(
        parent_run_id=parent.info.run_id,
        best_run_id=best_run_id,
        best_params=best_params,
        best_metrics=test_metrics,
        drift=drift,
        model_uri=info.model_uri,
    )
