"""
Frontend API extension for rag_service.py
=========================================
Adds the endpoints the Next.js frontend needs, WITHOUT rewriting rag_service.py:
  GET  /companies              list companies + their latest report status (Postgres)
  GET  /companies/{symbol}     one company's detail + document row
  GET  /corpus/stats           headline counts for the landing page
  POST /ask/stream             Server-Sent Events: streams the answer token-by-token,
                               then a final event with sources

Wire it into rag_service.py by adding at the bottom of that file:

    import frontend_api
    frontend_api.register(app, clients, hybrid_retrieve, build_context,
                          GEN_MODEL, SYSTEM_PROMPT, to_source)

It reuses rag_service's own retrieval + context functions (passed in), so the
streaming path produces identical retrieval to /ask — only the delivery differs.

Postgres connection via env (same vars as the ingestion service):
    PG_HOST PG_PORT PG_DB PG_USER PG_PASSWORD   (or PG_DSN)
"""
import os
import json
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


PG_DSN = os.environ.get(
    "PG_DSN",
    "host={h} port={p} dbname={d} user={u} password={pw}".format(
        h=os.environ.get("PG_HOST", "localhost"),
        p=os.environ.get("PG_PORT", "5432"),
        d=os.environ.get("PG_DB", "cse_rag"),
        u=os.environ.get("PG_USER", "raguser"),
        pw=os.environ.get("PG_PASSWORD", "ragpass")))


@contextmanager
def pg_cursor():
    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = True
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


class StreamAskRequest(BaseModel):
    question: str
    fiscal_year: int | None = None
    ticker: str | None = None
    company: str | None = None
    content_type: str | None = None
    top_k: int = 6


def register(app, clients, hybrid_retrieve, build_context,
             gen_model, system_prompt, to_source):
    """Attach the frontend endpoints to the existing FastAPI app."""

    # ---------------- Postgres-backed corpus data ----------------
    @app.get("/companies")
    def companies(status: str | None = None, q: str | None = None):
        sql = """
            SELECT c.symbol, c.name, c.sector,
                   d.fiscal_year, d.status, d.pages, d.tables, d.chunks
            FROM companies c
            LEFT JOIN LATERAL (
                SELECT fiscal_year, status, pages, tables, chunks
                FROM documents
                WHERE symbol = c.symbol AND status = 'INDEXED'
                ORDER BY fiscal_year DESC LIMIT 1
            ) d ON true
            WHERE 1=1
        """
        params = []
        if q:
            sql += " AND (c.name ILIKE %s OR c.symbol ILIKE %s)"
            params += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY c.name"
        with pg_cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return {"companies": rows, "count": len(rows)}

    @app.get("/companies/{symbol}")
    def company_detail(symbol: str):
        with pg_cursor() as cur:
            cur.execute("SELECT * FROM companies WHERE symbol=%s", (symbol,))
            comp = cur.fetchone()
            if not comp:
                raise HTTPException(404, detail="company not found")
            cur.execute("SELECT * FROM documents WHERE symbol=%s "
                        "ORDER BY fiscal_year DESC", (symbol,))
            docs = cur.fetchall()
        return {"company": comp, "documents": docs}

    @app.get("/corpus/stats")
    def corpus_stats():
        with pg_cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM companies")
            companies_n = cur.fetchone()["n"]
            cur.execute("SELECT status, count(*) AS n FROM documents "
                        "GROUP BY status")
            by_status = {r["status"]: r["n"] for r in cur.fetchall()}
        return {"companies": companies_n,
                "indexed": by_status.get("INDEXED", 0),
                "by_status": by_status}

    # ---------------- streaming ask (SSE) ----------------
    @app.post("/ask/stream")
    def ask_stream(req: StreamAskRequest):
        retrieved = hybrid_retrieve(req.question, req)
        sources = [to_source(r).model_dump() for r in retrieved]

        def event_gen():
            if not retrieved:
                yield _sse("token", {"text": "No relevant content found in the "
                                     "indexed reports for this question."})
                yield _sse("done", {"sources": []})
                return
            context = build_context(retrieved)
            stream = clients["oa"].chat.completions.create(
                model=gen_model, max_tokens=1200, stream=True,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content":
                           f"CONTEXT:\n{context}\n\nQUESTION: {req.question}"}])
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield _sse("token", {"text": delta})
            # final event carries the sources for the panel
            yield _sse("done", {"sources": sources})

        return StreamingResponse(event_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
