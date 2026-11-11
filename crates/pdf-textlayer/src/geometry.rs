//! Rectangles and matrices in the two coordinate systems the writer uses.
//!
//! *Display space* is what OCR reports: origin at the top-left of the page as
//! shown on screen (after `/Rotate`), y down. *Page space* is the same
//! top-left convention before `/Rotate` is applied. *PDF space* is the
//! content-stream system: origin at the bottom-left, y up.

/// An OCR box covers a native word when the overlap is at least half of the
/// smaller rectangle. Less than that is a glancing touch, not the same word.
pub(crate) const MIN_NATIVE_WORD_COVERAGE: f64 = 0.5;

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct Rect {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

impl Rect {
    pub fn width(&self) -> f64 {
        self.x1 - self.x0
    }

    pub fn height(&self) -> f64 {
        self.y1 - self.y0
    }

    pub fn union(&self, other: &Rect) -> Rect {
        Rect {
            x0: self.x0.min(other.x0),
            y0: self.y0.min(other.y0),
            x1: self.x1.max(other.x1),
            y1: self.y1.max(other.y1),
        }
    }

    /// Bounding box of four points.
    pub fn around(points: [(f64, f64); 4]) -> Rect {
        let mut rect = Rect {
            x0: points[0].0,
            y0: points[0].1,
            x1: points[0].0,
            y1: points[0].1,
        };

        for (x, y) in &points[1..] {
            rect.x0 = rect.x0.min(*x);
            rect.y0 = rect.y0.min(*y);
            rect.x1 = rect.x1.max(*x);
            rect.y1 = rect.y1.max(*y);
        }

        rect
    }
}

/// A PDF transformation matrix `[a b c d e f]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct Matrix {
    pub a: f64,
    pub b: f64,
    pub c: f64,
    pub d: f64,
    pub e: f64,
    pub f: f64,
}

impl Matrix {
    pub const IDENTITY: Matrix = Matrix {
        a: 1.0,
        b: 0.0,
        c: 0.0,
        d: 1.0,
        e: 0.0,
        f: 0.0,
    };

    pub fn translation(x: f64, y: f64) -> Matrix {
        Matrix {
            e: x,
            f: y,
            ..Matrix::IDENTITY
        }
    }

    /// `self` applied first, then `next`.
    pub fn then(&self, next: &Matrix) -> Matrix {
        Matrix {
            a: self.a * next.a + self.b * next.c,
            b: self.a * next.b + self.b * next.d,
            c: self.c * next.a + self.d * next.c,
            d: self.c * next.b + self.d * next.d,
            e: self.e * next.a + self.f * next.c + next.e,
            f: self.e * next.b + self.f * next.d + next.f,
        }
    }

    pub fn apply(&self, x: f64, y: f64) -> (f64, f64) {
        (x * self.a + y * self.c + self.e, x * self.b + y * self.d + self.f)
    }

    pub fn apply_rect(&self, rect: &Rect) -> Rect {
        Rect::around([
            self.apply(rect.x0, rect.y0),
            self.apply(rect.x1, rect.y0),
            self.apply(rect.x1, rect.y1),
            self.apply(rect.x0, rect.y1),
        ])
    }

    /// Linear scale factor: the side of a unit square after the transform.
    pub fn scale(&self) -> f64 {
        (self.a * self.d - self.b * self.c).abs().sqrt()
    }
}

/// Map a display-space rectangle onto the unrotated page.
///
/// `page_width` and `page_height` are the unrotated page size. `rotation` is
/// the page `/Rotate` value, normalized to 0, 90, 180, or 270.
pub(crate) fn display_to_page(rect: &Rect, rotation: i64, page_width: f64, page_height: f64) -> Rect {
    match rotation {
        90 => Rect {
            x0: rect.y0,
            y0: page_height - rect.x1,
            x1: rect.y1,
            y1: page_height - rect.x0,
        },
        180 => Rect {
            x0: page_width - rect.x1,
            y0: page_height - rect.y1,
            x1: page_width - rect.x0,
            y1: page_height - rect.y0,
        },
        270 => Rect {
            x0: page_width - rect.y1,
            y0: rect.x0,
            x1: page_width - rect.y0,
            y1: rect.x1,
        },
        _ => *rect,
    }
}

