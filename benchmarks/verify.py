"""Compare what two writers put on every page of the benchmark outputs.

    uv run python benchmarks/verify.py batched rust

Reads ``benchmarks/out/<variant>/*.pdf`` with PyMuPDF and compares, page by
page, the extracted words (text and order), each word's rectangle, and the
images. A page differs when any of those differ. Rectangles may differ by
``--tolerance`` points: the two writers print coordinates with different
precision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf
from generate import DATASET

HERE = Path(__file__).resolve().parent


def compare(
    left_path: Path, right_path: Path, tolerance: float
) -> tuple[int, int, float]:
    """Return ``(words compared, pages that differ, largest rectangle shift)``."""
    left = pymupdf.open(str(left_path))
    right = pymupdf.open(str(right_path))
    words = 0
    pages_that_differ = 0
    largest_shift = 0.0

    try:
        if left.page_count != right.page_count:
            return 0, max(left.page_count, right.page_count), 0.0

        for left_page, right_page in zip(left, right, strict=True):
            left_words = left_page.get_text("words")
            right_words = right_page.get_text("words")
            words += len(left_words)

            same_text = [word[4:] for word in left_words] == [
                word[4:] for word in right_words
            ]
            same_images = [image[0] for image in left_page.get_images()] == [
                image[0] for image in right_page.get_images()
            ]
            shift = 0.0

            if same_text:
                shift = max(
                    (
                        abs(left_value - right_value)
                        for left_word, right_word in zip(
                            left_words, right_words, strict=True
                        )
                        for left_value, right_value in zip(
                            left_word[:4], right_word[:4], strict=True
                        )
                    ),
                    default=0.0,
                )
                largest_shift = max(largest_shift, shift)

            if not same_text or not same_images or shift > tolerance:
                pages_that_differ += 1
    finally:
        left.close()
        right.close()

    return words, pages_that_differ, largest_shift


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--out", type=Path, default=HERE / "out")
    parser.add_argument("--tolerance", type=float, default=0.01)
    args = parser.parse_args()

    total_words = 0
    total_pages = 0
    total_differ = 0
    largest_shift = 0.0
    rows = []

    for profile, pages in DATASET:
        name = f"{profile}_{pages}.pdf"
        words, differ, shift = compare(
            args.out / args.left / name, args.out / args.right / name, args.tolerance
        )
        total_words += words
        total_pages += pages
        total_differ += differ
        largest_shift = max(largest_shift, shift)
        rows.append(
            {
                "document": name,
                "words": words,
                "pages_that_differ": differ,
                "largest_shift": shift,
            }
        )
        print(f"{name:34} {words:8} words  {differ:4} pages differ  shift {shift:.4f}")

    print(
        f"{args.left} vs {args.right}: {total_words} words on {total_pages} pages, "
        f"{total_differ} pages differ, largest shift {largest_shift:.4f} pt"
    )
    (args.out / f"verify_{args.left}_{args.right}.json").write_text(
        json.dumps(
            {
                "left": args.left,
                "right": args.right,
                "words": total_words,
                "pages": total_pages,
                "pages_that_differ": total_differ,
                "largest_shift": largest_shift,
                "documents": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
