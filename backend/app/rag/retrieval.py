import asyncio
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.rag.embeddings import EmbeddingProvider, EmbeddingUnavailableError
from app.repositories.chunks import DocumentChunkRepository
from app.scraping.models import SourceName

logger = logging.getLogger(__name__)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_content: str
    similarity: float = Field(ge=-1.0, le=1.0)
    document_title: str
    source: SourceName
    canonical_url: str
    metadata: dict[str, Any]


class VectorRetrievalService:
    def __init__(
        self, repository: DocumentChunkRepository, embeddings: EmbeddingProvider
    ) -> None:
        self._repository = repository
        self._embeddings = embeddings

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        source: SourceName | None = None,
    ) -> list[RetrievalResult]:
        clean_query = query.strip()
        if not clean_query:
            raise ValueError("Retrieval query cannot be empty")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        try:
            query_embedding = await asyncio.to_thread(
                self._embeddings.embed_query, clean_query
            )
        except EmbeddingUnavailableError:
            logger.warning(
                "Embedding provider unavailable; using lexical corpus fallback",
                extra={"source_filter": source.value if source is not None else None},
            )
            rows = await self._repository.search_lexical(
                clean_query, top_k=top_k, source=source
            )
        else:
            rows = await self._repository.search(query_embedding, top_k=top_k, source=source)
        return [
            RetrievalResult(
                chunk_content=row.chunk_content,
                similarity=max(-1.0, min(1.0, row.similarity)),
                document_title=row.document_title,
                source=row.source,
                canonical_url=row.canonical_url,
                metadata=row.metadata,
            )
            for row in rows
        ]
