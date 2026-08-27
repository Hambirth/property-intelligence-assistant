# Production checklist

Use this as a release gate. The repository's Compose defaults are for loopback development and must
not be copied unchanged into a public environment.

## 1. Release and supply chain

- [ ] Review the proposed Git inventory; do not add `.env`, raw/manual corpus files, processed JSONL,
  model caches, virtual environments, `node_modules`, `.next`, logs, local databases, or temporary
  files.
- [ ] Run a secret scanner against the exact commit and CI artifacts.
- [ ] Run `pip-audit`, `pip check`, and `npm audit --omit=dev`; classify and approve any residual item.
- [ ] Build containers in CI from the reviewed commit and scan application and base-image layers.
- [ ] Record image digests and release/version identifiers for rollback.
- [ ] Build and scan the actual Linux/amd64 deployment images; local Phase 9 builds were Linux/arm64.
- [ ] Refresh digest-pinned Python, Node, and pgvector bases only through a reviewed dependency update.

## 2. Required configuration

Set deployment values through the platform's secret/configuration system, never committed files:

- [ ] `APP_ENV=production`
- [ ] `DATABASE_URL=postgresql+asyncpg://...` for a non-superuser runtime role
- [ ] `FRONTEND_URL=https://<public-frontend-origin>` with no path, query, fragment, or localhost
- [ ] `NEXT_PUBLIC_API_BASE_URL=https://<public-api-origin>`
- [ ] `NEXT_PUBLIC_SITE_URL=https://<public-frontend-origin>`
- [ ] `OPENROUTER_API_KEY` stored as a backend-only secret
- [ ] `OPENROUTER_MODEL` selected and evaluated explicitly; do not accept the free alias accidentally
- [ ] `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1` or another reviewed HTTPS endpoint
- [ ] `EMBEDDING_MODEL=BAAI/bge-small-en-v1.5` and a writable/persistent or pre-populated model cache
- [ ] `EMBEDDING_PRELOAD=true` when one warm model per process fits the memory budget
- [ ] `CHAT_RATE_LIMIT_REQUESTS`, `CHAT_RATE_LIMIT_WINDOW_SECONDS`, provider timeouts, and retry counts
  reviewed against traffic/cost limits
- [ ] `TRUSTED_PROXY_IPS` contains only the exact direct proxy hops; otherwise leave empty
- [ ] `ALLOW_LOCALHOST_ORIGINS=false`
- [ ] `LOG_LEVEL=INFO` or stricter; debug logging disabled

Confirm `MAX_CHAT_BODY_BYTES` remains greater than `MAX_CHAT_MESSAGE_LENGTH` and keep the 8 KB/2,000
defaults unless measured product requirements justify a bounded change.

## 3. Database and migrations

- [ ] Provision PostgreSQL with pgvector, encrypted transport/storage, backups, and restore testing.
- [ ] Use a runtime role that is not a superuser and cannot create extensions or arbitrary schemas.
- [ ] If practical, use a separate short-lived migration role for DDL.
- [ ] Run `alembic upgrade head` once as a controlled release job, not concurrently in every replica.
- [ ] Verify the migration revision, `vector(384)` dimension, constraints, indexes, and cascade behavior.
- [ ] Confirm connection pool size/overflow fits database connection limits across all replicas.

## 4. Corpus ingestion and vectors

- [ ] Create the deterministic private corpus bundle twice and require matching SHA-256 digests.
- [ ] Store the immutable bundle in encrypted, versioned private object storage with audit logs and
  short-lived bootstrap-only access; never use a mutable `latest` object.
- [ ] Verify `CORPUS_BUNDLE_SHA256` and the internal per-file manifest before extraction/import.
- [ ] Obtain source files legitimately and retain provenance sidecars outside the application image.
- [ ] Run import dry-runs and review rejection/challenge/navigation/encoding/duplicate reports.
- [ ] Import with the operator-only CLI from a resource-limited trusted worker, never a public route.
- [ ] Confirm every manual record has `MANUAL_PUBLIC_IMPORT` provenance and an allowlisted HTTPS URL.
- [ ] Run vectorization and verify every document has current chunks/pipeline fingerprints.
- [ ] Re-run import/vectorization to verify idempotency before release.
- [ ] Record DarGlobal, Wasalt, total document, and total chunk counts.
- [ ] Require `python -m app.deployment.verify` to report 10/10/20 documents, 212 chunks, vector(384),
  one pipeline fingerprint, migration head, pgvector, and all-manual provenance.
