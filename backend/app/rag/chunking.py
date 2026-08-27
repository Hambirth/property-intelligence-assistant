import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.scraping.normalization import normalize_text

CHUNKING_VERSION = "paragraph-lines-v1"


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_chars: int = Field(default=900, ge=300, le=4000)
    overlap_chars: int = Field(default=120, ge=0, le=1000)
    min_chars: int = Field(default=180, ge=50, le=1000)

    @model_validator(mode="after")
    def validate_bounds(self) -> "ChunkingConfig":
        if self.overlap_chars >= self.target_chars:
            raise ValueError("Chunk overlap must be smaller than the target size")
        if self.min_chars > self.target_chars:
            raise ValueError("Minimum chunk size cannot exceed the target size")
        return self


class ChunkMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    document_id: str
    canonical_url: str
    title: str
    source_type: str
    property_metadata: dict[str, Any]
    chunk_index: int = Field(ge=0)
    document_content_hash: str
    pipeline_fingerprint: str


class DocumentChunkDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_index: int = Field(ge=0)
    content: str
    content_hash: str
    metadata: ChunkMetadata


def pipeline_fingerprint(*, embedding_model: str, config: ChunkingConfig) -> str:
    payload = {
        "chunking_version": CHUNKING_VERSION,
        "embedding_model": embedding_model,
        "target_chars": config.target_chars,
        "overlap_chars": config.overlap_chars,
        "min_chars": config.min_chars,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def chunk_document(
    content: str,
    *,
    document_id: str,
    source: str,
    canonical_url: str,
    title: str,
    source_type: str,
    property_metadata: dict[str, Any],
    document_content_hash: str,
    embedding_model: str,
    config: ChunkingConfig,
) -> list[DocumentChunkDraft]:
    normalized = normalize_text(content)
    if not normalized:
        return []

    units = _semantic_units(normalized, config.target_chars)
    contents = _pack_units(units, config)
    fingerprint = pipeline_fingerprint(embedding_model=embedding_model, config=config)
    drafts = []
    for index, chunk_content in enumerate(contents):
        normalized_chunk = normalize_text(chunk_content)
        if not normalized_chunk:
            continue
        metadata = ChunkMetadata(
            source=source,
            document_id=document_id,
            canonical_url=canonical_url,
            title=title,
            source_type=source_type,
            property_metadata=property_metadata,
            chunk_index=index,
            document_content_hash=document_content_hash,
            pipeline_fingerprint=fingerprint,
        )
        drafts.append(
            DocumentChunkDraft(
                chunk_index=index,
                content=normalized_chunk,
                content_hash=hashlib.sha256(normalized_chunk.encode()).hexdigest(),
                metadata=metadata,
            )
        )
    return drafts


def embedding_text(draft: DocumentChunkDraft) -> str:
    return f"{draft.metadata.title}\n{draft.content}"


def _semantic_units(content: str, target_chars: int) -> list[str]:
    units: list[str] = []
    for raw_line in content.splitlines():
        line = normalize_text(raw_line)
        if not line:
            continue
        if len(line) <= target_chars:
            units.append(line)
            continue
        sentences = [
            normalize_text(part)
            for part in re.split(r"(?<=[.!?؟])\s+", line)
            if normalize_text(part)
        ]
        for sentence in sentences or [line]:
            units.extend(_split_words(sentence, target_chars))
    return units


def _split_words(text: str, target_chars: int) -> list[str]:
    if len(text) <= target_chars:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    for word in text.split():
        if current and len(" ".join((*current, word))) > target_chars:
            parts.append(" ".join(current))
            current = []
        if len(word) > target_chars:
            if current:
                parts.append(" ".join(current))
                current = []
            parts.extend(
                word[start : start + target_chars]
                for start in range(0, len(word), target_chars)
            )
        else:
            current.append(word)
    if current:
        parts.append(" ".join(current))
    return parts


def _pack_units(units: list[str], config: ChunkingConfig) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and len("\n".join((*current, unit))) > config.target_chars:
            chunks.append("\n".join(current))
            current = _overlap_suffix(current, config.overlap_chars)
            while current and len("\n".join((*current, unit))) > config.target_chars:
                current.pop(0)
        current.append(unit)
    if current:
        chunks.append("\n".join(current))

    if (
        len(chunks) > 1
        and len(chunks[-1]) < config.min_chars
        and len(chunks[-2]) + 1 + len(chunks[-1]) <= config.target_chars + config.overlap_chars
    ):
        chunks[-2] = _merge_without_duplicate_lines(chunks[-2], chunks[-1])
        chunks.pop()
    return chunks


def _overlap_suffix(units: list[str], overlap_chars: int) -> list[str]:
    if overlap_chars == 0:
        return []
    suffix: list[str] = []
    for unit in reversed(units):
        candidate = [unit, *suffix]
        if suffix and len("\n".join(candidate)) > overlap_chars:
            break
        if len(unit) > overlap_chars and not suffix:
            break
        suffix = candidate
    return suffix


def _merge_without_duplicate_lines(first: str, second: str) -> str:
    first_lines = first.splitlines()
    second_lines = second.splitlines()
    max_overlap = min(len(first_lines), len(second_lines))
    overlap = next(
        (
            size
            for size in range(max_overlap, 0, -1)
            if first_lines[-size:] == second_lines[:size]
        ),
        0,
    )
    return "\n".join((*first_lines, *second_lines[overlap:]))
