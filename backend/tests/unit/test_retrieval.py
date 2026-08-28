from app.rag.embeddings import EmbeddingUnavailableError
from app.rag.retrieval import VectorRetrievalService
from app.repositories.chunks import ChunkSearchRow
from app.scraping.models import SourceName


class FakeEmbeddings:
    model_name = "fake"
    dimension = 3

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        assert text
        return [1.0, 0.0, 0.0]


class FakeRepository:
    def __init__(self) -> None:
        self.top_k = None
        self.source = None

    async def search(self, _embedding, *, top_k, source):
        self.top_k = top_k
        self.source = source
        return [
            ChunkSearchRow(
                chunk_content="most relevant",
                similarity=0.95,
                document_title="First",
                source=SourceName.WASALT,
                canonical_url="https://wasalt.sa/first",
                metadata={"chunk_index": 0},
            ),
            ChunkSearchRow(
                chunk_content="less relevant",
                similarity=0.75,
                document_title="Second",
                source=SourceName.WASALT,
                canonical_url="https://wasalt.sa/second",
                metadata={"chunk_index": 0},
            ),
        ]


class UnavailableEmbeddings(FakeEmbeddings):
    def embed_query(self, text):
        raise EmbeddingUnavailableError(text)


class LexicalFallbackRepository(FakeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.lexical_query = None

    async def search_lexical(self, query, *, top_k, source):
        self.lexical_query = query
        return await self.search(None, top_k=top_k, source=source)


async def test_retrieval_preserves_similarity_order_and_source_filter() -> None:
    repository = FakeRepository()
    service = VectorRetrievalService(repository, FakeEmbeddings())

    results = await service.search(
        "three bedroom apartment", top_k=2, source=SourceName.WASALT
    )

    assert [result.similarity for result in results] == [0.95, 0.75]
    assert repository.top_k == 2
    assert repository.source is SourceName.WASALT


async def test_retrieval_validates_top_k_and_query() -> None:
    service = VectorRetrievalService(FakeRepository(), FakeEmbeddings())

    for invalid in (0, 21):
        try:
            await service.search("query", top_k=invalid)
        except ValueError as exc:
            assert "top_k" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("invalid top_k was accepted")

    try:
        await service.search(" ", top_k=1)
    except ValueError as exc:
        assert "empty" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty query was accepted")


async def test_retrieval_falls_back_when_remote_embeddings_are_unavailable() -> None:
    repository = LexicalFallbackRepository()
    service = VectorRetrievalService(repository, UnavailableEmbeddings())

    results = await service.search(
        "The Astera interiors by Aston Martin",
        top_k=2,
        source=SourceName.DAR_GLOBAL,
    )

    assert repository.lexical_query == "The Astera interiors by Aston Martin"
    assert repository.source is SourceName.DAR_GLOBAL
    assert [result.document_title for result in results] == ["First", "Second"]
