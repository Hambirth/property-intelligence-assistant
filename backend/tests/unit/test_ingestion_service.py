from hashlib import sha256

import pytest

from app.scraping.models import (
    DiscoveryResult,
    ErrorCategory,
    FetchResponse,
    ScrapedDocument,
    ScrapeFailure,
    SourceName,
)
from app.services.ingestion import IngestionService


class FakeClient:
    async def fetch_text(self, url: str, **_kwargs) -> FetchResponse:
        text = "User-agent: *\nAllow: /" if url.endswith("robots.txt") else "page"
        content_type = "text/plain" if url.endswith("robots.txt") else "text/html"
        return FetchResponse(url, url, 200, content_type, text, 1.0)


class PartiallyFailingScraper:
    source = SourceName.WASALT
    base_url = "https://wasalt.sa"
    allowed_hosts = frozenset({"wasalt.sa"})

    async def discover_urls(self, _client, _robots, *, limit: int) -> DiscoveryResult:
        return DiscoveryResult(
            urls=[
                "https://wasalt.sa/en/project/good",
                "https://wasalt.sa/en/project/bad",
            ][:limit]
        )

    def parse_page(self, url: str, _html: str) -> ScrapedDocument:
        if url.endswith("/bad"):
            raise ScrapeFailure(ErrorCategory.PARSE_ERROR, "invalid fixture", url=url)
        text = "A sufficiently detailed public property description for ingestion."
        return ScrapedDocument(
            source=self.source,
            url=url,
            canonical_url=url,
            title="Good project",
            text=text,
            content_hash=sha256(text.encode()).hexdigest(),
        )


@pytest.mark.asyncio
async def test_page_failure_is_isolated_from_successful_dry_run() -> None:
    service = IngestionService(
        FakeClient(),
        session=None,
        user_agent="PropertyIntelligenceBot/0.1",
        dry_run=True,
    )

    summary = await service.run_source(PartiallyFailingScraper(), limit=2)

    assert summary.discovered == 2
    assert summary.fetched == 2
    assert summary.rejected == 1
    assert summary.failed == 0
