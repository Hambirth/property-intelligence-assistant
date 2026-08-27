from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SourceName(StrEnum):
    DAR_GLOBAL = "darglobal"
    WASALT = "wasalt"


class AcquisitionMethod(StrEnum):
    AUTOMATED_SCRAPE = "AUTOMATED_SCRAPE"
    MANUAL_PUBLIC_IMPORT = "MANUAL_PUBLIC_IMPORT"


class ErrorCategory(StrEnum):
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    ACCESS_BLOCKED = "ACCESS_BLOCKED"
    HTTP_ERROR = "HTTP_ERROR"
    TIMEOUT = "TIMEOUT"
    PARSE_ERROR = "PARSE_ERROR"
    INVALID_CONTENT = "INVALID_CONTENT"
    INVALID_URL = "INVALID_URL"
    DATABASE_ERROR = "DATABASE_ERROR"


class UpsertAction(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class PropertyMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_name: str | None = None
    description: str | None = None
    location: str | None = None
    city: str | None = None
    country: str | None = None
    property_type: str | None = None
    bedrooms: list[int] = Field(default_factory=list)
    bathrooms: list[int] = Field(default_factory=list)
    amenities: list[str] = Field(default_factory=list)
    developer: str | None = None
    brand_partnership: str | None = None
    price: str | None = None
    currency: str | None = None
    completion: str | None = None
    nearby_landmarks: list[str] = Field(default_factory=list)
    investment_information: str | None = None
    language: str | None = None
    external_reference: str | None = None
    acquisition_method: AcquisitionMethod = AcquisitionMethod.AUTOMATED_SCRAPE
    source_document_url: str | None = None
    source_format: str | None = None


class ScrapedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceName
    url: str
    canonical_url: str
    title: str
    text: str
    metadata: PropertyMetadata = Field(default_factory=PropertyMetadata)
    content_hash: str
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class FetchResponse:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    text: str
    duration_ms: float


@dataclass(frozen=True, slots=True)
class PageFailure:
    source: SourceName
    url: str
    category: ErrorCategory
    message: str


@dataclass(slots=True)
class DiscoveryResult:
    urls: list[str] = field(default_factory=list)
    failures: list[PageFailure] = field(default_factory=list)


class IngestionSummary(BaseModel):
    discovered: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    failed: int = 0
    blocked: int = 0

    def record_action(self, action: UpsertAction) -> None:
        setattr(self, action.value, getattr(self, action.value) + 1)

    def merge(self, other: "IngestionSummary") -> None:
        for field_name in type(self).model_fields:
            setattr(self, field_name, getattr(self, field_name) + getattr(other, field_name))


class ScrapeFailure(Exception):
    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        url: str,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.url = url
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds

    def as_log_fields(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "error_category": self.category.value,
            "retryable": self.retryable,
        }
