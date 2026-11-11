// Turn the sample scan into a searchable PDF with the Node.js package.
//
// scan.pdf is a raster page with no text layer. parsed.json is the LiteParse
// `--format json` output for that page. The script writes searchable.pdf,
// which looks the same and can be searched.

import { fileURLToPath } from "node:url";

import { makeSearchablePdf } from "pdf-textlayer";

const here = (name) => fileURLToPath(new URL(name, import.meta.url));

const stats = await makeSearchablePdf(
  here("scan.pdf"),
  here("parsed.json"),
  here("searchable.pdf"),
);

console.log(`Wrote ${here("searchable.pdf")}`);
console.log(stats);
