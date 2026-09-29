"""
Production platform layer for the copilot API — the cross-cutting concerns
an enterprise API gateway would normally own (auth, rate limiting, caching,
audit, metrics, structured logging, input guardrails), kept out of the
graph/tools so src/copilot/graph.py and src/copilot/tools/ stay exactly as
framework-agnostic as before. See DECISIONS.md D51-D55.

Every backing service here (Redis, Postgres, OTel Collector) is optional:
unset its env var and the module falls back to an in-process or no-op
implementation, so `MOCK_LLM=1 uvicorn ...` and the test suite still run
with zero infrastructure — same "runs locally with nothing else stood up"
philosophy as D17-D19/D26/D37.
"""
