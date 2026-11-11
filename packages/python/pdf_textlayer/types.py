"""Data types of the Python API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

# Whose advance widths size each run. The overlay font's glyphs are plain
# rectangles and it encodes every script, so this only decides how wide the
# invisible characters are.
DEFAULT_FONT = "helv"


@dataclass(frozen=True)
class TextBox:
    """A single piece of OCR text to draw, in PDF user-space points.

    The origin is the top-left of the displayed page, X increases right, Y
    increases down. ``(x, y)`` is the top-left corner of the axis-aligned box.

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
    """Drawing options."""

    min_confidence: float = 0.0
    """Drop boxes whose confidence is below this threshold (0.0–1.0).

    ``0.0`` keeps everything, including a confidence of exactly 0.
    """

    font: str = DEFAULT_FONT
    """Whose character widths size each run: ``helv``, ``tiro``, or ``cour``.

    The text is invisible, so this changes spacing, not appearance. Pick the
    one closest to the printed text so words inside a line land under their
    image. Every script is kept whichever is chosen. The CJK names from 0.2
    (``china-s``, ``china-t``, ``japan``, ``korea``) are still accepted and
    behave like ``helv``.
    """

    sanitize_unsupported: bool = True
    """If True, replace characters that cannot be drawn with ``?``.

    Those are control characters, zero-width characters, and characters
    outside the Basic Multilingual Plane (emoji, for example). If False,
    items containing one are dropped instead.
    """


class Stats(TypedDict):
    """Summary returned from a successful overlay pass."""

    pages: int
    ocr_pages: list[int]
    items_drawn: int
