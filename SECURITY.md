# Security

This document describes controls that exist in the application and limitations that still require deployment controls. It is not a claim that prompt injection or model output can be made perfectly safe.

## Trust boundaries and threat model

The system has five important boundaries:

1. an unauthenticated public browser sends one question to the FastAPI chat API;
2. the API embeds the question and retrieves PostgreSQL content;
3. retrieved public documents are supplied to OpenRouter as untrusted context;
4. OpenRouter returns untrusted structured output which is validated before release;
5. an operator-only CLI imports or scrapes public files into PostgreSQL.

| Threat | Existing mitigation | Remaining limitation |
|---|---|---|
| Malicious or oversized public requests | Chat-only 8 KB body ceiling before JSON parsing, normalized 2,000-character message ceiling, forbidden extra fields, fixed source enum, safe `413`/`422` responses | Public access is unauthenticated by assignment design |
| API abuse and rate-limit bypass | JSON and SSE share one process-local limiter; malformed requests consume budget; socket peer is authoritative unless the exact proxy IP is trusted; forwarded IPs must parse | Multiple processes or instances need Redis, an API gateway, reverse-proxy limiter, or edge/WAF enforcement |
| User prompt injection | Fixed system prompt plus deterministic refusals for common override, secret extraction, and fabrication requests | Pattern matching cannot cover every phrasing; model and output validation remain required |
| Retrieved-content injection | Retrieved text is escaped, size bounded, labelled untrusted, and placed in the user message rather than the system message | Source content can still influence a model semantically; ongoing adversarial evaluation is required |
| Hallucinations and fake citations | Evidence threshold, comparison gate, bounded context, backend-assigned IDs, required citations, unknown-ID rejection, model-URL rejection, backend URL mapping | Validation does not prove sentence-level entailment or resolve every contradictory source |
| XSS and unsafe Markdown | Model output becomes React text/elements only; no `dangerouslySetInnerHTML`; raw HTML and Markdown links remain inert; adversarial rendering is tested | Future Markdown features must preserve raw-HTML and URL restrictions |
| Malicious citation URLs | Backend permits only HTTPS DarGlobal/Wasalt hosts; frontend independently validates scheme, credentials, host, lengths, and organization | An allowlisted upstream domain compromise remains possible |
| SSE/provider compromise | Browser requires `text/event-stream`, validates the final schema and citations, and enforces 256 KB stream/128 KB event limits | Structured SSE is completion streaming, not token streaming |
| SSRF | Public chat cannot provide URLs; crawler/import hosts are fixed; HTTPS-only URLs reject credentials, IP literals, unusual ports, and cross-host redirects | Operator configuration such as the OpenRouter base URL remains trusted deployment input |
| SQL injection | Public values enter typed SQLAlchemy expressions; source is an enum; vectors and filters are bound; there is no public raw-query endpoint | Database/library vulnerabilities still require patch management |
| Secret leakage | `.env` is ignored, only `NEXT_PUBLIC_*` values enter browser code, provider errors are categorized, logs redact common secret shapes and omit questions/context | Operators must protect deployment secrets, CI logs, backups, and platform access |
| Verbose failures | Generic structured errors and request IDs; provider bodies and transport details never reach users; stream failures become safe events | Server logs contain stack traces and need restricted access and retention |
| Malicious imports | Operator-only CLI; fixed file types; non-symlink files; 50 MB files, 64 KB sidecars, 300 PDF pages, 10 million extracted characters; strict provenance/source/host checks | Parsing untrusted PDF/HTML remains a CPU/memory risk and should run separately from the public API |
| Dependency compromise | Exact application pins, multi-stage non-root containers, dependency audits, ignored caches/build output | Base images and transitive dependencies still need recurring scanning and update policy |
| Container or proxy mistakes | Non-root users, loopback Compose ports, no baked `.env`, Uvicorn proxy rewriting disabled, explicit trusted proxies | TLS, WAF, network policy, resource limits, and secret injection belong to the platform |
| Denial of service | Bounded input/context/output, timeouts, bounded retries, DB pool limits, scraper limits, rate limiting | CPU embeddings and unauthenticated traffic still need platform concurrency/resource controls |

## Public API controls

`POST /api/chat` and `POST /api/chat/stream` accept only `message` and an optional `darglobal`/`wasalt` source. Unknown fields, invalid JSON, invalid sources, empty messages, oversized messages, and oversized bodies receive generic errors without reflecting input.

The limiter executes before body buffering and JSON parsing, so switching between JSON and SSE or sending malformed JSON does not create a second budget. Untrusted `X-Forwarded-For` values are ignored. When the socket peer is explicitly trusted, the application walks the validated chain from right to left and selects the first untrusted hop. Uvicorn runs with `--no-proxy-headers` so its defaults cannot rewrite the peer first.

The limiter is intentionally process-local. A horizontally scaled deployment must enforce a shared limit with Redis, the API gateway, reverse proxy, or edge/WAF. Production should also consider authenticated tenant/user quotas and provider-cost budgets.

JSON service failures become safe `502`, `503`, or `504` envelopes. SSE emits a request-ID start event followed by either one validated completion or a safe error event. Cancellation is propagated. Database and embedding failures do not expose exception messages.

## Grounding and prompt injection

The user question and every retrieved document are untrusted. Retrieved content is escaped and wrapped in a labelled untrusted context block inside a user-role message. It never enters the fixed system message or executes as code.

The evidence gate refuses low-confidence results before provider use and requires at least two documents for comparisons. Context is deduplicated and capped at six chunks and 7,000 characters. The provider may cite only IDs assigned to that exact context. Unknown IDs, missing citations, citations on refusals, malformed responses, and model-generated HTTP(S) URLs are rejected. Final titles, organizations, and canonical URLs come from validated backend metadata.

