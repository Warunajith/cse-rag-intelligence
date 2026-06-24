"""
Annual Report Full Extraction (Docling) — see everything the parser gets
========================================================================
Extracts ALL text + tables + pictures, dumps everything for inspection.

Setup:
    pip install docling pandas       # first run downloads ~500MB of models

Run:
    python extract_all_docling.py path/to/annual_report.pdf

Output: ./parsed_output/<pdf_name>/
    full_text.md        - COMPLETE text export, reading order preserved
    elements_full.json  - every element: label, page, FULL untruncated text
    tables.json         - all tables (markdown + CSV via TableFormer)
    tables_preview.md   - human-readable dump of every table
    pictures/           - every detected chart/image saved as PNG
    pictures_index.json - picture inventory: page, size, saved path
    summary.json        - statistics: what was extracted, by element type
    docling_doc.json    - full structured document (production artifact)
"""
import sys
import json
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption


def build_converter() -> DocumentConverter:
    opts = PdfPipelineOptions(
        do_ocr=True,                    # auto-skipped on native-text pages
        do_table_structure=True,
        generate_picture_images=True,   # extract charts/images as PNGs
        images_scale=2.0,               # 2x resolution -> readable chart labels
    )
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def page_of(item) -> int | None:
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else None


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_all_docling.py <annual_report.pdf>")
        sys.exit(1)
    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    out_dir = Path("parsed_output") / pdf_path.stem
    pic_dir = out_dir / "pictures"
    out_dir.mkdir(parents=True, exist_ok=True)
    pic_dir.mkdir(exist_ok=True)

    print(f"Converting {pdf_path.name} (this can take a few minutes on CPU)...")
    t0 = time.time()
    result = build_converter().convert(pdf_path)
    doc = result.document
    print(f"Converted in {time.time() - t0:.0f}s | "
          f"{len(doc.pages)} pages | status: {result.status}")

    # ---- 1. ALL ELEMENTS, FULL TEXT (no truncation) ----
    elements, label_counts, total_chars = [], {}, 0
    for item, level in doc.iterate_items():
        label = str(getattr(item, "label", "unknown"))
        label_counts[label] = label_counts.get(label, 0) + 1
        text = getattr(item, "text", "") or ""
        total_chars += len(text)
        elements.append({
            "label": label,
            "page": page_of(item),
            "level": level,
            "text": text,            # FULL text, untruncated
        })
    (out_dir / "elements_full.json").write_text(
        json.dumps(elements, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- 2. COMPLETE TEXT EXPORT ----
    (out_dir / "full_text.md").write_text(
        doc.export_to_markdown(), encoding="utf-8")

    # ---- 3. ALL TABLES ----
    tables, tables_md = [], ["# Extracted Tables\n"]
    for idx, table in enumerate(doc.tables):
        df = table.export_to_dataframe()
        md = df.to_markdown(index=False)
        tables.append({
            "table_index": idx,
            "page": page_of(table),
            "rows": len(df),
            "cols": len(df.columns),
            "markdown": md,
            "csv": df.to_csv(index=False),
        })
        tables_md.append(f"\n## Table {idx} (page {page_of(table)}, "
                         f"{len(df)}x{len(df.columns)})\n\n{md}\n")
    (out_dir / "tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "tables_preview.md").write_text(
        "\n".join(tables_md), encoding="utf-8")

    # ---- 4. ALL PICTURES (charts, infographics, photos) ----
    pictures = []
    for idx, pic in enumerate(doc.pictures):
        entry = {"picture_index": idx, "page": page_of(pic), "saved": None}
        try:
            img = pic.get_image(doc)  # PIL image
            if img is not None:
                fname = f"page{page_of(pic) or 0:03d}_pic{idx:03d}.png"
                img.save(pic_dir / fname)
                entry["saved"] = f"pictures/{fname}"
                entry["size"] = list(img.size)
        except Exception as e:
            entry["error"] = str(e)
        pictures.append(entry)
    (out_dir / "pictures_index.json").write_text(
        json.dumps(pictures, indent=2), encoding="utf-8")

    # ---- 5. FULL STRUCTURED DOC (production artifact -> MinIO later) ----
    (out_dir / "docling_doc.json").write_text(
        json.dumps(doc.export_to_dict(), indent=2, default=str), encoding="utf-8")

    # ---- 6. SUMMARY ----
    summary = {
        "file": pdf_path.name,
        "pages": len(doc.pages),
        "conversion_status": str(result.status),
        "element_counts_by_label": label_counts,
        "total_text_characters": total_chars,
        "tables_extracted": len(tables),
        "pictures_extracted": len(pictures),
        "pictures_saved": sum(1 for p in pictures if p["saved"]),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nAll artifacts in: {out_dir.resolve()}")
    print("To SEE all extracted text -> open full_text.md")
    print("To audit per-element       -> elements_full.json")
    print("To check table quality     -> tables_preview.md (vs the PDF)")
    print("To see what text was MISSED-> open pictures/ (text inside these")
    print("                              images is NOT in full_text.md)")


if __name__ == "__main__":
    main()
