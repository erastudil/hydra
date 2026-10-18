//! Motion: the page's cubic-bezier easing and duration tokens, applied through egui's animate_* so nothing blocks the UI thread.

use crate::theme::{EASING_APPLE, MOTION_FADE_IN_MS, MOTION_PULSE_MS, MOTION_TRANSITION_MS};
use eframe::egui::{Color32, Context, Id};

/// CSS cubic-bezier(x1, y1, x2, y2) evaluated at progress t in [0, 1]. Bisection on x, then y.
pub fn cubic_bezier(p: [f32; 4], t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    if t == 0.0 || t == 1.0 {
        return t;
    }
    let axis = |a: f32, b: f32, s: f32| 3.0 * a * s * (1.0 - s) * (1.0 - s) + 3.0 * b * s * s * (1.0 - s) + s * s * s;
    let (mut lo, mut hi) = (0.0f32, 1.0f32);
    let mut s = t;
    for _ in 0..24 {
        s = 0.5 * (lo + hi);
        if axis(p[0], p[2], s) < t {
            lo = s;
        } else {
            hi = s;
        }
    }
    axis(p[1], p[3], s)
}

/// The page's --easing-apple.
pub fn apple(t: f32) -> f32 {
    cubic_bezier(EASING_APPLE, t)
}

/// True when the user or the OS asks for less motion. HYDRA_NO_ANIM=1 forces it on, =0 forces it off.
pub fn reduced_motion() -> bool {
    match std::env::var("HYDRA_NO_ANIM") {
        Ok(v) => !matches!(v.trim().to_ascii_lowercase().as_str(), "" | "0" | "false" | "no"),
        Err(_) => system_reduced_motion(),
    }
}

/// The phosphorPulse keyframe at `time` seconds: 1 at its 0%/100% frame, 0 at its 50% frame.
/// Reduced motion skips keyframes (tokens: motion.reduced_motion), so the dot rests at its 100% frame.
pub fn pulse_phase(time: f64, reduced: bool) -> f32 {
    if reduced {
        return 1.0;
    }
    let period = MOTION_PULSE_MS as f64 / 1000.0;
    ((time * std::f64::consts::TAU / period).cos() as f32 * 0.5 + 0.5).clamp(0.0, 1.0)
}

/// `pulse_phase` with the user's motion setting.
pub fn pulse(time: f64) -> f32 {
    pulse_phase(time, reduced_motion())
}

#[cfg(windows)]
fn system_reduced_motion() -> bool {
    #[link(name = "user32")]
    extern "system" {
        fn SystemParametersInfoW(action: u32, param: u32, pv: *mut i32, win_ini: u32) -> i32;
    }
    const SPI_GETCLIENTAREAANIMATION: u32 = 0x1042;
    let mut enabled: i32 = 1;
    // SAFETY: the call writes one BOOL into a valid local and reads nothing else.
    let ok = unsafe { SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, &mut enabled, 0) };
    ok != 0 && enabled == 0
}

#[cfg(not(windows))]
fn system_reduced_motion() -> bool {
    false
}

/// Transition seconds for the page's `transition: all 0.25s`.
pub fn transition_secs() -> f32 {
    MOTION_TRANSITION_MS / 1000.0
}

/// Install the motion settings on the context. Reduced motion turns every animate_* into a jump.
pub fn install(ctx: &Context) {
    let secs = if reduced_motion() { 0.0 } else { transition_secs() };
    ctx.all_styles_mut(|s| s.animation_time = secs);
}

/// Eased 0..1 progress toward `on`. Repaints itself while moving and costs nothing when settled.
pub fn toggle(ctx: &Context, id: Id, on: bool) -> f32 {
    ctx.animate_bool_with_easing(id, on, apple)
}

/// Linear blend of two colors.
pub fn mix(a: Color32, b: Color32, t: f32) -> Color32 {
    let l = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round().clamp(0.0, 255.0) as u8;
    Color32::from_rgba_unmultiplied(l(a.r(), b.r()), l(a.g(), b.g()), l(a.b(), b.b()), l(a.a(), b.a()))
}

/// Message arrival progress: eased 0..1 over the fadeIn duration, given the birth time. Settled when `born < 0`.
pub fn arrival(now: f64, born: f64) -> f32 {
    if born < 0.0 || reduced_motion() {
        return 1.0;
    }
    apple(((now - born) as f32 * 1000.0 / MOTION_FADE_IN_MS).clamp(0.0, 1.0))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bezier_hits_endpoints_and_is_monotonic() {
        assert_eq!(cubic_bezier(EASING_APPLE, 0.0), 0.0);
        assert_eq!(cubic_bezier(EASING_APPLE, 1.0), 1.0);
        let mut last = 0.0;
        for i in 1..=100 {
            let v = cubic_bezier(EASING_APPLE, i as f32 / 100.0);
            assert!(v >= last - 1e-5, "not monotonic at {i}");
            last = v;
        }
    }

    #[test]
    fn apple_curve_is_fast_out() {
        // cubic-bezier(0.16, 1, 0.3, 1) is most of the way there in the first quarter
        assert!(apple(0.25) > 0.7);
        assert!(apple(0.5) > 0.95);
    }

    #[test]
    fn linear_bezier_is_identity() {
        for i in 0..=10 {
            let t = i as f32 / 10.0;
            assert!((cubic_bezier([0.0, 0.0, 1.0, 1.0], t) - t).abs() < 1e-3);
        }
    }

    #[test]
    fn durations_come_from_tokens() {
        assert_eq!(transition_secs(), 0.25);
        assert_eq!(MOTION_FADE_IN_MS, 250.0);
    }

    #[test]
    fn arrival_progress_and_reduced_motion() {
        std::env::set_var("HYDRA_NO_ANIM", "0");
        assert_eq!(arrival(10.0, -1.0), 1.0);
        assert_eq!(arrival(10.0, 10.0), 0.0);
        assert!(arrival(10.1, 10.0) > 0.0 && arrival(10.1, 10.0) < 1.0);
        assert_eq!(arrival(11.0, 10.0), 1.0);
        std::env::set_var("HYDRA_NO_ANIM", "1");
        assert!(reduced_motion());
        assert_eq!(arrival(10.0, 10.0), 1.0);
        std::env::set_var("HYDRA_NO_ANIM", "0");
        assert!(!reduced_motion());
    }

    #[test]
    fn the_status_pulse_follows_its_keyframe_and_rests_under_reduced_motion() {
        assert_eq!(MOTION_PULSE_MS, 2000.0, "phosphorPulse duration from the tokens");
        let period = MOTION_PULSE_MS as f64 / 1000.0;
        assert_eq!(pulse_phase(0.0, false), 1.0);
        assert!(pulse_phase(period / 2.0, false) < 0.001, "50% frame");
        assert!((pulse_phase(period, false) - 1.0).abs() < 0.001, "loops");
        for t in [0.0, 0.3, period / 2.0, 1.7, 1234.5] {
            assert_eq!(pulse_phase(t, true), 1.0, "reduced motion: no keyframes, the dot holds still at t={t}");
        }
    }

    #[test]
    fn mix_blends_channels() {
        let c = mix(Color32::from_rgb(0, 0, 0), Color32::from_rgb(200, 100, 50), 0.5);
        assert_eq!((c.r(), c.g(), c.b()), (100, 50, 25));
    }
}
