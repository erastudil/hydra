//! Window breakpoints, from the responsive block of the design tokens.

use crate::theme::*;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Frame {
    /// Header collapses to the two-line brand and short labels.
    pub narrow: bool,
    /// Left nav becomes a slide-over instead of a column.
    pub overlay_nav: bool,
    pub drawer_width: f32,
    pub header_height: f32,
}

/// Layout for a window `total` points wide.
pub fn frame(total: f32) -> Frame {
    let narrow = total < RESP_HEADER_NARROW_BELOW;
    Frame {
        narrow,
        overlay_nav: total < RESP_SIDEBAR_OVERLAY_BELOW,
        drawer_width: (total * RESP_DRAWER_PCT).clamp(RESP_DRAWER_MIN, RESP_DRAWER_MAX),
        header_height: if narrow { 73.0 } else { LAYOUT_HEADER_HEIGHT },
    }
}

/// Width of the transcript column left after the fixed panels.
pub fn transcript_width(total: f32, nav_visible: f32) -> f32 {
    total - nav_visible - frame(total).drawer_width
}
