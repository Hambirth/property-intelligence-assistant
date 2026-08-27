from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.scraping.models import SourceName

_CITATION_HOSTS = {
    SourceName.DAR_GLOBAL: frozenset(
        {"darglobal.co.uk", "www.darglobal.co.uk", "cdn.darglobal.co.uk"}
    ),
    SourceName.WASALT: frozenset({"wasalt.sa", "www.wasalt.sa", "cdn.wasalt.sa"}),
}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=10000)
    source: SourceName | None = None

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("message must not be empty")
        return normalized


class ChatSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=2, max_length=16, pattern=r"^S[1-9][0-9]*$")
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=8, max_length=2048)
    source: Literal["DarGlobal", "Wasalt"]

    @model_validator(mode="after")
    def validate_backend_owned_url(self) -> "ChatSource":
        parsed = urlsplit(self.url)
        expected_source = (
            SourceName.DAR_GLOBAL if self.source == "DarGlobal" else SourceName.WASALT
        )
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.rstrip(".").casefold() not in _CITATION_HOSTS[expected_source]
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Citation URL is not an approved source URL")
        return self


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str = Field(min_length=1, max_length=20000)
    refused: bool
    sources: list[ChatSource] = Field(max_length=12)
    request_id: str = Field(min_length=8, max_length=128)


class APIErrorDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    request_id: str


class APIErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: APIErrorDetail
