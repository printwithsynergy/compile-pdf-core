"""Request-id middleware mirroring codex-pdf 1.5's planned shape.

Per spec §0 + IMPL-PLAN Phase 0 deliverable 0.2: every request gets a
correlation ID that flows from upstream callers through compile to codex
and back. ``X-Compile-Request-Id`` is the canonical header; missing IDs
are generated fresh; the value is echoed in the response header and added
to structured-log records.

The same middleware also stamps ``X-Compile-Instance-Id`` on responses so
operators can identify which replica answered the request during
multi-instance rollouts.
"""

from __future__ import annotations

import os
import secrets
import socket

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


def _resolve_instance_id() -> str:
    """``COMPILE_INSTANCE_ID`` env var wins; falls back to hostname.

    Used by both the middleware (response header) and the /healthz route.
    """
    explicit = os.environ.get("COMPILE_INSTANCE_ID", "").strip()
    if explicit:
        return explicit
    return socket.gethostname() or "unknown"


INSTANCE_ID = _resolve_instance_id()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Reads or generates ``X-Compile-Request-Id``, stores it on
    ``request.state.request_id``, echoes in response headers, and binds it
    to the structlog context so every log line correlates."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = (
            request.headers.get("X-Compile-Request-Id")
            or request.headers.get("x-compile-request-id")
            or secrets.token_hex(8)
        )
        request.state.request_id = request_id

        # Bind to structlog context so every log line during this request
        # carries the request_id automatically.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            instance_id=INSTANCE_ID,
            method=request.method,
            path=request.url.path,
        )
        # Also propagate any upstream codex request-id we received so the
        # full lint→compile→codex chain is queryable in logs.
        upstream_codex_request_id = request.headers.get("X-Codex-Request-Id")
        if upstream_codex_request_id:
            structlog.contextvars.bind_contextvars(
                upstream_codex_request_id=upstream_codex_request_id
            )

        response = await call_next(request)
        response.headers["X-Compile-Request-Id"] = request_id
        response.headers["X-Compile-Instance-Id"] = INSTANCE_ID
        return response
