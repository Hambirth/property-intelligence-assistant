import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.dependencies import RAGAnswerService, get_rag_service
from app.api.rate_limit import RateLimitDecision
from app.api.schemas import APIErrorDetail, APIErrorResponse, ChatRequest, ChatResponse, ChatSource
from app.core.config import Settings, get_settings
from app.rag.generation import RAGResponse, RAGStatus
from app.rag.openrouter import LLMErrorCategory

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger("app.chat")

_ERROR_MAPPING = {
    LLMErrorCategory.TIMEOUT: (
        status.HTTP_504_GATEWAY_TIMEOUT,
        "llm_timeout",
        "The answer service timed out. Please try again.",
    ),
    LLMErrorCategory.RATE_LIMITED: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "llm_rate_limited",
        "The answer service is temporarily busy. Please try again later.",
    ),
    LLMErrorCategory.UNAVAILABLE: (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "llm_unavailable",
        "The answer service is temporarily unavailable. Please try again later.",
    ),
    LLMErrorCategory.INVALID_RESPONSE: (
        status.HTTP_502_BAD_GATEWAY,
        "llm_invalid_response",
        "The answer service returned an invalid response. Please try again.",
    ),
}


@router.post(
    "",
    response_model=ChatResponse,
    responses={
        413: {"model": APIErrorResponse},
        422: {"model": APIErrorResponse},
        429: {"model": APIErrorResponse},
        502: {"model": APIErrorResponse},
        503: {"model": APIErrorResponse},
        504: {"model": APIErrorResponse},
    },
)
async def chat(
    payload: ChatRequest,
    request: Request,
    response: Response,
    service: Annotated[RAGAnswerService, Depends(get_rag_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ChatResponse | JSONResponse:
    decision: RateLimitDecision = request.state.chat_rate_limit_decision
    if len(payload.message) > settings.max_chat_message_length:
        return _validation_error_response(request)
    _set_rate_headers(response, decision)

    request.state.chat_validation_ms = _elapsed_since_request_start(request)
    try:
        async with asyncio.timeout(settings.request_timeout_seconds):
            result = await service.answer(payload.message, source=payload.source)
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        return _request_timeout_response(request, decision)
    except Exception:
        logger.exception("Chat answer service failed safely")
        return _service_error_response(request, decision)
    if result.status is RAGStatus.UNAVAILABLE:
        return _provider_error_response(request, result, decision)
    try:
        api_response = _public_response(request, result)
    except Exception:
        logger.exception("Backend citation validation failed safely")
        return _service_error_response(request, decision)
    response.headers["Cache-Control"] = "no-store"
    _log_chat(request, result, status.HTTP_200_OK, decision)
    return api_response


@router.post(
    "/stream",
    response_model=None,
    response_class=StreamingResponse,
    responses={429: {"model": APIErrorResponse}},
)
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    service: Annotated[RAGAnswerService, Depends(get_rag_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse | JSONResponse:
    decision: RateLimitDecision = request.state.chat_rate_limit_decision
    if len(payload.message) > settings.max_chat_message_length:
        return _validation_error_response(request)
    request.state.chat_validation_ms = _elapsed_since_request_start(request)

    async def events() -> AsyncIterator[str]:
        yield _sse("start", {"request_id": request.state.request_id})
        if await request.is_disconnected():
            return
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                result = await service.answer(payload.message, source=payload.source)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            yield _sse(
                "error",
                {
                    "error": {
                        "code": "request_timeout",
                        "message": "The answer service timed out.",
                        "request_id": request.state.request_id,
                    }
                },
            )
            return
        except Exception:
            logger.exception("Streaming chat answer service failed safely")
            yield _sse(
                "error",
                {
                    "error": {
                        "code": "service_unavailable",
                        "message": "The answer service is temporarily unavailable.",
                        "request_id": request.state.request_id,
                    }
                },
            )
            return
        if await request.is_disconnected():
            return
        if result.status is RAGStatus.UNAVAILABLE:
            http_status, code, message = _error_details(result)
            _log_chat(request, result, http_status, decision)
            yield _sse(
                "error",
                {
                    "error": {
                        "code": code,
                        "message": message,
                        "request_id": request.state.request_id,
                    }
                },
            )
            return
        try:
            public_response = _public_response(request, result)
        except Exception:
            logger.exception("Streaming backend citation validation failed safely")
            yield _sse(
                "error",
                {
                    "error": {
                        "code": "service_unavailable",
                        "message": "The answer service is temporarily unavailable.",
                        "request_id": request.state.request_id,
                    }
                },
            )
            return
        _log_chat(request, result, status.HTTP_200_OK, decision)
        yield _sse("complete", public_response.model_dump(mode="json"))

    headers = {
        "Cache-Control": "no-cache, no-store",
        "X-Accel-Buffering": "no",
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
    }
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


def _public_response(request: Request, result: RAGResponse) -> ChatResponse:
    return ChatResponse(
        answer=result.answer,
        refused=result.status is RAGStatus.REFUSED,
        sources=[
            ChatSource(
                id=citation.source_id,
                title=citation.title,
                url=citation.url,
                source=citation.organization,
            )
            for citation in result.citations
        ],
        request_id=request.state.request_id,
    )


def _validation_error_response(request: Request) -> JSONResponse:
    body = APIErrorResponse(
        error=APIErrorDetail(
            code="message_too_long",
            message="The chat message exceeds the configured maximum length.",
            request_id=request.state.request_id,
        )
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=body.model_dump(),
        headers={"Cache-Control": "no-store"},
    )


def _provider_error_response(
    request: Request, result: RAGResponse, decision: RateLimitDecision
) -> JSONResponse:
    http_status, code, message = _error_details(result)
    body = APIErrorResponse(
        error=APIErrorDetail(
            code=code, message=message, request_id=request.state.request_id
        )
    )
    _log_chat(request, result, http_status, decision)
    return JSONResponse(
        status_code=http_status,
        content=body.model_dump(),
        headers={
            "Cache-Control": "no-store",
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        },
    )


def _service_error_response(request: Request, decision: RateLimitDecision) -> JSONResponse:
    body = APIErrorResponse(
        error=APIErrorDetail(
            code="service_unavailable",
            message="The answer service is temporarily unavailable. Please try again later.",
            request_id=request.state.request_id,
        )
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body.model_dump(),
        headers={
            "Cache-Control": "no-store",
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        },
    )


def _request_timeout_response(request: Request, decision: RateLimitDecision) -> JSONResponse:
    body = APIErrorResponse(
        error=APIErrorDetail(
            code="request_timeout",
            message="The answer service timed out. Please try again.",
            request_id=request.state.request_id,
        )
    )
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content=body.model_dump(),
        headers={
            "Cache-Control": "no-store",
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        },
    )


def _error_details(result: RAGResponse) -> tuple[int, str, str]:
    category = result.error_category or LLMErrorCategory.UNAVAILABLE
    return _ERROR_MAPPING[category]


def _set_rate_headers(response: Response, decision: RateLimitDecision) -> None:
    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)


def _log_chat(
    request: Request,
    result: RAGResponse,
    http_status: int,
    decision: RateLimitDecision,
) -> None:
    elapsed_ms = _elapsed_since_request_start(request)
    validation_ms = request.state.chat_validation_ms
    logger.info(
        "Chat API request completed",
        extra={
            "endpoint": request.url.path,
            "status_code": http_status,
            "validation_api_ms": max(0.0, validation_ms),
            "api_overhead_ms": max(0.0, round(elapsed_ms - result.timings.total_rag_ms, 3)),
            "retrieval_ms": result.timings.retrieval_ms,
            "provider_ms": result.timings.llm_ms,
            "total_rag_ms": result.timings.total_rag_ms,
            "refused": result.status is RAGStatus.REFUSED,
            "model": result.model,
            "rate_limit_allowed": decision.allowed,
        },
    )


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _elapsed_since_request_start(request: Request) -> float:
    return round((time.perf_counter() - request.state.request_started_at) * 1000, 3)
