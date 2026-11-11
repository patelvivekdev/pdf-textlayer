//! Turn an OCR tool's JSON into [`ParsedPage`]s.
//!
//! Each source is one [`Adapter`] variant. Adding a source means adding a
//! variant, its name in [`Adapter::from_name`], and a parse function.

use std::fmt;
use std::path::Path;
use std::str::FromStr;

use serde_json::{Map, Value};

use crate::{Error, ParsedPage, TextBox};

/// A JSON format the writer can read OCR boxes from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Adapter {
    /// [LiteParse](https://github.com/run-llama/liteparse) `lit parse --format json`.
    ///
    /// Reads `pages[].text_items[]` whose `font_name` is `"OCR"`; native-text
    /// items keep their real font name and are skipped. `rotation` is read when
    /// present (`--extract-text-metadata`). The 0.1 field names `textItems` and
    /// `fontName` are still accepted.
    #[default]
    Liteparse,
}

impl Adapter {
    /// Every adapter, in the order the CLI lists them.
    pub const ALL: [Adapter; 1] = [Adapter::Liteparse];

    /// The name used by `--from` and the language packages.
    pub fn name(self) -> &'static str {
        match self {
            Adapter::Liteparse => "liteparse",
        }
    }

    /// Resolve an adapter by name, ignoring case.
    pub fn from_name(name: &str) -> Option<Adapter> {
        Adapter::ALL
            .into_iter()
            .find(|adapter| adapter.name().eq_ignore_ascii_case(name))
    }

    /// The pages of `document` that carry OCR boxes, in document order.
    ///
    /// Pages without OCR boxes are omitted. Malformed pages and items are
    /// skipped rather than rejected, so one bad entry does not cost the rest.
    pub fn parse(self, document: &Value) -> Vec<ParsedPage> {
        match self {
            Adapter::Liteparse => liteparse(document),
        }
    }

    /// Parse a JSON document held in memory.
    pub fn parse_str(self, json: &str) -> Result<Vec<ParsedPage>, Error> {
        Ok(self.parse(&serde_json::from_str(json)?))
    }

    /// Parse the JSON file at `path`.
    pub fn parse_file(self, path: impl AsRef<Path>) -> Result<Vec<ParsedPage>, Error> {
        let path = path.as_ref();
        let json = std::fs::read_to_string(path).map_err(|source| crate::io_error(path, source))?;
        self.parse_str(&json)
    }
}

impl fmt::Display for Adapter {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.name())
    }
}

impl FromStr for Adapter {
    type Err = Error;

    fn from_str(name: &str) -> Result<Adapter, Error> {
        Adapter::from_name(name).ok_or_else(|| Error::UnknownAdapter(name.to_string()))
    }
}

/// `font_name` that LiteParse gives text it read with OCR.
const LITEPARSE_OCR_FONT: &str = "OCR";

fn liteparse(document: &Value) -> Vec<ParsedPage> {
    let Some(pages) = document.get("pages").and_then(Value::as_array) else {
        return Vec::new();
    };

    pages
        .iter()
        .filter_map(|page| {
            let page = page.as_object()?;
            let page_number = u32::try_from(page.get("page")?.as_u64()?).ok()?;
            let items = if page.contains_key("text_items") {
                page.get("text_items")
            } else {
                page.get("textItems")
            };
            let boxes: Vec<TextBox> = items
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|item| liteparse_box(item.as_object()?))
                .collect();

            (!boxes.is_empty()).then_some(ParsedPage { page_number, boxes })
        })
        .collect()
}

/// One drawable OCR box, or `None` when the item should be skipped.
fn liteparse_box(item: &Map<String, Value>) -> Option<TextBox> {
    if first_present(item, &["font_name", "fontName"])?.as_str()? != LITEPARSE_OCR_FONT {
        return None;
    }

    let text = item.get("text").and_then(Value::as_str).unwrap_or("").trim();
    let width = optional_number(item, "width")?.unwrap_or(0.0);
    let height = optional_number(item, "height")?.unwrap_or(0.0);
    if text.is_empty() || width <= 0.0 || height <= 0.0 {
        return None;
    }

    Some(TextBox {
        text: text.to_string(),
        x: item.get("x")?.as_f64()?,
        y: item.get("y")?.as_f64()?,
        width,
        height,
        confidence: optional_number(item, "confidence")?.unwrap_or(1.0),
        rotation: optional_number(item, "rotation")?.unwrap_or(0.0),
    })
}

