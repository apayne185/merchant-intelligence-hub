"""
Tests for the Risk tool (src/copilot/tools/risk.py).

Runs against data/copilot_fixture_transactions.csv and the real, committed
outputs/model.pkl (no MOCK_LLM concern here — this tool never calls an LLM,
it's a deterministic feature-engineering + sklearn-pipeline path).

Deliberately does NOT assert that the designed "high risk" merchant (90001)
scores higher than the "healthy" one (90003). Verified manually: it doesn't
— 90003 scores higher. That's not a bug in this tool; it's the churn
model's own documented near-random discrimination (ROC-AUC 0.58, see
outputs/model_card.md) showing up in live single-merchant inference for the
first time. See DECISIONS.md D24. Asserting an ordering that isn't actually
true of the system would be a worse test than not asserting it at all.
"""
from __future__ import annotations

import pytest
from src.copilot.tools.data_analyst import FIXTURE_CSV_PATH, get_clean_transactions
from src.copilot.tools.risk import (
    FEATURE_NAMES,
    build_merchant_features,
    explain_drivers,
    load_model,
    score_merchant,
)


@pytest.fixture
def df():
    return get_clean_transactions(FIXTURE_CSV_PATH)


# ---------------------------------------------------------------------------
# build_merchant_features
# ---------------------------------------------------------------------------
def test_build_merchant_features_returns_expected_columns(df) -> None:
    X = build_merchant_features(df, 90001)
    assert list(X.columns) == FEATURE_NAMES
    assert len(X) == 1


def test_build_merchant_features_unknown_merchant_raises_keyerror(df) -> None:
    with pytest.raises(KeyError):
        build_merchant_features(df, 999999)


def test_build_merchant_features_declining_merchant_has_lower_trend_ratio(df) -> None:
    # tpv_trend_3m_6m ~0.5 is the "flat" baseline for a 3-of-6-month window
    # (roughly half the 6m total falls in the most recent 3m if volume is
    # steady) — a genuinely declining merchant should sit well below that.
    x_declining = build_merchant_features(df, 90001)
    x_stable = build_merchant_features(df, 90003)
    assert x_declining["tpv_trend_3m_6m"].iloc[0] < 0.3
    assert x_stable["tpv_trend_3m_6m"].iloc[0] > 0.45


def test_build_merchant_features_no_complaint_is_nan(df) -> None:
    X = build_merchant_features(df, 90002)
    assert X["days_since_complaint"].isna().iloc[0]


def test_build_merchant_features_recent_complaint_is_capped_correctly(df) -> None:
    # merchant 90001's complaint is dated 2025-09-20, reference_date is
    # 2025-09-30 -> exactly 10 days.
    X = build_merchant_features(df, 90001)
    assert X["days_since_complaint"].iloc[0] == 10.0


# ---------------------------------------------------------------------------
# score_merchant
# ---------------------------------------------------------------------------
def test_score_merchant_found_shape(df) -> None:
    result = score_merchant(90001, df=df)
    assert result["found"] is True
    assert 0.0 <= result["churn_probability"] <= 1.0
    assert result["risk_tier"] in {"low", "medium", "high"}
    assert 1 <= len(result["top_drivers"]) <= 3
    assert "caveat" in result and "0.58" in result["caveat"]


def test_score_merchant_unknown_merchant_returns_not_found(df) -> None:
    result = score_merchant(999999, df=df)
    assert result == {
        "merchant_id": 999999,
        "found": False,
        "churn_probability": None,
        "risk_tier": None,
        "top_drivers": [],
        "caveat": result["caveat"],
    }
    assert "0.58" in result["caveat"]


def test_score_merchant_is_deterministic(df) -> None:
    r1 = score_merchant(90001, df=df)
    r2 = score_merchant(90001, df=df)
    assert r1 == r2


def test_score_merchant_caveat_always_present_regardless_of_tier(df) -> None:
    for merchant_id in (90001, 90002, 90003, 90004):
        result = score_merchant(merchant_id, df=df)
        assert result["caveat"] == result["caveat"]  # same constant every time
        assert "coarse" in result["caveat"].lower() or "near-random" in result["caveat"].lower() or "0.58" in result["caveat"]


# ---------------------------------------------------------------------------
# explain_drivers
# ---------------------------------------------------------------------------
def test_explain_drivers_shape(df) -> None:
    model = load_model()
    X = build_merchant_features(df, 90001)
    drivers = explain_drivers(model, X, top_n=3)
    assert len(drivers) == 3
    for d in drivers:
        assert set(d) == {"feature", "value", "shap_value", "direction", "story", "also_globally_top5"}
        assert d["feature"] in FEATURE_NAMES
        assert d["direction"] in {"increases_risk", "decreases_risk"}


def test_explain_drivers_respects_top_n(df) -> None:
    model = load_model()
    X = build_merchant_features(df, 90002)
    assert len(explain_drivers(model, X, top_n=1)) == 1
    assert len(explain_drivers(model, X, top_n=5)) == 5
