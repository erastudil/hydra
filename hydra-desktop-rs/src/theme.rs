//! Tokens copied from the :root block of the retired web desk page, plus font and visuals setup.

use eframe::egui::{self, Color32, CornerRadius, FontData, FontDefinitions, FontFamily, FontId, RichText, Stroke};

include!(concat!(env!("OUT_DIR"), "/tokens.rs"));

pub const BG: Color32 = BG_PRIMARY;
pub const BG2: Color32 = BG_SECONDARY;
pub const BG3: Color32 = BG_TERTIARY;
pub const ELEV: Color32 = BG_ELEVATED;
pub const TEXT: Color32 = TEXT_PRIMARY;
pub const TEXT2: Color32 = TEXT_SECONDARY;
pub const MUTED: Color32 = TEXT_MUTED;
pub const ACCENT2: Color32 = ACCENT_SECONDARY;
pub const DIFF_ADD: Color32 = DIFF_ADD_BG;
pub const DIFF_DEL: Color32 = DIFF_DEL_BG;
pub const USER_BUBBLE: Color32 = USER_BUBBLE_BG;

/// rgba(0, 255, 102, a) over any background.
pub fn accent_a(a: f32) -> Color32 {
    Color32::from_rgba_unmultiplied(0, 255, 102, (a * 255.0).round() as u8)
}
/// --accent-dim
pub fn accent_dim() -> Color32 {
    ACCENT_DIM
}

/// Amber for "needs you" markers; not part of the retired page's tokens.
pub const WARN: Color32 = Color32::from_rgb(255, 196, 64);

pub const BOLD: &str = "mono_bold";

pub fn cr(r: u8) -> CornerRadius {
    CornerRadius::same(r)
}

/// Body text. The page sets letter-spacing: -0.2px on everything.
pub fn rt(text: impl Into<String>, size: f32, color: Color32) -> RichText {
    RichText::new(text).size(size).color(color).extra_letter_spacing(-0.2)
}
pub fn rtb(text: impl Into<String>, size: f32, color: Color32) -> RichText {
    rt(text, size, color).family(FontFamily::Name(BOLD.into()))
}

pub fn install_fonts(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();
    fonts.font_data.insert(
        "cascadia".into(),
        std::sync::Arc::new(FontData::from_static(include_bytes!("../assets/fonts/CascadiaCode-Regular.ttf"))),
    );
    fonts.font_data.insert(
        "cascadia_bold".into(),
        std::sync::Arc::new(FontData::from_static(include_bytes!("../assets/fonts/CascadiaCode-Bold.ttf"))),
    );
    for fam in [FontFamily::Proportional, FontFamily::Monospace] {
        fonts.families.entry(fam).or_default().insert(0, "cascadia".into());
    }
    fonts.families.insert(FontFamily::Name(BOLD.into()), vec!["cascadia_bold".into(), "cascadia".into()]);
    ctx.set_fonts(fonts);
}

pub fn apply(ctx: &egui::Context) {
    install_fonts(ctx);
    ctx.set_pixels_per_point(ctx.pixels_per_point());
    let mut v = egui::Visuals::dark();
    v.panel_fill = BG;
    v.window_fill = BG2;
    v.extreme_bg_color = BG3;
    v.override_text_color = Some(TEXT);
    v.selection.bg_fill = accent_a(0.25);
    v.selection.stroke = Stroke::new(1.0, ACCENT);
    v.window_corner_radius = cr(20);
    v.widgets.noninteractive.bg_stroke = Stroke::new(1.0, BORDER);
    v.widgets.inactive.bg_fill = BG3;
    v.widgets.inactive.weak_bg_fill = BG3;
    v.widgets.inactive.bg_stroke = Stroke::new(1.0, BORDER);
    v.widgets.inactive.corner_radius = cr(12);
    v.widgets.hovered.bg_fill = ELEV;
    v.widgets.hovered.weak_bg_fill = ELEV;
    v.widgets.hovered.bg_stroke = Stroke::new(1.0, accent_a(0.4));
    v.widgets.hovered.corner_radius = cr(12);
    v.widgets.active.bg_fill = ELEV;
    v.widgets.active.weak_bg_fill = ELEV;
    v.widgets.active.bg_stroke = Stroke::new(1.0, ACCENT);
    v.widgets.active.corner_radius = cr(12);
    v.widgets.open.bg_fill = BG3;
    v.widgets.open.weak_bg_fill = BG3;
    v.widgets.open.bg_stroke = Stroke::new(1.0, ACCENT);
    v.widgets.open.corner_radius = cr(12);
    ctx.set_visuals(v);
    ctx.all_styles_mut(|s| {
        s.spacing.item_spacing = egui::vec2(8.0, 8.0);
        s.spacing.button_padding = egui::vec2(16.0, 8.0);
        s.spacing.scroll.bar_width = 8.0;
        s.spacing.scroll.floating = false;
        s.text_styles.insert(egui::TextStyle::Body, FontId::new(13.0, FontFamily::Proportional));
        s.text_styles.insert(egui::TextStyle::Button, FontId::new(13.0, FontFamily::Proportional));
        s.text_styles.insert(egui::TextStyle::Monospace, FontId::new(13.0, FontFamily::Monospace));
    });
}

pub fn window_icon() -> Option<egui::IconData> {
    let bytes = include_bytes!("../assets/hydra_desktop.png");
    let img = image::load_from_memory(bytes).ok()?.into_rgba8();
    let (width, height) = img.dimensions();
    Some(egui::IconData { rgba: img.into_raw(), width, height })
}
