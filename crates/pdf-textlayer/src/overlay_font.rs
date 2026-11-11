//! The overlay font: a composite font whose glyphs are plain rectangles.
//!
//! Invisible text needs a font only so that viewers can map character codes
//! back to Unicode, advance the pen, and know how big each character is. The
//! font built here has one rectangular glyph per advance width in use and is
//! a few kilobytes at most. Character codes are UTF-16 code units, so one
//! font covers every script in the Basic Multilingual Plane. Tesseract and
//! OCRmyPDF write their text layers with a font of the same kind.
//!
//! The glyphs are rectangles rather than empty because PDFium takes a
//! character's size from its outline. With empty glyphs it sees a run with
//! no height, and drops the run altogether when the text is vertical.

use std::collections::BTreeMap;

use lopdf::{Dictionary, Document, Object, ObjectId, Stream, dictionary};

/// Ascender and descender in 1/1000 em. These match the metrics PyMuPDF's
/// Helvetica reported, so extracted word boxes keep the height they had with
/// the 0.2 writer.
pub(crate) const ASCENDER: i16 = 1075;
pub(crate) const DESCENDER: i16 = -299;

/// Height of each rectangle above the baseline: Helvetica's cap height.
/// PDFium groups runs into lines by the outlines' extent, so the rectangles
/// are as tall as a capital letter, not as tall as the line.
const GLYPH_HEIGHT: i16 = 718;

const FONT_NAME: &str = "PdfTextLayer";
const UNITS_PER_EM: u16 = 1000;

/// The `/pdftextlayer` font of one document.
///
/// Object ids are reserved up front so pages can reference the font while
/// the set of used characters is still growing. [`OverlayFont::finish`]
/// writes the objects once every page is done.
pub(crate) struct OverlayFont {
    pub id: ObjectId,
    default_advance: u16,
    /// Advance of every character drawn so far, in 1/1000 em.
    advances: BTreeMap<u16, u16>,
}

impl OverlayFont {
    pub fn reserve(document: &mut Document, default_advance: u16) -> OverlayFont {
        OverlayFont {
            id: document.new_object_id(),
            default_advance,
            advances: BTreeMap::new(),
        }
    }

    pub fn record(&mut self, code_unit: u16, advance: u16) {
        if advance != self.default_advance {
            self.advances.insert(code_unit, advance);
        }
    }

    pub fn finish(self, document: &mut Document) {
        // One glyph per distinct advance. Glyph 0 is the required `.notdef`.
        let mut glyph_advances: Vec<u16> = self.advances.values().copied().collect();
        glyph_advances.push(self.default_advance);
        glyph_advances.sort_unstable();
        glyph_advances.dedup();

        let glyph_for = |advance: u16| -> u16 {
            let index = glyph_advances.binary_search(&advance).unwrap_or(0);

            (index + 1) as u16
        };

        let mut cid_to_gid = glyph_for(self.default_advance).to_be_bytes().repeat(0x10000);

        for (&code_unit, &advance) in &self.advances {
            let offset = usize::from(code_unit) * 2;
            cid_to_gid[offset..offset + 2].copy_from_slice(&glyph_for(advance).to_be_bytes());
        }

        let font_file = document.add_object(compressed(dictionary! {}, rectangle_truetype(&glyph_advances)));
        let descriptor = document.add_object(dictionary! {
            "Type" => "FontDescriptor",
            "FontName" => Object::Name(FONT_NAME.into()),
            // Symbolic: the glyphs are not letters of any standard set.
            "Flags" => 4,
            "FontBBox" => vec![
                0.into(),
                i64::from(DESCENDER).into(),
                i64::from(glyph_advances.last().copied().unwrap_or(1000)).into(),
                i64::from(ASCENDER).into(),
            ],
            "ItalicAngle" => 0,
            "Ascent" => i64::from(ASCENDER),
            "Descent" => i64::from(DESCENDER),
            "CapHeight" => i64::from(GLYPH_HEIGHT),
            "StemV" => 80,
            "FontFile2" => font_file,
        });
        let cid_to_gid = document.add_object(compressed(dictionary! {}, cid_to_gid));
        let descendant = document.add_object(dictionary! {
            "Type" => "Font",
            "Subtype" => "CIDFontType2",
            "BaseFont" => Object::Name(FONT_NAME.into()),
            "CIDSystemInfo" => dictionary! {
                "Registry" => Object::string_literal("Adobe"),
                "Ordering" => Object::string_literal("Identity"),
                "Supplement" => 0,
            },
            "FontDescriptor" => descriptor,
            "CIDToGIDMap" => cid_to_gid,
            "DW" => i64::from(self.default_advance),
            "W" => self.widths_array(),
        });
        let to_unicode = document.add_object(compressed(dictionary! {}, to_unicode_cmap()));

        document.set_object(
            self.id,
            dictionary! {
                "Type" => "Font",
                "Subtype" => "Type0",
                "BaseFont" => Object::Name(FONT_NAME.into()),
                "Encoding" => "Identity-H",
                "DescendantFonts" => vec![descendant.into()],
                "ToUnicode" => to_unicode,
            },
        );
    }

