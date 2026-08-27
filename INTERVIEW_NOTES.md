# Interview Notes

This file is intentionally started during Phase 1 and will evolve with measured implementation evidence.

## Phase 7 frontend questions

### Why did you use structured SSE instead of token streaming?

The backend must validate grounding and citation IDs before anything reaches a user. Structured SSE
preserves that boundary: `start` acknowledges the request, while one `complete` event releases the
validated answer and trusted source records. Token streaming would risk exposing partial fabricated
URLs or unsupported claims and would complicate cancellation and error presentation for little UX
gain at this corpus size.

### How does the frontend safely render model output?

It treats the answer as text, splits only on paragraph and simple bullet boundaries, and builds React
`p`, `ul`, and `li` nodes. It never uses `dangerouslySetInnerHTML`, never enables raw Markdown HTML,
and never lets model content create links or attributes.

### How do citations work?

The model cites backend context IDs. The backend validates those IDs and maps them to canonical
database URLs, then the frontend renders those returned records as separate source cards. The browser
does not construct, rewrite, or scrape citation URLs; links use opener isolation in a new tab.

### Why doesn't the frontend send chat history?

Phase 6 is deliberately single-turn RAG. Sending visually retained messages would falsely imply
memory and could dilute retrieval or expand prompt-injection exposure. The UI states that every
question is independent and keeps prior messages only in session React state for reading continuity.

### How do you handle provider failures?

The SSE client maps backend-safe categories to separate rate-limit, unavailable, timeout, invalid-
response, and network messages. It uses `Retry-After` for 429 responses when present, shows request
IDs only as error references, never exposes provider bodies, and avoids a fallback request that could
duplicate spend.

### How did you design for accessibility?

The page uses semantic header/main/section/article/footer regions, visible focus rings, labelled
controls, `aria-pressed` source filters, polite answer updates, alert semantics for failures, and an
actual textarea with Enter/Shift+Enter behavior. Controls retain comfortable tap sizes and layouts
were reviewed at 375, 768, and 1440 pixels. Motion is disabled when reduced motion is requested.

## Why FastAPI?

**Choice:** FastAPI with Pydantic and SQLAlchemy 2.x.

**Why:** The API is I/O-heavy, benefits from typed validation and automatic OpenAPI documentation, and sits naturally beside Python scraping/ML libraries.

**Alternatives:** Django/DRF offers more batteries; Flask is smaller; Node would unify languages.

**Trade-off:** Async database/HTTP code requires discipline, and FastAPI does not provide Django's admin/auth ecosystem out of the box.

**Likely question:** Why not make the backend Next.js API routes?

**Short answer:** Keeping ingestion, embeddings, retrieval, and API orchestration in Python avoids cross-language ML friction. Next.js remains focused on the user experience, while the typed HTTP contract keeps the boundary clear.

## Why PostgreSQL and pgvector?

**Choice:** One PostgreSQL database with pgvector and built-in full-text search.

**Why:** The application needs transactional ingestion, uniqueness constraints, relational chat history, JSON metadata, vector search, and lexical search. One database handles the assignment-scale workload cleanly.

**Alternatives:** MongoDB plus a vector store, Pinecone, Weaviate, or Elasticsearch.

**Trade-off:** At very large vector scale, a specialized vector system may offer easier independent scaling. PostgreSQL vector dimensions also make embedding-model changes an explicit migration/re-index operation.

**Likely question:** Why not MongoDB?

**Short answer:** The core entities and consistency rules are relational, and pgvector gives us the needed semantic search without operating a second datastore. MongoDB would not solve a requirement that PostgreSQL fails to meet here.

## Why RAG instead of fine-tuning?

**Choice:** Retrieve current public pages at query time and provide them as bounded context.

**Why:** Property facts and availability change, and the product must show evidence URLs. RAG lets us refresh facts and inspect retrieval independently.

**Alternatives:** Fine-tuning, long-context prompting with the whole corpus.

**Trade-off:** RAG quality depends on ingestion, chunking, retrieval, and thresholds; it adds a retrieval failure mode.

**Likely question:** Would fine-tuning reduce hallucinations?

**Short answer:** Not reliably for changing factual data, and it would not create trustworthy provenance. Fine-tuning could later shape tone or format, but retrieval remains necessary for current, citable facts.

## Why not agents?

**Choice:** A deterministic chat orchestration pipeline.

**Why:** The required workflow is validate → retrieve → gate → prompt → generate → verify citations. There is no open-ended tool-selection problem.

**Trade-off:** The assistant cannot autonomously perform arbitrary research or workflows, which is intentional for safety and predictability.

