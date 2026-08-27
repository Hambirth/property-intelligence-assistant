import json
import re
from html import escape

from pydantic import BaseModel, ConfigDict, Field

from app.rag.retrieval import RetrievalResult
from app.scraping.models import SourceName


class ContextSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    title: str
    organization: str
    source: SourceName
    canonical_url: str
    text: str
    similarity: float
    metadata: dict[str, object]


class BuiltContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    rendered: str
    sources: list[ContextSource]
    character_count: int = Field(ge=0)


class ContextBuilder:
    def __init__(self, *, max_chunks: int, max_chars: int) -> None:
        self._max_chunks = max_chunks
        self._max_chars = max_chars

    def build(self, results: list[RetrievalResult]) -> BuiltContext:
        selected: list[RetrievalResult] = []
        used = 0
        for result in results:
            if len(selected) >= self._max_chunks or _is_duplicate(result, selected):
                continue
            remaining = self._max_chars - used
            if remaining <= 0:
                break
            text = result.chunk_content.strip()
            if len(text) > remaining:
                text = _truncate(text, remaining)
            if not text:
                break
            selected.append(result.model_copy(update={"chunk_content": text}))
            used += len(text)

        sources = [
            ContextSource(
                source_id=f"S{index}",
                title=result.document_title,
                organization=_organization(result.source),
                source=result.source,
                canonical_url=result.canonical_url,
                text=result.chunk_content,
                similarity=result.similarity,
                metadata=_public_metadata(result.metadata),
            )
            for index, result in enumerate(selected, start=1)
        ]
        rendered = "\n\n".join(_render_source(source) for source in sources)
        return BuiltContext(rendered=rendered, sources=sources, character_count=used)


def _is_duplicate(candidate: RetrievalResult, selected: list[RetrievalResult]) -> bool:
    candidate_tokens = _tokens(candidate.chunk_content)
    for existing in selected:
        existing_tokens = _tokens(existing.chunk_content)
        if candidate.canonical_url == existing.canonical_url:
            overlap = len(candidate_tokens & existing_tokens) / max(
                1, min(len(candidate_tokens), len(existing_tokens))
            )
            if overlap >= 0.82:
                return True
        if candidate_tokens == existing_tokens:
            return True
    return False


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _truncate(text: str, limit: int) -> str:
    if limit < 40:
        return ""
    shortened = text[: limit - 14].rsplit(" ", 1)[0].rstrip()
    return f"{shortened} [truncated]"


def _organization(source: SourceName) -> str:
    return "DarGlobal" if source is SourceName.DAR_GLOBAL else "Wasalt"


def _public_metadata(metadata: dict[str, object]) -> dict[str, object]:
    allowed = {
        "property_name",
        "location",
        "city",
        "country",
        "property_type",
        "bedrooms",
        "bathrooms",
        "amenities",
        "developer",
        "brand_partnership",
        "price",
        "currency",
        "completion",
        "nearby_landmarks",
        "investment_information",
        "language",
        "external_reference",
        "acquisition_method",
        "source_document_url",
        "source_format",
    }
    nested = metadata.get("property_metadata")
    candidate = nested if isinstance(nested, dict) else metadata
    public = {
        key: value
        for key, value in candidate.items()
        if key in allowed and value not in (None, [], "")
    }
    source_type = metadata.get("source_type")
    if source_type not in (None, ""):
        public.setdefault("source_format", source_type)
    return public


def _render_source(source: ContextSource) -> str:
    metadata = json.dumps(source.metadata, ensure_ascii=False, sort_keys=True)
    return (
        f'<source id="{source.source_id}" organization="{escape(source.organization)}" '
        f'title="{escape(source.title)}" url="{escape(source.canonical_url)}">\n'
        f"metadata: {escape(metadata)}\n"
        f"content: {escape(source.text)}\n"
        "</source>"
    )
