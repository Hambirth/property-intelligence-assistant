import argparse
import asyncio
import json
import time
from collections.abc import Sequence

from app.core.config import get_settings
from app.db.session import AsyncSessionFactory
from app.rag.chunking import ChunkingConfig
from app.rag.embeddings import get_embedding_service
from app.rag.vectorization import VectorizationService
from app.scraping.models import SourceName


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Chunk and locally embed normalized PostgreSQL documents"
    )
    parser.add_argument("--source", choices=("darglobal", "wasalt"))
    return parser


async def run(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    load_started = time.perf_counter()
    embeddings = get_embedding_service(
        settings.embedding_model, settings.embedding_batch_size
    )
    model_load_seconds = round(time.perf_counter() - load_started, 6)
    config = ChunkingConfig(
        target_chars=settings.rag_chunk_target_chars,
        overlap_chars=settings.rag_chunk_overlap_chars,
        min_chars=settings.rag_chunk_min_chars,
    )
    source = SourceName(args.source) if args.source else None
    async with AsyncSessionFactory() as session:
        summary = await VectorizationService(session, embeddings, config).run(source=source)
    return {
        **summary.model_dump(),
        "embedding_model": embeddings.model_name,
        "embedding_dimension": embeddings.dimension,
        "model_load_seconds": model_load_seconds,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
