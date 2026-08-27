import re

from bs4 import BeautifulSoup
from defusedxml import ElementTree

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


class WasaltScraper:
    source = SourceName.WASALT
    base_url = "https://wasalt.sa"
    allowed_hosts = frozenset({"wasalt.sa", "www.wasalt.sa"})
    sitemap_hosts = frozenset({"cdn.wasalt.sa"})

    async def discover_urls(
        self,
        client: SafeHttpClient,
        robots: RobotsPolicy,
        *,
        limit: int,
    ) -> DiscoveryResult:
        result = DiscoveryResult()
        product_sitemaps = [
            url
            for url in robots.sitemaps
            if "product_sitemap_en_" in url and url.startswith("https://cdn.wasalt.sa/sitemap/")
        ]
        if not product_sitemaps:
            result.failures.append(
                PageFailure(
                    self.source,
                    f"{self.base_url}/robots.txt",
                    ErrorCategory.PARSE_ERROR,
                    "No approved English product sitemap was declared",
                )
            )
            return result

        seen: set[str] = set()
        for sitemap_url in product_sitemaps:
            try:
                response = await client.fetch_text(
                    sitemap_url,
                    allowed_hosts=self.sitemap_hosts,
                    accepted_content_types=(
                        "application/xml",
                        "text/xml",
                        "application/gzip",
                        "application/x-gzip",
                    ),
                )
                sitemap_urls = _parse_sitemap(response.text, sitemap_url)
            except ScrapeFailure as exc:
                result.failures.append(PageFailure(self.source, exc.url, exc.category, str(exc)))
                continue

            for discovered_url in sitemap_urls:
                try:
                    candidate = normalize_and_validate_url(discovered_url, self.allowed_hosts)
                except ScrapeFailure:
                    continue
                path = candidate.casefold()
                if "/search" in path or not (
                    "/en/project/" in path or "/en/property/" in path
                ):
                    continue
                if candidate in seen or not robots.can_fetch(candidate):
                    continue
                seen.add(candidate)
                result.urls.append(candidate)
                if len(result.urls) >= limit:
                    return result
        return result

    def parse_page(self, url: str, html: str) -> ScrapedDocument:
        soup = BeautifulSoup(html, "html.parser")
        title = first_text(soup, "main h1", "h1") or meta_content(
            soup, "meta[property='og:title']", "meta[name='twitter:title']"
        )
        if not title and soup.title:
            title = normalize_text(soup.title.get_text(" ", strip=True))
        if not title:
            raise ScrapeFailure(ErrorCategory.PARSE_ERROR, "Listing title was not found", url=url)

        text = extract_meaningful_text(soup)
        validate_content(text, url=url, raw_html=html)
        lines = text.splitlines()
        structured = next((record for record in parse_json_ld(soup) if record.get("name")), {})

        description = (
            first_text(soup, "[data-field='description']", "#about-project p", ".about-project p")
            or _string_value(structured.get("description"))
            or meta_content(soup, "meta[name='description']", "meta[property='og:description']")
        )
        location = first_text(
            soup, "[data-field='location']", "[data-testid='location']", ".property-location"
        ) or value_after_label(lines, ("Location",))
        price = first_text(
            soup, "[data-field='price']", "[data-testid='price']", ".price"
        ) or value_after_label(lines, ("Price", "Starts from"))
        currency = "SAR" if price and re.search(r"\bSAR\b|ر\.س", price, re.I) else None
        metadata = PropertyMetadata(
            property_name=normalize_text(title),
            description=description,
            location=location,
            city=first_text(soup, "[data-field='city']", "[data-testid='city']"),
            country=first_text(soup, "[data-field='country']", "[data-testid='country']"),
            property_type=first_text(
                soup, "[data-field='property-type']", "[data-testid='property-type']"
            )
            or value_after_label(lines, ("Property Type", "Available Property Types")),
            bedrooms=integer_values(
                first_text(soup, "[data-field='bedrooms']", "[data-testid='bedrooms']")
                or value_after_label(lines, ("Bedrooms",))
            ),
            bathrooms=integer_values(
                first_text(soup, "[data-field='bathrooms']", "[data-testid='bathrooms']")
                or value_after_label(lines, ("Bathrooms",))
            ),
            amenities=_comma_or_list_values(soup, "amenities"),
            developer=first_text(soup, "[data-field='developer']", ".developer-name")
            or value_after_label(lines, ("Developer",)),
            price=price,
            currency=currency,
            completion=first_text(soup, "[data-field='completion']")
            or value_after_label(lines, ("Completion", "Handover")),
            language=_language_from_html(soup),
            external_reference=first_text(soup, "[data-field='reference']")
            or value_after_label(lines, ("Ref no.", "Reference")),
        )
        canonical_url = canonical_url_for_page(url, canonical_href(soup), self.allowed_hosts)
        return ScrapedDocument(
            source=self.source,
            url=normalize_and_validate_url(url, self.allowed_hosts),
            canonical_url=canonical_url,
            title=normalize_text(title),
            text=text,
            metadata=metadata,
            content_hash=content_hash(text),
        )


def _parse_sitemap(xml_text: str, url: str) -> list[str]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ScrapeFailure(ErrorCategory.PARSE_ERROR, "Invalid sitemap XML", url=url) from exc
    return [
        normalize_text(element.text)
        for element in root.findall(".//{*}loc")
        if element.text and normalize_text(element.text)
    ]


def _comma_or_list_values(soup: BeautifulSoup, section_name: str) -> list[str]:
    container = soup.select_one(f"[data-field='{section_name}'], .{section_name}")
    if container is None:
        return []
    list_values = [
        normalize_text(item.get_text(" ", strip=True)) for item in container.select("li")
    ]
    if list_values:
        return list(dict.fromkeys(value for value in list_values if value))[:30]
    value = normalize_text(container.get_text(" ", strip=True))
    return [part.strip() for part in value.split(",") if part.strip()][:30]


def _language_from_html(soup: BeautifulSoup) -> str | None:
    if soup.html and isinstance(soup.html.get("lang"), str):
        return str(soup.html.get("lang")).split("-", 1)[0].lower()
    return None


def _string_value(value: object) -> str | None:
    return normalize_text(value) if isinstance(value, str) and value.strip() else None
