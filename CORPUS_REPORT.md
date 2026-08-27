# Phase 3.5 Corpus Acquisition Report

Investigation date: 2026-08-25

Current decision: **CORPUS READY FOR PHASE 4**. The verified corpus contains 10 useful DarGlobal
first-party documents and 10 manually saved, sitemap-published Wasalt property pages. Automated
Wasalt requests remained blocked; no challenge was bypassed.

## DarGlobal

### Sitemap classification

`https://darglobal.co.uk/sitemap.xml` returned HTTP 200 `application/xml` and contained 465
entries representing 462 unique URLs and three duplicates. Classification deliberately uses URL
structure, sitemap priority, and corroboration from official brochures; uncorroborated root pages
remain unknown.

| Classification | Unique URLs | Basis |
|---|---:|---|
| BLOG | 255 | `/blog` and `/blog/...` |
| PRESS | 70 | `/press` and `/press/...` |
| CORPORATE | 36 | Explicit company, investor, partner, policy, careers, and service routes |
| OTHER | 54 | Insights, filtered project/category routes, payment, campaign, and utility pages |
| PROJECT | 21 | 12 explicit nested project-family routes plus 9 root routes corroborated by official project brochures |
| UNKNOWN | 26 | High-priority root routes whose type cannot be proven from sitemap metadata alone |

The sitemap exposes only `loc`, `lastmod`, `changefreq`, and `priority`. It has no schema type,
description, JSON-LD, or image metadata that reliably distinguishes every project from campaigns.

### Sampled sitemap URLs

DarGlobal robots permits these paths. HTTP 200 does not mean usable content here: each response
body was the same 212-byte Imperva interstitial.

| URL | Robots | HTTP | Content type | Anti-bot | Usable |
|---|---|---:|---|---|---|
| `https://darglobal.co.uk/dg1` | allowed | 200 | `text/html` | Imperva | no |
| `https://darglobal.co.uk/urban-oasis-by-missoni` | allowed | 200 | `text/html` | Imperva | no |
| `https://darglobal.co.uk/projects/properties-in-uae` | allowed | 200 | `text/html` | Imperva | no |
| `https://darglobal.co.uk/blog/luxury-real-estate-investment-2026` | explicitly allowed family | 200 | `text/html` | Imperva | no |
| `https://darglobal.co.uk/press` | explicitly allowed | 200 | `text/html` | Imperva | no |

Because no sampled HTML page was accessible, no JSON-LD, Open Graph, hydration data, or embedded
application JSON was extracted. The interstitial contains no useful project metadata.

### Accessible first-party resources

The official `cdn.darglobal.co.uk` host has no `robots.txt` object (HTTP 404) and delivered the
following explicitly selected, search-indexed files as ordinary HTTP 200 `application/pdf` without
authentication, redirects, challenges, or browser impersonation:

| Document | Official URL | Result |
|---|---|---|
| Altara at Rayana | `https://cdn.darglobal.co.uk/DG_Altara_Brochure_EN_e957a8caa7.pdf` | imported |
| Marriott Residences AIDA | `https://cdn.darglobal.co.uk/DG_Aida_Marriott_brochure_2_compressed_4497cdebe8.pdf` | imported |
| Neptune by Mouawad | `https://cdn.darglobal.co.uk/D_Gx_Mouawad_Neptune_Brochure_EN_d2730ddcf4.pdf` | imported |
| The Great Escape | `https://cdn.darglobal.co.uk/The_Great_Escape_1_ENG_5f8a045ef2.pdf` | imported |
| The Astera | `https://cdn.darglobal.co.uk/DG_AM_The_Astera_Brochure_EN_1_dce26e7ab3.pdf` | imported |
| W Residences Dubai | `https://cdn.darglobal.co.uk/W_Residences_Dubai_Downtown_Brochure_7b7a9f3695.pdf` | imported |
| Tierra Viva | `https://cdn.darglobal.co.uk/Tierra_Viva_Brochure_Digital_EN_adf1e03fad.pdf` | imported |
| Urban Oasis | `https://cdn.darglobal.co.uk/D_Gx_Missoni_Urban_Oasis_Brochure_EN_1f989fdd33.pdf` | imported |
| Marea | `https://cdn.darglobal.co.uk/DG_MAREA_Brochure_16e8e199f3.pdf` | imported |
| Les Vagues | `https://cdn.darglobal.co.uk/Les_Vagues_Brochure_8dba79dcdc.pdf` | imported |

These are first-party, DarGlobal-branded brochures covering Saudi Arabia, Oman, UAE, Spain, and
Qatar. The PDFs are curated public downloads, not anti-bot page captures. They are recorded as
`MANUAL_PUBLIC_IMPORT`, not `AUTOMATED_SCRAPE`.

