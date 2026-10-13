//! Status row geometry. The spinner occupies one column. Thinking text never enters this row.

use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

pub const SPIN: &[char] = &['|', '/', '-', '\\'];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StatusFit {
    pub spin: String,
    pub label: String,
    pub spin_cols: usize,
    pub label_cols: usize,
}

pub fn clip_cols(text: &str, cols: usize) -> String {
    let mut out = String::new();
    let mut used = 0usize;
    for ch in text.chars() {
        let w = UnicodeWidthChar::width(ch).unwrap_or(0);
        if used + w > cols {
            break;
        }
        out.push(ch);
        used += w;
    }
    out
}

/// Fit a one-column spinner and a label into `width` terminal columns.
pub fn fit_status(width: usize, frame: char, label: &str) -> StatusFit {
    let spin = frame.to_string();
    let spin_cols = UnicodeWidthStr::width(spin.as_str()).min(width);
    let gap = if width > spin_cols { 1 } else { 0 };
    let room = width.saturating_sub(spin_cols.saturating_add(gap));
    let label = clip_cols(label, room);
    let label_cols = UnicodeWidthStr::width(label.as_str());
    StatusFit { spin, label, spin_cols, label_cols }
}

pub fn status_overflow(width: usize, fit: &StatusFit) -> bool {
    let gap = if fit.label_cols == 0 { 0 } else { 1 };
    fit.spin_cols.saturating_add(gap).saturating_add(fit.label_cols) > width
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spinner_stays_one_column_and_row_never_overflows() {
        for width in [1usize, 2, 8, 20, 40, 80, 120] {
            for frame in SPIN {
                let fit = fit_status(width, *frame, "working tool_name thinking tokens that used to share this row");
                if width >= 1 {
                    assert_eq!(fit.spin_cols, 1, "frame {frame} width {width}");
                }
                assert!(!status_overflow(width, &fit), "width {width} fit {fit:?}");
            }
        }
    }

    #[test]
    fn thinking_text_is_not_a_status_input() {
        let fit = fit_status(80, '|', "working");
        assert_eq!(fit.label, "working");
        assert_eq!(fit.spin, "|");
    }
}
