# Reranker — cross-encoder reranking service

Optional precision-boosting microservice. Takes the query and the candidate
chunks from hybrid retrieval and re-scores them with a cross-encoder, which reads
query and passage *together* (unlike the bi-encoder used for the initial vector
search). Multi-backend: one uniform API over a local model or hosted providers.

## Backends

Set via `RERANKER_BACKEND`:

| Backend | Notes |
|---------|-------|
| `local` | `BAAI/bge-reranker` family, runs in-container (needs torch — built with `WITH_LOCAL=true`) |
| `jina` | Jina AI reranking API (`JINA_API_KEY`) |
| `cohere` | Cohere Rerank (`COHERE_API_KEY`) |
| `voyage` | Voyage AI rerank (`VOYAGE_API_KEY`) |

Swap by config, no code change. The `WITH_LOCAL` build arg keeps the image lean
when you only use hosted backends (skips the torch install).

## API

```
POST /rerank   { "query": str, "documents": [str, ...], "top_k": int }
            →  { "ranked": [ { "index": int, "score": float }, ... ] }
```

Uniform response shape across all backends — the serving layer doesn't care which
one is active.

## Design decisions

**Two-stage retrieval (retrieve wide, rerank narrow).** Hybrid search retrieves a
generous candidate set quickly; the cross-encoder then re-orders the top
candidates with higher precision. This is the standard precision/recall split —
cheap recall first, expensive precision second, only on the shortlist.

**Optional and isolated.** It's a separate service behind a profile
(`--profile rerank`), and the API degrades gracefully if it's absent. You can run
the whole system without it and add it when measuring its lift.

**Pluggable backends for honest evaluation.** Being able to swap local vs hosted
rerankers by config makes it easy to benchmark them against the eval harness and
choose based on measured quality, not assumption.

## Run

```bash
docker compose --profile rerank up -d --build
# and set RERANK_ENABLED=true so the API actually calls it
```
