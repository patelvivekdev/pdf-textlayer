"""Types of the Rust extension module. Private: use :mod:`pdf_textlayer`."""

from collections.abc import Sequence
from typing import Protocol

class _TextBox(Protocol):
    @property
    def text(self) -> str: ...
    @property
    def x(self) -> float: ...
    @property
    def y(self) -> float: ...
    @property
    def width(self) -> float: ...
    @property
    def height(self) -> float: ...
    @property
    def confidence(self) -> float: ...
    @property
    def rotation(self) -> float: ...

_Pages = Sequence[tuple[int, Sequence[_TextBox]]]
# (page_count, pages_with_text_layer, items_drawn)
_Stats = tuple[int, list[int], int]
# (text, x, y, width, height, confidence, rotation)
_TextBoxTuple = tuple[str, float, float, float, float, float, float]

class PdfError(RuntimeError):
    """The input is not a PDF this package can read."""

def write_textlayer(
    input_pdf: str,
    pages: _Pages,
    output_pdf: str,
    *,
    min_confidence: float,
    font: str,
    sanitize_unsupported: bool,
) -> _Stats: ...
def add_textlayer(
    pdf: bytes,
    pages: _Pages,
    *,
    min_confidence: float,
    font: str,
    sanitize_unsupported: bool,
) -> tuple[bytes, _Stats]: ...
def make_searchable_pdf(
    input_pdf: str,
    source_json: str,
    output_pdf: str,
    *,
    adapter: str,
    min_confidence: float,
    font: str,
    sanitize_unsupported: bool,
) -> _Stats: ...
def load_pages(
    source_json: str, *, adapter: str
) -> list[tuple[int, list[_TextBoxTuple]]]: ...
def run_cli(args: list[str]) -> int: ...
