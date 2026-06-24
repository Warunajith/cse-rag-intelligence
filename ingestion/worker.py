"""
Worker — consumes jobs from Redis and runs them. Runs as its own container,
separate from the API, so heavy Docling parsing never blocks query serving.

Job types:
    {"type": "index_report", "doc_id": "OSEA_2023"}
    {"type": "index_company", "symbol": "OSEA.N0000"}   # queues all AVAILABLE
    {"type": "discover", "symbols": [...]|null, "limit": int|null}

Run:
    python worker.py
"""
import time
import traceback
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2.extras
from openai import OpenAI

from infra import pg_connect, init_schema, minio_client, doc_upsert
import jobqueue
import pipeline
import discover as disc


def handle_index_report(conn, mc, oa, doc_id):
    pipeline.index_one_report(conn, mc, oa, doc_id)


def handle_index_company(conn, mc, oa, symbol):
    """Queue + process all AVAILABLE/FAILED/FLAGGED reports for a company."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT doc_id FROM documents WHERE symbol=%s "
                    "AND status IN ('AVAILABLE','QUEUED','FAILED','FLAGGED') "
                    "ORDER BY fiscal_year DESC", (symbol,))
        doc_ids = [r["doc_id"] for r in cur.fetchall()]
    print(f"index_company {symbol}: {len(doc_ids)} reports")
    for doc_id in doc_ids:
        pipeline.index_one_report(conn, mc, oa, doc_id)


def handle_discover(conn, symbols, limit):
    def prog(i, total, r):
        tag = r.get("error", f"{r.get('reports_found', 0)} reports")
        print(f"  discover [{i}/{total}] {r['symbol']}: {tag}")
    disc.discover_all(conn, symbols=symbols, limit=limit, progress=prog)


def main():
    conn = pg_connect()
    init_schema(conn)
    mc = minio_client()
    oa = OpenAI()
    print("Worker started. Waiting for jobs...")

    while True:
        try:
            job = jobqueue.dequeue(timeout=5)
            if job is None:
                continue
            t = job.get("type")
            print(f"\n>>> job: {t} {job}")
            if t == "index_report":
                handle_index_report(conn, mc, oa, job["doc_id"])
            elif t == "index_company":
                handle_index_company(conn, mc, oa, job["symbol"])
            elif t == "discover":
                handle_discover(conn, job.get("symbols"), job.get("limit"))
            else:
                print(f"unknown job type: {t}")
        except Exception as e:
            print(f"worker loop error: {e}")
            traceback.print_exc()
            time.sleep(2)


if __name__ == "__main__":
    main()
