import ipaddress
from collections.abc import Collection
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from app.scraping.models import ErrorCategory, ScrapeFailure

TRACKING_QUERY_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
}


def normalize_and_validate_url(url: str, allowed_hosts: Collection[str]) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ScrapeFailure(ErrorCategory.INVALID_URL, "Malformed URL", url=url) from exc

    if parsed.scheme.lower() != "https":
        raise ScrapeFailure(ErrorCategory.INVALID_URL, "Only HTTPS URLs are allowed", url=url)
    if not parsed.hostname:
        raise ScrapeFailure(ErrorCategory.INVALID_URL, "URL hostname is required", url=url)
    if parsed.username is not None or parsed.password is not None:
        raise ScrapeFailure(
            ErrorCategory.INVALID_URL,
            "Credentials embedded in URLs are forbidden",
            url=url,
        )
    if port not in {None, 443}:
        raise ScrapeFailure(ErrorCategory.INVALID_URL, "Unexpected URL port", url=url)

    hostname = parsed.hostname.rstrip(".").lower()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ScrapeFailure(ErrorCategory.INVALID_URL, "IP-literal URLs are forbidden", url=url)

    normalized_allowed_hosts = {host.rstrip(".").lower() for host in allowed_hosts}
    if hostname not in normalized_allowed_hosts:
        raise ScrapeFailure(ErrorCategory.INVALID_URL, "Hostname is not allowlisted", url=url)

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_PARAMETERS
        ),
        doseq=True,
    )
    return urlunsplit(("https", hostname, path, query, ""))


def canonical_url_for_page(
    page_url: str,
    canonical_href: str | None,
    allowed_hosts: Collection[str],
) -> str:
    normalized_page_url = normalize_and_validate_url(page_url, allowed_hosts)
    if not canonical_href:
        return normalized_page_url

    try:
        candidate = normalize_and_validate_url(urljoin(page_url, canonical_href), allowed_hosts)
    except ScrapeFailure:
        return normalized_page_url

    page_host = urlsplit(normalized_page_url).hostname
    if urlsplit(candidate).hostname != page_host:
        return normalized_page_url
    return candidate
