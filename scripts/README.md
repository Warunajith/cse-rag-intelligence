# Scripts — standalone pipeline experiments

Single-purpose scripts from building and validating the ingestion pipeline. They
predate the consolidated worker (`ingestion/pipeline.py`) and are kept as
readable references — each isolates one stage, useful for debugging or
understanding how a stage works without the full worker.

> These are reference / experimentation scripts. Production ingestion runs
> through the worker (`ingestion/`), which inlines this proven logic.

## Scripts

| File | Stage it isolates |
|------|-------------------|
| `parse_test.py` | Early PDF parse probe (pdfplumber) |
| `parse_test_docling.py` | Docling parse probe |
| `extract_safe_docling.py` | PDF → structured elements + tables (the chosen approach) |
| `extract_all_docling.py` | Fuller extraction variant |
| `chunk_enrich.py` | Section-aware chunking + LLM table summaries |
| `index_chunks.py` | Embed + bulk index to OpenSearch |
| `search_test.py` | BM25 / kNN / hybrid retrieval tester |
| `ingest.py` | Single-file end-to-end orchestrator (SQLite registry) |

## Why they're kept

They tell the story of how the pipeline was arrived at — the parsing approaches
tried, how chunking and table handling evolved, how retrieval was tested. For a
project meant to be read as well as run, that progression is worth preserving.
For actual ingestion at scale, use the worker.
