import httpx
import pytest

from app.core.config import Settings
from app.scraping.client import SafeHttpClient
from app.scraping.models import ErrorCategory, ScrapeFailure
from app.scraping.robots import RobotsPolicy


def _settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        scraper_request_delay_seconds=0.1,
        scraper_max_retries=1,
        scraper_max_response_bytes=100_000,
        **overrides,
    )


async def _no_sleep(_delay: float) -> None:
    return None


@pytest.mark.asyncio
async def test_client_fetches_allowlisted_html() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<html><body><p>Public property content</p></body></html>",
        )
    )
    async with SafeHttpClient(_settings(), transport=transport, sleep=_no_sleep) as client:
        response = await client.fetch_text(
            "https://wasalt.sa/en/project/demo", allowed_hosts={"wasalt.sa"}
        )

    assert response.status_code == 200
    assert "Public property content" in response.text


@pytest.mark.asyncio
async def test_robots_disallowed_url_never_reaches_transport() -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="unused")

    policy = RobotsPolicy.from_text(
        "https://wasalt.sa",
        "PropertyIntelligenceBot/0.1",
        "User-agent: *\nAllow: /\nDisallow: /search",
    )
    async with SafeHttpClient(
        _settings(), transport=httpx.MockTransport(handler), sleep=_no_sleep
    ) as client:
        with pytest.raises(ScrapeFailure) as exc_info:
            await client.fetch_text(
                "https://wasalt.sa/search",
                allowed_hosts={"wasalt.sa"},
                robots_policy=policy,
            )

    assert exc_info.value.category is ErrorCategory.ROBOTS_DISALLOWED
    assert request_count == 0


@pytest.mark.asyncio
async def test_client_does_not_follow_cross_host_redirect() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "https://evil.example/admin"})
    )
    async with SafeHttpClient(_settings(), transport=transport, sleep=_no_sleep) as client:
        with pytest.raises(ScrapeFailure) as exc_info:
            await client.fetch_text(
                "https://wasalt.sa/en/project/demo", allowed_hosts={"wasalt.sa"}
            )

    assert exc_info.value.category is ErrorCategory.INVALID_URL


@pytest.mark.asyncio
async def test_client_does_not_retry_access_block() -> None:
    request_count = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(403, headers={"Content-Type": "text/html"}, text="blocked")

    async with SafeHttpClient(
        _settings(), transport=httpx.MockTransport(handler), sleep=_no_sleep
    ) as client:
        with pytest.raises(ScrapeFailure) as exc_info:
            await client.fetch_text(
                "https://wasalt.sa/en/project/demo", allowed_hosts={"wasalt.sa"}
            )

    assert exc_info.value.category is ErrorCategory.ACCESS_BLOCKED
    assert request_count == 1


@pytest.mark.asyncio
async def test_client_retries_429_and_honors_retry_after() -> None:
    request_count = 0
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="valid")

    async with SafeHttpClient(
        _settings(), transport=httpx.MockTransport(handler), sleep=record_sleep
    ) as client:
        response = await client.fetch_text(
            "https://wasalt.sa/en/project/demo", allowed_hosts={"wasalt.sa"}
        )

    assert response.status_code == 200
    assert request_count == 2
    assert 3.0 in sleeps


@pytest.mark.asyncio
async def test_client_rejects_antibot_html_and_large_response() -> None:
    anti_bot = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text="<title>Just a moment...</title>Enable JavaScript and cookies to continue",
        )
    )
    async with SafeHttpClient(_settings(), transport=anti_bot, sleep=_no_sleep) as client:
        with pytest.raises(ScrapeFailure) as exc_info:
            await client.fetch_text(
                "https://wasalt.sa/en/project/demo", allowed_hosts={"wasalt.sa"}
            )
    assert exc_info.value.category is ErrorCategory.ACCESS_BLOCKED

    too_large = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Length": "100001"},
            text="small",
        )
    )
    async with SafeHttpClient(_settings(), transport=too_large, sleep=_no_sleep) as client:
        with pytest.raises(ScrapeFailure) as exc_info:
            await client.fetch_text(
                "https://wasalt.sa/en/project/demo", allowed_hosts={"wasalt.sa"}
            )
    assert exc_info.value.category is ErrorCategory.INVALID_CONTENT
