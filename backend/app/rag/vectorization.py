import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.chunking import ChunkingConfig, DocumentChunkDraft, chunk_document, embedding_text
from app.rag.embeddings import EmbeddingProvider
from app.repositories.chunks import DocumentChunkRepository
from app.scraping.models import SourceName

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    id: uuid.UUID
    source: str
    canonical_url: str
    title: str
    content: str
    content_hash: str
    metadata: dict[str, Any]


class VectorizationSummary(BaseModel):
    documents_seen: int = 0
    documents_processed: int = 0
    documents_unchanged: int = 0
    chunks_inserted: int = 0
    chunks_replaced: int = 0
    chunks_skipped: int = 0
    failures: int = 0
    vectorization_seconds: float = 0.0


class VectorizationService:
    def __init__(
        self,
        session: AsyncSession,
        embeddings: EmbeddingProvider,
        chunking_config: ChunkingConfig,
    ) -> None:
        self._session = session
        self._embeddings = embeddings
        self._config = chunking_config
        self._repository = DocumentChunkRepository(session)

    async def run(self, *, source: SourceName | None = None) -> VectorizationSummary:
        started = time.perf_counter()
        summary = VectorizationSummary()
        stored_documents = await self._repository.list_documents(source)
        documents = [
            DocumentSnapshot(
                id=document.id,
                source=document.source,
                canonical_url=document.canonical_url,
                title=document.title,
                content=document.content,
                content_hash=document.content_hash,
                metadata=dict(document.metadata_),
            )
            for document in stored_documents
        ]
        await self._session.commit()
        summary.documents_seen = len(documents)

        for document in documents:
            try:
                source_type = str(document.metadata.get("source_format", "unknown"))
                drafts = chunk_document(
                    document.content,
                    document_id=str(document.id),
                    source=document.source,
                    canonical_url=document.canonical_url,
                    title=document.title,
                    source_type=source_type,
                    property_metadata=document.metadata,
                    document_content_hash=document.content_hash,
                    embedding_model=self._embeddings.model_name,
                    config=self._config,
                )
                if not drafts:
                    raise ValueError("Document produced no non-empty chunks")

                existing = await self._repository.list_for_document(document.id)
                if _is_unchanged(existing, drafts):
                    summary.documents_unchanged += 1
                    summary.chunks_skipped += len(existing)
                    await self._session.commit()
                    continue

                vectors = await asyncio.to_thread(
                    self._embeddings.embed_documents,
                    [embedding_text(draft) for draft in drafts],
                )
                previous_count = len(existing)
                await self._repository.replace_document_chunks(document.id, drafts, vectors)
                await self._session.commit()
                summary.documents_processed += 1
                if previous_count:
                    summary.chunks_replaced += len(drafts)
                else:
                    summary.chunks_inserted += len(drafts)
            except Exception:
                summary.failures += 1
                await self._session.rollback()
                logger.exception(
                    "Document vectorization failed",
                    extra={
                        "source": document.source,
                        "url": document.canonical_url,
                        "action": "failed",
                    },
                )

        summary.vectorization_seconds = round(time.perf_counter() - started, 6)
        return summary


def _is_unchanged(
    existing: list[Any], drafts: list[DocumentChunkDraft]
) -> bool:
    if len(existing) != len(drafts):
        return False
    for stored, draft in zip(existing, drafts, strict=True):
        if (
            stored.chunk_index != draft.chunk_index
            or stored.content_hash != draft.content_hash
            or stored.metadata_ != draft.metadata.model_dump(mode="json")
        ):
            return False
    return True