### Corpus result and quality

- Usable documents: **10**
- Automated scrape: **0**
- Curated public/offline import: **10**
- Unique titles: 10
- Unique SHA-256 content hashes: 10
- Normalized text length after repeated-boilerplate cleanup: 8,127-24,426 characters
- Missing canonical URLs: 0
- Anti-bot content: 0
- Unicode replacement characters after cleanup: 0
- Detected email addresses or phone-like strings: 0

Representative types are official project brochures, branded-residence brochures, community and
location descriptions, amenities, property types, floor-plan labels, and investment/location facts.

## Wasalt

### Sitemap structure

Wasalt robots declares ten first-party sitemaps on `cdn.wasalt.sa`. Every inspected sitemap is a
plain `urlset` containing only `loc`, `lastmod`, and `priority`; none contains prices, descriptions,
locations, developer metadata, JSON-LD, or downloadable brochures.

| English sitemap | Entries inspected | Route type |
|---|---:|---|
| `static_sitemap_en_sa.xml.gz` | 9 | policy/business static pages |
| `product_sitemap_en_sa.xml.gz` | 45,811 | `/en/property/sale/...` details |
| `category_sitemap_en_sa.xml.gz` | 6,138 | aggregate geographic/category pages |
| `rental_pdp_sitemap_en_sa.xml.gz` | 2,685 | `/en/dailyrental/...` details |
| `rental_srp_sitemap_en_sa.xml.gz` | 5,054 | aggregate daily-rental location pages |

Arabic equivalents exist for each corresponding inventory. There is no separate project,
developer, location-metadata, content, PDF, RSS, or Atom inventory. `/search` remains disallowed
and was never requested.

### Sampled sitemap URLs

| URL | Robots | HTTP | Content type | Anti-bot | Usable |
|---|---|---:|---|---|---|
| `https://wasalt.sa/en/property/sale/apartment-with-3-bedrooms-5787065` | allowed | 403 | `text/html` | Cloudflare challenge | no |
| `https://wasalt.sa/en/property/sale/villa-29162-sqm-facing-north-on-12m-width-street-5786931` | allowed | 403 | `text/html` | Cloudflare challenge | no |
| `https://wasalt.sa/en/dailyrental/apartment-for-booking-5577` | allowed | 403 | `text/html` | Cloudflare challenge | no |
| `https://wasalt.sa/en/s/privacy-policy` | allowed | 403 | `text/html` | Cloudflare challenge | no |
| `https://wasalt.sa/llms.txt` | allowed | 403 | `text/html` | Cloudflare challenge | no |

After these representative failures, normal page scraping stopped. Search-engine cached text was
not imported and no undocumented API was investigated or called.

### Alternative first-party resources

- The sitemap CDN is accessible but contains URL inventory only.
- `llms.txt` is declared by robots but returns a Cloudflare challenge.
- An official Ministry of Tourism license PDF exists on the CDN but contains no useful property
  corpus information and was excluded.
- No useful official project/property PDFs, RSS feeds, Atom feeds, or content-rich static assets
  were found through first-party domain searches.

### Corpus result

- Usable documents: **10**
- Automated scrape: **0**
- Manual public import: **10**
- Unique canonical URLs: **10**
- Unique SHA-256 content hashes: **10**
- Unique titles: **9** (two distinct listings legitimately share the title
  `Apartment with 3 Bedrooms`)
- Normalized text length: **350-866 characters**
- Missing property descriptions, locations, types, prices, currencies, or references: **0**
- Challenge, error, cookie-only, or navigation-only records: **0**
- Unicode replacement characters: **0**

The ten source URLs and provenance sidecars are listed in `data/import/wasalt/README.md`. The pages
were opened and saved through a normal browser, then processed by the offline importer. The saved
HTML embeds the public target-property record in Next.js page data; normalization extracts only
that record and excludes headers, similar-property carousels, location-link navigation, and
browser companion assets.

## Provenance

Every persisted DarGlobal and Wasalt document stores:

- Original first-party source URL.
- Canonical first-party URL.
- `acquisition_method: MANUAL_PUBLIC_IMPORT`.
- Its original source format (`pdf` for DarGlobal and `html` for Wasalt).
- Source-specific project or property metadata.
- Normalized extracted text and SHA-256 content hash.

Automated HTML adapters continue to default to `AUTOMATED_SCRAPE`. The offline importer performs
no network requests and cannot accept arbitrary source domains.

## Compliance

