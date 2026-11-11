"""Compare two writers' outputs as PDFium reads them.

    uvx --from pypdfium2 python benchmarks/verify_pdfium.py batched rust

``verify.py`` reads the outputs with MuPDF. PDFium is the engine in Chrome
and Edge, and it places and groups text by its own rules, so this is an
independent check that both outputs extract to the same text on every page.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pypdfium2 as pdfium

HERE = Path(__file__).resolve().parent


def page_texts(path: Path) -> list[str]:
    document = pdfium.PdfDocument(str(path))

    return [
        document[index].get_textpage().get_text_range()
        for index in range(len(document))
    ]


def main() -> None:
    left, right = sys.argv[1:3]
    pages = 0
    pages_that_differ = 0

    for left_path in sorted((HERE / "out" / left).glob("*.pdf")):
        left_texts = page_texts(left_path)
        right_texts = page_texts(HERE / "out" / right / left_path.name)
        differ = sum(
            left_text != right_text
            for left_text, right_text in zip(left_texts, right_texts, strict=True)
        )
        pages += len(left_texts)
        pages_that_differ += differ
        print(f"{left_path.name:34} {len(left_texts):5} pages  {differ:4} differ")

    print(
        f"{left} vs {right} in PDFium: {pages} pages, {pages_that_differ} pages differ"
    )


if __name__ == "__main__":
    main()
