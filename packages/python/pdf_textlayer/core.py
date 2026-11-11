"""Draw an invisible text layer over a PDF.

Every function hands its work to the Rust writer with the GIL released. Pages
without OCR boxes are left as in the input PDF, and boxes that land on a word
the page already contains are skipped, so mixed scanned / native documents are
handled without re-rasterizing.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from . import _native
from .types import Options, ParsedPage, Stats, TextBox

__all__ = [
    "add_textlayer",
    "load_pages",
    "make_searchable_pdf",
    "write_textlayer",
]

Source = str | os.PathLike[str] | Mapping[str, Any]
"""OCR JSON: a path to a JSON file, or the already-loaded document."""


def write_textlayer(
    input_pdf: str | os.PathLike[str],
    pages: Iterable[ParsedPage],
    output_pdf: str | os.PathLike[str],
    *,
    options: Options | None = None,
) -> Stats:
    """Draw an invisible text layer onto ``input_pdf`` and save it to ``output_pdf``.

    Pages whose ``page_number`` does not appear in ``pages``, or that
    contribute no drawable boxes after filtering, are left untouched.

    Args:
        input_pdf: Path to the source PDF.
        pages: Iterable of :class:`ParsedPage`. Order does not matter.
        output_pdf: Where to write the result. Overwritten if it exists.
        options: Drawing options.

    Raises:
        FileNotFoundError: ``input_pdf`` does not exist.
        PdfError: ``input_pdf`` is not a readable PDF, or is encrypted.
        ValueError: ``options.font`` is not a known name.
    """
    draw = options or Options()
    return _stats(
        _native.write_textlayer(
            os.fspath(input_pdf),
            _native_pages(pages),
            os.fspath(output_pdf),
            min_confidence=draw.min_confidence,
            font=draw.font,
            sanitize_unsupported=draw.sanitize_unsupported,
        )
    )


def add_textlayer(
    pdf: bytes,
    pages: Iterable[ParsedPage],
    *,
    options: Options | None = None,
) -> tuple[bytes, Stats]:
    """Like :func:`write_textlayer`, for a PDF held in memory.

    Returns the new PDF and the :class:`Stats` of the pass.
    """
    draw = options or Options()
    output, stats = _native.add_textlayer(
        bytes(pdf),
        _native_pages(pages),
        min_confidence=draw.min_confidence,
        font=draw.font,
        sanitize_unsupported=draw.sanitize_unsupported,
    )
    return output, _stats(stats)


def load_pages(source: Source, *, adapter: str = "liteparse") -> list[ParsedPage]:
    """Read the OCR boxes of each page from an OCR tool's JSON.

    Pages without OCR boxes are omitted. Malformed pages and items are
    skipped.

    Args:
        source: Path to a JSON file, or the already-loaded document.
        adapter: The JSON format. ``"liteparse"`` reads ``lit parse --format
            json`` and keeps the items whose ``font_name`` is ``"OCR"``.

    Raises:
        ValueError: ``adapter`` is unknown, or the JSON is invalid.
    """
    return [
        ParsedPage(
            page_number=page_number,
            boxes=[TextBox(*box) for box in boxes],
        )
        for page_number, boxes in _native.load_pages(_json(source), adapter=adapter)
    ]


def make_searchable_pdf(
    input_pdf: str | os.PathLike[str],
    source: Source | Iterable[ParsedPage],
    output_pdf: str | os.PathLike[str],
    *,
    adapter: str = "liteparse",
    options: Options | None = None,
) -> Stats:
    """Write a searchable PDF from a PDF and an OCR tool's JSON.

    Args:
        input_pdf: Path to the source PDF (scanned, native, or mixed).
        source: A path to a JSON file, an already-loaded JSON document, or
            an iterable of :class:`ParsedPage` (``adapter`` is then ignored).
        output_pdf: Where to write the searchable PDF.
        adapter: The JSON format. Defaults to ``"liteparse"``.
        options: Drawing options.
    """
    if not isinstance(source, (str, os.PathLike, Mapping)):
        return write_textlayer(input_pdf, source, output_pdf, options=options)

    draw = options or Options()
    return _stats(
        _native.make_searchable_pdf(
            os.fspath(input_pdf),
            _json(source),
            os.fspath(output_pdf),
            adapter=adapter,
            min_confidence=draw.min_confidence,
            font=draw.font,
            sanitize_unsupported=draw.sanitize_unsupported,
        )
    )


def _json(source: Source) -> str:
    if isinstance(source, Mapping):
        return json.dumps(source)
    return Path(source).read_text(encoding="utf-8")


def _native_pages(pages: Iterable[ParsedPage]) -> list[tuple[int, list[TextBox]]]:
    return [(page.page_number, page.boxes) for page in pages]


def _stats(stats: tuple[int, list[int], int]) -> Stats:
    pages, ocr_pages, items_drawn = stats
    return Stats(pages=pages, ocr_pages=ocr_pages, items_drawn=items_drawn)
