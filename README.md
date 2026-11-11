# pdf-textlayer

Add an invisible, searchable text layer to a PDF from OCR JSON. Pages without OCR text are left as in the input: native text, embedded fonts, and annotations are preserved.

The writer is Rust (built on [lopdf](https://github.com/J-F-Liu/lopdf)). The same core ships as a Rust crate with a CLI, a Python package, and a Node.js package; each runs the same code and the same `pdf-textlayer` command.

| Package | Install | Source |
|---|---|---|
| Python | `pip install pdf-textlayer` | [`packages/python`](packages/python) |
| Node.js | `npm install pdf-textlayer` | [`packages/node`](packages/node) |
| Rust (library + CLI) | `cargo install pdf-textlayer` | [`crates/pdf-textlayer`](crates/pdf-textlayer) |

Full documentation (guides, API reference for every language, benchmarks) is a [Blume](https://useblume.dev) site in [`docs/`](docs). See [Docs site](#docs-site) to run or deploy it.

## What it does

Given a PDF and a JSON file from an OCR pipeline, `pdf-textlayer` writes a new PDF where every OCR-detected word is drawn at its bounding box in **PDF render mode 3** (invisible glyphs). The original page content is unchanged; the new layer is what `Ctrl+F`, screen readers, and indexers see.

- **Mixed PDFs are handled correctly.** A 200-page document with two scanned exhibits gets the overlay only on the scanned pages. Pages with native text are passed through untouched.
- **Adapter-based.** Out of the box it understands [liteparse](https://github.com/run-llama/liteparse) JSON. Boxes from any other source (Azure Document Intelligence, Textract, hOCR, your own pipeline) go in as pages of text boxes. See [Adapters](#adapters).
- **No runtime dependencies.** Each package holds one compiled library.
- **Fast.** A 500-page scan with 22,500 OCR boxes takes about 0.4 s, ten times faster than 0.2. See [Benchmarks](#benchmarks).
- **Every script is kept.** CJK, Cyrillic, Greek, and the rest of the Basic Multilingual Plane are written as real text, with no font to choose.

## CLI

Every package installs the same `pdf-textlayer` command.

```bash
# 1. Produce OCR JSON. --extract-text-metadata adds per-item rotation.
lit parse input.pdf --format json --extract-text-metadata -o parsed.json

# 2. Build the searchable PDF
pdf-textlayer input.pdf parsed.json output.pdf
```

Options:

```bash
pdf-textlayer input.pdf parsed.json output.pdf \
    --from liteparse \           # adapter name (default: liteparse)
    --min-confidence 0.5 \       # drop low-confidence OCR items
    --font tiro                  # size the text with Times widths (helv, tiro, cour)
```

## Python

Python **3.11+**. [Full reference](packages/python/README.md).

```python
from pdf_textlayer import Options, ParsedPage, TextBox, make_searchable_pdf, write_textlayer

stats = make_searchable_pdf(
    "input.pdf",
    "parsed.json",                       # path, or already-loaded dict
    "output.pdf",
    adapter="liteparse",                 # default
    options=Options(min_confidence=0.5, font="helv"),
)
print(stats)
# {'pages': 19, 'ocr_pages': [1, 2, ..., 19], 'items_drawn': 7643}

# Boxes from any other source:
pages = [ParsedPage(page_number=1, boxes=[TextBox("Hello", x=72, y=108, width=46, height=12)])]
write_textlayer("input.pdf", pages, "output.pdf")
```

## Node.js

Node.js **20+**, ESM and CommonJS. [Full reference](packages/node/README.md).

```ts
import { makeSearchablePdf, writeTextLayer } from "pdf-textlayer";

const stats = await makeSearchablePdf("input.pdf", "parsed.json", "output.pdf", {
  adapter: "liteparse", // default
  minConfidence: 0.5,
  font: "helv",
});
// { pages: 19, ocrPages: [1, 2, ..., 19], itemsDrawn: 7643 }

await writeTextLayer("input.pdf", [{ pageNumber: 1, boxes: [{ text: "Hello", x: 72, y: 108, width: 46, height: 12 }] }], "output.pdf");
```

## Rust

```rust
use pdf_textlayer::{Adapter, Options, ParsedPage, TextBox, make_searchable_pdf, write_text_layer};

let stats = make_searchable_pdf("input.pdf", "parsed.json", "output.pdf", Adapter::Liteparse, &Options::default())?;

let pages = vec![ParsedPage {
    page_number: 1,
    boxes: vec![TextBox::new("Hello world", 72.0, 100.0, 120.0, 12.0)],
}];
let stats = write_text_layer("scan.pdf", &pages, "searchable.pdf", &Options::default())?;
```

Disable the default `cli` feature (`default-features = false`) to drop the `clap` dependency.

## Coordinates

Coordinates have their origin at the **top-left** of the displayed page, `x` right, `y` down, in PDF user-space points. `(x, y)` is the top-left corner of the box. A box's rotation is degrees counter-clockwise (`0`, `90`, `180`, `270`); other values snap to a right angle. Boxes are in the upright viewport LiteParse emits, including on pages with a `/Rotate` entry.

## How mixed pages are handled

For each page in the input PDF, the adapter decides whether the page has OCR content to overlay:

1. If the adapter yields a `ParsedPage` with non-empty `boxes` → the page receives an invisible overlay (subject to `min_confidence`).
2. If the adapter omits the page, or yields it with no boxes → the page is left exactly as-is.

For the bundled liteparse adapter, "has OCR content" means the page contains at least one `text_items` entry with `font_name == "OCR"`. Native-text items keep the real font name and are skipped.

An OCR box that covers at least half of an existing native word (or half of itself, whichever area is smaller) is skipped too. That keeps a mixed page (native body text plus a scanned exhibit) from indexing the native words twice. A FreeText annotation or form field counts as native text. So does a text layer written earlier: running the tool twice on the same file does not stack a second layer.

## Adapters

An adapter turns an OCR tool's JSON into pages of text boxes. Adapters live in the Rust core ([`adapters.rs`](crates/pdf-textlayer/src/adapters.rs)), so every package reads JSON the same way. `load_pages` (Python) and `loadPages` (Node.js) return what an adapter reads, to inspect or edit before writing.

| Name | Source | Notes |
|------|--------|-------|
| `liteparse` | [liteparse](https://github.com/run-llama/liteparse) `lit parse --format json` | `pages[].text_items[]` with `font_name == "OCR"`. Reads `rotation` when present (`--extract-text-metadata`). `textItems` / `fontName` are still accepted. |

For any other source, build the pages yourself and call `write_textlayer` / `writeTextLayer` / `write_text_layer`. PRs adding adapters (Azure DI, Textract, hOCR, …) are welcome: add a variant to `Adapter` with its parse function.

## Fonts and non-Latin text

The overlay uses one small font built by the writer (a few kilobytes at most). Its glyphs are plain rectangles, one per character width in use: the text is never painted, so a glyph only has to tell viewers how big a character is. Character codes are UTF-16 code units, so the same font carries Latin, CJK, Cyrillic, Greek, and every other script in the Basic Multilingual Plane. Tesseract and OCRmyPDF write their text layers with a font of the same kind.

`--font` / the `font` option picks whose character widths size the invisible text: `helv` (Helvetica, the default), `tiro` (Times), or `cour` (Courier). It changes spacing, not appearance. OCR reports whole lines, and a word inside a line lands under its image only when the characters before it advance about as far as the printed ones did. The CJK names from 0.2 (`china-s`, `china-t`, `japan`, `korea`) are still accepted and behave like `helv`.

Control characters, zero-width characters, and characters outside the Basic Multilingual Plane (emoji, for example) cannot be drawn. They are replaced with `?` so the rest of the item stays searchable. Set `sanitize_unsupported` (`sanitizeUnsupported` in Node.js) to false to drop those items instead.

## How the overlay is drawn

For each box, the font is scaled so the text width matches the box along the text direction (the box width when the text is horizontal, the box height when it is vertical). The size is capped at 1.2 times the box's other side. The insertion point is the baseline at the left of the run. Horizontal text sits on `(x, y + height)`. Text is written with PDF render mode 3, which is present in the content stream and never painted, so search, copy/paste, and assistive tech work and the visible page is unchanged.

Every box on a page goes into one new content stream appended after the page's own. When the existing content leaves the graphics state changed (an unclosed `q`, or a matrix set outside any `q`/`Q` pair), the writer brackets it so the overlay still starts from the page's default matrix. The overlay font is written once per document.

## Benchmarks

Three versions of the writer on the same synthetic documents: **0.1** called PyMuPDF's `page.insert_text` once per box, **0.2** batched each page into one PyMuPDF commit, and **0.3** is the Rust writer. Each value is the median of 3 runs, each in a fresh process pinned to one CPU core (13th Gen Intel Core i7-1360P). Only the `write_textlayer` call is timed.

| Document profile | Pages | OCR boxes | 0.1 | 0.2 | 0.3 (Rust) | 0.3 vs 0.2 | 0.3 vs 0.1 | Peak RSS 0.1 / 0.2 / 0.3 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| Scanned | 10 | 450 | 1.02 s | 108 ms | 11 ms | 9.6x | 91x | 55 / 54 / 21 MB |
| Scanned | 100 | 4,500 | 10.76 s | 853 ms | 77 ms | 11.1x | 140x | 59 / 58 / 43 MB |
| Scanned | 500 | 22,500 | 55.20 s | 4.32 s | 426 ms | 10.1x | 129x | 80 / 72 / 137 MB |
| Scanned, vector-heavy page content | 10 | 450 | 1.16 s | 144 ms | 15 ms | 9.5x | 77x | 55 / 54 / 22 MB |
| Scanned, vector-heavy page content | 50 | 2,250 | 5.67 s | 441 ms | 75 ms | 5.9x | 76x | 57 / 56 / 31 MB |
| Scanned, vector-heavy page content | 200 | 9,000 | 22.84 s | 1.69 s | 269 ms | 6.3x | 85x | 64 / 61 / 68 MB |
| Native text, scanned exhibit every 10th page | 100 | 900 | 2.33 s | 217 ms | 50 ms | 4.4x | 47x | 56 / 56 / 28 MB |
| Native text, scanned exhibit every 10th page | 1000 | 9,000 | 24.97 s | 2.20 s | 441 ms | 5.0x | 57x | 79 / 75 / 111 MB |
| Mixed native and scanned on every page | 20 | 900 | 2.55 s | 263 ms | 30 ms | 8.7x | 84x | 56 / 57 / 25 MB |
| Mixed native and scanned on every page | 200 | 9,000 | 24.86 s | 2.70 s | 256 ms | 10.5x | 97x | 66 / 114 / 73 MB |
| Scanned, rotated pages and rotated boxes | 50 | 1,650 | 4.04 s | 364 ms | 44 ms | 8.3x | 92x | 56 / 55 / 30 MB |
| **Total** | | | **155.4 s** | **13.3 s** | **1.69 s** | **7.9x** | **92x** | |

- **Output matches 0.2.** Read back with MuPDF, 938,450 words on 2,240 pages have the same text in the same order, and no word moved by more than 0.001 pt. Read back with PDFium, every page extracts to the same text. Version 0.1 differs from both by design: it had no native-word dedup and no rotation, and it wrote `€` as `·`.
- **Native-text detection agrees with PyMuPDF.** On 118 real PDFs, 111,290 boxes placed on the words PyMuPDF extracts were all skipped, and 22,428 boxes placed where it finds no words were all drawn.
- **Memory.** Lower on small and medium documents, because PyMuPDF is no longer loaded. Higher on the two largest: the Rust writer holds the whole file (137 MB peak for a 48 MB scan, where 0.2 used 72 MB).
- **File size.** The text layer adds 2.4 MB across the dataset. Version 0.2 added 2.6 MB and 0.1 added 15.1 MB.
- **Runs varied little.** The three runs of a document differed by at most 0.08 s for 0.3, 0.4 s for 0.2, and 2.8 s for 0.1.

To reproduce, check out the older versions next to the working tree and point the runner at them:

```bash
git worktree add ../v0.1 92662c2
git worktree add ../v0.2 695696d
(cd packages/python && uv sync)
cd benchmarks
uv run --project ../packages/python python generate.py data
uv run --project ../packages/python python run.py --variant 0.1=../../v0.1/src --variant 0.2=../../v0.2/src --variant 0.3=
uv run --project ../packages/python python report.py 0.1 0.2 0.3
uv run --project ../packages/python python verify.py 0.2 0.3
uvx --from pypdfium2 python verify_pdfium.py 0.2 0.3
uv run --project ../packages/python python dedup_agreement.py ~/some/folder/of/pdfs
```

## Limitations

- **Rotation comes from the JSON.** `lit parse --format json --extract-text-metadata` includes `rotation` on each item. Without that flag the field is omitted and the run is drawn horizontally inside the box. Angles snap to `0`, `90`, `180`, or `270`.
- **Invisible text uses PDF render mode 3.** All modern viewers and search indexers handle this correctly; very old tooling may not extract it.
- **Overlap dedup is geometric.** Two words that sit side by side and barely intersect are both kept. A box that covers half or more of a native word is treated as that word and skipped.
- **Right-to-left text is written in the order given.** Hebrew and Arabic are kept, but viewers expect such runs in visual order and may return them reversed.
- **The whole PDF is held in memory.** Peak memory is about twice the input file size while it loads.
- **Encrypted PDFs are rejected** (`PdfError` in Python, `ERR_INVALID_PDF` in Node.js), including files that open without a password. Version 0.2 could write those.

## Development

The layout follows [liteparse](https://github.com/run-llama/liteparse): a Rust core, thin binding crates, and one package per language.

| Path | What |
|------|------|
| `crates/pdf-textlayer` | The writer, the adapters, and the `pdf-textlayer` CLI (`cli` feature). A plain Rust library. |
| `crates/pdf-textlayer-python` | PyO3 bindings, built as `pdf_textlayer._native` (abi3, one wheel per platform for Python 3.11+). |
| `crates/pdf-textlayer-napi` | napi-rs bindings, built as `pdf-textlayer.<platform>.node`. |
| `packages/python` | The PyPI package: typed Python API and CLI entry point. |
| `packages/node` | The npm package: typed TypeScript API (ESM + CJS) and CLI entry point. |
| `docs` | The documentation site, built with [Blume](https://useblume.dev). |
| `examples` | A sample scan, its LiteParse JSON, and a script per language. |
| `benchmarks` | The synthetic dataset and the timing, comparison, and agreement scripts. |

```bash
make test                    # cargo test, plus the Python and Node.js suites
make lint                    # cargo fmt --check, clippy, ruff, tsc

cargo test                   # Rust tests
cd packages/python && uv sync && uv run pytest   # builds the extension; PyMuPDF checks the output
cd packages/node && npm install && npm run build && npm test
```

`uv sync` rebuilds the extension when a Rust source changes.

### Docs site

The docs are Markdown in `docs/content`, built with [Blume](https://useblume.dev) (Node.js 22.19+).

```bash
cd docs
npm install
npm run dev        # http://localhost:4321
npm run build      # static site in docs/dist
```

To deploy on Vercel, import the repository and set the project's **Root Directory** to `docs`. `docs/vercel.json` sets the install and build commands, the `dist` output directory, and the content-type headers Blume needs for its `.md` and `llms.txt` files; Vercel supplies the site URL.

### Releases

Releases are manual GitHub workflows (`release-crates.yml`, `release-python.yml`, `release-node.yml`), each checking that the version you enter matches the package. PyPI and npm use trusted publishing, so no PyPI or npm token is stored in GitHub. npm releases are staged: they go live only after a maintainer approves them with 2FA on npmjs.com.

## Contributing

Bug reports, docs improvements, and new adapters are welcome — open an issue or PR on [GitHub](https://github.com/patelvivekdev/pdf-textlayer/issues).

## License

Released under the **MIT License**. See [`LICENSE`](LICENSE) for the full text.
