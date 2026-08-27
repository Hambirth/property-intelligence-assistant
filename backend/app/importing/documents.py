import json
import math
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader

from app.scraping.models import (
    AcquisitionMethod,
    ErrorCategory,
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
    normalize_text,
    validate_content,
)
from app.scraping.url_policy import normalize_and_validate_url

MAX_IMPORT_BYTES = 50_000_000
MAX_PDF_PAGES = 300
MAX_MANIFEST_BYTES = 65_536
MAX_EXTRACTED_TEXT_CHARS = 10_000_000
SUPPORTED_SUFFIXES = frozenset({".html", ".htm", ".txt", ".json", ".pdf"})
SOURCE_HOSTS = {
    SourceName.DAR_GLOBAL: frozenset(
        {"darglobal.co.uk", "www.darglobal.co.uk", "cdn.darglobal.co.uk"}
    ),
    SourceName.WASALT: frozenset({"wasalt.sa", "www.wasalt.sa", "cdn.wasalt.sa"}),
}


class ImportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceName
    source_url: str
    canonical_url: str | None = None
    title: str | None = None
    metadata: PropertyMetadata = Field(default_factory=PropertyMetadata)


class JsonImportDocument(ImportManifest):
    text: str


def discover_import_files(path: Path) -> list[Path]:
    if not path.exists():
        raise ValueError(f"Import path does not exist: {path}")
    candidates = [path] if path.is_file() else list(path.rglob("*"))
    return sorted(
        candidate
        for candidate in candidates
        if candidate.is_file()
        and not candidate.is_symlink()
        and not _inside_browser_companion_directory(candidate, path)
        and candidate.suffix.casefold() in SUPPORTED_SUFFIXES
        and not candidate.name.casefold().endswith(".metadata.json")
    )


def parse_public_document(path: Path, expected_source: SourceName) -> ScrapedDocument:
    _validate_local_file(path)
    suffix = path.suffix.casefold()
    if suffix == ".json":
        return _parse_json_document(path, expected_source)

    manifest = _load_manifest(path, expected_source)
    metadata = manifest.metadata
    if suffix in {".html", ".htm"}:
        if expected_source is SourceName.WASALT:
            text, discovered_title, discovered_canonical, discovered_metadata = (
                _extract_wasalt_html(path)
            )
            metadata = _merge_metadata(discovered_metadata, manifest.metadata)
        else:
            text, discovered_title, discovered_canonical = _extract_html(path)
        _validate_discovered_canonical(
            path,
            expected_source,
            discovered_canonical,
            manifest.canonical_url or manifest.source_url,
        )
        title = manifest.title or discovered_title
        canonical = manifest.canonical_url or discovered_canonical or manifest.source_url
    elif suffix == ".txt":
        text = normalize_text(path.read_text(encoding="utf-8"))
        title = manifest.title
        canonical = manifest.canonical_url or manifest.source_url
    elif suffix == ".pdf":
        text, pdf_title = _extract_pdf(path)
        title = manifest.title or pdf_title
        canonical = manifest.canonical_url or manifest.source_url
    else:  # pragma: no cover - guarded by discovery and validation
        raise ValueError(f"Unsupported document type: {suffix}")

    if not title or not normalize_text(title):
        raise _failure(path, "A trusted title is required")
    return _build_document(
        source=expected_source,
        source_url=manifest.source_url,
        canonical_url=canonical,
        title=title,
        text=text,
        metadata=metadata,
        source_format=suffix.removeprefix("."),
    )


