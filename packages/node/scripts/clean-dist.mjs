// Clears dist/ before tsup runs. tsup's own `clean` wipes the outDir per
// config, which races when the lib and cli configs build concurrently.
import { rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
await rm(join(packageDir, "dist"), { recursive: true, force: true });