    /// The `/W` array: `first [w w w]` for each run of consecutive codes.
    fn widths_array(&self) -> Object {
        let mut array: Vec<Object> = Vec::new();
        let mut run_start: Option<u16> = None;
        let mut run: Vec<Object> = Vec::new();
        let mut previous: Option<u16> = None;

        for (&code_unit, &advance) in &self.advances {
            let continues_run = previous.is_some_and(|code| code.checked_add(1) == Some(code_unit));

            if !continues_run {
                if let Some(start) = run_start.take() {
                    array.push(i64::from(start).into());
                    array.push(std::mem::take(&mut run).into());
                }

                run_start = Some(code_unit);
            }

            run.push(i64::from(advance).into());
            previous = Some(code_unit);
        }

        if let Some(start) = run_start {
            array.push(i64::from(start).into());
            array.push(run.into());
        }

        array.into()
    }
}

fn compressed(dictionary: Dictionary, content: Vec<u8>) -> Stream {
    let mut stream = Stream::new(dictionary, content);
    // Compression only fails to help on tiny inputs; lopdf then keeps the
    // plain content, which is still a valid stream.
    let _ = stream.compress();

    stream
}

/// Map every two-byte code to the UTF-16 code unit of the same value.
///
/// A `bfrange` may not cross a change of the high byte, so there is one
/// range per high byte, in blocks of at most 100 as the format requires.
fn to_unicode_cmap() -> Vec<u8> {
    let mut cmap = String::from(
        "/CIDInit /ProcSet findresource begin\n\
         12 dict begin\n\
         begincmap\n\
         /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n\
         /CMapName /Adobe-Identity-UCS def\n\
         /CMapType 2 def\n\
         1 begincodespacerange\n\
         <0000> <FFFF>\n\
         endcodespacerange\n",
    );
    let high_bytes: Vec<u32> = (0..=0xFF).collect();

    for block in high_bytes.chunks(100) {
        cmap.push_str(&format!("{} beginbfrange\n", block.len()));

        for high in block {
            cmap.push_str(&format!("<{high:02X}00> <{high:02X}FF> <{high:02X}00>\n"));
        }

        cmap.push_str("endbfrange\n");
    }

    cmap.push_str(
        "endcmap\n\
         CMapName currentdict /CMap defineresource pop\n\
         end\n\
         end\n",
    );

    cmap.into_bytes()
}

