//! Node.js bindings for `pdf-textlayer`.
//!
//! The `.node` module is private. The typed API in `packages/node/src/lib.ts`
//! is the supported surface. Writes run on the libuv thread pool and return
//! promises; errors carry a `code` (`ENOENT`, `EIO`, `ERR_INVALID_PDF`, or
//! `ERR_INVALID_ARG_VALUE`).

use std::path::PathBuf;

use napi::bindgen_prelude::*;
use napi::{Env, JsUnknown, Task};
use napi_derive::napi;

use pdf_textlayer::{Adapter, Error as CoreError, FontMetrics, Options, ParsedPage, Stats, TextBox};

#[napi(object, js_name = "TextBox")]
pub struct JsTextBox {
    pub text: String,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub confidence: Option<f64>,
    pub rotation: Option<f64>,
}

#[napi(object, js_name = "ParsedPage")]
pub struct JsParsedPage {
    pub page_number: u32,
    pub boxes: Vec<JsTextBox>,
}

#[napi(object, js_name = "Options")]
pub struct JsOptions {
    pub min_confidence: Option<f64>,
    pub font: Option<String>,
    pub sanitize_unsupported: Option<bool>,
}

#[napi(object, js_name = "Stats")]
pub struct JsStats {
    pub pages: u32,
    pub ocr_pages: Vec<u32>,
    pub items_drawn: u32,
}

#[napi(object, js_name = "TextLayerResult")]
pub struct JsTextLayerResult {
    pub pdf: Buffer,
    pub stats: JsStats,
}

impl From<JsTextBox> for TextBox {
    fn from(text_box: JsTextBox) -> TextBox {
        TextBox {
            text: text_box.text,
            x: text_box.x,
            y: text_box.y,
            width: text_box.width,
            height: text_box.height,
            confidence: text_box.confidence.unwrap_or(1.0),
            rotation: text_box.rotation.unwrap_or(0.0),
        }
    }
}

impl From<TextBox> for JsTextBox {
    fn from(text_box: TextBox) -> JsTextBox {
        JsTextBox {
            text: text_box.text,
            x: text_box.x,
            y: text_box.y,
            width: text_box.width,
            height: text_box.height,
            confidence: Some(text_box.confidence),
            rotation: Some(text_box.rotation),
        }
    }
}

impl From<Stats> for JsStats {
    fn from(stats: Stats) -> JsStats {
        JsStats {
            pages: stats.pages as u32,
            ocr_pages: stats.ocr_pages,
            items_drawn: stats.items_drawn as u32,
        }
    }
}

fn parsed_pages(pages: Vec<JsParsedPage>) -> Vec<ParsedPage> {
    pages
        .into_iter()
        .map(|page| ParsedPage {
            page_number: page.page_number,
            boxes: page.boxes.into_iter().map(TextBox::from).collect(),
        })
        .collect()
}

fn options(options: Option<JsOptions>) -> std::result::Result<Options, CoreError> {
    let defaults = Options::default();
    let Some(options) = options else {
        return Ok(defaults);
    };

    Ok(Options {
        min_confidence: options.min_confidence.unwrap_or(defaults.min_confidence),
        font: options
            .font
            .map(|font| font.parse::<FontMetrics>())
            .transpose()?
            .unwrap_or(defaults.font),
        sanitize_unsupported: options.sanitize_unsupported.unwrap_or(defaults.sanitize_unsupported),
    })
}

/// A JS `Error` with the message of `error` and a `code` naming its kind.
fn js_error(env: &Env, error: CoreError) -> Error {
    let code = match &error {
        CoreError::Io { source, .. } if source.kind() == std::io::ErrorKind::NotFound => "ENOENT",
        CoreError::Io { .. } => "EIO",
        CoreError::Pdf(_) | CoreError::Encrypted => "ERR_INVALID_PDF",
        CoreError::Json(_) | CoreError::UnknownAdapter(_) | CoreError::UnknownFont(_) => "ERR_INVALID_ARG_VALUE",
    };
    let message = error.to_string();
    let build = || -> Result<JsUnknown> {
        let mut object = env.create_error(Error::from_reason(message.clone()))?;
        object.set_named_property("code", env.create_string(code)?)?;
        Ok(object.into_unknown())
    };

    match build() {
        Ok(object) => Error::from(object),
        Err(_) => Error::from_reason(message),
    }
}

