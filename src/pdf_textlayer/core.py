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

Boxes that land on an existing native word are skipped, so a mixed page does
not get a second copy of text the PDF already contains. Rotated OCR boxes
(and pages with a ``/Rotate`` entry) are placed along the text direction.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, TypeGuard

import pymupdf

if TYPE_CHECKING:
    from .adapters import Adapter

# PyMuPDF render mode 3 = "neither fill nor stroke". The glyphs are present in
# the content stream (so text extraction, search, and accessibility tools find
# them) but never painted.
_INVISIBLE_RENDER_MODE = 3

# Base-14 font available in every PDF reader without embedding. CJK and other
# non-Latin characters get transliterated to "?" unless an embedded font is
# selected — the text layer is for search, not visual fidelity.
DEFAULT_FONT = "helv"

# Resource name used when the overlay font is embedded. One embedding per
# page; ``page.insert_text`` would otherwise register the font on every box.
_OVERLAY_FONT_RESOURCE = "pdftextlayer"

_UNSUPPORTED_GLYPH = "?"

# An OCR box covers a native word when the overlap is at least half of the
# smaller rectangle. Less than that is a glancing touch, not the same word.
_MIN_NATIVE_WORD_COVERAGE = 0.5

_NativeWordRect = tuple[float, float, float, float]


@dataclass(frozen=True)
class TextBox:
    """A single piece of OCR text to draw, in PDF user-space points.

    Coordinates use PyMuPDF's convention: origin at the top-left of the
    displayed page, X increases right, Y increases down. ``(x, y)`` is the
    top-left corner of the axis-aligned box.

    ``rotation`` is degrees counter-clockwise (0, 90, 180, 270). Other
    values snap to the nearest right angle. For a page with a ``/Rotate``
    entry, pass the box in the rotated display space LiteParse emits; the
    writer maps it back onto the page's unrotated coordinate system.
    """

    text: str
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0
    rotation: float = 0.0


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

    ``0.0`` keeps everything, including a confidence of exactly 0.
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


class _GlyphMetrics:
    """Per-character advance and coverage for one font.

    Widths match :meth:`pymupdf.Font.text_length` at fontsize 1. Caching
    avoids repeating that lookup for every character of every box.
    """

    def __init__(self, font: pymupdf.Font) -> None:
        self.font = font
        self._advances: dict[str, float] = {}
        self._supported: dict[str, bool] = {}

    def is_supported(self, char: str) -> bool:
        supported = self._supported.get(char)

        if supported is None:
            try:
                supported = self.font.glyph_advance(ord(char)) > 0
            except Exception:
                supported = False

            self._supported[char] = supported

        return supported

    def encodable_text(self, text: str) -> str:
        """Replace glyphs the font cannot encode.

        ``insert_text`` silently drops an unsupported codepoint, which can
        erase the rest of the item. A ``?`` keeps the surrounding word
        searchable.
        """
        return "".join(
            char if self.is_supported(char) else _UNSUPPORTED_GLYPH for char in text
        )

    def width_at_1pt(self, text: str) -> float:
        width = 0.0

        for char in text:
            advance = self._advances.get(char)

            if advance is None:
                try:
                    advance = self.font.glyph_advance(ord(char))
                except Exception:
                    advance = 0.0

                self._advances[char] = advance

            width += advance

        return width


def _text_to_draw(metrics: _GlyphMetrics, text: str, options: Options) -> str:
    if options.sanitize_unsupported:
        return metrics.encodable_text(text)

    if any(not metrics.is_supported(char) for char in text):
        return ""

    return text


