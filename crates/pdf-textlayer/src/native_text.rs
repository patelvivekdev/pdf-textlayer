//! Find where a page already has text.
//!
//! An OCR box that lands on a word the PDF already contains must not be
//! drawn again. This module walks a page's content stream, its form
//! XObjects, and its annotation appearances, and returns one rectangle per
//! word in PDF space. It tracks just enough text state to place each glyph:
//! the matrices, the font's advance widths, and where the spaces are. It
//! does not decode the text itself.
//!
//! The same walk reports how the content leaves the graphics state, which
//! the writer needs before it appends its own stream.

use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use lopdf::content::{Content, Operation};
use lopdf::{Dictionary, Document, Object, ObjectId};

use crate::geometry::{Matrix, Rect};
use crate::metrics::standard_font_advance;

/// Form XObjects can nest. Real documents stay shallow; a cycle would not.
const MAX_FORM_DEPTH: usize = 12;

/// A glyph that starts further than this from where the previous one ended
/// begins a new word. In units of the font size.
const WORD_GAP_IN_EM: f64 = 0.3;

/// Ascender and descender used when a font does not state its own.
const DEFAULT_ASCENT: f64 = 1.075;
const DEFAULT_DESCENT: f64 = -0.299;

const ANNOTATION_HIDDEN: i64 = 1 << 1;
const ANNOTATION_NO_VIEW: i64 = 1 << 5;

/// What one walk over a page found.
#[derive(Debug, Default)]
pub(crate) struct PageScan {
    /// One rectangle per word, in PDF space.
    pub words: Vec<Rect>,
    /// How many `q` are still open at the end of the page content.
    pub open_saves: i64,
    /// How far `Q` ran below the starting level, as a positive count.
    pub unmatched_restores: i64,
    /// Whether the content changes the matrix outside any `q`/`Q` pair.
    pub changes_base_matrix: bool,
    /// False when the content stream could not be parsed to its end.
    pub fully_parsed: bool,
}

impl PageScan {
    /// Whether a stream appended after the page content would run under the
    /// page's default graphics state.
    pub fn leaves_graphics_state_clean(&self) -> bool {
        self.fully_parsed && self.open_saves == 0 && self.unmatched_restores == 0 && !self.changes_base_matrix
    }
}

/// Fonts parsed so far, shared across the pages of a document.
#[derive(Default)]
pub(crate) struct FontCache {
    fonts: HashMap<ObjectId, Rc<Font>>,
}

pub(crate) fn scan_page(document: &Document, page: &Dictionary, page_id: ObjectId, fonts: &mut FontCache) -> PageScan {
    let mut scan = PageScan {
        fully_parsed: true,
        ..PageScan::default()
    };
    let resources = inherited(document, page, b"Resources").and_then(|object| object.as_dict().ok());
    let empty = Dictionary::new();
    let resources = resources.unwrap_or(&empty);

    let content = document.get_page_content(page_id);
    let operations = match Content::decode(&content) {
        Ok(content) => content.operations,
        Err(_) => {
            scan.fully_parsed = false;
            Vec::new()
        }
    };

    let mut walker = Walker {
        document,
        fonts,
        words: Vec::new(),
        word: None,
        pen: None,
        active_forms: HashSet::new(),
    };
    let balance = walker.run(&operations, resources, Matrix::IDENTITY, 0);
    scan.open_saves = balance.open_saves;
    scan.unmatched_restores = balance.unmatched_restores;
    scan.changes_base_matrix = balance.changes_base_matrix;

    walker.run_annotations(page);
    scan.words = walker.words;

    scan
}

/// Look up a page attribute, following `/Parent` when the page lacks it.
pub(crate) fn inherited<'a>(document: &'a Document, page: &'a Dictionary, key: &[u8]) -> Option<&'a Object> {
    let mut node = page;

    for _ in 0..64 {
        if let Ok(value) = node.get_deref(key, document) {
            return Some(value);
        }

        node = node.get_deref(b"Parent", document).ok()?.as_dict().ok()?;
    }

    None
}

pub(crate) fn number(object: &Object) -> Option<f64> {
    match object {
        Object::Integer(value) => Some(*value as f64),
        Object::Real(value) => Some(f64::from(*value)),
        _ => None,
    }
}

