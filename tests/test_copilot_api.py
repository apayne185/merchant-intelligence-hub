"""HTTP contract of the copilot API (mock LLM mode)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from src.copilot.api import app, get_graph
from src.copilot.tracing import _request_span_buffer


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "model": "mock", "version": app.version}


def test_ask_returns_verified_cited_answer(client: TestClient) -> None:
    r = client.post("/ask", json={"question": "What was Apple's revenue and net margin in FY2025?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == ["fundamentals"]
    assert body["verification"]["status"] == "verified"
    assert body["verification"]["numbers_checked"] == body["verification"]["numbers_verified"] >= 2
    rev = next(e for e in body["evidence"] if e["id"].startswith("AAPL:revenue:FY2025"))
    assert rev["values"]["value"] == 416_161_000_000
    assert rev["source"]["accession"] == "0000320193-25-000079"
    assert rev["source"]["url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert f"[{rev['id']}]" in body["answer"]
    assert {t["node"] for t in body["trace"]} >= {"route", "fundamentals", "synthesize"}


def test_ask_point_in_time(client: TestClient) -> None:
    body = client.post("/ask", json={"question": "Apple diluted EPS for fiscal 2019", "as_of": "2020-06-01"}).json()
    assert "$11.89" in body["answer"] and body["as_of"] == "2020-06-01"


def test_ask_with_explicit_portfolio(client: TestClient) -> None:
    body = client.post("/ask", json={
        "question": "What is the expected shortfall of this portfolio?",
        "portfolio": [{"ticker": "jpm", "weight": 0.5}, {"ticker": "GS", "weight": 0.5}],
    }).json()
    assert body["route"] == ["market_risk"]
    assert body["tool_calls"][0]["args"]["positions"] == {"JPM": 0.5, "GS": 0.5}


@pytest.mark.parametrize("payload", [
    {},
    {"question": ""},
    {"question": "x" * 4001},
    {"question": "hi", "locale": "fr"},
    {"question": "hi", "as_of": "June 2024"},
    {"question": "hi", "portfolio": [{"ticker": "AAPL", "weight": 2.0}]},
])
def test_ask_validation(client: TestClient, payload: dict) -> None:
    assert client.post("/ask", json=payload).status_code == 422


def test_ask_failure_returns_502_without_leaking_trace(client: TestClient) -> None:
    class Boom:
        def invoke(self, state):
            raise RuntimeError("secret internal detail")

    buf = _request_span_buffer()
    buf._by_trace.clear()
    buf._order.clear()
    app.dependency_overrides[get_graph] = lambda: Boom()
    try:
        r = client.post("/ask", json={"question": "Apple revenue"})
    finally:
        app.dependency_overrides.pop(get_graph)
    assert r.status_code == 502 and "secret" not in r.text
    assert buf._by_trace == {}


def test_v1_facts_and_versions(client: TestClient) -> None:
    body = client.get("/v1/facts/aapl", params={"fiscal_year": 2025}).json()
    assert body["ticker"] == "AAPL" and body["fiscal_year"] == 2025
    assert any(e["id"].startswith("AAPL:net_margin") for e in body["derived"])
    v = client.get("/v1/facts/TSLA/capex/versions", params={"fiscal_year": 2024}).json()
    assert v["restated"] is True and [x["value"] for x in v["versions"]] == [11_339_000_000, 11_342_000_000]
    assert client.get("/v1/facts/ZZZZ").status_code == 404
    assert client.get("/v1/facts/AAPL/made_up/versions", params={"fiscal_year": 2024}).status_code == 404


def test_v1_risk(client: TestClient) -> None:
    r = client.post("/v1/risk", json={"portfolio": [{"ticker": "AAPL", "weight": 0.6}, {"ticker": "KO", "weight": 0.4}],
                                      "mc_paths": 5000})
    assert r.status_code == 200 and r.json()["backtest"]["observations"] > 0
    bad = client.post("/v1/risk", json={"portfolio": [{"ticker": "ZZZZ", "weight": 1.0}]})
    assert bad.status_code == 422
