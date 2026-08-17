"""
Risk tool — scores a merchant against the churn model and explains drivers.

`build_merchant_features()` ports (not imports — the notebook isn't
import-safe) the exact feature engineering from src/parte3_modeling.ipynb
cells 5 and 7, cited by cell number below so a future reader doesn't mistake
the duplication for accidental drift. `score_merchant()` loads
outputs/model.pkl (a joblib-pickled sklearn Pipeline whose ColumnTransformer
already does imputation/scaling/encoding — see model_card.md "Persistence")
and calls predict_proba() on the raw feature row; it must not re-implement
preprocessing. See DECISIONS.md D24.

Every result carries `caveat` forward from outputs/model_card.md's
"ROC-AUC 0.5828 (near-random), safe only for coarse deprioritization"
limitation — turning that documented weakness into a tool output someone
might act on is exactly where this project's honesty-about-limitations ethos
could quietly get lost if the caveat weren't threaded through explicitly.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from src.copilot.tools.data_analyst import get_clean_transactions

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUTS_DIR = REPO_ROOT / "outputs"
DEFAULT_MODEL_PATH = OUTPUTS_DIR / "model.pkl"
FEATURE_IMPORTANCE_PATH = OUTPUTS_DIR / "feature_importance.csv"

# Exact feature set/order from src/parte3_modeling.ipynb cell 7 — must match
# what outputs/model.pkl's ColumnTransformer was fit on.
FEATURES_NUM = [
    "tpv_total", "n_tx_total", "approval_rate_total", "n_months_active",
    "tpv_3m", "n_tx_3m", "approval_rate_3m", "pct_ecom_3m", "avg_tx_3m",
    "tpv_6m", "n_tx_6m", "approval_rate_6m", "pct_ecom_6m", "avg_tx_6m",
    "tpv_trend_3m_6m", "log_tpv_total", "log_tpv_3m", "days_since_complaint",
]
FEATURES_CAT = ["segment", "mcc"]
FEATURE_NAMES = FEATURES_NUM + FEATURES_CAT

# Same interpretive copy as notebook cell 13's FEATURE_STORY — duplicated
# here for the same reason the feature engineering is: the notebook isn't
# importable. Keep these two copies in sync if either changes.
FEATURE_STORY = {
    "days_since_complaint": "Recent complaint = strong churn signal.",
    "tpv_total": "Volume proxy: low all-time volume merchants churn more.",
    "tpv_trend_3m_6m": "Falling 3m-vs-6m TPV trend = early disengagement signal.",
    "avg_tx_6m": "Lower average ticket size over 6m = declining engagement.",
    "n_tx_total": "Activity tenure: fewer all-time transactions = higher risk.",
    "n_months_active": "Activity tenure: fewer active months = higher risk.",
    "tpv_6m": "Volume proxy: low 6m volume merchants churn more.",
    "approval_rate_total": "Declining approval = technical/fraud problems.",
    "approval_rate_3m": "Declining recent approval = technical/fraud problems.",
    "log_tpv_total": "Volume proxy (log-scaled): low-volume merchants churn more.",
    "avg_tx_3m": "Lower average ticket size over 3m = declining engagement.",
    "segment": "SMB structural risk vs Enterprise stickiness.",
    "mcc": "Merchant category correlates with churn risk.",
}

MODEL_CAVEAT = (
    "This model's discrimination is weak (ROC-AUC 0.58 on the held-out test "
    "set, close to random — see outputs/model_card.md). Treat this score as "
    "one coarse, low-confidence signal among several, not a verdict — safe "
    "only for deprioritizing at a high threshold (e.g. bottom 90%), not for "
    "precisely ranking who to call first."
)


def build_merchant_features(
    df: pd.DataFrame, merchant_id: int, reference_date: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Builds the 1-row, 20-column raw feature frame for `merchant_id`,
    replicating src/parte3_modeling.ipynb cells 5 and 7 exactly: 3m/6m
    trailing windows off `reference_date`, tpv_trend_3m_6m clipped to
    [0, 5], log1p-scaled TPV, and days_since_complaint capped at
    `reference_date` (the same T2 leakage guard load_clean's caller relies
    on elsewhere in this repo). Computed over the full `df` (matching how
    training built its feature matrix) then sliced to one row — not
    optimized for repeated single-merchant scoring at production scale, see
    DECISIONS.md D24.

    Raises KeyError if `merchant_id` has no transactions on or before
    `reference_date`.
    """
    ref_date = reference_date if reference_date is not None else df["reference_date"].max()
    pre = df[df["transaction_date"] <= ref_date]
    if merchant_id not in set(pre["merchant_id"]):
        raise KeyError(f"merchant_id {merchant_id} has no transactions on or before {ref_date.date()}")

    win_3m = ref_date - pd.Timedelta(days=90)
    win_6m = ref_date - pd.Timedelta(days=180)

    def agg_window(data: pd.DataFrame, window_start: pd.Timestamp, suffix: str) -> pd.DataFrame:
        w = data[data["transaction_date"] >= window_start]
        return w.groupby("merchant_id", observed=True).agg(**{
            f"tpv_{suffix}": ("amount", "sum"),
            f"n_tx_{suffix}": ("transaction_id", "count"),
            f"approval_rate_{suffix}": ("status", lambda x: (x == "approved").mean()),
            f"pct_ecom_{suffix}": ("channel", lambda x: (x == "ecom").mean()),
            f"avg_tx_{suffix}": ("amount", "mean"),
        })

    all_time = pre.groupby("merchant_id", observed=True).agg(
        tpv_total=("amount", "sum"),
        n_tx_total=("transaction_id", "count"),
        approval_rate_total=("status", lambda x: (x == "approved").mean()),
        n_months_active=("transaction_date", lambda x: x.dt.to_period("M").nunique()),
    )
    feats_3m = agg_window(pre, win_3m, "3m")
    feats_6m = agg_window(pre, win_6m, "6m")

    complaints = df.groupby("merchant_id", observed=True)["last_complaint_date"].first().reset_index()
    complaints["lcd_capped"] = complaints["last_complaint_date"].where(
        complaints["last_complaint_date"] <= ref_date
    )
    complaints["days_since_complaint"] = (ref_date - complaints["lcd_capped"]).dt.days
    complaints = complaints.set_index("merchant_id")[["days_since_complaint"]]

    meta = df.drop_duplicates("merchant_id").set_index("merchant_id")[["segment", "mcc"]]

    merchant_df = (
        all_time.join(feats_3m, how="left").join(feats_6m, how="left").join(complaints, how="left").join(meta, how="left")
    )

    merchant_df["tpv_trend_3m_6m"] = (
        merchant_df["tpv_3m"] / merchant_df["tpv_6m"].where(merchant_df["tpv_6m"] > 0)
    ).fillna(1.0).clip(0, 5)
    merchant_df["log_tpv_total"] = np.log1p(merchant_df["tpv_total"])
    merchant_df["log_tpv_3m"] = np.log1p(merchant_df["tpv_3m"].fillna(0))

    return merchant_df.loc[[merchant_id], FEATURE_NAMES]


