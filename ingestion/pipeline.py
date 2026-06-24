"""
Reusable indexing pipeline — download -> parse -> gate -> chunk -> enrich -> index
for a SINGLE report identified by doc_id (a row already in `documents`, typically
status AVAILABLE/QUEUED). Updates Postgres status throughout.

Entry point: index_one_report(conn, mc, oa, doc_id) -> final status string.

This is the heavy worker logic, factored out so both the worker and any CLI can
call it. Mirrors the validated logic from orchestrate.py.
"""
import os
import json
import html
import re
import traceback
from datetime import datetime, timezone

from infra import doc_upsert, doc_get, put_pdf
import cse_client as cse

LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-large")
EMBED_DIMS = 3072
INDEX = os.environ.get("OS_INDEX", "annual-reports")
OS_HOST = os.environ.get("OPENSEARCH_HOST", "localhost")
OS_PORT = int(os.environ.get("OPENSEARCH_PORT", "9200"))

MAX_CHUNK_TOKENS = 700
MIN_CHUNK_TOKENS = 80
EMBED_BATCH = 64
MIN_CHARS_PER_PAGE = 200
MIN_TABLES_EXPECTED = 1
MIN_TOTAL_CHARS = 5000

DROP_LABELS = {"page_footer", "page_header"}
TEXT_LABELS = {"text", "list_item", "caption"}
HEADER_LABELS = {"section_header", "title"}

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def n_tokens(s): return len(_enc.encode(s))
except ImportError:
    def n_tokens(s): return max(1, len(s) // 4)


def parse_pdf_bytes(pdf_bytes, tmp_path):
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption
    with open(tmp_path, "wb") as f:
        f.write(pdf_bytes)
    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True,
                              generate_picture_images=False, images_scale=1.0)
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    conv = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opts,
                                         backend=PyPdfiumDocumentBackend)})
    result = conv.convert(tmp_path)
    doc = result.document

    def page_of(it):
        p = getattr(it, "prov", None)
        return p[0].page_no if p else None

    elements, total = [], 0
    for it, lvl in doc.iterate_items():
        t = getattr(it, "text", "") or ""
        total += len(t)
        elements.append({"label": str(getattr(it, "label", "unknown")),
                         "page": page_of(it), "level": lvl, "text": t})
    tables = []
    for idx, tb in enumerate(doc.tables):
        df = tb.export_to_dataframe()
        tables.append({"table_index": idx, "page": page_of(tb),
                       "rows": len(df), "cols": len(df.columns),
                       "markdown": df.to_markdown(index=False)})
    return {"pages": len(doc.pages), "tables": len(tables), "total_chars": total,
            "status": str(result.status), "elements": elements, "tables_data": tables}


def quality_flags(pr):
    f, pages = [], max(1, pr["pages"])
    cpp = pr["total_chars"] / pages
    if "SUCCESS" not in pr["status"].upper():
        f.append(f"conversion_status={pr['status']}")
    if cpp < MIN_CHARS_PER_PAGE:
        f.append(f"low_text:{cpp:.0f}_cpp")
    if pr["total_chars"] < MIN_TOTAL_CHARS:
        f.append(f"tiny_doc:{pr['total_chars']}")
    if pr["tables"] < MIN_TABLES_EXPECTED:
        f.append(f"no_tables:{pr['tables']}")
    return f


def clean_text(s): return re.sub(r"\s+", " ", html.unescape(s)).strip()


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
        r = oa.chat.completions.create(model=LLM_MODEL, max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}])
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        print(f"      LLM failed: {e}")
        return None


