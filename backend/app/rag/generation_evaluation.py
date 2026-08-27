import html
import json
import re
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.rag.generation import GroundedRAGService, RAGResponse, RAGStatus
from app.rag.openrouter import ChatMessage, LLMCompletion
from app.rag.prompting import STANDARD_REFUSAL
from app.scraping.models import SourceName


class GenerationCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: str
    query: str
    answerable: bool
    expected_urls: list[str] = Field(default_factory=list)
    expected_terms: list[str] = Field(default_factory=list)
    expected_sources: list[SourceName] = Field(default_factory=list)
    source_filter: SourceName | None = None
    mock_answer: str = STANDARD_REFUSAL


class GenerationMetrics(BaseModel):
    mode: str
    models: list[str]
    total_cases: int
    answerable_cases: int
    unanswerable_cases: int
    grounded_answer_rate: float
    unsupported_answer_rate: float
    refusal_accuracy: float
    false_refusal_rate: float
    citation_presence: float
    citation_validity: float
    source_attribution_accuracy: float
    darglobal_grounded_rate: float | None
    wasalt_grounded_rate: float | None
    average_retrieval_ms: float
    average_generation_ms: float
    average_total_rag_ms: float
    failures: list[str] = Field(default_factory=list)
    failure_details: list[str] = Field(default_factory=list)


class ScriptedEvaluationGenerator:
    """A deterministic provider double; it measures orchestration, not model quality."""

    def __init__(self, case: GenerationCase) -> None:
        self._case = case

    async def generate(self, messages: Sequence[ChatMessage]) -> LLMCompletion:
        context = messages[-1].content
        available = {
            html.unescape(url): source_id
            for source_id, url in re.findall(r'id="(S\d+)"[^>]+url="([^"]+)"', context)
        }
        citation_ids = [
            available[url] for url in self._case.expected_urls if url in available
        ]
        if not self._case.answerable or not citation_ids:
            answer = STANDARD_REFUSAL
            citation_ids = []
        else:
            answer = self._case.mock_answer
        return LLMCompletion(
            content=json.dumps({"answer": answer, "citations": citation_ids}),
            model="mocked/grounded-fixture",
        )


def load_generation_cases(path: Path) -> list[GenerationCase]:
    return TypeAdapter(list[GenerationCase]).validate_python(
        json.loads(path.read_text(encoding="utf-8"))
    )


async def evaluate_generation(
    cases: list[GenerationCase],
    service_factory,
    *,
    mode: str,
) -> GenerationMetrics:
    responses: list[tuple[GenerationCase, RAGResponse]] = []
    for case in cases:
        service: GroundedRAGService = service_factory(case)
        response = await service.answer(case.query, source=case.source_filter)
        responses.append((case, response))

    answerable = [(case, response) for case, response in responses if case.answerable]
    unanswerable = [(case, response) for case, response in responses if not case.answerable]
    answered = [
        (case, response)
        for case, response in responses
        if response.status is RAGStatus.ANSWERED
    ]
    grounded = [
        (case, response)
        for case, response in answerable
        if _is_grounded(case, response)
    ]
    failures = [
        case.case_id
        for case, response in answerable
        if not _is_grounded(case, response)
    ]
    failures.extend(
        case.case_id
        for case, response in unanswerable
        if response.status is RAGStatus.ANSWERED
    )
    failure_details = [
        _failure_detail(case, response)
        for case, response in responses
        if case.case_id in failures
    ]
    return GenerationMetrics(
        mode=mode,
        models=sorted(
            {response.model for _, response in responses if response.model is not None}
        ),
        total_cases=len(responses),
        answerable_cases=len(answerable),
        unanswerable_cases=len(unanswerable),
        grounded_answer_rate=_rate(len(grounded), len(answerable)),
        unsupported_answer_rate=_rate(
            sum(response.status is RAGStatus.ANSWERED for _, response in unanswerable),
            len(unanswerable),
        ),
        refusal_accuracy=_rate(
            sum(response.status is RAGStatus.REFUSED for _, response in unanswerable),
            len(unanswerable),
        ),
        false_refusal_rate=_rate(
            sum(response.status is RAGStatus.REFUSED for _, response in answerable),
            len(answerable),
        ),
        citation_presence=_rate(
            sum(bool(response.citations) for _, response in answered), len(answered)
        ),
        citation_validity=_rate(
            sum(
                all(citation.url.startswith(("https://wasalt.sa/", "https://cdn.darglobal.co.uk/"))
                    for citation in response.citations)
                for _, response in answered
            ),
            len(answered),
        ),
        source_attribution_accuracy=_rate(
            sum(_attribution_correct(case, response) for case, response in answered),
            len(answered),
        ),
        darglobal_grounded_rate=_source_rate(answerable, SourceName.DAR_GLOBAL),
        wasalt_grounded_rate=_source_rate(answerable, SourceName.WASALT),
        average_retrieval_ms=_average([response.timings.retrieval_ms for _, response in responses]),
        average_generation_ms=_average([response.timings.llm_ms for _, response in responses]),
        average_total_rag_ms=_average([response.timings.total_rag_ms for _, response in responses]),
        failures=list(dict.fromkeys(failures)),
        failure_details=failure_details,
    )


def _is_grounded(case: GenerationCase, response: RAGResponse) -> bool:
    citation_urls = {citation.url for citation in response.citations}
    return (
        response.status is RAGStatus.ANSWERED
        and all(_term_present(term, response.answer) for term in case.expected_terms)
        and set(case.expected_urls).issubset(citation_urls)
    )


def _attribution_correct(case: GenerationCase, response: RAGResponse) -> bool:
    actual = {citation.source for citation in response.citations}
    return set(case.expected_sources).issubset(actual)


def _failure_detail(case: GenerationCase, response: RAGResponse) -> str:
    missing_terms = [
        term for term in case.expected_terms if not _term_present(term, response.answer)
    ]
    missing_urls = sorted(
        set(case.expected_urls) - {citation.url for citation in response.citations}
    )
    return (
        f"{case.case_id}:status={response.status.value};reason={response.refusal_reason};"
        f"error={response.error_category};missing_terms={missing_terms};missing_urls={missing_urls}"
    )


def _term_present(term: str, answer: str) -> bool:
    if term.casefold() in answer.casefold():
        return True
    if term.isdigit():
        return term in "".join(character for character in answer if character.isdigit())
    return False


def _source_rate(
    rows: list[tuple[GenerationCase, RAGResponse]], source: SourceName
) -> float | None:
    matching = [(case, response) for case, response in rows if source in case.expected_sources]
    if not matching:
        return None
    return _rate(sum(_is_grounded(case, response) for case, response in matching), len(matching))


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0