**Likely question:** Isn't a multi-agent system more impressive?

**Short answer:** The engineering signal is choosing the simplest architecture that meets the business need. Agents would add latency, cost, prompt-injection surface, and harder-to-test behavior without improving this retrieval task.

## How do citations work?

**Choice:** The model sees stable source IDs; the backend maps accepted IDs to retrieved database URLs.

**Why:** A model must never be trusted to produce URLs. The API returns structured, deduplicated source records and stores citation snapshots for audit.

**Likely question:** Does a citation guarantee the claim is supported?

**Short answer:** No. It guarantees provenance of the retrieved context. Claim-level grounding still needs prompt constraints, unknown-ID rejection, low-confidence refusal, and evaluation/manual review.

## How do you reduce hallucination?

Use bounded retrieval, evidence thresholds, a refusal path, explicit separation of fact from recommendation, untrusted-context delimiters, low temperature, source-ID validation, and an evaluation set containing unsupported questions. The system does not claim zero hallucinations; it makes failures visible and measurable.

## How do you handle prompt injection?

Scraped pages are data, never instructions. The LLM has no tools or database credentials, context is delimited and labeled untrusted, the system prompt outranks it, only retrieved source IDs are accepted, inputs/outputs are bounded, and sensitive secrets never enter the prompt.

## Why local sentence-transformer embeddings?

They avoid per-query API cost, keep public corpus data local, and are sufficient for a bounded
corpus. The Phase 4 benchmark compared MiniLM with BGE Small on 19 real-corpus questions. BGE won
first-result quality materially, including the Wasalt cases whose normalized chunks retain Arabic
descriptions alongside English titles and structured facts. The trade-offs are CPU/memory,
cold-start time, model download/cache operations, and full re-embedding when model semantics or
dimensions change.

## Why OpenRouter?

It is an assignment requirement and provides one configurable API across multiple models. The integration is isolated behind a typed client, so model names and provider behavior are not spread through business logic. Free-model limits and variability are treated as deployment risks, not hidden.

## Why a modular monolith?

One deployable backend is easier to debug, test, and operate for an interview assignment. Module boundaries still separate scraping, ingestion, retrieval, and chat, so ingestion can later become a worker without rewriting domain logic.

## How does Docker networking work locally?

Compose services resolve one another by service name on the Compose network: the backend reaches `postgres`, while browser code reaches the published backend URL rather than a container-only hostname. Environment variables distinguish server-side and browser-visible URLs.

## How would this scale?

Keep API instances stateless, move rate-limit/session cache to Redis, run ingestion in queued workers, use pooled PostgreSQL connections, add HNSW after measuring corpus/query load, partition or archive old data, and independently autoscale frontend/API/workers. At millions of chunks, evaluate a dedicated search/vector service based on measured latency and operations—not fashion.

## What if OpenRouter goes down?

Use a strict timeout, limited retry for transient failures, safe 503 response, visible retry UI, and return retrieved sources where helpful. Do not fabricate a model answer. A configured paid fallback is a product decision, not silently enabled in a “free model” assignment.

## How would scraping scale and stay fresh?

Use sitemap-driven incremental discovery, per-domain rate controls, content hashes, transactional updates, scheduled queue jobs, retry/dead-letter tracking, and freshness SLAs by document type. More workers do not override robots policy or origin capacity.

## What would Redis add?

Shared rate limiting, short-lived response/cache data, distributed locks, and queue support across API instances. It is deferred because a single-instance MVP can use bounded in-memory controls and PostgreSQL persistence.

## What would a worker queue add?

Durable retries, scheduled ingestion, isolation of CPU-heavy embeddings from API latency, backpressure, and independent scaling. It is deferred until ingestion frequency/volume justifies the operational cost.

## How would deployment change on AWS/GCP/OCI?

Map the same containers to a managed container runtime, PostgreSQL to the cloud's managed Postgres with pgvector, secrets to its secret manager, images to its registry, logs/metrics to its observability stack, and ingestion to scheduled jobs/queues. The application boundaries remain the same; networking, IAM, autoscaling, and IaC become the main additions.

## Phase 2 implementation decisions

### Why async SQLAlchemy with asyncpg?

The API will spend significant time waiting on PostgreSQL and OpenRouter. One async request path avoids mixing session styles and supports concurrency without one thread per in-flight I/O operation. The engine and session factory are process-level singletons; each request gets a scoped session that is always closed.

### Why is `/health` separate from `/ready`?

Liveness answers whether the process can respond and must not restart a healthy process merely because PostgreSQL is temporarily unavailable. Readiness checks `SELECT 1` and returns 503 so a load balancer can stop routing traffic until the required dependency recovers.

