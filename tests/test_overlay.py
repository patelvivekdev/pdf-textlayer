"""Rotation, native-word dedup, and single-commit overlay drawing."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdf_textlayer import Options, ParsedPage, TextBox, write_textlayer
from pdf_textlayer.core import (
    _OVERLAY_FONT_RESOURCE,
    DEFAULT_FONT,
    _covers_native_word,
    _GlyphMetrics,
    _native_word_rects,
    _overlay_font_inserted_once,
    _text_box_in_page_space,
)


def _blank(path: Path, width: float = 200, height: float = 200) -> None:
    doc = pymupdf.open()
    try:
        doc.new_page(width=width, height=height)
        doc.save(str(path))
    finally:
        doc.close()


def _rotation_pdf(path: Path, *, rotation: int, metadata_rotation: bool) -> None:
    doc = pymupdf.open()
    try:
        width, height = (792, 612) if rotation in (90, 270) else (612, 792)
        page = doc.new_page(width=width, height=height)
        if metadata_rotation:
            page.set_rotation(rotation)
        doc.save(str(path))
    finally:
        doc.close()


def _physical_box(box: pymupdf.Rect, rotation: int) -> pymupdf.Rect:
    if rotation == 90:
        return pymupdf.Rect(box.y0, 612 - box.x1, box.y1, 612 - box.x0)
    if rotation == 180:
        return pymupdf.Rect(612 - box.x1, 792 - box.y1, 612 - box.x0, 792 - box.y0)
    if rotation == 270:
        return pymupdf.Rect(792 - box.y1, box.x0, 792 - box.y0, box.x1)
    return box


def _iou(left: pymupdf.Rect, right: pymupdf.Rect) -> float:
    intersection = left & right
    intersection_area = max(intersection.width, 0) * max(intersection.height, 0)
    union = left.width * left.height + right.width * right.height - intersection_area
    return intersection_area / union


def _assert_search(path: Path, quote: str, expected: pymupdf.Rect) -> None:
    doc = pymupdf.open(str(path))
    try:
        page = doc[0]
        hits = [rect * page.rotation_matrix for rect in page.search_for(quote)]
    finally:
        doc.close()
    assert len(hits) == 1
    assert _iou(hits[0], expected) >= 0.65


def _write(src: Path, dest: Path, boxes: list[TextBox], **kwargs: object) -> None:
    options = kwargs.get("options")
    write_textlayer(
        src,
        [ParsedPage(page_number=1, boxes=boxes)],
        dest,
        options=options if isinstance(options, Options) else None,
    )


def test_display_box_on_rotated_page_maps_to_unrotated_space(tmp_path: Path) -> None:
    src = tmp_path / "rotated.pdf"
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=792, height=612)
        page.set_rotation(270)
        doc.save(str(src))
    finally:
        doc.close()

    doc = pymupdf.open(str(src))
    try:
        box = _text_box_in_page_space(doc[0], TextBox("Text", 138, 307, 389, 11))
    finally:
        doc.close()

    assert box == TextBox("Text", 474, 138, 11, 389, rotation=270)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_metadata_rotated_page_places_upright_ocr_text(
    tmp_path: Path, rotation: int
) -> None:
    quote = "Statement of Work No. SOW-AR-2026-017."
    expected = pymupdf.Rect(61.2, 231.6, 214.8, 240.24)
    src = tmp_path / f"metadata-{rotation}.pdf"
    dest = tmp_path / f"metadata-{rotation}-out.pdf"
    _rotation_pdf(src, rotation=rotation, metadata_rotation=True)
    _write(
        src,
        dest,
        [TextBox(quote, expected.x0, expected.y0, expected.width, expected.height)],
    )
    _assert_search(dest, quote, expected)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_physically_rotated_ocr_text(tmp_path: Path, rotation: int) -> None:
    quote = "Statement of Work No. SOW-AR-2026-017."
    expected = _physical_box(pymupdf.Rect(61.2, 231.6, 214.8, 240.24), rotation)
    src = tmp_path / f"physical-{rotation}.pdf"
    dest = tmp_path / f"physical-{rotation}-out.pdf"
    _rotation_pdf(src, rotation=rotation, metadata_rotation=False)
    _write(
        src,
        dest,
        [
            TextBox(
                quote,
                expected.x0,
                expected.y0,
                expected.width,
                expected.height,
                rotation=float(rotation),
            )
        ],
    )
    _assert_search(dest, quote, expected)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, pymupdf.Rect(330.96, 231.84, 463.2, 240.72)),
        (90, pymupdf.Rect(231.84, 149.04, 241.44, 280.8)),
        (180, pymupdf.Rect(149.04, 550.08, 280.56, 559.68)),
        (270, pymupdf.Rect(550.56, 330.96, 560.4, 462.72)),
    ],
)
def test_short_physically_rotated_ocr_text(
    tmp_path: Path, rotation: int, expected: pymupdf.Rect
) -> None:
    quote = "Purchased work: engineering services."
    src = tmp_path / f"short-{rotation}.pdf"
    dest = tmp_path / f"short-{rotation}-out.pdf"
    _rotation_pdf(src, rotation=rotation, metadata_rotation=False)
    _write(
        src,
        dest,
        [
            TextBox(
                quote,
                expected.x0,
                expected.y0,
                expected.width,
                expected.height,
                rotation=float(rotation),
            )
        ],
    )
    _assert_search(dest, quote, expected)


def test_native_duplicate_is_not_drawn_again(tmp_path: Path) -> None:
    src = tmp_path / "native.pdf"
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=200, height=200)
        for line in range(10):
            page.insert_text((10, 20 + line * 12), f"native line {line}")
        doc.save(str(src))
    finally:
        doc.close()

    dest = tmp_path / "out.pdf"
    stats = write_textlayer(
        src,
        [
            ParsedPage(
                page_number=1,
                boxes=[TextBox("native line 0", 10, 8, 65, 14)],
            )
        ],
        dest,
    )
    assert stats["items_drawn"] == 0
    doc = pymupdf.open(str(dest))
    try:
        assert len(doc[0].search_for("native line 0")) == 1
    finally:
        doc.close()


def test_mixed_page_keeps_scanned_region(tmp_path: Path) -> None:
    src = tmp_path / "mixed.pdf"
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=240)
        for line in range(10):
            page.insert_text((20, 25 + line * 14), f"native line {line}")
        doc.save(str(src))
    finally:
        doc.close()

    dest = tmp_path / "out.pdf"
    scanned = "Scanned region text"
    stats = write_textlayer(
        src,
        [
            ParsedPage(
                page_number=1,
                boxes=[
                    TextBox("native line 0", 20, 14, 70, 12),
                    TextBox(scanned, 170, 80, 100, 12),
                ],
            )
        ],
        dest,
    )
    assert stats["items_drawn"] == 1
    doc = pymupdf.open(str(dest))
    try:
        page = doc[0]
        assert len(page.search_for("native line 0")) == 1
        assert len(page.search_for(scanned)) == 1
        overlay_fonts = [
            font for font in page.get_fonts() if font[4] == _OVERLAY_FONT_RESOURCE
        ]
        assert len(overlay_fonts) == 1
    finally:
        doc.close()


def test_builtin_font_other_than_helvetica(tmp_path: Path) -> None:
    src = tmp_path / "blank.pdf"
    dest = tmp_path / "out.pdf"
    _blank(src)
    stats = write_textlayer(
        src,
        [ParsedPage(1, [TextBox("TIMES", 40, 40, 80, 14)])],
        dest,
        options=Options(font="tiro"),
    )
    assert stats["items_drawn"] == 1
    doc = pymupdf.open(str(dest))
    try:
        assert "TIMES" in doc[0].get_text()
    finally:
        doc.close()


def test_zero_confidence_is_kept_until_the_threshold(tmp_path: Path) -> None:
    src = tmp_path / "blank.pdf"
    kept = tmp_path / "kept.pdf"
    dropped = tmp_path / "dropped.pdf"
    _blank(src)
    box = TextBox("ZERO", 40, 40, 40, 12, confidence=0.0)
    assert write_textlayer(src, [ParsedPage(1, [box])], kept)["items_drawn"] == 1
    stats = write_textlayer(
        src,
        [ParsedPage(1, [box])],
        dropped,
        options=Options(min_confidence=0.01),
    )
    assert stats["items_drawn"] == 0


def test_unsupported_glyphs_are_replaced_or_dropped(tmp_path: Path) -> None:
    src = tmp_path / "blank.pdf"
    replaced = tmp_path / "replaced.pdf"
    dropped = tmp_path / "dropped.pdf"
    _blank(src)
    box = TextBox("A\x00B", 40, 40, 40, 12)
    write_textlayer(src, [ParsedPage(1, [box])], replaced)
    write_textlayer(
        src,
        [ParsedPage(1, [box])],
        dropped,
        options=Options(sanitize_unsupported=False),
    )
    doc = pymupdf.open(str(replaced))
    try:
        assert "A?B" in doc[0].get_text()
    finally:
        doc.close()
    doc = pymupdf.open(str(dropped))
    try:
        assert "A" not in doc[0].get_text()
    finally:
        doc.close()


def test_glyph_metrics_width_matches_font_text_length() -> None:
    font = pymupdf.Font(DEFAULT_FONT)
    metrics = _GlyphMetrics(font)
    samples = [
        "Hello World",
        "AV To ffi",
        "Contract \u00a74.2 \u2014 \u20ac100",
        "?? x",
        "",
        "W" * 400,
    ]
    for text in samples:
        encodable = metrics.encodable_text(text)
        assert metrics.width_at_1pt(encodable) == font.text_length(
            encodable, fontsize=1
        )


def test_glyph_metrics_replaces_unsupported_glyphs() -> None:
    metrics = _GlyphMetrics(pymupdf.Font(DEFAULT_FONT))
    encoded = metrics.encodable_text("Fee\u200b \u4e2d \u20ac5\x00")
    assert encoded == "Fee? \u4e2d \u20ac5?"


def test_overlay_font_lookup_embeds_once_and_restores_insert_font(
    tmp_path: Path,
) -> None:
    src = tmp_path / "blank.pdf"
    _blank(src)
    doc = pymupdf.open(str(src))
    try:
        page = doc[0]
        font = pymupdf.Font(DEFAULT_FONT)
        with _overlay_font_inserted_once(page, font):
            first = page.insert_font(fontname=_OVERLAY_FONT_RESOURCE)
            second = page.insert_font(fontname=_OVERLAY_FONT_RESOURCE)
            other = page.insert_font(fontname="helv")
        assert first == second
        assert other != first
        assert "insert_font" not in vars(page)
        names = [entry[4] for entry in page.get_fonts()]
        assert names.count(_OVERLAY_FONT_RESOURCE) == 1
    finally:
        doc.close()


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ((10.0, 10.0, 50.0, 20.0), True),
        ((30.0, 10.0, 70.0, 20.0), True),
        ((35.0, 10.0, 75.0, 20.0), False),
        ((100.0, 100.0, 140.0, 110.0), False),
        ((10.0, 10.0, 10.0, 20.0), False),
    ],
)
def test_half_coverage_of_the_smaller_box_counts_as_the_native_word(
    word: tuple[float, float, float, float], expected: bool
) -> None:
    box = TextBox("word", 10.0, 10.0, 40.0, 10.0)
    assert _covers_native_word(box, [word]) is expected


def test_zero_area_box_does_not_count_as_a_native_word() -> None:
    box = TextBox("word", 10.0, 10.0, 0.0, 10.0)
    assert _covers_native_word(box, [(0.0, 0.0, 100.0, 100.0)]) is False


def _scan_png(width: int = 120, height: int = 160) -> bytes:
    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, width, height), 0)
    pixmap.set_rect(pixmap.irect, (235,))
    pixmap.set_rect(pymupdf.IRect(10, 10, 110, 20), (30,))
    return pixmap.tobytes("png")


def _synthetic_contract(path: Path) -> list[ParsedPage]:
    doc = pymupdf.open()
    scan = _scan_png()
    pages: list[ParsedPage] = []
    try:
        scanned = doc.new_page(width=612, height=792)
        scanned.insert_image(scanned.rect, stream=scan)
        shape = scanned.new_shape()
        for row in range(300):
            shape.draw_line((20, 20 + row * 2.5), (590, 20 + row * 2.5))
        shape.finish(width=0.1, color=(0.9, 0.9, 0.9))
        shape.commit()
        pages.append(
            ParsedPage(
                1,
                [
                    TextBox(
                        f"Scanned clause {line}: fees payable net 30.",
                        60,
                        80 + line * 14,
                        380,
                        11,
                    )
                    for line in range(40)
                ],
            )
        )

        native = doc.new_page(width=612, height=792)
        for line in range(20):
            native.insert_text(
                (60, 80 + line * 14),
                f"Native clause {line} remains native text.",
            )
        pages.append(
            ParsedPage(
                2,
                [
                    TextBox(
                        f"Native clause {line} remains native text.",
                        60,
                        69 + line * 14,
                        230,
                        12,
                    )
                    for line in range(20)
                ],
            )
        )

        mixed = doc.new_page(width=612, height=792)
        for line in range(5):
            mixed.insert_text((60, 80 + line * 14), f"Mixed native line {line}")
        mixed.insert_image(pymupdf.Rect(300, 300, 560, 600), stream=scan)
        pages.append(
            ParsedPage(
                3,
                [TextBox("Mixed native line 0", 60, 69, 120, 12)]
                + [
                    TextBox(
                        "Image region line "
                        f"{line} \u00e9\u00df \u4e2d\u6587 \u2014 \u20ac9\u200b",
                        310,
                        310 + line * 14,
                        200,
                        10,
                    )
                    for line in range(12)
                ],
            )
        )

        doc.new_page(width=612, height=792).insert_image(
            pymupdf.Rect(0, 0, 612, 792), stream=scan
        )

        rotated = doc.new_page(width=792, height=612)
        rotated.set_rotation(90)
        rotated.insert_image(rotated.rect, stream=scan)
        pages.append(
            ParsedPage(
                5,
                [
                    TextBox(f"Rotated page line {line}", 50, 60 + line * 16, 260, 12)
                    for line in range(15)
                ]
                + [
                    TextBox("Sideways stamp", 500, 100, 12, 140, rotation=90),
                    TextBox("Upside down note", 300, 500, 150, 12, rotation=180),
                    TextBox("Margin note", 20, 300, 12, 120, rotation=270),
                    TextBox("   ", 20, 20, 50, 12),
                ],
            )
        )
        doc.save(str(path))
    finally:
        doc.close()
    return pages


def _reference_write(src: Path, pages: list[ParsedPage], dest: Path) -> None:
    """Per-box ``page.insert_text`` the batched writer has to match."""
    font = pymupdf.Font(DEFAULT_FONT)
    metrics = _GlyphMetrics(font)
    by_num = {page.page_number: page for page in pages}
    doc = pymupdf.open(str(src))
    try:
        for idx in range(doc.page_count):
            parsed = by_num.get(idx + 1)
            if parsed is None:
                continue
            page = doc[idx]
            native = _native_word_rects(page)
            for box in parsed.boxes:
                if not box.text.strip():
                    continue
                page_box = _text_box_in_page_space(page, box)
                if _covers_native_word(page_box, native):
                    continue
                text = metrics.encodable_text(page_box.text)
                rotation = round(page_box.rotation / 90) * 90 % 360
                horizontal = rotation in (0, 180)
                inline = page_box.width if horizontal else page_box.height
                cross = page_box.height if horizontal else page_box.width
                width_at_1pt = font.text_length(text, fontsize=1)
                if width_at_1pt <= 0:
                    continue
                font_size = min(inline / width_at_1pt, max(cross * 1.2, 1.0))
                rect = pymupdf.Rect(
                    page_box.x,
                    page_box.y,
                    page_box.x + page_box.width,
                    page_box.y + page_box.height,
                )
                origin = {
                    0: (rect.x0, rect.y1),
                    90: (rect.x1, rect.y1),
                    180: (rect.x1, rect.y0),
                    270: (rect.x0, rect.y0),
                }[rotation]
                page.insert_font(
                    fontname=_OVERLAY_FONT_RESOURCE,
                    fontbuffer=font.buffer,
                )
                page.insert_text(
                    origin,
                    text,
                    fontname=_OVERLAY_FONT_RESOURCE,
                    fontsize=font_size,
                    render_mode=3,
                    color=(0, 0, 0),
                    rotate=rotation,
                )
        doc.save(str(dest))
    finally:
        doc.close()


def _snapshot(path: Path) -> list[tuple]:
    doc = pymupdf.open(str(path))
    try:
        return [
            (
                page.get_text("words"),
                [image[0] for image in page.get_images(full=True)],
                sorted(font[4] for font in page.get_fonts()),
            )
            for page in doc
        ]
    finally:
        doc.close()


def test_batched_writer_matches_per_box_insertion(tmp_path: Path) -> None:
    src = tmp_path / "synthetic.pdf"
    pages = _synthetic_contract(src)
    _reference_write(src, pages, tmp_path / "reference.pdf")
    write_textlayer(src, pages, tmp_path / "batched.pdf")
    reference = _snapshot(tmp_path / "reference.pdf")
    batched = _snapshot(tmp_path / "batched.pdf")
    assert batched == reference
    assert any(words for words, _, _ in batched)


def test_overlay_font_is_embedded_once_per_page(tmp_path: Path) -> None:
    src = tmp_path / "synthetic.pdf"
    pages = _synthetic_contract(src)
    dest = tmp_path / "out.pdf"
    write_textlayer(src, pages, dest)
    doc = pymupdf.open(str(dest))
    try:
        counts = [
            sum(1 for font in page.get_fonts() if font[4] == _OVERLAY_FONT_RESOURCE)
            for page in doc
        ]
    finally:
        doc.close()
    assert counts == [1, 0, 1, 0, 1]


def test_pages_without_new_text_keep_their_content_stream(tmp_path: Path) -> None:
    src = tmp_path / "synthetic.pdf"
    pages = _synthetic_contract(src)
    dest = tmp_path / "out.pdf"
    write_textlayer(src, pages, dest)
    before = pymupdf.open(str(src))
    after = pymupdf.open(str(dest))
    try:
        for index in (1, 3):
            assert after[index].read_contents() == before[index].read_contents()
            assert after[index].get_fonts() == before[index].get_fonts()
    finally:
        before.close()
        after.close()


def _annotated(path: Path) -> None:
    doc = pymupdf.open()
    try:
        scan = _scan_png()
        image_only = doc.new_page(width=300, height=300)
        image_only.insert_image(image_only.rect, stream=scan)
        native = doc.new_page(width=300, height=300)
        native.insert_text((20, 40), "native words here")
        annotated = doc.new_page(width=300, height=300)
        annotated.insert_image(annotated.rect, stream=scan)
        annotated.add_freetext_annot(
            pymupdf.Rect(20, 20, 200, 60),
            "annotation words here",
        )
        widget_page = doc.new_page(width=300, height=300)
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = "party"
        widget.field_value = "widget words here"
        widget.rect = pymupdf.Rect(20, 20, 200, 50)
        widget_page.add_widget(widget)
        doc.save(str(path))
    finally:
        doc.close()


def test_native_word_rects_match_extraction(tmp_path: Path) -> None:
    src = tmp_path / "annotated.pdf"
    _annotated(src)
    doc = pymupdf.open(str(src))
    try:
        for page in doc:
            expected = [tuple(word[:4]) for word in page.get_text("words")]
            assert _native_word_rects(page) == expected
    finally:
        doc.close()
