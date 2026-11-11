//! Draw the invisible runs onto each page and attach them to the document.

use std::collections::HashMap;
use std::fmt::Write as _;
use std::io::Write as _;

use flate2::Compression;
use flate2::write::ZlibEncoder;

use lopdf::{Dictionary, Document, Object, ObjectId, Stream};

use crate::geometry::{Rect, covers_native_word, display_to_page, snap_to_right_angle};
use crate::native_text::{FontCache, PageScan, inherited, number, rect_from, scan_page};
use crate::overlay_font::OverlayFont;
use crate::{Options, ParsedPage, Stats, TextBox};

/// Resource name of the overlay font on a page.
const FONT_RESOURCE: &str = "pdftextlayer";

/// Text render mode 3: neither fill nor stroke. The glyphs are in the content
/// stream, so search and copy find them, but they are never painted.
const INVISIBLE_RENDER_MODE: u8 = 3;

/// Characters the overlay cannot encode are drawn as this.
const UNSUPPORTED_GLYPH: char = '?';

const HEX_DIGITS: &[u8; 16] = b"0123456789ABCDEF";

/// US Letter, the size a page has when it states no `/MediaBox`.
const DEFAULT_MEDIA_BOX: Rect = Rect {
    x0: 0.0,
    y0: 0.0,
    x1: 612.0,
    y1: 792.0,
};

/// The visible area of a page and how it is turned for display.
struct PageGeometry {
    /// The crop box in PDF space.
    crop_box: Rect,
    /// `/Rotate`, normalized to 0, 90, 180, or 270.
    rotation: i64,
}

impl PageGeometry {
    fn read(document: &Document, page: &Dictionary) -> PageGeometry {
        let media_box = inherited(document, page, b"MediaBox")
            .and_then(|media_box| rect_from(document, media_box))
            .filter(|media_box| media_box.width() > 0.0 && media_box.height() > 0.0)
            .unwrap_or(DEFAULT_MEDIA_BOX);
        let crop_box = inherited(document, page, b"CropBox")
            .and_then(|crop_box| rect_from(document, crop_box))
            .map(|crop_box| Rect {
                x0: crop_box.x0.max(media_box.x0),
                y0: crop_box.y0.max(media_box.y0),
                x1: crop_box.x1.min(media_box.x1),
                y1: crop_box.y1.min(media_box.y1),
            })
            .filter(|crop_box| crop_box.width() > 0.0 && crop_box.height() > 0.0)
            .unwrap_or(media_box);
        let rotation = inherited(document, page, b"Rotate").and_then(number).unwrap_or(0.0);

        PageGeometry {
            crop_box,
            rotation: snap_to_right_angle(rotation),
        }
    }

    /// Map a rectangle from PDF space to page space (top-left origin, y down).
    fn to_page_space(&self, rect: &Rect) -> Rect {
        Rect {
            x0: rect.x0 - self.crop_box.x0,
            y0: self.crop_box.y1 - rect.y1,
            x1: rect.x1 - self.crop_box.x0,
            y1: self.crop_box.y1 - rect.y0,
        }
    }
}

/// One run ready to be written: the text and where its baseline starts.
struct Run<'a> {
    text: &'a str,
    /// The box in page space.
    rect: Rect,
    /// Text direction in degrees counter-clockwise: 0, 90, 180, or 270.
    rotation: i64,
}

/// Per-document state shared by every page.
pub(crate) struct Overlay<'a> {
    options: &'a Options,
    font: OverlayFont,
    native_fonts: FontCache,
    /// `q` streams prepended to pages whose content leaves the graphics state
    /// dirty, keyed by how many `q` they hold.
    save_streams: HashMap<i64, ObjectId>,
}

