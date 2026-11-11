"""Rotation, native-word dedup, and what the writer puts on each page.

PyMuPDF builds the fixtures and reads the output back. It is a development
dependency only: the package itself does not import it.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdf_textlayer import Options, ParsedPage, PdfError, TextBox, write_textlayer

# Resource name the writer gives its font on each page.
OVERLAY_FONT_RESOURCE = "pdftextlayer"


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
            font for font in page.get_fonts() if font[4] == OVERLAY_FONT_RESOURCE
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
                        f"Image region line {line} \u00e9\u00df \u2014 \u20ac9\u200b",
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
    """The 0.2 writer: one ``page.insert_text`` per box with PyMuPDF.

    The Rust writer has to put the same words in the same places.
    """
    font = pymupdf.Font("helv")
    by_num = {page.page_number: page for page in pages}
    doc = pymupdf.open(str(src))
    try:
        for idx in range(doc.page_count):
            parsed = by_num.get(idx + 1)
            if parsed is None:
                continue
            page = doc[idx]
            native = [tuple(word[:4]) for word in page.get_text("words")]
            for box in parsed.boxes:
                if not box.text.strip():
                    continue
                rect = pymupdf.Rect(box.x, box.y, box.x + box.width, box.y + box.height)
                rect = rect * page.derotation_matrix
                if _covers_any(rect, native):
                    continue
                text = "".join(
                    char if font.glyph_advance(ord(char)) > 0 else "?"
                    for char in box.text
                )
                rotation = round((box.rotation + page.rotation) / 90) * 90 % 360
                horizontal = rotation in (0, 180)
                inline = rect.width if horizontal else rect.height
                cross = rect.height if horizontal else rect.width
                width_at_1pt = font.text_length(text, fontsize=1)
                font_size = min(inline / width_at_1pt, max(cross * 1.2, 1.0))
                origin = {
                    0: (rect.x0, rect.y1),
                    90: (rect.x1, rect.y1),
                    180: (rect.x1, rect.y0),
                    270: (rect.x0, rect.y0),
                }[rotation]
                page.insert_font(fontname="reference", fontbuffer=font.buffer)
                page.insert_text(
                    origin,
                    text,
                    fontname="reference",
                    fontsize=font_size,
                    render_mode=3,
                    color=(0, 0, 0),
                    rotate=rotation,
                )
        doc.save(str(dest))
    finally:
        doc.close()


def _covers_any(
    rect: pymupdf.Rect, words: list[tuple[float, float, float, float]]
) -> bool:
    area = rect.width * rect.height
    for x0, y0, x1, y1 in words:
        overlap_width = min(rect.x1, x1) - max(rect.x0, x0)
        overlap_height = min(rect.y1, y1) - max(rect.y0, y0)
        word_area = (x1 - x0) * (y1 - y0)
        if overlap_width <= 0 or overlap_height <= 0 or word_area <= 0:
            continue
        if overlap_width * overlap_height / min(area, word_area) >= 0.5:
            return True
    return False


def _words(path: Path) -> list[list[tuple]]:
    doc = pymupdf.open(str(path))
    try:
        return [page.get_text("words") for page in doc]
    finally:
        doc.close()


def _images(path: Path) -> list[list[int]]:
    doc = pymupdf.open(str(path))
    try:
        return [[image[0] for image in page.get_images(full=True)] for page in doc]
    finally:
        doc.close()


def test_rust_writer_matches_the_pymupdf_writer(tmp_path: Path) -> None:
    src = tmp_path / "synthetic.pdf"
    pages = _synthetic_contract(src)
    _reference_write(src, pages, tmp_path / "reference.pdf")
    write_textlayer(src, pages, tmp_path / "rust.pdf")

    reference = _words(tmp_path / "reference.pdf")
    written = _words(tmp_path / "rust.pdf")
    assert any(reference)
    assert len(written) == len(reference)

    for page_words, reference_words in zip(written, reference, strict=True):
        assert [word[4:] for word in page_words] == [
            word[4:] for word in reference_words
        ]
        for word, reference_word in zip(page_words, reference_words, strict=True):
            assert word[:4] == pytest.approx(reference_word[:4], abs=0.01)

    assert _images(tmp_path / "rust.pdf") == _images(tmp_path / "reference.pdf")


def test_text_in_any_script_is_kept(tmp_path: Path) -> None:
    src = tmp_path / "blank.pdf"
    dest = tmp_path / "out.pdf"
    _blank(src, width=400)
    text = "Fee \u4e2d\u6587 \u0416\u0443\u043a \u03b1\u03b2\u03b3 \u20ac5"
    write_textlayer(src, [ParsedPage(1, [TextBox(text, 20, 40, 300, 12)])], dest)
    doc = pymupdf.open(str(dest))
    try:
        assert text in doc[0].get_text()
        assert len(doc[0].search_for("\u4e2d\u6587")) == 1
    finally:
        doc.close()


def test_undrawable_characters_become_question_marks(tmp_path: Path) -> None:
    src = tmp_path / "blank.pdf"
    dest = tmp_path / "out.pdf"
    _blank(src, width=400)
    box = TextBox("Fee\u200b paid \U0001f600\x00", 20, 40, 200, 12)
    write_textlayer(src, [ParsedPage(1, [box])], dest)
    doc = pymupdf.open(str(dest))
    try:
        assert "Fee? paid ??" in doc[0].get_text()
    finally:
        doc.close()


def test_overlay_font_is_embedded_once_per_page(tmp_path: Path) -> None:
    src = tmp_path / "synthetic.pdf"
    pages = _synthetic_contract(src)
    dest = tmp_path / "out.pdf"
    write_textlayer(src, pages, dest)
    doc = pymupdf.open(str(dest))
    try:
        counts = [
            sum(1 for font in page.get_fonts() if font[4] == OVERLAY_FONT_RESOURCE)
            for page in doc
        ]
    finally:
        doc.close()
    assert counts == [1, 0, 1, 0, 1]


def test_overlay_font_is_one_small_object_for_the_document(tmp_path: Path) -> None:
    src = tmp_path / "synthetic.pdf"
    pages = _synthetic_contract(src)
    dest = tmp_path / "out.pdf"
    write_textlayer(src, pages, dest)
    doc = pymupdf.open(str(dest))
    try:
        xrefs = {
            font[0]
            for page in doc
            for font in page.get_fonts()
            if font[4] == OVERLAY_FONT_RESOURCE
        }
    finally:
        doc.close()
    assert len(xrefs) == 1
    assert dest.stat().st_size - src.stat().st_size < 8_000


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


@pytest.mark.parametrize(
    ("page_number", "box", "drawn"),
    [
        (1, TextBox("scanned words here", 20, 20, 150, 14), 1),
        (2, TextBox("native words here", 20, 28, 95, 14), 0),
        (3, TextBox("annotation words here", 22, 20, 110, 16), 0),
        (4, TextBox("widget words here", 22, 26, 90, 16), 0),
    ],
)
def test_annotation_and_form_field_text_counts_as_native(
    tmp_path: Path, page_number: int, box: TextBox, drawn: int
) -> None:
    src = tmp_path / "annotated.pdf"
    _annotated(src)
    doc = pymupdf.open(str(src))
    try:
        words = doc[page_number - 1].get_text("words")
    finally:
        doc.close()
    # The box sits on the words PyMuPDF reports for the page, when it has any.
    if not drawn:
        assert _covers_any(
            pymupdf.Rect(box.x, box.y, box.x + box.width, box.y + box.height),
            [tuple(word[:4]) for word in words],
        )

    stats = write_textlayer(src, [ParsedPage(page_number, [box])], tmp_path / "out.pdf")
    assert stats["items_drawn"] == drawn


def test_content_that_leaves_the_matrix_changed_does_not_move_the_text(
    tmp_path: Path,
) -> None:
    src = tmp_path / "dirty.pdf"
    dest = tmp_path / "out.pdf"
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=300, height=300)
        # Scanner-style content: scale the matrix at the top level, open a
        # save that is never closed, and restore once too often up front.
        stream = doc.get_new_xref()
        doc.update_object(stream, "<<>>")
        doc.update_stream(
            stream, b"Q 0.5 0 0 0.5 40 40 cm q 2 0 0 2 0 0 cm 0 0 10 10 re f"
        )
        page.set_contents(stream)
        doc.save(str(src))
    finally:
        doc.close()

    expected = pymupdf.Rect(40, 100, 140, 112)
    write_textlayer(
        src,
        [ParsedPage(1, [TextBox("Unmoved overlay text", 40, 100, 100, 12)])],
        dest,
    )
    _assert_search(dest, "Unmoved overlay text", expected)


def test_inherited_and_shared_resources_are_not_edited_in_place(
    tmp_path: Path,
) -> None:
    src = tmp_path / "shared.pdf"
    dest = tmp_path / "out.pdf"
    doc = pymupdf.open()
    try:
        doc.new_page(width=300, height=300)
        doc.new_page(width=300, height=300)
        first, second = doc[0], doc[1]
        first.insert_text((20, 40), "first page")
        # Point both pages at one resources object.
        resources = doc.xref_get_key(first.xref, "Resources")
        shared = doc.get_new_xref()
        doc.update_object(shared, resources[1])
        doc.xref_set_key(first.xref, "Resources", f"{shared} 0 R")
        doc.xref_set_key(second.xref, "Resources", f"{shared} 0 R")
        doc.save(str(src))
    finally:
        doc.close()

    write_textlayer(src, [ParsedPage(1, [TextBox("Overlay", 20, 100, 60, 12)])], dest)
    doc = pymupdf.open(str(dest))
    try:
        first_fonts = [font[4] for font in doc[0].get_fonts()]
        second_fonts = [font[4] for font in doc[1].get_fonts()]
    finally:
        doc.close()
    assert OVERLAY_FONT_RESOURCE in first_fonts
    assert OVERLAY_FONT_RESOURCE not in second_fonts


def test_writing_twice_does_not_stack_a_second_layer(tmp_path: Path) -> None:
    src = tmp_path / "blank.pdf"
    once = tmp_path / "once.pdf"
    twice = tmp_path / "twice.pdf"
    _blank(src)
    pages = [ParsedPage(1, [TextBox("Only once", 40, 40, 80, 12)])]
    assert write_textlayer(src, pages, once)["items_drawn"] == 1
    assert write_textlayer(once, pages, twice)["items_drawn"] == 0


def test_cropped_page_places_text_from_the_crop_box_corner(tmp_path: Path) -> None:
    src = tmp_path / "cropped.pdf"
    dest = tmp_path / "out.pdf"
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=400, height=400)
        page.set_cropbox(pymupdf.Rect(50, 80, 350, 380))
        doc.save(str(src))
    finally:
        doc.close()

    expected = pymupdf.Rect(30, 40, 130, 52)
    write_textlayer(
        src,
        [ParsedPage(1, [TextBox("Cropped page overlay", 30, 40, 100, 12)])],
        dest,
    )
    _assert_search(dest, "Cropped page overlay", expected)


def test_missing_input_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        write_textlayer(tmp_path / "missing.pdf", [], tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


def test_input_that_is_not_a_pdf_raises_pdf_error(tmp_path: Path) -> None:
    src = tmp_path / "not.pdf"
    src.write_bytes(b"this is not a pdf")
    with pytest.raises(PdfError):
        write_textlayer(src, [], tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


@pytest.mark.parametrize("user_password", ["", "secret"])
def test_encrypted_input_raises_pdf_error(tmp_path: Path, user_password: str) -> None:
    src = tmp_path / "encrypted.pdf"
    doc = pymupdf.open()
    try:
        doc.new_page()
        doc.save(
            str(src),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw=user_password,
        )
    finally:
        doc.close()

    with pytest.raises(PdfError, match="encrypted"):
        write_textlayer(src, [], tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


def test_unknown_font_raises_value_error(tmp_path: Path) -> None:
    src = tmp_path / "blank.pdf"
    _blank(src)
    with pytest.raises(ValueError, match="comic"):
        write_textlayer(src, [], tmp_path / "out.pdf", options=Options(font="comic"))
