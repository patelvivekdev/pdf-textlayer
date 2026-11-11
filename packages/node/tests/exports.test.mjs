// Entry-point tests for the dual ESM/CJS build. The package is loaded by name,
// so Node resolves it through the real `exports` map in package.json.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const API = ["addTextLayer", "loadPages", "makeSearchablePdf", "writeTextLayer"];

function collectPaths(node, found = []) {
  if (typeof node === "string") found.push(node);
  else if (node && typeof node === "object") {
    for (const value of Object.values(node)) collectPaths(value, found);
  }
  return found;
}

test("every path package.json references exists in the build", () => {
  const pkg = JSON.parse(readFileSync(join(packageDir, "package.json"), "utf8"));
  const referenced = [
    pkg.main,
    pkg.module,
    pkg.types,
    ...Object.values(pkg.bin),
    ...collectPaths(pkg.exports),
  ];
  for (const path of referenced) {
    assert.ok(existsSync(join(packageDir, path)), `${path} was not built`);
  }
});

test("import and require expose the same API", async () => {
  const esm = await import("pdf-textlayer");
  const cjs = require("pdf-textlayer");
  for (const name of API) {
    assert.equal(typeof esm[name], "function", `import is missing ${name}`);
    assert.equal(typeof cjs[name], "function", `require is missing ${name}`);
  }
});
