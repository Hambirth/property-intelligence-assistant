from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.rag.context import ContextBuilder
from app.rag.embeddings import get_embedding_service
from app.rag.generation import GroundedRAGService
from app.rag.grounding import EvidenceGate
from app.rag.openrouter import OpenRouterClient
from app.rag.retrieval import VectorRetrievalService
from app.repositories.chunks import DocumentChunkRepository
from app.scraping.models import SourceName


class RAGAnswerService(Protocol):
    async def answer(
        self, question: str, *, source: SourceName | None = None
    ): ...


class LazyRAGService:
    """Defer model loading until validated, rate-allowed input needs an answer."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def answer(self, question: str, *, source: SourceName | None = None):
        service = _build_rag_service(self._session, self._settings)
        return await service.answer(question, source=source)


def get_rag_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RAGAnswerService:
    return LazyRAGService(session, settings)


def _build_rag_service(
    session: AsyncSession, settings: Settings
) -> GroundedRAGService:
    embeddings = get_embedding_service(
        settings.embedding_model, settings.embedding_batch_size
    )
    return GroundedRAGService(
        retrieval=VectorRetrievalService(DocumentChunkRepository(session), embeddings),
        generator=OpenRouterClient(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.openrouter_timeout_seconds,
            max_retries=settings.openrouter_max_retries,
        ),
        evidence_gate=EvidenceGate(settings.rag_similarity_threshold),
        context_builder=ContextBuilder(
            max_chunks=settings.rag_context_max_chunks,
            max_chars=settings.rag_context_max_chars,
        ),
        top_k=settings.rag_top_k,
        max_question_length=settings.max_chat_message_length,
    )
