from dataclasses import dataclass, field

from app.scraping.models import ScrapedDocument


@dataclass(slots=True)
class Deduplicator:
    urls: set[str] = field(default_factory=set)
    canonical_urls: set[str] = field(default_factory=set)
    content_hashes: set[str] = field(default_factory=set)

    def duplicate_reason(self, document: ScrapedDocument) -> str | None:
        if document.url in self.urls:
            return "duplicate_url"
        if document.canonical_url in self.canonical_urls:
            return "duplicate_canonical_url"
        if document.content_hash in self.content_hashes:
            return "duplicate_content"
        self.urls.add(document.url)
        self.canonical_urls.add(document.canonical_url)
        self.content_hashes.add(document.content_hash)
        return None
