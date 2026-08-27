import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.rag.chunking import DocumentChunkDraft
from app.scraping.models import SourceName


@dataclass(frozen=True, slots=True)
class ChunkSearchRow:
    chunk_content: str
    similarity: float
    document_title: str
    source: SourceName
    canonical_url: str
    metadata: dict[str, Any]


class DocumentChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_documents(self, source: SourceName | None = None) -> list[Document]:
        statement = select(Document).order_by(Document.source, Document.canonical_url)
        if source is not None:
            statement = statement.where(Document.source == source.value)
        result = await self._session.execute(statement)
        return list(result.scalars())

    async def list_for_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        result = await self._session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars())

    async def replace_document_chunks(
        self,
        document_id: uuid.UUID,
        drafts: Sequence[DocumentChunkDraft],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if len(drafts) != len(embeddings):
            raise ValueError("Chunk and embedding counts must match")
        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        self._session.add_all(
            DocumentChunk(
                document_id=document_id,
                chunk_index=draft.chunk_index,
                content=draft.content,
                content_hash=draft.content_hash,
                embedding=list(embedding),
                metadata_=draft.metadata.model_dump(mode="json"),
            )
            for draft, embedding in zip(drafts, embeddings, strict=True)
        )
        await self._session.flush()

    async def search(
        self,
        query_embedding: Sequence[float],
        *,
        top_k: int,
        source: SourceName | None = None,
    ) -> list[ChunkSearchRow]:
        distance = DocumentChunk.embedding.cosine_distance(list(query_embedding))
        statement = (
            select(DocumentChunk, Document, (1.0 - distance).label("similarity"))
            .join(Document, Document.id == DocumentChunk.document_id)
            .order_by(distance, DocumentChunk.id)
            .limit(top_k)
        )
        if source is not None:
            statement = statement.where(Document.source == source.value)
        rows = (await self._session.execute(statement)).all()
        return [
            ChunkSearchRow(
                chunk_content=chunk.content,
                similarity=float(similarity),
                document_title=document.title,
                source=SourceName(document.source),
                canonical_url=document.canonical_url,
                metadata=chunk.metadata_,
            )
            for chunk, document, similarity in rows
        ]