/// Snap an angle in degrees to 0, 90, 180, or 270.
///
/// Halfway values round to the even quarter turn, as Python's `round` does.
pub(crate) fn snap_to_right_angle(degrees: f64) -> i64 {
    if !degrees.is_finite() {
        return 0;
    }

    let quarter_turns = (degrees / 90.0).round_ties_even() as i64;

    (quarter_turns * 90).rem_euclid(360)
}

/// Whether `rect` covers enough of a native word to be that same word.
pub(crate) fn covers_native_word(rect: &Rect, native_words: &[Rect]) -> bool {
    let area = rect.width() * rect.height();
    if area <= 0.0 {
        return false;
    }

    for word in native_words {
        let overlap_width = rect.x1.min(word.x1) - rect.x0.max(word.x0);
        if overlap_width <= 0.0 {
            continue;
        }

        let overlap_height = rect.y1.min(word.y1) - rect.y0.max(word.y0);
        if overlap_height <= 0.0 {
            continue;
        }

        let word_area = word.width() * word.height();
        if word_area <= 0.0 {
            continue;
        }

        let coverage = overlap_width * overlap_height / area.min(word_area);
        if coverage >= MIN_NATIVE_WORD_COVERAGE {
            return true;
        }
    }

    false
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect(x0: f64, y0: f64, x1: f64, y1: f64) -> Rect {
        Rect { x0, y0, x1, y1 }
    }

    #[test]
    fn display_box_on_rotated_page_maps_to_unrotated_space() {
        // A 792x612 page shown rotated by 270.
        let display = rect(138.0, 307.0, 138.0 + 389.0, 307.0 + 11.0);

        assert_eq!(
            display_to_page(&display, 270, 792.0, 612.0),
            rect(474.0, 138.0, 485.0, 527.0)
        );
    }

    #[test]
    fn rotating_four_times_returns_to_the_start() {
        let display = rect(10.0, 20.0, 110.0, 32.0);
        let once = display_to_page(&display, 90, 792.0, 612.0);

        assert_eq!(once, rect(20.0, 502.0, 32.0, 602.0));
        assert_eq!(display_to_page(&display, 0, 612.0, 792.0), display);
        assert_eq!(
            display_to_page(&display, 180, 612.0, 792.0),
            rect(502.0, 760.0, 602.0, 772.0)
        );
    }

    #[test]
    fn angles_snap_to_the_nearest_right_angle() {
        assert_eq!(snap_to_right_angle(0.0), 0);
        assert_eq!(snap_to_right_angle(44.0), 0);
        assert_eq!(snap_to_right_angle(45.0), 0);
        assert_eq!(snap_to_right_angle(46.0), 90);
        assert_eq!(snap_to_right_angle(135.0), 180);
        assert_eq!(snap_to_right_angle(-90.0), 270);
        assert_eq!(snap_to_right_angle(359.0), 0);
        assert_eq!(snap_to_right_angle(f64::NAN), 0);
    }

    #[test]
    fn half_coverage_of_the_smaller_box_counts_as_the_native_word() {
        let word = [rect(0.0, 0.0, 10.0, 10.0)];

        assert!(covers_native_word(&rect(5.0, 0.0, 15.0, 10.0), &word));
        assert!(!covers_native_word(&rect(5.1, 0.0, 15.1, 10.0), &word));
        assert!(covers_native_word(&rect(-100.0, -5.0, 100.0, 15.0), &word));
    }

    #[test]
    fn zero_area_box_does_not_count_as_a_native_word() {
        let word = [rect(0.0, 0.0, 10.0, 10.0)];

        assert!(!covers_native_word(&rect(2.0, 2.0, 2.0, 8.0), &word));
    }

    #[test]
    fn matrices_compose_left_to_right() {
        let scale = Matrix {
            a: 2.0,
            d: 2.0,
            ..Matrix::IDENTITY
        };
        let moved = scale.then(&Matrix::translation(10.0, 5.0));

        assert_eq!(moved.apply(1.0, 1.0), (12.0, 7.0));
        assert_eq!(moved.scale(), 2.0);
    }
}
