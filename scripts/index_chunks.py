"""
Stage 3a: Embed + Index into OpenSearch
=======================================
Reads chunks.jsonl (from Stage 2), embeds with OpenAI text-embedding-3-large,
bulk-indexes into a hybrid-search-ready OpenSearch index.

Setup:
    pip install opensearch-py openai python-dotenv
    docker compose up -d        # OpenSearch on localhost:9200

Run:
    python index_chunks.py parsed_output/osea/chunks.jsonl
    python index_chunks.py parsed_output/*/chunks.jsonl      # all docs at once
"""
import sys
import json
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI
from opensearchpy import OpenSearch, helpers

INDEX = "annual-reports"
EMBED_MODEL = "text-embedding-3-large"   # 3072 dims
EMBED_DIMS = 3072
BATCH = 64

MAPPING = {
    "settings": {
        "index": {"knn": True},
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "properties": {
            "chunk_id":     {"type": "keyword"},
            "doc_id":       {"type": "keyword"},
            "content_type": {"type": "keyword"},     # narrative | table
            "section_path": {"type": "text",
                             "fields": {"raw": {"type": "keyword"}}},
            "pages":        {"type": "integer"},
            "company":      {"type": "keyword"},
            "ticker":       {"type": "keyword"},
            "fiscal_year":  {"type": "integer"},
            "currency":     {"type": "keyword"},
            "text":         {"type": "text"},        # BM25 target
            "raw_table":    {"type": "text", "index": False},  # stored, not searched
            "embedding": {
                "type": "knn_vector",
                "dimension": EMBED_DIMS,
                "method": {
                    "name": "hnsw",
                    "engine": "lucene",   # supports efficient filtered kNN
                    "space_type": "cosinesimil",
                    "parameters": {"m": 16, "ef_construction": 128},
                },
            },
        }
    },
}


def get_clients():
    os_client = OpenSearch(hosts=[{"host": "localhost", "port": 9200}],
                           http_compress=True, use_ssl=False)
    return os_client, OpenAI()


def ensure_index(os_client):
    if os_client.indices.exists(INDEX):
        print(f"Index '{INDEX}' exists — indexing into it (delete to rebuild: "
              f"curl -XDELETE localhost:9200/{INDEX})")
    else:
        os_client.indices.create(INDEX, body=MAPPING)
        print(f"Created index '{INDEX}' ({EMBED_DIMS}-dim Lucene HNSW, cosine)")


def embed_batch(oa: OpenAI, texts: list[str]) -> list[list[float]]:
    resp = oa.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def load_chunks(paths: list[Path]):
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def main():
    if len(sys.argv) < 2:
        print("Usage: python index_chunks.py <chunks.jsonl> [more.jsonl ...]")
        sys.exit(1)
    paths = [Path(p) for p in sys.argv[1:]]
    os_client, oa = get_clients()
    ensure_index(os_client)

    chunks = list(load_chunks(paths))
    print(f"Embedding + indexing {len(chunks)} chunks in batches of {BATCH} ...")

    actions, total = [], 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        vectors = embed_batch(oa, [c["text"] for c in batch])
        for c, v in zip(batch, vectors):
            meta = c.get("doc_metadata", {})
            actions.append({
                "_index": INDEX,
                "_id": c["chunk_id"],          # idempotent re-runs
                "_source": {
                    "chunk_id": c["chunk_id"],
                    "doc_id": meta.get("doc_id"),
                    "content_type": c["content_type"],
                    "section_path": c.get("section_path", ""),
                    "pages": c.get("pages", []),
                    "company": meta.get("company"),
                    "ticker": meta.get("ticker"),
                    "fiscal_year": meta.get("fiscal_year"),
                    "currency": meta.get("currency"),
                    "text": c["text"],
                    "raw_table": c.get("raw_table"),
                    "embedding": v,
                },
            })
        helpers.bulk(os_client, actions)
        total += len(actions)
        actions = []
        print(f"  indexed {total}/{len(chunks)}", end="\r")

    os_client.indices.refresh(INDEX)
    count = os_client.count(index=INDEX)["count"]
    print(f"\nDone. Index '{INDEX}' now holds {count} chunks.")


if __name__ == "__main__":
    main()
