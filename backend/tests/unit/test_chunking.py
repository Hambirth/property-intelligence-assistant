from itertools import pairwise

from app.rag.chunking import ChunkingConfig, chunk_document


def _chunk(content: str, config: ChunkingConfig | None = None):
    return chunk_document(
        content,
        document_id="00000000-0000-0000-0000-000000000001",
        source="wasalt",
        canonical_url="https://wasalt.sa/en/property/example",
        title="Example property",
        source_type="html",
        property_metadata={"price": "620000", "currency": "SAR"},
        document_content_hash="a" * 64,
        embedding_model="BAAI/bge-small-en-v1.5",
        config=config or ChunkingConfig(target_chars=300, overlap_chars=60, min_chars=80),
    )


def test_short_document_produces_one_chunk_with_metadata() -> None:
    chunks = _chunk("A concise property description with location, bedrooms, and price. " * 4)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].metadata.source == "wasalt"
    assert chunks[0].metadata.source_type == "html"
    assert chunks[0].metadata.property_metadata["price"] == "620000"
    assert chunks[0].metadata.chunk_index == 0


def test_long_document_prefers_line_boundaries_and_has_bounded_overlap() -> None:
    lines = [f"Section {index}: meaningful property detail number {index}." for index in range(30)]
    chunks = _chunk("\n".join(lines))

    assert len(chunks) > 3
    assert all(len(chunk.content) <= 360 for chunk in chunks)
    for previous, current in pairwise(chunks):
        shared = set(previous.content.splitlines()) & set(current.content.splitlines())
        assert shared


def test_chunking_is_deterministic() -> None:
    content = "\n".join(f"Paragraph {index} with stable content." for index in range(25))

    assert _chunk(content) == _chunk(content)


def test_oversized_paragraph_is_split_without_empty_chunks() -> None:
    chunks = _chunk("property " * 500)

    assert len(chunks) > 1
    assert all(chunk.content.strip() for chunk in chunks)


def test_empty_document_produces_no_chunks() -> None:
    assert _chunk(" \n\t ") == []


def test_invalid_overlap_is_rejected() -> None:
    try:
        ChunkingConfig(target_chars=300, overlap_chars=300, min_chars=80)
    except ValueError as exc:
        assert "overlap" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("invalid chunking config was accepted")
