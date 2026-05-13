# pdf-textlayer

Add an invisible, searchable text layer to a PDF from OCR JSON. Pages without OCR text are left exactly as in the input — native text, embedded fonts, and annotations are preserved. Built on **[PyMuPDF](https://pymupdf.readthedocs.io/)**.

Published on **[PyPI](https://pypi.org/project/pdf-textlayer/)** · Developed on **[GitHub](https://github.com/patelvivekdev/pdf-textlayer)**.

## What it does

Given a PDF and a JSON file from an OCR pipeline, `pdf-textlayer` writes a new PDF where every OCR-detected word is drawn at its bounding box in **PDF render mode 3** (invisible glyphs). The original page content is unchanged; the new layer is what `Ctrl+F`, screen readers, and indexers see.

- **Mixed PDFs are handled correctly.** A 200-page document with two scanned exhibits gets the overlay only on the scanned pages. Pages with native text are passed through untouched.
- **Adapter-based.** Out of the box it understands [liteparse](https://github.com/run-llama/liteparse) JSON. Other sources (Azure Document Intelligence, Textract, hOCR, MinerU, your own pipeline) can be added by writing a small adapter — see [Adapters](#adapters).
- **Minimal dependencies.** Only `pymupdf`.

## Installation

Python **3.11+**.

```bash
pip install pdf-textlayer
# or
uv add pdf-textlayer
```

This installs the `pdf-textlayer` CLI and the `pdf_textlayer` Python package.

## CLI

```bash
# 1. Produce OCR JSON for your PDF (example: liteparse)
liteparse input.pdf --format json -o parsed.json

# 2. Build the searchable PDF
pdf-textlayer input.pdf parsed.json output.pdf
```

Options:

```bash
pdf-textlayer input.pdf parsed.json output.pdf \
    --from liteparse \           # adapter name (default: liteparse)
    --min-confidence 0.5 \       # drop low-confidence OCR items
    --font china-s               # CJK font for Asian-language documents
```

## Python API

```python
from pdf_textlayer import make_searchable_pdf, Options

stats = make_searchable_pdf(
    "input.pdf",
    "parsed.json",                       # path, or already-loaded dict
    "output.pdf",
    adapter="liteparse",                 # default
    options=Options(min_confidence=0.5, font="helv"),
)
print(stats)
# {'pages': 19, 'ocr_pages': [1, 2, ..., 19], 'items_drawn': 7643}
```

If you already have the OCR results in memory (e.g. from another library), build `ParsedPage` objects directly and skip the adapter:

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

Coordinates use PyMuPDF's convention: origin at the **top-left** of the page, `x` right, `y` down, in PDF user-space points. `(x, y)` is the top-left corner of the box.

## How mixed pages are handled

For each page in the input PDF, the adapter decides whether the page has OCR content to overlay:

1. If the adapter yields a `ParsedPage` with non-empty `boxes` → the page receives an invisible overlay (subject to `min_confidence`).
2. If the adapter omits the page, or yields it with no boxes → the page is left exactly as-is.

For the bundled liteparse adapter, "has OCR content" means the page contains at least one `textItems` entry with `fontName == "OCR"`. Native-text items keep the real font name and are skipped, so the original PDF text is never duplicated.

## Adapters

An adapter is a small class that turns a source's JSON into normalized `ParsedPage` objects. The protocol is one method:

```python
from collections.abc import Iterable
from pdf_textlayer import ParsedPage, register_adapter

class MyAdapter:
    def parse(self, data) -> Iterable[ParsedPage]:
        for page in data["pages"]:
            yield ParsedPage(
                page_number=page["index"] + 1,        # 1-indexed
                boxes=[...],                          # list[TextBox]
            )

register_adapter("mine", MyAdapter())
# pdf-textlayer in.pdf in.json out.pdf --from mine
```

Bundled adapters:

| Name | Source | Notes |
|------|--------|-------|
| `liteparse` | [liteparse](https://github.com/run-llama/liteparse) `--format json` | Items with `fontName == "OCR"` per the [OCR API spec](https://github.com/run-llama/liteparse/blob/main/OCR_API_SPEC.md). |

PRs adding more adapters (Azure DI, Textract, hOCR, …) are welcome.

## Fonts and non-Latin text

The default font (`helv` = Helvetica) covers Latin-1. Unsupported glyphs are replaced with `?` so each item still emits a usable text run; set `Options(sanitize_unsupported=False)` to drop those items instead.

For CJK documents, pass a bundled PyMuPDF font:

```bash
pdf-textlayer in.pdf parsed.json out.pdf --font china-s
# china-s, china-t, japan, korea — bundled with PyMuPDF
```

## How the overlay is drawn

For each box, the font is scaled so the rendered text width matches the box width. The insertion point is the glyph baseline, placed at `(x, y + height)`. Text is written with PDF render mode 3 — present in the content stream, never painted — so search, copy/paste, and assistive tech work, but the visible page is unchanged.

## Limitations

- **Rotated text is drawn upright.** OCR sources that don't include rotation metadata can't be rendered rotated; in practice most pipelines de-rotate before emitting boxes.
- **Invisible text uses PDF render mode 3.** All modern viewers and search indexers handle this correctly; very old tooling may not extract it.
- **No de-duplication across overlay and native text.** If you point the tool at native-text pages but the adapter treats them as OCR, you'll get duplicated search hits. The bundled `liteparse` adapter avoids this by filtering on `fontName`.

## Contributing

Bug reports, docs improvements, and new adapters are welcome — open an issue or PR on [GitHub](https://github.com/patelvivekdev/pdf-textlayer/issues).

## License

Released under the **MIT License**. See [`LICENSE`](LICENSE) for the full text.
