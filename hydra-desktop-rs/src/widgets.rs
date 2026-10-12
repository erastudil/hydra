//! Painted widgets that reproduce the page's buttons, pills, glows and dashed borders.

use crate::motion;
use crate::theme::*;
use eframe::egui::{self, epaint::Shadow, pos2, vec2, Align2, Color32, FontFamily, Pos2, Rect, Response, Sense, Shape, Stroke, StrokeKind, Ui};

#[derive(Clone, Copy, PartialEq)]
pub enum Kind {
    Primary,
    Secondary,
    Danger,
    Ghost,
}

/// Paint one or more token shadow layers (outer only; inset shadows are drawn by the caller).
pub fn shadow(ui: &Ui, rect: Rect, radius: u8, layers: &[ShadowSpec]) {
    for l in layers.iter().filter(|l| !l.inset) {
        let s = Shadow { offset: [l.x, l.y], blur: l.blur, spread: 0, color: l.color };
        ui.painter().add(s.as_shape(rect, cr(radius)));
    }
}

/// Token shadow layers as one shape, for painting into a slot reserved behind content that is laid out first.
pub fn shadow_shape(rect: Rect, radius: u8, layers: &[ShadowSpec]) -> Shape {
    Shape::Vec(layers.iter().filter(|l| !l.inset).map(|l| Shadow { offset: [l.x, l.y], blur: l.blur, spread: 0, color: l.color }.as_shape(rect, cr(radius)).into()).collect())
}

pub fn glow(ui: &Ui, rect: Rect, radius: u8, color: Color32, blur: u8) {
    let shadow = Shadow { offset: [0, 0], blur, spread: 0, color };
    ui.painter().add(shadow.as_shape(rect, cr(radius)));
}

/// Polyline around a rounded rectangle, for dashed borders.
pub fn rounded_outline(rect: Rect, r: f32) -> Vec<Pos2> {
    let mut pts = Vec::new();
    let steps = 6;
    let corners = [
        (rect.right() - r, rect.top() + r, -90.0f32),
        (rect.right() - r, rect.bottom() - r, 0.0),
        (rect.left() + r, rect.bottom() - r, 90.0),
        (rect.left() + r, rect.top() + r, 180.0),
    ];
    for (cx, cy, start) in corners {
        for i in 0..=steps {
            let a = (start + 90.0 * i as f32 / steps as f32).to_radians();
            pts.push(pos2(cx + r * a.cos(), cy + r * a.sin()));
        }
    }
    pts.push(pts[0]);
    pts
}

/// Button as the page draws it. `wrap` is the cell width: text breaks to fit and stays centered.
pub fn button(ui: &mut Ui, text: &str, kind: Kind, size: f32, pad: (f32, f32), wrap: Option<f32>) -> Response {
    let fam = FontFamily::Name(BOLD.into());
    // the font has no command-key glyph, so it is painted as a shape and the label is shifted right
    let (cmd_glyph, text) = match text.strip_prefix('⌘') {
        Some(rest) => (true, format!("     {rest}")),
        None => (false, text.to_owned()),
    };
    let text = text.as_str();
    let color = match kind {
        Kind::Primary => Color32::BLACK,
        Kind::Danger => ERROR,
        _ => TEXT,
    };
    let wrap_w = wrap.map(|w| (w - pad.0 * 2.0).max(10.0)).unwrap_or(f32::INFINITY);
    let mut job = egui::text::LayoutJob::single_section(
        text.to_owned(),
        egui::TextFormat { font_id: egui::FontId::new(size, fam), color, extra_letter_spacing: -0.2, ..Default::default() },
    );
    job.wrap.max_width = wrap_w;
    job.halign = egui::Align::Center;
    let galley = ui.painter().layout_job(job);
    let desired = vec2(wrap.unwrap_or(galley.size().x + pad.0 * 2.0), galley.size().y + pad.1 * 2.0);
    let (rect, resp) = ui.allocate_exact_size(desired, Sense::click());
    let t = motion::toggle(ui.ctx(), resp.id.with("hv"), resp.hovered());
    let lift = match kind {
        Kind::Primary => 2.0,
        Kind::Secondary | Kind::Danger => 1.0,
        Kind::Ghost => 0.0,
    };
    let rect = rect.translate(vec2(0.0, -lift * t));
    let (fill, stroke, tcolor) = match kind {
        Kind::Primary => (motion::mix(ACCENT, ACCENT2, t), Stroke::NONE, Color32::BLACK),
        Kind::Secondary => (
            motion::mix(BG3, ELEV, t),
            Stroke::new(1.0, motion::mix(BORDER, accent_a(0.4), t)),
            motion::mix(TEXT, ACCENT, t),
        ),
        Kind::Danger => (motion::mix(BG3, ELEV, t), Stroke::new(1.0, motion::mix(BORDER, ERROR, t)), ERROR),
        Kind::Ghost => (Color32::TRANSPARENT, Stroke::NONE, TEXT),
    };
    if kind == Kind::Primary {
        glow(ui, rect, 12, accent_a(0.35 + 0.20 * t), (12.0 + 8.0 * t).round() as u8);
    }
    ui.painter().rect_filled(rect, cr(12), fill);
    if stroke.width > 0.0 {
        ui.painter().rect_stroke(rect, cr(12), stroke, StrokeKind::Inside);
    }
    let gw = galley.size().x;
    let pos = pos2(rect.center().x, rect.center().y - galley.size().y / 2.0);
    ui.painter().galley(pos, galley, tcolor);
    let _ = Align2::CENTER_CENTER;
    if cmd_glyph {
        command_glyph(ui, egui::Rect::from_center_size(pos2(rect.center().x - gw / 2.0 + 7.0, rect.center().y), vec2(11.0, 11.0)), tcolor);
    }
    resp.on_hover_cursor(egui::CursorIcon::PointingHand)
}

