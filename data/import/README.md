# Manual public document import

Place legitimately obtained public documents under `darglobal/` or `wasalt/`. Raw payloads are
ignored by Git. Each HTML, TXT, or PDF needs a sibling `<filename>.metadata.json` sidecar:

```json
{
  "source": "wasalt",
  "source_url": "https://wasalt.sa/en/property/example",
  "canonical_url": "https://wasalt.sa/en/property/example",
  "title": "Public property title",
  "metadata": {"location": "Riyadh"}
}
```

JSON imports embed the same fields plus `text` in one file and do not need a sidecar. URLs must
belong to the selected first-party source. Imports are always marked `MANUAL_PUBLIC_IMPORT`.

From the repository root:

```bash
backend/.venv/bin/python -m scripts.import_documents \
  --source wasalt --path data/import/wasalt --dry-run
```

The command validates and normalizes the files and writes deterministic JSONL under
`data/processed/`. Remove `--dry-run` only after configuring PostgreSQL and applying migrations.