fn numbers<const N: usize>(document: &Document, object: &Object) -> Option<[f64; N]> {
    let array = document.dereference(object).ok()?.1.as_array().ok()?;
    if array.len() < N {
        return None;
    }

    let mut values = [0.0; N];

    for (value, item) in values.iter_mut().zip(array) {
        *value = number(document.dereference(item).ok()?.1)?;
    }

    Some(values)
}

fn matrix_from(document: &Document, object: &Object) -> Option<Matrix> {
    let [a, b, c, d, e, f] = numbers::<6>(document, object)?;

    Some(Matrix { a, b, c, d, e, f })
}

pub(crate) fn rect_from(document: &Document, object: &Object) -> Option<Rect> {
    let [x0, y0, x1, y1] = numbers::<4>(document, object)?;

    Some(Rect {
        x0: x0.min(x1),
        y0: y0.min(y1),
        x1: x0.max(x1),
        y1: y0.max(y1),
    })
}

#[derive(Debug, Default, Clone, Copy)]
struct Balance {
    open_saves: i64,
    unmatched_restores: i64,
    changes_base_matrix: bool,
}

/// The part of the graphics state that `q` saves and `Q` restores.
#[derive(Clone)]
struct GraphicsState {
    matrix: Matrix,
    font: Option<Rc<Font>>,
    font_size: f64,
    character_spacing: f64,
    word_spacing: f64,
    horizontal_scale: f64,
    leading: f64,
    rise: f64,
}

struct Walker<'a> {
    document: &'a Document,
    fonts: &'a mut FontCache,
    words: Vec<Rect>,
    /// The word being built.
    word: Option<Rect>,
    /// Where the last glyph ended, and how far the next may start from there.
    pen: Option<((f64, f64), f64)>,
    active_forms: HashSet<ObjectId>,
}

