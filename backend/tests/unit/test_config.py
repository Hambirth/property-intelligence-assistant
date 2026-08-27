import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_openrouter_key_is_optional() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        openrouter_api_key=None,
    )

    assert settings.openrouter_api_key is None
    assert settings.openrouter_model == "openrouter/free"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openrouter_max_retries == 2


def test_blank_openrouter_key_is_treated_as_unconfigured() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        openrouter_api_key="   ",
    )

    assert settings.openrouter_api_key is None


def test_cors_origins_are_normalized() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        frontend_url="http://localhost:3000/, https://example.com/",
    )

    assert settings.cors_origins == ["http://localhost:3000", "https://example.com"]


def test_cors_origins_reject_paths_credentials_and_production_loopback() -> None:
    for frontend_url in (
        "https://example.com/path",
        "https://user:password@example.com",
        "https://example.com?origin=bad",
    ):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                database_url="postgresql+asyncpg://user:password@db:5432/app",
                frontend_url=frontend_url,
            )

    with pytest.raises(ValidationError, match="loopback"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://user:password@db:5432/app",
            frontend_url="http://127.0.0.1:3000",
        )

    explicit_local = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        frontend_url="http://localhost:3000",
        allow_localhost_origins=True,
    )
    assert explicit_local.cors_origins == ["http://localhost:3000"]


def test_production_openrouter_endpoint_requires_https() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            _env_file=None,
            app_env="production",
            allow_localhost_origins=True,
            database_url="postgresql+asyncpg://user:password@db:5432/app",
            openrouter_base_url="http://provider.example/api/v1",
        )


def test_database_url_requires_asyncpg() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql://user:password@db:5432/app")


def test_rag_top_k_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://user:password@db:5432/app",
            rag_top_k=1000,
        )


def test_openrouter_base_url_is_normalized_and_validated() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        openrouter_base_url="https://openrouter.ai/api/v1/",
    )
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://user:password@db:5432/app",
            openrouter_base_url="file:///tmp/provider",
        )


def test_trusted_proxy_addresses_are_explicit_ips() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/app",
        trusted_proxy_ips="127.0.0.1, 2001:db8::1",
    )
    assert settings.trusted_proxies == frozenset({"127.0.0.1", "2001:db8::1"})

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://user:password@db:5432/app",
            trusted_proxy_ips="proxy.example",
        )
