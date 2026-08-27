import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.session import AsyncSessionFactory
from app.rag.context import ContextBuilder
from app.rag.embeddings import get_embedding_service
from app.rag.generation import GroundedRAGService
from app.rag.generation_evaluation import (
    ScriptedEvaluationGenerator,
    evaluate_generation,
    load_generation_cases,
)
from app.rag.grounding import EvidenceGate
from app.rag.openrouter import OpenRouterClient
from app.rag.retrieval import VectorRetrievalService
from app.repositories.chunks import DocumentChunkRepository


async def run(dataset: Path, *, live: bool, live_limit: int) -> dict[str, object]:
    settings = get_settings()
    cases = load_generation_cases(dataset)
    if live:
        if settings.openrouter_api_key is None:
            raise RuntimeError("OPENROUTER_API_KEY is not configured; live evaluation was not run")
        preferred = [
            "dg-astera",
            "wa-5786979",
            "compare-cross-source",
            "trap-helipad",
            "injection-user",
            "wa-5786970",
            "dg-neptune",
            "trap-roi",
            "compare-wasalt-apartments",
            "dg-w-residences",
        ]
        by_id = {case.case_id: case for case in cases}
        cases = [by_id[case_id] for case_id in preferred[:live_limit]]
    embeddings = get_embedding_service(settings.embedding_model, settings.embedding_batch_size)
    async with AsyncSessionFactory() as session:
        retrieval = VectorRetrievalService(DocumentChunkRepository(session), embeddings)

        def factory(case):
            generator = (
                OpenRouterClient(
                    api_key=settings.openrouter_api_key,
                    model=settings.openrouter_model,
                    base_url=settings.openrouter_base_url,
                    timeout_seconds=settings.openrouter_timeout_seconds,
                    max_retries=settings.openrouter_max_retries,
                )
                if live
                else ScriptedEvaluationGenerator(case)
            )
            return GroundedRAGService(
                retrieval=retrieval,
                generator=generator,
                evidence_gate=EvidenceGate(settings.rag_similarity_threshold),
                context_builder=ContextBuilder(
                    max_chunks=settings.rag_context_max_chunks,
                    max_chars=settings.rag_context_max_chars,
                ),
                top_k=settings.rag_top_k,
                max_question_length=settings.max_chat_message_length,
            )

        metrics = await evaluate_generation(
            cases, factory, mode="live_openrouter" if live else "mocked_provider"
        )
    return metrics.model_dump()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the internal grounded generation layer")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("backend/data/evaluation/generation_queries.json"),
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-limit", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.live_limit <= 10:
        parser.error("--live-limit must be between 1 and 10")
    try:
        result = asyncio.run(run(args.dataset, live=args.live, live_limit=args.live_limit))
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
