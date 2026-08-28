import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware import ChatBodyLimitMiddleware, ChatRateLimitMiddleware
from app.api.rate_limit import FixedWindowRateLimiter
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import engine
from app.rag.embeddings import get_embedding_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_application: FastAPI):
    settings = get_settings()
    preload_task: asyncio.Task[None] | None = None
    if settings.embedding_preload:
        preload_task = asyncio.create_task(_preload_embeddings(settings))
    try:
        yield
    finally:
        if preload_task is not None and not preload_task.done():
            preload_task.cancel()
        await engine.dispose()


async def _preload_embeddings(settings: Settings) -> None:
    started = time.perf_counter()
    try:
        await asyncio.to_thread(
            get_embedding_service,
            settings.embedding_model,
            settings.embedding_batch_size,
            provider=settings.embedding_provider,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.openrouter_timeout_seconds,
            max_retries=settings.openrouter_max_retries,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Optional embedding preload failed; lazy loading remains available")
    else:
        logger.info(
            "Embedding model preloaded",
            extra={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
        )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=_lifespan,
    )
    application.state.chat_rate_limiter = FixedWindowRateLimiter(
        limit=settings.chat_rate_limit_requests,
        window_seconds=settings.chat_rate_limit_window_seconds,
    )
    application.add_middleware(
        ChatBodyLimitMiddleware,
        max_body_bytes=settings.max_chat_body_bytes,
    )
    application.add_middleware(
        ChatRateLimitMiddleware,
        trusted_proxies=settings.trusted_proxies,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    application.add_middleware(RequestContextMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(chat_router)
    return application


app = create_app()
