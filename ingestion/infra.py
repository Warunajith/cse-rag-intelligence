"""
Shared infra: Postgres connection + helpers, MinIO client.
"""
import os
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

# ----------------------------- Postgres -----------------------------
PG_DSN = os.environ.get(
    "PG_DSN",
    "host={h} port={p} dbname={d} user={u} password={pw}".format(
        h=os.environ.get("PG_HOST", "localhost"),
        p=os.environ.get("PG_PORT", "5432"),
        d=os.environ.get("PG_DB", "cse_rag"),
        u=os.environ.get("PG_USER", "raguser"),
        pw=os.environ.get("PG_PASSWORD", "ragpass")))


def pg_connect():
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def init_schema(conn, schema_path: str = None):
    path = schema_path or str(Path(__file__).parent / "schema.sql")
    with open(path) as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    print("schema initialized")


def doc_upsert(conn, doc_id: str, **fields):
    """Insert-or-update a documents row. Uses UPDATE when the row already exists
    so partial updates don't need to re-supply NOT NULL columns (e.g. symbol)."""
    fields["updated_at"] = datetime.now(timezone.utc)
    if "flags" in fields and not isinstance(fields["flags"], str):
        fields["flags"] = json.dumps(fields["flags"])

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM documents WHERE doc_id=%s", (doc_id,))
        exists = cur.fetchone() is not None

        if exists:
            if fields:
                sets = ", ".join(f"{c}=%s" for c in fields)
                cur.execute(f"UPDATE documents SET {sets} WHERE doc_id=%s",
                            [*fields.values(), doc_id])
        else:
            cols = ["doc_id", *fields]
            vals = [doc_id, *fields.values()]
            cur.execute(
                f"INSERT INTO documents ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})", vals)

def doc_get(conn, doc_id: str) -> dict | None:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM documents WHERE doc_id=%s", (doc_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def company_upsert(conn, symbol: str, **fields):
    fields["updated_at"] = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM companies WHERE symbol=%s", (symbol,))
        exists = cur.fetchone() is not None
        if exists:
            if fields:
                sets = ", ".join(f"{c}=%s" for c in fields)
                cur.execute(f"UPDATE companies SET {sets} WHERE symbol=%s",
                            [*fields.values(), symbol])
        else:
            cols = ["symbol", *fields]
            vals = [symbol, *fields.values()]
            cur.execute(
                f"INSERT INTO companies ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})", vals)


# ----------------------------- MinIO -----------------------------
def minio_client():
    from minio import Minio
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.environ.get("MINIO_SECRET_KEY", "minioadmin"),
        secure=os.environ.get("MINIO_SECURE", "false").lower() == "true")


RAW_BUCKET = os.environ.get("MINIO_RAW_BUCKET", "cse-raw-pdfs")


def ensure_bucket(client, bucket: str = RAW_BUCKET):
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def put_pdf(client, object_key: str, data: bytes, bucket: str = RAW_BUCKET) -> str:
    ensure_bucket(client, bucket)
    client.put_object(bucket, object_key, io.BytesIO(data), length=len(data),
                      content_type="application/pdf")
    return object_key


def get_pdf(client, object_key: str, bucket: str = RAW_BUCKET) -> bytes:
    resp = client.get_object(bucket, object_key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()
