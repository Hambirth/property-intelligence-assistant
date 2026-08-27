# Deployment validation and runbook

This runbook describes the intended production topology and the controlled bootstrap required before
the public API receives traffic:

```text
Browser -> Next.js frontend -> FastAPI backend -> PostgreSQL/pgvector
                                      |
                                      +----------> OpenRouter
```

The public web process never scrapes, imports, or vectorizes source websites at startup. Migrations
and corpus bootstrap are operator jobs. The application image contains code only.

## Validated container contract

- `backend/Dockerfile` and `frontend/Dockerfile` are multi-stage builds with digest-pinned base
  images. Refresh the pins deliberately after reviewing upstream release notes and scans.
- The backend installs the official CPU-only PyTorch wheel. A default PyPI resolution can pull
  multi-gigabyte CUDA dependencies that this CPU service cannot use.
- Backend and frontend runtime identities are fixed non-root UID/GID `10001`.
- The backend starts one Uvicorn worker with proxy rewriting disabled. The frontend starts the
  Next.js standalone server. Both implement container health checks.
- `.dockerignore` files exclude Git metadata, `.env*`, raw/processed corpus data, caches, tests,
  coverage, local databases, logs, virtual environments, `node_modules`, and Next.js build output.
- Compose binds all host ports to loopback and is for local deployment validation. Its backend
  command applies migrations because there is one local replica. A real deployment runs migrations
  once in a controlled release job, then uses the Dockerfile `CMD` unchanged.

The Phase 9 validation host built Linux/arm64 images. Production is expected to use Linux/amd64, so
CI or the selected platform must also build and scan that architecture before release.

## Runtime resources and model cache

`BAAI/bge-small-en-v1.5` is pinned in code to Hugging Face revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` and loaded once per Python process. The revision is
part of the chunk pipeline fingerprint, so a reviewed future model update causes deterministic
replacement instead of silently mixing vectors. The measured native cold-process peak
was 437,862,400 bytes (about 418 MiB). The warmed container used about 451 MiB at idle, and corpus
processing exceeded that value. These numbers do not include a safe allowance for allocator
fragmentation, concurrent requests, temporary tensors, database buffers, or platform overhead.

- Recommended backend allocation: **2 GiB RAM, one Uvicorn worker, at least 1 CPU**.
- Hard validation floor: **1 GiB** for one worker under controlled load; monitor RSS and OOM events.
- Rejected configuration: **512 MiB**. It has no credible operational headroom.
- Do not add Uvicorn workers without multiplying the memory budget; each process loads its own model.

The container cache path is `/app/.cache/huggingface`. Compose uses a named volume so a recreated
container does not redownload the model. A hosted persistent disk is optional: it improves cold
starts after the first download but can constrain scaling and zero-downtime deploys. If no disk is
used, allow outbound HTTPS to Hugging Face and expect each fresh instance to download the pinned
model revision before warm retrieval is available. Do not copy a developer model cache into the
image.

## Hosting recommendation

The current assignment-sized recommendation is:

- **Frontend: Vercel Pro for a public production deployment; Hobby only for a non-commercial demo.**
  The [current pricing page](https://vercel.com/pricing) lists Hobby at $0 and Pro at $20/month plus
  usage (with an included usage credit). It has native Next.js builds, managed HTTPS, and build-time
  `NEXT_PUBLIC_*` configuration. Verify the final API origin before building because public
  variables are compiled into browser assets.
- **Backend: Render Standard-class web service with 2 GiB RAM, one instance, and health path
  `/health`.** [Render's current compute table](https://render.com/docs/compute-plans) gives Free and
  Starter 512 MiB but Standard 2 GiB. A small persistent disk mounted at
  `/app/.cache/huggingface` is useful for this single-instance assignment, but
  [paid disks](https://render.com/docs/disks) prevent multi-instance scaling and zero-downtime
  deploys; a stateless service with accepted model redownload time is the alternative. Confirm the
  current regional Standard and disk prices in the Render dashboard.
- **Database: Neon PostgreSQL with pgvector.** The corpus is tiny (20 documents and 212 vectors), so
  the free tier can support deployment validation. [Neon's current pricing](https://neon.com/pricing)
  lists Free at 100 CU-hours/project/month and 0.5 GB, and usage-based Launch at $0.106/CU-hour plus
  $0.35/GB-month. Use Launch for a continuously public service that needs stronger operational
  capacity and support. Use an SSL connection, a direct endpoint for migrations, and a pooled
  endpoint for the runtime when compatible with the driver.

Render [free services](https://render.com/docs/free) spin down and have an ephemeral filesystem;
they are unsuitable for this
backend's memory and cold-start profile. Neon free compute can scale to zero, so the first readiness
or query after inactivity can include a database wake-up. Prices and free-tier terms change: confirm
them in the provider consoles before spending or launching.

## Production environment contract

Set these in the platform configuration system. Never place secret values in Git, Docker build
arguments, `NEXT_PUBLIC_*`, logs, or screenshots.

Required backend runtime names:

```text
APP_ENV
DATABASE_URL
FRONTEND_URL
OPENROUTER_API_KEY
OPENROUTER_MODEL
EMBEDDING_MODEL
EMBEDDING_PRELOAD
ALLOW_LOCALHOST_ORIGINS
```

Reviewed backend tuning names:

```text
APP_NAME
LOG_LEVEL
DATABASE_POOL_SIZE
DATABASE_MAX_OVERFLOW
DATABASE_POOL_RECYCLE_SECONDS
OPENROUTER_BASE_URL
OPENROUTER_TIMEOUT_SECONDS
OPENROUTER_MAX_RETRIES
EMBEDDING_BATCH_SIZE
RAG_CHUNK_TARGET_CHARS
RAG_CHUNK_OVERLAP_CHARS
RAG_CHUNK_MIN_CHARS
RAG_TOP_K
RAG_SIMILARITY_THRESHOLD
RAG_CONTEXT_MAX_CHUNKS
RAG_CONTEXT_MAX_CHARS
MAX_CHAT_MESSAGE_LENGTH
MAX_CHAT_BODY_BYTES
REQUEST_TIMEOUT_SECONDS
CHAT_RATE_LIMIT_REQUESTS
CHAT_RATE_LIMIT_WINDOW_SECONDS
TRUSTED_PROXY_IPS
```

Frontend build-time names:

```text
NEXT_PUBLIC_API_BASE_URL
NEXT_PUBLIC_SITE_URL
```

Private bootstrap-only names used by the release job, not the web service:

```text
CORPUS_BUNDLE_URL
CORPUS_BUNDLE_SHA256
CORPUS_VERSION
```

Set `APP_ENV=production`, `ALLOW_LOCALHOST_ORIGINS=false`, exact HTTPS `FRONTEND_URL` origins, and
the fixed `EMBEDDING_MODEL=BAAI/bge-small-en-v1.5`. `DATABASE_URL` must use the
`postgresql+asyncpg://` SQLAlchemy scheme and provider-required TLS settings. Keep
`OPENROUTER_API_KEY` only in the backend. Scraper variables are intentionally absent from the public
runtime contract because deployment startup does not scrape.

