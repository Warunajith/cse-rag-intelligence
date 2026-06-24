"""
Annual Report Extraction (Docling) — memory-safe Windows edition
================================================================
Fixes for `std::bad_alloc` / segfault during preprocessing:
  1. pypdfium2 backend (lighter than default docling-parse)
  2. images_scale=1.0 and picture extraction OFF by default
  3. OCR OFF by default (enable only if your PDF has scanned pages)
  4. page-range support to isolate problem pages

Run (basic, safest):
    python extract_safe_docling.py report.pdf

Isolate a problem page:
    python extract_safe_docling.py report.pdf --pages 4 4

Re-enable features once the basic run works:
    python extract_safe_docling.py report.pdf --pictures
    python extract_safe_docling.py report.pdf --ocr
"""
import sys
import json
import time
import argparse
from pathlib import Path

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption


def build_converter(use_ocr: bool, extract_pictures: bool) -> DocumentConverter:
    opts = PdfPipelineOptions(
        do_ocr=use_ocr,                 # OFF by default — native-text PDFs don't need it
        do_table_structure=True,
        generate_picture_images=extract_pictures,
        images_scale=1.0,               # was 2.0 — halves memory for page rendering
    )
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=opts,
                backend=PyPdfiumDocumentBackend,  # lighter backend, fixes many bad_allocs
            )
        }
    )


def page_of(item):
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", nargs=2, type=int, metavar=("START", "END"),
                    help="only process this page range, e.g. --pages 1 10")
    ap.add_argument("--ocr", action="store_true", help="enable OCR")
    ap.add_argument("--pictures", action="store_true", help="extract images as PNGs")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"File not found: {args.pdf}")
        sys.exit(1)

    out_dir = Path("parsed_output") / args.pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    conv_kwargs = {}
    if args.pages:
        conv_kwargs["page_range"] = (args.pages[0], args.pages[1])
        print(f"Processing only pages {args.pages[0]}-{args.pages[1]}")

    print(f"Converting {args.pdf.name} "
          f"(backend=pypdfium2, ocr={args.ocr}, pictures={args.pictures}) ...")
    t0 = time.time()
    result = build_converter(args.ocr, args.pictures).convert(args.pdf, **conv_kwargs)
    doc = result.document
    print(f"Converted in {time.time() - t0:.0f}s | status: {result.status}")

    # ---- elements, full text ----
    elements, label_counts, total_chars = [], {}, 0
    for item, level in doc.iterate_items():
        label = str(getattr(item, "label", "unknown"))
        label_counts[label] = label_counts.get(label, 0) + 1
        text = getattr(item, "text", "") or ""
        total_chars += len(text)
        elements.append({"label": label, "page": page_of(item),
                         "level": level, "text": text})
    (out_dir / "elements_full.json").write_text(
        json.dumps(elements, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "full_text.md").write_text(
        doc.export_to_markdown(), encoding="utf-8")

    # ---- tables ----
    tables, tables_md = [], ["# Extracted Tables\n"]
    for idx, table in enumerate(doc.tables):
        df = table.export_to_dataframe()
        md = df.to_markdown(index=False)
        tables.append({"table_index": idx, "page": page_of(table),
                       "rows": len(df), "cols": len(df.columns),
                       "markdown": md, "csv": df.to_csv(index=False)})
        tables_md.append(f"\n## Table {idx} (page {page_of(table)}, "
                         f"{len(df)}x{len(df.columns)})\n\n{md}\n")
    (out_dir / "tables.json").write_text(
        json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "tables_preview.md").write_text(
        "\n".join(tables_md), encoding="utf-8")

    # ---- pictures (only if enabled) ----
    if args.pictures:
        pic_dir = out_dir / "pictures"
        pic_dir.mkdir(exist_ok=True)
        pictures = []
        for idx, pic in enumerate(doc.pictures):
            entry = {"picture_index": idx, "page": page_of(pic), "saved": None}
            try:
                img = pic.get_image(doc)
                if img is not None:
                    fname = f"page{page_of(pic) or 0:03d}_pic{idx:03d}.png"
                    img.save(pic_dir / fname)
                    entry["saved"] = f"pictures/{fname}"
            except Exception as e:
                entry["error"] = str(e)
            pictures.append(entry)
        (out_dir / "pictures_index.json").write_text(
            json.dumps(pictures, indent=2), encoding="utf-8")

    summary = {
        "file": args.pdf.name,
        "page_range": args.pages or "all",
        "conversion_status": str(result.status),
        "element_counts_by_label": label_counts,
        "total_text_characters": total_chars,
        "tables_extracted": len(tables),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2),
                                          encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nArtifacts in: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