impl<'a> Overlay<'a> {
    pub fn new(document: &mut Document, options: &'a Options) -> Overlay<'a> {
        Overlay {
            options,
            font: OverlayFont::reserve(document, options.font.fallback_advance()),
            native_fonts: FontCache::default(),
            save_streams: HashMap::new(),
        }
    }

    /// Overlay every page that has drawable boxes and return what was written.
    pub fn run(mut self, document: &mut Document, pages: &[ParsedPage]) -> Result<Stats, lopdf::Error> {
        // A later entry for the same page number replaces an earlier one.
        let parsed_by_number: HashMap<u32, &ParsedPage> = pages.iter().map(|page| (page.page_number, page)).collect();
        let page_ids = document.get_pages();
        let mut stats = Stats {
            pages: page_ids.len(),
            ocr_pages: Vec::new(),
            items_drawn: 0,
        };

        for (page_number, page_id) in page_ids {
            let Some(parsed) = parsed_by_number.get(&page_number) else {
                continue;
            };
            let drawn = self.overlay_page(document, page_id, &parsed.boxes)?;

            if drawn > 0 {
                stats.ocr_pages.push(page_number);
                stats.items_drawn += drawn;
            }
        }

        if stats.items_drawn > 0 {
            self.font.finish(document);
        }

        Ok(stats)
    }

    /// Draw one page's boxes. Returns how many landed.
    fn overlay_page(
        &mut self,
        document: &mut Document,
        page_id: ObjectId,
        boxes: &[TextBox],
    ) -> Result<usize, lopdf::Error> {
        let page = document.get_dictionary(page_id)?;
        let geometry = PageGeometry::read(document, page);
        let candidates: Vec<Run> = boxes
            .iter()
            .filter(|text_box| self.is_drawable(text_box))
            .filter_map(|text_box| run_in_page_space(text_box, &geometry))
            .collect();

        if candidates.is_empty() {
            return Ok(0);
        }

        let scan = scan_page(document, page, page_id, &mut self.native_fonts);
        let native_words: Vec<Rect> = scan.words.iter().map(|word| geometry.to_page_space(word)).collect();
        let (resources, font_name) = resources_with_overlay_font(document, page, self.font.id);

        let mut content = String::new();
        let mut drawn = 0;

        for run in &candidates {
            if covers_native_word(&run.rect, &native_words) {
                continue;
            }

            if self.draw(&mut content, run, &geometry, &font_name) {
                drawn += 1;
            }
        }

        if drawn == 0 {
            return Ok(0);
        }

        let mut contents: Vec<Object> = Vec::new();
        let mut stream = String::new();

        if !scan.leaves_graphics_state_clean() {
            // Bracket the existing content so the overlay starts from the
            // page's default matrix whatever that content left behind.
            let saves = 1 + scan.unmatched_restores;
            let save_stream = *self.save_streams.entry(saves).or_insert_with(|| {
                document.add_object(Stream::new(
                    Dictionary::new(),
                    "q\n".repeat(saves as usize).into_bytes(),
                ))
            });
            contents.push(save_stream.into());
            stream.push_str(&"Q\n".repeat(restores_after(&scan) as usize));
        }

        let _ = writeln!(stream, "q {INVISIBLE_RENDER_MODE} Tr");
        stream.push_str(&content);
        stream.push_str("Q\n");

        contents.extend(document.get_page_contents(page_id).into_iter().map(Object::from));
        contents.push(document.add_object(deflated(stream.into_bytes())).into());

        let page = document.get_dictionary_mut(page_id)?;
        page.set("Contents", contents);
        page.set("Resources", resources);

        Ok(drawn)
    }

    fn is_drawable(&self, text_box: &TextBox) -> bool {
        // Written so that a NaN in any field fails the test.
        text_box.confidence >= self.options.min_confidence
            && text_box.width > 0.0
            && text_box.height > 0.0
            && !text_box.text.trim().is_empty()
    }

    /// Append one run to `content`. Returns false when nothing can be drawn.
    fn draw(&mut self, content: &mut String, run: &Run, geometry: &PageGeometry, font_name: &str) -> bool {
        let metrics = self.options.font;
        let mut code_units: Vec<(u16, u16)> = Vec::with_capacity(run.text.len());
        let mut width_in_thousandths: u64 = 0;

        for character in run.text.chars() {
            let (character, advance) = match metrics.advance(character) {
                Some(advance) => (character, advance),
                None if self.options.sanitize_unsupported => (
                    UNSUPPORTED_GLYPH,
                    metrics.advance(UNSUPPORTED_GLYPH).unwrap_or(metrics.fallback_advance()),
                ),
                None => return false,
            };

            // `advance` only returns characters of the Basic Multilingual
            // Plane, which are one UTF-16 code unit each.
            code_units.push((character as u16, advance));
            width_in_thousandths += u64::from(advance);
        }

        if width_in_thousandths == 0 {
            return false;
        }

        let width_at_1pt = width_in_thousandths as f64 / 1000.0;
        let horizontal = matches!(run.rotation, 0 | 180);
        let (along_text, across_text) = if horizontal {
            (run.rect.width(), run.rect.height())
        } else {
            (run.rect.height(), run.rect.width())
        };

        // Scale along the text direction so the run fills the OCR box. Cap
        // the size across it so a wide but short box cannot blow the font up.
        let font_size = (along_text / width_at_1pt).min((across_text * 1.2).max(1.0));

        // The baseline's left end in page space, and the text matrix that
        // turns the run, for each direction.
        let ((x, y), (a, b, c, d)) = match run.rotation {
            90 => ((run.rect.x1, run.rect.y1), (0, 1, -1, 0)),
            180 => ((run.rect.x1, run.rect.y0), (-1, 0, 0, -1)),
            270 => ((run.rect.x0, run.rect.y0), (0, -1, 1, 0)),
            _ => ((run.rect.x0, run.rect.y1), (1, 0, 0, 1)),
        };

        let _ = write!(content, "BT /{font_name} ");
        push_number(content, font_size, 5);
        let _ = write!(content, " Tf {a} {b} {c} {d} ");
        push_number(content, geometry.crop_box.x0 + x, 4);
        content.push(' ');
        push_number(content, geometry.crop_box.y1 - y, 4);
        content.push_str(" Tm <");

        for (code_unit, advance) in code_units {
            for shift in [12, 8, 4, 0] {
                content.push(char::from(HEX_DIGITS[usize::from(code_unit >> shift) & 0xF]));
            }

            self.font.record(code_unit, advance);
        }

        content.push_str("> Tj ET\n");

        true
    }
}

/// Flate level for the overlay streams. A stream is hex digits and a few
/// operators. On a 500-page scan, level 4 writes 1.9 kB per page and costs
/// 0.1 ms per page over level 1, which writes 3.5 kB. Level 6 saves another
/// 0.3 kB for three times that cost.
const OVERLAY_COMPRESSION: Compression = Compression::new(4);

/// A Flate-compressed content stream.
fn deflated(content: Vec<u8>) -> Stream {
    let mut encoder = ZlibEncoder::new(Vec::with_capacity(content.len() / 3), OVERLAY_COMPRESSION);
    let compressed = encoder.write_all(&content).and_then(|()| encoder.finish());

    match compressed {
        Ok(compressed) => {
            let mut dictionary = Dictionary::new();
            dictionary.set("Filter", "FlateDecode");

            Stream::new(dictionary, compressed)
        }
        // Writing to a `Vec` cannot fail. Keep the plain stream if it ever does.
        Err(_) => Stream::new(Dictionary::new(), content),
    }
}

/// How many `Q` return the page to the state saved by the prepended stream.
fn restores_after(scan: &PageScan) -> i64 {
    if scan.fully_parsed { scan.open_saves + 1 } else { 1 }
}

/// Move a display-space box onto the unrotated page.
///
/// OCR reports coordinates in the upright view, after `/Rotate`. The content
/// stream is written in the unrotated page. The page rotation is added to the
/// text angle so the glyphs still read upright on screen.
fn run_in_page_space<'a>(text_box: &'a TextBox, geometry: &PageGeometry) -> Option<Run<'a>> {
    let display = Rect {
        x0: text_box.x,
        y0: text_box.y,
        x1: text_box.x + text_box.width,
        y1: text_box.y + text_box.height,
    };
    let rect = display_to_page(
        &display,
        geometry.rotation,
        geometry.crop_box.width(),
        geometry.crop_box.height(),
    );

    if !(rect.width() > 0.0 && rect.height() > 0.0) {
        return None;
    }

    Some(Run {
        text: &text_box.text,
        rect,
        rotation: (snap_to_right_angle(text_box.rotation) + geometry.rotation) % 360,
    })
}

