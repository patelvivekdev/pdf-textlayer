"""Tests for the liteparse adapter + overlay core."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdf_textlayer import (
    LiteparseAdapter,
    Options,
    ParsedPage,
    TextBox,
    make_searchable_pdf,
    write_textlayer,
)
from pdf_textlayer.adapters import available_adapters, get_adapter, register_adapter


def _build_native_pdf(path: Path, text: str = "Native page text") -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    if text:
        page.insert_text((50, 100), text, fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()


def _build_two_page_pdf(path: Path) -> None:
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 100), "Native page one", fontname="helv", fontsize=12)
    doc.new_page(width=595, height=842)  # blank — pretend-scanned
    doc.save(str(path))
    doc.close()


# ---------------------------------------------------------------------------
# End-to-end via the liteparse adapter
# ---------------------------------------------------------------------------


def test_pure_ocr_page_becomes_searchable(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    out = tmp_path / "out.pdf"
    _build_native_pdf(pdf, text="")  # blank page

    parsed = {
        "pages": [
            {
                "page": 1,
                "width": 595,
                "height": 842,
                "textItems": [
                    {
                        "text": "HELLO",
                        "x": 50,
                        "y": 100,
                        "width": 60,
                        "height": 14,
                        "fontName": "OCR",
                        "fontSize": 14,
                        "confidence": 0.95,
                    }
                ],
            }
        ]
    }
    stats = make_searchable_pdf(pdf, parsed, out)
    assert stats["ocr_pages"] == [1]
    assert stats["items_drawn"] == 1

    doc = pymupdf.open(str(out))
    assert "HELLO" in doc[0].get_text()
    assert len(doc[0].search_for("HELLO")) == 1
    doc.close()


def test_native_page_is_untouched(tmp_path: Path) -> None:
    pdf = tmp_path / "native.pdf"
    out = tmp_path / "out.pdf"
    _build_native_pdf(pdf, text="Native page text")

    parsed = {
        "pages": [
            {
                "page": 1,
                "textItems": [
                    {
                        "text": "Native page text",
                        "x": 50,
                        "y": 100,
                        "width": 100,
                        "height": 12,
                        "fontName": "Helvetica",
                        "fontSize": 12,
                    }
                ],
            }
        ]
    }
    stats = make_searchable_pdf(pdf, parsed, out)
    assert stats["ocr_pages"] == []
    assert stats["items_drawn"] == 0

    doc = pymupdf.open(str(out))
    assert "Native page text" in doc[0].get_text()
    doc.close()


def test_mixed_pdf_only_overlays_ocr_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "mixed.pdf"
    out = tmp_path / "out.pdf"
    _build_two_page_pdf(pdf)

    parsed = {
        "pages": [
            {
                "page": 1,
                "textItems": [
                    {
                        "text": "Native page one",
                        "x": 50,
                        "y": 100,
                        "width": 100,
                        "height": 12,
                        "fontName": "Helvetica",
                        "fontSize": 12,
                    }
                ],
            },
            {
                "page": 2,
                "textItems": [
                    {
                        "text": "SCANNED",
                        "x": 50,
                        "y": 100,
                        "width": 80,
                        "height": 14,
                        "fontName": "OCR",
                        "fontSize": 14,
                        "confidence": 0.9,
                    }
                ],
            },
        ]
    }
    stats = make_searchable_pdf(pdf, parsed, out)
    assert stats["ocr_pages"] == [2]
    assert stats["items_drawn"] == 1

    doc = pymupdf.open(str(out))
    assert "Native page one" in doc[0].get_text()
    assert "SCANNED" in doc[1].get_text()
    doc.close()


def test_confidence_threshold_filters(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    out = tmp_path / "out.pdf"
    _build_native_pdf(pdf, text="")

    parsed = {
        "pages": [
            {
                "page": 1,
                "textItems": [
                    {
                        "text": "GOOD",
                        "x": 50,
                        "y": 100,
                        "width": 50,
                        "height": 14,
                        "fontName": "OCR",
                        "fontSize": 14,
                        "confidence": 0.9,
                    },
                    {
                        "text": "BAD",
                        "x": 120,
                        "y": 100,
                        "width": 40,
                        "height": 14,
                        "fontName": "OCR",
                        "fontSize": 14,
                        "confidence": 0.2,
                    },
                ],
            }
        ]
    }
    stats = make_searchable_pdf(pdf, parsed, out, options=Options(min_confidence=0.5))
    assert stats["items_drawn"] == 1

    doc = pymupdf.open(str(out))
    txt = doc[0].get_text()
    assert "GOOD" in txt
    assert "BAD" not in txt
    doc.close()


def test_drops_zero_size_and_empty_items(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    out = tmp_path / "out.pdf"
    _build_native_pdf(pdf, text="")

    parsed = {
        "pages": [
            {
                "page": 1,
                "textItems": [
                    {
                        "text": "  ",
                        "x": 50,
                        "y": 100,
                        "width": 50,
                        "height": 14,
                        "fontName": "OCR",
                        "fontSize": 14,
                        "confidence": 0.9,
                    },
                    {
                        "text": "ZEROW",
                        "x": 50,
                        "y": 120,
                        "width": 0,
                        "height": 14,
                        "fontName": "OCR",
                        "fontSize": 14,
                        "confidence": 0.9,
                    },
                ],
            }
        ]
    }
    stats = make_searchable_pdf(pdf, parsed, out)
    assert stats["items_drawn"] == 0


# ---------------------------------------------------------------------------
# Adapter layer
# ---------------------------------------------------------------------------


def test_liteparse_adapter_skips_non_ocr_items() -> None:
    data = {
        "pages": [
            {
                "page": 1,
                "textItems": [
                    {
                        "text": "native",
                        "x": 0,
                        "y": 0,
                        "width": 10,
                        "height": 10,
                        "fontName": "Helvetica",
                    },
                    {
                        "text": "ocr",
                        "x": 0,
                        "y": 0,
                        "width": 10,
                        "height": 10,
                        "fontName": "OCR",
                        "confidence": 0.8,
                    },
                ],
            }
        ]
    }
    pages = list(LiteparseAdapter().parse(data))
    assert len(pages) == 1
    assert [b.text for b in pages[0].boxes] == ["ocr"]


def test_liteparse_adapter_reads_current_json_schema() -> None:
    """liteparse 2.17 ``--format json`` uses snake_case and may include rotation."""
    data = {
        "total_pages": 1,
        "pages": [
            {
                "page": 1,
                "width": 612,
                "height": 792,
                "text": "Hello",
                "text_items": [
                    {
                        "text": "native",
                        "x": 1,
                        "y": 2,
                        "width": 3,
                        "height": 4,
                        "font_name": "Helvetica",
                        "font_size": 12,
                        "confidence": 1,
                    },
                    {
                        "text": "ocr",
                        "x": 5,
                        "y": 6,
                        "width": 7,
                        "height": 8,
                        "font_name": "OCR",
                        "font_size": 8,
                        "confidence": 0,
                        "rotation": 90,
                    },
                ],
            }
        ],
    }
    pages = list(LiteparseAdapter().parse(data))
    assert len(pages) == 1
    assert pages[0].boxes == [TextBox("ocr", 5, 6, 7, 8, confidence=0.0, rotation=90.0)]


def test_liteparse_adapter_defaults_missing_rotation() -> None:
    data = {
        "pages": [
            {
                "page": 1,
                "text_items": [
                    {
                        "text": "ocr",
                        "x": 0,
                        "y": 0,
                        "width": 10,
                        "height": 10,
                        "font_name": "OCR",
                    }
                ],
            }
        ]
    }
    box = list(LiteparseAdapter().parse(data))[0].boxes[0]
    assert box.rotation == 0.0
    assert box.confidence == 1.0


def test_liteparse_adapter_skips_pages_with_no_ocr() -> None:
    data = {
        "pages": [
            {
                "page": 1,
                "textItems": [
                    {
                        "text": "native",
                        "x": 0,
                        "y": 0,
                        "width": 10,
                        "height": 10,
                        "fontName": "Helvetica",
                    },
                ],
            },
        ]
    }
    assert list(LiteparseAdapter().parse(data)) == []


def test_adapter_registry_roundtrip() -> None:
    assert "liteparse" in available_adapters()
    assert isinstance(get_adapter("liteparse"), LiteparseAdapter)
    with pytest.raises(KeyError, match="Unknown adapter"):
        get_adapter("does-not-exist")


def test_custom_adapter_can_be_registered(tmp_path: Path) -> None:
    """Register a custom adapter and drive it via make_searchable_pdf."""
    pdf = tmp_path / "scan.pdf"
    out = tmp_path / "out.pdf"
    _build_native_pdf(pdf, text="")

    class FakeAdapter:
        def parse(self, data):
            for p in data["my_pages"]:
                yield ParsedPage(
                    page_number=p["n"],
                    boxes=[TextBox(text=p["t"], x=50, y=100, width=60, height=14)],
                )

    register_adapter("fake", FakeAdapter())

    stats = make_searchable_pdf(
        pdf,
        {"my_pages": [{"n": 1, "t": "CUSTOM"}]},
        out,
        adapter="fake",
    )
    assert stats["items_drawn"] == 1

    doc = pymupdf.open(str(out))
    assert "CUSTOM" in doc[0].get_text()
    doc.close()


# ---------------------------------------------------------------------------
# Low-level entry point
# ---------------------------------------------------------------------------


def test_write_textlayer_accepts_parsed_pages_directly(tmp_path: Path) -> None:
    pdf = tmp_path / "scan.pdf"
    out = tmp_path / "out.pdf"
    _build_native_pdf(pdf, text="")

    pages = [
        ParsedPage(
            page_number=1,
            boxes=[TextBox(text="DIRECT", x=50, y=100, width=60, height=14)],
        )
    ]
    stats = write_textlayer(pdf, pages, out)
    assert stats["items_drawn"] == 1
    doc = pymupdf.open(str(out))
    assert "DIRECT" in doc[0].get_text()
    doc.close()


def test_make_searchable_pdf_accepts_parsed_pages_list(tmp_path: Path) -> None:
    """If the caller already has ParsedPage objects, the adapter is bypassed."""
    pdf = tmp_path / "scan.pdf"
    out = tmp_path / "out.pdf"
    _build_native_pdf(pdf, text="")

    pages = [
        ParsedPage(
            page_number=1,
            boxes=[TextBox(text="BYPASS", x=50, y=100, width=60, height=14)],
        )
    ]
    stats = make_searchable_pdf(pdf, pages, out, adapter="nonexistent-on-purpose")
    assert stats["items_drawn"] == 1
