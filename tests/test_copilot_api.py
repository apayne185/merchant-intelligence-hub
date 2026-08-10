"""
Tests for the copilot API (src/copilot/api.py).

Must pass with MOCK_LLM=1 — no OpenAI dependency for CI. Uses the
force_fixture_csv fixture (tests/conftest.py) per-test so results are
deterministic regardless of whether the real (gitignored)
transactions_sample.csv happens to be present locally.

Run with:
    MOCK_LLM=1 pytest -v tests/test_copilot_api.py
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Forzamos MOCK_LLM antes de importar la app, por si lee el env en import
# time — mismo patrón defensivo que tests/test_api.py.
os.environ.setdefault("MOCK_LLM", "1")

from src.copilot.api import app  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# -----------------------------------------------------------------------------
# /health
# -----------------------------------------------------------------------------
def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "model" in body
    assert "version" in body


# -----------------------------------------------------------------------------
# /ask — happy paths
# -----------------------------------------------------------------------------
def test_ask_flagship_question(client: TestClient, force_fixture_csv: None) -> None:
    payload = {
        "question": (
            "Which merchants are trending toward churn and why, and does anything "
            "in our onboarding policy flag them?"
        ),
    }
    r = client.post("/ask", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["route"]) == {"risk", "grounding"}
    assert len(body["citations"]) > 0
    assert any(c["source_type"] == "model_output" for c in body["citations"])
    assert any(c["source_type"] == "policy_doc" for c in body["citations"])
    assert "0.58" in body["answer"]
    assert body["mode"] == "mock"
    assert body["latency_ms"] >= 0


def test_ask_merchant_specific_question(client: TestClient, force_fixture_csv: None) -> None:
    payload = {"question": "Is this merchant at risk of churning and why?", "merchant_id": 90001}
    r = client.post("/ask", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "data_analyst" in body["route"]
    assert "risk" in body["route"]
    assert "90001" in body["answer"]


def test_ask_complaint_routes_to_classifier_only(client: TestClient, force_fixture_csv: None) -> None:
    payload = {
        "question": "I want to cancel my account, this is unacceptable.",
        "merchant_id": 90001,
        "locale": "en",
    }
    r = client.post("/ask", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route"] == ["complaint_classifier"]


# -----------------------------------------------------------------------------
# /ask — response shape / contract
# -----------------------------------------------------------------------------
def test_ask_response_matches_schema_fields(client: TestClient, force_fixture_csv: None) -> None:
    r = client.post("/ask", json={"question": "What does onboarding require?"})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"question", "route", "answer", "citations", "tool_calls", "mode", "latency_ms"}
    for c in body["citations"]:
        assert set(c.keys()) == {"source_type", "id", "title", "excerpt"}
    for tc in body["tool_calls"]:
        assert set(tc.keys()) == {"tool", "args", "summary"}


def test_ask_invalid_input_missing_question(client: TestClient) -> None:
    r = client.post("/ask", json={"merchant_id": 1})
    assert r.status_code == 422


def test_ask_invalid_locale(client: TestClient) -> None:
    r = client.post("/ask", json={"question": "hi", "locale": "fr"})
    assert r.status_code == 422


def test_ask_no_match_still_returns_200_with_fallback_answer(client: TestClient, force_fixture_csv: None) -> None:
    r = client.post("/ask", json={"question": "hello there"})
    assert r.status_code == 200
    assert r.json()["answer"]
