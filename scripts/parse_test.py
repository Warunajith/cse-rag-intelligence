"""
Annual Report Parse Test — Stage 1 of RAG ingestion pipeline
=============================================================
Stages: load → inspect → extract text → extract tables → metadata → artifacts

Setup:
    pip install pymupdf pdfplumber          # (or: uv pip install pymupdf pdfplumber)

Run:
    python parse_test.py path/to/annual_report.pdf

Output:
    ./parsed_output/<pdf_name>/
        summary.json     - parse statistics & doc-level metadata
        headings.json    - detected section headings (your future chunk boundaries)
        blocks.json      - all text blocks with font info, per page
        tables.json      - extracted tables with markdown rendering
        full_text.md     - human-readable dump, page by page

NOTE: This uses PyMuPDF + pdfplumber for a fast smoke test. In the production
Docker stack, swap stages 2-3 for Docling — the artifact structure stays the same.
"""
import sys
import json
import re
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber


def load_and_inspect(doc: fitz.Document) -> dict:
    """Stage 1: Basic inspection — page count, metadata, scanned-vs-native check."""
    text_lengths = [len(doc[i].get_text("text").strip()) for i in range(len(doc))]
    native_pages = sum(1 for t in text_lengths if t > 50)
    return {
        "pages": len(doc),
        "pdf_metadata": {k: v for k, v in doc.metadata.items() if v},
        "native_text_pages": native_pages,
        "likely_scanned_pages": len(doc) - native_pages,
        "needs_ocr": native_pages < len(doc) * 0.8,
    }


def extract_text_blocks(doc: fitz.Document) -> list[dict]:
    """Stage 2: Block-level text extraction with font size + bold flags.

    Font info is what lets us detect headings heuristically, which later
    becomes the section hierarchy for structure-aware chunking.
    """
    pages_out = []
    for i, page in enumerate(doc):
        blocks = page.get_text("dict")["blocks"]
        page_blocks = []
        for b in blocks:
            if b["type"] != 0:  # 0 = text block, 1 = image
                continue
            spans_text, max_size, bold = [], 0.0, False
            for line in b["lines"]:
                for s in line["spans"]:
                    spans_text.append(s["text"])
                    max_size = max(max_size, s["size"])
                    if s["flags"] & 16:  # bold bit
                        bold = True
            txt = " ".join(spans_text).strip()
            if txt:
                page_blocks.append(
                    {"text": txt, "font_size": round(max_size, 1), "bold": bold}
                )
        pages_out.append({"page": i + 1, "blocks": page_blocks})
    return pages_out


def detect_headings(pages_out: list[dict]) -> tuple[list[dict], float]:
    """Heuristic: a heading is a short block whose font is >=1.3x the body median."""
    all_sizes = [b["font_size"] for p in pages_out for b in p["blocks"]]
    if not all_sizes:
        return [], 10.0
    body_size = sorted(all_sizes)[len(all_sizes) // 2]
    headings = [
        {"page": p["page"], "text": b["text"], "size": b["font_size"]}
        for p in pages_out
        for b in p["blocks"]
        if b["font_size"] >= body_size * 1.3 and len(b["text"]) < 120
    ]
    return headings, body_size


def extract_tables(pdf_path: Path) -> list[dict]:
    """Stage 3: Table extraction via pdfplumber, rendered to markdown.

    fill_density filters out 'tables' that are really just page layout artifacts.
    """
    tables = []
    with pdfplumber.open(pdf_path) as plumb:
        for i, page in enumerate(plumb.pages):
            for t_idx, tbl in enumerate(page.extract_tables()):
                if not tbl or len(tbl) < 2:
                    continue
                n_cols = max(len(r) for r in tbl)
                non_empty = sum(1 for r in tbl for c in r if c and str(c).strip())
                density = non_empty / (len(tbl) * n_cols) if n_cols else 0
                if density < 0.3:
                    continue
                md_rows = []
                for r_i, row in enumerate(tbl):
                    cells = [
                        (str(c).replace("\n", " ").strip() if c else "") for c in row
                    ]
                    md_rows.append("| " + " | ".join(cells) + " |")
                    if r_i == 0:
                        md_rows.append("|" + "---|" * len(cells))
                tables.append(
                    {
                        "page": i + 1,
                        "index_on_page": t_idx,
                        "rows": len(tbl),
                        "cols": n_cols,
                        "fill_density": round(density, 2),
                        "markdown": "\n".join(md_rows),
                    }
                )
    return tables


def extract_doc_metadata(doc: fitz.Document) -> dict:
    """Stage 4: Doc-level metadata, heuristic version.

    In production this becomes ONE LLM call over the first ~5 pages returning
    structured JSON: {company, fiscal_year, currency, report_type, filing_date}.
    """
    first_pages = "\n".join(doc[i].get_text("text") for i in range(min(5, len(doc))))
    years = re.findall(r"\b(20[12]\d)\b", first_pages)
    return {
        "candidate_fiscal_years": sorted(set(years), reverse=True)[:4],
        "first_page_preview": doc[0].get_text("text")[:500],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_test.py <path-to-annual-report.pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    out_dir = Path("parsed_output") / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    report = {"file": pdf_path.name}

    print(f"[1/5] Loading & inspecting {pdf_path.name} ...")
    report.update(load_and_inspect(doc))
    print(f"      {report['pages']} pages | native text: {report['native_text_pages']} "
          f"| needs OCR: {report['needs_ocr']}")

    print("[2/5] Extracting text blocks ...")
    pages_out = extract_text_blocks(doc)
    headings, body_size = detect_headings(pages_out)
    report["body_font_size"] = body_size
    report["headings_detected"] = len(headings)
    print(f"      body font ~{body_size}pt | {len(headings)} headings detected")

    print("[3/5] Extracting tables (this is the slow stage) ...")
    tables = extract_tables(pdf_path)
    report["tables_extracted"] = len(tables)
    print(f"      {len(tables)} tables extracted")

    print("[4/5] Extracting doc-level metadata ...")
    report.update(extract_doc_metadata(doc))
    print(f"      fiscal year candidates: {report['candidate_fiscal_years']}")

    print("[5/5] Writing artifacts ...")
    (out_dir / "summary.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "headings.json").write_text(
        json.dumps(headings, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "blocks.json").write_text(
        json.dumps(pages_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = []
    for p in pages_out:
        md.append(f"\n\n---\n## Page {p['page']}\n")
        for b in p["blocks"]:
            is_heading = b["font_size"] >= body_size * 1.3 and len(b["text"]) < 120
            md.append(("### " if is_heading else "") + b["text"] + "\n")
    (out_dir / "full_text.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\nDone. Inspect artifacts in: {out_dir.resolve()}")
    print("Start with summary.json, then eyeball tables.json — table quality is")
    print("the go/no-go signal for the whole pipeline.")


if __name__ == "__main__":
    main()
