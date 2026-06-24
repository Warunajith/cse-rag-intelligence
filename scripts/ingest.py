"""
Ingestion Orchestrator — parse -> chunk -> index, with registry + quality gating
=================================================================================
One command per PDF. Tracks every document's stage, config, and parse-quality
flags in a SQLite registry so you never re-ingest blindly or miss a bad parse.

Setup:
    pip install docling openai tiktoken opensearch-py python-dotenv pandas
    # OpenSearch running (docker compose up -d), OPENAI_API_KEY set

Run:
    python ingest.py report.pdf                  # full pipeline for one PDF
    python ingest.py *.pdf                        # batch
    python ingest.py report.pdf --force           # re-ingest even if done
    python ingest.py --status                      # show registry
    python ingest.py --flagged                     # show only flagged docs
    python ingest.py report.pdf --skip-index       # parse+chunk only

Stages tracked: PARSED -> CHUNKED -> INDEXED (or FLAGGED / FAILED).

This wraps the existing stage scripts' logic. It imports nothing from them;
the proven functions are inlined so this is a single self-contained entrypoint.
"""
import os
import sys
import json
import html
import re
import time
import sqlite3
import hashlib
import argparse
import traceback
from pathlib import Path
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ----------------------------- config -----------------------------
REGISTRY_DB = Path("ingestion_registry.db")
OUTPUT_ROOT = Path("parsed_output")
INDEX = "annual-reports"
OS_HOST = os.environ.get("OPENSEARCH_HOST", "localhost")

LLM_MODEL = "gpt-4o-mini"
EMBED_MODEL = "text-embedding-3-large"
EMBED_DIMS = 3072

MAX_CHUNK_TOKENS = 700
MIN_CHUNK_TOKENS = 80
EMBED_BATCH = 64

# Quality-gate thresholds — tuned conservatively; adjust from real runs
MIN_CHARS_PER_PAGE = 200        # below this avg => likely scanned / failed text
MIN_TABLES_EXPECTED = 1         # a financial report with 0 tables is suspicious
MIN_TOTAL_CHARS = 5000          # whole-doc floor

