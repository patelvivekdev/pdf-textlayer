# pdf-textlayer

Add an invisible, searchable text layer to a PDF from OCR boxes. Each box is drawn at its location in text render mode 3, so the page looks the same and becomes selectable and searchable. Pages without boxes are left as they are, and a box that lands on a word the page already contains is skipped.

This crate is the core of the [pdf-textlayer](https://github.com/patelvivekdev/pdf-textlayer) Python and Node.js packages, and ships the `pdf-textlayer` command.

```bash
cargo install pdf-textlayer
pdf-textlayer input.pdf parsed.json output.pdf --from liteparse --min-confidence 0.5 --font helv
```

```rust
use pdf_textlayer::{Adapter, Options, ParsedPage, TextBox, make_searchable_pdf, write_text_layer};

// OCR JSON from a supported tool
let stats = make_searchable_pdf("scan.pdf", "parsed.json", "searchable.pdf", Adapter::Liteparse, &Options::default())?;

// Boxes from anywhere else
let pages = vec![ParsedPage {
    page_number: 1,
    boxes: vec![TextBox::new("Hello world", 72.0, 100.0, 120.0, 12.0)],
}];
let stats = write_text_layer("scan.pdf", &pages, "searchable.pdf", &Options::default())?;
```

`add_text_layer` does the same for a PDF held in memory. The default `cli` feature adds the binary and `pdf_textlayer::cli`; turn it off with `default-features = false` to drop `clap`.

License: MIT.