_MODEL_CACHE: dict[str, Any] = {}


def load_model(model_path: str | Path | None = None) -> Any:
    """Cached joblib.load() of the churn pipeline, keyed by resolved path.

    outputs/model.pkl is a joblib-pickled sklearn object — deserialization
    executes arbitrary code (see model_card.md "Persistence"). Only ever
    point this at a model file from a trusted source (this repo's own
    outputs/), never an untrusted upload.
    """
    resolved = Path(model_path) if model_path is not None else DEFAULT_MODEL_PATH
    key = str(resolved.resolve())
    if key not in _MODEL_CACHE:
        import joblib

        _MODEL_CACHE[key] = joblib.load(resolved)
    return _MODEL_CACHE[key]


_EXPLAINER_CACHE: dict[int, Any] = {}


def _get_explainer(model: Any) -> Any:
    """Cached shap.TreeExplainer, keyed by the model object's identity (not
    a path — explain_drivers() only ever receives an already-loaded model,
    and load_model() itself returns the same cached object for a given
    path, so id(model) is a stable, correct key). Purely a function of the
    model, with no per-merchant dependency, so building a fresh one on
    every explain_drivers() call was pure repeated work (tree-structure
    parsing over the whole LightGBM booster) for an identical result every
    time."""
    key = id(model)
    if key not in _EXPLAINER_CACHE:
        import shap

        _EXPLAINER_CACHE[key] = shap.TreeExplainer(model.named_steps["clf"])
    return _EXPLAINER_CACHE[key]


