"""Draw an invisible text layer over a PDF.

The package separates two concerns:

* **Adapters** (see :mod:`pdf_textlayer.adapters`) translate a source-specific
  JSON shape into :class:`ParsedPage` objects.
* **Core** (this module) takes those :class:`ParsedPage` objects and draws an
  invisible PDF text layer at each box's location, so the page becomes
  selectable and searchable in any viewer.

Pages without OCR boxes are left exactly as in the input PDF — native text and
embedded searchability are preserved. This handles mixed scanned / native
documents without re-rasterizing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import pymupdf

if TYPE_CHECKING:
    from .adapters import Adapter

log = logging.getLogger(__name__)

# PyMuPDF render mode 3 = "neither fill nor stroke". The glyphs are present in
# the content stream (so text extraction, search, and accessibility tools find
# them) but never painted.
_INVISIBLE_RENDER_MODE = 3

# Base-14 font available in every PDF reader without embedding. CJK and other
# non-Latin characters get transliterated to "?" unless an embedded font is
# selected — the text layer is for search, not visual fidelity.
DEFAULT_FONT = "helv"


@dataclass(frozen=True)
class TextBox:
    """A single piece of OCR text to draw, in PDF user-space points.

    Coordinates use PyMuPDF's convention: origin at the top-left of the
    page, X increases right, Y increases down. ``(x, y)`` is the top-left
    corner of the box.
    """

    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0


@dataclass(frozen=True)
class ParsedPage:
    """OCR boxes for a single page of the source PDF.

    ``page_number`` is 1-indexed and matches the page in the input PDF
    that should receive the overlay. Pages not present in the iterable
    are left untouched.
    """

    page_number: int
    boxes: list[TextBox] = field(default_factory=list)


@dataclass(frozen=True)
class Options:
    """Drawing options for :func:`write_textlayer` / :func:`make_searchable_pdf`."""

    min_confidence: float = 0.0
    """Drop boxes whose confidence is below this threshold (0.0–1.0).

    ``0.0`` keeps everything.
    """

    font: str = DEFAULT_FONT
    """PyMuPDF font name.

    Built-in: ``helv``, ``cour``, ``tiro``. Bundled CJK fonts: ``china-s``,
    ``china-t``, ``japan``, ``korea``.
    """

    sanitize_unsupported: bool = True
    """If True, replace glyphs the font cannot encode with ``?``.

    If False, items containing unsupported glyphs are dropped so they
    don't silently emit empty content streams.
    """


class Stats(TypedDict):
    """Summary returned from a successful overlay pass."""

    pages: int
    ocr_pages: list[int]
    items_drawn: int


def _sanitize(text: str, font: pymupdf.Font) -> str:
    """Replace characters the font can't encode with ``?``.

    ``page.insert_text`` silently drops unsupported glyphs, which means
    a single bad codepoint can erase the entire item. Sanitizing
    preserves the rest of the string so search still hits the word.
    """
    out: list[str] = []
    for ch in text:
        try:
            keep = font.glyph_advance(ord(ch)) > 0
        except Exception:
            keep = False
        out.append(ch if keep else "?")
    return "".join(out)


def _draw_box(
    page: pymupdf.Page,
    font: pymupdf.Font,
    box: TextBox,
    opts: Options,
) -> bool:
    """Draw a single invisible text box. Returns True if anything was drawn."""
    text = box.text
    if opts.sanitize_unsupported:
        text = _sanitize(text, font)
    if not text:
        return False

    # Scale font so the string's rendered width matches the OCR bbox width.
    # This is what makes word-level highlights line up when a user
    # searches inside Acrobat / Preview / browser PDF viewers.
    width_at_1pt = font.text_length(text, fontsize=1)
    if width_at_1pt <= 0:
        return False
    font_size = box.width / width_at_1pt
    # Sanity cap: if the bbox is wide-but-short (single tall glyph or an
    # unhandled rotation), don't blow the font size up absurdly.
    font_size = min(font_size, max(box.height * 1.2, 1.0))

    # The insertion point is the glyph baseline, so add `height` to drop
    # from the top of the bbox down to where the baseline should sit.
    page.insert_text(
        (box.x, box.y + box.height),
        text,
        fontname=opts.font,
        fontsize=font_size,
        render_mode=_INVISIBLE_RENDER_MODE,
        # Color is irrelevant under render_mode=3, but PyMuPDF requires it.
        color=(0, 0, 0),
    )
    return True


def write_textlayer(
    input_pdf: str | Path,
    pages: Iterable[ParsedPage],
    output_pdf: str | Path,
    *,
    options: Options | None = None,
) -> Stats:
    """Draw an invisible text layer onto ``input_pdf`` and save it to ``output_pdf``.

    This is the low-level entry point: it takes already-normalized
    :class:`ParsedPage` objects, so any OCR source can be plugged in by
    writing an adapter (see :mod:`pdf_textlayer.adapters`) or by
    constructing :class:`ParsedPage` instances directly.

    Pages whose ``page_number`` does not appear in ``pages`` — or that
    contribute no drawable boxes after filtering — are left untouched.

    Args:
        input_pdf: Path to the source PDF.
        pages: Iterable of :class:`ParsedPage`. Order does not matter.
        output_pdf: Where to write the result. Overwritten if it exists.
        options: Drawing options.

    Returns:
        :class:`Stats` summarizing what was written.
    """
    opts = options or Options()
    pages_by_num: dict[int, ParsedPage] = {p.page_number: p for p in pages}

    doc = pymupdf.open(str(input_pdf))
    try:
        font = pymupdf.Font(opts.font)
        ocr_page_nums: list[int] = []
        items_drawn = 0

        for idx in range(doc.page_count):
            pageno = idx + 1
            parsed = pages_by_num.get(pageno)
            if parsed is None or not parsed.boxes:
                continue

            page = doc[idx]
            drawn_on_page = 0
            for box in parsed.boxes:
                if box.confidence < opts.min_confidence:
                    continue
                if box.width <= 0 or box.height <= 0:
                    continue
                if not box.text.strip():
                    continue
                if _draw_box(page, font, box, opts):
                    drawn_on_page += 1

            if drawn_on_page:
                ocr_page_nums.append(pageno)
                items_drawn += drawn_on_page

        # Plain save keeps the input PDF's structure intact (image streams,
        # fonts, annotations) and just appends the overlay objects.
        doc.save(str(output_pdf))

        return Stats(
            pages=doc.page_count,
            ocr_pages=ocr_page_nums,
            items_drawn=items_drawn,
        )
    finally:
        doc.close()


def make_searchable_pdf(
    input_pdf: str | Path,
    source: str | Path | dict[str, Any] | Iterable[ParsedPage],
    output_pdf: str | Path,
    *,
    adapter: Adapter | str = "liteparse",
    options: Options | None = None,
) -> Stats:
    """Generate a searchable PDF from a raw PDF plus an OCR JSON source.

    This is the convenience entry point: it accepts a JSON path / dict /
    pre-built iterable of :class:`ParsedPage`, runs it through the chosen
    adapter, and writes the overlay.

    Args:
        input_pdf: Path to the source PDF (scanned, native, or mixed).
        source: One of —

            * a path (``str`` / ``Path``) to a JSON file,
            * an already-loaded ``dict``,
            * or an iterable of :class:`ParsedPage` (in which case
              ``adapter`` is ignored).
        output_pdf: Where to write the searchable PDF.
        adapter: An :class:`~pdf_textlayer.adapters.Adapter` instance or
            the name of a registered adapter. Defaults to ``"liteparse"``.
        options: Drawing options.

    Returns:
        :class:`Stats` summarizing what was written.
    """
    if _looks_like_pages(source):
        return write_textlayer(input_pdf, source, output_pdf, options=options)  # type: ignore[arg-type]

    from .adapters import get_adapter

    adp = get_adapter(adapter) if isinstance(adapter, str) else adapter
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text())  # type: ignore[arg-type]
    return write_textlayer(input_pdf, adp.parse(data), output_pdf, options=options)


def _looks_like_pages(source: Any) -> bool:
    """True if ``source`` is already an iterable of ParsedPage."""
    if isinstance(source, (str, Path, dict, bytes)):
        return False
    if isinstance(source, list):
        return all(isinstance(p, ParsedPage) for p in source)
    return False
