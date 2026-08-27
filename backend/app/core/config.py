from functools import lru_cache
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Property Intelligence API"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str
    frontend_url: str = "http://localhost:3000"

    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    openrouter_max_retries: int = Field(default=2, ge=0, le=5)
    embedding_model: Literal["BAAI/bge-small-en-v1.5"] = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = Field(default=32, ge=1, le=128)
    rag_chunk_target_chars: int = Field(default=900, ge=300, le=4000)
    rag_chunk_overlap_chars: int = Field(default=120, ge=0, le=1000)
    rag_chunk_min_chars: int = Field(default=180, ge=50, le=1000)

    rag_top_k: int = Field(default=6, ge=1, le=20)
    rag_similarity_threshold: float = Field(default=0.61, ge=0.0, le=1.0)
    rag_context_max_chunks: int = Field(default=6, ge=1, le=12)
    rag_context_max_chars: int = Field(default=7000, ge=500, le=20000)
    max_chat_message_length: int = Field(default=2000, ge=100, le=10000)
    max_chat_body_bytes: int = Field(default=8192, ge=1024, le=65536)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    chat_rate_limit_requests: int = Field(default=10, ge=1, le=1000)
    chat_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    trusted_proxy_ips: str = ""
    allow_localhost_origins: bool = False
    embedding_preload: bool = False

    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)

    scraper_user_agent: str = "PropertyIntelligenceBot/0.1 (+https://example.com/crawler)"
    scraper_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    scraper_read_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    scraper_total_timeout_seconds: float = Field(default=30.0, gt=0, le=180)
    scraper_request_delay_seconds: float = Field(default=1.0, ge=0.1, le=30)
    scraper_max_concurrency: int = Field(default=2, ge=1, le=5)
    scraper_max_retries: int = Field(default=2, ge=0, le=5)
    scraper_max_response_bytes: int = Field(default=20_000_000, ge=100_000, le=20_000_000)
    scraper_default_limit: int = Field(default=100, ge=1, le=1000)

    @field_validator("database_url")
    @classmethod
    def validate_async_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        return value

    @field_validator("frontend_url")
    @classmethod
    def validate_frontend_urls(cls, value: str) -> str:
        origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("FRONTEND_URL must contain comma-separated HTTP(S) origins")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("FRONTEND_URL must contain only HTTP(S) origins without paths")
        return ",".join(origins)

    @field_validator("openrouter_base_url")
    @classmethod
    def validate_openrouter_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("OPENROUTER_BASE_URL must be an HTTP(S) URL")
        return normalized

    @field_validator("openrouter_api_key", mode="before")
    @classmethod
    def normalize_empty_openrouter_key(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("trusted_proxy_ips")
    @classmethod
    def validate_trusted_proxy_ips(cls, value: str) -> str:
        addresses = [item.strip() for item in value.split(",") if item.strip()]
        for address in addresses:
            ip_address(address)
        return ",".join(addresses)

    @model_validator(mode="after")
    def validate_production_safety(self) -> "Settings":
        if self.rag_chunk_overlap_chars >= self.rag_chunk_target_chars:
            raise ValueError("RAG chunk overlap must be smaller than the target size")
        if self.rag_chunk_min_chars > self.rag_chunk_target_chars:
            raise ValueError("RAG minimum chunk size cannot exceed the target size")
        if self.max_chat_body_bytes <= self.max_chat_message_length:
            raise ValueError("MAX_CHAT_BODY_BYTES must exceed MAX_CHAT_MESSAGE_LENGTH")
        if self.app_env == "production":
            if urlsplit(self.openrouter_base_url).scheme != "https":
                raise ValueError("OPENROUTER_BASE_URL must use HTTPS in production")
            if not self.allow_localhost_origins and any(
                _is_loopback_origin(origin) for origin in self.cors_origins
            ):
                raise ValueError(
                    "Production FRONTEND_URL cannot use a loopback origin unless "
                    "ALLOW_LOCALHOST_ORIGINS=true"
                )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return self.frontend_url.split(",")

    @property
    def is_development(self) -> bool:
        return self.app_env in {"development", "test"}

    @property
    def trusted_proxies(self) -> frozenset[str]:
        return frozenset(filter(None, self.trusted_proxy_ips.split(",")))


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _is_loopback_origin(origin: str) -> bool:
    hostname = urlsplit(origin).hostname
    if hostname is None:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False
