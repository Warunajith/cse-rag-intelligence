"""
Catalog & indexing API — coverage view + on-demand indexing endpoints.
Register alongside frontend_api on the same FastAPI app.

  GET  /catalog/stats              corpus coverage (available vs indexed)
  POST /catalog/refresh            enqueue a discovery pass (populate AVAILABLE)
  GET  /catalog/company/{symbol}   all annual reports for a company + statuses
  POST /index/report               enqueue one report  {doc_id}
  POST /index/company              enqueue all available for a company {symbol}
  GET  /index/queue                queue length + currently processing

Enqueues onto the same Redis queue the worker consumes. Reads status from
Postgres (the documents table is the source of truth for the UI).
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import redis
from fastapi import HTTPException
from pydantic import BaseModel

PG_DSN = os.environ.get(
    "PG_DSN",
    "host={h} port={p} dbname={d} user={u} password={pw}".format(
        h=os.environ.get("PG_HOST", "localhost"),
        p=os.environ.get("PG_PORT", "5432"),
        d=os.environ.get("PG_DB", "cse_rag"),
        u=os.environ.get("PG_USER", "raguser"),
        pw=os.environ.get("PG_PASSWORD", "ragpass")))

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
Q_INDEX = "cse:jobs:index"
Q_DISCOVER = "cse:jobs:discover"
_r = None


def _redis():
    global _r
    if _r is None:
        _r = redis.from_url(REDIS_URL, decode_responses=True)
    return _r


def _enqueue(job: dict) -> int:
    import json
    queue = Q_DISCOVER if job.get("type") == "discover" else Q_INDEX
    return _redis().rpush(queue, json.dumps(job))


@contextmanager
def _cur():
    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = True
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


class IndexReportReq(BaseModel):
    doc_id: str


class IndexCompanyReq(BaseModel):
    symbol: str


class RefreshReq(BaseModel):
    symbols: list[str] | None = None
    limit: int | None = None


def register_catalog(app):

    @app.get("/catalog/stats")
    def catalog_stats():
        with _cur() as cur:
            cur.execute("SELECT count(*) AS n FROM companies")
            companies = cur.fetchone()["n"]
            cur.execute("SELECT status, count(*) AS n FROM documents GROUP BY status")
            by_status = {r["status"]: r["n"] for r in cur.fetchall()}
            cur.execute("SELECT count(*) AS n FROM documents")
            total = cur.fetchone()["n"]
        return {
            "companies": companies,
            "reports_available_total": total,           # full known catalog
            "indexed": by_status.get("INDEXED", 0),
            "available": by_status.get("AVAILABLE", 0),
            "processing": by_status.get("PROCESSING", 0)
                          + by_status.get("QUEUED", 0),
            "flagged": by_status.get("FLAGGED", 0),
            "failed": by_status.get("FAILED", 0),
            "by_status": by_status,
        }

    @app.get("/catalog/company/{symbol}")
    def catalog_company(symbol: str):
        with _cur() as cur:
            cur.execute("SELECT * FROM companies WHERE symbol=%s", (symbol,))
            comp = cur.fetchone()
            if not comp:
                raise HTTPException(404, "company not found")
            cur.execute("SELECT doc_id, fiscal_year, status, pages, tables, chunks, "
                        "cse_file_text, indexed_at, error FROM documents "
                        "WHERE symbol=%s ORDER BY fiscal_year DESC", (symbol,))
            reports = cur.fetchall()
        return {"company": comp, "reports": reports}

    @app.post("/catalog/refresh")
    def catalog_refresh(req: RefreshReq):
        # Guard: refuse to queue a second full-catalog discovery if one is
        # already pending — repeated clicks otherwise stack redundant passes.
        pending = _redis().llen(Q_DISCOVER)
        if pending > 0 and not req.symbols:
            return {"status": "already_pending", "job": "discover",
                    "discover_queue": pending,
                    "message": "A catalog refresh is already queued or running."}
        n = _enqueue({"type": "discover", "symbols": req.symbols, "limit": req.limit})
        return {"status": "queued", "job": "discover", "discover_queue": n}

    @app.post("/index/report")
    def index_report(req: IndexReportReq):
        with _cur() as cur:
            cur.execute("SELECT status FROM documents WHERE doc_id=%s", (req.doc_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "doc_id not found")
            if row["status"] == "INDEXED":
                return {"status": "already_indexed", "doc_id": req.doc_id}
            cur.execute("UPDATE documents SET status='QUEUED', queued_at=now() "
                        "WHERE doc_id=%s", (req.doc_id,))
        n = _enqueue({"type": "index_report", "doc_id": req.doc_id})
        return {"status": "queued", "doc_id": req.doc_id, "queue_length": n}

    @app.post("/index/company")
    def index_company(req: IndexCompanyReq):
        with _cur() as cur:
            cur.execute("UPDATE documents SET status='QUEUED', queued_at=now() "
                        "WHERE symbol=%s AND status IN "
                        "('AVAILABLE','FAILED','FLAGGED')", (req.symbol,))
        n = _enqueue({"type": "index_company", "symbol": req.symbol})
        return {"status": "queued", "symbol": req.symbol, "queue_length": n}

    @app.get("/index/queue")
    def index_queue():
        with _cur() as cur:
            cur.execute("SELECT doc_id, company_name, fiscal_year FROM documents "
                        "WHERE status='PROCESSING' LIMIT 5")
            processing = cur.fetchall()
        r = _redis()
        return {"index_queue": r.llen(Q_INDEX),
                "discover_queue": r.llen(Q_DISCOVER),
                "processing": processing}