def _draw_invisible_text(
    shape: pymupdf.Shape,
    metrics: _GlyphMetrics,
    box: TextBox,
    options: Options,
) -> bool:
    """Queue one invisible text run. Returns True when a run was queued."""
    text = _text_to_draw(metrics, box.text, options)
    if not text:
        return False

    width_at_1pt = metrics.width_at_1pt(text)
    if width_at_1pt <= 0:
        return False

    # PyMuPDF accepts 0/90/180/270. LiteParse rotation is already one of
    # those when the polygon was recovered; anything else snaps.
    rotation = round(box.rotation / 90) * 90 % 360
    horizontal = rotation in (0, 180)
    along_text = box.width if horizontal else box.height
    across_text = box.height if horizontal else box.width

    # Scale along the text direction so the run fills the OCR box. Cap the
    # perpendicular size so a wide-but-short box cannot blow the font up.
    font_size = along_text / width_at_1pt
    font_size = min(font_size, max(across_text * 1.2, 1.0))

    rect = pymupdf.Rect(box.x, box.y, box.x + box.width, box.y + box.height)
    # Baseline-left corner of the box in page space, for each text direction.
    origin = {
        0: (rect.x0, rect.y1),
        90: (rect.x1, rect.y1),
        180: (rect.x1, rect.y0),
        270: (rect.x0, rect.y0),
    }[rotation]

    shape.insert_text(
        origin,
        text,
        fontname=_OVERLAY_FONT_RESOURCE,
        fontsize=font_size,
        render_mode=_INVISIBLE_RENDER_MODE,
        # Color is irrelevant under render_mode=3, but PyMuPDF requires it.
        color=(0, 0, 0),
        rotate=rotation,
    )

    return True


def _native_word_rects(page: pymupdf.Page) -> list[_NativeWordRect]:
    """Word rectangles already on the page, in unrotated page space.

    Image-only pages have no fonts, annotations, or widgets, and
    ``get_text("words")`` still walks the content stream. Skip that walk.
    Annotations and widgets are included because a FreeText annotation or a
    form field can carry words on a page that has no fonts of its own.
    """
    if not page.get_fonts() and page.first_annot is None and page.first_widget is None:
        return []

    return [(word[0], word[1], word[2], word[3]) for word in page.get_text("words")]


def _covers_native_word(
    box: TextBox,
    native_word_rects: list[_NativeWordRect],
) -> bool:
    """Whether ``box`` covers enough of a native word to be that same word."""
    box_area = box.width * box.height
    if box_area <= 0:
        return False

    box_x1 = box.x + box.width
    box_y1 = box.y + box.height

    for x0, y0, x1, y1 in native_word_rects:
        overlap_width = min(box_x1, x1) - max(box.x, x0)
        if overlap_width <= 0:
            continue

        overlap_height = min(box_y1, y1) - max(box.y, y0)
        if overlap_height <= 0:
            continue

        word_area = (x1 - x0) * (y1 - y0)
        if word_area <= 0:
            continue

        coverage = overlap_width * overlap_height / min(box_area, word_area)
        if coverage >= _MIN_NATIVE_WORD_COVERAGE:
            return True

    return False


def _text_box_in_page_space(page: pymupdf.Page, box: TextBox) -> TextBox:
    """Map a display-space OCR box onto the page's unrotated user space.

    LiteParse reports coordinates in the upright viewport (after ``/Rotate``).
    ``insert_text`` writes into the unrotated page. ``derotation_matrix``
    moves the rectangle, and the page rotation is added to the text angle so
    the glyphs still read upright on screen.
    """
    rect = pymupdf.Rect(box.x, box.y, box.x + box.width, box.y + box.height)
    rect = rect * page.derotation_matrix

    return TextBox(
        text=box.text,
        x=rect.x0,
        y=rect.y0,
        width=rect.width,
        height=rect.height,
        confidence=box.confidence,
        rotation=(box.rotation + page.rotation) % 360,
    )


@contextmanager
def _overlay_font_inserted_once(
    page: pymupdf.Page, font: pymupdf.Font
) -> Iterator[None]:
    """Answer PyMuPDF's per-run font lookup from a single embedding.

    ``Shape.insert_text`` calls ``page.insert_font`` on every run. That
    lookup walks the page's font list. The first call embeds ``font``; later
    calls return the same xref. The real method is restored on the way out.
    """
    insert_font: Callable[..., Any] = page.insert_font
    embedded_xref: int | None = None

    def insert_cached_overlay_font(*args: Any, **kwargs: Any) -> Any:
        nonlocal embedded_xref

        if args or kwargs.get("fontname") != _OVERLAY_FONT_RESOURCE:
            return insert_font(*args, **kwargs)

        if embedded_xref is None:
            embedded_xref = insert_font(
                fontname=_OVERLAY_FONT_RESOURCE,
                fontbuffer=font.buffer,
            )

        return embedded_xref

    page.insert_font = insert_cached_overlay_font  # type: ignore[method-assign]

    try:
        yield
    finally:
        delattr(page, "insert_font")


