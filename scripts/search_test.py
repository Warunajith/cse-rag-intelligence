"""
Stage 3b: Hybrid Search (BM25 + kNN) — retrieval validation
===========================================================
Interactive tester: runs BM25-only, vector-only, and hybrid retrieval
side by side so you can SEE what hybrid search adds.

Run:
    python search_test.py "rental income growth 2025"
    python search_test.py "EBITDA" --type table
    python search_test.py "what are the company's main risks" --year 2025
    python search_test.py --interactive
"""
import sys
import json
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI
from opensearchpy import OpenSearch

INDEX = "annual-reports"
EMBED_MODEL = "text-embedding-3-large"
K = 5


def get_clients():
    return (OpenSearch(hosts=[{"host": "localhost", "port": 9200}],
                       use_ssl=False), OpenAI())


def build_filters(args) -> list[dict]:
    f = []
    if args.year:
        f.append({"term": {"fiscal_year": args.year}})
    if args.type:
        f.append({"term": {"content_type": args.type}})
    if args.ticker:
        f.append({"term": {"ticker": args.ticker}})
    return f


def bm25_query(q, filters):
    return {"size": K, "_source": {"excludes": ["embedding"]},
            "query": {"bool": {
                "must": [{"match": {"text": q}}],
                "filter": filters}}}


def knn_query(vec, filters):
    knn = {"vector": vec, "k": K}
    if filters:
        knn["filter"] = {"bool": {"filter": filters}}
    return {"size": K, "_source": {"excludes": ["embedding"]},
            "query": {"knn": {"embedding": knn}}}


def rrf_fuse(bm25_hits, knn_hits, k_const=60):
    """Reciprocal Rank Fusion — simple, robust, no score normalization needed."""
    scores, docs = {}, {}
    for rank, h in enumerate(bm25_hits):
        scores[h["_id"]] = scores.get(h["_id"], 0) + 1 / (k_const + rank + 1)
        docs[h["_id"]] = h
    for rank, h in enumerate(knn_hits):
        scores[h["_id"]] = scores.get(h["_id"], 0) + 1 / (k_const + rank + 1)
        docs[h["_id"]] = h
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:K]
    return [(docs[i], s) for i, s in ranked]


def show(label, hits, scorer=lambda h: h.get("_score", 0)):
    print(f"\n--- {label} ---")
    for h in hits:
        s = h["_source"]
        preview = s["text"][:140].replace("\n", " ")
        print(f"  [{scorer(h):.4f}] ({s['content_type']}) p{s['pages']} "
              f"{s['section_path'][:50]}\n           {preview}...")


def search(q, args, os_client, oa):
    filters = build_filters(args)
    vec = oa.embeddings.create(model=EMBED_MODEL, input=[q]).data[0].embedding

    bm25 = os_client.search(index=INDEX, body=bm25_query(q, filters))["hits"]["hits"]
    knn = os_client.search(index=INDEX, body=knn_query(vec, filters))["hits"]["hits"]
    fused = rrf_fuse(bm25, knn)

    show("BM25 (keyword)", bm25)
    show("kNN (semantic)", knn)
    print(f"\n=== HYBRID (RRF) — what your RAG would retrieve ===")
    for h, score in fused:
        s = h["_source"]
        preview = s["text"][:140].replace("\n", " ")
        print(f"  [{score:.4f}] ({s['content_type']}) p{s['pages']} "
              f"{s['section_path'][:50]}\n           {preview}...")
        if s.get("raw_table"):
            print(f"           [raw_table available: "
                  f"{len(s['raw_table'])} chars -> context injection]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", default=None)
    ap.add_argument("--year", type=int)
    ap.add_argument("--type", choices=["narrative", "table"])
    ap.add_argument("--ticker")
    ap.add_argument("--interactive", action="store_true")
    args = ap.parse_args()

    os_client, oa = get_clients()

    if args.interactive:
        print("Hybrid search tester. Empty line to quit.")
        while True:
            q = input("\nquery> ").strip()
            if not q:
                break
            search(q, args, os_client, oa)
    elif args.query:
        search(args.query, args, os_client, oa)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
