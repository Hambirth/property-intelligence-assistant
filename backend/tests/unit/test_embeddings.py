import json

import numpy as np
import pytest
from pydantic import SecretStr

from app.rag import embeddings as embedding_module
from app.rag.embeddings import (
    BGE_QUERY_PREFIX,
    EMBEDDING_DIMENSION,
    LocalEmbeddingService,
    OpenRouterEmbeddingService,
)


class FakeSentenceTransformer:
    def __init__(self, dimension: int = 384) -> None:
        self.dimension = dimension
        self.calls: list[list[str]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def encode(self, texts: list[str], **_kwargs: object) -> np.ndarray:
        self.calls.append(texts)
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors


def test_selected_embedding_dimension_and_query_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeSentenceTransformer()
    monkeypatch.setattr(embedding_module, "_load_model", lambda _name, _revision: model)
    service = LocalEmbeddingService()

    vector = service.embed_query("find a Riyadh residence")

    assert service.dimension == 384
    assert len(vector) == 384
    assert model.calls[-1][0].startswith(BGE_QUERY_PREFIX)


def test_document_embeddings_are_batched_and_not_query_prefixed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeSentenceTransformer()
    monkeypatch.setattr(embedding_module, "_load_model", lambda _name, _revision: model)
    service = LocalEmbeddingService(batch_size=8)

    vectors = service.embed_documents(["first passage", "second passage"])

    assert len(vectors) == 2
    assert model.calls[-1] == ["first passage", "second passage"]


def test_empty_embedding_text_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        embedding_module, "_load_model", lambda _name, _revision: FakeSentenceTransformer()
    )
    service = LocalEmbeddingService()

    with pytest.raises(ValueError, match="empty"):
        service.embed_query("  ")


def test_dimension_mismatch_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        embedding_module, "_load_model", lambda _name, _revision: FakeSentenceTransformer(768)
    )

    with pytest.raises(RuntimeError, match="local model dimension"):
        LocalEmbeddingService()


def test_openrouter_embedding_service_normalizes_and_uses_query_input_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []
    real_client = embedding_module.httpx.Client

    def handler(request):
        requests.append(request)
        return embedding_module.httpx.Response(
            200,
            json={
                "data": [
                    {
                        "index": 0,
                        "embedding": [2.0] + [0.0] * (EMBEDDING_DIMENSION - 1),
                    }
                ]
            },
        )

    transport = embedding_module.httpx.MockTransport(handler)
    monkeypatch.setattr(
        embedding_module.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    service = OpenRouterEmbeddingService(api_key=SecretStr("test-key"))

    vector = service.embed_query("find a residence")

    assert len(vector) == EMBEDDING_DIMENSION
    assert vector[0] == 1.0
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    assert b'"input_type":"search_query"' in requests[0].content


def test_openrouter_embedding_service_splits_rejected_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = embedding_module.httpx.Client
    batch_sizes = []

    def handler(request):
        payload = json.loads(request.content)
        batch_sizes.append(len(payload["input"]))
        if len(payload["input"]) > 1:
            return embedding_module.httpx.Response(400, json={"error": {"message": "too large"}})
        return embedding_module.httpx.Response(
            200,
            json={
                "data": [
                    {
                        "index": 0,
                        "embedding": [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1),
                    }
                ]
            },
        )

    transport = embedding_module.httpx.MockTransport(handler)
    monkeypatch.setattr(
        embedding_module.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    service = OpenRouterEmbeddingService(api_key=SecretStr("test-key"), batch_size=8)

    vectors = service.embed_documents(["one", "two"])

    assert len(vectors) == 2
    assert batch_sizes == [2, 1, 1]


def test_embedding_service_cache_prevents_duplicate_model_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = 0

    def build_service(_model_name: str, *, batch_size: int):
        nonlocal created
        created += 1
        return object()

    embedding_module._embedding_services.clear()
    monkeypatch.setattr(embedding_module, "LocalEmbeddingService", build_service)

    first = embedding_module.get_embedding_service("model", 8)
    second = embedding_module.get_embedding_service("model", 8)

    assert first is second
    assert created == 1
    embedding_module._embedding_services.clear()
