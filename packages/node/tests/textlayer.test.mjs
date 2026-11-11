// Runs the API and the CLI on the sample scan in examples/.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  addTextLayer,
  loadPages,
  makeSearchablePdf,
  writeTextLayer,
} from "pdf-textlayer";

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const examples = resolve(packageDir, "..", "..", "examples");
const scan = join(examples, "scan.pdf");
const parsed = join(examples, "parsed.json");
const scanStats = { pages: 1, ocrPages: [1], itemsDrawn: 15 };

function tempPath(name) {
  return join(mkdtempSync(join(tmpdir(), "pdf-textlayer-")), name);
}

test("loadPages reads the OCR boxes from a path or an object", async () => {
  const pages = await loadPages(parsed);

  assert.deepEqual(pages.map((page) => page.pageNumber), [1]);
  assert.equal(pages[0].boxes.length, 15);
  assert.deepEqual(pages[0].boxes[0], {
    text: "ACME",
    x: 72,
    y: 72.35,
    width: 63.56,
    height: 30.23,
    confidence: 0.98,
    rotation: 0,
  });
  assert.deepEqual(await loadPages(JSON.parse(readFileSync(parsed, "utf8"))), pages);
});

test("makeSearchablePdf, writeTextLayer, and addTextLayer write the same PDF", async () => {
  const fromJson = tempPath("from-json.pdf");
  const fromPages = tempPath("from-pages.pdf");
  const pages = await loadPages(parsed);

  assert.deepEqual(await makeSearchablePdf(scan, parsed, fromJson), scanStats);
  assert.deepEqual(await writeTextLayer(scan, pages, fromPages), scanStats);
  const { pdf, stats } = await addTextLayer(new Uint8Array(readFileSync(scan)), pages);

  assert.deepEqual(stats, scanStats);
  assert.ok(pdf.subarray(0, 5).equals(Buffer.from("%PDF-")));
  assert.ok(readFileSync(fromJson).equals(readFileSync(fromPages)));
  assert.ok(readFileSync(fromJson).equals(pdf));
});

test("options reach the writer", async () => {
  const output = tempPath("filtered.pdf");
  const pages = [{ pageNumber: 1, boxes: [{ text: "low", x: 72, y: 72, width: 30, height: 10, confidence: 0.2 }] }];

  const filtered = await makeSearchablePdf(scan, pages, output, { minConfidence: 0.5 });
  const kept = await makeSearchablePdf(scan, pages, output, { minConfidence: 0.1, font: "cour" });

  assert.deepEqual(filtered, { pages: 1, ocrPages: [], itemsDrawn: 0 });
  assert.deepEqual(kept, { pages: 1, ocrPages: [1], itemsDrawn: 1 });
});

test("errors carry a code", async () => {
  const notPdf = tempPath("not.pdf");
  writeFileSync(notPdf, "hello");
  const output = tempPath("out.pdf");

  await assert.rejects(writeTextLayer(join(examples, "missing.pdf"), [], output), { code: "ENOENT" });
  await assert.rejects(writeTextLayer(notPdf, [], output), { code: "ERR_INVALID_PDF" });
  await assert.rejects(writeTextLayer(scan, [], output, { font: "comic" }), {
    code: "ERR_INVALID_ARG_VALUE",
    message: /unknown font "comic"/,
  });
  await assert.rejects(makeSearchablePdf(scan, {}, output, { adapter: "textract" }), {
    code: "ERR_INVALID_ARG_VALUE",
  });
});

test("the CLI writes the PDF and reports a missing input", () => {
  const cli = join(packageDir, "dist", "cli.js");
  const output = tempPath("cli.pdf");

  const stdout = execFileSync(process.execPath, [cli, scan, parsed, output], { encoding: "utf8" });
  assert.match(stdout, /Text layer added on 1 page\(s\): 1\n/);
  assert.match(stdout, /Items drawn: 15\n/);
  assert.ok(readFileSync(output).subarray(0, 5).equals(Buffer.from("%PDF-")));

  const failed = spawnSync(process.execPath, [cli, join(examples, "missing.pdf"), parsed, output], {
    encoding: "utf8",
  });
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /^error: .*missing\.pdf/);
});
