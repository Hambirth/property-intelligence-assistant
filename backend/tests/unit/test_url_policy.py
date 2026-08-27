import pytest

from app.scraping.models import ErrorCategory, ScrapeFailure
from app.scraping.url_policy import canonical_url_for_page, normalize_and_validate_url

ALLOWED = {"wasalt.sa"}


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "https://127.0.0.1/",
        "file:///etc/passwd",
        "ftp://wasalt.sa/file",
        "https://user:password@wasalt.sa/project",
        "https://unexpected.example/project",
        "https://wasalt.sa:8443/project",
        "not a url",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(ScrapeFailure) as exc_info:
        normalize_and_validate_url(url, ALLOWED)

    assert exc_info.value.category is ErrorCategory.INVALID_URL


def test_canonicalization_removes_only_tracking_noise() -> None:
    normalized = normalize_and_validate_url(
        "https://WASALT.SA/en/project/demo/?unit=2&utm_source=email&gclid=abc#gallery",
        ALLOWED,
    )

    assert normalized == "https://wasalt.sa/en/project/demo?unit=2"


def test_invalid_or_cross_domain_canonical_falls_back_to_page() -> None:
    page = "https://wasalt.sa/en/project/demo"

    assert canonical_url_for_page(page, "https://evil.example/copy", ALLOWED) == page


def test_same_domain_relative_canonical_is_accepted() -> None:
    canonical = canonical_url_for_page(
        "https://wasalt.sa/en/project/demo?utm_source=x",
        "/en/project/demo/",
        ALLOWED,
    )

    assert canonical == "https://wasalt.sa/en/project/demo"
