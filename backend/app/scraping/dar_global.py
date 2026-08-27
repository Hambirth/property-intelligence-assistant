import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.scraping.client import SafeHttpClient
from app.scraping.models import (
    DiscoveryResult,
    ErrorCategory,
    PageFailure,
    PropertyMetadata,
    ScrapedDocument,
    ScrapeFailure,
    SourceName,
)
from app.scraping.normalization import (
    canonical_href,
    content_hash,
    extract_meaningful_text,
    first_text,
    integer_values,
    meta_content,
    normalize_text,
    parse_json_ld,
    validate_content,
    value_after_label,
)
from app.scraping.robots import RobotsPolicy
from app.scraping.url_policy import canonical_url_for_page, normalize_and_validate_url


class DarGlobalScraper:
    source = SourceName.DAR_GLOBAL
    base_url = "https://darglobal.co.uk"
    allowed_hosts = frozenset({"darglobal.co.uk", "www.darglobal.co.uk"})
    projects_url = "https://darglobal.co.uk/projects"

    async def discover_urls(
        self,
        client: SafeHttpClient,
        robots: RobotsPolicy,
        *,
        limit: int,
    ) -> DiscoveryResult:
        result = DiscoveryResult()
        if not robots.can_fetch(self.projects_url):
            result.failures.append(
                PageFailure(
                    self.source,
                    self.projects_url,
                    ErrorCategory.ROBOTS_DISALLOWED,
                    "DarGlobal robots policy is unavailable or does not permit the project index",
                )
            )
            return result

        try:
            response = await client.fetch_text(
                self.projects_url,
                allowed_hosts=self.allowed_hosts,
                robots_policy=robots,
            )
        except ScrapeFailure as exc:
            result.failures.append(PageFailure(self.source, exc.url, exc.category, str(exc)))
            return result

        soup = BeautifulSoup(response.text, "html.parser")
        seen: set[str] = set()
        for anchor in soup.select("main a[href], a[href]"):
            if not isinstance(anchor, Tag):
                continue
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            try:
                candidate = normalize_and_validate_url(
                    urljoin(response.final_url, href), self.allowed_hosts
                )
            except ScrapeFailure:
                continue
            if (
                candidate == self.projects_url
                or candidate in seen
                or not robots.can_fetch(candidate)
            ):
                continue
            if _looks_like_non_project_path(candidate):
                continue
            seen.add(candidate)
            result.urls.append(candidate)
            if len(result.urls) >= limit:
                break
        return result

    def parse_page(self, url: str, html: str) -> ScrapedDocument:
        soup = BeautifulSoup(html, "html.parser")
        title = first_text(soup, "main h1", "h1") or meta_content(
            soup, "meta[property='og:title']", "meta[name='twitter:title']"
        )
        if not title and soup.title:
            title = normalize_text(soup.title.get_text(" ", strip=True))
        if not title:
            raise ScrapeFailure(ErrorCategory.PARSE_ERROR, "Project title was not found", url=url)

        text = extract_meaningful_text(soup)
        validate_content(text, url=url, raw_html=html)
        lines = text.splitlines()
        json_ld = parse_json_ld(soup)
        structured = next((record for record in json_ld if record.get("name")), {})

        description = (
            first_text(soup, "[data-field='description']", ".project-description", "main p")
            or _string_value(structured.get("description"))
            or meta_content(soup, "meta[name='description']", "meta[property='og:description']")
        )
        location = first_text(
            soup, "[data-field='location']", ".project-location"
        ) or value_after_label(lines, ("Location",))
        property_type = value_after_label(lines, ("Property Type", "Unit type"))
        completion = value_after_label(lines, ("Expected Completion Date", "Completion Date"))
        bedrooms = integer_values(value_after_label(lines, ("Bedrooms", "Unit type")))
        amenities = _section_items(soup, "amenities")
        landmarks = _section_items(soup, "key features")
        brand_match = re.search(
            r"(?:interiors by|design(?:ed)? (?:inspired )?by)\s+([^|,]+)",
            title,
            flags=re.IGNORECASE,
        )

        canonical_url = canonical_url_for_page(url, canonical_href(soup), self.allowed_hosts)
        normalized_title = normalize_text(title)
        metadata = PropertyMetadata(
            property_name=normalized_title,
            description=description,
            location=location,
            property_type=property_type,
            bedrooms=bedrooms,
            amenities=amenities,
            brand_partnership=normalize_text(brand_match.group(1)) if brand_match else None,
            completion=completion,
            nearby_landmarks=landmarks,
            language="en",
        )
        return ScrapedDocument(
            source=self.source,
            url=normalize_and_validate_url(url, self.allowed_hosts),
            canonical_url=canonical_url,
            title=normalized_title,
            text=text,
            metadata=metadata,
            content_hash=content_hash(text),
        )


def _looks_like_non_project_path(url: str) -> bool:
    excluded = (
        "/about",
        "/contact",
        "/get-in-touch",
        "/privacy",
        "/terms",
        "/investor",
        "/news",
        "/careers",
    )
    return any(path in url.lower() for path in excluded)


def _section_items(soup: BeautifulSoup, heading_text: str) -> list[str]:
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        if heading_text not in heading.get_text(" ", strip=True).casefold():
            continue
        values: list[str] = []
        for sibling in heading.find_next_siblings():
            if isinstance(sibling, Tag) and re.fullmatch(r"h[1-6]", sibling.name or ""):
                break
            if not isinstance(sibling, Tag):
                continue
            candidates = sibling.find_all("li") or [sibling]
            for candidate in candidates:
                value = normalize_text(candidate.get_text(" ", strip=True))
                if value and value not in values:
                    values.append(value)
        return values[:30]
    return []


def _string_value(value: object) -> str | None:
    return normalize_text(value) if isinstance(value, str) and value.strip() else None
