from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.scraping.models import ScrapedDocument, UpsertAction


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, scraped: ScrapedDocument) -> UpsertAction:
        existing = await self._by_canonical_url(scraped)
        if existing is None:
            existing = await self._by_content_hash(scraped)
            if existing is not None:
                existing.scraped_at = scraped.scraped_at
                return UpsertAction.UNCHANGED

            self._session.add(
                Document(
                    source=scraped.source.value,
                    url=scraped.url,
                    canonical_url=scraped.canonical_url,
                    title=scraped.title,
                    content=scraped.text,
                    content_hash=scraped.content_hash,
                    metadata_=scraped.metadata.model_dump(mode="json", exclude_none=True),
                    scraped_at=scraped.scraped_at,
                )
            )
            await self._session.flush()
            return UpsertAction.INSERTED

        existing.scraped_at = scraped.scraped_at
        existing.url = scraped.url
        if existing.content_hash == scraped.content_hash:
            return UpsertAction.UNCHANGED

        existing.title = scraped.title
        existing.content = scraped.text
        existing.content_hash = scraped.content_hash
        existing.metadata_ = scraped.metadata.model_dump(mode="json", exclude_none=True)
        await self._session.flush()
        return UpsertAction.UPDATED

    async def _by_canonical_url(self, scraped: ScrapedDocument) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.source == scraped.source.value,
                Document.canonical_url == scraped.canonical_url,
            )
        )
        return result.scalar_one_or_none()

    async def _by_content_hash(self, scraped: ScrapedDocument) -> Document | None:
        result = await self._session.execute(
            select(Document).where(
                Document.source == scraped.source.value,
                Document.content_hash == scraped.content_hash,
            )
        )
        return result.scalar_one_or_none()
