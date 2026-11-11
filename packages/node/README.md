# pdf-textlayer (Node.js)

Add an invisible, searchable text layer to a PDF from OCR JSON. The writer is Rust, loaded as a prebuilt native module; this package is a typed wrapper around it with no runtime dependencies. Pages without OCR text are left as in the input: native text, embedded fonts, and annotations are preserved.

Source, benchmarks, and the Python and Rust packages: **[GitHub](https://github.com/patelvivekdev/pdf-textlayer)**.

## Installation

Node.js **20+**. Prebuilt for macOS (arm64, x64), Linux (x64 and arm64 glibc, x64 musl), and Windows (x64, arm64).

```bash
npm install pdf-textlayer
```

This installs the library (ESM and CommonJS) and the `pdf-textlayer` command.

## CLI

```bash
# 1. Produce OCR JSON. --extract-text-metadata adds per-item rotation.
lit parse input.pdf --format json --extract-text-metadata -o parsed.json

# 2. Build the searchable PDF
npx pdf-textlayer input.pdf parsed.json output.pdf --min-confidence 0.5 --font tiro
```

## Library

```ts
import { makeSearchablePdf, writeTextLayer } from "pdf-textlayer";

const stats = await makeSearchablePdf("input.pdf", "parsed.json", "output.pdf", {
  adapter: "liteparse", // default; `source` may also be the parsed JSON object
  minConfidence: 0.5,
  font: "helv",
});
console.log(stats);
// { pages: 19, ocrPages: [1, 2, ..., 19], itemsDrawn: 7643 }

// OCR results from any other source:
await writeTextLayer(
  "input.pdf",
  [
    {
      pageNumber: 1,
      boxes: [
        { text: "Hello", x: 72, y: 108, width: 46, height: 12, confidence: 0.98 },
        { text: "world", x: 124, y: 108, width: 48, height: 12, confidence: 0.97 },
      ],
    },
  ],
  "output.pdf",
);
```

| Function | Does |
|---|---|
| `makeSearchablePdf(input, source, output, options?)` | Reads OCR JSON (path or object) with an adapter and writes the PDF. `source` may also be an array of pages. |
| `writeTextLayer(input, pages, output, options?)` | Writes the PDF from pages of text boxes. |
| `addTextLayer(pdf, pages, options?)` | Same, in memory. Resolves to `{ pdf: Buffer, stats }`. |
| `loadPages(source, { adapter }?)` | Resolves to the pages an adapter reads from OCR JSON, to inspect or edit before writing. |

Options: `minConfidence` (default `0`), `font` (`"helv"`, `"tiro"`, or `"cour"`; default `"helv"`), and `sanitizeUnsupported` (default `true`: characters that cannot be drawn become `?`; `false` drops the box).

Writes run on the libuv thread pool. A rejected promise carries a `code`: `ENOENT` for a missing file, `EIO` for other file errors, `ERR_INVALID_PDF` for a file that is not a readable PDF or is encrypted, and `ERR_INVALID_ARG_VALUE` for an unknown adapter or font, or invalid JSON.

Coordinates have their origin at the **top-left** of the displayed page, `x` right, `y` down, in PDF points. `(x, y)` is the top-left corner of the box. `rotation` is degrees counter-clockwise (`0`, `90`, `180`, `270`); other values snap to a right angle.

## Adapters

| Name | Source | Notes |
|------|--------|-------|
| `liteparse` | [liteparse](https://github.com/run-llama/liteparse) `lit parse --format json` | `pages[].text_items[]` with `font_name == "OCR"`. Reads `rotation` when present (`--extract-text-metadata`). `textItems` / `fontName` are still accepted. |

## License

MIT.