### Why is there no vector column yet?

pgvector is enabled by migration, but `vector(N)` fixes the embedding dimension in the schema. We intentionally defer that irreversible choice until the English-versus-multilingual embedding benchmark provides evidence. Changing it later requires a migration and complete re-embedding.

### How are request IDs implemented?

Middleware accepts only bounded, log-safe IDs matching a conservative character pattern; otherwise it generates a UUID. The ID is bound to a context variable for structured logs and returned on normal and unhandled-error responses. This creates a traceable path without adding an observability vendor.

### Why use a small JSON logging formatter instead of structlog?

The current fields are simple and stable, so the standard library is sufficient and avoids another dependency. The formatter centralizes schema and redacts common credential shapes. Its boundary can later feed OpenTelemetry or a log collector without changing business services.

### Why does the Next.js production build use webpack?

Next.js 16 defaults to Turbopack, but its CSS worker attempted a prohibited internal port bind in the assignment execution environment. The supported `next build --webpack` path builds successfully, is deterministic in the current CI-like environment, and does not change application architecture. Development can still use Turbopack.

### Why run Alembic before the backend in local Compose?

It makes `docker compose up --build` self-initializing for a single local backend. In a horizontally scaled production deployment, migrations become a separate release job so multiple replicas never race schema changes.

## Phase 3 scraping questions

### How did you scrape the websites?

I built fixed DarGlobal and Wasalt adapters behind a shared safe HTTP client. Each run obtains robots first, discovers only through declared sitemaps or a public project index, validates every URL and redirect, fetches conservatively, normalizes semantic HTML, rejects low-quality/challenge pages, hashes the result, and transactionally upserts a typed document.

### Why didn't you use Selenium or Playwright to bypass DarGlobal?

The issue is an explicit Imperva access-control interstitial, not missing JavaScript-rendered content. Automating a browser to defeat it would violate the assignment policy and create legal and operational risk. The correct behavior is a typed blocked result and a future path for legitimately obtained public exports.

### How do you respect robots.txt?

Robots is a runtime gate, not documentation. If it is unavailable, the source is denied. The evaluator chooses the most specific user-agent group and longest matching path rule. Every discovered page and same-host redirect is checked immediately before transport; Wasalt `/search` is covered by a regression test proving zero HTTP requests occur.

### How does your crawler avoid SSRF?

There is no user URL input. Only HTTPS and exact fixed hosts are accepted; IP literals, URL credentials, unexpected ports, malformed URLs, and unknown hosts are rejected. Redirects are manual, revalidated before following, robots checked again, and prohibited from changing host.

### What happens if a site's HTML changes?

Extraction prefers semantic elements and labeled fields, while the cleaned full text remains the retrieval source of truth. Minimal saved fixtures catch expected structures. Required-title/content-quality checks make major breakage fail visibly as `PARSE_ERROR` or `INVALID_CONTENT` instead of silently storing navigation garbage.

### How do you avoid duplicate data?

URLs and same-host canonicals are normalized narrowly, tracking parameters are removed, and cleaned content receives a SHA-256 hash. The run checks URL, canonical, and content duplicates; PostgreSQL enforces unique `(source, canonical_url)` and the repository checks content hashes before insert.

### Why use sitemap-first discovery?

Sitemaps are bounded, publisher-declared inventories that avoid expensive recursive crawling, duplicate navigation routes, and disallowed search pagination. Every sitemap entry still passes host, route, robots, and limit checks.

### How would you scale ingestion?

Move the unchanged adapters into scheduled queue workers, partition work by source, retain per-domain rate limits, use durable retries/dead letters, and separate CPU-heavy downstream processing. More workers must not increase pressure on one origin.

### How would you schedule refreshes in production?

Use a platform scheduler to enqueue source runs at a freshness cadence, then track each run and page outcome. Project pages might refresh daily or weekly; volatile listings more often only if policy and business need justify it. Overlapping runs need a distributed source lock.

### How would you detect changed pages?

Compare SHA-256 of normalized content with the stored hash. An unchanged page refreshes `scraped_at` without downstream work; a changed page updates content and will later trigger re-chunking/re-embedding. ETag and Last-Modified conditional requests are a future transfer optimization, not the correctness mechanism.

### What happens when one source is unavailable?

Failures are typed and isolated. DarGlobal can be blocked while Wasalt discovery continues; individual page errors do not roll back successful documents. Summaries and structured logs expose blocked, rejected, and failed counts without logging HTML or credentials.

## Phase 4 retrieval questions

### Why BGE Small instead of MiniLM?