/// The page's resources with the overlay font added, and the font's name.
///
/// The dictionaries are copied onto the page rather than edited in place:
/// they may be inherited from the page tree or shared with other pages.
fn resources_with_overlay_font(document: &Document, page: &Dictionary, font_id: ObjectId) -> (Dictionary, String) {
    let mut resources = inherited(document, page, b"Resources")
        .and_then(|resources| resources.as_dict().ok())
        .cloned()
        .unwrap_or_default();
    let mut fonts = resources
        .get_deref(b"Font", document)
        .ok()
        .and_then(|fonts| fonts.as_dict().ok())
        .cloned()
        .unwrap_or_default();

    // Reuse the name when this run already put the font on a shared
    // dictionary. Otherwise pick one the page does not use.
    let mut name = FONT_RESOURCE.to_string();
    let mut suffix = 1;

    while let Ok(existing) = fonts.get(name.as_bytes()) {
        if matches!(existing, Object::Reference(id) if *id == font_id) {
            break;
        }

        name = format!("{FONT_RESOURCE}{suffix}");
        suffix += 1;
    }

    fonts.set(name.as_bytes(), font_id);
    resources.set("Font", fonts);

    (resources, name)
}

/// Write `value` with at most `decimals` decimal places and no trailing zeros.
fn push_number(target: &mut String, value: f64, decimals: usize) {
    let start = target.len();
    let _ = write!(target, "{value:.decimals$}");

    if target[start..].contains('.') {
        while target.ends_with('0') {
            target.pop();
        }

        if target.ends_with('.') {
            target.pop();
        }
    }

    if &target[start..] == "-0" {
        target.truncate(start);
        target.push('0');
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn formatted(value: f64, decimals: usize) -> String {
        let mut text = String::new();
        push_number(&mut text, value, decimals);

        text
    }

    #[test]
    fn numbers_are_written_without_trailing_zeros() {
        assert_eq!(formatted(12.0, 4), "12");
        assert_eq!(formatted(12.5, 4), "12.5");
        assert_eq!(formatted(0.12345678, 4), "0.1235");
        assert_eq!(formatted(-0.00001, 4), "0");
        assert_eq!(formatted(100.0, 0), "100");
    }
}
