# Ingestion — async worker subsystem

Discovers CSE annual reports, downloads them, and runs the
parse → chunk → enrich → index pipeline. Runs as a **separate worker** consuming
jobs from Redis, so heavy PDF parsing never blocks the API.

## Modules

| File | Responsibility |
|------|----------------|
| `worker.py` | Job loop — consumes Redis jobs (`index_report`, `index_company`, `discover`) |
| `pipeline.py` | `index_one_report()` — download → Docling parse → gate → chunk → enrich → index |
| `discover.py` | Catalogue discovery — records every available annual report as `AVAILABLE` |
| `cse_client.py` | CSE financials API + PDF download |
| `jobqueue.py` | Two priority Redis queues (index > discover) |
| `infra.py` | Postgres + MinIO helpers |
| `load_companies.py` | Load the company master CSV into Postgres |
| `orchestrate.py` | CLI bulk orchestrator (alternative to UI-driven indexing) |
| `schema.sql` | `companies` + `documents` tables |

## The ingestion lifecycle

```
discover   →  Postgres marks each report AVAILABLE   (metadata only, no download)
enqueue    →  API pushes index job to Redis
worker     →  PROCESSING → download (MinIO) → Docling parse → quality gate
              → section-aware chunk → LLM table summaries → embed → OpenSearch
              → INDEXED
```

Status transitions are tracked per report in Postgres:
`AVAILABLE → QUEUED → PROCESSING → INDEXED` (or `FLAGGED` / `FAILED`).

## Design decisions

**Discovery is separate from indexing.** Discovery is a cheap metadata pass
recording the *full universe* of available reports (the coverage view). Indexing
is the expensive part and is on-demand — you don't pre-index thousands of PDFs,
you index what you actually want, when you want it.

**Two priority queues.** On-demand index jobs use a high-priority queue; a full
catalogue refresh uses a low-priority one. The worker drains index jobs first, so
clicking "Index" never waits behind a multi-minute discovery pass.

**Quality gate before chunking.** After parsing, a gate flags scanned/empty/
table-less documents (`low_text`, `no_tables`, etc.) rather than silently
indexing hollow content that would poison retrieval.

**Docling for parsing.** Layout-aware, with TableFormer for table structure —
essential for financial statements where the data lives in tables. The worker
image uses CPU-only torch + headless OpenCV (no GPU/display libs).

**Idempotent + resumable.** State lives in Postgres; a crash mid-run is resumed
by re-running. Re-discovery never downgrades already-indexed reports.

## Run

Built as the `worker` service in the root compose. First load companies, then
discover, then index on demand (or from the UI):

```bash
docker compose exec worker python load_companies.py /app/data/CompanyList.csv
curl -X POST localhost:8000/catalog/refresh -d '{"limit": 10}'
curl -X POST localhost:8000/index/report -d '{"doc_id": "OSEA_2024"}'
```
