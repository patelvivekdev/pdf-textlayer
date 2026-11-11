# pdf-textlayer (Python)

Add an invisible, searchable text layer to a PDF from OCR JSON. The writer is Rust; this package is a typed wrapper around it with no runtime dependencies. Pages without OCR text are left as in the input: native text, embedded fonts, and annotations are preserved.

Source, benchmarks, and the Node.js and Rust packages: **[GitHub](https://github.com/patelvivekdev/pdf-textlayer)**.

## Installation

Python **3.11+**.

```bash
pip install pdf-textlayer
# or
uv add pdf-textlayer
```

This installs the `pdf_textlayer` package and the `pdf-textlayer` command.

## CLI

```bash
# 1. Produce OCR JSON. --extract-text-metadata adds per-item rotation.
lit parse input.pdf --format json --extract-text-metadata -o parsed.json

# 2. Build the searchable PDF
pdf-textlayer input.pdf parsed.json output.pdf \
    --from liteparse \       # JSON format (default: liteparse)
    --min-confidence 0.5 \   # drop low-confidence OCR items
    --font tiro              # size the text with Times widths (helv, tiro, cour)
```

## Library

```python
from pdf_textlayer import Options, make_searchable_pdf

stats = make_searchable_pdf(
    "input.pdf",
    "parsed.json",              # path, or the already-loaded dict
    "output.pdf",
    adapter="liteparse",        # default
    options=Options(min_confidence=0.5, font="helv"),
)
print(stats)
# {'pages': 19, 'ocr_pages': [1, 2, ..., 19], 'items_drawn': 7643}
```

OCR results from any other source go in as `ParsedPage` objects:

```python
from pdf_textlayer import ParsedPage, TextBox, write_textlayer

pages = [
    ParsedPage(
        page_number=1,
        boxes=[
            TextBox(text="Hello", x=72.0, y=108.0, width=46.0, height=12.0, confidence=0.98),
            TextBox(text="world", x=124.0, y=108.0, width=48.0, height=12.0, confidence=0.97),
        ],
    ),
]
write_textlayer("input.pdf", pages, "output.pdf")
```

| Function | Does |
|---|---|
| `make_searchable_pdf(input_pdf, source, output_pdf, *, adapter, options)` | Reads OCR JSON (path or dict) with an adapter and writes the PDF. `source` may also be a list of `ParsedPage`. |
| `write_textlayer(input_pdf, pages, output_pdf, *, options)` | Writes the PDF from `ParsedPage` objects. |
| `add_textlayer(pdf_bytes, pages, *, options)` | Same, in memory. Returns `(pdf_bytes, stats)`. |
| `load_pages(source, *, adapter)` | Returns the `ParsedPage` objects an adapter reads from OCR JSON, to inspect or edit before writing. |

All of them run in Rust with the GIL released. They raise `FileNotFoundError` for a missing input, `PdfError` for a file that is not a readable PDF or is encrypted, and `ValueError` for an unknown adapter or font, or invalid JSON.

Coordinates have their origin at the **top-left** of the displayed page, `x` right, `y` down, in PDF points. `(x, y)` is the top-left corner of the box. `TextBox.rotation` is degrees counter-clockwise (`0`, `90`, `180`, `270`); other values snap to a right angle.

## Adapters

| Name | Source | Notes |
|------|--------|-------|
| `liteparse` | [liteparse](https://github.com/run-llama/liteparse) `lit parse --format json` | `pages[].text_items[]` with `font_name == "OCR"`. Reads `rotation` when present (`--extract-text-metadata`). `textItems` / `fontName` are still accepted. |

## License

MIT.
