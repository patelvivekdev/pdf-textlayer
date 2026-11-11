//! Python bindings for `pdf-textlayer`.
//!
//! The module is private (`pdf_textlayer._native`). The typed API in the
//! `pdf_textlayer` package is the supported surface.

use std::path::PathBuf;

use pyo3::create_exception;
use pyo3::exceptions::{PyFileNotFoundError, PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use pdf_textlayer::{Adapter, Error, FontMetrics, Options, ParsedPage, Stats, TextBox};

create_exception!(
    _native,
    PdfError,
    PyRuntimeError,
    "The input is not a PDF this package can read."
);

/// Reads the attributes of a `pdf_textlayer.TextBox`.
#[derive(FromPyObject)]
struct TextBoxAttributes {
    text: String,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    confidence: f64,
    rotation: f64,
}

impl From<TextBoxAttributes> for TextBox {
    fn from(attributes: TextBoxAttributes) -> TextBox {
        TextBox {
            text: attributes.text,
            x: attributes.x,
            y: attributes.y,
            width: attributes.width,
            height: attributes.height,
            confidence: attributes.confidence,
            rotation: attributes.rotation,
        }
    }
}

/// `(text, x, y, width, height, confidence, rotation)`.
type TextBoxTuple = (String, f64, f64, f64, f64, f64, f64);
/// `(page_count, pages_with_text_layer, items_drawn)`.
type StatsTuple = (usize, Vec<u32>, usize);

fn to_python_error(error: Error) -> PyErr {
    match &error {
        Error::Io { source, .. } if source.kind() == std::io::ErrorKind::NotFound => {
            PyFileNotFoundError::new_err(error.to_string())
        }
        Error::Io { .. } => PyOSError::new_err(error.to_string()),
        Error::Pdf(_) | Error::Encrypted => PdfError::new_err(error.to_string()),
        Error::Json(_) | Error::UnknownAdapter(_) | Error::UnknownFont(_) => PyValueError::new_err(error.to_string()),
    }
}

fn options(min_confidence: f64, font: &str, sanitize_unsupported: bool) -> PyResult<Options> {
    Ok(Options {
        min_confidence,
        font: font.parse::<FontMetrics>().map_err(to_python_error)?,
        sanitize_unsupported,
    })
}

fn parsed_pages(pages: Vec<(i64, Vec<TextBoxAttributes>)>) -> Vec<ParsedPage> {
    // A page number no PDF can have matches no page.
    pages
        .into_iter()
        .filter_map(|(page_number, boxes)| {
            Some(ParsedPage {
                page_number: u32::try_from(page_number).ok()?,
                boxes: boxes.into_iter().map(TextBox::from).collect(),
            })
        })
        .collect()
}

fn stats_tuple(stats: Stats) -> StatsTuple {
    (stats.pages, stats.ocr_pages, stats.items_drawn)
}

/// Draw the text layer and save the result.
///
/// `pages` is a list of `(page_number, boxes)`.
#[pyfunction]
#[pyo3(signature = (input_pdf, pages, output_pdf, *, min_confidence, font, sanitize_unsupported))]
fn write_textlayer(
    py: Python<'_>,
    input_pdf: PathBuf,
    pages: Vec<(i64, Vec<TextBoxAttributes>)>,
    output_pdf: PathBuf,
    min_confidence: f64,
    font: &str,
    sanitize_unsupported: bool,
) -> PyResult<StatsTuple> {
    let options = options(min_confidence, font, sanitize_unsupported)?;
    let pages = parsed_pages(pages);
    let stats = py
        .detach(|| pdf_textlayer::write_text_layer(&input_pdf, &pages, &output_pdf, &options))
        .map_err(to_python_error)?;

    Ok(stats_tuple(stats))
}

/// Draw the text layer onto a PDF in memory and return the new PDF.
#[pyfunction]
#[pyo3(signature = (pdf, pages, *, min_confidence, font, sanitize_unsupported))]
fn add_textlayer<'py>(
    py: Python<'py>,
    pdf: Vec<u8>,
    pages: Vec<(i64, Vec<TextBoxAttributes>)>,
    min_confidence: f64,
    font: &str,
    sanitize_unsupported: bool,
) -> PyResult<(Bound<'py, PyBytes>, StatsTuple)> {
    let options = options(min_confidence, font, sanitize_unsupported)?;
    let pages = parsed_pages(pages);
    let (output, stats) = py
        .detach(|| pdf_textlayer::add_text_layer(&pdf, &pages, &options))
        .map_err(to_python_error)?;

    Ok((PyBytes::new(py, &output), stats_tuple(stats)))
}

/// Read OCR JSON with `adapter`, draw its boxes, and save the result.
#[pyfunction]
#[pyo3(signature = (input_pdf, source_json, output_pdf, *, adapter, min_confidence, font, sanitize_unsupported))]
#[allow(clippy::too_many_arguments)]
fn make_searchable_pdf(
    py: Python<'_>,
    input_pdf: PathBuf,
    source_json: &str,
    output_pdf: PathBuf,
    adapter: &str,
    min_confidence: f64,
    font: &str,
    sanitize_unsupported: bool,
) -> PyResult<StatsTuple> {
    let options = options(min_confidence, font, sanitize_unsupported)?;
    let adapter = adapter.parse::<Adapter>().map_err(to_python_error)?;
    let stats = py
        .detach(|| {
            let pages = adapter.parse_str(source_json)?;
            pdf_textlayer::write_text_layer(&input_pdf, &pages, &output_pdf, &options)
        })
        .map_err(to_python_error)?;

    Ok(stats_tuple(stats))
}

/// The OCR boxes `adapter` reads from `source_json`, per page.
#[pyfunction]
#[pyo3(signature = (source_json, *, adapter))]
fn load_pages(py: Python<'_>, source_json: &str, adapter: &str) -> PyResult<Vec<(u32, Vec<TextBoxTuple>)>> {
    let adapter = adapter.parse::<Adapter>().map_err(to_python_error)?;
    let pages = py.detach(|| adapter.parse_str(source_json)).map_err(to_python_error)?;

    Ok(pages
        .into_iter()
        .map(|page| {
            let boxes = page
                .boxes
                .into_iter()
                .map(|b| (b.text, b.x, b.y, b.width, b.height, b.confidence, b.rotation))
                .collect();
            (page.page_number, boxes)
        })
        .collect())
}

/// Run the `pdf-textlayer` command line and return its exit code.
#[pyfunction]
fn run_cli(py: Python<'_>, args: Vec<String>) -> i32 {
    py.detach(|| pdf_textlayer::cli::main(args))
}

#[pymodule]
mod _native {
    #[pymodule_export]
    use super::PdfError;
    #[pymodule_export]
    use super::add_textlayer;
    #[pymodule_export]
    use super::load_pages;
    #[pymodule_export]
    use super::make_searchable_pdf;
    #[pymodule_export]
    use super::run_cli;
    #[pymodule_export]
    use super::write_textlayer;
}
