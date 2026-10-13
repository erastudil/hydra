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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn design_width_matches_the_old_page() {
        let f = frame(1280.0);
        assert!(!f.narrow && !f.overlay_nav);
        assert_eq!(f.drawer_width, 500.0);
        assert_eq!(f.header_height, LAYOUT_HEADER_HEIGHT);
        assert_eq!(transcript_width(1280.0, LAYOUT_SIDEBAR_WIDTH), 560.0);
    }

    #[test]
    fn nine_hundred_is_usable_not_180() {
        let f = frame(900.0);
        assert!(f.narrow && f.overlay_nav);
        assert_eq!(f.header_height, 73.0);
        // the nav slides over, so the transcript keeps everything except the drawer
        assert!(transcript_width(900.0, 0.0) >= 500.0);
        // the old page left 180 here
        assert!(transcript_width(900.0, LAYOUT_SIDEBAR_WIDTH) > 180.0);
    }

    #[test]
    fn drawer_is_clamped() {
        assert_eq!(frame(2000.0).drawer_width, RESP_DRAWER_MAX);
        assert_eq!(frame(RESP_MIN_WINDOW[0]).drawer_width, RESP_DRAWER_MIN.max(RESP_MIN_WINDOW[0] * RESP_DRAWER_PCT));
        assert!(frame(RESP_MIN_WINDOW[0]).drawer_width >= RESP_DRAWER_MIN);
    }

    #[test]
    fn breakpoints_are_exclusive_below() {
        assert!(frame(RESP_HEADER_NARROW_BELOW - 1.0).narrow);
        assert!(!frame(RESP_HEADER_NARROW_BELOW).narrow);
        assert!(frame(RESP_SIDEBAR_OVERLAY_BELOW - 1.0).overlay_nav);
        assert!(!frame(RESP_SIDEBAR_OVERLAY_BELOW).overlay_nav);
    }
}