/// A TrueType font with one rectangular glyph per entry of `advances`.
///
/// Glyph `i + 1` is a rectangle `advances[i]` wide that stands on the
/// baseline and is [`GLYPH_HEIGHT`] tall. Glyph 0 is empty.
fn rectangle_truetype(advances: &[u16]) -> Vec<u8> {
    let glyph_count = advances.len() as u16 + 1;
    let widest = advances.iter().copied().max().unwrap_or(UNITS_PER_EM);

    let mut glyf = Vec::new();
    // Short offsets are stored divided by two. `.notdef` has no outline, so
    // its entry and the next are both zero.
    let mut loca = be16s(&[0, 0]);
    // advanceWidth and leftSideBearing for each glyph.
    let mut hmtx = be16s(&[UNITS_PER_EM, 0]);

    for &advance in advances {
        let width = advance as i16;

        glyf.extend(1i16.to_be_bytes()); // numberOfContours
        glyf.extend(0i16.to_be_bytes()); // xMin
        glyf.extend(0i16.to_be_bytes()); // yMin
        glyf.extend(width.to_be_bytes()); // xMax
        glyf.extend(GLYPH_HEIGHT.to_be_bytes()); // yMax
        glyf.extend(be16(3)); // endPtsOfContours: four points
        glyf.extend(be16(0)); // instructionLength
        glyf.extend([1u8; 4]); // flags: on the curve, two-byte deltas
        // x deltas, then y deltas: up the left side, across, and down.
        for delta in [0, 0, width, 0, 0, GLYPH_HEIGHT, 0, -GLYPH_HEIGHT] {
            glyf.extend(delta.to_be_bytes());
        }

        loca.extend(be16((glyf.len() / 2) as u16));
        hmtx.extend(be16s(&[advance, 0]));
    }

    let mut head = Vec::new();
    head.extend(be32(0x0001_0000)); // version
    head.extend(be32(0x0001_0000)); // fontRevision
    head.extend(be32(0)); // checkSumAdjustment, filled in below
    head.extend(be32(0x5F0F_3CF5)); // magicNumber
    head.extend(be16(0x000B)); // flags
    head.extend(be16(UNITS_PER_EM));
    head.extend([0u8; 16]); // created, modified
    head.extend(0i16.to_be_bytes()); // xMin
    head.extend(0i16.to_be_bytes()); // yMin
    head.extend(be16(widest)); // xMax
    head.extend(GLYPH_HEIGHT.to_be_bytes()); // yMax
    head.extend(be16(0)); // macStyle
    head.extend(be16(8)); // lowestRecPPEM
    head.extend(be16(2)); // fontDirectionHint
    head.extend(be16(0)); // indexToLocFormat: short
    head.extend(be16(0)); // glyphDataFormat

    let mut hhea = Vec::new();
    hhea.extend(be32(0x0001_0000));
    hhea.extend(ASCENDER.to_be_bytes());
    hhea.extend(DESCENDER.to_be_bytes());
    hhea.extend(be16(0)); // lineGap
    hhea.extend(be16(widest.max(UNITS_PER_EM))); // advanceWidthMax
    hhea.extend(be16s(&[0, 0, widest])); // minLeftSideBearing, minRightSideBearing, xMaxExtent
    hhea.extend(be16s(&[1, 0, 0])); // caretSlopeRise, caretSlopeRun, caretOffset
    hhea.extend(be16s(&[0, 0, 0, 0])); // reserved
    hhea.extend(be16(0)); // metricDataFormat
    hhea.extend(be16(glyph_count)); // numberOfHMetrics

    let mut maxp = Vec::new();
    maxp.extend(be32(0x0001_0000));
    maxp.extend(be16(glyph_count));
    // maxPoints, maxContours, maxCompositePoints, maxCompositeContours,
    // maxZones, maxTwilightPoints, maxStorage, maxFunctionDefs,
    // maxInstructionDefs, maxStackElements, maxSizeOfInstructions,
    // maxComponentElements, maxComponentDepth
    maxp.extend(be16s(&[4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0]));

    let mut post = Vec::new();
    post.extend(be32(0x0003_0000)); // format 3: no glyph names
    post.extend(be32(0)); // italicAngle
    post.extend((-100i16).to_be_bytes()); // underlinePosition
    post.extend(be16(50)); // underlineThickness
    post.extend(be32(0)); // isFixedPitch
    post.extend([0u8; 16]); // memory hints

    // An empty naming table: format 0, no records.
    let name = be16s(&[0, 0, 6]);

    let mut os2 = Vec::new();
    os2.extend(be16(0)); // version
    os2.extend(be16(UNITS_PER_EM)); // xAvgCharWidth
    os2.extend(be16(400)); // usWeightClass
    os2.extend(be16(5)); // usWidthClass
    os2.extend(be16(0)); // fsType: installable embedding
    os2.extend([0u8; 20]); // subscript, superscript, strikeout
    os2.extend(be16(0)); // sFamilyClass
    os2.extend([0u8; 10]); // panose
    os2.extend([0u8; 16]); // ulUnicodeRange
    os2.extend(*b"    "); // achVendID
    os2.extend(be16(0x0040)); // fsSelection: regular
    os2.extend(be16(0xFFFF)); // usFirstCharIndex
    os2.extend(be16(0xFFFF)); // usLastCharIndex
    os2.extend(ASCENDER.to_be_bytes()); // sTypoAscender
    os2.extend(DESCENDER.to_be_bytes()); // sTypoDescender
    os2.extend(be16(0)); // sTypoLineGap
    os2.extend(be16(ASCENDER as u16)); // usWinAscent
    os2.extend(be16(DESCENDER.unsigned_abs())); // usWinDescent

    // One format 4 subtable holding only the required final segment.
    let mut cmap = Vec::new();
    cmap.extend(be16s(&[0, 1])); // version, numTables
    cmap.extend(be16s(&[3, 1])); // Windows, Unicode BMP
    cmap.extend(be32(12)); // offset of the subtable
    cmap.extend(be16s(&[4, 24, 0])); // format, length, language
    cmap.extend(be16s(&[2, 2, 0, 0])); // segCountX2, searchRange, entrySelector, rangeShift
    cmap.extend(be16(0xFFFF)); // endCode
    cmap.extend(be16(0)); // reservedPad
    cmap.extend(be16(0xFFFF)); // startCode
    cmap.extend(be16(1)); // idDelta
    cmap.extend(be16(0)); // idRangeOffset

    // The table directory lists tags in ascending order.
    let tables: [(&[u8; 4], Vec<u8>); 10] = [
        (b"OS/2", os2),
        (b"cmap", cmap),
        (b"glyf", glyf),
        (b"head", head),
        (b"hhea", hhea),
        (b"hmtx", hmtx),
        (b"loca", loca),
        (b"maxp", maxp),
        (b"name", name),
        (b"post", post),
    ];

    let table_count = tables.len() as u16;
    let entry_selector = 15 - table_count.leading_zeros() as u16;
    let search_range = (1u16 << entry_selector) * 16;

    let mut font = Vec::new();
    font.extend(be32(0x0001_0000));
    font.extend(be16(table_count));
    font.extend(be16(search_range));
    font.extend(be16(entry_selector));
    font.extend(be16(table_count * 16 - search_range));

    let mut offset = 12 + 16 * tables.len();
    let mut head_offset = 0;
    let mut body = Vec::new();

    for (tag, data) in &tables {
        if *tag == b"head" {
            head_offset = offset;
        }

        font.extend_from_slice(*tag);
        font.extend(be32(table_checksum(data)));
        font.extend(be32(offset as u32));
        font.extend(be32(data.len() as u32));

        body.extend_from_slice(data);

        while body.len() % 4 != 0 {
            body.push(0);
        }

        offset = 12 + 16 * tables.len() + body.len();
    }

    font.extend(body);

    let adjustment = 0xB1B0_AFBAu32.wrapping_sub(table_checksum(&font));
    font[head_offset + 8..head_offset + 12].copy_from_slice(&adjustment.to_be_bytes());

    font
}

