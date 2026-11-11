"""Build the synthetic benchmark dataset.

Each document is a PDF plus a ``.boxes.json`` file with the OCR boxes to
overlay. The profiles mirror the document shapes the writer sees in
production: scanned, scanned with heavy vector content, native with scanned
exhibits, mixed native and scanned on one page, and rotated.

    uv run python benchmarks/generate.py benchmarks/data
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pymupdf

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
SCAN_WIDTH = 850
SCAN_HEIGHT = 1100

WORDS = [
    "agreement",
    "services",
    "fees",
    "payable",
    "net",
    "thirty",
    "days",
    "contractor",
    "client",
    "shall",
    "deliver",
    "invoice",
    "statement",
    "work",
    "order",
    "term",
    "renewal",
    "notice",
    "termination",
    "liability",
    "indemnify",
    "confidential",
    "schedule",
    "exhibit",
    "amendment",
    "section",
    "party",
    "Müller",
    "café",
    "naïve",
    "résumé",
    "€9,500",
    "§4.2",
    "2026-017",
    "SOW-AR",
    "No.",
]  # noqa: SIM905

# (profile, pages). Box counts follow from the profile.
DATASET: list[tuple[str, int]] = [
    ("scanned", 10),
    ("scanned", 100),
    ("scanned", 500),
    ("scanned_vector", 10),
    ("scanned_vector", 50),
    ("scanned_vector", 200),
    ("native_with_exhibits", 100),
    ("native_with_exhibits", 1000),
    ("mixed", 20),
    ("mixed", 200),
    ("rotated", 50),
]

PROFILE_LABELS = {
    "scanned": "Scanned",
    "scanned_vector": "Scanned, vector-heavy page content",
    "native_with_exhibits": "Native text, scanned exhibit every 10th page",
    "mixed": "Mixed native and scanned on every page",
    "rotated": "Scanned, rotated pages and rotated boxes",
}

Box = list  # [text, x, y, width, height, confidence, rotation]


def _line(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(words))


def _scan_png(rng: random.Random) -> bytes:
    """A grey page with noisy text-like bands, about the size of a real scan."""
    samples = bytearray(b"\xeb" * (SCAN_WIDTH * SCAN_HEIGHT))

    for line in range(45):
        top = 90 + line * 21
        left = rng.randrange(60, 120)
        length = rng.randrange(380, 640)

        for row in range(top, top + 4):
            start = row * SCAN_WIDTH + left
            samples[start : start + length] = rng.randbytes(length)

    pixmap = pymupdf.Pixmap(pymupdf.csGRAY, SCAN_WIDTH, SCAN_HEIGHT, bytes(samples), 0)

    return pixmap.tobytes("png")


def _scanned_boxes(rng: random.Random, count: int) -> list[Box]:
    return [
        [_line(rng, 8), 60.0, 70.0 + line * 15.2, rng.uniform(300, 480), 11.0, 0.9, 0.0]
        for line in range(count)
    ]


def _scanned_page(doc: pymupdf.Document, rng: random.Random) -> list[Box]:
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(page.rect, stream=_scan_png(rng))

    return _scanned_boxes(rng, 45)


def _scanned_vector_page(doc: pymupdf.Document, rng: random.Random) -> list[Box]:
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_image(page.rect, stream=_scan_png(rng))
    shape = page.new_shape()

    for row in range(600):
        y = 20 + row * 1.25
        shape.draw_line((20, y), (590, y))

    shape.finish(width=0.1, color=(0.9, 0.9, 0.9))
    shape.commit()

    return _scanned_boxes(rng, 45)


def _native_page(doc: pymupdf.Document, rng: random.Random) -> list[Box]:
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    for line in range(45):
        page.insert_text((60, 80 + line * 15.2), _line(rng, 10), fontsize=10)

    return []


def _mixed_page(doc: pymupdf.Document, rng: random.Random) -> list[Box]:
    """Native body text on top, a scanned region below.

    OCR reports both. The boxes on the native lines must be skipped.
    """
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    boxes: list[Box] = []

    for line in range(20):
        text = _line(rng, 10)
        baseline = 80 + line * 15.2
        page.insert_text((60, baseline), text, fontsize=10)
        width = pymupdf.get_text_length(text, fontsize=10)
        boxes.append([text, 60.0, baseline - 10.0, width, 12.0, 0.9, 0.0])

    page.insert_image(pymupdf.Rect(40, 400, 572, 780), stream=_scan_png(rng))

    for line in range(25):
        boxes.append(
            [
                _line(rng, 8),
                60.0,
                410.0 + line * 14.5,
                rng.uniform(300, 480),
                11.0,
                0.9,
                0.0,
            ]
        )

    return boxes


def _rotated_page(doc: pymupdf.Document, rng: random.Random, index: int) -> list[Box]:
    """A landscape scan stored with /Rotate, plus sideways margin boxes."""
    rotation = (90, 180, 270)[index % 3]
    width, height = (
        (PAGE_HEIGHT, PAGE_WIDTH)
        if rotation in (90, 270)
        else (PAGE_WIDTH, PAGE_HEIGHT)
    )
    page = doc.new_page(width=width, height=height)
    page.insert_image(page.rect, stream=_scan_png(rng))
    page.set_rotation(rotation)
    display_height = page.rect.height

    boxes = [
        [_line(rng, 8), 70.0, 60.0 + line * 15.2, rng.uniform(260, 420), 11.0, 0.9, 0.0]
        for line in range(30)
        if 60.0 + line * 15.2 + 11.0 < display_height - 40
    ]
    boxes = boxes[:30]

    while len(boxes) < 30:
        boxes.append([_line(rng, 3), 70.0, 60.0, 120.0, 11.0, 0.9, 0.0])

    boxes.append(["Sideways stamp received", 20.0, 100.0, 12.0, 140.0, 0.9, 90.0])
    boxes.append(["Margin note see exhibit", 40.0, 260.0, 12.0, 140.0, 0.9, 270.0])
    boxes.append(["Upside down footer note", 300.0, 20.0, 150.0, 12.0, 0.9, 180.0])

    return boxes


def build_document(profile: str, pages: int, directory: Path) -> Path:
    name = f"{profile}_{pages}"
    pdf_path = directory / f"{name}.pdf"
    boxes_path = directory / f"{name}.boxes.json"

    if pdf_path.exists() and boxes_path.exists():
        return pdf_path

    rng = random.Random(f"{profile}-{pages}")
    doc = pymupdf.open()
    parsed_pages: list[dict] = []

    try:
        for index in range(pages):
            if profile == "scanned":
                boxes = _scanned_page(doc, rng)
            elif profile == "scanned_vector":
                boxes = _scanned_vector_page(doc, rng)
            elif profile == "native_with_exhibits":
                if index % 10 == 9:
                    boxes = _scanned_boxes(rng, 90)
                    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
                    page.insert_image(page.rect, stream=_scan_png(rng))

                    for box in boxes:
                        box[2] = 40.0 + (box[2] - 70.0) / 2
                        box[4] = 6.5
                else:
                    boxes = _native_page(doc, rng)
            elif profile == "mixed":
                boxes = _mixed_page(doc, rng)
            elif profile == "rotated":
                boxes = _rotated_page(doc, rng, index)
            else:
                raise ValueError(profile)

            if boxes:
                parsed_pages.append({"page": index + 1, "boxes": boxes})

        doc.save(str(pdf_path), deflate=True)
    finally:
        doc.close()

    boxes_path.write_text(
        json.dumps(
            {
                "profile": profile,
                "label": PROFILE_LABELS[profile],
                "pages": pages,
                "ocr_boxes": sum(len(page["boxes"]) for page in parsed_pages),
                "parsed_pages": parsed_pages,
            }
        )
    )

    return pdf_path


def main() -> None:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmarks/data")
    directory.mkdir(parents=True, exist_ok=True)

    for profile, pages in DATASET:
        pdf_path = build_document(profile, pages, directory)
        size_mb = pdf_path.stat().st_size / 1e6
        print(f"{pdf_path.name}: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
