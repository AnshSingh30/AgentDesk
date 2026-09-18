"""Pure-ASGI middleware: request id, security headers, metrics, log context (safe for SSE streaming)."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import http_request_duration, http_requests_total

log = structlog.get_logger()

SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"strict-origin-when-cross-origin",
    b"x-frame-options": b"DENY",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
    b"cache-control": b"no-store",
}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode()[:64] or uuid.uuid4().hex
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        status_holder = {"status": 500}
        method = scope.get("method", "GET")
        path = scope.get("path", "")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers", []))
                raw.append((b"x-request-id", request_id.encode()))
                existing = {k.lower() for k, _ in raw}
                for k, v in SECURITY_HEADERS.items():
                    if k not in existing:
                        raw.append((k, v))
                if self.hsts and b"strict-transport-security" not in existing:
                    raw.append(
                        (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload")
                    )
                message["headers"] = raw
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            route = scope.get("route")
            route_path = getattr(route, "path", None) or path
            http_requests_total.labels(method, route_path, str(status_holder["status"])).inc()
            http_request_duration.labels(method, route_path).observe(duration)
            if not path.startswith(("/healthz", "/readyz", "/metrics")):
                log.info(
                    "request",
                    method=method,
                    path=path,
                    status=status_holder["status"],
                    duration_ms=round(duration * 1000, 1),
                )
