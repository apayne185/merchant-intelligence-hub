"""
Request-context ASGI middleware (DECISIONS.md D16): request id, W3C trace-context
propagation, and one structured access-log line per request.

  - X-Request-ID: accepted from the caller/gateway if well-formed, else
    generated; echoed on the response and bound to every log line.
  - traceparent: an incoming W3C trace context (from an upstream gateway or
    service) is extracted, and this request's server span becomes its
    child, one trace end to end, not a new root per hop. The response
    carries `traceparent` back so a caller can find the trace.

Pure ASGI rather than Starlette's BaseHTTPMiddleware: contextvars set here
reliably reach the endpoint (including sync endpoints run in the
threadpool) and the access log is written even when the app raises.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from opentelemetry import propagate, trace
from src.copilot.infra.logging_config import request_id_var
from src.copilot.tracing import traced

access_logger = logging.getLogger("copilot.access")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")
# Probes and scrapes: no span, no access line, at a 10s scrape/probe
# interval per replica they'd drown the real traffic in both backends.
_QUIET_PATHS = frozenset({"/health", "/ready", "/metrics"})


class RequestContextMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        path = scope.get("path", "")
        quiet = path in _QUIET_PATHS
        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                out_headers = list(message.get("headers", []))
                out_headers.append((b"x-request-id", request_id.encode()))
                carrier: dict[str, str] = {}
                propagate.inject(carrier)
                out_headers.extend((k.encode(), v.encode()) for k, v in carrier.items())
                message["headers"] = out_headers
            await send(message)

        try:
            if quiet:
                await self.app(scope, receive, send_wrapper)
            else:
                with traced(
                    f"HTTP {scope.get('method', '')} {path}",
                    context=propagate.extract(headers),
                    kind=trace.SpanKind.SERVER,
                    **{"http.request.method": scope.get("method", ""), "url.path": path, "request_id": request_id},
                ) as span:
                    try:
                        await self.app(scope, receive, send_wrapper)
                    finally:
                        span.set_attribute("http.response.status_code", status_code)
                        self._log(scope, path, status_code, start)
        finally:
            request_id_var.reset(token)

    @staticmethod
    def _log(scope: dict[str, Any], path: str, status_code: int, start: float) -> None:
        client = scope.get("client")
        access_logger.info(
            "%s %s %s",
            scope.get("method"),
            path,
            status_code,
            extra={
                "http_method": scope.get("method"),
                "http_path": path,
                "http_status": status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                "client_ip": client[0] if client else None,
            },
        )
