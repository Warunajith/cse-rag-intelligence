"""
RAG Serving Layer — stateless, read-only over the OpenSearch index
==================================================================
Endpoints:
    POST /ask            question -> hybrid retrieve -> inject raw tables -> answer
    POST /search         hybrid retrieve only (no LLM) — cheap, for UI/debugging
    GET  /documents      indexed corpus (aggregated FROM OpenSearch)
    GET  /filters        distinct companies / years / currencies available
    GET  /health/live    process up
    GET  /health/ready   OpenSearch reachable + index exists

Reads only: OPENAI_API_KEY, OPENSEARCH_HOST, OPENSEARCH_PORT from env.
Stateless: no registry, no shared files — corpus info comes from the index itself.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from opensearchpy import OpenSearch
import requests

# ----------------------------- config -----------------------------
INDEX = "annual-reports"
EMBED_MODEL = "text-embedding-3-large"
GEN_MODEL = os.environ.get("GEN_MODEL", "gpt-4o-mini")
TOP_K = 6
RETRIEVE_K = 10            # per-retriever depth when reranking is OFF
RERANK_CANDIDATES = 30     # WIDER per-retriever depth when reranking is ON
MAX_TABLE_CHARS = 6000

# Two-level reranking switch:
#  1) RERANK_ENABLED (intent): false -> never call the service, pure baseline.
#  2) runtime fallback: if enabled but the service errors/times out, /ask still
#     proceeds with retrieval order. Reranking is an enhancement, never a hard dep.
RERANK_ENABLED = os.environ.get("RERANK_ENABLED", "false").lower() == "true"
RERANKER_URL = os.environ.get("RERANKER_URL", "http://reranker:8001")
RERANK_TIMEOUT = float(os.environ.get("RERANK_TIMEOUT", "15"))

OS_HOST = os.environ.get("OPENSEARCH_HOST", "localhost")
OS_PORT = int(os.environ.get("OPENSEARCH_PORT", "9200"))

SYSTEM_PROMPT = """You are a financial analyst assistant answering questions about \
company annual reports. Rules:
- Answer ONLY from the provided context. If the context lacks the answer, say so \
explicitly — never invent figures.
- Always state currency and units (e.g. Rs. Mn) exactly as in the source.
- When using a table, read values from the correct column (check year headers).
- Cite sources inline as [p.<page>] after each claim.
- For comparisons/trends, show figures, the change, and percentage where possible.
- Be concise and quantitative. Distinguish Group vs Company figures when both exist.
- Write in plain prose. Do NOT use Markdown formatting, bold (**), headers, or
  LaTeX math notation. Write calculations inline, e.g. "33,327.5M ÷ 3,778.8M ≈ 8.81",
  not as \[ \frac{}{} \] blocks."""

clients = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    clients["os"] = OpenSearch(hosts=[{"host": OS_HOST, "port": OS_PORT}],
                               use_ssl=False, http_compress=True)
    clients["oa"] = OpenAI()
    yield
    clients.clear()


app = FastAPI(title="Annual Report RAG", version="1.0", lifespan=lifespan)


# ----------------------------- models -----------------------------
class AskRequest(BaseModel):
    question: str
    fiscal_year: int | None = None
    ticker: str | None = None
    company: str | None = None
    content_type: str | None = None
    top_k: int = TOP_K


class Source(BaseModel):
    chunk_id: str
    content_type: str
    section_path: str
    pages: list[int]
    company: str | None = None
    fiscal_year: int | None = None
    score: float


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    model: str


class SearchResponse(BaseModel):
    results: list[Source]
    count: int


# ----------------------------- retrieval core -----------------------------
def build_filters(req: AskRequest) -> list[dict]:
    f = []
    if req.fiscal_year:
        f.append({"term": {"fiscal_year": req.fiscal_year}})
    if req.ticker:
        f.append({"term": {"ticker": req.ticker}})
    if req.company:
        f.append({"term": {"company": req.company}})
    if req.content_type:
        f.append({"term": {"content_type": req.content_type}})
    return f


def hybrid_retrieve(question: str, req: AskRequest) -> list[dict]:
    """Hybrid BM25+kNN -> RRF fusion -> (optional) cross-encoder rerank -> top_k.

    When reranking is ON, retrieve WIDE (RERANK_CANDIDATES per retriever) so the
    cross-encoder has more candidates to sharpen; then narrow to top_k.
    When OFF, behaves exactly as before (narrow retrieve, fuse, slice top_k).
    """
    os_client, oa = clients["os"], clients["oa"]
    filters = build_filters(req)
    vec = oa.embeddings.create(model=EMBED_MODEL, input=[question]).data[0].embedding

    depth = RERANK_CANDIDATES if RERANK_ENABLED else RETRIEVE_K

    bm25 = os_client.search(index=INDEX, body={
        "size": depth, "_source": {"excludes": ["embedding"]},
        "query": {"bool": {"must": [{"match": {"text": question}}],
                           "filter": filters}}})["hits"]["hits"]

    knn_body = {"vector": vec, "k": depth}
    if filters:
        knn_body["filter"] = {"bool": {"filter": filters}}
    knn = os_client.search(index=INDEX, body={
        "size": depth, "_source": {"excludes": ["embedding"]},
        "query": {"knn": {"embedding": knn_body}}})["hits"]["hits"]

    scores, docs = {}, {}
    for hits in (bm25, knn):
        for rank, h in enumerate(hits):
            scores[h["_id"]] = scores.get(h["_id"], 0) + 1 / (60 + rank + 1)
            docs[h["_id"]] = h
    fused = [{"hit": docs[i], "rrf_score": s}
             for i, s in sorted(scores.items(), key=lambda x: -x[1])]

    if RERANK_ENABLED:
        return maybe_rerank(question, fused, req.top_k)
    return fused[: req.top_k]


def maybe_rerank(question: str, fused: list[dict], top_k: int) -> list[dict]:
    """Call the rerank service; on ANY failure, fall back to retrieval order.
    Reranking must never break /ask."""
    if not fused:
        return []
    try:
        texts = [c["hit"]["_source"]["text"] for c in fused]
        resp = requests.post(f"{RERANKER_URL}/rerank",
                             json={"query": question, "documents": texts,
                                   "top_k": top_k}, timeout=RERANK_TIMEOUT)
        resp.raise_for_status()
        order = resp.json()["ranked"]            # [{index, score}, ...]
        out = []
        for r in order:
            c = fused[r["index"]]
            c["rerank_score"] = r["score"]
            out.append(c)
        return out
    except Exception as e:
        print(f"[rerank] unavailable ({e}); using retrieval order.")
        return fused[:top_k]


def to_source(r: dict) -> Source:
    s = r["hit"]["_source"]
    return Source(chunk_id=s["chunk_id"], content_type=s["content_type"],
                  section_path=s.get("section_path", ""), pages=s.get("pages", []),
                  company=s.get("company"), fiscal_year=s.get("fiscal_year"),
                  score=round(r["rrf_score"], 4))


def build_context(retrieved: list[dict]) -> str:
    blocks = []
    for r in retrieved:
        s = r["hit"]["_source"]
        pages = ",".join(map(str, s.get("pages", [])))
        header = f"[SOURCE p.{pages} | {s['content_type']} | {s.get('section_path','')}]"
        body = (f"{s['text']}\n\nFULL TABLE:\n{s['raw_table'][:MAX_TABLE_CHARS]}"
                if s.get("raw_table") else s["text"])
        blocks.append(f"{header}\n{body}")
    return "\n\n---\n\n".join(blocks)


# ----------------------------- endpoints -----------------------------
@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    retrieved = hybrid_retrieve(req.question, req)
    if not retrieved:
        return AskResponse(answer="No relevant content found in the indexed reports "
                           "for this question.", sources=[], model=GEN_MODEL)
    resp = clients["oa"].chat.completions.create(
        model=GEN_MODEL, max_tokens=1200,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content":
                   f"CONTEXT:\n{build_context(retrieved)}\n\nQUESTION: {req.question}"}])
    return AskResponse(answer=resp.choices[0].message.content,
                       sources=[to_source(r) for r in retrieved], model=GEN_MODEL)


@app.post("/search", response_model=SearchResponse)
def search(req: AskRequest):
    """Hybrid retrieval only — no LLM. Cheap, fast, for UI source-browsing/debugging."""
    retrieved = hybrid_retrieve(req.question, req)
    sources = [to_source(r) for r in retrieved]
    return SearchResponse(results=sources, count=len(sources))


@app.get("/documents")
def documents():
    """Indexed corpus, aggregated FROM the index (source of truth for what's queryable)."""
    body = {"size": 0, "aggs": {"docs": {"terms": {"field": "doc_id", "size": 1000},
            "aggs": {"company": {"terms": {"field": "company", "size": 1}},
                     "year": {"terms": {"field": "fiscal_year", "size": 1}}}}}}
    res = clients["os"].search(index=INDEX, body=body)
    out = []
    for b in res["aggregations"]["docs"]["buckets"]:
        comp = b["company"]["buckets"]
        yr = b["year"]["buckets"]
        out.append({"doc_id": b["key"], "chunks": b["doc_count"],
                    "company": comp[0]["key"] if comp else None,
                    "fiscal_year": yr[0]["key"] if yr else None})
    return {"documents": out, "count": len(out)}


@app.get("/filters")
def filters():
    """Distinct filter values available — for populating UI dropdowns."""
    body = {"size": 0, "aggs": {
        "companies": {"terms": {"field": "company", "size": 1000}},
        "years": {"terms": {"field": "fiscal_year", "size": 100}},
        "currencies": {"terms": {"field": "currency", "size": 50}}}}
    a = clients["os"].search(index=INDEX, body=body)["aggregations"]
    return {"companies": [b["key"] for b in a["companies"]["buckets"]],
            "fiscal_years": sorted(b["key"] for b in a["years"]["buckets"]),
            "currencies": [b["key"] for b in a["currencies"]["buckets"]]}


@app.get("/health/live")
def live():
    return {"status": "alive"}


@app.get("/health/ready")
def ready():
    try:
        if not clients["os"].indices.exists(index=INDEX):
            raise HTTPException(503, detail=f"index '{INDEX}' not found")
        count = clients["os"].count(index=INDEX)["count"]
        rerank_info = {"enabled": RERANK_ENABLED}
        if RERANK_ENABLED:
            try:
                rerank_info.update(
                    requests.get(f"{RERANKER_URL}/health", timeout=5).json())
            except Exception as e:
                rerank_info["service"] = f"unreachable: {e}"
        return {"status": "ready", "index": INDEX, "chunks": count,
                "reranker": rerank_info}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, detail=f"opensearch unreachable: {e}")

import frontend_api
frontend_api.register(app, clients, hybrid_retrieve, build_context,
                      GEN_MODEL, SYSTEM_PROMPT, to_source)

import catalog_api
catalog_api.register_catalog(app)