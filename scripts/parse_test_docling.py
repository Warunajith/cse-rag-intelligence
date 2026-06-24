"""
Annual Report Parse Test (Docling edition) — Stage 1 of RAG ingestion pipeline
==============================================================================
Stages: load & convert → element inventory → headings → tables → metadata → artifacts

Setup:
    pip install docling pandas       # (or: uv pip install docling pandas)
    # First run downloads model weights (~500MB), cached afterward.

Run:
    python parse_test_docling.py path/to/annual_report.pdf

Output:
    ./parsed_output/<pdf_name>/
        summary.json       - parse statistics & doc-level metadata
        headings.json      - section headers detected by the layout model
        elements.json      - every element with semantic label + page provenance
        tables.json        - tables with markdown + CSV rendering (TableFormer)
        full_text.md       - Docling's native markdown export
        docling_doc.json   - complete structured document (the production artifact)
"""
import sys
import json
import re
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel


def build_converter() -> DocumentConverter:
    """Configure the PDF pipeline.

    - do_ocr=True: Docling auto-skips OCR on native-text pages, runs it only
      on scanned pages. Safe default for annual reports.
    - TableFormerMode.ACCURATE: slower but the right choice for financial
      statements where cell-level accuracy is non-negotiable.
    """
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
    )
    pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
    pipeline_options.table_structure_options.do_cell_matching = True

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def get_page_no(item) -> int | None:
    """Page provenance for any doc item (1-indexed)."""
    if getattr(item, "prov", None):
        return item.prov[0].page_no
    return None


def inventory_elements(doc) -> tuple[list[dict], dict]:
    """Walk every element; record its semantic label, page, and text."""
    elements, label_counts = [], {}
    for item, _level in doc.iterate_items():
        label = str(item.label) if hasattr(item, "label") else "unknown"
        label_counts[label] = label_counts.get(label, 0) + 1
        text = getattr(item, "text", "") or ""
        elements.append(
            {
                "label": label,
                "page": get_page_no(item),
                "text": text[:300],  # truncate for the inventory file
            }
        )
    return elements, label_counts


def extract_headings(doc) -> list[dict]:
    """Section headers as classified by Docling's layout model — these become
    the chunk boundaries in the next pipeline stage."""
    headings = []
    for item, level in doc.iterate_items():
        if getattr(item, "label", None) in (
            DocItemLabel.SECTION_HEADER,
            DocItemLabel.TITLE,
        ):
            headings.append(
                {
                    "page": get_page_no(item),
                    "level": level,
                    "text": item.text,
                }
            )
    return headings


def extract_tables(doc) -> list[dict]:
    """TableFormer output: each table as markdown + CSV, with page provenance.

    In the production pipeline, the markdown here is what gets summarized by
    an LLM for embedding, while the raw form is stored for context injection.
    """
    tables = []
    for t_idx, table in enumerate(doc.tables):
        df = table.export_to_dataframe()
        tables.append(
            {
                "table_index": t_idx,
                "page": get_page_no(table),
                "rows": len(df),
                "cols": len(df.columns),
                "markdown": df.to_markdown(index=False),
                "csv": df.to_csv(index=False),
            }
        )
    return tables


def extract_doc_metadata(doc) -> dict:
    """Heuristic doc-level metadata from the first pages.
    Production version: one LLM call returning structured JSON
    {company, fiscal_year, currency, report_type, filing_date}."""
    first_texts = []
    for item, _ in doc.iterate_items():
        page = get_page_no(item)
        if page is not None and page <= 5 and getattr(item, "text", None):
            first_texts.append(item.text)
    blob = "\n".join(first_texts)
    years = re.findall(r"\b(20[12]\d)\b", blob)
    return {
        "candidate_fiscal_years": sorted(set(years), reverse=True)[:4],
        "first_pages_preview": blob[:500],
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_test_docling.py <path-to-annual-report.pdf>")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    out_dir = Path("parsed_output") / pdf_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] Converting {pdf_path.name} with Docling "
          f"(TableFormer ACCURATE, auto-OCR) ... this can take a few minutes")
    t0 = time.time()
    converter = build_converter()
    result = converter.convert(pdf_path)
    doc = result.document
    elapsed = time.time() - t0

    report = {
        "file": pdf_path.name,
        "conversion_status": str(result.status),
        "conversion_seconds": round(elapsed, 1),
        "pages": len(doc.pages),
    }
    print(f"      done in {elapsed:.0f}s | {report['pages']} pages | "
          f"status: {report['conversion_status']}")

    print("[2/5] Building element inventory ...")
    elements, label_counts = inventory_elements(doc)
    report["element_counts_by_label"] = label_counts
    print(f"      {json.dumps(label_counts)}")

    print("[3/5] Extracting headings (layout-model classified) ...")
    headings = extract_headings(doc)
    report["headings_detected"] = len(headings)
    print(f"      {len(headings)} section headers")

    print("[4/5] Extracting tables (TableFormer) ...")
    tables = extract_tables(doc)
    report["tables_extracted"] = len(tables)
    print(f"      {len(tables)} tables")

    report.update(extract_doc_metadata(doc))
    print(f"      fiscal year candidates: {report['candidate_fiscal_years']}")

    print("[5/5] Writing artifacts ...")
    (out_dir / "summary.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    (out_dir / "headings.json").write_text(
        json.dumps(headings, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "elements.json").write_text(
        json.dumps(elements, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "full_text.md").write_text(
        doc.export_to_markdown(), encoding="utf-8")
    # Full structured doc — this is what you'd persist to MinIO in production
    (out_dir / "docling_doc.json").write_text(
        json.dumps(doc.export_to_dict(), indent=2, default=str), encoding="utf-8")

    print(f"\nDone. Inspect artifacts in: {out_dir.resolve()}")
    print("Check tables.json first — compare a few financial statements against")
    print("the PDF. Then skim headings.json: that's your chunking skeleton.")


if __name__ == "__main__":
    main()
