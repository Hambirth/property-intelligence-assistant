from pathlib import Path

import pytest

from app.scraping.dar_global import DarGlobalScraper
from app.scraping.models import ErrorCategory, FetchResponse
from app.scraping.robots import RobotsPolicy
from app.scraping.wasalt import WasaltScraper

FIXTURES = Path(__file__).parents[1] / "fixtures"
USER_AGENT = "PropertyIntelligenceBot/0.1"


class SitemapClient:
    async def fetch_text(self, url: str, **_kwargs) -> FetchResponse:
        return FetchResponse(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="application/xml",
            text=(FIXTURES / "wasalt_sitemap.xml").read_text(),
            duration_ms=1.0,
        )


@pytest.mark.asyncio
async def test_wasalt_discovers_only_allowed_detail_pages() -> None:
    robots = RobotsPolicy.from_text(
        "https://wasalt.sa", USER_AGENT, (FIXTURES / "wasalt_robots.txt").read_text()
    )

    result = await WasaltScraper().discover_urls(SitemapClient(), robots, limit=10)

    assert result.urls == [
        "https://wasalt.sa/en/project/Jeddah/padel-living-100567",
        "https://wasalt.sa/en/property/sale/apartment-123",
    ]
    assert all("/search" not in url for url in result.urls)


@pytest.mark.asyncio
async def test_darglobal_discovery_stops_when_robots_is_unavailable() -> None:
    robots = RobotsPolicy.unavailable("https://darglobal.co.uk", USER_AGENT)

    result = await DarGlobalScraper().discover_urls(SitemapClient(), robots, limit=10)

    assert result.urls == []
    assert result.failures[0].category is ErrorCategory.ROBOTS_DISALLOWED
