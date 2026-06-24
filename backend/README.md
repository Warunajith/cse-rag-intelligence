# Backend — FastAPI serving layer

Stateless query-serving API. Handles retrieval, answer generation, and the
catalogue/indexing endpoints. Reads from OpenSearch (retrieval) and Postgres
(catalogue state); enqueues ingestion jobs to Redis.

## Modules

| File | Responsibility |
|------|----------------|
| `rag_service.py` | Core RAG — hybrid retrieval, reranking, answer generation, `/ask` `/search` `/health` |
| `frontend_api.py` | Frontend support — `/companies`, `/corpus/stats`, and `/ask/stream` (SSE) |
| `catalog_api.py` | Catalogue + indexing — `/catalog/*`, `/index/*`; enqueues jobs to Redis |

The three are composed onto one FastAPI app: `frontend_api.register(...)` and
`catalog_api.register_catalog(app)` are called at the bottom of `rag_service.py`.

## Key endpoints

```
POST /ask                question → grounded answer + sources
POST /ask/stream         same, streamed token-by-token (SSE)
POST /search             hybrid retrieval only (no LLM) — cheap, for debugging
GET  /companies          company list (+ latest indexed report) from Postgres
GET  /catalog/stats      coverage: available vs indexed
POST /catalog/refresh    enqueue a discovery pass (guarded against duplicates)
GET  /catalog/company/{symbol}   per-company report list with statuses
POST /index/report       enqueue indexing for one report {doc_id}
POST /index/company      enqueue all available reports for a company
GET  /health/ready       OpenSearch reachable + index exists
```

## Design decisions

**Hybrid retrieval over pure vector.** Financial questions are full of exact
terms — "EBITDA", "Note 23", "profit after tax". Pure semantic search retrieves
"profit before tax" when asked about "profit after tax"; BM25 catches the exact
match, vectors catch paraphrase. Results are fused with Reciprocal Rank Fusion.

**Table summary + raw-table injection.** A raw markdown table embeds poorly (a
wall of numbers). So each table's *LLM summary* is embedded for retrieval, while
the raw table is stored un-indexed and injected into the LLM context at answer
time — answers come from the actual figures, not a paraphrase.

**Stateless.** No local state; everything comes from OpenSearch / Postgres. Lets
the API scale horizontally and stay responsive — heavy work is offloaded to the
worker via Redis, never run in-process.

**Reranking is optional and fails safe.** If `RERANK_ENABLED=true` but the
reranker service is unreachable, retrieval falls back to RRF order; `/ask` never
breaks.

## Run

Built and run via the root `docker-compose.yml` as the `rag-api` service.
Env vars: `OPENSEARCH_HOST`, `OPENAI_API_KEY`, `PG_*`, `REDIS_URL`,
`RERANK_ENABLED`, `RERANKER_URL`. See `.env.example`.
