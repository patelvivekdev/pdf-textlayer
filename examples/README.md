# Example

`scan.pdf` is a one-page scan: the words are painted into an image, so a viewer finds no text. `parsed.json` is LiteParse `--format json` for that page. Each item has `font_name` set to `OCR`.

Each command below writes `searchable.pdf`. The page looks the same. Search for `Lovelace`.

From the repository root:

```bash
# Rust CLI
cargo run -p pdf-textlayer -- examples/scan.pdf examples/parsed.json examples/searchable.pdf

# Python (after `uv sync` in packages/python)
uv run --project packages/python python examples/make_searchable.py
uv run --project packages/python pdf-textlayer examples/scan.pdf examples/parsed.json examples/searchable.pdf

# Node.js (after `npm install && npm run build` in packages/node)
node packages/node/dist/cli.js examples/scan.pdf examples/parsed.json examples/searchable.pdf
npm install --no-save --no-package-lock ./packages/node   # lets `import "pdf-textlayer"` resolve
node examples/make_searchable.mjs
```
