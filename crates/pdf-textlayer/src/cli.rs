//! The `pdf-textlayer` command line.
//!
//! The binary, the Python package, and the Node.js package all run this, so
//! the command behaves the same however it was installed.

use std::ffi::OsString;
use std::path::PathBuf;

use clap::Parser;
use clap::builder::{PossibleValuesParser, TypedValueParser};

use crate::{Adapter, Error, FontMetrics, Options, make_searchable_pdf};

#[derive(Parser, Debug)]
#[command(
    name = "pdf-textlayer",
    bin_name = "pdf-textlayer",
    version,
    about = "Add an invisible, searchable text layer to a PDF using OCR JSON. \
             Pages without OCR text are left untouched."
)]
struct Cli {
    /// Source PDF (scanned, native, or mixed).
    input_pdf: PathBuf,
    /// OCR JSON for the source PDF (see --from for supported formats).
    parsed_json: PathBuf,
    /// Path to write the resulting PDF.
    output_pdf: PathBuf,
    /// JSON adapter to use.
    #[arg(
        long = "from",
        default_value = "liteparse",
        value_parser = PossibleValuesParser::new(Adapter::ALL.map(Adapter::name))
            .map(|name| Adapter::from_name(&name).expect("listed adapter")),
    )]
    adapter: Adapter,
    /// Drop OCR items below this confidence (0-1).
    #[arg(long, default_value_t = 0.0)]
    min_confidence: f64,
    /// Whose character widths size the invisible text: Helvetica, Times, or Courier.
    #[arg(
        long,
        default_value = "helv",
        value_parser = PossibleValuesParser::new(["helv", "tiro", "cour"])
            .map(|name| FontMetrics::from_name(&name).expect("listed font")),
    )]
    font: FontMetrics,
}

/// Run the command with `args` (the first is the program name) and return the
/// process exit code. Errors are printed to stderr. `--help`, `--version`,
/// and invalid arguments exit the process, as clap does.
pub fn main<I, T>(args: I) -> i32
where
    I: IntoIterator<Item = T>,
    T: Into<OsString> + Clone,
{
    match run(args) {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("error: {error}");
            1
        }
    }
}

/// Run the command with `args` (the first is the program name).
pub fn run<I, T>(args: I) -> Result<(), Error>
where
    I: IntoIterator<Item = T>,
    T: Into<OsString> + Clone,
{
    let cli = Cli::parse_from(args);
    let options = Options {
        min_confidence: cli.min_confidence,
        font: cli.font,
        ..Options::default()
    };
    let stats = make_searchable_pdf(&cli.input_pdf, &cli.parsed_json, &cli.output_pdf, cli.adapter, &options)?;

    let ocr_pages: Vec<String> = stats.ocr_pages.iter().map(u32::to_string).collect();
    println!("Wrote: {}", cli.output_pdf.display());
    println!("Pages: {}", stats.pages);
    println!(
        "Text layer added on {} page(s): {}",
        ocr_pages.len(),
        if ocr_pages.is_empty() {
            "(none)".to_string()
        } else {
            ocr_pages.join(", ")
        }
    );
    println!("Items drawn: {}", stats.items_drawn);

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_and_flags_parse() {
        let cli = Cli::try_parse_from(["pdf-textlayer", "in.pdf", "in.json", "out.pdf"]).unwrap();
        assert_eq!(
            (cli.adapter, cli.font, cli.min_confidence),
            (Adapter::Liteparse, FontMetrics::Helvetica, 0.0)
        );

        let cli = Cli::try_parse_from([
            "pdf-textlayer",
            "in.pdf",
            "in.json",
            "out.pdf",
            "--from",
            "liteparse",
            "--font",
            "tiro",
            "--min-confidence",
            "0.5",
        ])
        .unwrap();
        assert_eq!((cli.font, cli.min_confidence), (FontMetrics::Times, 0.5));
    }

    #[test]
    fn unknown_adapter_and_font_are_rejected() {
        assert!(Cli::try_parse_from(["pdf-textlayer", "a", "b", "c", "--from", "textract"]).is_err());
        assert!(Cli::try_parse_from(["pdf-textlayer", "a", "b", "c", "--font", "comic"]).is_err());
    }
}