impl Walker<'_> {
    fn run(&mut self, operations: &[Operation], resources: &Dictionary, matrix: Matrix, depth: usize) -> Balance {
        let mut balance = Balance::default();
        let mut state = GraphicsState {
            matrix,
            font: None,
            font_size: 0.0,
            character_spacing: 0.0,
            word_spacing: 0.0,
            horizontal_scale: 1.0,
            leading: 0.0,
            rise: 0.0,
        };
        let mut saved: Vec<GraphicsState> = Vec::new();
        let mut text_matrix = Matrix::IDENTITY;
        let mut line_matrix = Matrix::IDENTITY;

        for operation in operations {
            let operands = &operation.operands;
            let operand = |index: usize| operands.get(index).and_then(number);

            match operation.operator.as_str() {
                "q" => saved.push(state.clone()),
                "Q" => match saved.pop() {
                    Some(previous) => state = previous,
                    None => balance.unmatched_restores += 1,
                },
                "cm" => {
                    if let [Some(a), Some(b), Some(c), Some(d), Some(e), Some(f)] =
                        [operand(0), operand(1), operand(2), operand(3), operand(4), operand(5)]
                    {
                        state.matrix = Matrix { a, b, c, d, e, f }.then(&state.matrix);

                        if saved.is_empty() {
                            balance.changes_base_matrix = true;
                        }
                    }
                }
                "BT" => {
                    text_matrix = Matrix::IDENTITY;
                    line_matrix = Matrix::IDENTITY;
                }
                "ET" => self.end_word(),
                "Tf" => {
                    state.font = operands
                        .first()
                        .and_then(|name| name.as_name().ok())
                        .and_then(|name| self.font(resources, name));
                    state.font_size = operand(1).unwrap_or(0.0);
                }
                "Tc" => state.character_spacing = operand(0).unwrap_or(0.0),
                "Tw" => state.word_spacing = operand(0).unwrap_or(0.0),
                "Tz" => state.horizontal_scale = operand(0).unwrap_or(100.0) / 100.0,
                "TL" => state.leading = operand(0).unwrap_or(0.0),
                "Ts" => state.rise = operand(0).unwrap_or(0.0),
                "Td" | "TD" => {
                    let (tx, ty) = (operand(0).unwrap_or(0.0), operand(1).unwrap_or(0.0));

                    if operation.operator == "TD" {
                        state.leading = -ty;
                    }

                    line_matrix = Matrix::translation(tx, ty).then(&line_matrix);
                    text_matrix = line_matrix;
                }
                "Tm" => {
                    if let [Some(a), Some(b), Some(c), Some(d), Some(e), Some(f)] =
                        [operand(0), operand(1), operand(2), operand(3), operand(4), operand(5)]
                    {
                        line_matrix = Matrix { a, b, c, d, e, f };
                        text_matrix = line_matrix;
                    }
                }
                "T*" => {
                    line_matrix = Matrix::translation(0.0, -state.leading).then(&line_matrix);
                    text_matrix = line_matrix;
                }
                "Tj" => {
                    if let Some(Object::String(bytes, _)) = operands.first() {
                        self.show(bytes, &state, &mut text_matrix);
                    }
                }
                "'" | "\"" => {
                    let text = if operation.operator == "\"" {
                        state.word_spacing = operand(0).unwrap_or(state.word_spacing);
                        state.character_spacing = operand(1).unwrap_or(state.character_spacing);
                        operands.get(2)
                    } else {
                        operands.first()
                    };

                    line_matrix = Matrix::translation(0.0, -state.leading).then(&line_matrix);
                    text_matrix = line_matrix;

                    if let Some(Object::String(bytes, _)) = text {
                        self.show(bytes, &state, &mut text_matrix);
                    }
                }
                "TJ" => {
                    if let Some(Object::Array(items)) = operands.first() {
                        for item in items {
                            match item {
                                Object::String(bytes, _) => self.show(bytes, &state, &mut text_matrix),
                                other => {
                                    if let Some(adjustment) = number(other) {
                                        let shift = -adjustment / 1000.0 * state.font_size * state.horizontal_scale;
                                        text_matrix = Matrix::translation(shift, 0.0).then(&text_matrix);
                                    }
                                }
                            }
                        }
                    }
                }
                "Do" => {
                    if let Some(name) = operands.first().and_then(|name| name.as_name().ok()) {
                        self.run_form_xobject(resources, name, &state.matrix, depth);
                    }
                }
                _ => {}
            }
        }

        self.end_word();
        balance.open_saves = saved.len() as i64;

        balance
    }

    /// Place the glyphs of one string and grow the current word.
    fn show(&mut self, bytes: &[u8], state: &GraphicsState, text_matrix: &mut Matrix) {
        let Some(font) = state.font.as_ref() else {
            return;
        };
        let code_length = if font.two_byte_codes { 2 } else { 1 };

        for code_bytes in bytes.chunks(code_length) {
            let code = code_bytes
                .iter()
                .fold(0u32, |code, byte| (code << 8) | u32::from(*byte));
            let advance = font.advance(code);
            let to_page = text_matrix.then(&state.matrix);
            let start = to_page.apply(0.0, state.rise);

            if font.is_space(code) {
                self.end_word();
            } else {
                if let Some((pen, tolerance)) = self.pen
                    && (start.0 - pen.0).hypot(start.1 - pen.1) > tolerance
                {
                    self.end_word();
                }

                let glyph = to_page.apply_rect(&Rect {
                    x0: 0.0,
                    y0: font.descent * state.font_size + state.rise,
                    x1: advance * state.font_size * state.horizontal_scale,
                    y1: font.ascent * state.font_size + state.rise,
                });

                self.word = Some(match self.word {
                    Some(word) => word.union(&glyph),
                    None => glyph,
                });
            }

            let mut shift = advance * state.font_size + state.character_spacing;

            // Word spacing applies to the single-byte code 32 only.
            if code == 32 && code_length == 1 {
                shift += state.word_spacing;
            }

            *text_matrix = Matrix::translation(shift * state.horizontal_scale, 0.0).then(text_matrix);

            let to_page = text_matrix.then(&state.matrix);
            let tolerance = WORD_GAP_IN_EM * state.font_size.abs() * to_page.scale();
            self.pen = Some((to_page.apply(0.0, state.rise), tolerance));
        }
    }

    fn end_word(&mut self) {
        if let Some(word) = self.word.take()
            && word.width() > 0.0
            && word.height() > 0.0
        {
            self.words.push(word);
        }

        self.pen = None;
    }

    fn font(&mut self, resources: &Dictionary, name: &[u8]) -> Option<Rc<Font>> {
        let fonts = resources.get_deref(b"Font", self.document).ok()?.as_dict().ok()?;

        match fonts.get(name).ok()? {
            Object::Reference(id) => {
                if let Some(font) = self.fonts.fonts.get(id) {
                    return Some(Rc::clone(font));
                }

                let dictionary = self.document.get_dictionary(*id).ok()?;
                let font = Rc::new(Font::load(self.document, dictionary));
                self.fonts.fonts.insert(*id, Rc::clone(&font));

                Some(font)
            }
            Object::Dictionary(dictionary) => Some(Rc::new(Font::load(self.document, dictionary))),
            _ => None,
        }
    }

    fn run_form_xobject(&mut self, resources: &Dictionary, name: &[u8], matrix: &Matrix, depth: usize) {
        let Some(Object::Reference(id)) = resources
            .get_deref(b"XObject", self.document)
            .ok()
            .and_then(|xobjects| xobjects.as_dict().ok())
            .and_then(|xobjects| xobjects.get(name).ok())
        else {
            return;
        };

        self.run_form(*id, resources, matrix, depth);
    }

    /// Walk a form XObject under `matrix`. Images and other streams are skipped.
    fn run_form(&mut self, id: ObjectId, parent_resources: &Dictionary, matrix: &Matrix, depth: usize) {
        if depth >= MAX_FORM_DEPTH || self.active_forms.contains(&id) {
            return;
        }

        let Ok(Object::Stream(form)) = self.document.get_object(id) else {
            return;
        };

        if !matches!(form.dict.get(b"Subtype"), Ok(Object::Name(subtype)) if subtype == b"Form") {
            return;
        }

        let Ok(content) = form.get_plain_content() else {
            return;
        };
        let Ok(content) = Content::decode(&content) else {
            return;
        };

        let form_matrix = form
            .dict
            .get(b"Matrix")
            .ok()
            .and_then(|matrix| matrix_from(self.document, matrix))
            .unwrap_or(Matrix::IDENTITY);
        let resources = form
            .dict
            .get_deref(b"Resources", self.document)
            .ok()
            .and_then(|resources| resources.as_dict().ok())
            .unwrap_or(parent_resources);

        self.active_forms.insert(id);
        self.run(&content.operations, resources, form_matrix.then(matrix), depth + 1);
        self.active_forms.remove(&id);
    }

    /// Walk the normal appearance of each visible annotation and form field.
    fn run_annotations(&mut self, page: &Dictionary) {
        let Some(annotations) = page
            .get_deref(b"Annots", self.document)
            .ok()
            .and_then(|annotations| annotations.as_array().ok())
        else {
            return;
        };
        let empty = Dictionary::new();

        for annotation in annotations {
            let Some(annotation) = self
                .document
                .dereference(annotation)
                .ok()
                .and_then(|(_, annotation)| annotation.as_dict().ok())
            else {
                continue;
            };

            let flags = annotation
                .get(b"F")
                .ok()
                .and_then(|flags| flags.as_i64().ok())
                .unwrap_or(0);
            if flags & (ANNOTATION_HIDDEN | ANNOTATION_NO_VIEW) != 0 {
                continue;
            }

            let Some(appearance_id) = self.normal_appearance(annotation) else {
                continue;
            };
            let Some(rect) = annotation
                .get(b"Rect")
                .ok()
                .and_then(|rect| rect_from(self.document, rect))
            else {
                continue;
            };
            let Some(placement) = self.appearance_placement(appearance_id, &rect) else {
                continue;
            };

            self.run_form(appearance_id, &empty, &placement, 0);
        }
    }

    fn normal_appearance(&self, annotation: &Dictionary) -> Option<ObjectId> {
        let appearances = annotation.get_deref(b"AP", self.document).ok()?.as_dict().ok()?;

        match appearances.get(b"N").ok()? {
            Object::Reference(id) => match self.document.get_object(*id).ok()? {
                Object::Stream(_) => Some(*id),
                // A dictionary of states: `/AS` names the one that is shown.
                Object::Dictionary(states) => self.appearance_state(annotation, states),
                _ => None,
            },
            Object::Dictionary(states) => self.appearance_state(annotation, states),
            _ => None,
        }
    }

    fn appearance_state(&self, annotation: &Dictionary, states: &Dictionary) -> Option<ObjectId> {
        let state = annotation.get(b"AS").ok()?.as_name().ok()?;

        states.get(state).ok()?.as_reference().ok()
    }

    /// The matrix that maps an appearance's bounding box onto the annotation
    /// rectangle (PDF 32000-1, 12.5.5). The form's own `/Matrix` is applied
    /// by [`Walker::run_form`]; this returns only the placement after it.
    fn appearance_placement(&self, appearance_id: ObjectId, rect: &Rect) -> Option<Matrix> {
        let Object::Stream(form) = self.document.get_object(appearance_id).ok()? else {
            return None;
        };
        let bbox = rect_from(self.document, form.dict.get(b"BBox").ok()?)?;
        let form_matrix = form
            .dict
            .get(b"Matrix")
            .ok()
            .and_then(|matrix| matrix_from(self.document, matrix))
            .unwrap_or(Matrix::IDENTITY);
        let transformed = form_matrix.apply_rect(&bbox);

        if transformed.width() <= 0.0 || transformed.height() <= 0.0 {
            return None;
        }

        let scale_x = rect.width() / transformed.width();
        let scale_y = rect.height() / transformed.height();

        Some(Matrix {
            a: scale_x,
            b: 0.0,
            c: 0.0,
            d: scale_y,
            e: rect.x0 - transformed.x0 * scale_x,
            f: rect.y0 - transformed.y0 * scale_y,
        })
    }
}

