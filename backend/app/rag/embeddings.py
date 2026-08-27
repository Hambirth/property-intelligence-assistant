from collections.abc import Sequence
from functools import lru_cache
from threading import Lock
from typing import Protocol

import numpy as np

from app.models.chunk import EMBEDDING_DIMENSION

SELECTED_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
SELECTED_EMBEDDING_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


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
            and self.dimension != EMBEDDING_DIMENSION
        ):
            raise RuntimeError(
                f"Selected embedding model dimension {self.dimension} does not match "
                f"database dimension {EMBEDDING_DIMENSION}"
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


_embedding_services: dict[tuple[str, int], LocalEmbeddingService] = {}
_embedding_service_lock = Lock()


def get_embedding_service(
    model_name: str = SELECTED_EMBEDDING_MODEL, batch_size: int = 32
) -> LocalEmbeddingService:
    key = (model_name, batch_size)
    with _embedding_service_lock:
        service = _embedding_services.get(key)
        if service is None:
            service = LocalEmbeddingService(model_name, batch_size=batch_size)
            _embedding_services.clear()
            _embedding_services[key] = service
        return service
