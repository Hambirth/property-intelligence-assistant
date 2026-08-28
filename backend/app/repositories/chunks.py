import re
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

_LEXICAL_FALLBACK_MAX_ROWS = 2_000
_LEXICAL_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_LEXICAL_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "all",
        "an",
        "and",
        "are",
        "at",
        "available",
        "by",
        "compare",
        "darglobal",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "information",
        "is",
        "it",
        "near",
        "of",
        "on",
        "or",
        "project",
        "property",
        "residence",
        "that",
        "the",
        "this",
        "to",
        "wasalt",
        "what",
        "which",
        "with",
    }
)


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

    async def search_lexical(
        self,
        query: str,
        *,
        top_k: int,
        source: SourceName | None = None,
    ) -> list[ChunkSearchRow]:
        """Rank the small verified corpus without an external embedding request."""
        query_terms = _lexical_terms(query)
        if not query_terms:
            return []
        statement = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .order_by(DocumentChunk.id)
            .limit(_LEXICAL_FALLBACK_MAX_ROWS)
        )
        if source is not None:
            statement = statement.where(Document.source == source.value)
        rows = (await self._session.execute(statement)).all()
        ranked: list[tuple[float, int, int, DocumentChunk, Document]] = []
        for chunk, document in rows:
            matched, similarity = _lexical_score(query_terms, f"{document.title} {chunk.content}")
            if matched:
                title_matched = len(query_terms & _lexical_terms(document.title))
                ranked.append((similarity, title_matched, matched, chunk, document))
        ranked.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                -item[2],
                item[4].canonical_url,
                item[3].chunk_index,
            )
        )
        return [
            ChunkSearchRow(
                chunk_content=chunk.content,
                similarity=similarity,
                document_title=document.title,
                source=SourceName(document.source),
                canonical_url=document.canonical_url,
                metadata={**chunk.metadata_, "retrieval_mode": "lexical_fallback"},
            )
            for similarity, _title_matched, _matched, chunk, document in ranked[:top_k]
        ]


def _lexical_terms(text: str) -> frozenset[str]:
    return frozenset(
        normalized
        for token in _LEXICAL_TOKEN_RE.findall(text.casefold())
        if (normalized := _normalize_lexical_token(token)) and normalized not in _LEXICAL_STOP_WORDS
    )


def _normalize_lexical_token(term: str) -> str:
    # "W" is a distinctive property/hospitality brand in the verified corpus.
    if term == "w":
        return term
    if len(term) < 2:
        return ""
    if term.isascii() and len(term) > 4 and term.endswith("s"):
        return term[:-1]
    return term


def _lexical_score(query_terms: frozenset[str], text: str) -> tuple[int, float]:
    matched = len(query_terms & _lexical_terms(text))
    denominator = max(1, min(4, len(query_terms)))
    return matched, min(0.99, matched / denominator)
