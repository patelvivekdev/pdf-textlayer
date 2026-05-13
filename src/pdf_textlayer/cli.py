"""CLI: take a raw PDF + OCR JSON, write a PDF with an invisible text layer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters import available_adapters
from .core import DEFAULT_FONT, Options, make_searchable_pdf


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf-textlayer",
        description=(
            "Add an invisible, searchable text layer to a PDF using OCR JSON. "
            "Pages without OCR text are left untouched."
        ),
    )
    p.add_argument(
        "input_pdf",
        type=Path,
        help="Source PDF (scanned, native, or mixed).",
    )
    p.add_argument(
        "parsed_json",
        type=Path,
        help="OCR JSON for the source PDF (see --from for supported formats).",
    )
    p.add_argument(
        "output_pdf",
        type=Path,
        help="Path to write the resulting PDF.",
    )
    p.add_argument(
        "--from",
        dest="adapter",
        default="liteparse",
        choices=available_adapters(),
        help="JSON adapter to use. Default: liteparse.",
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="Drop OCR items below this confidence (0–1). Default: 0.",
    )
    p.add_argument(
        "--font",
        default=DEFAULT_FONT,
        help=(
            "PyMuPDF font name. Use a CJK font (china-s, china-t, japan, "
            f"korea) for Asian docs. Default: {DEFAULT_FONT}."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.input_pdf.exists():
        print(f"Input PDF not found: {args.input_pdf}", file=sys.stderr)
        return 1
    if not args.parsed_json.exists():
        print(f"Parsed JSON not found: {args.parsed_json}", file=sys.stderr)
        return 1

    stats = make_searchable_pdf(
        args.input_pdf,
        args.parsed_json,
        args.output_pdf,
        adapter=args.adapter,
        options=Options(min_confidence=args.min_confidence, font=args.font),
    )

    print(f"Wrote: {args.output_pdf}")
    print(f"Pages: {stats['pages']}")
    print(
        f"Text layer added on {len(stats['ocr_pages'])} page(s): "
        f"{', '.join(map(str, stats['ocr_pages'])) or '(none)'}"
    )
    print(f"Items drawn: {stats['items_drawn']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
