"""
Stage 2: Chunking + Enrichment — annual report RAG pipeline
===========================================================
Consumes Stage 1 (Docling) artifacts:
    parsed_output/<doc>/elements_full.json   -> narrative chunks
    parsed_output/<doc>/tables.json          -> table chunks (LLM-summarized)

Produces:
    parsed_output/<doc>/chunks.jsonl         -> ready for embedding + indexing
    parsed_output/<doc>/doc_metadata.json    -> company/year/currency (LLM-extracted)

Setup:
    pip install openai tiktoken
    set OPENAI_API_KEY=sk-...        (Windows)   export OPENAI_API_KEY=... (Linux)

Run:
    python chunk_enrich.py parsed_output/osea            # full run with LLM calls
    python chunk_enrich.py parsed_output/osea --no-llm   # dry run, no API cost
"""
import os
import sys
import json
import html
import re
import argparse
import hashlib
from pathlib import Path

# ----------------------------- config -----------------------------
LLM_MODEL = "gpt-4o-mini"          # swap for the current mini-tier model if newer
MAX_CHUNK_TOKENS = 700             # target narrative chunk size
MIN_CHUNK_TOKENS = 80              # merge tiny trailing chunks into previous
DROP_LABELS = {"page_footer", "page_header", "footnote"}  # footnotes: see note below
TEXT_LABELS = {"text", "list_item", "caption"}
HEADER_LABELS = {"section_header", "title"}

try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")
    def n_tokens(s: str) -> int:
        return len(_enc.encode(s))
except ImportError:
    def n_tokens(s: str) -> int:
        return max(1, len(s) // 4)  # rough fallback


# ----------------------------- helpers -----------------------------
def clean_text(s: str) -> str:
    s = html.unescape(s)                      # &lt;IR&gt; -> <IR>
    s = re.sub(r"\s+", " ", s).strip()
    return s


def chunk_id(doc_id: str, kind: str, idx: int) -> str:
    return f"{doc_id}::{kind}::{idx:04d}"


def is_pseudo_table(table: dict) -> bool:
    """Designed text panels misclassified as tables: 1 column, or prose-dominant cells."""
    if table["cols"] <= 1:
        return True
    # prose heuristic: average cell length very high & few numeric cells
    cells = re.split(r"\|", table["markdown"])
    cells = [c.strip() for c in cells if c.strip() and not set(c.strip()) <= {"-"}]
    if not cells:
        return True
    avg_len = sum(len(c) for c in cells) / len(cells)
    numeric = sum(1 for c in cells if re.search(r"\d", c))
    return avg_len > 120 and numeric / len(cells) < 0.2


# ----------------------------- LLM calls -----------------------------
def get_client():
    from openai import OpenAI
    return OpenAI()  # reads OPENAI_API_KEY


def llm_json(client, prompt: str, max_tokens: int = 500) -> dict | None:
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"      LLM call failed: {e}")
        return None


def extract_doc_metadata(client, elements: list[dict]) -> dict:
    first_pages = " ".join(
        e["text"] for e in elements
        if e.get("page") and e["page"] <= 6 and e.get("text")
    )[:6000]
    fallback = {"company": None, "ticker": None, "fiscal_year_end": None,
                "currency": None, "report_type": "annual_report"}
    if client is None:
        return fallback
    out = llm_json(client, (
        "From this annual report excerpt, extract metadata. Respond ONLY with JSON: "
        '{"company": str, "ticker": str|null, "fiscal_year_end": "YYYY-MM-DD"|null, '
        '"fiscal_year": int|null, "currency": str|null, "report_type": str}\n\n'
        f"EXCERPT:\n{first_pages}"
    ))
    return out or fallback


def summarize_table(client, table: dict, section_path: str) -> str:
    md = table["markdown"][:4000]  # cap very large tables
    if client is None:
        return f"[DRY RUN] Table on page {table['page']} in section '{section_path}' " \
               f"({table['rows']}x{table['cols']})."
    out = llm_json(client, (
        "Summarize this financial table from an annual report in 2-4 sentences for "
        "search retrieval. State WHAT the table shows, the PERIODS covered, the "
        "CURRENCY/UNITS if visible, and 2-3 key figures or trends. "
        'Respond ONLY with JSON: {"summary": str}\n\n'
        f"SECTION: {section_path}\nTABLE:\n{md}"
    ), max_tokens=300)
    return (out or {}).get("summary") or f"Table on page {table['page']}: {section_path}"


# ----------------------------- chunking -----------------------------
def build_page_section_map(elements: list[dict]) -> dict[int, str]:
    """Last active section breadcrumb per page — used to stamp tables."""
    page_map, stack = {}, []
    for e in elements:
        if e["label"] in HEADER_LABELS:
            level = e.get("level") or 1
            stack = stack[: max(0, level - 1)]
            stack.append(clean_text(e["text"]))
        if e.get("page"):
            page_map[e["page"]] = " > ".join(stack) if stack else ""
    return page_map


