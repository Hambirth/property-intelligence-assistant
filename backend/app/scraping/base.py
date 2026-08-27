from typing import Protocol

from app.scraping.client import SafeHttpClient
from app.scraping.models import DiscoveryResult, ScrapedDocument, SourceName
from app.scraping.robots import RobotsPolicy


class SourceScraper(Protocol):
    source: SourceName
    base_url: str
    allowed_hosts: frozenset[str]

    async def discover_urls(
        self,
        client: SafeHttpClient,
        robots: RobotsPolicy,
        *,
        limit: int,
    ) -> DiscoveryResult: ...

    def parse_page(self, url: str, html: str) -> ScrapedDocument: ...
