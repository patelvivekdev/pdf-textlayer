//! Advance widths used to size each invisible run.
//!
//! The overlay font has no outlines, so any font's widths can drive the
//! layout. Proportional widths matter: OCR reports whole lines, and a word
//! inside a line only lands under its image when the characters before it
//! advance by roughly what the printed characters did.

use unicode_width::UnicodeWidthChar;

use crate::metrics_data::{HELVETICA, HELVETICA_BOLD, TIMES, TIMES_BOLD, TIMES_BOLD_ITALIC, TIMES_ITALIC};

const COURIER_ADVANCE: u16 = 600;
const WIDE_ADVANCE: u16 = 1000;

/// Which width table sizes the text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum FontMetrics {
    /// Helvetica widths. Fits most printed Latin text.
    #[default]
    Helvetica,
    /// Times widths.
    Times,
    /// Courier widths (monospaced).
    Courier,
}

impl FontMetrics {
    /// Resolve a font name.
    ///
    /// Accepts the PyMuPDF short names the Python package has always taken
    /// (`helv`, `tiro`, `cour`). The CJK names (`china-s`, `china-t`,
    /// `japan`, `korea`) resolve to Helvetica: the overlay font encodes every
    /// script, so a CJK font is no longer needed to keep that text.
    pub fn from_name(name: &str) -> Option<FontMetrics> {
        match name.to_ascii_lowercase().as_str() {
            "helv" | "helvetica" | "china-s" | "china-t" | "japan" | "korea" => Some(FontMetrics::Helvetica),
            "tiro" | "times" | "times-roman" => Some(FontMetrics::Times),
            "cour" | "courier" => Some(FontMetrics::Courier),
            _ => None,
        }
    }

    /// Advance of `character` in 1/1000 em, or `None` when it cannot be drawn.
    ///
    /// Control characters, zero-width characters, and characters outside the
    /// Basic Multilingual Plane cannot be drawn.
    pub(crate) fn advance(&self, character: char) -> Option<u16> {
        let codepoint = character as u32;
        if codepoint > 0xFFFF {
            return None;
        }

        let table: &[(u16, u16)] = match self {
            FontMetrics::Helvetica => &HELVETICA,
            FontMetrics::Times => &TIMES,
            FontMetrics::Courier => &[],
        };

        if let Some(advance) = lookup(table, character) {
            return Some(advance);
        }

        match character.width() {
            None | Some(0) => None,
            Some(1) => Some(self.fallback_advance()),
            Some(_) => Some(WIDE_ADVANCE),
        }
    }

    /// Advance for a narrow character the table does not list.
    pub(crate) fn fallback_advance(&self) -> u16 {
        match self {
            FontMetrics::Helvetica => 556,
            FontMetrics::Times => 500,
            FontMetrics::Courier => COURIER_ADVANCE,
        }
    }
}

impl std::str::FromStr for FontMetrics {
    type Err = crate::Error;

    fn from_str(name: &str) -> Result<FontMetrics, crate::Error> {
        FontMetrics::from_name(name).ok_or_else(|| crate::Error::UnknownFont(name.to_string()))
    }
}

fn lookup(table: &[(u16, u16)], character: char) -> Option<u16> {
    let codepoint = u16::try_from(character as u32).ok()?;
    let index = table.binary_search_by_key(&codepoint, |entry| entry.0).ok()?;

    Some(table[index].1)
}

/// Advance of `character` in one of the 14 standard fonts, in 1/1000 em.
///
/// A PDF may use a standard font without listing its widths. `base_font` is
/// the font's `/BaseFont` name. Names outside the standard set get the
/// closest of them: bold and italic are read from the name, and anything
/// that is not Times or Courier is treated as Helvetica.
pub(crate) fn standard_font_advance(base_font: &str, character: char) -> u16 {
    let name = base_font.to_ascii_lowercase();
    let bold = name.contains("bold");
    let italic = name.contains("italic") || name.contains("oblique");

    if name.contains("courier") {
        return COURIER_ADVANCE;
    }

    let (table, fallback): (&[(u16, u16)], u16) = if name.contains("times") {
        match (bold, italic) {
            (true, true) => (&TIMES_BOLD_ITALIC, 500),
            (true, false) => (&TIMES_BOLD, 500),
            (false, true) => (&TIMES_ITALIC, 500),
            (false, false) => (&TIMES, 500),
        }
    } else if bold {
        (&HELVETICA_BOLD, 556)
    } else {
        (&HELVETICA, 556)
    };

    lookup(table, character).unwrap_or(fallback)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn helvetica_widths_match_the_standard_metrics() {
        let helvetica = FontMetrics::Helvetica;

        assert_eq!(helvetica.advance(' '), Some(278));
        assert_eq!(helvetica.advance('A'), Some(667));
        assert_eq!(helvetica.advance('i'), Some(222));
        assert_eq!(helvetica.advance('é'), Some(556));
        assert_eq!(helvetica.advance('€'), Some(556));
    }

    #[test]
    fn scripts_outside_the_table_still_have_an_advance() {
        let helvetica = FontMetrics::Helvetica;

        assert_eq!(helvetica.advance('中'), Some(1000));
        assert_eq!(helvetica.advance('א'), Some(556));
    }

    #[test]
    fn undrawable_characters_have_no_advance() {
        let helvetica = FontMetrics::Helvetica;

        assert_eq!(helvetica.advance('\n'), None);
        assert_eq!(helvetica.advance('\u{200b}'), None);
        assert_eq!(helvetica.advance('\u{0301}'), None);
        assert_eq!(helvetica.advance('😀'), None);
    }

    #[test]
    fn standard_fonts_are_picked_from_the_base_font_name() {
        assert_eq!(standard_font_advance("Helvetica", 'a'), 556);
        assert_eq!(standard_font_advance("Helvetica-Bold", 'a'), 556);
        assert_eq!(standard_font_advance("Helvetica-Bold", 'f'), 333);
        assert_eq!(standard_font_advance("Helvetica", 'f'), 278);
        assert_eq!(standard_font_advance("Times-Roman", 'a'), 444);
        assert_eq!(standard_font_advance("Times-BoldItalic", 'a'), 500);
        assert_eq!(standard_font_advance("Courier-Oblique", 'a'), 600);
        assert_eq!(standard_font_advance("ABCDEF+Arial-BoldMT", 'f'), 333);
    }

    #[test]
    fn courier_is_monospaced() {
        let courier = FontMetrics::Courier;

        assert_eq!(courier.advance('i'), Some(600));
        assert_eq!(courier.advance('W'), Some(600));
    }

    #[test]
    fn pymupdf_font_names_resolve() {
        assert_eq!(FontMetrics::from_name("helv"), Some(FontMetrics::Helvetica));
        assert_eq!(FontMetrics::from_name("tiro"), Some(FontMetrics::Times));
        assert_eq!(FontMetrics::from_name("cour"), Some(FontMetrics::Courier));
        assert_eq!(FontMetrics::from_name("china-s"), Some(FontMetrics::Helvetica));
        assert_eq!(FontMetrics::from_name("comic"), None);
    }
}
