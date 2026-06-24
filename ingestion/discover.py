"""
Catalog discovery — populate Postgres with EVERY annual report CSE lists,
without downloading or indexing. Cheap metadata-only pass.

This is what powers the coverage view: it records the full universe of
available annual reports per company as status=AVAILABLE. Indexing later
flips individual reports to INDEXED.

Usage (standalone / one-off):
    python discover.py                      # all companies
    python discover.py --symbols OSEA.N0000 HNB.N0000
    python discover.py --limit 20

Also importable: discover_company(conn, symbol, name) and
discover_all(conn, ...) are called by the /catalog/refresh endpoint.
"""
import sys
import time
import argparse
from datetime import datetime, timezone

import psycopg2.extras

from infra import pg_connect, init_schema, doc_upsert
import cse_client as cse

FETCH_DELAY = 1.0   # politeness between companies


def discover_company(conn, symbol: str, name: str) -> dict:
    """Fetch one company's annual catalog; upsert each report as AVAILABLE
    (without clobbering reports already INDEXED/PROCESSING). Returns counts."""
    fin = cse.fetch_financials(symbol)
    reports = cse.all_annual_reports(fin)
    now = datetime.now(timezone.utc)

    added, existing = 0, 0
    for rep in reports:
        doc_id = f"{symbol.split('.')[0]}_{rep['fiscal_year']}"
        # Only set AVAILABLE if the row doesn't already exist in a further state.
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM documents WHERE doc_id=%s", (doc_id,))
            row = cur.fetchone()
        if row:
            existing += 1
            # refresh catalog metadata but DO NOT downgrade an indexed/queued doc
            doc_upsert(conn, doc_id, cse_report_id=rep["cse_report_id"],
                       cse_path=rep["cse_path"], cse_file_text=rep["file_text"],
                       pdf_url=rep["pdf_url"], company_name=name,
                       discovered_at=now)
        else:
            added += 1
            doc_upsert(conn, doc_id, symbol=symbol, company_name=name,
                       fiscal_year=rep["fiscal_year"], report_type="annual",
                       cse_report_id=rep["cse_report_id"], cse_path=rep["cse_path"],
                       cse_file_text=rep["file_text"], pdf_url=rep["pdf_url"],
                       status="AVAILABLE", discovered_at=now)

    with conn.cursor() as cur:
        cur.execute("UPDATE companies SET catalog_synced_at=%s, updated_at=now() "
                    "WHERE symbol=%s", (now, symbol))
    return {"symbol": symbol, "reports_found": len(reports),
            "added": added, "existing": existing}


def discover_all(conn, symbols=None, limit=None, delay=FETCH_DELAY,
                 progress=None) -> dict:
    """Discover catalog for many companies. `progress` is an optional callback
    (i, total, result) for live status (used by the worker/endpoint)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if symbols:
            cur.execute("SELECT symbol, name FROM companies WHERE symbol = ANY(%s)",
                        (symbols,))
        else:
            cur.execute("SELECT symbol, name FROM companies ORDER BY symbol"
                        + (f" LIMIT {int(limit)}" if limit else ""))
        targets = cur.fetchall()

    totals = {"companies": 0, "reports_found": 0, "added": 0, "failed": 0}
    for i, row in enumerate(targets, 1):
        try:
            r = discover_company(conn, row["symbol"], row["name"])
            totals["companies"] += 1
            totals["reports_found"] += r["reports_found"]
            totals["added"] += r["added"]
            if progress:
                progress(i, len(targets), r)
        except Exception as e:
            totals["failed"] += 1
            if progress:
                progress(i, len(targets), {"symbol": row["symbol"], "error": str(e)})
        time.sleep(delay)
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    conn = pg_connect()
    init_schema(conn)

    def prog(i, total, r):
        if "error" in r:
            print(f"  [{i}/{total}] {r['symbol']} FAILED: {r['error']}")
        else:
            print(f"  [{i}/{total}] {r['symbol']}: {r['reports_found']} reports "
                  f"({r['added']} new)")

    print("Discovering annual report catalog...")
    totals = discover_all(conn, symbols=args.symbols, limit=args.limit, progress=prog)
    print(f"\nDone. {totals['companies']} companies, "
          f"{totals['reports_found']} reports found, "
          f"{totals['added']} newly added, {totals['failed']} failed.")


if __name__ == "__main__":
    main()
