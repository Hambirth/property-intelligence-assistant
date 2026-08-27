import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import Settings
from app.scraping.models import ErrorCategory, FetchResponse, ScrapeFailure
from app.scraping.normalization import is_access_blocked
from app.scraping.robots import RobotsPolicy
from app.scraping.url_policy import normalize_and_validate_url

logger = logging.getLogger(__name__)
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


class SafeHttpClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(settings.scraper_max_concurrency)
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        timeout = httpx.Timeout(
            connect=settings.scraper_connect_timeout_seconds,
            read=settings.scraper_read_timeout_seconds,
            write=settings.scraper_connect_timeout_seconds,
            pool=settings.scraper_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={
                "User-Agent": settings.scraper_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9",
            },
        )

    async def __aenter__(self) -> "SafeHttpClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_text(
        self,
        url: str,
        *,
        allowed_hosts: Collection[str],
        accepted_content_types: tuple[str, ...] = ("text/html", "application/xhtml+xml"),
        robots_policy: RobotsPolicy | None = None,
    ) -> FetchResponse:
        normalized_url = normalize_and_validate_url(url, allowed_hosts)
        if robots_policy is not None and not robots_policy.can_fetch(normalized_url):
            raise ScrapeFailure(
                ErrorCategory.ROBOTS_DISALLOWED,
                "robots.txt does not permit this URL",
                url=normalized_url,
            )

        last_failure: ScrapeFailure | None = None
        for attempt in range(self._settings.scraper_max_retries + 1):
            try:
                return await self._fetch_once(
                    normalized_url,
                    allowed_hosts=allowed_hosts,
                    accepted_content_types=accepted_content_types,
                    robots_policy=robots_policy,
                )
            except ScrapeFailure as exc:
                last_failure = exc
                if not exc.retryable or attempt >= self._settings.scraper_max_retries:
                    raise
                delay = exc.retry_after_seconds
                if delay is None:
                    delay = min(2**attempt, 8)
                await self._sleep(min(delay, 30))
        if last_failure is not None:  # pragma: no cover - loop always returns or raises
            raise last_failure
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _fetch_once(
        self,
        url: str,
        *,
        allowed_hosts: Collection[str],
        accepted_content_types: tuple[str, ...],
        robots_policy: RobotsPolicy | None,
    ) -> FetchResponse:
        started_at = time.perf_counter()
        current_url = url

        async with self._semaphore:
            try:
                async with asyncio.timeout(self._settings.scraper_total_timeout_seconds):
                    for _redirect in range(6):
                        await self._respect_delay()
                        async with self._client.stream("GET", current_url) as response:
                            if response.status_code in _REDIRECT_STATUS_CODES:
                                location = response.headers.get("Location")
                                if not location:
                                    raise ScrapeFailure(
                                        ErrorCategory.HTTP_ERROR,
                                        "Redirect response had no Location header",
                                        url=current_url,
                                    )
                                next_url = normalize_and_validate_url(
                                    urljoin(current_url, location), allowed_hosts
                                )
                                if urlsplit(next_url).hostname != urlsplit(current_url).hostname:
                                    raise ScrapeFailure(
                                        ErrorCategory.INVALID_URL,
                                        "Cross-host redirects are forbidden",
                                        url=next_url,
                                    )
                                if robots_policy is not None and not robots_policy.can_fetch(
                                    next_url
                                ):
                                    raise ScrapeFailure(
                                        ErrorCategory.ROBOTS_DISALLOWED,
                                        "Redirect target is disallowed by robots.txt",
                                        url=next_url,
                                    )
                                current_url = next_url
                                continue
                            return await self._validate_response(
                                response,
                                requested_url=url,
                                current_url=current_url,
                                accepted_content_types=accepted_content_types,
                                started_at=started_at,
                            )
                    raise ScrapeFailure(
                        ErrorCategory.HTTP_ERROR,
                        "Too many redirects",
                        url=current_url,
                    )
            except TimeoutError as exc:
                raise ScrapeFailure(
                    ErrorCategory.TIMEOUT,
                    "Request exceeded the total timeout",
                    url=current_url,
                    retryable=True,
                ) from exc
            except httpx.TimeoutException as exc:
                raise ScrapeFailure(
                    ErrorCategory.TIMEOUT,
                    "HTTP request timed out",
                    url=current_url,
                    retryable=True,
                ) from exc
            except httpx.TransportError as exc:
                raise ScrapeFailure(
                    ErrorCategory.HTTP_ERROR,
                    "Transient HTTP transport error",
                    url=current_url,
                    retryable=True,
                ) from exc

    async def _validate_response(
        self,
        response: httpx.Response,
        *,
        requested_url: str,
        current_url: str,
        accepted_content_types: tuple[str, ...],
        started_at: float,
    ) -> FetchResponse:
        status_code = response.status_code
        if status_code in {401, 403}:
            raise ScrapeFailure(
                ErrorCategory.ACCESS_BLOCKED,
                f"Remote access was blocked with HTTP {status_code}",
                url=current_url,
            )
        if status_code in _TRANSIENT_STATUS_CODES:
            raise ScrapeFailure(
                ErrorCategory.HTTP_ERROR,
                f"Transient upstream HTTP {status_code}",
                url=current_url,
                retryable=True,
                retry_after_seconds=_retry_after_seconds(response.headers.get("Retry-After")),
            )
        if status_code < 200 or status_code >= 300:
            raise ScrapeFailure(
                ErrorCategory.HTTP_ERROR,
                f"Unexpected upstream HTTP {status_code}",
                url=current_url,
            )

        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            if int(content_length) > self._settings.scraper_max_response_bytes:
                raise ScrapeFailure(
                    ErrorCategory.INVALID_CONTENT,
                    "Response exceeded the configured size limit",
                    url=current_url,
                )
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self._settings.scraper_max_response_bytes:
                raise ScrapeFailure(
                    ErrorCategory.INVALID_CONTENT,
                    "Response exceeded the configured size limit",
                    url=current_url,
                )

        encoding = response.charset_encoding or "utf-8"
        text = bytes(body).decode(encoding, errors="replace")
        if is_access_blocked(text):
            raise ScrapeFailure(
                ErrorCategory.ACCESS_BLOCKED,
                "Anti-bot interstitial detected",
                url=current_url,
            )

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if not any(content_type == accepted for accepted in accepted_content_types):
            raise ScrapeFailure(
                ErrorCategory.INVALID_CONTENT,
                f"Unexpected content type: {content_type or 'missing'}",
                url=current_url,
            )

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "Scraper fetch completed",
            extra={
                "url": current_url,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "action": "fetched",
            },
        )
        return FetchResponse(
            requested_url=requested_url,
            final_url=current_url,
            status_code=status_code,
            content_type=content_type,
            text=text,
            duration_ms=duration_ms,
        )

    async def _respect_delay(self) -> None:
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._settings.scraper_request_delay_seconds - elapsed
            if remaining > 0:
                await self._sleep(remaining)
            self._last_request_at = time.monotonic()


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return max(0.0, retry_at.timestamp() - time.time())
