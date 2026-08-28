import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.session import AsyncSessionFactory
from app.rag.embeddings import get_embedding_service
from app.rag.evaluation import evaluate_retrieval, load_evaluation_cases
from app.rag.retrieval import VectorRetrievalService
from app.repositories.chunks import DocumentChunkRepository


async def run(dataset: Path) -> dict[str, object]:
    settings = get_settings()
    embeddings = get_embedding_service(
        settings.embedding_model,
        settings.embedding_batch_size,
        provider=settings.embedding_provider,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout_seconds=settings.openrouter_timeout_seconds,
        max_retries=settings.openrouter_max_retries,
    )
    cases = load_evaluation_cases(dataset)
    async with AsyncSessionFactory() as session:
        service = VectorRetrievalService(DocumentChunkRepository(session), embeddings)
        metrics = await evaluate_retrieval(service, cases)
    return metrics.model_dump()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate exact pgvector retrieval")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("backend/data/evaluation/retrieval_queries.json"),
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.dataset)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
