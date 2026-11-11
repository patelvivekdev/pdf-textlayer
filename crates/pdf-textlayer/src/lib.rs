//! Add an invisible, searchable text layer to a PDF.
//!
//! Give the writer a PDF and the OCR boxes for its pages. Each box is drawn
//! at its location in text render mode 3, so the page looks the same and
//! becomes selectable and searchable.
//!
//! * Pages without drawable boxes are left as they are.
//! * A box that lands on a word the page already contains is skipped, so a
//!   page that mixes native text and a scan is not indexed twice.
//! * Boxes are given in the upright view of the page. Pages with a `/Rotate`
//!   entry and boxes with their own rotation are placed along the text
//!   direction.
//!
//! ```no_run
//! use pdf_textlayer::{Options, ParsedPage, TextBox, write_text_layer};
//!
//! let pages = vec![ParsedPage {
//!     page_number: 1,
//!     boxes: vec![TextBox::new("Hello world", 72.0, 100.0, 120.0, 12.0)],
//! }];
//!
//! let stats = write_text_layer("scan.pdf", &pages, "searchable.pdf", &Options::default())?;
//! assert_eq!(stats.items_drawn, 1);
//! # Ok::<(), pdf_textlayer::Error>(())
//! ```
//!
//! OCR JSON from a supported tool goes through an [`Adapter`]:
//!
//! ```no_run
//! use pdf_textlayer::{Adapter, Options, make_searchable_pdf};
//!
//! let stats = make_searchable_pdf("scan.pdf", "parsed.json", "searchable.pdf", Adapter::Liteparse, &Options::default())?;
//! # Ok::<(), pdf_textlayer::Error>(())
//! ```

pub mod adapters;
#[cfg(feature = "cli")]
pub mod cli;
mod geometry;
mod metrics;
mod metrics_data;
mod native_text;
mod overlay;
mod overlay_font;

use std::fs::File;
use std::io::BufWriter;
use std::path::Path;

use lopdf::Document;

pub use adapters::Adapter;
pub use metrics::FontMetrics;

/// One piece of OCR text to draw, in PDF points.
///
/// The origin is the top-left of the page as displayed, x to the right and
/// y down. `(x, y)` is the top-left corner of the axis-aligned box.
#[derive(Debug, Clone, PartialEq)]
pub struct TextBox {
    pub text: String,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    /// OCR confidence from 0 to 1.
    pub confidence: f64,
    /// Text direction in degrees counter-clockwise. Values snap to 0, 90,
    /// 180, or 270.
    pub rotation: f64,
}

impl TextBox {
    /// A horizontal box with full confidence.
    pub fn new(text: impl Into<String>, x: f64, y: f64, width: f64, height: f64) -> TextBox {
        TextBox {
            text: text.into(),
            x,
            y,
            width,
            height,
            confidence: 1.0,
            rotation: 0.0,
        }
    }
}

/// The OCR boxes of one page.
#[derive(Debug, Clone, PartialEq)]
pub struct ParsedPage {
    /// 1-indexed page of the input PDF that receives the boxes.
    pub page_number: u32,
    pub boxes: Vec<TextBox>,
}

/// Drawing options.
#[derive(Debug, Clone, PartialEq)]
pub struct Options {
    /// Drop boxes whose confidence is below this. `0.0` keeps everything.
    pub min_confidence: f64,
    /// The width table that sizes each run.
    pub font: FontMetrics,
    /// Replace characters that cannot be drawn with `?`. When false, a box
    /// that contains one is dropped instead.
    pub sanitize_unsupported: bool,
}

impl Default for Options {
    fn default() -> Options {
        Options {
            min_confidence: 0.0,
            font: FontMetrics::default(),
            sanitize_unsupported: true,
        }
    }
}

/// What a pass wrote.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Stats {
    /// Pages in the document.
    pub pages: usize,
    /// 1-indexed pages that received a text layer, in page order.
    pub ocr_pages: Vec<u32>,
    /// Boxes drawn across all pages.
    pub items_drawn: usize,
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{path}: {source}")]
    Io {
        path: String,
        #[source]
        source: std::io::Error,
    },
    #[error("not a readable PDF: {0}")]
    Pdf(#[from] lopdf::Error),
    #[error("encrypted PDFs are not supported")]
    Encrypted,
    #[error("not valid JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("unknown adapter {0:?}: expected 'liteparse'")]
    UnknownAdapter(String),
    #[error("unknown font {0:?}: expected 'helv', 'tiro', or 'cour'")]
    UnknownFont(String),
}

/// Draw a text layer onto the PDF at `input` and save it to `output`.
///
/// `output` is overwritten if it exists. Nothing is written when the input
/// cannot be read.
pub fn write_text_layer(
    input: impl AsRef<Path>,
    pages: &[ParsedPage],
    output: impl AsRef<Path>,
    options: &Options,
) -> Result<Stats, Error> {
    let input = input.as_ref();
    let output = output.as_ref();
    let pdf = std::fs::read(input).map_err(|source| io_error(input, source))?;
    let (mut document, stats) = overlay_document(&pdf, pages, options)?;

    // The input bytes are no longer needed; the document owns its own copy.
    drop(pdf);

    let mut file = BufWriter::new(File::create(output).map_err(|source| io_error(output, source))?);
    document.save_to(&mut file).map_err(|source| io_error(output, source))?;
    file.into_inner()
        .map_err(|error| io_error(output, error.into_error()))?;

    Ok(stats)
}

/// Read the OCR JSON at `source` with `adapter`, draw its boxes onto the PDF
/// at `input`, and save the result to `output`.
pub fn make_searchable_pdf(
    input: impl AsRef<Path>,
    source: impl AsRef<Path>,
    output: impl AsRef<Path>,
    adapter: Adapter,
    options: &Options,
) -> Result<Stats, Error> {
    let pages = adapter.parse_file(source)?;
    write_text_layer(input, &pages, output, options)
}

/// Draw a text layer onto a PDF held in memory and return the new PDF.
pub fn add_text_layer(pdf: &[u8], pages: &[ParsedPage], options: &Options) -> Result<(Vec<u8>, Stats), Error> {
    let (mut document, stats) = overlay_document(pdf, pages, options)?;
    let mut output = Vec::with_capacity(pdf.len() + pdf.len() / 8);
    document
        .save_to(&mut output)
        .map_err(|source| io_error(Path::new("<memory>"), source))?;

    Ok((output, stats))
}

fn overlay_document(pdf: &[u8], pages: &[ParsedPage], options: &Options) -> Result<(Document, Stats), Error> {
    let mut document = Document::load_mem(pdf)?;

    // An encrypted file is rejected even when it opens without a password:
    // saving it decrypted would silently drop the owner's restrictions.
    if document.trailer.has(b"Encrypt") || document.was_encrypted() {
        return Err(Error::Encrypted);
    }

    // A file whose page tree cannot be found must not be saved back empty.
    document.catalog()?;

    let stats = overlay::Overlay::new(&mut document, options).run(&mut document, pages)?;

    Ok((document, stats))
}

pub(crate) fn io_error(path: &Path, source: std::io::Error) -> Error {
    Error::Io {
        path: path.display().to_string(),
        source,
    }
}
