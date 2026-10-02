"""Verifier, entity extraction, router, tools and the synthesis retry/fallback loop."""
from __future__ import annotations

import pytest
from src.copilot import synthesis
from src.copilot.entities import extract_fiscal_year, extract_metrics, extract_tickers, extract_weights
from src.copilot.formatting import fmt_value, pct, usd
from src.copilot.graph import build_graph
from src.copilot.router import route_mock, router_node
from src.copilot.schemas import Evidence
from src.copilot.state import initial_state
from src.copilot.tools.filing_search import run_filing_search
from src.copilot.tools.fundamentals import run_fundamentals
from src.copilot.tools.pretrade import run_pretrade_check
from src.copilot.verification import extract_claims, verify_answer
from src.filings.factstore import get_fact_store

EV = [
    Evidence(id="rev", kind="xbrl_fact", label="Revenue", values={"value": 416_161_000_000}),
    Evidence(id="margin", kind="derived_metric", label="Net margin", values={"value": 0.2691506}),
    Evidence(id="growth", kind="derived_metric", label="Growth", values={"value": -0.0312}),
    Evidence(id="eps", kind="xbrl_fact", label="EPS", values={"value": 7.46}),
    Evidence(id="var", kind="risk_metric", label="VaR", values={"var": 0.0404, "confidence": 0.99, "horizon_days": 10,
                                                                 "exceptions": 4, "de": 1.234}),
]


# ------------------------------------------------------------ verification
@pytest.mark.parametrize("answer", [
    "Revenue was $416.16 billion and net margin 26.9%.",
    "Revenue was $416.2B, about $416 billion; margin 27%.",
    "Revenue was 416,161 million.",
    "Revenue fell 3.1% year over year.",           # sign-insensitive for percentages
    "Revenue growth was -3.12%.",
    "Diluted EPS was $7.46.",
    "The 10-day 99% VaR is 4.04% with 4 exceptions.",
    "Leverage was 1.23x.",
    "Per the 10-K filed 2025-10-31 (accession 0000320193-25-000079), Item 1A, FY2025 and Q4 2025 were strong.",
])
def test_verifier_accepts_supported_numbers(answer: str) -> None:
    assert verify_answer(answer, EV).status in ("verified", "no_numeric_claims")


@pytest.mark.parametrize("answer,bad", [
    ("Revenue was $461.2 billion.", "$461.2 billion"),   # transposition
    ("Revenue was $416.3 billion.", "$416.3 billion"),   # off by 0.03%: wrong at the stated precision
    ("Net margin was 26.8%.", "26.8%"),
    ("Diluted EPS was $7.64.", "$7.64"),
    ("There were 5 exceptions.", "5"),
    ("Leverage was 1.32x.", "1.32x"),
])
def test_verifier_rejects_unsupported_numbers(answer: str, bad: str) -> None:
    report = verify_answer(answer, EV)
    assert report.status == "failed"
    assert [u.text for u in report.unverified] == [bad]


def test_claim_precision_and_spans() -> None:
    text = "Revenue $416.2 billion, margin 26.9%."
    usd_claim, pct_claim = extract_claims(text)
    assert usd_claim.tolerance == pytest.approx(0.05e9)
    assert pct_claim.tolerance == pytest.approx(0.0005)
    assert text[usd_claim.start:usd_claim.end] == "$416.2 billion"


def test_formatting_round_trips_through_the_verifier() -> None:
    for value, unit in [(416_161_000_000, "USD"), (-3_581_000_000, "USD"), (0.2691506, "ratio"), (7.46, "USD/shares"),
                        (1.234, "ratio_x"), (12_345_678, "USD"), (950.5, "USD"), (3.14159, None)]:
        ev = [Evidence(id="x", kind="xbrl_fact", label="x", values={"value": value})]
        assert verify_answer(f"It was {fmt_value(value, unit)}.", ev).status == "verified", (value, unit)
    assert usd(2.5e9) == "$2.50 billion" and pct(0.123) == "12.3%"


# ---------------------------------------------------------------- entities
def test_entity_extraction() -> None:
    q = "Compare Apple and JPMorgan with MSFT in FY2024; 40% NVDA, 35% in Tesla and 25% KO"
    assert extract_tickers(q) == ["AAPL", "JPM", "MSFT", "NVDA", "TSLA", "KO"]
    assert extract_fiscal_year(q) == 2024
    assert extract_weights(q) == {"NVDA": 0.40, "TSLA": 0.35, "KO": 0.25}
    assert extract_tickers("ko and gs in lowercase are not tickers") == []
    assert extract_metrics("revenue growth and net margin") == ["revenue_growth_yoy", "net_margin"]
    assert extract_metrics("what was revenue") == ["revenue"]


