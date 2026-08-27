import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import Message, Receive, Scope, Send

from app.api.rate_limit import client_identity

logger = logging.getLogger("app.chat")
_CHAT_PATHS = frozenset({"/api/chat", "/api/chat/stream"})


class ChatBodyLimitMiddleware:
    """Buffer only small public chat bodies and reject oversized payloads before JSON parsing."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, max_body_bytes: int) -> None:
        self.app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _CHAT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self._max_body_bytes:
            await self._reject(scope, receive, send)
            return

        received: list[Message] = []
        total = 0
        while True:
            message = await receive()
            received.append(message)
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self._max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break

        messages = iter(received)

        async def replay() -> Message:
            try:
                return next(messages)
            except StopIteration:
                return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id", "unavailable")
        response = JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": "request_too_large",
                    "message": "The request body exceeds the configured size limit.",
                    "request_id": request_id,
                }
            },
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            parsed = int(value)
        except ValueError:
            return None
        return max(0, parsed)
    return None


class ChatRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, trusted_proxies: frozenset[str]) -> None:
        super().__init__(app)
        self._trusted_proxies = trusted_proxies

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method != "POST" or request.url.path not in _CHAT_PATHS:
            return await call_next(request)
        limiter = request.app.state.chat_rate_limiter
        identity = client_identity(request, self._trusted_proxies)
        decision = await limiter.check(identity)
        request.state.chat_rate_limit_decision = decision
        logger.info(
            "Chat rate limit checked",
            extra={
                "endpoint": request.url.path,
                "rate_limit_allowed": decision.allowed,
                "rate_limit_remaining": decision.remaining,
            },
        )
        if decision.allowed:
            return await call_next(request)
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "Too many chat requests. Please try again later.",
                    "request_id": request.state.request_id,
                }
            },
            headers={
                "Retry-After": str(decision.retry_after_seconds),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "Cache-Control": "no-store",
            },
        )
