"""Turn the sample scan into a searchable PDF.

``scan.pdf`` is a raster page with no text layer. ``parsed.json`` is the
LiteParse ``--format json`` output for that page. The script writes
``searchable.pdf``, which looks the same and can be searched.
"""

from pathlib import Path

from pdf_textlayer import make_searchable_pdf

HERE = Path(__file__).resolve().parent


def main() -> None:
    stats = make_searchable_pdf(
        HERE / "scan.pdf",
        HERE / "parsed.json",
        HERE / "searchable.pdf",
    )

    print(f"Wrote {HERE / 'searchable.pdf'}")
    print(stats)


if __name__ == "__main__":
    main()