def chunk_narrative(elements: list[dict], doc_id: str) -> list[dict]:
    chunks, stack, buf, buf_pages, idx = [], [], [], set(), 0

    def flush():
        nonlocal buf, buf_pages, idx
        if not buf:
            return
        breadcrumb = " > ".join(stack)
        body = "\n".join(buf)
        text = (f"[{breadcrumb}]\n{body}" if breadcrumb else body)
        if chunks and n_tokens(body) < MIN_CHUNK_TOKENS:
            chunks[-1]["text"] += "\n" + body          # merge tiny tail
            chunks[-1]["pages"] = sorted(set(chunks[-1]["pages"]) | buf_pages)
        else:
            chunks.append({
                "chunk_id": chunk_id(doc_id, "text", idx),
                "content_type": "narrative",
                "section_path": breadcrumb,
                "pages": sorted(buf_pages),
                "text": text,
            })
            idx += 1
        buf, buf_pages = [], set()

    for e in elements:
        label = e["label"]
        if label in DROP_LABELS:
            continue
        if label in HEADER_LABELS:
            flush()
            level = e.get("level") or 1
            stack = stack[: max(0, level - 1)]
            stack.append(clean_text(e["text"]))
            continue
        if label in TEXT_LABELS and e.get("text"):
            txt = clean_text(e["text"])
            if not txt:
                continue
            buf.append(txt)
            if e.get("page"):
                buf_pages.add(e["page"])
            if n_tokens("\n".join(buf)) >= MAX_CHUNK_TOKENS:
                flush()
    flush()
    return chunks


def chunk_tables(tables: list[dict], page_sections: dict, doc_id: str,
                 client) -> tuple[list[dict], int]:
    chunks, pseudo_count = [], 0
    real = []
    for t in tables:
        if is_pseudo_table(t):
            pseudo_count += 1
        else:
            real.append(t)
    print(f"      {len(real)} real tables, {pseudo_count} pseudo-tables filtered")
    for i, t in enumerate(real):
        section = page_sections.get(t["page"], "")
        print(f"      summarizing table {i+1}/{len(real)} (page {t['page']}) ...",
              end="\r")
        summary = summarize_table(client, t, section)
        chunks.append({
            "chunk_id": chunk_id(doc_id, "table", i),
            "content_type": "table",
            "section_path": section,
            "pages": [t["page"]] if t["page"] else [],
            "text": (f"[{section}]\n{summary}" if section else summary),
            "raw_table": t["markdown"],          # injected at query time, not embedded
        })
    print()
    return chunks, pseudo_count


# ----------------------------- main -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_dir", type=Path, help="e.g. parsed_output/osea")
    ap.add_argument("--no-llm", action="store_true",
                    help="dry run: skip OpenAI calls (placeholder summaries)")
    args = ap.parse_args()

    elements = json.loads((args.doc_dir / "elements_full.json").read_text(encoding="utf-8"))
    tables = json.loads((args.doc_dir / "tables.json").read_text(encoding="utf-8"))
    doc_id = args.doc_dir.name

    client = None
    if not args.no_llm:
        if not os.environ.get("OPENAI_API_KEY"):
            print("OPENAI_API_KEY not set. Use --no-llm for a dry run.")
            sys.exit(1)
        client = get_client()

    print("[1/4] Extracting doc-level metadata ...")
    doc_meta = extract_doc_metadata(client, elements)
    doc_meta["doc_id"] = doc_id
    print(f"      {json.dumps(doc_meta)}")

    print("[2/4] Chunking narrative ...")
    narrative = chunk_narrative(elements, doc_id)
    print(f"      {len(narrative)} narrative chunks")

    print("[3/4] Filtering + summarizing tables ...")
    page_sections = build_page_section_map(elements)
    table_chunks, _ = chunk_tables(tables, page_sections, doc_id, client)

    print("[4/4] Writing chunks.jsonl ...")
    all_chunks = narrative + table_chunks
    for c in all_chunks:
        c["doc_metadata"] = doc_meta
    out = args.doc_dir / "chunks.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (args.doc_dir / "doc_metadata.json").write_text(
        json.dumps(doc_meta, indent=2), encoding="utf-8")

    sizes = [n_tokens(c["text"]) for c in all_chunks]
    print(f"\nDone: {len(all_chunks)} chunks "
          f"({len(narrative)} narrative + {len(table_chunks)} table) -> {out}")
    print(f"Token sizes: min {min(sizes)}, median {sorted(sizes)[len(sizes)//2]}, "
          f"max {max(sizes)}")


if __name__ == "__main__":
    main()