# ------------------------------------------------------------------ router
@pytest.mark.parametrize("q,expected", [
    ("Apple revenue in 2024", ["fundamentals"]),
    ("What does NVIDIA say about export controls?", ["filing_search"]),
    ("VaR of my portfolio", ["market_risk"]),
    ("Check pre-trade limits for 50% TSLA", ["pretrade_check"]),
    ("Does Apple's 10-K mention supply chain concentration?", ["filing_search"]),
    ("Can I buy 30% NVDA within our risk limits?", ["pretrade_check"]),
    ("Revenue growth and what risk factors are disclosed", ["fundamentals", "filing_search"]),
    ("hello", ["filing_search"]),
])
def test_route_mock(q: str, expected: list[str]) -> None:
    assert route_mock(q, [], has_portfolio=False) == expected


def test_router_defaults_equal_weights_and_dedupes_risk_tools() -> None:
    out = router_node(initial_state("Pre-trade limit check and VaR for Apple and Microsoft"))
    assert out["pending_tools"] == ["pretrade_check"]
    assert out["positions"] == {"AAPL": 0.5, "MSFT": 0.5}


# ------------------------------------------------------------------- tools
def test_fundamentals_reports_restatement_and_gaps() -> None:
    out = run_fundamentals(get_fact_store(), ["TSLA", "JPM"], ["capex", "gross_margin"], 2024, None)
    capex = next(e for e in out["evidence"] if e.id.startswith("TSLA:capex"))
    assert capex.values["originally_reported"] == 11_339_000_000 and capex.values["value"] == 11_342_000_000
    assert any("restated" in f for f in out["findings"])
    assert any("JPM" in g and "not computable" in g for g in out["gaps"])


def test_filing_search_scopes_to_requested_company_and_cites_numbers() -> None:
    out = run_filing_search("export controls on data center products", ["NVDA"], mock=True)
    assert out["evidence"] and all(e.ticker == "NVDA" and e.kind == "filing_passage" for e in out["evidence"])
    assert verify_answer(" ".join(out["findings"]), out["evidence"]).status != "failed"


def test_pretrade_check_decisions() -> None:
    reject = run_pretrade_check({"NVDA": 1.0}, None)
    assert reject["decision"] == "REJECT"
    approve = run_pretrade_check({"AAPL": 0.2, "JPM": 0.2, "XOM": 0.2, "JNJ": 0.2, "KO": 0.2}, None)
    assert approve["decision"] == "APPROVE"
    for out in (reject, approve):
        assert verify_answer(" ".join(out["findings"]), out["evidence"]).status == "verified"


# ------------------------------------------------- graph + synthesis loop
@pytest.fixture(scope="module")
def graph():
    return build_graph()


def test_graph_end_to_end_answers_verify(graph) -> None:
    result = graph.invoke(initial_state("Apple net margin and revenue in FY2025, and the VaR of 50% AAPL 50% MSFT"))
    assert [c["tool"] for c in result["tool_calls"]] == ["fundamentals", "market_risk"]
    assert result["verification"]["status"] == "verified"
    assert "$416.16 billion" in result["answer"]


def test_graph_reports_gap_when_no_company_identified(graph) -> None:
    result = graph.invoke(initial_state("What was revenue in 2024?"))
    assert "needs at least one covered company" in result["answer"]
    assert result["verification"]["status"] == "no_numeric_claims"


def _real_state(graph_state_question: str):
    from src.copilot.graph import _fundamentals

    state = initial_state(graph_state_question, tickers=["AAPL"], mock=False)
    state["fiscal_year"] = 2025
    out = _fundamentals(state)
    state["evidence"] = [e.model_dump() for e in out["evidence"]]
    state["findings"] = {"fundamentals": out["findings"]}
    return state


def test_real_synthesis_accepts_verified_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(synthesis, "_llm", lambda p, loc, c: calls.append(c) or "Revenue was $416.16 billion.")
    out = synthesis.synthesize_node(_real_state("Apple revenue"))
    assert out["verification"]["status"] == "verified" and calls == ["synthesis"]


def test_real_synthesis_retries_then_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    drafts = iter(["Revenue was $461.2 billion.", "Revenue was $416.2 billion."])
    prompts: list[str] = []
    monkeypatch.setattr(synthesis, "_llm", lambda p, loc, c: prompts.append(p) or next(drafts))
    out = synthesis.synthesize_node(_real_state("Apple revenue"))
    assert out["verification"]["status"] == "verified" and out["verification"]["attempts"] == 2
    assert "$461.2 billion" in prompts[1]  # the retry is told exactly what was wrong


def test_real_synthesis_falls_back_to_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(synthesis, "_llm", lambda p, loc, c: "Revenue was $999 billion.")
    out = synthesis.synthesize_node(_real_state("Apple revenue"))
    v = out["verification"]
    assert v["fallback_used"] and v["status"] == "verified"
    assert "$999" not in out["answer"] and "$416.16 billion" in out["answer"]
