from starlette.requests import Request

from app.api import rate_limit
from app.api.rate_limit import FixedWindowRateLimiter, client_identity


async def test_fixed_window_allows_limit_blocks_excess_and_resets(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: now)
    limiter = FixedWindowRateLimiter(limit=2, window_seconds=10)

    first = await limiter.check("client")
    second = await limiter.check("client")
    blocked = await limiter.check("client")
    now = 111.0
    reset = await limiter.check("client")

    assert first.allowed and first.remaining == 1
    assert second.allowed and second.remaining == 0
    assert not blocked.allowed and blocked.retry_after_seconds == 10
    assert reset.allowed and reset.remaining == 1


def _request(peer: str, headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": headers,
            "client": (peer, 1234),
        }
    )


def test_untrusted_peer_cannot_spoof_forwarded_identity() -> None:
    request = _request("198.51.100.10", [(b"x-forwarded-for", b"203.0.113.99")])

    assert client_identity(request, frozenset()) == "198.51.100.10"


def test_trusted_proxy_uses_only_valid_first_forwarded_ip() -> None:
    valid = _request(
        "127.0.0.1",
        [(b"x-forwarded-for", b"203.0.113.8, 127.0.0.1")],
    )
    malformed = _request("127.0.0.1", [(b"x-forwarded-for", b"fake-client")])

    assert client_identity(valid, frozenset({"127.0.0.1"})) == "203.0.113.8"
    assert client_identity(malformed, frozenset({"127.0.0.1"})) == "127.0.0.1"


def test_trusted_proxy_handles_ipv6_and_skips_trusted_forwarding_hops() -> None:
    request = _request(
        "127.0.0.1",
        [(b"x-forwarded-for", b"2001:db8::42, 10.0.0.5, 127.0.0.1")],
    )

    assert client_identity(
        request, frozenset({"127.0.0.1", "10.0.0.5"})
    ) == "2001:db8::42"
