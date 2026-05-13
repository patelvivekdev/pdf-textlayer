"""Add an invisible, searchable text layer to a PDF from OCR JSON."""

from __future__ import annotations

from .adapters import Adapter, LiteparseAdapter, get_adapter, register_adapter
from .cli import main
from .core import (
    Options,
    ParsedPage,
    Stats,
    TextBox,
    make_searchable_pdf,
    write_textlayer,
)

__all__ = [
    "Adapter",
    "LiteparseAdapter",
    "Options",
    "ParsedPage",
    "Stats",
    "TextBox",
    "get_adapter",
    "main",
    "make_searchable_pdf",
    "register_adapter",
    "write_textlayer",
]
