"""
Tests for the Data Analyst tool (src/copilot/tools/data_analyst.py).

Runs against data/copilot_fixture_transactions.csv — the small, committed
fixture with 4 merchants of deliberately distinct profiles (see
data/README.md). No network calls, no OPENAI_API_KEY needed.
"""
from __future__ import annotations

import pytest
from src.copilot.tools.data_analyst import (
    FIXTURE_CSV_PATH,
    churn_rate_by_segment,
    get_clean_transactions,
    top_merchants_by_tpv,
    yoy_tpv_by_month,
)


@pytest.fixture
def df():
    return get_clean_transactions(FIXTURE_CSV_PATH)


# ---------------------------------------------------------------------------
# top_merchants_by_tpv
# ---------------------------------------------------------------------------
def test_top_merchants_by_tpv_ranks_highest_volume_first(df) -> None:
    results = top_merchants_by_tpv(df, "2025-07-01", "2025-09-30", limit=10)
    assert [r["merchant_id"] for r in results][:2] == [90004, 90002]


def test_top_merchants_by_tpv_high_risk_merchant_has_low_recent_tpv_and_approval(df) -> None:
    results = top_merchants_by_tpv(df, "2025-07-01", "2025-09-30", limit=10)
    by_id = {r["merchant_id"]: r for r in results}
    assert by_id[90001]["tpv"] < by_id[90004]["tpv"]
    assert by_id[90001]["approval_rate"] < 0.7


def test_top_merchants_by_tpv_respects_limit(df) -> None:
    results = top_merchants_by_tpv(df, "2024-01-01", "2025-09-30", limit=2)
    assert len(results) == 2


def test_top_merchants_by_tpv_segment_filter(df) -> None:
    results = top_merchants_by_tpv(df, "2024-01-01", "2025-09-30", segment="SMB", limit=10)
    assert {r["merchant_id"] for r in results} == {90001, 90003}


def test_top_merchants_by_tpv_mcc_filter(df) -> None:
    results = top_merchants_by_tpv(df, "2024-01-01", "2025-09-30", mcc="5411", limit=10)
    assert {r["merchant_id"] for r in results} == {90002}


def test_top_merchants_by_tpv_out_of_range_dates_returns_empty(df) -> None:
    assert top_merchants_by_tpv(df, "2030-01-01", "2030-12-31", limit=10) == []


def test_top_merchants_by_tpv_does_not_have_country_filter_param() -> None:
    # DECISIONS.md D23: the real schema has no `country` column (unlike the
    # hypothetical warehouse schema in parte2_sql.sql Q1) — asserting the
    # param genuinely doesn't exist, not just that it's unused.
    import inspect

    params = inspect.signature(top_merchants_by_tpv).parameters
    assert "country" not in params


# ---------------------------------------------------------------------------
# churn_rate_by_segment
# ---------------------------------------------------------------------------
def test_churn_rate_by_segment_shape(df) -> None:
    results = churn_rate_by_segment(df, min_merchants=1)
    by_segment = {r["segment"]: r for r in results}
    assert set(by_segment) == {"SMB", "Enterprise"}
    assert by_segment["SMB"]["n_merchants"] == 2
    assert by_segment["Enterprise"]["n_merchants"] == 2


def test_churn_rate_by_segment_smb_reflects_the_one_churner(df) -> None:
    results = churn_rate_by_segment(df, min_merchants=1)
    by_segment = {r["segment"]: r for r in results}
    assert by_segment["SMB"]["pct_churn"] == 0.5
    assert by_segment["Enterprise"]["pct_churn"] == 0.0


def test_churn_rate_by_segment_min_merchants_filters_out_small_segments(df) -> None:
    assert churn_rate_by_segment(df, min_merchants=100) == []


# ---------------------------------------------------------------------------
# yoy_tpv_by_month
# ---------------------------------------------------------------------------
def test_yoy_tpv_by_month_unknown_merchant_returns_empty(df) -> None:
    assert yoy_tpv_by_month(df, 999999) == []


def test_yoy_tpv_by_month_no_2024_data_has_null_prev_year(df) -> None:
    # merchant 90003 only has Apr-Sep 2025 data — deliberate edge case.
    results = yoy_tpv_by_month(df, 90003)
    assert len(results) == 6
    assert all(r["tpv_prev_year"] is None for r in results)
    assert all(r["tpv_yoy_pct"] is None for r in results)


def test_yoy_tpv_by_month_high_risk_merchant_shows_sharp_decline(df) -> None:
    results = yoy_tpv_by_month(df, 90001)
    by_month = {r["month"]: r for r in results}
    # Jul/Aug/Sep 2025 all have a 2024 comparison and all decline YoY —
    # this is the concrete "trending toward churn" signal a Risk tool answer
    # should be able to point to.
    for month in ("2025-07-01", "2025-08-01", "2025-09-01"):
        assert by_month[month]["tpv_prev_year"] is not None
        assert by_month[month]["tpv_yoy_pct"] < -0.4
    # Jan-Jun 2025 have no 2024 counterpart in this fixture (only Jul-Sep
    # 2024 was generated) — must not fabricate a comparison.
    assert by_month["2025-01-01"]["tpv_prev_year"] is None


def test_yoy_tpv_by_month_sorted_chronologically(df) -> None:
    results = yoy_tpv_by_month(df, 90002)
    months = [r["month"] for r in results]
    assert months == sorted(months)