Direct requests to ignore instructions, reveal prompts/credentials, fabricate facts, or invent prices are refused before retrieval. This is defense in depth and not a complete natural-language firewall. The calibrated dataset is small, and automatic checks cannot prove every generated sentence is supported. Higher-stakes property decisions require review of the original cited material.

## Browser and frontend security

Model output is rendered through React as paragraphs, headings, lists, emphasis, and inline code. Raw HTML is never interpreted, Markdown links are not activated, and `dangerouslySetInnerHTML` is not used. Backend citations are independently checked in the browser for expected shape, size, organization, HTTPS scheme, no URL credentials, and exact DarGlobal/Wasalt host allowlists. External links use `target="_blank"` with `rel="noopener noreferrer"`.

The frontend applies a Content Security Policy with `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, a configured API `connect-src`, and restricted image/font sources. It also sets `nosniff`, `DENY` frame protection, strict-origin referrers, same-origin opener policy, and a permissions policy disabling camera, microphone, and geolocation. `unsafe-inline` remains for Next.js bootstrap/styles; development alone also permits `unsafe-eval` for tooling.

The SSE client requires the correct media type, caps total/event bytes, and validates the final response before rendering. It does not display server-provided error messages.

## Secrets and logging

`OPENROUTER_API_KEY` is read only by the backend. `.env` and `.env.*` are ignored except for the placeholder-only `.env.example`. Raw/manual corpus payloads, generated JSONL, model caches, virtual environments, dependency folders, build output, logs, local databases, and temporary files are also ignored.

Only variables prefixed `NEXT_PUBLIC_` can enter browser code, and these contain public origins only. Application logs contain request IDs, status, timings, retrieval counts, similarity aggregates, and model names. They intentionally exclude questions, prompts, retrieved text, provider response bodies, authorization headers, keys, and database passwords. Logs must still be treated as operationally sensitive.

## Database security

SQLAlchemy's async engine uses bounded pools, pre-ping, and session-scoped transactions. Repository queries use typed expressions and bound values. Document/chunk relationships use a database foreign key with `ON DELETE CASCADE`; chunk replacement is transactional. Source checks, canonical uniqueness, content hashes, and supporting indexes are enforced in schema/model definitions.

The production runtime database role should not be a PostgreSQL superuser and should receive only the schema/table privileges required at runtime. In a larger deployment, run Alembic with a separate migration role capable of DDL and keep those credentials out of the application process. Extension creation may require a one-time privileged platform operation.

## Import and scraper security

Import is a local operator action, not an HTTP endpoint. It does not accept a remote URL option. Discovery permits only `.html`, `.htm`, `.txt`, `.json`, and `.pdf`, ignores browser companion directories and provenance sidecars, and rejects symlinks. Every source/canonical URL must be HTTPS on the fixed source host allowlist with no embedded credentials, IP literals, or unusual ports. Sidecars forbid unknown fields and must identify the requested source. Wasalt saves require structured page data and a matching canonical URL. Content validation rejects anti-bot, error, cookie-only, and navigation-only material.

The network scraper is separate, robots-aware, non-recursive, allowlisted, redirect-checked, content-type checked, byte bounded, delayed, concurrency bounded, and retry bounded. Production should schedule parsing/vectorization in a worker/container with CPU and memory limits rather than in the public API process.

## Privacy

Chat history is held only in browser React memory for the current page session. It is not written to PostgreSQL, cookies, localStorage, analytics, or tracking services, and it is not sent back as model history. The backend does not log the question.

For an answerable request, OpenRouter necessarily receives the user's current question and the bounded retrieved context required to generate the answer. It does not receive prior chat turns, database credentials, the OpenRouter key, or unrelated corpus documents. Deployment owners must review OpenRouter's data handling, retention, region, and model-provider policies before production.

## Production configuration decisions

- `/docs`, `/redoc`, and `/openapi.json` are disabled when `APP_ENV=production`.
- Production OpenRouter transport must use HTTPS.
- Production CORS rejects localhost/loopback origins unless `ALLOW_LOCALHOST_ORIGINS=true` is explicitly set for an unusual local validation.
- `/health` is process-only and never loads the embedding model or checks dependencies.
- `/ready` checks PostgreSQL only; OpenRouter outages return safe chat failures rather than restarting healthy instances.
- `EMBEDDING_PRELOAD=true` starts one best-effort background warm-up. A process-safe cache lock avoids duplicate in-process model instances. Warm-up failure does not fail startup, and lazy loading stays available.

## Dependency audit status

Phase 8 found known issues in the previous `pypdf` pin and the local packaging tool. The application now pins `pypdf==6.15.0`; the container builder pins `pip==26.2.1`. The follow-up `pip-audit` and production `npm audit --omit=dev` scans reported no known vulnerabilities. The local project package itself is skipped by `pip-audit` because it is not published on PyPI.

## Remaining risks

- The endpoint is public and unauthenticated; production exposure needs edge controls and an explicit abuse/cost policy.
- Process-local limits do not coordinate across workers or instances.
- Prompt-injection and grounding controls reduce, but cannot eliminate, semantic model risk.
- The free OpenRouter alias can change model, latency, availability, privacy behavior, and quality. Reliability-sensitive deployment should pin and evaluate a model or controlled fallback.
- Source freshness is not continuous, and contradictory or stale public information may exist.
- Exact pgvector search is appropriate for the small corpus; larger corpora require measured indexing and capacity work.
- CSP permits inline script/style required by the current Next.js output. A nonce/hash design can tighten this if justified.
- Platform TLS, WAF, distributed quotas, resource limits, backups, monitoring, log retention, secret rotation, image scanning, and rollback are deployment responsibilities.