DROP_LABELS = {"page_footer", "page_header"}
TEXT_LABELS = {"text", "list_item", "caption"}
HEADER_LABELS = {"section_header", "title"}

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def n_tokens(s): return len(_enc.encode(s))
except ImportError:
    def n_tokens(s): return max(1, len(s) // 4)


# ============================ REGISTRY ============================
def registry_connect():
    conn = sqlite3.connect(REGISTRY_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id        TEXT PRIMARY KEY,
            source_path   TEXT,
            file_hash     TEXT,
            stage         TEXT,            -- PARSED|CHUNKED|INDEXED|FLAGGED|FAILED
            company       TEXT,
            fiscal_year   INTEGER,
            pages         INTEGER,
            tables        INTEGER,
            chunks        INTEGER,
            total_chars   INTEGER,
            flags         TEXT,            -- JSON list of quality flags
            config        TEXT,            -- JSON of run config
            error         TEXT,
            updated_at    TEXT
        )""")
    conn.commit()
    return conn


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


def registry_get(conn, doc_id):
    row = conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if not row:
        return None
    cols = [c[0] for c in conn.execute("SELECT * FROM documents LIMIT 0").description]
    return dict(zip(cols, row))


def registry_upsert(conn, doc_id, **fields):
    fields["doc_id"] = doc_id
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    existing = registry_get(conn, doc_id)
    if existing:
        sets = ", ".join(f"{k}=?" for k in fields if k != "doc_id")
        vals = [v for k, v in fields.items() if k != "doc_id"] + [doc_id]
        conn.execute(f"UPDATE documents SET {sets} WHERE doc_id=?", vals)
    else:
        cols = ", ".join(fields)
        ph = ", ".join("?" for _ in fields)
        conn.execute(f"INSERT INTO documents ({cols}) VALUES ({ph})",
                     list(fields.values()))
    conn.commit()


# ============================ STAGE 1: PARSE ============================
def parse_pdf(pdf_path: Path, out_dir: Path, use_ocr: bool):
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=use_ocr, do_table_structure=True,
                              generate_picture_images=False, images_scale=1.0)
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    converter = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opts,
                                         backend=PyPdfiumDocumentBackend)})

    result = converter.convert(pdf_path)
    doc = result.document

    def page_of(item):
        prov = getattr(item, "prov", None)
        return prov[0].page_no if prov else None

    elements, total_chars = [], 0
    for item, level in doc.iterate_items():
        text = getattr(item, "text", "") or ""
        total_chars += len(text)
        elements.append({"label": str(getattr(item, "label", "unknown")),
                         "page": page_of(item), "level": level, "text": text})

    tables = []
    for idx, table in enumerate(doc.tables):
        df = table.export_to_dataframe()
        tables.append({"table_index": idx, "page": page_of(table),
                       "rows": len(df), "cols": len(df.columns),
                       "markdown": df.to_markdown(index=False),
                       "csv": df.to_csv(index=False)})

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "elements_full.json").write_text(
        json.dumps(elements, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "full_text.md").write_text(doc.export_to_markdown(), encoding="utf-8")

    return {"pages": len(doc.pages), "tables": len(tables),
            "total_chars": total_chars, "status": str(result.status),
            "elements": elements, "tables_data": tables}


# ============================ QUALITY GATE ============================
def quality_flags(parse_result) -> list[str]:
    flags = []
    pages = max(1, parse_result["pages"])
    cpp = parse_result["total_chars"] / pages
    if "SUCCESS" not in parse_result["status"].upper():
        flags.append(f"conversion_status={parse_result['status']}")
    if cpp < MIN_CHARS_PER_PAGE:
        flags.append(f"low_text:{cpp:.0f}_chars_per_page(maybe_scanned)")
    if parse_result["total_chars"] < MIN_TOTAL_CHARS:
        flags.append(f"tiny_doc:{parse_result['total_chars']}_chars")
    if parse_result["tables"] < MIN_TABLES_EXPECTED:
        flags.append(f"no_tables:{parse_result['tables']}")
    return flags


# ============================ STAGE 2: CHUNK + ENRICH ============================
def clean_text(s):
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def is_pseudo_table(t):
    if t["cols"] <= 1:
        return True
    cells = [c.strip() for c in re.split(r"\|", t["markdown"])
             if c.strip() and not set(c.strip()) <= {"-"}]
    if not cells:
        return True
    avg = sum(len(c) for c in cells) / len(cells)
    numeric = sum(1 for c in cells if re.search(r"\d", c))
    return avg > 120 and numeric / len(cells) < 0.2


def llm_json(oa, prompt, max_tokens=400):
    try:
        r = oa.chat.completions.create(
            model=LLM_MODEL, max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}])
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        print(f"      LLM call failed: {e}")
        return None


def chunk_and_enrich(elements, tables, doc_id, oa):
    # doc metadata
    first = " ".join(e["text"] for e in elements
                     if e.get("page") and e["page"] <= 6 and e.get("text"))[:6000]
    meta = llm_json(oa, (
        "Extract metadata from this annual report excerpt. JSON only: "
        '{"company": str, "ticker": str|null, "fiscal_year": int|null, '
        '"currency": str|null, "report_type": str}\n\n' + first)) or {}
    meta["doc_id"] = doc_id

    # section map for table stamping
    page_sections, stack = {}, []
    for e in elements:
        if e["label"] in HEADER_LABELS:
            lvl = e.get("level") or 1
            stack = stack[:max(0, lvl - 1)] + [clean_text(e["text"])]
        if e.get("page"):
            page_sections[e["page"]] = " > ".join(stack)

    # narrative chunks
    chunks, stack, buf, buf_pages, idx = [], [], [], set(), 0
    def flush():
        nonlocal buf, buf_pages, idx
        if not buf:
            return
        crumb = " > ".join(stack)
        body = "\n".join(buf)
        if chunks and n_tokens(body) < MIN_CHUNK_TOKENS:
            chunks[-1]["text"] += "\n" + body
        else:
            chunks.append({"chunk_id": f"{doc_id}::text::{idx:04d}",
                           "content_type": "narrative", "section_path": crumb,
                           "pages": sorted(buf_pages),
                           "text": f"[{crumb}]\n{body}" if crumb else body})
            idx += 1
        buf, buf_pages = [], set()
    for e in elements:
        if e["label"] in DROP_LABELS:
            continue
        if e["label"] in HEADER_LABELS:
            flush()
            lvl = e.get("level") or 1
            stack = stack[:max(0, lvl - 1)] + [clean_text(e["text"])]
        elif e["label"] in TEXT_LABELS and e.get("text"):
            txt = clean_text(e["text"])
            if txt:
                buf.append(txt)
                if e.get("page"):
                    buf_pages.add(e["page"])
                if n_tokens("\n".join(buf)) >= MAX_CHUNK_TOKENS:
                    flush()
    flush()

    # table chunks
    real = [t for t in tables if not is_pseudo_table(t)]
    for i, t in enumerate(real):
        sec = page_sections.get(t["page"], "")
        out = llm_json(oa, (
            "Summarize this financial table in 2-4 sentences for retrieval. State "
            "what it shows, periods, currency/units, key figures. JSON: "
            '{"summary": str}\n\n' + f"SECTION: {sec}\n{t['markdown'][:4000]}'"),
            max_tokens=300)
        summary = (out or {}).get("summary") or f"Table p.{t['page']}: {sec}"
        chunks.append({"chunk_id": f"{doc_id}::table::{i:04d}",
                       "content_type": "table", "section_path": sec,
                       "pages": [t["page"]] if t["page"] else [],
                       "text": f"[{sec}]\n{summary}" if sec else summary,
                       "raw_table": t["markdown"]})
    for c in chunks:
        c["doc_metadata"] = meta
    return chunks, meta, len(real)


# ============================ STAGE 3: INDEX ============================
MAPPING = {
    "settings": {"index": {"knn": True}, "number_of_shards": 1,
                 "number_of_replicas": 0},
    "mappings": {"properties": {
        "chunk_id": {"type": "keyword"}, "doc_id": {"type": "keyword"},
        "content_type": {"type": "keyword"},
        "section_path": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
        "pages": {"type": "integer"}, "company": {"type": "keyword"},
        "ticker": {"type": "keyword"}, "fiscal_year": {"type": "integer"},
        "currency": {"type": "keyword"}, "text": {"type": "text"},
        "raw_table": {"type": "text", "index": False},
        "embedding": {"type": "knn_vector", "dimension": EMBED_DIMS,
                      "method": {"name": "hnsw", "engine": "lucene",
                                 "space_type": "cosinesimil",
                                 "parameters": {"m": 16, "ef_construction": 128}}}}}}


def index_chunks(chunks, oa):
    from opensearchpy import OpenSearch, helpers
    os_client = OpenSearch(hosts=[{"host": OS_HOST, "port": 9200}], use_ssl=False)
    if not os_client.indices.exists(index=INDEX):
        os_client.indices.create(index=INDEX, body=MAPPING)
    actions = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i:i + EMBED_BATCH]
        vecs = oa.embeddings.create(model=EMBED_MODEL,
                                    input=[c["text"] for c in batch]).data
        for c, d in zip(batch, vecs):
            m = c.get("doc_metadata", {})
            actions.append({"_index": INDEX, "_id": c["chunk_id"], "_source": {
                "chunk_id": c["chunk_id"], "doc_id": m.get("doc_id"),
                "content_type": c["content_type"],
                "section_path": c.get("section_path", ""),
                "pages": c.get("pages", []), "company": m.get("company"),
                "ticker": m.get("ticker"), "fiscal_year": m.get("fiscal_year"),
                "currency": m.get("currency"), "text": c["text"],
                "raw_table": c.get("raw_table"), "embedding": d.embedding}})
        helpers.bulk(os_client, actions)
        actions = []
    os_client.indices.refresh(index=INDEX)


# ============================ ORCHESTRATION ============================
def ingest_one(conn, pdf_path: Path, args):
    doc_id = pdf_path.stem.lower().replace(" ", "_")
    fhash = file_hash(pdf_path)
    existing = registry_get(conn, doc_id)

    if existing and existing["stage"] == "INDEXED" and \
       existing["file_hash"] == fhash and not args.force:
        print(f"[skip] {doc_id} already INDEXED (use --force to re-ingest)")
        return

    out_dir = OUTPUT_ROOT / doc_id
    config = {"ocr": args.ocr, "model": LLM_MODEL, "embed": EMBED_MODEL}
    print(f"\n=== Ingesting {pdf_path.name} (doc_id={doc_id}) ===")

    try:
        from openai import OpenAI
        oa = OpenAI()

        # Stage 1: parse
        print("[1/3] Parsing (Docling)...")
        t0 = time.time()
        pr = parse_pdf(pdf_path, out_dir, args.ocr)
        print(f"      {pr['pages']}p, {pr['tables']} tables, "
              f"{pr['total_chars']} chars in {time.time()-t0:.0f}s")

        # Quality gate
        flags = quality_flags(pr)
        registry_upsert(conn, doc_id, source_path=str(pdf_path), file_hash=fhash,
                        stage="PARSED", pages=pr["pages"], tables=pr["tables"],
                        total_chars=pr["total_chars"], flags=json.dumps(flags),
                        config=json.dumps(config), error=None)
        if flags:
            print(f"      QUALITY FLAGS: {flags}")
            if not args.force:
                registry_upsert(conn, doc_id, stage="FLAGGED")
                print(f"      -> FLAGGED, halting. Review, then rerun "
                      f"(--ocr if scanned, --force to override).")
                return

        # Stage 2: chunk + enrich
        print("[2/3] Chunking + enriching...")
        chunks, meta, n_real = chunk_and_enrich(pr["elements"], pr["tables_data"],
                                                doc_id, oa)
        (out_dir / "chunks.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks),
            encoding="utf-8")
        registry_upsert(conn, doc_id, stage="CHUNKED", chunks=len(chunks),
                        company=meta.get("company"),
                        fiscal_year=meta.get("fiscal_year"))
        print(f"      {len(chunks)} chunks ({n_real} real tables) | "
              f"{meta.get('company')} FY{meta.get('fiscal_year')}")

        if args.skip_index:
            print("      --skip-index set, stopping before indexing.")
            return

        # Stage 3: index
        print("[3/3] Embedding + indexing...")
        index_chunks(chunks, oa)
        registry_upsert(conn, doc_id, stage="INDEXED")
        print(f"      DONE -> INDEXED")

    except Exception as e:
        registry_upsert(conn, doc_id, stage="FAILED", error=str(e)[:500])
        print(f"      FAILED: {e}")
        traceback.print_exc()


def show_status(conn, only_flagged=False):
    q = "SELECT doc_id, stage, company, fiscal_year, pages, tables, chunks, flags " \
        "FROM documents"
    if only_flagged:
        q += " WHERE stage IN ('FLAGGED','FAILED')"
    q += " ORDER BY updated_at DESC"
    rows = conn.execute(q).fetchall()
    if not rows:
        print("Registry empty." if not only_flagged else "No flagged/failed docs.")
        return
    print(f"\n{'doc_id':<22}{'stage':<10}{'company':<28}{'FY':<6}"
          f"{'pg':<5}{'tbl':<5}{'chk':<6}flags")
    print("-" * 100)
    for r in rows:
        flags = json.loads(r[7] or "[]")
        flag_str = ("⚠ " + ";".join(flags))[:30] if flags else ""
        print(f"{r[0]:<22}{r[1]:<10}{(r[2] or '')[:27]:<28}{str(r[3] or ''):<6}"
              f"{str(r[4] or ''):<5}{str(r[5] or ''):<5}{str(r[6] or ''):<6}{flag_str}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="*", type=Path)
    ap.add_argument("--force", action="store_true", help="re-ingest / override flags")
    ap.add_argument("--ocr", action="store_true", help="enable OCR")
    ap.add_argument("--skip-index", action="store_true", help="parse+chunk only")
    ap.add_argument("--status", action="store_true", help="show registry")
    ap.add_argument("--flagged", action="store_true", help="show flagged/failed only")
    args = ap.parse_args()

    conn = registry_connect()
    if args.status or args.flagged:
        show_status(conn, only_flagged=args.flagged)
        return
    if not args.pdfs:
        ap.print_help()
        return
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set.")
        sys.exit(1)

    for pdf in args.pdfs:
        if not pdf.exists():
            print(f"[skip] not found: {pdf}")
            continue
        ingest_one(conn, pdf, args)

    print("\n--- Final registry ---")
    show_status(conn)


if __name__ == "__main__":
    main()
