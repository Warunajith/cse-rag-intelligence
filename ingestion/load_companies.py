"""
Load the CSE company list CSV into Postgres `companies`.

Usage:
    python load_companies.py CompanyList.csv

CSV format: "Company Name,Symbol" header, e.g.
    OVERSEAS REALTY (CEYLON) PLC,OSEA.N0000
"""
import sys
import csv
from pathlib import Path

from infra import pg_connect, init_schema, company_upsert


def main():
    if len(sys.argv) < 2:
        print("Usage: python load_companies.py <CompanyList.csv>")
        sys.exit(1)
    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"not found: {csv_path}")
        sys.exit(1)

    conn = pg_connect()
    init_schema(conn)

    n = 0
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("Company Name") or "").strip()
            symbol = (row.get("Symbol") or "").strip()
            if not symbol:
                continue
            company_upsert(conn, symbol=symbol, name=name)
            n += 1
    print(f"loaded {n} companies into Postgres")

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM companies")
        print(f"companies table now holds {cur.fetchone()[0]} rows")


if __name__ == "__main__":
    main()
