"""Check the writer's native-text detection against PyMuPDF on real PDFs.

    uv run python benchmarks/dedup_agreement.py ~/pdfs [more folders or files]

The writer skips an OCR box that lands on a word the page already has. It
finds those words with its own content-stream walk, not with PyMuPDF. This
script measures how often the two agree, through the public API:

* **On native words.** Every word PyMuPDF extracts is offered as an OCR box
  at that word's rectangle. The writer should skip all of them.
* **On empty areas.** Boxes are offered in cells of a grid that no PyMuPDF
  word touches. The writer should draw all of them.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pymupdf

from pdf_textlayer import ParsedPage, PdfError, TextBox, write_textlayer

GRID = 12


def boxes_for(page: pymupdf.Page) -> tuple[list[TextBox], list[TextBox]]:
    """Boxes on the page's native words, and boxes where it has none."""
    to_display = page.rotation_matrix
    words = [pymupdf.Rect(word[:4]) for word in page.get_text("words")]
    on_words = []

    for word in words:
        if word.is_empty or word.get_area() < 4:
            continue

        rect = word * to_display
        on_words.append(TextBox("word", rect.x0, rect.y0, rect.width, rect.height))

    in_gaps = []
    cell_width = page.rect.width / GRID
    cell_height = page.rect.height / GRID

    for row in range(GRID):
        for column in range(GRID):
            cell = pymupdf.Rect(
                column * cell_width + 2,
                row * cell_height + 2,
                (column + 1) * cell_width - 2,
                (row + 1) * cell_height - 2,
            )
            unrotated = cell * page.derotation_matrix

            if any(unrotated.intersects(word) for word in words):
                continue

            in_gaps.append(TextBox("gap", cell.x0, cell.y0, cell.width, cell.height))

    return on_words, in_gaps


def main() -> None:
    paths: list[Path] = []

    for argument in sys.argv[1:]:
        path = Path(argument).expanduser()
        paths += sorted(path.rglob("*.pdf")) if path.is_dir() else [path]

    documents = 0
    unreadable: list[str] = []
    word_boxes = word_boxes_drawn = 0
    gap_boxes = gap_boxes_drawn = 0
    worst: list[tuple[float, str]] = []

    with tempfile.TemporaryDirectory() as scratch:
        output = Path(scratch) / "out.pdf"

        for path in paths:
            try:
                document = pymupdf.open(str(path))
            except Exception:
                continue

            try:
                if document.needs_pass or document.page_count == 0:
                    continue

                on_words: list[ParsedPage] = []
                in_gaps: list[ParsedPage] = []

                for index, page in enumerate(document):
                    words, gaps = boxes_for(page)
                    on_words.append(ParsedPage(index + 1, words))
                    in_gaps.append(ParsedPage(index + 1, gaps))
            except Exception:
                continue
            finally:
                document.close()

            try:
                drawn_on_words = write_textlayer(path, on_words, output)["items_drawn"]
                drawn_in_gaps = write_textlayer(path, in_gaps, output)["items_drawn"]
            except PdfError as error:
                unreadable.append(f"{path.name}: {error}")
                continue

            documents += 1
            offered = sum(len(page.boxes) for page in on_words)
            word_boxes += offered
            word_boxes_drawn += drawn_on_words
            gaps_offered = sum(len(page.boxes) for page in in_gaps)
            gap_boxes += gaps_offered
            gap_boxes_drawn += drawn_in_gaps

            if offered and drawn_on_words:
                worst.append((drawn_on_words / offered, path.name))

    print(f"Documents: {documents} read, {len(unreadable)} the writer could not read")
    for line in unreadable:
        print(f"  {line}")

    if word_boxes:
        skipped = word_boxes - word_boxes_drawn
        print(
            f"Boxes on native words: {word_boxes:,} offered, {skipped:,} skipped "
            f"({skipped / word_boxes:.2%}), {word_boxes_drawn:,} drawn again"
        )

    if gap_boxes:
        print(
            f"Boxes on empty areas:  {gap_boxes:,} offered, {gap_boxes_drawn:,} drawn "
            f"({gap_boxes_drawn / gap_boxes:.2%}), "
            f"{gap_boxes - gap_boxes_drawn:,} wrongly skipped"
        )

    for share, name in sorted(worst, reverse=True)[:10]:
        print(f"  {share:7.2%} of native words drawn again in {name}")


if __name__ == "__main__":
    main()
