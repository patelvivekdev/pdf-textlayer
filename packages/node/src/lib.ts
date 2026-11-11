// Add an invisible, searchable text layer to a PDF from OCR JSON.
//
// Every function hands its work to the Rust writer. Writes run off the main
// thread. Pages without OCR boxes are left as in the input PDF, and boxes that
// land on a word the page already contains are skipped.
//
// Errors carry a `code`: `ENOENT` (missing file), `EIO` (other file errors),
// `ERR_INVALID_PDF` (not a readable PDF, or encrypted), or
// `ERR_INVALID_ARG_VALUE` (unknown adapter or font, invalid JSON).

import { readFile } from "node:fs/promises";

import { native } from "./native.js";

/**
 * One piece of OCR text to draw, in PDF points. The origin is the top-left of
 * the displayed page, x right, y down; `(x, y)` is the box's top-left corner.
 */
export interface TextBox {
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** OCR confidence from 0 to 1. Default 1. */
  confidence?: number;
  /** Degrees counter-clockwise; snaps to 0, 90, 180, or 270. Default 0. */
  rotation?: number;
}

/** The OCR boxes of one page. */
export interface ParsedPage {
  /** 1-indexed page of the input PDF that receives the boxes. */
  pageNumber: number;
  boxes: TextBox[];
}

/** Whose character widths size the invisible text: Helvetica, Times, or Courier. */
export type FontName = "helv" | "tiro" | "cour";

/** A JSON format the boxes can be read from. */
export type Adapter = "liteparse";

export interface Options {
  /** Drop boxes whose confidence is below this (0-1). Default 0. */
  minConfidence?: number;
  /** Default `"helv"`. Changes spacing, not appearance. */
  font?: FontName;
  /**
   * Replace characters that cannot be drawn (control, zero-width, outside the
   * Basic Multilingual Plane) with `?`. When false, boxes containing one are
   * dropped. Default true.
   */
  sanitizeUnsupported?: boolean;
}

export interface MakeSearchableOptions extends Options {
  /** The JSON format of `source`. Default `"liteparse"`. */
  adapter?: Adapter;
}

/** What a pass wrote. */
export interface Stats {
  /** Pages in the document. */
  pages: number;
  /** 1-indexed pages that received a text layer. */
  ocrPages: number[];
  /** Boxes drawn across all pages. */
  itemsDrawn: number;
}

/** OCR JSON: a path to a JSON file, or the already-parsed document. */
export type Source = string | URL | Record<string, unknown>;

/** Draw a text layer onto the PDF at `input` and save it to `output`. */
export async function writeTextLayer(
  input: string,
  pages: ParsedPage[],
  output: string,
  options?: Options,
): Promise<Stats> {
  return native.writeTextLayer(input, pages, output, options);
}

/** Like {@link writeTextLayer}, for a PDF held in memory. */
export async function addTextLayer(
  pdf: Uint8Array,
  pages: ParsedPage[],
  options?: Options,
): Promise<{ pdf: Buffer; stats: Stats }> {
  const buffer = Buffer.isBuffer(pdf)
    ? pdf
    : Buffer.from(pdf.buffer, pdf.byteOffset, pdf.byteLength);
  return native.addTextLayer(buffer, pages, options);
}

/**
 * Read the OCR boxes of each page from an OCR tool's JSON. Pages without OCR
 * boxes are omitted; malformed pages and items are skipped.
 */
export async function loadPages(
  source: Source,
  options: { adapter?: Adapter } = {},
): Promise<ParsedPage[]> {
  return native.loadPages(await sourceJson(source), options.adapter ?? "liteparse");
}

/**
 * Write a searchable PDF from a PDF and an OCR tool's JSON. `source` may also
 * be an array of {@link ParsedPage}, in which case `adapter` is ignored.
 */
export async function makeSearchablePdf(
  input: string,
  source: Source | ParsedPage[],
  output: string,
  options: MakeSearchableOptions = {},
): Promise<Stats> {
  const { adapter = "liteparse", ...drawOptions } = options;
  if (Array.isArray(source)) {
    return writeTextLayer(input, source, output, drawOptions);
  }
  return native.makeSearchablePdf(
    input,
    await sourceJson(source),
    output,
    adapter,
    drawOptions,
  );
}

async function sourceJson(source: Source): Promise<string> {
  if (typeof source === "string" || source instanceof URL) {
    return readFile(source, "utf8");
  }
  return JSON.stringify(source);
}
