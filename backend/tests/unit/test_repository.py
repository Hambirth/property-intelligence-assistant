from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.document import Document
from app.repositories.documents import DocumentRepository
from app.scraping.models import UpsertAction
from app.scraping.wasalt import WasaltScraper

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _scraped_document():
    return WasaltScraper().parse_page(
        "https://wasalt.sa/en/project/Jeddah/padel-living-100567",
        (FIXTURES / "wasalt_project.html").read_text(),
    )


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _session(*query_results):
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_result(value) for value in query_results])
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_new_document_is_inserted() -> None:
    session = _session(None, None)

    action = await DocumentRepository(session).upsert(_scraped_document())

    assert action is UpsertAction.INSERTED
    added = session.add.call_args.args[0]
    assert isinstance(added, Document)
    assert added.source == "wasalt"
    assert added.metadata_["developer"] == "Dar Global"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_unchanged_document_is_idempotent() -> None:
    scraped = _scraped_document()
    existing = Document(
        source="wasalt",
        url=scraped.url,
        canonical_url=scraped.canonical_url,
        title=scraped.title,
        content=scraped.text,
        content_hash=scraped.content_hash,
        metadata_={},
        scraped_at=scraped.scraped_at,
    )
    session = _session(existing)

    action = await DocumentRepository(session).upsert(scraped)

    assert action is UpsertAction.UNCHANGED
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_document_updates_content_and_hash() -> None:
    scraped = _scraped_document()
    existing = Document(
        source="wasalt",
        url=scraped.url,
        canonical_url=scraped.canonical_url,
        title="Old title",
        content="Old content",
        content_hash="0" * 64,
        metadata_={},
        scraped_at=scraped.scraped_at,
    )
    session = _session(existing)

    action = await DocumentRepository(session).upsert(scraped)

    assert action is UpsertAction.UPDATED
    assert existing.content == scraped.text
    assert existing.content_hash == scraped.content_hash
    assert existing.metadata_["external_reference"] == "100567"
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_identical_content_under_another_canonical_url_is_not_inserted() -> None:
    scraped = _scraped_document()
    existing = Document(
        source="wasalt",
        url="https://wasalt.sa/en/project/original",
        canonical_url="https://wasalt.sa/en/project/original",
        title=scraped.title,
        content=scraped.text,
        content_hash=scraped.content_hash,
        metadata_={},
        scraped_at=scraped.scraped_at,
    )
    session = _session(None, existing)

    action = await DocumentRepository(session).upsert(scraped)

    assert action is UpsertAction.UNCHANGED
    session.add.assert_not_called()