Both candidates are local, free, compact, and 384-dimensional. On the checked-in evaluation set,
BGE achieved Recall@1 0.9706 and MRR 1.0; MiniLM achieved 0.7647 and 0.8627. MiniLM encoded the
corpus in 6.0099 seconds versus BGE's 22.4625 seconds, but embedding is offline and the corpus is
small, so retrieval relevance matters more than a roughly 16-second batch difference.

### Why cosine similarity?

BGE produces semantic direction rather than a meaningful magnitude for this use. Both passage and
query vectors are L2-normalized, PostgreSQL orders by pgvector cosine distance, and the service
returns `1 - distance`. Keeping one metric throughout avoids ranking drift between Python and SQL.

### Why exact vector search with no HNSW?

There are 212 chunks. A sequential scan is fast, deterministic, needs no index build or tuning, and
does not trade recall for latency. HNSW becomes a candidate only after measured query latency at a
larger corpus justifies its memory and maintenance cost.

### How is vectorization idempotent?

Each chunk carries the parent document hash plus a fingerprint of the embedding model and chunking
configuration. Stored chunk hashes and complete metadata are compared in stable index order. An
unchanged document skips embedding and writes; a changed document's old chunks are deleted and its
new chunks inserted in one transaction. The real repeat run skipped all 212 chunks.

### How do source filters stay safe?

The internal service accepts the fixed `SourceName` enum, not an arbitrary SQL expression.
SQLAlchemy binds the query vector and source value. There is no public endpoint, raw SQL input, or
execution path from retrieved document text.

### Why keep vectors in PostgreSQL?

Documents, provenance, metadata, and chunks need one transactional lifecycle. pgvector adds exact
cosine search without another database, synchronization pipeline, credential set, or operational
surface. At millions of documents, benchmark HNSW/IVFFlat, partitioning, replicas, and background
workers first; adopt a dedicated service only for measured scale or availability needs.

### What do no-answer queries prove?

Dense retrieval always returns a nearest neighbor, even when the corpus lacks the answer. The two
trap queries are excluded from recall/MRR and their mean top similarity (0.5760) is reported
separately. Phase 5 must require grounded evidence and calibrate refusal thresholds; a nearest
chunk alone is not proof that an answer exists.

## Phase 5 grounded-generation questions

### How do you prevent hallucinations?

I refuse before generation when retrieval confidence is below a measured threshold, bound and
deduplicate the context, tell the model to use only that evidence, require citations on every
non-refusal answer, and validate the output. Unknown citations, uncited facts, and model-generated
URLs become an invalid response rather than user-visible content. These controls reduce risk; they
do not prove perfect sentence-level entailment.

### How do you handle prompt injection?

The system instructions are fixed. Retrieved pages are escaped and placed in a labelled untrusted
block in the user message, never in the system message. The prompt says document commands are data,
and deterministic input checks refuse direct override, secret-extraction, and fabrication requests.
Tests cover both malicious user questions and instructions embedded in retrieved content.

### Why don't you trust LLM-generated URLs?

A fluent model can invent a plausible URL or attach the wrong source. The model emits only IDs like
`S1`; the backend validates those IDs against the exact context and maps them to canonical URLs from
PostgreSQL metadata. Any URL in answer text is rejected.

### How did you choose the refusal threshold?

I measured top-score distributions on the labelled retrieval set. Unsupported queries had a maximum
of 0.5854, while answerable queries had a minimum of 0.6300. Their midpoint is 0.6077, so the default
is 0.61. It must be recalibrated when the corpus, embedding model, or query population changes.

### What happens if OpenRouter is unavailable?

The client retries only bounded transient failures, then returns a safe timeout, rate-limit,
unavailable, or invalid-response category. It never leaks provider bodies and never presents raw
retrieval context as a generated answer. The application still starts when no key is configured.

### Why did you use `openrouter/free`?

It satisfies the assignment's free-access constraint and keeps the application configurable when
individual free models change. The returned resolved model is logged for observability, so the router
alias is not mistaken for a fixed underlying model.

### Would you use a free model in production?

Not for a reliability-sensitive service without measurements and an explicit fallback/budget plan.
Free availability, quotas, latency, and model identity can change. Production selection should use a
pinned, evaluated model or controlled fallback set with privacy, cost, and SLO review.

### How do you evaluate RAG quality?

Retrieval uses recall and reciprocal rank plus unsupported-query score distributions. Generation
uses labelled answerable and adversarial cases with deterministic checks for expected facts,
refusal behavior, citation presence/validity, and source attribution. Scripted-provider evaluation
tests orchestration; live-provider results are reported separately and subjective outputs require
manual review rather than self-judging with the same LLM.