- [ ] Delete extracted source files and bootstrap credentials before starting public web processes.

## 5. Build and start

- [ ] Build the frontend with the final public API/site origins; public variables are compile-time values.
- [ ] Verify the frontend CSP `connect-src` contains only self and the intended API origin.
- [ ] Start the backend as a non-root user with proxy-header rewriting disabled.
- [ ] Start the frontend as a non-root user.
- [ ] Apply CPU, memory, process, and request concurrency limits.
- [ ] Allocate 2 GiB RAM and one Uvicorn worker; do not deploy this model on a 512 MiB instance.
- [ ] Decide explicitly between a persistent `/app/.cache/huggingface` disk and accepted model
  redownload/cold-start time; never bake a developer cache into the image.
- [ ] Run Alembic once in a controlled release job, then start the backend from its Dockerfile `CMD`.
- [ ] Terminate TLS at the reviewed ingress and force HTTPS/HSTS there after domain validation.
- [ ] Ensure PostgreSQL and management surfaces are not publicly exposed.

## 6. Health and readiness

- [ ] `/health` returns 200 without touching PostgreSQL, OpenRouter, or the embedding model.
- [ ] `/ready` returns 200 only when PostgreSQL responds and returns a generic 503 otherwise.
- [ ] `/docs`, `/redoc`, and `/openapi.json` return 404 in production.
- [ ] An OpenRouter outage produces a safe chat failure without making the process unready.
- [ ] Confirm the embedding preload log appears when enabled; a forced warm-up failure must not crash startup.

## 7. Security and abuse smoke tests

- [ ] CORS allows only the production frontend origin and rejects an unrelated origin.
- [ ] Untrusted `X-Forwarded-For`, malformed forwarding chains, IPv6, and endpoint switching do not
  bypass the effective shared/edge limiter.
- [ ] Oversized bodies return 413; invalid JSON/fields/source return generic 422; excess requests return
  429 with `Retry-After`.
- [ ] Prompt override, system-prompt/key extraction, fabrication, and fake-URL requests safely refuse.
- [ ] Retrieved instructions remain data; missing/unknown citations, URLs, and malformed provider output
  never render.
- [ ] Browser responses include CSP, `nosniff`, frame protection, referrer policy, permissions policy,
  and same-origin opener policy.
- [ ] External citation links are HTTPS, source-allowlisted, new-tab isolated, and backend owned.

## 8. Functional deployment smoke test

- [ ] Ask one DarGlobal question and open its citation.
- [ ] Ask one Wasalt question and open its citation.
- [ ] Run one cross-source comparison and verify citations from both organizations.
- [ ] Ask one unsupported question and confirm a natural refusal with no citations.
- [ ] Run one prompt-injection attempt and confirm refusal/no secret output.
- [ ] Verify desktop and mobile composer, loading, final scroll position, Markdown rendering, and source cards.
- [ ] Check application and browser logs for errors, hydration failures, secrets, or question/context bodies.
- [ ] Record warm retrieval, provider, API overhead, and total latency against the release baseline.

## 9. Operations, privacy, and rollback

- [ ] Document that the current question and bounded retrieved context reach OpenRouter; review provider
  retention, subprocessors, region, and contractual terms.
- [ ] Confirm there are no analytics, tracking cookies, server-side chat persistence, or localStorage history.
- [ ] Configure redacted log access, retention, alerting, dashboards, and provider/DB error budgets.
- [ ] Configure credential rotation, database backup/restore drills, and corpus/version recovery.
- [ ] Define rollback: route traffic to the previous image digest, run only backward-compatible schema
  changes, and restore corpus/vectors from a known version if data—not code—is the cause.
- [ ] Assign an incident owner and document how to disable public chat or rotate the OpenRouter key quickly.

Do not expose the service publicly until every applicable unchecked item has an owner and resolution.
