"""
Reranker Service — standalone cross-encoder reranking microservice
==================================================================
One endpoint, multiple backends, switched by a single env var. The caller sends
{query, documents, top_k} and gets back a reordering — it never knows or cares
which backend scored the pairs. That encapsulation is the whole point.

    POST /rerank   {query, documents: [str], top_k}  ->  {ranked: [{index, score}]}
    GET  /health   backend readiness

Backends (env RERANKER_BACKEND):
    local  -> sentence-transformers CrossEncoder (self-hosted, no API cost)
    cohere -> Cohere Rerank API        (needs COHERE_API_KEY)
    jina   -> Jina Rerank API          (needs JINA_API_KEY)
    voyage -> Voyage Rerank API        (needs VOYAGE_API_KEY)

Heavy deps (torch) load ONLY for the local backend, lazily on first request,
so running in a hosted mode has zero ML overhead even if torch is installed.
"""
import os
import functools
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

BACKEND = os.environ.get("RERANKER_BACKEND", "local").lower()
LOCAL_MODEL = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
COHERE_MODEL = os.environ.get("COHERE_RERANK_MODEL", "rerank-english-v3.0")
JINA_MODEL = os.environ.get("JINA_RERANK_MODEL", "jina-reranker-v2-base-multilingual")
VOYAGE_MODEL = os.environ.get("VOYAGE_RERANK_MODEL", "rerank-2")


# ----------------------------- backends -----------------------------
@functools.lru_cache(maxsize=1)
def _local_model():
    from sentence_transformers import CrossEncoder
    print(f"[reranker] loading local model {LOCAL_MODEL} ...", flush=True)
    m = CrossEncoder(LOCAL_MODEL, max_length=512)
    print("[reranker] model loaded.", flush=True)
    return m


def _local(query, documents, top_k):
    model = _local_model()
    scores = model.predict([(query, d) for d in documents])
    ranked = sorted(
        ({"index": i, "score": float(s)} for i, s in enumerate(scores)),
        key=lambda x: x["score"], reverse=True)
    return ranked[:top_k]


@functools.lru_cache(maxsize=1)
def _cohere_client():
    import cohere
    return cohere.Client(os.environ["COHERE_API_KEY"])


def _cohere(query, documents, top_k):
    res = _cohere_client().rerank(model=COHERE_MODEL, query=query,
                                  documents=documents, top_n=top_k)
    return [{"index": r.index, "score": r.relevance_score} for r in res.results]


def _jina(query, documents, top_k):
    import requests
    r = requests.post("https://api.jina.ai/v1/rerank",
                      headers={"Authorization": f"Bearer {os.environ['JINA_API_KEY']}"},
                      json={"model": JINA_MODEL, "query": query,
                            "documents": documents, "top_n": top_k}, timeout=30)
    r.raise_for_status()
    return [{"index": d["index"], "score": d["relevance_score"]}
            for d in r.json()["results"]]


def _voyage(query, documents, top_k):
    import voyageai
    vo = voyageai.Client(api_key=os.environ.get("VOYAGE_API_KEY"))
    res = vo.rerank(query, documents, model=VOYAGE_MODEL, top_k=top_k)
    return [{"index": r.index, "score": r.relevance_score} for r in res.results]


_DISPATCH = {"local": _local, "cohere": _cohere, "jina": _jina, "voyage": _voyage}


# ----------------------------- startup validation -----------------------------
def validate_backend():
    if BACKEND not in _DISPATCH:
        raise RuntimeError(f"unknown RERANKER_BACKEND='{BACKEND}'. "
                           f"Choose: {list(_DISPATCH)}")
    key_required = {"cohere": "COHERE_API_KEY", "jina": "JINA_API_KEY",
                    "voyage": "VOYAGE_API_KEY"}
    if BACKEND in key_required and not os.environ.get(key_required[BACKEND]):
        raise RuntimeError(f"backend '{BACKEND}' requires {key_required[BACKEND]}")
    print(f"[reranker] backend='{BACKEND}' validated.", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_backend()          # fail loudly at boot, not on first query
    yield


app = FastAPI(title="Reranker Service", version="1.0", lifespan=lifespan)


# ----------------------------- API -----------------------------
class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_k: int = 6


class RankItem(BaseModel):
    index: int
    score: float


class RerankResponse(BaseModel):
    ranked: list[RankItem]
    backend: str


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    if not req.documents:
        return RerankResponse(ranked=[], backend=BACKEND)
    try:
        ranked = _DISPATCH[BACKEND](req.query, req.documents, req.top_k)
        return RerankResponse(ranked=ranked, backend=BACKEND)
    except Exception as e:
        # Surface as 503 so the caller's fallback path triggers cleanly
        raise HTTPException(503, detail=f"rerank failed ({BACKEND}): {e}")


@app.get("/health")
def health():
    info = {"status": "ok", "backend": BACKEND}
    if BACKEND == "local":
        # report whether the model is loaded yet (lazy)
        info["model"] = LOCAL_MODEL
        info["model_loaded"] = _local_model.cache_info().currsize > 0
    return info
