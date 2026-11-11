// Loads the native module: the pdf-textlayer-<platform> optional dependency in
// an installed package, or the .node file `napi build` writes next to
// package.json during development.

import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));

export interface NativeTextBox {
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  confidence?: number;
  rotation?: number;
}

export interface NativeParsedPage {
  pageNumber: number;
  boxes: NativeTextBox[];
}

export interface NativeOptions {
  minConfidence?: number;
  font?: string;
  sanitizeUnsupported?: boolean;
}

export interface NativeStats {
  pages: number;
  ocrPages: number[];
  itemsDrawn: number;
}

export interface NativeBindings {
  writeTextLayer(
    input: string,
    pages: NativeParsedPage[],
    output: string,
    options?: NativeOptions,
  ): Promise<NativeStats>;
  addTextLayer(
    pdf: Buffer,
    pages: NativeParsedPage[],
    options?: NativeOptions,
  ): Promise<{ pdf: Buffer; stats: NativeStats }>;
  makeSearchablePdf(
    input: string,
    sourceJson: string,
    output: string,
    adapter: string,
    options?: NativeOptions,
  ): Promise<NativeStats>;
  loadPages(sourceJson: string, adapter: string): NativeParsedPage[];
  runCli(args: string[]): number;
}

function platformKeys(): string[] {
  const { platform, arch } = process;
  if (platform === "linux") {
    return [`linux-${arch}-gnu`, `linux-${arch}-musl`];
  }
  if (platform === "win32") {
    return [`win32-${arch}-msvc`];
  }
  return [`${platform}-${arch}`];
}

function loadNative(): NativeBindings {
  const errors: string[] = [];

  for (const key of platformKeys()) {
    for (const candidate of [
      `pdf-textlayer-${key}`,
      join(__dirname, "..", `pdf-textlayer.${key}.node`),
    ]) {
      try {
        return require(candidate) as NativeBindings;
      } catch (error) {
        errors.push(`${candidate}: ${(error as Error).message}`);
      }
    }
  }

  throw new Error(
    `pdf-textlayer has no native module for ${process.platform}-${process.arch}.\n` +
      errors.join("\n"),
  );
}

export const native = loadNative();