enum Advances {
    /// One advance per single-byte code.
    Simple(Box<[f64; 256]>),
    /// Advances by CID, with the font's default for the rest.
    Composite { default: f64, by_cid: HashMap<u32, f64> },
}

/// What the walk needs to know about a font. All sizes are in text space
/// units for a font size of 1.
struct Font {
    two_byte_codes: bool,
    advances: Advances,
    ascent: f64,
    descent: f64,
    space_codes: Vec<u32>,
}

impl Font {
    fn advance(&self, code: u32) -> f64 {
        match &self.advances {
            Advances::Simple(advances) => advances[(code & 0xFF) as usize],
            Advances::Composite { default, by_cid } => by_cid.get(&code).copied().unwrap_or(*default),
        }
    }

    fn is_space(&self, code: u32) -> bool {
        self.space_codes.contains(&code)
    }

    fn load(document: &Document, font: &Dictionary) -> Font {
        let subtype = font
            .get(b"Subtype")
            .ok()
            .and_then(|subtype| subtype.as_name().ok())
            .unwrap_or(b"");
        let mut space_codes = font
            .get_deref(b"ToUnicode", document)
            .ok()
            .and_then(|to_unicode| to_unicode.as_stream().ok())
            .and_then(|to_unicode| to_unicode.get_plain_content().ok())
            .map(|cmap| space_codes_in_to_unicode(&cmap))
            .unwrap_or_default();

        if subtype == b"Type0" {
            let descendant = font
                .get_deref(b"DescendantFonts", document)
                .ok()
                .and_then(|descendants| descendants.as_array().ok())
                .and_then(|descendants| descendants.first())
                .and_then(|descendant| document.dereference(descendant).ok())
                .and_then(|(_, descendant)| descendant.as_dict().ok());
            let default = descendant
                .and_then(|descendant| descendant.get_deref(b"DW", document).ok())
                .and_then(number)
                .unwrap_or(1000.0)
                / 1000.0;
            let by_cid = descendant
                .and_then(|descendant| descendant.get_deref(b"W", document).ok())
                .and_then(|widths| widths.as_array().ok())
                .map(|widths| composite_advances(document, widths))
                .unwrap_or_default();
            let (ascent, descent) = descendant
                .and_then(|descendant| vertical_extent(document, descendant))
                .unwrap_or((DEFAULT_ASCENT, DEFAULT_DESCENT));

            return Font {
                two_byte_codes: true,
                advances: Advances::Composite { default, by_cid },
                ascent,
                descent,
                space_codes,
            };
        }

        if space_codes.is_empty() {
            space_codes.push(32);
        }

        if subtype == b"Type3" {
            return Font::load_type3(document, font, space_codes);
        }

        let (ascent, descent) = vertical_extent(document, font).unwrap_or((DEFAULT_ASCENT, DEFAULT_DESCENT));

        Font {
            two_byte_codes: false,
            advances: Advances::Simple(simple_advances(document, font, 0.001)),
            ascent,
            descent,
            space_codes,
        }
    }