## Phase 6 public API questions

### How is your public chat endpoint secured?

It accepts a forbidden-extra Pydantic schema with only a message and fixed source enum, enforces a
hard and configurable size bound, rate-limits before parsing, preserves the grounded RAG controls,
maps provider failures to safe envelopes, emits backend-owned citations, uses explicit CORS, and
logs metrics without bodies. Request IDs connect API and RAG logs without storing conversations.

### How do you prevent abuse?

A fixed-window limiter applies to valid and malformed POST attempts before body parsing. It keys on
the socket peer and ignores spoofable forwarding headers unless an exact trusted proxy is configured.
The assignment uses one process; production would combine authenticated tenant/user limits, IP/edge
limits, payload ceilings, budgets, and anomaly monitoring.

### Why don't clients control retrieval thresholds?

The threshold and top-k are safety policy, not presentation preferences. Letting clients lower the
gate would turn unsupported nearest neighbors into answers; increasing retrieval arbitrarily would
raise cost and injection exposure. They remain calibrated, tested server configuration.

### Why is OpenRouter not part of readiness?

The process can safely validate, rate-limit, refuse low-evidence questions, and report a temporary
provider error while OpenRouter is down. Marking it unready would remove all instances or trigger
restarts, neither of which repairs an external outage. PostgreSQL remains required for retrieval.

### How do you handle provider outages?

Bounded retry policy classifies timeout, rate limit, unavailable, and invalid output. JSON requests
receive stable `504`, `503`, or `502` responses; an SSE request receives a safe error event after its
start event. No upstream body or raw retrieval fallback is shown.

### How would you scale rate limiting?

Move the same key/window policy to an atomic Redis script or a managed edge/API gateway limiter,
then add authenticated tenant and user keys. Configure and test the deployment's exact trusted proxy
chain so instances never trust arbitrary forwarding headers.

### Why SSE instead of WebSockets?

The interaction is one request followed by ordered server events, so SSE/fetch streaming fits normal
HTTP infrastructure and cancellation without a bidirectional session protocol. Structured stages
also let the backend validate the whole answer and citations before release. WebSockets would add
connection state and operational complexity without a current requirement.

## Phase 8 security and production questions

### What was the most important security finding?

The message character limit existed, but JSON had to be parsed before that check. I added a small
chat-route body ceiling before parsing while preserving the earlier rate-limit decision, so malformed
and oversized attempts are both cheap and quota-accounted. I also found that production disabled the
Swagger page but not `/openapi.json`, which is now disabled explicitly.

### How is model output safe in the browser?

The browser never interprets model HTML. A small renderer creates React paragraph, list, heading,
emphasis, and code nodes; raw HTML and Markdown links remain text. The API client requires SSE,
bounds stream size, validates the answer schema, and independently restricts citations to HTTPS
DarGlobal/Wasalt hosts. CSP and external-link isolation are additional layers, not substitutes for
safe rendering.

### Can an attacker bypass rate limiting with forwarded headers or route switching?

Not in the single process under the tested proxy policy. JSON and SSE share the same limiter, and the
socket peer wins unless it is an exact trusted proxy. A trusted chain is fully IP-validated and walked
from the proxy side. This does not solve horizontal scaling: production replicas need one shared
limiter at Redis, the gateway, reverse proxy, or edge.

### What happens when PostgreSQL or the embedding model fails during SSE?

Cancellation still propagates, but other internal failures are caught inside the event generator and
become one generic `service_unavailable` event. No exception text, database value, retrieved context,
or partial model answer is sent. The non-stream route returns the corresponding safe `503` envelope.

### Why preload embeddings in the background?

The measured first load added about 5.8 seconds. `EMBEDDING_PRELOAD=true` starts a best-effort warm-up
without making liveness wait for the model or failing startup if the cache is unavailable. A locked
cache ensures a warm-up/request race cannot create multiple model instances in one process. Lazy load
remains the fallback.

### What information reaches OpenRouter?

Only the current question and the bounded retrieved excerpts/metadata required for that answer.
Prior turns, unrelated documents, database credentials, and the provider key are not included. This
is still a third-party data transfer, so provider retention, region, and model-subprocessor policies
must be reviewed before production.

### What would still block a real public launch?

The assignment needs deployment-specific TLS and secret injection, a non-superuser runtime database
role, migrations as a controlled release step, shared/edge rate limiting for multiple instances,
resource/concurrency limits, monitoring and redacted log retention, backup/restore verification,
image scanning, and a rollback procedure. The free routed model also needs an explicit reliability,
privacy, cost, and quality decision.
