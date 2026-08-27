import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.scraping.models import ErrorCategory, ScrapeFailure

_BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "dt", "dd")
_NOISE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "header",
    "form",
    "svg",
    "[aria-hidden='true']",
    ".cookie-banner",
    ".cookie-consent",
    "#cookie-banner",
)
_ANTI_BOT_MARKERS = (
    "cf-chl-",
    "cloudflare ray id",
    "enable javascript and cookies to continue",
    "just a moment...",
    "_incapsula_resource",
    "incap_ses_",
    "access denied | imperva",
)
_ERROR_PAGE_MARKERS = ("502 bad gateway", "503 service unavailable", "internal server error")


def normalize_text(value: str) -> str:
    value = (
        unicodedata.normalize("NFKC", value)
        .replace("\u00a0", " ")
        .replace("\ufffd", "")
    )
    lines = []
    for raw_line in value.splitlines():
        line = re.sub(r"[\t\f\v ]+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def is_access_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in _ANTI_BOT_MARKERS)


def extract_meaningful_text(soup: BeautifulSoup) -> str:
    working = BeautifulSoup(str(soup), "html.parser")
    for selector in _NOISE_SELECTORS:
        for element in working.select(selector):
            element.decompose()

    root = working.find("main") or working.find("article") or working.body or working
    blocks: list[str] = []
    seen_ui_text: set[str] = set()
    for element in root.find_all(_BLOCK_TAGS):
        if not isinstance(element, Tag):
            continue
        text = normalize_text(element.get_text(" ", strip=True))
        if not text:
            continue
        normalized_key = text.casefold()
        if len(text) <= 80 and normalized_key in seen_ui_text:
            continue
        if len(text) <= 80:
            seen_ui_text.add(normalized_key)
        blocks.append(text)

    if not blocks:
        return normalize_text(root.get_text("\n", strip=True))
    return normalize_text("\n".join(blocks))


def validate_content(text: str, *, url: str, raw_html: str = "") -> None:
    combined = f"{raw_html[:20_000]}\n{text}"
    if is_access_blocked(combined):
        raise ScrapeFailure(ErrorCategory.ACCESS_BLOCKED, "Anti-bot interstitial detected", url=url)
    lowered = text.casefold()
    if any(marker in lowered for marker in _ERROR_PAGE_MARKERS):
        raise ScrapeFailure(ErrorCategory.INVALID_CONTENT, "Generic error page detected", url=url)
    if len(text) < 200 or len(re.findall(r"\w+", text, flags=re.UNICODE)) < 30:
        raise ScrapeFailure(
            ErrorCategory.INVALID_CONTENT,
            "Page contains too little meaningful text",
            url=url,
        )


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for script in soup.select("script[type='application/ld+json']"):
        try:
            payload = json.loads(script.get_text(strip=True))
        except (json.JSONDecodeError, TypeError):
            continue
        candidates: Iterable[Any] = payload if isinstance(payload, list) else (payload,)
        for candidate in candidates:
            if isinstance(candidate, dict):
                graph = candidate.get("@graph")
                if isinstance(graph, list):
                    records.extend(item for item in graph if isinstance(item, dict))
                else:
                    records.append(candidate)
    return records


def meta_content(soup: BeautifulSoup, *selectors: str) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if isinstance(element, Tag):
            value = element.get("content")
            if isinstance(value, str) and normalize_text(value):
                return normalize_text(value)
    return None


def first_text(soup: BeautifulSoup, *selectors: str) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if isinstance(element, Tag):
            value = normalize_text(element.get_text(" ", strip=True))
            if value:
                return value
    return None


def canonical_href(soup: BeautifulSoup) -> str | None:
    element = soup.select_one("link[rel='canonical']")
    if not isinstance(element, Tag):
        return None
    href = element.get("href")
    return href if isinstance(href, str) else None


def value_after_label(lines: list[str], labels: Iterable[str]) -> str | None:
    normalized_labels = {label.casefold().rstrip(":") for label in labels}
    for index, line in enumerate(lines[:-1]):
        key = line.casefold().rstrip(":")
        next_key = lines[index + 1].casefold().rstrip(":")
        if key in normalized_labels and next_key not in normalized_labels:
            return lines[index + 1]
    return None


def integer_values(value: str | None) -> list[int]:
    if not value:
        return []
    return list(dict.fromkeys(int(match) for match in re.findall(r"\b\d{1,2}\b", value)))