    /// Type 3 widths and the font box are in glyph space, scaled by `/FontMatrix`.
    fn load_type3(document: &Document, font: &Dictionary, space_codes: Vec<u32>) -> Font {
        let font_matrix = font
            .get(b"FontMatrix")
            .ok()
            .and_then(|matrix| matrix_from(document, matrix))
            .unwrap_or(Matrix {
                a: 0.001,
                d: 0.001,
                ..Matrix::IDENTITY
            });
        let extent = font
            .get(b"FontBBox")
            .ok()
            .and_then(|bbox| rect_from(document, bbox))
            .map(|bbox| (bbox.y1 * font_matrix.d, bbox.y0 * font_matrix.d))
            .filter(|(ascent, descent)| ascent - descent > 0.1)
            .unwrap_or((DEFAULT_ASCENT, DEFAULT_DESCENT));

        Font {
            two_byte_codes: false,
            advances: Advances::Simple(simple_advances(document, font, font_matrix.a)),
            ascent: extent.0,
            descent: extent.1,
            space_codes,
        }
    }
}

/// Advances of a simple font, from `/Widths` or, for a standard font that
/// omits them, from the built-in tables.
fn simple_advances(document: &Document, font: &Dictionary, glyph_scale: f64) -> Box<[f64; 256]> {
    let mut advances = Box::new([0.0; 256]);
    let widths = font
        .get_deref(b"Widths", document)
        .ok()
        .and_then(|widths| widths.as_array().ok());

    if let Some(widths) = widths {
        let missing = font
            .get_deref(b"FontDescriptor", document)
            .ok()
            .and_then(|descriptor| descriptor.as_dict().ok())
            .and_then(|descriptor| descriptor.get_deref(b"MissingWidth", document).ok())
            .and_then(number)
            .unwrap_or(0.0);
        let first = font
            .get_deref(b"FirstChar", document)
            .ok()
            .and_then(number)
            .unwrap_or(0.0) as usize;

        advances.fill(missing * glyph_scale);

        for (offset, width) in widths.iter().enumerate() {
            let Some(advance) = advances.get_mut(first + offset) else {
                break;
            };

            if let Some(width) = document.dereference(width).ok().and_then(|(_, width)| number(width)) {
                *advance = width * glyph_scale;
            }
        }

        return advances;
    }

    let base_font = font
        .get(b"BaseFont")
        .ok()
        .and_then(|name| name.as_name().ok())
        .map(|name| String::from_utf8_lossy(name).into_owned())
        .unwrap_or_default();

    for (code, advance) in advances.iter_mut().enumerate() {
        *advance = f64::from(standard_font_advance(&base_font, windows_1252(code as u8))) / 1000.0;
    }

    advances
}

