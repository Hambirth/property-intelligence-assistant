import argparse
import asyncio
import json
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionFactory
from app.scraping.client import SafeHttpClient
from app.scraping.dar_global import DarGlobalScraper
from app.scraping.models import IngestionSummary
from app.scraping.wasalt import WasaltScraper
from app.services.ingestion import IngestionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest approved public property sources")
    parser.add_argument(
        "--source",
        choices=("wasalt", "darglobal", "all"),
        required=True,
        help="Approved source adapter to run",
    )
    parser.add_argument("--limit", type=int, help="Maximum detail URLs per source")
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch and parse without database writes"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG-level structured logs")
    return parser


async def run(args: argparse.Namespace) -> IngestionSummary:
    settings = get_settings()
    configure_logging("DEBUG" if args.verbose else settings.log_level)
    limit = args.limit if args.limit is not None else settings.scraper_default_limit
    if limit < 1 or limit > 1000:
        raise ValueError("--limit must be between 1 and 1000")

    scrapers = []
    if args.source in {"darglobal", "all"}:
        scrapers.append(DarGlobalScraper())
    if args.source in {"wasalt", "all"}:
        scrapers.append(WasaltScraper())

    total = IngestionSummary()
    async with SafeHttpClient(settings) as client:
        if args.dry_run:
            service = IngestionService(
                client,
                session=None,
                user_agent=settings.scraper_user_agent,
                dry_run=True,
            )
            for scraper in scrapers:
                total.merge(await service.run_source(scraper, limit=limit))
        else:
            async with AsyncSessionFactory() as session:
                service = IngestionService(
                    client,
                    session=session,
                    user_agent=settings.scraper_user_agent,
                    dry_run=False,
                )
                for scraper in scrapers:
                    total.merge(await service.run_source(scraper, limit=limit))
    return total


def exit_code_for_summary(summary: IngestionSummary) -> int:
    if summary.failed:
        return 1
    if summary.fetched == 0 and summary.blocked:
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(summary.model_dump(), sort_keys=True))
    return exit_code_for_summary(summary)


if __name__ == "__main__":
    raise SystemExit(main())
