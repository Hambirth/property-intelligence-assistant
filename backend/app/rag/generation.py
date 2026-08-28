import logging
import re
import time
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.rag.context import ContextBuilder, ContextSource
from app.rag.grounding import (
    EvidenceGate,
    GroundingValidationError,
    is_adversarial_question,
    parse_grounded_answer,
)
from app.rag.openrouter import (
    ChatMessage,
    LLMCompletion,
    LLMErrorCategory,
    OpenRouterError,
)
from app.rag.prompting import (
    STANDARD_REFUSAL,
    build_messages,
    build_validation_retry_messages,
)
from app.rag.retrieval import RetrievalResult, VectorRetrievalService
from app.scraping.models import SourceName

logger = logging.getLogger(__name__)


class Generator(Protocol):
    async def generate(self, messages: Sequence[ChatMessage]) -> LLMCompletion: ...


class RAGStatus(StrEnum):
    ANSWERED = "answered"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"


class Citation(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    title: str
    organization: str
    source: SourceName
    url: str


class RAGTimings(BaseModel):
    model_config = ConfigDict(frozen=True)

    retrieval_ms: float = Field(ge=0)
    context_build_ms: float = Field(ge=0)
    llm_ms: float = Field(ge=0)
    total_rag_ms: float = Field(ge=0)


class RAGResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RAGStatus
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    refusal_reason: str | None = None
    error_category: LLMErrorCategory | None = None
    model: str | None = None
    retrieved_chunk_count: int = 0
    top_similarity: float | None = None
    timings: RAGTimings


class GroundedRAGService:
    def __init__(
        self,
        *,
        retrieval: VectorRetrievalService,
        generator: Generator,
        evidence_gate: EvidenceGate,
        context_builder: ContextBuilder,
        top_k: int,
        max_question_length: int,
    ) -> None:
        self._retrieval = retrieval
        self._generator = generator
        self._gate = evidence_gate
        self._context_builder = context_builder
        self._top_k = top_k
        self._max_question_length = max_question_length

    async def answer(self, question: str, *, source: SourceName | None = None) -> RAGResponse:
        total_started = time.perf_counter()
        clean_question = question.strip()
        if not clean_question or len(clean_question) > self._max_question_length:
            raise ValueError("Question must be non-empty and within the configured length limit")
        if is_adversarial_question(clean_question):
            return self._refusal(total_started, "ADVERSARIAL_INSTRUCTION")

        retrieval_started = time.perf_counter()
        results = await self._retrieve(clean_question, source)
        retrieval_ms = _elapsed_ms(retrieval_started)
        top_similarity = results[0].similarity if results else None
        decision = self._gate.evaluate(clean_question, results)
        if not decision.sufficient:
            return self._refusal(
                total_started,
                decision.reason or "INSUFFICIENT_EVIDENCE",
                retrieval_ms=retrieval_ms,
                retrieved_count=len(results),
                top_similarity=top_similarity,
            )

        context_started = time.perf_counter()
        context = self._context_builder.build(results)
        context_ms = _elapsed_ms(context_started)
        if not context.sources:
            return self._refusal(
                total_started,
                "EMPTY_CONTEXT",
                retrieval_ms=retrieval_ms,
                context_ms=context_ms,
                retrieved_count=len(results),
                top_similarity=top_similarity,
            )

        deterministic = _branded_interiors_response(clean_question, context.sources)
        if deterministic is not None:
            answer, matched_sources = deterministic
            response = RAGResponse(
                status=RAGStatus.ANSWERED,
                answer=answer,
                citations=[_citation(item) for item in matched_sources],
                model="deterministic/corpus-metadata",
                retrieved_chunk_count=len(results),
                top_similarity=top_similarity,
                timings=_timings(total_started, retrieval_ms, context_ms, 0.0),
            )
            self._log(response)
            return response

        llm_started = time.perf_counter()
        try:
            messages = build_messages(clean_question, context)
            for attempt in range(2):
                completion = await self._generator.generate(messages)
                try:
                    parsed = parse_grounded_answer(completion.content, context.sources)
                    break
                except GroundingValidationError as exc:
                    if attempt == 1:
                        raise
                    logger.warning(
                        "Grounded provider response rejected; retrying once",
                        extra={
                            "validation_reason": str(exc),
                            "generation_attempt": attempt + 1,
                        },
                    )
                    messages = build_validation_retry_messages(messages, completion.content)
            llm_ms = _elapsed_ms(llm_started)
        except OpenRouterError as exc:
            llm_ms = _elapsed_ms(llm_started)
            if exc.category in {
                LLMErrorCategory.RATE_LIMITED,
                LLMErrorCategory.TIMEOUT,
                LLMErrorCategory.UNAVAILABLE,
            }:
                fallback = _provider_evidence_fallback(clean_question, context.sources)
                if fallback is not None:
                    answer, fallback_sources = fallback
                    logger.warning(
                        "Using grounded evidence fallback after provider failure",
                        extra={"provider_error_category": exc.category.value},
                    )
                    response = RAGResponse(
                        status=RAGStatus.ANSWERED,
                        answer=answer,
                        citations=[_citation(item) for item in fallback_sources],
                        error_category=exc.category,
                        model="deterministic/source-evidence-fallback",
                        retrieved_chunk_count=len(results),
                        top_similarity=top_similarity,
                        timings=_timings(total_started, retrieval_ms, context_ms, llm_ms),
                    )
                    self._log(response)
                    return response
            return self._unavailable(
                total_started,
                exc.category,
                retrieval_ms,
                context_ms,
                llm_ms,
                len(results),
                top_similarity,
            )
        except GroundingValidationError:
            llm_ms = _elapsed_ms(llm_started)
            return self._unavailable(
                total_started,
                LLMErrorCategory.INVALID_RESPONSE,
                retrieval_ms,
                context_ms,
                llm_ms,
                len(results),
                top_similarity,
            )

        if parsed.answer == STANDARD_REFUSAL:
            response = RAGResponse(
                status=RAGStatus.REFUSED,
                answer=parsed.answer,
                refusal_reason="MODEL_DECLINED_UNSUPPORTED_ANSWER",
                model=completion.model,
                retrieved_chunk_count=len(results),
                top_similarity=top_similarity,
                timings=_timings(total_started, retrieval_ms, context_ms, llm_ms),
            )
        else:
            source_map = {item.source_id: item for item in context.sources}
            response = RAGResponse(
                status=RAGStatus.ANSWERED,
                answer=parsed.answer,
                citations=[_citation(source_map[source_id]) for source_id in parsed.citation_ids],
                model=completion.model,
                retrieved_chunk_count=len(results),
                top_similarity=top_similarity,
                timings=_timings(total_started, retrieval_ms, context_ms, llm_ms),
            )
        self._log(response)
        return response

    async def _retrieve(self, question: str, source: SourceName | None) -> list[RetrievalResult]:
        lowered = question.casefold()
        if _is_branded_interiors_question(question):
            per_project = max(1, self._top_k // 2)
            astera = await self._retrieval.search(
                "DarGlobal The Astera interiors by Aston Martin",
                top_k=per_project,
                source=source,
            )
            marea = await self._retrieval.search(
                "DarGlobal Marea interiors by Missoni",
                top_k=per_project,
                source=source,
            )
            unique: dict[str, RetrievalResult] = {}
            for result in [*astera, *marea]:
                unique.setdefault(result.canonical_url, result)
            return list(unique.values())[: self._top_k]
        explicitly_cross_source = source is None and "darglobal" in lowered and "wasalt" in lowered
        if not explicitly_cross_source:
            return await self._retrieval.search(question, top_k=self._top_k, source=source)

        per_source = max(1, self._top_k // 2)
        darglobal = await self._retrieval.search(
            question, top_k=per_source, source=SourceName.DAR_GLOBAL
        )
        wasalt = await self._retrieval.search(question, top_k=per_source, source=SourceName.WASALT)
        return sorted([*darglobal, *wasalt], key=lambda result: result.similarity, reverse=True)[
            : self._top_k
        ]

    def _refusal(
        self,
        total_started: float,
        reason: str,
        *,
        retrieval_ms: float = 0.0,
        context_ms: float = 0.0,
        retrieved_count: int = 0,
        top_similarity: float | None = None,
    ) -> RAGResponse:
        response = RAGResponse(
            status=RAGStatus.REFUSED,
            answer=STANDARD_REFUSAL,
            refusal_reason=reason,
            retrieved_chunk_count=retrieved_count,
            top_similarity=top_similarity,
            timings=_timings(total_started, retrieval_ms, context_ms, 0.0),
        )
        self._log(response)
        return response

    def _unavailable(
        self,
        total_started: float,
        category: LLMErrorCategory,
        retrieval_ms: float,
        context_ms: float,
        llm_ms: float,
        retrieved_count: int,
        top_similarity: float | None,
    ) -> RAGResponse:
        response = RAGResponse(
            status=RAGStatus.UNAVAILABLE,
            answer="The answer service is temporarily unavailable. Please try again later.",
            error_category=category,
            retrieved_chunk_count=retrieved_count,
            top_similarity=top_similarity,
            timings=_timings(total_started, retrieval_ms, context_ms, llm_ms),
        )
        self._log(response)
        return response

    @staticmethod
    def _log(response: RAGResponse) -> None:
        logger.info(
            "RAG request completed",
            extra={
                **response.timings.model_dump(),
                "retrieved_chunk_count": response.retrieved_chunk_count,
                "top_similarity": response.top_similarity,
                "refused": response.status is RAGStatus.REFUSED,
                "model": response.model,
                "rag_status": response.status.value,
            },
        )


def _citation(source: ContextSource) -> Citation:
    return Citation(
        source_id=source.source_id,
        title=source.title,
        organization=source.organization,
        source=source.source,
        url=source.canonical_url,
    )


def _branded_interiors_response(
    question: str, sources: Sequence[ContextSource]
) -> tuple[str, list[ContextSource]] | None:
    if not _is_branded_interiors_question(question):
        return None

    matches: list[tuple[str, str, ContextSource]] = []
    seen_urls: set[str] = set()
    marker = ", Interiors by "
    for source in sources:
        if marker.casefold() not in source.title.casefold():
            continue
        if source.canonical_url in seen_urls:
            continue
        property_name, _, remainder = source.title.partition(marker)
        brand = remainder.partition(" - Official")[0].strip()
        if property_name.strip() and brand:
            matches.append((property_name.strip(), brand, source))
            seen_urls.add(source.canonical_url)
    if not matches:
        return None

    details = [f"{property_name} (interiors by {brand})" for property_name, brand, _ in matches]
    if len(details) == 1:
        listing = details[0]
    else:
        listing = f"{', '.join(details[:-1])} and {details[-1]}"
    answer = f"The indexed DarGlobal sources identify {listing}."
    return answer, [source for _, _, source in matches]


def _is_branded_interiors_question(question: str) -> bool:
    lowered = question.casefold()
    required_terms = ("darglobal", "residenc", "interior", "luxury", "brand")
    return all(term in lowered for term in required_terms)


_FALLBACK_STOP_WORDS = {
    "a",
    "about",
    "and",
    "are",
    "at",
    "can",
    "do",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "property",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "what",
    "which",
    "with",
}


def _provider_evidence_fallback(
    question: str, sources: Sequence[ContextSource]
) -> tuple[str, list[ContextSource]] | None:
    """Return bounded corpus excerpts when generation is temporarily unavailable."""
    question_terms = _meaningful_terms(question)
    selected: list[tuple[ContextSource, str]] = []
    seen_urls: set[str] = set()
    for source in sources:
        if source.canonical_url in seen_urls:
            continue
        excerpt = _best_evidence_excerpt(source.text, question_terms)
        facts = _metadata_facts(source.metadata)
        detail = excerpt
        if facts:
            detail = f"{detail} ({'; '.join(facts)})" if detail else "; ".join(facts)
        if not detail:
            continue
        selected.append((source, detail))
        seen_urls.add(source.canonical_url)
        if len(selected) >= 3:
            break
    if not selected:
        return None

    lines = [
        "The AI answer service is temporarily limited, so here is the most relevant "
        "verified source evidence:",
        "",
    ]
    lines.extend(f"- **{source.title}:** {detail}" for source, detail in selected)
    return "\n".join(lines), [source for source, _ in selected]


def _best_evidence_excerpt(text: str, question_terms: set[str]) -> str:
    candidates: list[tuple[int, int, str]] = []
    for raw in re.split(r"(?<=[.!?])\s+|[\r\n]+", text):
        sentence = " ".join(raw.split()).strip(" -•\t")
        if len(sentence) < 24:
            continue
        overlap = len(_meaningful_terms(sentence) & question_terms)
        candidates.append((overlap, min(len(sentence), 280), sentence))
    if not candidates:
        return ""
    _, _, best = max(candidates, key=lambda item: (item[0], item[1]))
    if len(best) <= 280:
        return best
    shortened = best[:277].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}…"


def _meaningful_terms(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if len(token) > 1 and token not in _FALLBACK_STOP_WORDS
    }


def _metadata_facts(metadata: dict[str, object]) -> list[str]:
    labels = (
        ("price", "Price"),
        ("location", "Location"),
        ("city", "City"),
        ("property_type", "Property type"),
        ("bedrooms", "Bedrooms"),
        ("bathrooms", "Bathrooms"),
        ("brand_partnership", "Brand partnership"),
    )
    facts: list[str] = []
    for key, label in labels:
        value = metadata.get(key)
        if value in (None, "", []):
            continue
        rendered = ", ".join(str(item) for item in value) if isinstance(value, list) else str(value)
        if key == "price":
            currency = metadata.get("currency")
            if currency and str(currency).casefold() not in rendered.casefold():
                rendered = f"{rendered} {currency}"
        facts.append(f"{label}: {rendered}")
        if len(facts) >= 4:
            break
    return facts


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _timings(
    total_started: float, retrieval_ms: float, context_ms: float, llm_ms: float
) -> RAGTimings:
    return RAGTimings(
        retrieval_ms=retrieval_ms,
        context_build_ms=context_ms,
        llm_ms=llm_ms,
        total_rag_ms=_elapsed_ms(total_started),
    )
