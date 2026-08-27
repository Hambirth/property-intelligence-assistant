from types import SimpleNamespace

from app.rag.chunking import ChunkingConfig, chunk_document
from app.rag.vectorization import _is_unchanged


def _drafts(content: str = "Meaningful property information. " * 20):
    return chunk_document(
        content,
        document_id="00000000-0000-0000-0000-000000000001",
        source="darglobal",
        canonical_url="https://cdn.darglobal.co.uk/example.pdf",
        title="Example",
        source_type="pdf",
        property_metadata={},
        document_content_hash="a" * 64,
        embedding_model="BAAI/bge-small-en-v1.5",
        config=ChunkingConfig(target_chars=300, overlap_chars=60, min_chars=80),
    )


def test_identical_vectorization_state_is_unchanged() -> None:
    drafts = _drafts()
    stored = [
        SimpleNamespace(
            chunk_index=draft.chunk_index,
            content_hash=draft.content_hash,
            metadata_=draft.metadata.model_dump(mode="json"),
        )
        for draft in drafts
    ]

    assert _is_unchanged(stored, drafts)


def test_changed_document_requires_replacement() -> None:
    drafts = _drafts()
    changed = _drafts("Changed property information. " * 20)
    stored = [
        SimpleNamespace(
            chunk_index=draft.chunk_index,
            content_hash=draft.content_hash,
            metadata_=draft.metadata.model_dump(mode="json"),
        )
        for draft in drafts
    ]

    assert not _is_unchanged(stored, changed)
