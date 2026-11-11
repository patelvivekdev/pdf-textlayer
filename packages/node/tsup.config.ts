import { defineConfig } from "tsup";

// Dual ESM/CJS build. `shims: true` matters: native.ts finds the .node binary
// through `import.meta.url`, which tsup rewrites for the .cjs output.
// dist/ is cleared once by scripts/clean-dist.mjs rather than tsup's `clean`,
// which would race the two configs.
const shared = {
  platform: "node",
  target: "node20",
  outDir: "dist",
  sourcemap: true,
  shims: true,
} as const;

export default defineConfig([
  {
    ...shared,
    entry: { lib: "src/lib.ts" },
    format: ["esm", "cjs"],
    dts: true,
  },
  {
    ...shared,
    // The bin is launched by Node directly, never require()'d.
    entry: { cli: "src/cli.ts" },
    format: ["esm"],
    dts: false,
  },
]);
