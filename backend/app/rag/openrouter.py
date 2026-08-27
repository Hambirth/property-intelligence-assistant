import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

logger = logging.getLogger(__name__)


class LLMErrorCategory(StrEnum):
    TIMEOUT = "LLM_TIMEOUT"
    RATE_LIMITED = "LLM_RATE_LIMITED"
    UNAVAILABLE = "LLM_UNAVAILABLE"
    INVALID_RESPONSE = "LLM_INVALID_RESPONSE"


class OpenRouterError(Exception):
    def __init__(self, category: LLMErrorCategory, *, retryable: bool = False) -> None:
        super().__init__(category.value)
        self.category = category
        self.retryable = retryable


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    content: str


class LLMCompletion(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    model: str


Sleep = Callable[[float], Awaitable[None]]
_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 524, 529})
_GROUNDED_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "grounded_property_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": (
                        "A concise answer grounded only in the supplied source excerpts, "
                        "or the required unsupported-answer refusal."
                    ),
                },
                "citations": {
                    "type": "array",
                    "description": (
                        "Source IDs such as S1 that directly support the answer; empty only "
                        "for the required refusal."
                    ),
                    "items": {"type": "string"},
                },
            },
            "required": ["answer", "citations"],
            "additionalProperties": False,
        },
    },
}


class OpenRouterClient:
    """Small direct HTTP client with bounded retries and deliberately safe failures."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None,
        model: str,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
        self._max_retries = max_retries
        self._transport = transport
        self._sleep = sleep

    async def generate(self, messages: Sequence[ChatMessage]) -> LLMCompletion:
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            raise OpenRouterError(LLMErrorCategory.UNAVAILABLE)

        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [message.model_dump() for message in messages],
            "temperature": 0,
            "max_tokens": 2400,
            "stream": False,
            "response_format": _GROUNDED_RESPONSE_FORMAT,
            "provider": {"require_parameters": True},
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    async with asyncio.timeout(self._timeout_seconds):
                        response = await client.post(self._url, headers=headers, json=payload)
                except (TimeoutError, httpx.TimeoutException) as exc:
                    if attempt < self._max_retries:
                        await self._sleep(_retry_delay(attempt, None))
                        continue
                    raise OpenRouterError(LLMErrorCategory.TIMEOUT, retryable=True) from exc
                except httpx.RequestError as exc:
                    if attempt < self._max_retries:
                        await self._sleep(_retry_delay(attempt, None))
                        continue
                    raise OpenRouterError(LLMErrorCategory.UNAVAILABLE, retryable=True) from exc

                if response.status_code in _TRANSIENT_STATUSES:
                    if attempt < self._max_retries:
                        await self._sleep(
                            _retry_delay(attempt, response.headers.get("Retry-After"))
                        )
                        continue
                    category = (
                        LLMErrorCategory.RATE_LIMITED
                        if response.status_code == 429
                        else LLMErrorCategory.TIMEOUT
                        if response.status_code == 408
                        else LLMErrorCategory.UNAVAILABLE
                    )
                    raise OpenRouterError(category, retryable=True)
                if response.status_code >= 400:
                    raise OpenRouterError(LLMErrorCategory.UNAVAILABLE)
                return _parse_completion(response, self._model)

        raise OpenRouterError(LLMErrorCategory.UNAVAILABLE)  # pragma: no cover


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after is not None:
        try:
            return max(0.0, min(float(retry_after), 8.0))
        except ValueError:
            pass
    return min(0.5 * (2**attempt), 4.0)


def _parse_completion(response: httpx.Response, configured_model: str) -> LLMCompletion:
    try:
        payload: Any = response.json()
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
        model = payload.get("model") or configured_model
        if not isinstance(content, str) or not content.strip() or not isinstance(model, str):
            raise TypeError
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning(
            "OpenRouter returned a malformed success payload",
            extra={"response_shape": _safe_response_shape(response)},
        )
        raise OpenRouterError(LLMErrorCategory.INVALID_RESPONSE) from exc
    return LLMCompletion(content=content.strip(), model=model)


def _safe_response_shape(response: httpx.Response) -> dict[str, object]:
    """Describe provider structure without logging generated text or provider details."""
    try:
        payload: Any = response.json()
    except ValueError:
        return {"json": False}
    if not isinstance(payload, dict):
        return {"json": True, "payload_type": type(payload).__name__}
    choices = payload.get("choices")
    shape: dict[str, object] = {
        "json": True,
        "has_choices": isinstance(choices, list),
        "choice_count": len(choices) if isinstance(choices, list) else 0,
        "has_model": isinstance(payload.get("model"), str),
    }
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        shape["finish_reason"] = choices[0].get("finish_reason")
        shape["has_message"] = isinstance(message, dict)
        if isinstance(message, dict):
            content = message.get("content")
            shape["content_type"] = type(content).__name__
            shape["content_length"] = len(content) if isinstance(content, (str, list)) else 0
    return shape
