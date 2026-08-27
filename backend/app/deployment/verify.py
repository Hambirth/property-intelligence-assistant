import argparse
import asyncio
import json
from collections.abc import Sequence

from sqlalchemy import text

from app.db.session import engine


async def verify_database(
    *, expected_darglobal: int, expected_wasalt: int, expected_chunks: int
) -> dict[str, object]:
    async with engine.connect() as connection:
        source_rows = (
            await connection.execute(
                text("SELECT source, count(*) FROM documents GROUP BY source ORDER BY source")
            )
        ).all()
        source_counts = {source: count for source, count in source_rows}
        documents = sum(source_counts.values())
        chunks = int(await connection.scalar(text("SELECT count(*) FROM document_chunks")) or 0)
        vector_extension = await connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        manual_documents = int(
            await connection.scalar(
                text(
                    "SELECT count(*) FROM documents "
                    "WHERE metadata->>'acquisition_method' = 'MANUAL_PUBLIC_IMPORT'"
                )
            )
            or 0
        )
        dimensions = (
            await connection.execute(
                text("SELECT DISTINCT vector_dims(embedding) FROM document_chunks")
            )
        ).scalars().all()
        fingerprint_count = int(
            await connection.scalar(
                text(
                    "SELECT count(DISTINCT metadata->>'pipeline_fingerprint') "
                    "FROM document_chunks"
                )
            )
            or 0
        )

    expected_documents = expected_darglobal + expected_wasalt
    checks = {
        "alembic_head": revision == "20260825_0003",
        "darglobal_documents": source_counts.get("darglobal", 0) == expected_darglobal,
        "document_total": documents == expected_documents,
        "document_chunks": chunks == expected_chunks,
        "manual_public_provenance": manual_documents == expected_documents,
        "pgvector_extension": vector_extension is not None,
        "single_pipeline_fingerprint": fingerprint_count == 1,
        "vector_dimension": dimensions == [384],
        "wasalt_documents": source_counts.get("wasalt", 0) == expected_wasalt,
    }
    return {
        "checks": checks,
        "counts": {
            "darglobal": source_counts.get("darglobal", 0),
            "wasalt": source_counts.get("wasalt", 0),
            "documents": documents,
            "chunks": chunks,
        },
        "migration_revision": revision,
        "pgvector_version": vector_extension,
        "ready": all(checks.values()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify a bootstrapped production corpus")
    parser.add_argument("--expected-darglobal", type=int, default=10)
    parser.add_argument("--expected-wasalt", type=int, default=10)
    parser.add_argument("--expected-chunks", type=int, default=212)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if min(args.expected_darglobal, args.expected_wasalt, args.expected_chunks) < 1:
        raise SystemExit("Expected counts must be positive")
    result = asyncio.run(
        verify_database(
            expected_darglobal=args.expected_darglobal,
            expected_wasalt=args.expected_wasalt,
            expected_chunks=args.expected_chunks,
        )
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