/// Bordered chip like .status-pill and the token tally.
pub fn chip<R>(ui: &mut Ui, pad: (f32, f32), add: impl FnOnce(&mut Ui) -> R) -> R {
    egui::Frame::new()
        .fill(BG3)
        .stroke(Stroke::new(1.0, BORDER))
        .corner_radius(cr(12))
        .inner_margin(egui::Margin::symmetric(pad.0 as i8, pad.1 as i8))
        .show(ui, |ui| {
            ui.spacing_mut().item_spacing.x = 8.0;
            ui.with_layout(egui::Layout::left_to_right(egui::Align::Center), add).inner
        })
        .inner
}

/// Pulsing 8px phosphor dot.
pub fn status_dot(ui: &mut Ui, live: bool, time: f64) {
    let (rect, _) = ui.allocate_exact_size(vec2(8.0, 8.0), Sense::hover());
    let phase = ((time * std::f64::consts::PI).cos() as f32 * 0.5 + 0.5).clamp(0.0, 1.0); // 2s cycle
    let a = 0.6 + 0.4 * phase;
    let c = if live { ACCENT } else { Color32::from_rgb(0x4b, 0x55, 0x63) };
    if live {
        glow(ui, rect.expand(1.0), 4, Color32::from_rgba_unmultiplied(0, 255, 102, (110.0 * a) as u8), 10);
    }
    let r = 4.0 * (0.9 + 0.1 * phase);
    ui.painter().circle_filled(rect.center(), r, c.gamma_multiply(if live { a } else { 1.0 }));
}

pub fn dashed_rounded(ui: &Ui, rect: Rect, r: f32, stroke: Stroke) {
    let pts = rounded_outline(rect, r);
    ui.painter().extend(Shape::dashed_line(&pts, stroke, 4.0, 3.0));
}


pub enum Part {
    Text(String, f32, bool, Color32),
    Dot(bool),
}

/// Bordered chip made of text runs and an optional status dot; measured first so it lays out the same in any direction.
pub fn chip_parts(ui: &mut Ui, pad: (f32, f32), gap: f32, parts: &[Part], time: f64) {
    let mut widths = Vec::new();
    for p in parts {
        widths.push(match p {
            Part::Dot(_) => 8.0,
            Part::Text(t, size, bold, c) => {
                let fam = if *bold { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
                let ls = LETTER_SPACING_BODY * t.chars().count() as f32;
                ui.fonts_mut(|f| f.layout_no_wrap(t.clone(), egui::FontId::new(*size, fam), *c).size().x) + ls
            }
        });
    }
    let inner: f32 = widths.iter().sum::<f32>() + gap * (parts.len() as f32 - 1.0);
    let size = vec2(inner + pad.0 * 2.0 + 2.0, 17.0 + pad.1 * 2.0 + 2.0);
    let (rect, _) = ui.allocate_exact_size(size, Sense::hover());
    ui.painter().rect_filled(rect, cr(12), BG3);
    ui.painter().rect_stroke(rect, cr(12), Stroke::new(1.0, BORDER), egui::StrokeKind::Inside);
    let mut x = rect.left() + 1.0 + pad.0;
    for (p, w) in parts.iter().zip(widths) {
        match p {
            Part::Dot(live) => {
                let r = egui::Rect::from_center_size(egui::pos2(x + 4.0, rect.center().y), vec2(8.0, 8.0));
                let phase = ((time * std::f64::consts::PI).cos() as f32 * 0.5 + 0.5).clamp(0.0, 1.0);
                if *live {
                    glow(ui, r.expand(1.0), 4, Color32::from_rgba_unmultiplied(0, 255, 102, (110.0 * (0.6 + 0.4 * phase)) as u8), 10);
                }
                ui.painter().circle_filled(r.center(), 4.0, if *live { ACCENT } else { Color32::from_rgb(0x4b, 0x55, 0x63) });
            }
            Part::Text(t, size, bold, c) => {
                let fam = if *bold { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
                ui.painter().text(egui::pos2(x, rect.center().y), egui::Align2::LEFT_CENTER, t, egui::FontId::new(*size, fam), *c);
            }
        }
        x += w + gap;
    }
}

/// The command-key symbol: a square with a small loop at each corner.
pub fn command_glyph(ui: &Ui, r: Rect, color: Color32) {
    let stroke = Stroke::new(1.2, color);
    let c = r.shrink(3.2);
    let k = 1.8;
    ui.painter().rect_stroke(c, 0, stroke, StrokeKind::Middle);
    for p in [c.left_top(), c.right_top(), c.left_bottom(), c.right_bottom()] {
        ui.painter().circle_stroke(p + vec2(if p.x < c.center().x { -k * 0.6 } else { k * 0.6 }, if p.y < c.center().y { -k * 0.6 } else { k * 0.6 }), k, stroke);
    }
}
