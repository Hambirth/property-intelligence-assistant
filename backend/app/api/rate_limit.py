import asyncio
import math
import time
from dataclasses import dataclass
from ipaddress import ip_address

from fastapi import Request


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


@dataclass(slots=True)
class _Window:
    count: int
    resets_at: float


class FixedWindowRateLimiter:
    """Process-local limiter; multi-instance deployments need a shared backend."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = asyncio.Lock()
        self._checks = 0

    async def check(self, identity: str) -> RateLimitDecision:
        now = time.monotonic()
        async with self._lock:
            self._checks += 1
            if self._checks % 256 == 0:
                self._windows = {
                    key: value
                    for key, value in self._windows.items()
                    if value.resets_at > now
                }
            window = self._windows.get(identity)
            if window is None or now >= window.resets_at:
                window = _Window(count=0, resets_at=now + self._window_seconds)
                self._windows[identity] = window
            if window.count >= self._limit:
                return RateLimitDecision(
                    allowed=False,
                    limit=self._limit,
                    remaining=0,
                    retry_after_seconds=max(1, math.ceil(window.resets_at - now)),
                )
            window.count += 1
            return RateLimitDecision(
                allowed=True,
                limit=self._limit,
                remaining=self._limit - window.count,
                retry_after_seconds=max(1, math.ceil(window.resets_at - now)),
            )


def client_identity(request: Request, trusted_proxies: frozenset[str]) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer not in trusted_proxies:
        return peer
    try:
        forwarded = [
            str(ip_address(item.strip()))
            for item in request.headers.get("X-Forwarded-For", "").split(",")
            if item.strip()
        ]
    except ValueError:
        return peer
    for address in reversed(forwarded):
        if address not in trusted_proxies:
            return address
    return peer
