import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.rag.openrouter import (
    ChatMessage,
    LLMErrorCategory,
    OpenRouterClient,
    OpenRouterError,
)


async def _no_sleep(_seconds: float) -> None:
    return None


def _client(handler, *, retries: int = 2, key: str = "arbitrary-secret") -> OpenRouterClient:
    return OpenRouterClient(
        api_key=SecretStr(key),
        model="openrouter/free",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=1,
        max_retries=retries,
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )


async def test_openrouter_success_uses_bearer_key_without_exposing_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer arbitrary-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "openrouter/free"
        assert "models" not in payload
        assert payload["response_format"]["type"] == "json_schema"
        schema = payload["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"]["required"] == ["answer", "citations"]
        assert schema["schema"]["additionalProperties"] is False
        assert payload["provider"] == {"require_parameters": True}
        assert payload["max_tokens"] == 2400
        assert "reasoning" not in payload
        return httpx.Response(
            200,
            json={
                "model": "resolved/free-model",
                "choices": [{"message": {"content": '{"answer":"A","citations":["S1"]}'}}],
            },
        )

    completion = await _client(handler).generate([ChatMessage(role="user", content="question")])

    assert completion.model == "resolved/free-model"
    assert completion.content.startswith("{")


@pytest.mark.parametrize("status", [400, 401, 403, 422])
async def test_openrouter_does_not_retry_permanent_client_errors(status: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, text="provider secret details")

    with pytest.raises(OpenRouterError) as captured:
        await _client(handler).generate([ChatMessage(role="user", content="question")])

    assert captured.value.category is LLMErrorCategory.UNAVAILABLE
    assert attempts == 1
    assert "provider secret details" not in str(captured.value)


@pytest.mark.parametrize(
    ("status", "category"),
    [(429, LLMErrorCategory.RATE_LIMITED), (503, LLMErrorCategory.UNAVAILABLE)],
)
async def test_openrouter_retries_transient_statuses(
    status: int, category: LLMErrorCategory
) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, headers={"Retry-After": "0"})

    with pytest.raises(OpenRouterError) as captured:
        await _client(handler).generate([ChatMessage(role="user", content="question")])

    assert captured.value.category is category
    assert attempts == 3


async def test_openrouter_timeout_is_bounded_and_categorized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private timeout detail", request=request)

    with pytest.raises(OpenRouterError) as captured:
        await _client(handler, retries=1).generate([ChatMessage(role="user", content="question")])

    assert captured.value.category is LLMErrorCategory.TIMEOUT


async def test_openrouter_enforces_absolute_response_deadline() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={})

    client = OpenRouterClient(
        api_key=SecretStr("arbitrary-secret"),
        model="openrouter/free",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=0.01,
        max_retries=0,
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )

    with pytest.raises(OpenRouterError) as captured:
        await client.generate([ChatMessage(role="user", content="question")])

    assert captured.value.category is LLMErrorCategory.TIMEOUT


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{"message": {}}]}])
async def test_openrouter_rejects_malformed_success(payload: dict[str, object]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(OpenRouterError) as captured:
        await _client(handler).generate([ChatMessage(role="user", content="question")])

    assert captured.value.category is LLMErrorCategory.INVALID_RESPONSE


async def test_missing_key_fails_only_when_generation_is_requested() -> None:
    client = OpenRouterClient(
        api_key=None,
        model="openrouter/free",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=1,
        max_retries=0,
    )

    with pytest.raises(OpenRouterError) as captured:
        await client.generate([ChatMessage(role="user", content="question")])

    assert captured.value.category is LLMErrorCategory.UNAVAILABLE


async def test_explicit_model_is_respected_without_free_fallbacks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "provider/custom-model"
        assert "models" not in payload
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"answer":"A","citations":[]}'}}]},
        )

    client = OpenRouterClient(
        api_key=SecretStr("arbitrary-secret"),
        model="provider/custom-model",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=1,
        max_retries=0,
        transport=httpx.MockTransport(handler),
    )

    await client.generate([ChatMessage(role="user", content="question")])


async def test_cancellation_is_not_swallowed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _client(handler).generate([ChatMessage(role="user", content="question")])