- No CAPTCHA, Cloudflare, or Imperva challenge was solved or bypassed.
- No browser identity was spoofed and no stealth browser was used.
- No proxy rotation, private API, authenticated page, or `/search` path was used.
- Sitemap requests and sample requests used a descriptive crawler identity with conservative
  volume.
- DarGlobal PDFs were ordinary public first-party downloads selected individually.
- Wasalt challenge responses were rejected and never stored as content. Only normally
  browser-visible public property pages supplied manually were imported.
- Raw imported payloads and generated JSONL are ignored by Git.

## Database integration

A dedicated local PostgreSQL 15 database named `property_intelligence_phase35` was created. The
pgvector 0.8.6 extension and all three Alembic revisions through `20260825_0003` applied
successfully.

First DarGlobal import:

```text
discovered 10, parsed 10, inserted 10, updated 0, unchanged 0, rejected 0, failed 0
```

Second identical baseline import:

```text
discovered 10, parsed 10, inserted 0, updated 0, unchanged 10, rejected 0, failed 0
```

Direct verification found 10 DarGlobal rows, all marked `MANUAL_PUBLIC_IMPORT`, and zero rows with
short content, missing canonicals, or challenge text. The two generated JSONL files were byte-for-
byte identical. A final quality pass removed long disclaimer lines repeated across at least 20% of
brochure pages; it updated 8 documents and left 2 unchanged. A subsequent repository pass against
the final normalized JSONL reported all 10 documents unchanged, and a serializer round trip was
again byte-identical.

Verified Wasalt dry run:

```text
discovered 10, parsed 10, inserted 0, updated 0, unchanged 0, rejected 0, failed 0
```

First Wasalt PostgreSQL import:

```text
discovered 10, parsed 10, inserted 10, updated 0, unchanged 0, rejected 0, failed 0
```

Second identical Wasalt import:

```text
discovered 10, parsed 10, inserted 0, updated 0, unchanged 10, rejected 0, failed 0
```

Final PostgreSQL verification found **10 DarGlobal rows, 10 Wasalt rows, and 20 total rows**. All
10 Wasalt rows have `acquisition_method: MANUAL_PUBLIC_IMPORT`, matching source-document URLs,
distinct canonical URLs, and distinct content hashes.

## Phase 4 derived vector corpus

Migration `20260825_0003` creates `document_chunks.embedding` as `vector(384)`. The selected local
model is `BAAI/bge-small-en-v1.5`; embeddings are normalized and retrieval uses exact cosine
distance. No HNSW or IVFFlat index is present because the corpus contains only 212 chunks.

First vectorization:

```text
documents seen 20, processed 20, unchanged 0, chunks inserted 212, failures 0
model load 12.876837 s, vectorization 16.903080 s
```

Identical repeat:

```text
documents seen 20, processed 0, unchanged 20, chunks inserted 0, chunks replaced 0,
chunks skipped 212, failures 0, vectorization 0.181576 s
```

Final storage statistics:

- DarGlobal chunks: **202**
- Wasalt chunks: **10**
- Total chunks: **212** across all 20 documents
- Average content length: **845.43 characters** (minimum 163, maximum 900)
- Missing canonical, chunk-index, parent-hash, or pipeline-fingerprint metadata: **0**

The 19-query PostgreSQL evaluation contains 17 answerable questions and two explicit no-answer
traps. Actual macro metrics are Recall@1 **0.9706**, Recall@3 **0.9706**, Recall@5 **0.9706**, and
MRR **1.0000**. Every answerable query returns at least one relevant document at rank one. The
cross-source query defines two relevant documents and retrieves one of those two in the top five,
which accounts for the fractional macro recall. The no-answer cases are not counted as successful
retrievals; their average top similarity is recorded separately as **0.5760**.
After one unmeasured warm-up query, end-to-end query embedding plus exact PostgreSQL retrieval
averaged **32.737 ms** with **39.836 ms p95** on this machine.

## Manual acquisition completed

The ten Wasalt pages were saved as `001.html` through `010.html`. The initial audit detected that
`005.html` and `006.html` were reversed relative to their provenance sidecars; the files were
corrected, and canonical-to-sidecar validation now prevents this class of mismatch. Browser
companion directories such as `001_files/` are explicitly excluded from import discovery.

## Limitations

- DarGlobal HTML content remains inaccessible to the compliant client.
- PDF text extraction preserves source wording but does not reproduce visual layouts or reliably
  convert every floor plan into structured room metadata.
- Wasalt data is a verified ten-page public snapshot, not a complete copy of Wasalt inventory.
- The current database is a dedicated local validation database, not a production deployment.

**CORPUS VECTORIZED AND READY FOR PHASE 5**
