from pathlib import Path

from app.scraping.deduplication import Deduplicator
from app.scraping.wasalt import WasaltScraper

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_deduplicator_detects_url_canonical_and_content_duplicates() -> None:
    document = WasaltScraper().parse_page(
        "https://wasalt.sa/en/project/Jeddah/padel-living-100567",
        (FIXTURES / "wasalt_project.html").read_text(),
    )
    deduplicator = Deduplicator()

    assert deduplicator.duplicate_reason(document) is None
    assert deduplicator.duplicate_reason(document.model_copy()) == "duplicate_url"

    same_canonical = document.model_copy(
        update={"url": "https://wasalt.sa/en/project/Jeddah/padel-living-alias"}
    )
    assert deduplicator.duplicate_reason(same_canonical) == "duplicate_canonical_url"

    same_content = document.model_copy(
        update={
            "url": "https://wasalt.sa/en/project/Jeddah/padel-living-copy",
            "canonical_url": "https://wasalt.sa/en/project/Jeddah/padel-living-copy",
        }
    )
    assert deduplicator.duplicate_reason(same_content) == "duplicate_content"