## Private corpus release artifact

Raw/manual source files and generated JSONL are intentionally excluded from Git and images. The
verified corpus reaches production as an immutable, versioned private artifact:

1. On an authorized release workstation, create the deterministic bundle:

   ```bash
   backend/.venv/bin/python scripts/corpus_bundle.py create \
     --input data/import \
     --output /secure-staging/property-corpus-<version>.tar.gz \
     --version <version>
   ```

2. Create it a second time and require identical SHA-256 digests. Record the digest as release
   metadata, not as an unreviewed mutable `latest` pointer.
3. Upload it over TLS to private object storage with encryption at rest, object versioning, audit
   logs, and least-privilege read access for only the bootstrap job. Do not grant the public web
   service permanent corpus-storage credentials. A short-lived workload identity or presigned URL
   is preferred.
4. The bootstrap job downloads the exact object to ephemeral storage, checks the recorded outer
   `CORPUS_BUNDLE_SHA256`, and then verifies its internal manifest and every file hash:

   ```bash
   sha256sum property-corpus.tar.gz
   backend/.venv/bin/python scripts/corpus_bundle.py verify \
     --bundle property-corpus.tar.gz \
     --extract /bootstrap/corpus
   ```

The bundle contains exactly ten DarGlobal PDFs, ten Wasalt HTML pages, and twenty provenance
sidecars. It excludes browser companion assets and embeds the expected 20-document/212-chunk
contract. `scripts/corpus_bundle.py` rejects symlinks, traversal, unexpected members, oversized
members, and hash mismatches.

## Ordered production deployment

1. Provision PostgreSQL with encrypted transport/storage, backups, a restricted runtime role, and a
   separately controlled migration role where practical.
2. Confirm `CREATE EXTENSION vector` is permitted; migration `20260825_0001` creates it. On a managed
   service where only an administrative role may create extensions, enable pgvector first.
3. Build Linux/amd64 backend and frontend images from the reviewed commit. Scan them, record immutable
   image digests, and build the frontend with the final HTTPS API/site origins.
4. Run `alembic upgrade head` once using the backend image and migration database endpoint. Do not
   run it concurrently in every replica.
5. Download and verify the exact private corpus artifact as described above. Mount the extracted
   directory read-only into a trusted one-off backend job.
6. Dry-run both imports, then persist them:

   ```bash
   python -m app.importing.cli --source darglobal --path /bootstrap/corpus/darglobal \
     --output /tmp/darglobal.jsonl --dry-run
   python -m app.importing.cli --source wasalt --path /bootstrap/corpus/wasalt \
     --output /tmp/wasalt.jsonl --dry-run
   python -m app.importing.cli --source darglobal --path /bootstrap/corpus/darglobal \
     --output /tmp/darglobal.jsonl
   python -m app.importing.cli --source wasalt --path /bootstrap/corpus/wasalt \
     --output /tmp/wasalt.jsonl
   ```

7. Build the derived vectors and verify the database contract:

   ```bash
   python -m app.rag.cli
   python -m app.deployment.verify
   ```

8. Repeat both real imports and `python -m app.rag.cli`. Require 20 unchanged documents, 212 skipped
   chunks, and `"ready": true` from the verifier. Delete the extracted corpus and job credentials.
9. Start one backend instance from the Dockerfile `CMD`; set `/health` as the platform liveness/deploy
   check and use `/ready` for database-aware traffic readiness. Then deploy the frontend.
10. Verify HTTPS, CORS, CSP, hidden production docs, database counts, source provenance, one grounded
    question per source, one cross-source question, refusal behavior, and graceful SIGTERM shutdown.

## Rollback

Retain the prior application image digests and corpus artifact version. Prefer backward-compatible
migrations. Roll back code by routing to the previous image; roll back data by rerunning the trusted
bootstrap with the known prior artifact and verifying counts/fingerprints. Never scrape as an
automatic rollback or web startup action.