_GLOBAL_IMPORTANCE_CACHE: list[str] | None = None


def _load_global_top_features(top_n: int = 5) -> list[str]:
    global _GLOBAL_IMPORTANCE_CACHE
    if _GLOBAL_IMPORTANCE_CACHE is None:
        if FEATURE_IMPORTANCE_PATH.exists():
            imp = pd.read_csv(FEATURE_IMPORTANCE_PATH)
            _GLOBAL_IMPORTANCE_CACHE = imp.sort_values("mean_abs_shap", ascending=False)["feature"].tolist()
        else:
            _GLOBAL_IMPORTANCE_CACHE = []
    return _GLOBAL_IMPORTANCE_CACHE[:top_n]


def explain_drivers(model: Any, X: pd.DataFrame, top_n: int = 3) -> list[dict[str, Any]]:
    """Per-instance SHAP for this merchant's row (shap is already a pinned
    dependency; the notebook already builds this exact TreeExplainer in
    cell 13 — this is not a new capability, just applied to one row instead
    of a test set). Blended with a one-line cross-reference to
    outputs/feature_importance.csv's global ranking — see DECISIONS.md D24
    for why per-instance beats global-only for "why is this merchant
    flagged" questions.
    """
    X_prep = model.named_steps["prep"].transform(X)
    explainer = _get_explainer(model)
    with warnings.catch_warnings():
        # Same known-benign warning src/parte3_modeling.ipynb silences
        # (cell 1): shap's LightGBM-binary-classifier output-format notice,
        # already handled by the isinstance check below.
        warnings.filterwarnings("ignore", category=UserWarning)
        shap_values = explainer.shap_values(X_prep)
    sv = shap_values[1] if isinstance(shap_values, list) else shap_values
    row_shap = np.asarray(sv)[0]

    global_top = set(_load_global_top_features())
    # lexsort with a fixed secondary key (original index), not plain
    # argsort: numpy's argsort isn't tie-break stable, so two features
    # landing at the exact same |shap_value| (plausible for tree-path-
    # unused features at 0.0) could otherwise flip which one ranks 3rd/4th
    # across runs/numpy versions — same nondeterminism class already fixed
    # in retrieval_core.py's SimpleVectorStore.query and the parte3
    # notebook's recall_at_k.
    order = np.lexsort((np.arange(len(row_shap)), -np.abs(row_shap)))[:top_n]

    drivers = []
    for idx in order:
        feat = FEATURE_NAMES[idx]
        drivers.append({
            "feature": feat,
            "value": X.iloc[0][feat],
            "shap_value": round(float(row_shap[idx]), 4),
            "direction": "increases_risk" if row_shap[idx] > 0 else "decreases_risk",
            "story": FEATURE_STORY.get(feat, ""),
            "also_globally_top5": feat in global_top,
        })
    return drivers


def _risk_tier(probability: float) -> str:
    """Coarse, illustrative bucketing — NOT a calibrated probability cutoff
    (model_card.md notes no calibration step was applied). Exists only so a
    synthesized answer has a human-readable label instead of a bare float.
    """
    if probability >= 0.20:
        return "high"
    if probability >= 0.10:
        return "medium"
    return "low"


def score_merchant(
    merchant_id: int,
    df: pd.DataFrame | None = None,
    model_path: str | Path | None = None,
) -> dict[str, Any]:
    """Scores `merchant_id` against the churn model. Returns
    `{"found": False, ...}` (mirrors src/parte4_api/agent.py's
    get_merchant_context "not found" shape) rather than raising, when the
    merchant has no transaction history — a normal "no data" case, not an
    error the caller should have to except-handle.
    """
    if df is None:
        df = get_clean_transactions()

    try:
        X = build_merchant_features(df, merchant_id)
    except KeyError:
        return {
            "merchant_id": merchant_id,
            "found": False,
            "churn_probability": None,
            "risk_tier": None,
            "top_drivers": [],
            "caveat": MODEL_CAVEAT,
        }

    model = load_model(model_path)
    probability = float(model.predict_proba(X)[:, 1][0])

    return {
        "merchant_id": merchant_id,
        "found": True,
        "churn_probability": round(probability, 4),
        "risk_tier": _risk_tier(probability),
        "top_drivers": explain_drivers(model, X, top_n=3),
        "caveat": MODEL_CAVEAT,
    }