fn table_checksum(data: &[u8]) -> u32 {
    data.chunks(4).fold(0u32, |sum, chunk| {
        let mut word = [0u8; 4];
        word[..chunk.len()].copy_from_slice(chunk);

        sum.wrapping_add(u32::from_be_bytes(word))
    })
}

fn be16(value: u16) -> [u8; 2] {
    value.to_be_bytes()
}

fn be32(value: u32) -> [u8; 4] {
    value.to_be_bytes()
}

fn be16s(values: &[u16]) -> Vec<u8> {
    values.iter().flat_map(|value| value.to_be_bytes()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truetype_tables_are_aligned_and_the_file_checksums() {
        let font = rectangle_truetype(&[278, 556, 1000]);

        assert_eq!(&font[0..4], &[0, 1, 0, 0]);
        assert_eq!(font.len() % 4, 0);
        assert!(font.len() < 700, "font is {} bytes", font.len());
        assert_eq!(table_checksum(&font), 0xB1B0_AFBA);
    }

    #[test]
    fn each_advance_gets_a_rectangle_of_that_width() {
        let font = rectangle_truetype(&[278, 556]);
        let table = |tag: &[u8; 4]| -> &[u8] {
            let entry = (0..10)
                .map(|index| &font[12 + index * 16..28 + index * 16])
                .find(|entry| &entry[0..4] == tag)
                .unwrap();
            let offset = u32::from_be_bytes(entry[8..12].try_into().unwrap()) as usize;
            let length = u32::from_be_bytes(entry[12..16].try_into().unwrap()) as usize;

            &font[offset..offset + length]
        };

        // Three glyphs: `.notdef` and one per advance.
        assert_eq!(table(b"maxp")[4..6], [0, 3]);
        assert_eq!(table(b"loca"), be16s(&[0, 0, 17, 34]));
        assert_eq!(table(b"hmtx"), be16s(&[1000, 0, 278, 0, 556, 0]));

        // The second rectangle: xMax is its advance.
        let second = &table(b"glyf")[34..];
        assert_eq!(second[6..8], 556u16.to_be_bytes());
    }

    #[test]
    fn consecutive_codes_share_one_width_run() {
        let mut document = Document::new();
        let mut font = OverlayFont::reserve(&mut document, 556);
        font.record(0x41, 667);
        font.record(0x42, 667);
        font.record(0x61, 556); // the default: not listed
        font.record(0x69, 222);

        let expected: Object = vec![
            0x41.into(),
            vec![667.into(), 667.into()].into(),
            0x69.into(),
            vec![222.into()].into(),
        ]
        .into();

        assert_eq!(font.widths_array(), expected);
    }

    #[test]
    fn to_unicode_has_one_range_per_high_byte() {
        let cmap = String::from_utf8(to_unicode_cmap()).unwrap();

        assert_eq!(cmap.matches("beginbfrange").count(), 3);
        assert!(cmap.contains("<4E00> <4EFF> <4E00>"));
        assert!(cmap.contains("<FF00> <FFFF> <FF00>"));
    }
}
