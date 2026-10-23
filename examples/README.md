# Example

`scan.pdf` is a one-page scan: the words are painted into an image, so a viewer finds no text. `parsed.json` is LiteParse `--format json` for that page. Each item has `font_name` set to `OCR`.

`make_searchable.py` writes `searchable.pdf`. The page looks the same. Search for `Lovelace`.

From the repository root:

```bash
uv run python examples/make_searchable.py
```

The CLI does the same thing:

```bash
uv run pdf-textlayer examples/scan.pdf examples/parsed.json examples/searchable.pdf
```
