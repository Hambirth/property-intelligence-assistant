# Human acquisition targets

These ten URLs were published in Wasalt's English product sitemap on 2026-08-25. Automated
requests returned Cloudflare HTTP 403 challenges, so no payload has been added here.

Use a normal browser. If a page is publicly visible after normal human interaction, save it with
the exact filename below. Do not use browser automation, proxies, or challenge-solving tools.

| File | Public sitemap URL |
|---|---|
| `001.html` | `https://wasalt.sa/en/property/sale/apartment-with-3-bedrooms-5787065` |
| `002.html` | `https://wasalt.sa/en/property/sale/apartment-with-4-bedrooms-5786882` |
| `003.html` | `https://wasalt.sa/en/property/sale/land-33351-sqm-facing-west-on-15m-width-street-5787055` |
| `004.html` | `https://wasalt.sa/en/property/sale/floor-with-6-bedrooms-5786879` |
| `005.html` | `https://wasalt.sa/en/property/sale/villa-29162-sqm-facing-north-on-12m-width-street-5786931` |
| `006.html` | `https://wasalt.sa/en/property/sale/villa-23873-sqm-facing-south-on-12m-width-street-5786944` |
| `007.html` | `https://wasalt.sa/en/property/sale/apartment-with-5-bedrooms-5786961` |
| `008.html` | `https://wasalt.sa/en/property/sale/apartment-103-sqm-with-3-bedrooms-5786979` |
| `009.html` | `https://wasalt.sa/en/property/sale/land-3125-sqm-facing-east-on-16m-width-street-5786878` |
| `010.html` | `https://wasalt.sa/en/property/sale/apartment-with-3-bedrooms-5786970` |

The provenance sidecars are already present. After saving the pages, run:

```bash
backend/.venv/bin/python -m scripts.import_documents \
  --source wasalt --path data/import/wasalt --dry-run
```

Review `data/processed/wasalt.jsonl`. If any page was an access challenge, the importer will reject
it. Remove `--dry-run` only after the normalized records are correct.