/// The first of `keys` that is present and not null.
fn first_present<'a>(item: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    keys.iter()
        .filter_map(|key| item.get(*key))
        .find(|value| !value.is_null())
}

/// `Some(None)` when `key` is missing or null, `None` when it is not a number.
fn optional_number(item: &Map<String, Value>, key: &str) -> Option<Option<f64>> {
    match item.get(key) {
        None | Some(Value::Null) => Some(None),
        Some(value) => value.as_f64().map(Some),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn item(text: &str, font_name: &str) -> Value {
        json!({"text": text, "x": 10.0, "y": 20.0, "width": 30.0, "height": 8.0, "font_name": font_name})
    }

    #[test]
    fn keeps_only_ocr_items_and_pages_that_have_them() {
        let document = json!({"pages": [
            {"page": 1, "text_items": [item("native", "Helvetica"), item(" scanned ", "OCR")]},
            {"page": 2, "text_items": [item("native only", "Times")]},
            {"page": 3, "text_items": []},
        ]});

        let pages = Adapter::Liteparse.parse(&document);

        assert_eq!(
            pages,
            vec![ParsedPage {
                page_number: 1,
                boxes: vec![TextBox::new("scanned", 10.0, 20.0, 30.0, 8.0)],
            }]
        );
    }

    #[test]
    fn reads_confidence_and_rotation_and_defaults_them_when_missing() {
        let document = json!({"pages": [{"page": 4, "text_items": [
            {"text": "a", "x": 1, "y": 2, "width": 3, "height": 4, "font_name": "OCR",
             "confidence": 0.25, "rotation": 90},
            {"text": "b", "x": 1, "y": 2, "width": 3, "height": 4, "font_name": "OCR",
             "confidence": null},
        ]}]});

        let boxes = &Adapter::Liteparse.parse(&document)[0].boxes;

        assert_eq!((boxes[0].confidence, boxes[0].rotation), (0.25, 90.0));
        assert_eq!((boxes[1].confidence, boxes[1].rotation), (1.0, 0.0));
    }

    #[test]
    fn accepts_the_camel_case_field_names() {
        let document = json!({"pages": [{"page": 1, "textItems": [
            {"text": "old", "x": 1, "y": 2, "width": 3, "height": 4, "fontName": "OCR"},
        ]}]});

        assert_eq!(Adapter::Liteparse.parse(&document)[0].boxes[0].text, "old");
    }

    #[test]
    fn skips_malformed_pages_and_items() {
        let document = json!({"pages": [
            "not a page",
            {"page": "1", "text_items": [item("string page number", "OCR")]},
            {"page": -1, "text_items": [item("negative page", "OCR")]},
            {"page": 2, "text_items": [
                7,
                {"text": "no x", "y": 2, "width": 3, "height": 4, "font_name": "OCR"},
                {"text": "zero width", "x": 1, "y": 2, "width": 0, "height": 4, "font_name": "OCR"},
                {"text": "no height", "x": 1, "y": 2, "width": 3, "font_name": "OCR"},
                {"text": "bad confidence", "x": 1, "y": 2, "width": 3, "height": 4, "font_name": "OCR",
                 "confidence": "high"},
                {"text": "   ", "x": 1, "y": 2, "width": 3, "height": 4, "font_name": "OCR"},
                {"x": 1, "y": 2, "width": 3, "height": 4, "font_name": "OCR"},
                item("kept", "OCR"),
            ]},
        ]});

        let pages = Adapter::Liteparse.parse(&document);

        assert_eq!(pages.len(), 1);
        assert_eq!(pages[0].page_number, 2);
        assert_eq!(pages[0].boxes.len(), 1);
        assert_eq!(pages[0].boxes[0].text, "kept");
    }

    #[test]
    fn a_document_without_pages_has_no_boxes() {
        assert!(Adapter::Liteparse.parse(&json!({})).is_empty());
        assert!(Adapter::Liteparse.parse(&json!({"pages": {"1": []}})).is_empty());
        assert!(matches!(Adapter::Liteparse.parse_str("{"), Err(Error::Json(_))));
    }

    #[test]
    fn names_resolve_ignoring_case() {
        assert_eq!("LiteParse".parse::<Adapter>().unwrap(), Adapter::Liteparse);
        assert!(matches!("textract".parse::<Adapter>(), Err(Error::UnknownAdapter(name)) if name == "textract"));
    }
}
