-- CSE RAG — Postgres schema (v2: catalog discovery + on-demand indexing)
-- companies: master reference from CSV
-- documents: every KNOWN annual report (full CSE catalog), with lifecycle status

CREATE TABLE IF NOT EXISTS companies (
    symbol         TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    sector         TEXT,
    cse_secid      INTEGER,
    catalog_synced_at TIMESTAMPTZ,        -- last time we discovered this co's reports
    created_at     TIMESTAMPTZ DEFAULT now(),
    updated_at     TIMESTAMPTZ DEFAULT now()
);

-- Lifecycle:
--   AVAILABLE   -> known to exist on CSE (from discovery), not yet indexed
--   QUEUED      -> user requested indexing; waiting for a worker
--   PROCESSING  -> a worker is actively ingesting it
--   INDEXED     -> chunks in OpenSearch (terminal success)
--   FLAGGED     -> parse quality gate tripped
--   FAILED      -> error during indexing
CREATE TABLE IF NOT EXISTS documents (
    doc_id         TEXT PRIMARY KEY,        -- SYMBOL_YEAR e.g. OSEA_2025
    symbol         TEXT NOT NULL REFERENCES companies(symbol),
    company_name   TEXT,
    fiscal_year    INTEGER,
    report_type    TEXT DEFAULT 'annual',
    cse_report_id  INTEGER,
    cse_path       TEXT,
    cse_file_text  TEXT,
    pdf_url        TEXT,
    pdf_object     TEXT,
    file_hash      TEXT,
    status         TEXT NOT NULL DEFAULT 'AVAILABLE',
    flags          JSONB DEFAULT '[]'::jsonb,
    pages          INTEGER,
    tables         INTEGER,
    chunks         INTEGER,
    total_chars    INTEGER,
    error          TEXT,
    discovered_at  TIMESTAMPTZ,
    queued_at      TIMESTAMPTZ,
    indexed_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_symbol ON documents(symbol);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_year   ON documents(fiscal_year);
