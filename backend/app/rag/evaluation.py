import json
import math
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.rag.retrieval import VectorRetrievalService
from app.scraping.models import SourceName


class EvaluationCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    category: str
    query: str
    expected_urls: list[str]
    source: SourceName | None = None


class EvaluationMetrics(BaseModel):
    evaluated_queries: int
    no_answer_queries: int
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    average_retrieval_ms: float
    p95_retrieval_ms: float
    average_no_answer_top_similarity: float | None = None
    minimum_answerable_top_similarity: float
    maximum_no_answer_top_similarity: float | None = None
    calibrated_midpoint_threshold: float | None = None
    failures: list[str] = Field(default_factory=list)


def load_evaluation_cases(path: Path) -> list[EvaluationCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[EvaluationCase]).validate_python(payload)


async def evaluate_retrieval(
    service: VectorRetrievalService, cases: list[EvaluationCase]
) -> EvaluationMetrics:
    recalls = {1: [], 3: [], 5: []}
    reciprocal_ranks = []
    latencies = []
    no_answer_scores = []
    answerable_top_scores = []
    failures = []

    if cases:
        warmup = cases[0]
        await service.search(warmup.query, top_k=20, source=warmup.source)

    for case in cases:
        started = time.perf_counter()
        results = await service.search(case.query, top_k=20, source=case.source)
        latencies.append((time.perf_counter() - started) * 1000)
        ranked_urls = list(dict.fromkeys(result.canonical_url for result in results))
        expected = set(case.expected_urls)
        if not expected:
            if results:
                no_answer_scores.append(results[0].similarity)
            continue

        if results:
            answerable_top_scores.append(results[0].similarity)

        for k in recalls:
            recalls[k].append(len(set(ranked_urls[:k]) & expected) / len(expected))
        rank = next(
            (index + 1 for index, url in enumerate(ranked_urls) if url in expected), None
        )
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        if rank is None or rank > 5:
            failures.append(case.case_id)

    evaluated = len(reciprocal_ranks)
    if not evaluated:
        raise ValueError("Evaluation dataset has no answerable queries")
    ordered_latencies = sorted(latencies)
    p95_index = max(
        0,
        min(
            len(ordered_latencies) - 1,
            math.ceil(len(ordered_latencies) * 0.95) - 1,
        ),
    )
    return EvaluationMetrics(
        evaluated_queries=evaluated,
        no_answer_queries=len(cases) - evaluated,
        recall_at_1=round(sum(recalls[1]) / evaluated, 4),
        recall_at_3=round(sum(recalls[3]) / evaluated, 4),
        recall_at_5=round(sum(recalls[5]) / evaluated, 4),
        mrr=round(sum(reciprocal_ranks) / evaluated, 4),
        average_retrieval_ms=round(sum(latencies) / len(latencies), 3),
        p95_retrieval_ms=round(ordered_latencies[p95_index], 3),
        average_no_answer_top_similarity=(
            round(sum(no_answer_scores) / len(no_answer_scores), 4)
            if no_answer_scores
            else None
        ),
        minimum_answerable_top_similarity=round(min(answerable_top_scores), 4),
        maximum_no_answer_top_similarity=(
            round(max(no_answer_scores), 4) if no_answer_scores else None
        ),
        calibrated_midpoint_threshold=(
            round((min(answerable_top_scores) + max(no_answer_scores)) / 2, 4)
            if no_answer_scores and min(answerable_top_scores) > max(no_answer_scores)
            else None
        ),
        failures=failures,
    )
