#!/usr/bin/env python3
"""
Docling Enhanced PDF Extractor
Role: Advanced PDF understanding for complex tables, charts, and multi-column layouts.

ROUTING RULE — only invoke Docling when at least one of these conditions is true:
  1. Standard pdftotext produces garbled or merged table cells
  2. The target page(s) contain financial tables (segment P&L, capital adequacy, pricing grids)
  3. The target page(s) contain multi-column layouts where text order matters
  4. health_checker.py raises MARGIN_FRAGILITY / LEVERAGE_ESCALATION and you need the exact table

Do NOT invoke for: text-only narrative pages, simple single-column PDFs.
Docling loads layout and table-structure models on first run — unnecessary latency for simple extractions.

Usage:
    python3 skills/docling_extractor.py <pdf_path> [--pages 1-10] [--tables-only] [--output json|markdown]
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

# Threshold below which extracted body text triggers an OCR warning (characters)
_MIN_TEXT_LENGTH = 200

ROUTING_NOTE = (
    "Invoke only for pages with complex tables or multi-column layouts. "
    "Prefer pdftotext for simple text-only PDFs — Docling adds model-load latency without benefit."
)


def parse_page_range(pages_str: str) -> Tuple[int, int]:
    """
    Parse '3-10' or '5' into a 1-indexed (start, end) tuple for Docling.

    Validates:
      - Both start and end must be integers >= 1
      - end must be >= start (no reversed ranges)
    """
    parts = pages_str.split("-")

    def to_positive_int(s: str, label: str) -> int:
        try:
            n = int(s)
        except ValueError:
            raise ValueError(f"{label} must be an integer, got '{s}'.")
        if n < 1:
            raise ValueError(f"{label} must be >= 1, got {n}.")
        return n

    if len(parts) == 2:
        start = to_positive_int(parts[0], "Start page")
        end = to_positive_int(parts[1], "End page")
        if end < start:
            raise ValueError(
                f"End page ({end}) must be >= start page ({start}). Got: '{pages_str}'."
            )
        return (start, end)
    elif len(parts) == 1:
        p = to_positive_int(parts[0], "Page number")
        return (p, p)
    else:
        raise ValueError(f"Invalid page range format: '{pages_str}'. Use '1-10' or '5'.")


def format_page_range(page_range_tuple: Optional[Tuple[int, int]]) -> str:
    """Return a human-readable string like '3-10' or 'all'."""
    if page_range_tuple is None:
        return "all"
    start, end = page_range_tuple
    return f"{start}-{end}" if start != end else str(start)


def extract(pdf_path: str, pages: str = None, tables_only: bool = False) -> dict:
    """
    Extract structured content from a PDF using Docling.

    Args:
        pdf_path:    Path to the PDF file.
        pages:       Optional page range string, e.g. '1-10' or '5'.
                     If None, extracts the full document.
        tables_only: If True, output contains only extracted tables; body text is omitted.

    Returns a dict with:
        text           — full markdown body (empty string when tables_only=True)
        tables         — list of extracted tables as markdown strings
        metadata       — extraction context (file, pages, mode, routing_note, freshness note)
        warnings       — non-fatal issues to surface to downstream consumers
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat
    except ImportError:
        return {
            "error": "Docling not installed. Run: pip install docling",
            "text": "", "tables": [], "metadata": {}, "warnings": []
        }

    path = Path(pdf_path)
    if not path.exists():
        return {
            "error": f"File not found: {pdf_path}",
            "text": "", "tables": [], "metadata": {}, "warnings": []
        }

    # OCR disabled by default — text PDFs only; scanned PDFs will silently produce short output
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options.do_cell_matching = True

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )

    # Resolve page range — Docling expects 1-indexed (start, end) tuple
    page_range_tuple = None
    warnings = []
    if pages:
        try:
            page_range_tuple = parse_page_range(pages)
        except ValueError as e:
            warnings.append(f"Page range parse error: {e}. Extracting full document.")

    convert_kwargs = {}
    if page_range_tuple:
        convert_kwargs["page_range"] = page_range_tuple

    result = converter.convert(str(path), **convert_kwargs)
    doc = result.document

    # Extract tables as markdown strings
    tables = []
    for table in doc.tables:
        try:
            df = table.export_to_dataframe()
            tables.append(df.to_markdown(index=False))
        except Exception as e:
            warnings.append(f"Table export failed: {e}")
            tables.append(str(table))

    # Extract body text
    body_text = "" if tables_only else doc.export_to_markdown()

    # Warning: OCR is off — if body text is suspiciously short, flag scanned PDF risk
    if not tables_only and len(body_text.strip()) < _MIN_TEXT_LENGTH:
        warnings.append(
            "OCR disabled; extracted text is very short — this may be a scanned PDF. "
            "Re-run with do_ocr=True if content appears missing."
        )

    # Warning: tables_only mode but nothing found
    if tables_only and len(tables) == 0:
        warnings.append(
            "No tables found in tables-only mode. "
            "Verify the target page range contains structured tables, or re-run without --tables-only."
        )

    metadata = {
        "file": path.name,
        "pages_requested": pages if pages else "all",
        "page_range_applied": format_page_range(page_range_tuple),
        "pages_extracted": len(doc.pages) if hasattr(doc, "pages") else "unknown",
        "tables_found": len(tables),
        "mode": "tables_only" if tables_only else "full",
        "extracted_at": datetime.now().isoformat(),
        "tool": f"docling-{_docling_version()}",
        "routing_note": ROUTING_NOTE,
        "freshness_note": "Extracted from local file — content date depends on source document, not extraction date."
    }

    return {
        "text": body_text,
        "tables": tables,
        "metadata": metadata,
        "warnings": warnings
    }


def _docling_version() -> str:
    try:
        from importlib.metadata import version
        return version("docling")
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(
        description="Docling Enhanced PDF Extractor — structured extraction for complex financial PDFs.",
        epilog=ROUTING_NOTE
    )
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("--pages", help="Page range, e.g. '1-10' or '5'", default=None)
    parser.add_argument(
        "--tables-only", action="store_true",
        help="Extract only tables; omit body text (lower context noise)"
    )
    parser.add_argument(
        "--output", choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown). Use json for pipeline consumption."
    )
    args = parser.parse_args()

    result = extract(args.pdf_path, pages=args.pages, tables_only=args.tables_only)

    if "error" in result:
        print(f"[ERROR] {result['error']}", file=sys.stderr)
        sys.exit(1)

    for w in result.get("warnings", []):
        print(f"[WARN] {w}", file=sys.stderr)

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        meta = result["metadata"]
        print(
            f"<!-- Docling | {meta['file']} "
            f"| pages: {meta['page_range_applied']} "
            f"| mode: {meta['mode']} "
            f"| {meta['extracted_at']} -->"
        )
        print(f"<!-- tables_found: {meta['tables_found']} | {meta['freshness_note']} -->")
        print()
        if result["text"]:
            print(result["text"])
        if result["tables"]:
            print("\n## Extracted Tables\n")
            for i, tbl in enumerate(result["tables"], 1):
                print(f"### Table {i}\n")
                print(tbl)
                print()


if __name__ == "__main__":
    main()