def _overlay_boxes(parsed_page: ParsedPage, options: Options) -> list[TextBox]:
    boxes: list[TextBox] = []

    for box in parsed_page.boxes:
        if box.confidence < options.min_confidence:
            continue
        if box.width <= 0 or box.height <= 0:
            continue
        if not box.text.strip():
            continue

        boxes.append(box)

    return boxes


def _overlay_page(
    page: pymupdf.Page,
    boxes: list[TextBox],
    font: pymupdf.Font,
    metrics: _GlyphMetrics,
    options: Options,
) -> int:
    """Queue this page's runs and commit them once. Returns how many landed."""
    native_word_rects = _native_word_rects(page)
    drawn = 0
    shape = page.new_shape()

    with _overlay_font_inserted_once(page, font):
        for box in boxes:
            box_in_page_space = _text_box_in_page_space(page, box)
            if box_in_page_space.width <= 0 or box_in_page_space.height <= 0:
                continue

            if _covers_native_word(box_in_page_space, native_word_rects):
                continue

            if _draw_invisible_text(shape, metrics, box_in_page_space, options):
                drawn += 1

        if drawn:
            shape.commit(overlay=True)

    return drawn


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
    Every drawable box on a page is queued on one content stream and
    committed once. The overlay font is embedded once on that page.

    Args:
        input_pdf: Path to the source PDF.
        pages: Iterable of :class:`ParsedPage`. Order does not matter.
        output_pdf: Where to write the result. Overwritten if it exists.
        options: Drawing options.

    Returns:
        :class:`Stats` summarizing what was written.
    """
    draw_options = options or Options()
    parsed_page_by_number = {
        parsed_page.page_number: parsed_page for parsed_page in pages
    }

    document = pymupdf.open(str(input_pdf))

    try:
        font = pymupdf.Font(draw_options.font)
        metrics = _GlyphMetrics(font)
        pages_with_overlay: list[int] = []
        items_drawn = 0

        for page_index in range(document.page_count):
            page_number = page_index + 1
            parsed_page = parsed_page_by_number.get(page_number)
            if parsed_page is None or not parsed_page.boxes:
                continue

            overlay_boxes = _overlay_boxes(parsed_page, draw_options)
            if not overlay_boxes:
                continue

            drawn = _overlay_page(
                document[page_index],
                overlay_boxes,
                font,
                metrics,
                draw_options,
            )
            if not drawn:
                continue

            pages_with_overlay.append(page_number)
            items_drawn += drawn

        # Plain save keeps the input PDF's structure intact (image streams,
        # fonts, annotations) and just appends the overlay objects.
        document.save(str(output_pdf))

        return Stats(
            pages=document.page_count,
            ocr_pages=pages_with_overlay,
            items_drawn=items_drawn,
        )
    finally:
        document.close()


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
    if _is_parsed_page_list(source):
        return write_textlayer(input_pdf, source, output_pdf, options=options)

    from .adapters import get_adapter

    resolved_adapter = get_adapter(adapter) if isinstance(adapter, str) else adapter

    if isinstance(source, dict):
        document = source
    else:
        document = json.loads(Path(source).read_text())  # type: ignore[arg-type]

    return write_textlayer(
        input_pdf,
        resolved_adapter.parse(document),
        output_pdf,
        options=options,
    )


def _is_parsed_page_list(source: Any) -> TypeGuard[list[ParsedPage]]:
    """True when ``source`` is already a list of :class:`ParsedPage`."""
    if isinstance(source, (str, Path, dict, bytes)):
        return False

    if isinstance(source, list):
        return all(isinstance(page, ParsedPage) for page in source)

    return False
