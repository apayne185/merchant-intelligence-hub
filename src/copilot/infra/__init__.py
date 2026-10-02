"""
Production platform layer for the copilot API: the cross-cutting concerns an
API gateway would normally own (auth, rate limiting, caching, audit, metrics,
structured logging, input guardrails), kept out of the graph and tools so
those stay framework-agnostic. See DECISIONS.md D12, D15, D16.

Every backing service (Redis, Postgres, OTel Collector) is optional: unset
its env var and the module falls back to an in-process or no-op
implementation, so `MOCK_LLM=1 uvicorn ...` and the test suite run with zero
infrastructure.
"""
