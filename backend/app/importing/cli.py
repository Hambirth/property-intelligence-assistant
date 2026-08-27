import argparse
import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.importing.documents import (
    discover_import_files,
    parse_public_document,
    write_deterministic_jsonl,
)
from app.repositories.documents import DocumentRepository
from app.scraping.deduplication import Deduplicator
from app.scraping.models import ScrapeFailure, SourceName, UpsertAction

logger = logging.getLogger(__name__)


class ImportSummary(BaseModel):
    discovered: int = 0
    parsed: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    failed: int = 0
    output: str | None = None

    def record_action(self, action: UpsertAction) -> None:
        setattr(self, action.value, getattr(self, action.value) + 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import legitimately obtained public documents without network access"
    )
    parser.add_argument("--source", choices=("darglobal", "wasalt"), required=True)
    parser.add_argument("--path", type=Path, required=True, help="Local file or directory")
    parser.add_argument("--output", type=Path, help="Deterministic normalized JSONL path")
    parser.add_argument("--dry-run", action="store_true", help="Do not modify PostgreSQL")
    parser.add_argument("--verbose", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> ImportSummary:
    if args.dry_run:
        configure_logging("DEBUG" if args.verbose else "INFO")
    else:
        settings = get_settings()
        configure_logging("DEBUG" if args.verbose else settings.log_level)
    source = SourceName(args.source)
    summary = ImportSummary()
    documents = []
    deduplicator = Deduplicator()

    files = discover_import_files(args.path)
    summary.discovered = len(files)
    for path in files:
        try:
            document = parse_public_document(path, source)
        except (OSError, ValueError, ScrapeFailure):
            summary.rejected += 1
            logger.warning(
                "Manual public document rejected",
                extra={"source": source.value, "url": path.name, "action": "rejected"},
            )
            continue
        if deduplicator.duplicate_reason(document) is not None:
            summary.unchanged += 1
            continue
        documents.append(document)
        summary.parsed += 1

    output = args.output or Path("data/processed") / f"{source.value}.jsonl"
    write_deterministic_jsonl(output, documents)
    summary.output = str(output)
    if args.dry_run or not documents:
        return summary

    # Keep database configuration optional for the documented offline dry-run path.
    from app.db.session import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        for document in documents:
            try:
                async with session.begin():
                    action = await DocumentRepository(session).upsert(document)
                summary.record_action(action)
            except SQLAlchemyError:
                summary.failed += 1
                logger.exception(
                    "Manual public document persistence failed",
                    extra={"source": source.value, "url": document.url, "action": "failed"},
                )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(summary.model_dump(), sort_keys=True))
    return exit_code_for_summary(summary)


def exit_code_for_summary(summary: ImportSummary) -> int:
    if summary.failed or summary.rejected:
        return 1
    if summary.parsed == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