type Work<T> = Box<dyn FnOnce() -> std::result::Result<T, CoreError> + Send>;
type StatsTask = AsyncTask<Job<Stats, JsStats>>;
type TextLayerTask = AsyncTask<Job<(Vec<u8>, Stats), JsTextLayerResult>>;

/// Runs `work` on the libuv thread pool and settles its promise with `to_js`.
pub struct Job<T: Send + 'static, J> {
    work: Option<Work<T>>,
    to_js: fn(T) -> J,
}

impl<T: Send + 'static, J: ToNapiValue + TypeName> Task for Job<T, J> {
    type Output = std::result::Result<T, CoreError>;
    type JsValue = J;

    fn compute(&mut self) -> Result<Self::Output> {
        let work = self.work.take().expect("a job runs once");
        Ok(work())
    }

    fn resolve(&mut self, env: Env, output: Self::Output) -> Result<J> {
        output.map(self.to_js).map_err(|error| js_error(&env, error))
    }
}

fn job<T: Send + 'static, J: ToNapiValue + TypeName>(
    work: impl FnOnce() -> std::result::Result<T, CoreError> + Send + 'static,
    to_js: fn(T) -> J,
) -> AsyncTask<Job<T, J>> {
    AsyncTask::new(Job {
        work: Some(Box::new(work)),
        to_js,
    })
}

/// Draw the text layer onto the PDF at `input` and save it to `output`.
#[napi(ts_return_type = "Promise<Stats>")]
pub fn write_text_layer(
    env: Env,
    input: String,
    pages: Vec<JsParsedPage>,
    output: String,
    options: Option<JsOptions>,
) -> Result<StatsTask> {
    let options = self::options(options).map_err(|error| js_error(&env, error))?;
    let pages = parsed_pages(pages);

    Ok(job(
        move || pdf_textlayer::write_text_layer(PathBuf::from(input), &pages, PathBuf::from(output), &options),
        JsStats::from,
    ))
}

/// Draw the text layer onto a PDF in memory and return the new PDF.
#[napi(ts_return_type = "Promise<TextLayerResult>")]
pub fn add_text_layer(
    env: Env,
    pdf: Buffer,
    pages: Vec<JsParsedPage>,
    options: Option<JsOptions>,
) -> Result<TextLayerTask> {
    let options = self::options(options).map_err(|error| js_error(&env, error))?;
    let pages = parsed_pages(pages);
    let pdf = pdf.to_vec();

    Ok(job(
        move || pdf_textlayer::add_text_layer(&pdf, &pages, &options),
        |(pdf, stats)| JsTextLayerResult {
            pdf: pdf.into(),
            stats: stats.into(),
        },
    ))
}

/// Read OCR JSON with `adapter`, draw its boxes onto the PDF at `input`, and
/// save the result to `output`.
#[napi(ts_return_type = "Promise<Stats>")]
pub fn make_searchable_pdf(
    env: Env,
    input: String,
    source_json: String,
    output: String,
    adapter: String,
    options: Option<JsOptions>,
) -> Result<StatsTask> {
    let prepared = self::options(options).and_then(|options| Ok((options, adapter.parse::<Adapter>()?)));
    let (options, adapter) = prepared.map_err(|error| js_error(&env, error))?;

    Ok(job(
        move || {
            let pages = adapter.parse_str(&source_json)?;
            pdf_textlayer::write_text_layer(PathBuf::from(input), &pages, PathBuf::from(output), &options)
        },
        JsStats::from,
    ))
}

/// The OCR boxes `adapter` reads from `source_json`, per page.
#[napi]
pub fn load_pages(env: Env, source_json: String, adapter: String) -> Result<Vec<JsParsedPage>> {
    let pages = adapter
        .parse::<Adapter>()
        .and_then(|adapter| adapter.parse_str(&source_json))
        .map_err(|error| js_error(&env, error))?;

    Ok(pages
        .into_iter()
        .map(|page| JsParsedPage {
            page_number: page.page_number,
            boxes: page.boxes.into_iter().map(JsTextBox::from).collect(),
        })
        .collect())
}

/// Run the `pdf-textlayer` command line and return its exit code.
#[napi]
pub fn run_cli(args: Vec<String>) -> i32 {
    pdf_textlayer::cli::main(args)
}