def chunk_and_enrich(elements, tables, doc_id, symbol, year, name, oa):
    first = " ".join(e["text"] for e in elements
                     if e.get("page") and e["page"] <= 6 and e.get("text"))[:6000]
    meta = llm_json(oa, ("Extract metadata from this annual report excerpt. JSON "
        'only: {"currency": str|null, "report_type": str}\n\n' + first)) or {}
    meta.update({"doc_id": doc_id, "company": name, "ticker": symbol,
                 "fiscal_year": year})

    page_sections, stack = {}, []
    for e in elements:
        if e["label"] in HEADER_LABELS:
            lvl = e.get("level") or 1
            stack = stack[:max(0, lvl - 1)] + [clean_text(e["text"])]
        if e.get("page"):
            page_sections[e["page"]] = " > ".join(stack)

    chunks, stack, buf, buf_pages, idx = [], [], [], set(), 0
    def flush():
        nonlocal buf, buf_pages, idx
        if not buf:
            return
        crumb = " > ".join(stack); body = "\n".join(buf)
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
            flush(); lvl = e.get("level") or 1
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

    real = [t for t in tables if not is_pseudo_table(t)]
    for i, t in enumerate(real):
        sec = page_sections.get(t["page"], "")
        out = llm_json(oa, ("Summarize this financial table in 2-4 sentences for "
            "retrieval. State what it shows, periods, currency/units, key figures. "
            'JSON: {"summary": str}\n\n' + f"SECTION: {sec}\n{t['markdown'][:4000]}"),
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


MAPPING = {"settings": {"index": {"knn": True}, "number_of_shards": 1,
        "number_of_replicas": 0},
    "mappings": {"properties": {
        "chunk_id": {"type": "keyword"}, "doc_id": {"type": "keyword"},
        "content_type": {"type": "keyword"},
        "section_path": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
        "pages": {"type": "integer"}, "company": {"type": "keyword"},
        "ticker": {"type": "keyword"}, "fiscal_year": {"type": "integer"},
        "currency": {"type": "keyword"}, "report_type": {"type": "keyword"},
        "text": {"type": "text"}, "raw_table": {"type": "text", "index": False},
        "embedding": {"type": "knn_vector", "dimension": EMBED_DIMS,
            "method": {"name": "hnsw", "engine": "lucene",
                "space_type": "cosinesimil",
                "parameters": {"m": 16, "ef_construction": 128}}}}}}


def index_chunks(chunks, oa):
    from opensearchpy import OpenSearch, helpers
    os_client = OpenSearch(hosts=[{"host": OS_HOST, "port": OS_PORT}], use_ssl=False)
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
                "currency": m.get("currency"), "report_type": "annual",
                "text": c["text"], "raw_table": c.get("raw_table"),
                "embedding": d.embedding}})
        helpers.bulk(os_client, actions)
        actions = []
    os_client.indices.refresh(index=INDEX)


def index_one_report(conn, mc, oa, doc_id: str) -> str:
    """Full pipeline for one report row. Returns final status."""
    row = doc_get(conn, doc_id)
    if not row:
        raise ValueError(f"doc_id {doc_id} not in documents table")
    if row["status"] == "INDEXED":
        return "INDEXED"  # idempotent

    symbol, name = row["symbol"], row["company_name"]
    year, pdf_url = row["fiscal_year"], row["pdf_url"]
    doc_upsert(conn, doc_id, status="PROCESSING")
    print(f"[{doc_id}] PROCESSING ({name} FY{year})")

    try:
        pdf = cse.download_pdf(pdf_url)
        fhash = cse.sha16(pdf)
        object_key = f"{doc_id}.pdf"
        put_pdf(mc, object_key, pdf)
        doc_upsert(conn, doc_id, pdf_object=object_key, file_hash=fhash)
        print(f"[{doc_id}] downloaded {len(pdf)//1024}KB")

        pr = parse_pdf_bytes(pdf, f"/tmp/{doc_id}.pdf")
        flags = quality_flags(pr)
        doc_upsert(conn, doc_id, pages=pr["pages"], tables=pr["tables"],
                   total_chars=pr["total_chars"], flags=flags)
        if flags:
            doc_upsert(conn, doc_id, status="FLAGGED")
            print(f"[{doc_id}] FLAGGED {flags}")
            return "FLAGGED"

        chunks, meta, n_real = chunk_and_enrich(
            pr["elements"], pr["tables_data"], doc_id, symbol, year, name, oa)
        doc_upsert(conn, doc_id, chunks=len(chunks))
        index_chunks(chunks, oa)
        doc_upsert(conn, doc_id, status="INDEXED",
                   indexed_at=datetime.now(timezone.utc))
        print(f"[{doc_id}] INDEXED ({len(chunks)} chunks)")
        return "INDEXED"
    except Exception as e:
        doc_upsert(conn, doc_id, status="FAILED", error=str(e)[:500])
        print(f"[{doc_id}] FAILED: {e}")
        traceback.print_exc()
        return "FAILED"