/// The character a standard font shows for a WinAnsi code.
fn windows_1252(code: u8) -> char {
    const HIGH: [char; 32] = [
        '€', '\u{81}', '‚', 'ƒ', '„', '…', '†', '‡', 'ˆ', '‰', 'Š', '‹', 'Œ', '\u{8d}', 'Ž', '\u{8f}', '\u{90}', '‘',
        '’', '“', '”', '•', '–', '—', '˜', '™', 'š', '›', 'œ', '\u{9d}', 'ž', 'Ÿ',
    ];

    match code {
        0x80..=0x9F => HIGH[(code - 0x80) as usize],
        _ => char::from(code),
    }
}

/// Parse a CID font `/W` array: `first [w ...]` and `first last w` entries.
fn composite_advances(document: &Document, widths: &[Object]) -> HashMap<u32, f64> {
    let mut by_cid = HashMap::new();
    let mut index = 0;
    let resolve = |object: &'_ Object| document.dereference(object).ok().map(|(_, object)| object.clone());

    while index + 1 < widths.len() {
        let Some(first) = resolve(&widths[index]).as_ref().and_then(number) else {
            break;
        };
        let first = first as u32;

        match resolve(&widths[index + 1]) {
            Some(Object::Array(run)) => {
                for (offset, width) in run.iter().enumerate() {
                    if let Some(width) = resolve(width).as_ref().and_then(number) {
                        by_cid.insert(first + offset as u32, width / 1000.0);
                    }
                }

                index += 2;
            }
            Some(last) => {
                let (Some(last), Some(width)) = (
                    number(&last),
                    widths.get(index + 2).and_then(&resolve).as_ref().and_then(number),
                ) else {
                    break;
                };

                // A range can name every CID. Keep the table bounded.
                for cid in first..=(last as u32).min(first.saturating_add(0xFFFF)) {
                    by_cid.insert(cid, width / 1000.0);
                }

                index += 3;
            }
            None => break,
        }
    }

    by_cid
}

