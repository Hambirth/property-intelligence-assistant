import json
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.rag.context import ContextSource
from app.rag.prompting import STANDARD_REFUSAL
from app.rag.retrieval import RetrievalResult


class GroundingValidationError(ValueError):
    pass


class ParsedAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str
    citation_ids: list[str] = Field(default_factory=list)


class EvidenceDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    sufficient: bool
    reason: str | None = None


class EvidenceGate:
    def __init__(self, minimum_similarity: float) -> None:
        self.minimum_similarity = minimum_similarity

    def evaluate(self, question: str, results: Sequence[RetrievalResult]) -> EvidenceDecision:
        if not results or results[0].similarity < self.minimum_similarity:
            return EvidenceDecision(sufficient=False, reason="LOW_RETRIEVAL_CONFIDENCE")
        if _is_comparison(question) and len({row.canonical_url for row in results}) < 2:
            return EvidenceDecision(sufficient=False, reason="INCOMPLETE_COMPARISON_EVIDENCE")
        return EvidenceDecision(sufficient=True)


def is_adversarial_question(question: str) -> bool:
    normalized = " ".join(question.casefold().split())
    patterns = (
        r"ignore (all |the )?(previous|prior|system) instructions",
        r"reveal (the |your )?(openrouter )?(system prompt|api key|secret|credentials)",
        r"show (me )?(the |your )?(openrouter )?(api key|system prompt|secret|credentials)",
        r"make up|fabricate|invent (a |the )?(price|answer|fact)",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def parse_grounded_answer(raw: str, sources: Sequence[ContextSource]) -> ParsedAnswer:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        answer = payload.get("answer")
        citations = payload.get("citations", [])
        if not isinstance(answer, str) or not isinstance(citations, list) or not all(
            isinstance(item, str) for item in citations
        ):
            raise GroundingValidationError("Malformed grounded response")
    else:
        answer = text
        citations = re.findall(r"\[(S\d+)\]", text)

    answer = answer.strip()
    if not answer:
        raise GroundingValidationError("Empty grounded response")
    if re.search(r"https?://", answer, flags=re.I):
        raise GroundingValidationError("Model-generated URLs are not accepted")

    known = {source.source_id for source in sources}
    unknown = set(citations) - known
    if unknown:
        raise GroundingValidationError("Unknown citation ID")
    unique = list(dict.fromkeys(citations))
    if answer != STANDARD_REFUSAL and not unique:
        raise GroundingValidationError("Grounded answer has no citation")
    if answer == STANDARD_REFUSAL and unique:
        raise GroundingValidationError("Refusal cannot cite sources")
    return ParsedAnswer(answer=answer, citation_ids=unique)


def _is_comparison(question: str) -> bool:
    lowered = question.casefold()
    return any(word in lowered for word in ("compare", "versus", " vs ", "difference between"))
