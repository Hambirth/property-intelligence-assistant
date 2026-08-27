import html
import json
import os
import re
from collections.abc import Sequence

import httpx
import pytest

from app.api.dependencies import get_rag_service
from app.core.config import get_settings
from app.db.session import AsyncSessionFactory, engine
from app.main import create_app
from app.rag.context import ContextBuilder
from app.rag.embeddings import get_embedding_service
from app.rag.generation import GroundedRAGService
from app.rag.grounding import EvidenceGate
from app.rag.openrouter import ChatMessage, LLMCompletion
from app.rag.retrieval import VectorRetrievalService
from app.repositories.chunks import DocumentChunkRepository

pytestmark = pytest.mark.integration

ASTERA_URL = "https://cdn.darglobal.co.uk/DG_AM_The_Astera_Brochure_EN_1_dce26e7ab3.pdf"
WASALT_URL = "https://wasalt.sa/en/property/sale/apartment-103-sqm-with-3-bedrooms-5786979"
NEPTUNE_URL = "https://cdn.darglobal.co.uk/D_Gx_Mouawad_Neptune_Brochure_EN_d2730ddcf4.pdf"
VILLA_URL = "https://wasalt.sa/en/property/sale/villa-29162-sqm-facing-north-on-12m-width-street-5786931"


class CorpusAwareGenerator:
    async def generate(self, messages: Sequence[ChatMessage]) -> LLMCompletion:
        prompt = messages[-1].content
        available = {
            html.unescape(url): source_id
            for source_id, url in re.findall(r'id="(S\d+)"[^>]+url="([^"]+)"', prompt)
        }
        question = prompt.split("<user_question>\n", 1)[1].split("\n</user_question>", 1)[0]
        if "Aston Martin" in question:
            answer = "The Astera has interiors by Aston Martin."
            urls = [ASTERA_URL]
        elif "103 SQM" in question:
            answer = "The 103 SQM three-bedroom Wasalt apartment is listed at 620000 SAR."
            urls = [WASALT_URL]
        else:
            answer = (
                "Neptune is the DarGlobal Mouawad residence; the Wasalt villa is 291 SQM."
            )
            urls = [NEPTUNE_URL, VILLA_URL]
        citations = [available[url] for url in urls if url in available]
        return LLMCompletion(
            content=json.dumps({"answer": answer, "citations": citations}),
            model="mocked/corpus-api",
        )


class RealCorpusAPIService:
    async def answer(self, question, *, source=None):
        settings = get_settings()
        embeddings = get_embedding_service(
            settings.embedding_model, settings.embedding_batch_size
        )
        async with AsyncSessionFactory() as session:
            service = GroundedRAGService(
                retrieval=VectorRetrievalService(
                    DocumentChunkRepository(session), embeddings
                ),
                generator=CorpusAwareGenerator(),
                evidence_gate=EvidenceGate(settings.rag_similarity_threshold),
                context_builder=ContextBuilder(
                    max_chunks=settings.rag_context_max_chunks,
                    max_chars=settings.rag_context_max_chars,
                ),
                top_k=settings.rag_top_k,
                max_question_length=settings.max_chat_message_length,
            )
            return await service.answer(question, source=source)


async def test_public_chat_api_against_real_pgvector_corpus() -> None:
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("set RUN_POSTGRES_INTEGRATION=1 to run real PostgreSQL tests")
    app = create_app()
    app.dependency_overrides[get_rag_service] = RealCorpusAPIService
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            darglobal = await client.post(
                "/api/chat",
                json={"message": "Which DarGlobal residence has interiors by Aston Martin?"},
            )
            wasalt = await client.post(
                "/api/chat",
                json={
                    "message": "What is the price of the 103 SQM three-bedroom apartment?",
                    "source": "wasalt",
                },
            )
            unsupported = await client.post(
                "/api/chat",
                json={"message": "Which property has a private helipad in London?"},
            )
            comparison = await client.post(
                "/api/chat",
                json={
                    "message": (
                        "Compare the DarGlobal Mouawad residence with the 291 SQM "
                        "Wasalt villa."
                    )
                },
            )
    finally:
        await engine.dispose()

    assert darglobal.status_code == wasalt.status_code == 200
    assert darglobal.json()["sources"][0]["url"] == ASTERA_URL
    assert wasalt.json()["sources"][0]["url"] == WASALT_URL
    assert unsupported.status_code == 200
    assert unsupported.json()["refused"] is True
    assert unsupported.json()["sources"] == []
    assert comparison.status_code == 200
    assert {source["url"] for source in comparison.json()["sources"]} == {
        NEPTUNE_URL,
        VILLA_URL,
    }