/// Ascent and descent from the font descriptor, when it states usable ones.
fn vertical_extent(document: &Document, font: &Dictionary) -> Option<(f64, f64)> {
    let descriptor = font.get_deref(b"FontDescriptor", document).ok()?.as_dict().ok()?;
    let ascent = number(descriptor.get_deref(b"Ascent", document).ok()?)? / 1000.0;
    let descent = number(descriptor.get_deref(b"Descent", document).ok()?)? / 1000.0;
    // Some producers write the descent as a positive number.
    let descent = -descent.abs();

    (ascent > 0.0 && ascent - descent > 0.1).then_some((ascent, descent))
}

/// The character codes a `/ToUnicode` CMap maps to a space.
fn space_codes_in_to_unicode(cmap: &[u8]) -> Vec<u32> {
    #[derive(PartialEq)]
    enum Section {
        None,
        Chars,
        Ranges,
    }

    const SPACES: [u32; 3] = [0x20, 0xA0, 0x09];

    let mut codes = Vec::new();
    let mut section = Section::None;
    let mut pending: Vec<u32> = Vec::new();
    let mut in_array = false;
    let mut position = 0;

    while position < cmap.len() {
        match cmap[position] {
            b'<' => {
                let end = cmap[position..]
                    .iter()
                    .position(|byte| *byte == b'>')
                    .map_or(cmap.len(), |offset| position + offset);
                let value = cmap[position + 1..end]
                    .iter()
                    .filter_map(|byte| char::from(*byte).to_digit(16))
                    .fold(0u32, |value, digit| (value << 4) | digit);
                position = end + 1;

                match section {
                    Section::Chars => {
                        pending.push(value);

                        if let [code, target] = pending[..] {
                            if SPACES.contains(&target) {
                                codes.push(code);
                            }

                            pending.clear();
                        }
                    }
                    Section::Ranges if in_array => {
                        // `low high [target target ...]`: one target per code.
                        if pending.len() == 3 {
                            if SPACES.contains(&value) {
                                codes.push(pending[0] + pending[2]);
                            }

                            pending[2] += 1;
                        }
                    }
                    Section::Ranges => {
                        pending.push(value);

                        if let [low, high, target] = pending[..] {
                            for space in SPACES {
                                if space >= target && space - target <= high.saturating_sub(low) {
                                    codes.push(low + (space - target));
                                }
                            }

                            pending.clear();
                        }
                    }
                    Section::None => {}
                }
            }
            b'[' => {
                if section == Section::Ranges && pending.len() == 2 {
                    in_array = true;
                    pending.push(0);
                }

                position += 1;
            }
            b']' => {
                in_array = false;
                pending.clear();
                position += 1;
            }
            byte if byte.is_ascii_alphabetic() => {
                let end = cmap[position..]
                    .iter()
                    .position(|byte| !byte.is_ascii_alphabetic())
                    .map_or(cmap.len(), |offset| position + offset);

                match &cmap[position..end] {
                    b"beginbfchar" => section = Section::Chars,
                    b"beginbfrange" => section = Section::Ranges,
                    b"endbfchar" | b"endbfrange" => section = Section::None,
                    _ => {}
                }

                pending.clear();
                in_array = false;
                position = end;
            }
            _ => position += 1,
        }
    }

    codes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn to_unicode_space_codes_come_from_chars_ranges_and_arrays() {
        let cmap = b"
            2 beginbfchar
            <0003> <0020>
            <0004> <0041>
            endbfchar
            2 beginbfrange
            <0010> <0030> <0010>
            <0050> <0052> [<0041> <00A0> <0043>]
            endbfrange
        ";

        assert_eq!(space_codes_in_to_unicode(cmap), vec![0x03, 0x20, 0x51]);
    }

    #[test]
    fn composite_widths_read_both_entry_forms() {
        let document = Document::new();
        let widths: Vec<Object> = vec![
            1.into(),
            vec![500.into(), 600.into()].into(),
            10.into(),
            12.into(),
            250.into(),
        ];
        let by_cid = composite_advances(&document, &widths);

        assert_eq!(by_cid[&1], 0.5);
        assert_eq!(by_cid[&2], 0.6);
        assert_eq!(by_cid[&11], 0.25);
        assert_eq!(by_cid.len(), 5);
    }

    #[test]
    fn standard_font_codes_map_through_windows_1252() {
        assert_eq!(windows_1252(0x41), 'A');
        assert_eq!(windows_1252(0x80), '€');
        assert_eq!(windows_1252(0xE9), 'é');
    }
}
