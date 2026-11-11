"""Add an invisible, searchable text layer to a PDF from OCR JSON."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from ._native import PdfError
from .cli import main
from .core import add_textlayer, load_pages, make_searchable_pdf, write_textlayer
from .types import Options, ParsedPage, Stats, TextBox

try:
    __version__ = version("pdf-textlayer")
except PackageNotFoundError:  # source tree without installed metadata
    __version__ = "0.0.0+unknown"

__all__ = [
    "Options",
    "ParsedPage",
    "PdfError",
    "Stats",
    "TextBox",
    "__version__",
    "add_textlayer",
    "load_pages",
    "main",
    "make_searchable_pdf",
    "write_textlayer",
]
