import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.documents import DocumentRepository
from app.scraping.base import SourceScraper
from app.scraping.client import SafeHttpClient
from app.scraping.deduplication import Deduplicator
from app.scraping.models import (
    ErrorCategory,
    IngestionSummary,
    PageFailure,
    ScrapedDocument,
    ScrapeFailure,
)
from app.scraping.robots import RobotsPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PageResult:
    fetched: bool
    document: ScrapedDocument | None = None
    failure: PageFailure | None = None


class IngestionService:
    def __init__(
        self,
        client: SafeHttpClient,
        *,
        session: AsyncSession | None,
        user_agent: str,
        dry_run: bool,
    ) -> None:
        self._client = client
        self._session = session
        self._user_agent = user_agent
        self._dry_run = dry_run

    async def run_source(self, scraper: SourceScraper, *, limit: int) -> IngestionSummary:
        summary = IngestionSummary()
        robots, robots_failure = await self._load_robots(scraper)
        if robots_failure is not None:
            self._record_failure(summary, robots_failure)
            return summary

        discovery = await scraper.discover_urls(self._client, robots, limit=limit)
        summary.discovered = len(discovery.urls)
        for failure in discovery.failures:
            self._record_failure(summary, failure)

        page_results = await asyncio.gather(
            *(self._fetch_and_parse(scraper, robots, url) for url in discovery.urls)
        )
        deduplicator = Deduplicator()
        for page_result in page_results:
            if page_result.fetched:
                summary.fetched += 1
            if page_result.failure is not None:
                self._record_failure(summary, page_result.failure)
                continue
            document = page_result.document
            if document is None:  # pragma: no cover - defensive invariant
                continue

            duplicate_reason = deduplicator.duplicate_reason(document)
            if duplicate_reason is not None:
                summary.unchanged += 1
                logger.info(
                    "Document duplicate skipped",
                    extra={
                        "source": scraper.source.value,
                        "url": document.url,
                        "action": duplicate_reason,
                    },
                )
                continue
            if self._dry_run:
                logger.info(
                    "Document parsed in dry-run mode",
                    extra={
                        "source": scraper.source.value,
                        "url": document.url,
                        "action": "dry_run",
                    },
                )
                continue
            await self._persist(document, summary)
        return summary

    async def _load_robots(
        self, scraper: SourceScraper
    ) -> tuple[RobotsPolicy, PageFailure | None]:
        robots_url = f"{scraper.base_url.rstrip('/')}/robots.txt"
        try:
            response = await self._client.fetch_text(
                robots_url,
                allowed_hosts=scraper.allowed_hosts,
                accepted_content_types=("text/plain", "text/html"),
            )
        except ScrapeFailure as exc:
            return RobotsPolicy.unavailable(scraper.base_url, self._user_agent), PageFailure(
                scraper.source, exc.url, exc.category, str(exc)
            )
        return (
            RobotsPolicy.from_text(scraper.base_url, self._user_agent, response.text),
            None,
        )

    async def _fetch_and_parse(
        self,
        scraper: SourceScraper,
        robots: RobotsPolicy,
        url: str,
    ) -> _PageResult:
        try:
            response = await self._client.fetch_text(
                url,
                allowed_hosts=scraper.allowed_hosts,
                robots_policy=robots,
            )
        except ScrapeFailure as exc:
            return _PageResult(
                fetched=False,
                failure=PageFailure(scraper.source, exc.url, exc.category, str(exc)),
            )
        try:
            document = scraper.parse_page(response.final_url, response.text)
            return _PageResult(fetched=True, document=document)
        except ScrapeFailure as exc:
            return _PageResult(
                fetched=True,
                failure=PageFailure(scraper.source, exc.url, exc.category, str(exc)),
            )
        except Exception:
            logger.exception(
                "Unexpected parser failure",
                extra={"source": scraper.source.value, "url": response.final_url},
            )
            return _PageResult(
                fetched=True,
                failure=PageFailure(
                    scraper.source,
                    response.final_url,
                    ErrorCategory.PARSE_ERROR,
                    "Unexpected parser failure",
                ),
            )

    async def _persist(self, document: ScrapedDocument, summary: IngestionSummary) -> None:
        if self._session is None:
            raise RuntimeError("A database session is required outside dry-run mode")
        try:
            async with self._session.begin():
                action = await DocumentRepository(self._session).upsert(document)
            summary.record_action(action)
            logger.info(
                "Document persistence completed",
                extra={
                    "source": document.source.value,
                    "url": document.url,
                    "action": action.value,
                },
            )
        except SQLAlchemyError:
            summary.failed += 1
            logger.exception(
                "Document persistence failed",
                extra={
                    "source": document.source.value,
                    "url": document.url,
                    "error_category": ErrorCategory.DATABASE_ERROR.value,
                },
            )

    @staticmethod
    def _record_failure(summary: IngestionSummary, failure: PageFailure) -> None:
        if failure.category in {ErrorCategory.ACCESS_BLOCKED, ErrorCategory.ROBOTS_DISALLOWED}:
            summary.blocked += 1
        elif failure.category in {ErrorCategory.INVALID_CONTENT, ErrorCategory.PARSE_ERROR}:
            summary.rejected += 1
        else:
            summary.failed += 1
        logger.warning(
            "Ingestion item was not processed",
            extra={
                "source": failure.source.value,
                "url": failure.url,
                "error_category": failure.category.value,
                "action": "skipped",
            },
        )
