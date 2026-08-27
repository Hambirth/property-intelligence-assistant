import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models.chunk import EMBEDDING_DIMENSION
from app.models.document import Document
from app.rag.chunking import ChunkMetadata, DocumentChunkDraft
from app.repositories.chunks import DocumentChunkRepository
from app.scraping.models import SourceName

pytestmark = pytest.mark.integration


@pytest.fixture
async def postgres_session():
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("set RUN_POSTGRES_INTEGRATION=1 to run real PostgreSQL tests")
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
            await engine.dispose()


def _document(source: SourceName, suffix: str) -> Document:
    return Document(
        id=uuid.uuid4(),
        source=source.value,
        url=f"https://example.invalid/{suffix}",
        canonical_url=f"https://example.invalid/{suffix}",
        title=f"Vector test {suffix}",
        content="Integration-only vector test content.",
        content_hash=suffix.ljust(64, "0")[:64],
        metadata_={"source_format": "txt"},
        scraped_at=datetime.now(UTC),
    )


def _draft(document: Document, content: str, index: int = 0) -> DocumentChunkDraft:
    metadata = ChunkMetadata(
        source=document.source,
        document_id=str(document.id),
        canonical_url=document.canonical_url,
        title=document.title,
        source_type="txt",
        property_metadata={},
        chunk_index=index,
        document_content_hash=document.content_hash,
        pipeline_fingerprint="f" * 64,
    )
    return DocumentChunkDraft(
        chunk_index=index,
        content=content,
        content_hash=str(index).ljust(64, "0"),
        metadata=metadata,
    )


async def test_real_pgvector_similarity_order_and_source_filter(
    postgres_session: AsyncSession,
) -> None:
    darglobal = _document(SourceName.DAR_GLOBAL, "exact-darglobal")
    wasalt = _document(SourceName.WASALT, "near-wasalt")
    postgres_session.add_all([darglobal, wasalt])
    await postgres_session.flush()
    repository = DocumentChunkRepository(postgres_session)
    exact = [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
    near = [0.999] + [0.0] * (EMBEDDING_DIMENSION - 2) + [0.0447]
    await repository.replace_document_chunks(darglobal.id, [_draft(darglobal, "exact")], [exact])
    await repository.replace_document_chunks(wasalt.id, [_draft(wasalt, "near")], [near])

    results = await repository.search(exact, top_k=2)
    wasalt_only = await repository.search(exact, top_k=1, source=SourceName.WASALT)

    assert results[0].canonical_url == darglobal.canonical_url
    assert results[0].similarity > results[1].similarity
    assert wasalt_only[0].canonical_url == wasalt.canonical_url


async def test_real_postgres_replaces_changed_document_chunks_atomically(
    postgres_session: AsyncSession,
) -> None:
    document = _document(SourceName.WASALT, "replacement")
    postgres_session.add(document)
    await postgres_session.flush()
    repository = DocumentChunkRepository(postgres_session)
    vector = [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
    await repository.replace_document_chunks(
        document.id,
        [_draft(document, "old zero", 0), _draft(document, "old one", 1)],
        [vector, vector],
    )
    await repository.replace_document_chunks(
        document.id, [_draft(document, "new only", 0)], [vector]
    )

    stored = await repository.list_for_document(document.id)

    assert len(stored) == 1
    assert stored[0].content == "new only"