def write_deterministic_jsonl(path: Path, documents: Iterable[ScrapedDocument]) -> None:
    records = []
    for document in sorted(documents, key=lambda item: (item.source.value, item.canonical_url)):
        payload = document.model_dump(mode="json", exclude={"scraped_at"})
        records.append(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")


def _validate_local_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Import target must be a regular non-symlink file: {path}")
    if path.suffix.casefold() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported document type: {path.suffix}")
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise _failure(path, "Import file exceeds the 50 MB limit")


def _load_manifest(path: Path, expected_source: SourceName) -> ImportManifest:
    manifest_path = path.with_name(f"{path.name}.metadata.json")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise _failure(path, f"Required sidecar is missing: {manifest_path.name}")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise _failure(path, "Import sidecar exceeds the 64 KB limit")
    try:
        manifest = ImportManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise _failure(path, "Import sidecar is invalid") from exc
    _validate_manifest_source(manifest.source, expected_source, path)
    return manifest


def _parse_json_document(path: Path, expected_source: SourceName) -> ScrapedDocument:
    try:
        record = JsonImportDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise _failure(path, "JSON import document is invalid") from exc
    _validate_manifest_source(record.source, expected_source, path)
    return _build_document(
        source=expected_source,
        source_url=record.source_url,
        canonical_url=record.canonical_url or record.source_url,
        title=record.title or "",
        text=record.text,
        metadata=record.metadata,
        source_format="json",
    )


def _extract_html(path: Path) -> tuple[str, str | None, str | None]:
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    title = first_text(soup, "main h1", "article h1", "h1")
    if not title and soup.title:
        title = normalize_text(soup.title.get_text(" ", strip=True))
    return extract_meaningful_text(soup), title, canonical_href(soup)


def _extract_wasalt_html(
    path: Path,
) -> tuple[str, str | None, str | None, PropertyMetadata]:
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if script is None:
        raise _failure(path, "Wasalt page data was not found")
    try:
        payload = json.loads(script.get_text())
        details = payload["props"]["pageProps"]["propertyDetailsV3"]
        info = details["propertyInfo"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise _failure(path, "Wasalt page data is malformed or incomplete") from exc
    if not isinstance(details, dict) or not isinstance(info, dict):
        raise _failure(path, "Wasalt property record is malformed")

    title = _optional_text(info.get("title"))
    description_html = _optional_text(info.get("description"))
    description = (
        normalize_text(BeautifulSoup(description_html, "html.parser").get_text("\n", strip=True))
        if description_html
        else None
    )
    attributes = details.get("attributes")
    attribute_records = attributes if isinstance(attributes, list) else []
    bedrooms = _attribute_integers(attribute_records, "noOfBedrooms")
    bathrooms = _attribute_integers(attribute_records, "noOfBathrooms")
    price = _optional_text(info.get("salePrice")) or _optional_text(info.get("expectedRent"))
    currency = _optional_text(info.get("currencyType"))
    location = _optional_text(info.get("address"))
    property_type = _optional_text(info.get("propertySubType"))
    external_reference = _optional_text(details.get("id"))

    lines = [title, f"Property type: {property_type}" if property_type else None]
    property_for = _optional_text(info.get("propertyFor"))
    if property_for:
        lines.append(f"Transaction: {property_for}")
    lines.extend(
        (
            f"Location: {location}" if location else None,
            f"City: {info.get('city')}" if _optional_text(info.get("city")) else None,
            f"Country: {info.get('country')}" if _optional_text(info.get("country")) else None,
            f"Price: {price} {currency or ''}".rstrip() if price else None,
        )
    )
    for attribute in attribute_records:
        if not isinstance(attribute, dict):
            continue
        name = _optional_text(attribute.get("name"))
        value = _optional_text(attribute.get("value"))
        unit = _optional_text(attribute.get("unit"))
        if name and value:
            lines.append(f"{name}: {value} {unit or ''}".rstrip())
    if description:
        lines.extend(("Property description:", description))
    if external_reference:
        lines.append(f"Wasalt property reference: {external_reference}")
    text = normalize_text("\n".join(line for line in lines if line))

    language = None
    if soup.html and isinstance(soup.html.get("lang"), str):
        language = str(soup.html.get("lang")).split("-", 1)[0].lower()
    metadata = PropertyMetadata(
        property_name=title,
        description=description,
        location=location,
        city=_optional_text(info.get("city")),
        country=_optional_text(info.get("country")),
        property_type=property_type,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        price=price,
        currency=currency,
        language=language,
        external_reference=external_reference,
    )
    return text, title, canonical_href(soup), metadata


def _optional_text(value: object) -> str | None:
    if isinstance(value, (str, int, float)):
        normalized = normalize_text(str(value))
        return normalized or None
    return None


def _attribute_integers(attributes: list[object], key: str) -> list[int]:
    for attribute in attributes:
        if isinstance(attribute, dict) and attribute.get("key") == key:
            value = _optional_text(attribute.get("value"))
            if value and value.isdigit():
                return [int(value)]
    return []


def _merge_metadata(
    discovered: PropertyMetadata, manifest: PropertyMetadata
) -> PropertyMetadata:
    overrides = {
        field_name: getattr(manifest, field_name)
        for field_name in manifest.model_fields_set
    }
    return discovered.model_copy(update=overrides)


def _validate_discovered_canonical(
    path: Path,
    source: SourceName,
    discovered: str | None,
    expected: str,
) -> None:
    if discovered is None:
        if source is SourceName.WASALT:
            raise _failure(path, "Saved HTML page has no canonical URL")
        return
    allowed_hosts = SOURCE_HOSTS[source]
    normalized_discovered = normalize_and_validate_url(discovered, allowed_hosts)
    normalized_expected = normalize_and_validate_url(expected, allowed_hosts)
    if normalized_discovered != normalized_expected:
        raise _failure(path, "Saved HTML canonical URL does not match its provenance sidecar")


def _inside_browser_companion_directory(candidate: Path, root: Path) -> bool:
    if root.is_file():
        return False
    relative_parts = candidate.relative_to(root).parts[:-1]
    return any(part.casefold().endswith("_files") for part in relative_parts)


def _extract_pdf(path: Path) -> tuple[str, str | None]:
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise _failure(path, "Encrypted PDFs are not accepted")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise _failure(path, "PDF exceeds the 300-page limit")
        pages = []
        extracted_chars = 0
        for page in reader.pages:
            page_text = normalize_text(page.extract_text() or "")
            extracted_chars += len(page_text)
            if extracted_chars > MAX_EXTRACTED_TEXT_CHARS:
                raise _failure(path, "Extracted PDF text exceeds the 10 million character limit")
            pages.append(page_text)
    except ScrapeFailure:
        raise
    except Exception as exc:
        raise _failure(path, "PDF text extraction failed") from exc
    title_value: Any = reader.metadata.title if reader.metadata else None
    title = normalize_text(title_value) if isinstance(title_value, str) else None
    return _remove_repeated_pdf_lines(pages), title


def _remove_repeated_pdf_lines(pages: list[str]) -> str:
    threshold = max(3, math.ceil(len(pages) * 0.2))
    line_counts: Counter[str] = Counter()
    for page in pages:
        line_counts.update(set(page.splitlines()))
    repeated_noise = {
        line
        for line, count in line_counts.items()
        if count >= threshold and len(line) >= 30
    }
    cleaned_pages = [
        normalize_text("\n".join(line for line in page.splitlines() if line not in repeated_noise))
        for page in pages
    ]
    return normalize_text("\n\n".join(page for page in cleaned_pages if page))


def _build_document(
    *,
    source: SourceName,
    source_url: str,
    canonical_url: str,
    title: str,
    text: str,
    metadata: PropertyMetadata,
    source_format: str,
) -> ScrapedDocument:
    allowed_hosts = SOURCE_HOSTS[source]
    normalized_source_url = normalize_and_validate_url(source_url, allowed_hosts)
    normalized_canonical = normalize_and_validate_url(canonical_url, allowed_hosts)
    normalized_text = normalize_text(text)
    validate_content(normalized_text, url=normalized_source_url, raw_html=text[:20_000])
    provenance = metadata.model_copy(
        update={
            "acquisition_method": AcquisitionMethod.MANUAL_PUBLIC_IMPORT,
            "source_document_url": normalized_source_url,
            "source_format": source_format,
        }
    )
    return ScrapedDocument(
        source=source,
        url=normalized_source_url,
        canonical_url=normalized_canonical,
        title=normalize_text(title),
        text=normalized_text,
        metadata=provenance,
        content_hash=content_hash(normalized_text),
    )


def _validate_manifest_source(
    actual_source: SourceName, expected_source: SourceName, path: Path
) -> None:
    if actual_source is not expected_source:
        raise _failure(path, "Manifest source does not match --source")


def _failure(path: Path, message: str) -> ScrapeFailure:
    return ScrapeFailure(ErrorCategory.INVALID_CONTENT, message, url=path.name)
