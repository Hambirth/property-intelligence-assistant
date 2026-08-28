from collections.abc import Sequence
from functools import lru_cache
from threading import Lock
from typing import Protocol

import httpx
import numpy as np
from pydantic import SecretStr

from app.models.chunk import EMBEDDING_DIMENSION

SELECTED_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
SELECTED_EMBEDDING_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
LOCAL_BGE_DIMENSION = 384
OPENROUTER_EMBEDDING_MODEL = "liquid/lfm-2.5-embedding-350m:free"
REMOTE_EMBEDDING_INPUT_MAX_CHARS = 400


class _OpenRouterEmbeddingInputError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    model_name: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbeddingService:
    def __init__(self, model_name: str = SELECTED_EMBEDDING_MODEL, *, batch_size: int = 32) -> None:
        self._base_model_name = model_name
        self.model_revision = (
            SELECTED_EMBEDDING_REVISION if model_name == SELECTED_EMBEDDING_MODEL else None
        )
        self.model_name = (
            f"{model_name}@{self.model_revision}" if self.model_revision else model_name
        )
        self.batch_size = batch_size
        self._encode_lock = Lock()
        self._model = _load_model(model_name, self.model_revision)
        dimension_getter = getattr(
            self._model,
            "get_embedding_dimension",
            self._model.get_sentence_embedding_dimension,
        )
        dimension = dimension_getter()
        if dimension is None:
            raise RuntimeError(f"Embedding model did not declare a dimension: {model_name}")
        self.dimension = int(dimension)
        if (
            self._base_model_name == SELECTED_EMBEDDING_MODEL
            and self.dimension != LOCAL_BGE_DIMENSION
        ):
            raise RuntimeError(
                f"Selected embedding model dimension {self.dimension} does not match "
                f"local model dimension {LOCAL_BGE_DIMENSION}"
            )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        clean = _validate_texts(texts)
        if not clean:
            return []
        return self._encode(clean)

    def embed_query(self, text: str) -> list[float]:
        clean = _validate_texts([text])[0]
        query = (
            f"{BGE_QUERY_PREFIX}{clean}"
            if self._base_model_name == SELECTED_EMBEDDING_MODEL
            else clean
        )
        return self._encode([query])[0]

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            with self._encode_lock:
                encoded = self._model.encode(
                    list(texts),
                    batch_size=self.batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
        except Exception as exc:
            raise RuntimeError(f"Embedding generation failed for {self.model_name}") from exc
        array = np.asarray(encoded, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != self.dimension or not np.isfinite(array).all():
            raise RuntimeError("Embedding model returned invalid vectors")
        return array.tolist()


class OpenRouterEmbeddingService:
    """Generate normalized retrieval vectors without loading an in-process ML model."""

    dimension = EMBEDDING_DIMENSION

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model_name: str = OPENROUTER_EMBEDDING_MODEL,
        base_url: str = "https://openrouter.ai/api/v1",
        batch_size: int = 32,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self.model_name = f"openrouter:{model_name}:bounded-input-v1"
        self._request_model = model_name
        self._api_key = api_key.get_secret_value()
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self.batch_size = batch_size
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        clean = _validate_texts(texts)
        return self._embed(clean, input_type="search_document") if clean else []

    def embed_query(self, text: str) -> list[float]:
        clean = _validate_texts([text])
        return self._embed(clean, input_type="search_query")[0]

    def _embed(self, texts: Sequence[str], *, input_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        bounded_texts = [text[:REMOTE_EMBEDDING_INPUT_MAX_CHARS] for text in texts]
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            for start in range(0, len(bounded_texts), self.batch_size):
                batch = bounded_texts[start : start + self.batch_size]
                vectors.extend(
                    self._embed_batch(
                        client,
                        batch,
                        input_type=input_type,
                        headers=headers,
                    )
                )
        return _normalize_remote_vectors(vectors, expected_count=len(texts))

    def _embed_batch(
        self,
        client: httpx.Client,
        texts: list[str],
        *,
        input_type: str,
        headers: dict[str, str],
    ) -> list[list[float]]:
        try:
            response = self._request_with_retries(
                client,
                headers=headers,
                payload={
                    "model": self._request_model,
                    "input": texts,
                    "input_type": input_type,
                },
            )
        except _OpenRouterEmbeddingInputError:
            if len(texts) > 1:
                midpoint = len(texts) // 2
                return self._embed_batch(
                    client, texts[:midpoint], input_type=input_type, headers=headers
                ) + self._embed_batch(
                    client, texts[midpoint:], input_type=input_type, headers=headers
                )
            if len(texts[0]) > REMOTE_EMBEDDING_INPUT_MAX_CHARS:
                return self._embed_batch(
                    client,
                    [texts[0][:REMOTE_EMBEDDING_INPUT_MAX_CHARS]],
                    input_type=input_type,
                    headers=headers,
                )
            raise
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: item["index"])
            return [item["embedding"] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("OpenRouter returned an invalid embedding response") from exc

    def _request_with_retries(
        self,
        client: httpx.Client,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            try:
                response = client.post(self._url, headers=headers, json=payload)
                response.raise_for_status()
                return response
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400:
                    raise _OpenRouterEmbeddingInputError(
                        "OpenRouter rejected an embedding input batch"
                    ) from exc
                retryable = (
                    not isinstance(exc, httpx.HTTPStatusError)
                    or exc.response.status_code in {408, 429, 500, 502, 503, 504}
                )
                if not retryable or attempt >= self.max_retries:
                    raise RuntimeError("OpenRouter embedding request failed") from exc
        raise RuntimeError("OpenRouter embedding request failed")


def _normalize_remote_vectors(
    vectors: Sequence[Sequence[float]], *, expected_count: int
) -> list[list[float]]:
    array = np.asarray(vectors, dtype=np.float32)
    if (
        array.ndim != 2
        or array.shape != (expected_count, EMBEDDING_DIMENSION)
        or not np.isfinite(array).all()
    ):
        raise RuntimeError("OpenRouter returned invalid embedding vectors")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise RuntimeError("OpenRouter returned zero-length embedding vectors")
    return (array / norms).tolist()


def _validate_texts(texts: Sequence[str]) -> list[str]:
    clean = []
    for text in texts:
        normalized = text.strip()
        if not normalized:
            raise ValueError("Cannot embed empty text")
        clean.append(normalized)
    return clean


@lru_cache(maxsize=2)
def _load_model(model_name: str, revision: str | None):
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(model_name, device="cpu", revision=revision)
    except Exception as exc:
        raise RuntimeError(f"Unable to load local embedding model: {model_name}") from exc


_embedding_services: dict[tuple[object, ...], EmbeddingProvider] = {}
_embedding_service_lock = Lock()


def get_embedding_service(
    model_name: str = SELECTED_EMBEDDING_MODEL,
    batch_size: int = 32,
    *,
    provider: str = "local",
    api_key: SecretStr | None = None,
    base_url: str = "https://openrouter.ai/api/v1",
    timeout_seconds: float = 30.0,
    max_retries: int = 2,
) -> EmbeddingProvider:
    key = (provider, model_name, batch_size, base_url, timeout_seconds, max_retries)
    with _embedding_service_lock:
        service = _embedding_services.get(key)
        if service is None:
            if provider == "openrouter":
                if api_key is None:
                    raise RuntimeError("OpenRouter API key is required for remote embeddings")
                service = OpenRouterEmbeddingService(
                    api_key=api_key,
                    model_name=model_name,
                    base_url=base_url,
                    batch_size=batch_size,
                    timeout_seconds=timeout_seconds,
                    max_retries=max_retries,
                )
            elif provider == "local":
                service = LocalEmbeddingService(model_name, batch_size=batch_size)
            else:
                raise ValueError(f"Unsupported embedding provider: {provider}")
            _embedding_services.clear()
            _embedding_services[key] = service
        return service
