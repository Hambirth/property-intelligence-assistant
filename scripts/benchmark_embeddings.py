import argparse
import json
import time
from pathlib import Path

import numpy as np
from app.rag.chunking import ChunkingConfig, chunk_document, embedding_text
from app.rag.embeddings import LocalEmbeddingService
from app.rag.evaluation import load_evaluation_cases

DEFAULT_MODELS = (
    "sentence-transformers/all-MiniLM-L6-v2",
    "BAAI/bge-small-en-v1.5",
)


def _records() -> list[dict[str, object]]:
    records = []
    for source in ("darglobal", "wasalt"):
        path = Path(f"data/processed/{source}.jsonl")
        records.extend(json.loads(line) for line in path.read_text().splitlines())
    return records


def _benchmark(model_name: str) -> dict[str, object]:
    config = ChunkingConfig()
    chunk_rows = []
    for record in _records():
        drafts = chunk_document(
            str(record["text"]),
            document_id=str(record["canonical_url"]),
            source=str(record["source"]),
            canonical_url=str(record["canonical_url"]),
            title=str(record["title"]),
            source_type=str(record["metadata"].get("source_format", "unknown")),
            property_metadata=dict(record["metadata"]),
            document_content_hash=str(record["content_hash"]),
            embedding_model=model_name,
            config=config,
        )
        chunk_rows.extend(
            {
                "source": str(record["source"]),
                "url": str(record["canonical_url"]),
                "text": embedding_text(draft),
            }
            for draft in drafts
        )

    started = time.perf_counter()
    embeddings = LocalEmbeddingService(model_name)
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    document_vectors = np.asarray(
        embeddings.embed_documents([str(row["text"]) for row in chunk_rows])
    )
    document_embedding_seconds = time.perf_counter() - started

    cases = load_evaluation_cases(Path("backend/data/evaluation/retrieval_queries.json"))
    recalls = {1: [], 3: [], 5: []}
    reciprocal_ranks = []
    query_latencies = []
    no_answer_scores = []
    for case in cases:
        started = time.perf_counter()
        query_vector = np.asarray(embeddings.embed_query(case.query))
        query_latencies.append((time.perf_counter() - started) * 1000)
        allowed = [
            index
            for index, row in enumerate(chunk_rows)
            if case.source is None or row["source"] == case.source.value
        ]
        similarities = document_vectors[allowed] @ query_vector
        order = np.argsort(-similarities)
        ranked_urls = list(
            dict.fromkeys(str(chunk_rows[allowed[int(index)]]["url"]) for index in order)
        )
        expected = set(case.expected_urls)
        if not expected:
            no_answer_scores.append(float(similarities[order[0]]))
            continue
        for k, values in recalls.items():
            values.append(len(set(ranked_urls[:k]) & expected) / len(expected))
        rank = next(
            (index + 1 for index, url in enumerate(ranked_urls) if url in expected), None
        )
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)

    answerable = len(reciprocal_ranks)
    return {
        "model": model_name,
        "dimension": embeddings.dimension,
        "chunks": len(chunk_rows),
        "load_seconds": round(load_seconds, 4),
        "document_embedding_seconds": round(document_embedding_seconds, 4),
        "average_query_embedding_ms": round(sum(query_latencies) / len(query_latencies), 3),
        "recall_at_1": round(sum(recalls[1]) / answerable, 4),
        "recall_at_3": round(sum(recalls[3]) / answerable, 4),
        "recall_at_5": round(sum(recalls[5]) / answerable, 4),
        "mrr": round(sum(reciprocal_ranks) / answerable, 4),
        "average_no_answer_top_similarity": round(
            sum(no_answer_scores) / len(no_answer_scores), 4
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local embedding candidates")
    parser.add_argument("--model", action="append", dest="models")
    args = parser.parse_args()
    for model_name in args.models or DEFAULT_MODELS:
        print(json.dumps(_benchmark(model_name), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
