//! Application state and the web desk layout: header, session bar, nav, chat, composer, inspection drawer.

use crate::theme::*;
use crate::widgets::{self, Kind};
use eframe::egui::{self, vec2, Align, Color32, CornerRadius, Layout, Margin, RichText, ScrollArea, Sense, Stroke, StrokeKind};

pub const MODELS: &[(&str, &str)] = &[
    ("mimo 2.6 pro", "Xiaomi MiMo 2.6 Pro (Daily Driver)"),
    ("mimo 2.6 flash", "Xiaomi MiMo 2.6 Flash"),
    ("sonnet 5.5", "Claude Sonnet 5.5"),
    ("claude-3-7-sonnet", "Claude 3.7 Sonnet"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("qwen-3.8-27b:free", "Qwen 3.8 27B (Free)"),
    ("deepseek-chat:free", "DeepSeek Chat (Free)"),
    ("llama-3.3-70b-instruct:free", "Llama 3.3 70B (Free)"),
    ("opus 5.5", "Claude Opus 5.5"),
    ("sol 6.1 pro", "GPT-6.1 Sol Pro"),
    ("glm 5.3 flash", "GLM 5.3 Flash"),
    ("deepseek 4.1 flash", "DeepSeek 4.1 Flash"),
    ("free", "Free Forge Routing"),
    ("local", "Offline Local (Ollama)"),
];
pub const NAV: &[(&str, &str)] = &[
    ("chat", "Chat Mode"),
    ("agent", "Agent ReAct Loop"),
    ("swarm", "3-Headed Swarm"),
    ("computer", "Computer Use"),
    ("terminal", "Terminal Sandbox"),
];
const DRAWER_TABS: &[(&str, &str)] = &[("screen", "Live Screen"), ("term", "Terminal Logs"), ("diff", "Diffs & Code"), ("agent", "Agent Runner")];

#[derive(Clone)]
pub struct Msg {
    pub user: bool,
    pub header: String,
    pub text: String,
    pub actions: Vec<String>,
}

pub struct Session {
    pub id: u32,
    pub name: String,
    pub messages: Vec<Msg>,
}

pub struct HydraApp {
    pub sessions: Vec<Session>,
    pub active: usize,
    counter: u32,
    pub nav: &'static str,
    pub drawer_tab: &'static str,
    pub model: usize,
    pub input: String,
    pub terminal: String,
    pub palette_open: bool,
    shot: Option<ShotPlan>,
}

struct ShotPlan {
    path: String,
    frames: u32,
    requested: bool,
}

impl HydraApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        apply(&cc.egui_ctx);
        let shot = std::env::var("HYDRA_SHOT").ok().map(|path| ShotPlan { path, frames: 0, requested: false });
        let first = Session {
            id: 1,
            name: "Session 1".into(),
            messages: vec![Msg { user: false, header: "HYDRA ORCHESTRATOR".into(), text: "Ask anything, pick a model, and send.".into(), actions: vec![] }],
        };
        Self {
            sessions: vec![first],
            active: 0,
            counter: 1,
            nav: "chat",
            drawer_tab: "screen",
            model: 0,
            input: String::new(),
            terminal: "> Hydra Terminal Sandbox ready.".into(),
            palette_open: false,
            shot,
        }
    }

    fn new_session(&mut self) {
        self.counter += 1;
        let name = format!("Session {}", self.counter);
        let text = format!("Session {} primed. Dispatch instruction or summon heads.", self.counter);
        self.sessions.push(Session { id: self.counter, name, messages: vec![Msg { user: false, header: "HYDRA ORCHESTRATOR".into(), text, actions: vec![] }] });
        self.active = self.sessions.len() - 1;
    }

    fn drawer_width(&self, total: f32) -> f32 {
        (total * 0.34).min(420.0)
    }

    // ---------------------------------------------------------------- header
    fn header(&mut self, ui: &mut egui::Ui, time: f64) {
        ui.spacing_mut().item_spacing = vec2(12.0, 0.0);
        let rect = ui.max_rect();
        ui.scope_builder(egui::UiBuilder::new().max_rect(rect).layout(Layout::left_to_right(Align::Center)), |ui| {
            // brand
            let (rect, _) = ui.allocate_exact_size(vec2(28.0, 28.0), Sense::hover());
            paint_logo(ui, rect);
            ui.label(rtb("HYDRA DESKTOP", TYPE_BRAND, ACCENT));
            let (rect, _) = ui.allocate_exact_size(vec2(66.0, 22.0), Sense::hover());
            widgets::glow(ui, rect, 11, ACCENT_DIM, 8);
            ui.painter().rect_filled(rect, cr(11), ACCENT_DIM);
            ui.painter().rect_stroke(rect, cr(11), Stroke::new(1.0, ACCENT), StrokeKind::Inside);
            ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, "v1.2.2", egui::FontId::new(TYPE_BADGE, egui::FontFamily::Name(BOLD.into())), ACCENT);

        });
        ui.scope_builder(egui::UiBuilder::new().max_rect(rect).layout(Layout::right_to_left(Align::Center)), |ui| {
            {
                ui.spacing_mut().item_spacing.x = 12.0;
                if widgets::button(ui, "⌘K Palette", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                    self.palette_open = true;
                }
                if widgets::button(ui, "Clear", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                    self.sessions[self.active].messages.clear();
                }
                self.model_select(ui);
                widgets::chip_parts(ui, (12.0, 6.0), 8.0, &[
                    widgets::Part::Dot(false),
                    widgets::Part::Text("Gateway : 7777".into(), TYPE_STATUS_PILL, false, TEXT2),
                ], time);
                widgets::chip_parts(ui, (14.0, 6.0), 8.0, &[
                    widgets::Part::Text("Tokens:".into(), TYPE_TOKEN_TALLY, false, TEXT2),
                    widgets::Part::Text("0".into(), TYPE_TOKEN_TALLY, true, TEXT),
                    widgets::Part::Text("•".into(), TYPE_TOKEN_TALLY, false, BORDER),
                    widgets::Part::Text("Cost:".into(), TYPE_TOKEN_TALLY, false, TEXT2),
                    widgets::Part::Text("$0.0000".into(), TYPE_TOKEN_TALLY, true, ACCENT),
                ], time);
            }
        });
    }

    fn model_select(&mut self, ui: &mut egui::Ui) {
        let label = MODELS[self.model].1;
        ui.scope(|ui| {
            ui.spacing_mut().button_padding = vec2(14.0, 8.0);
            let v = &mut ui.style_mut().visuals;
            for w in [&mut v.widgets.inactive, &mut v.widgets.hovered, &mut v.widgets.active, &mut v.widgets.open] {
                w.weak_bg_fill = BG3;
                w.bg_fill = BG3;
                w.bg_stroke = Stroke::new(1.0, BORDER);
                w.corner_radius = cr(12);
            }
            v.widgets.hovered.bg_stroke = Stroke::new(1.0, ACCENT);
            egui::ComboBox::from_id_salt("model-select")
                .width(300.0)
                .selected_text(rt(label, 13.0, TEXT))
                .show_ui(ui, |ui| {
                    for (i, (_, name)) in MODELS.iter().enumerate() {
                        ui.selectable_value(&mut self.model, i, rt(*name, 13.0, TEXT));
                    }
                });
        });
    }

    // ---------------------------------------------------------------- session bar
    fn session_bar(&mut self, ui: &mut egui::Ui) {
        ui.spacing_mut().item_spacing = vec2(8.0, 0.0);
        let mut close: Option<usize> = None;
        let mut select: Option<usize> = None;
        let mut add = false;
        ui.horizontal_centered(|ui| {
            for (i, s) in self.sessions.iter().enumerate() {
                let active = i == self.active;
                let weight = if active { rtb(&s.name, TYPE_SESSION_TAB, ACCENT) } else { rt(&s.name, TYPE_SESSION_TAB, MUTED) };
                let x = rt("×", 14.0, if active { ACCENT } else { MUTED });
                let galley_w = ui.fonts_mut(|f| f.layout_no_wrap(s.name.clone(), egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT).size().x);
                let size = vec2(14.0 * 2.0 + galley_w + 8.0 + 8.0, 29.0);
                let (rect, resp) = ui.allocate_exact_size(size, Sense::click());
                let hover = resp.hovered();
                if active {
                    widgets::shadow(ui, rect, 12, SHADOW_TAB_ACTIVE_GLOW);
                }
                ui.painter().rect_filled(rect, cr(12), if active { ACCENT_DIM } else { BG2 });
                ui.painter().rect_stroke(
                    rect,
                    cr(12),
                    Stroke::new(1.0, if active { ACCENT } else if hover { ACCENT_BORDER_HOVER } else { BORDER }),
                    StrokeKind::Inside,
                );
                let mut name_pos = rect.left_center() + vec2(14.0, 0.0);
                name_pos.y -= 8.0;
                ui.painter().text(rect.left_center() + vec2(14.0, 0.0), egui::Align2::LEFT_CENTER, &s.name, egui::FontId::new(TYPE_SESSION_TAB, if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional }), if active { ACCENT } else if hover { TEXT } else { MUTED });
                let xr = egui::Rect::from_center_size(rect.right_center() - vec2(14.0 + 4.0, 0.0), vec2(14.0, 20.0));
                let xhover = ui.rect_contains_pointer(xr);
                ui.painter().text(xr.center(), egui::Align2::CENTER_CENTER, "×", egui::FontId::new(14.0, egui::FontFamily::Proportional), if xhover { ERROR } else if active { ACCENT } else { MUTED });
                let _ = (weight, x);
                if resp.clicked() {
                    if xhover { close = Some(i) } else { select = Some(i) }
                }
            }
            // + New Session
            let gw = ui.fonts_mut(|f| f.layout_no_wrap("+ New Session".into(), egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT).size().x);
            let (rect, resp) = ui.allocate_exact_size(vec2(gw + 24.0 + 2.0, 29.0), Sense::click());
            if resp.hovered() {
                ui.painter().rect_filled(rect, cr(12), ACCENT_DIM);
                ui.painter().rect_stroke(rect, cr(12), Stroke::new(1.0, ACCENT), StrokeKind::Inside);
            } else {
                widgets::dashed_rounded(ui, rect.shrink(0.5), 12.0, Stroke::new(1.0, ACCENT_BORDER_DASHED));
            }
            ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, "+ New Session", egui::FontId::new(12.0, egui::FontFamily::Proportional), ACCENT);
            if resp.clicked() {
                add = true;
            }
        });
        if let Some(i) = select {
            self.active = i;
        }
        if let Some(i) = close {
            if self.sessions.len() > 1 {
                self.sessions.remove(i);
                self.active = self.active.min(self.sessions.len() - 1);
            }
        }
        if add {
            self.new_session();
        }
    }

    // ---------------------------------------------------------------- nav
    fn nav(&mut self, ui: &mut egui::Ui) {
        ui.spacing_mut().item_spacing = vec2(0.0, 6.0);
        for (key, label) in NAV {
            let active = self.nav == *key;
            let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 35.5), Sense::click());
            let hover = resp.hovered();
            let off = if hover && !active { 3.0 } else { 0.0 };
            let rect = rect.translate(vec2(off, 0.0));
            if active {
                ui.painter().rect_filled(rect, cr(12), ACCENT_DIM);
                ui.painter().rect_filled(egui::Rect::from_min_size(rect.min + vec2(0.0, 6.0), vec2(3.0, rect.height() - 12.0)), cr(1), ACCENT);
            } else if hover {
                ui.painter().rect_filled(rect, cr(12), BG3);
            }
            let family = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
            ui.painter().text(
                rect.left_center() + vec2(14.0, 0.0),
                egui::Align2::LEFT_CENTER,
                label,
                egui::FontId::new(TYPE_NAV_ITEM, family),
                if active { ACCENT } else if hover { TEXT } else { MUTED },
            );
            if resp.clicked() {
                self.nav = key;
                self.terminal.push_str(&format!("\n[MODE] Switched to mode: {key}"));
                if *key == "agent" {
                    self.drawer_tab = "agent";
                }
            }
        }
    }

    // ---------------------------------------------------------------- chat
    fn chat(&mut self, ui: &mut egui::Ui) {
        let max_w = (ui.available_width() - 48.0).min(760.0).max(160.0);
        ScrollArea::vertical().auto_shrink([false, false]).stick_to_bottom(true).show(ui, |ui| {
            ui.spacing_mut().item_spacing = vec2(0.0, 20.0);
            egui::Frame::new().inner_margin(Margin::same(24)).show(ui, |ui| {
                ui.set_width(ui.available_width());
                let msgs = self.sessions[self.active].messages.clone();
                for m in msgs {
                    let layout = if m.user { Layout::top_down(Align::Max) } else { Layout::top_down(Align::Min) };
                    ui.with_layout(layout, |ui| {
                        ui.spacing_mut().item_spacing = vec2(0.0, 8.0);
                        ui.label(rt(m.header.to_uppercase(), TYPE_MSG_HEADER, MUTED).extra_letter_spacing(0.3));
                        let (fill, stroke, radius) = if m.user {
                            (USER_BUBBLE, Stroke::new(1.0, USER_BUBBLE_BORDER), CornerRadius { nw: 16, ne: 16, sw: 16, se: 4 })
                        } else {
                            (BG2, Stroke::new(1.0, BORDER), CornerRadius { nw: 16, ne: 16, sw: 4, se: 16 })
                        };
                        let frame = egui::Frame::new().fill(fill).stroke(stroke).corner_radius(radius).inner_margin(Margin::symmetric(18, 14));
                        let resp = frame.show(ui, |ui| {
                            ui.set_max_width(max_w - 36.0);
                            ui.add(egui::Label::new(rt(&m.text, TYPE_MESSAGE_BODY, TEXT)).wrap());
                        });
                        let r = resp.response.rect;
                        let _ = r;
                        for a in &m.actions {
                            ui.label(rt(a, 12.0, Color32::from_rgb(0xd7, 0xe0, 0xda)));
                        }
                    });
                }
            });
        });
    }

    // ---------------------------------------------------------------- composer
    fn composer(&mut self, ui: &mut egui::Ui) {
        ui.spacing_mut().item_spacing.x = 12.0;
        ui.horizontal(|ui| {
            let send_w = 62.0;
            let width = ui.available_width() - send_w - 12.0;
            let mut te_rect = egui::Rect::NOTHING;
            ui.scope(|ui| {
                let focused_id = egui::Id::new("prompt");
                let focused = ui.memory(|m| m.has_focus(focused_id));
                let frame = egui::Frame::new().fill(BG3).corner_radius(cr(16)).inner_margin(Margin::symmetric(16, 12))
                    .stroke(Stroke::new(1.0, if focused { ACCENT } else { BORDER }));
                let resp = frame.show(ui, |ui| {
                    ui.set_width(width - 32.0 - 2.0);
                    ui.add(
                        egui::TextEdit::multiline(&mut self.input)
                            .id(focused_id)
                            .hint_text(rt("Summon frontier model, instruct agent, or issue bash command...", TYPE_TEXTAREA, MUTED))
                            .font(egui::FontId::new(TYPE_TEXTAREA, egui::FontFamily::Proportional))
                            .desired_rows(2)
                            .frame(egui::Frame::NONE)
                            .desired_width(f32::INFINITY),
                    );
                    ui.set_min_height(56.0 - 24.0 - 2.0);
                });
                te_rect = resp.response.rect;
                if focused {
                    widgets::shadow(ui, te_rect, 16, SHADOW_GLOW);
                }
            });
            ui.with_layout(Layout::bottom_up(Align::Max), |ui| {
                widgets::button(ui, "Send", Kind::Primary, 13.0, (16.0, 8.0), None);
            });
        });
    }

    // ---------------------------------------------------------------- drawer
    fn drawer(&mut self, ui: &mut egui::Ui, width: f32) {
        // header
        egui::Frame::new().inner_margin(Margin::symmetric(20, 14)).show(ui, |ui| {
            ui.set_width(width - 40.0 - 1.0);
            ui.horizontal(|ui| {
                ui.label(rtb("INSPECTION & AUTOMATION", TYPE_DRAWER_HEADER, TEXT2).extra_letter_spacing(0.3));
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    ui.label(rt("X: - | Y: -", TYPE_COORD_READOUT, MUTED));
                });
            });
        });
        let y = ui.cursor().top();
        ui.painter().hline(ui.max_rect().x_range(), y, Stroke::new(1.0, BORDER));
        // tabs strip
        let strip = egui::Frame::new().fill(BG).inner_margin(Margin::symmetric(8, 4));
        strip.show(ui, |ui| {
            ui.set_width(width - 16.0 - 1.0);
            ui.spacing_mut().item_spacing = vec2(4.0, 0.0);
            let cell = (width - 16.0 - 1.0 - 12.0) / 4.0;
            ui.horizontal(|ui| {
                for (key, label) in DRAWER_TABS {
                    let active = self.drawer_tab == *key;
                    let wrapped = {
                        let w = ui.fonts_mut(|f| f.layout_no_wrap(label.to_string(), egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT).size().x);
                        w + 12.0 > cell
                    };
                    let text = if wrapped { label.replacen(' ', "\n", 1) } else { label.to_string() };
                    let h: f32 = if wrapped { 17.0 * 2.0 + 16.0 } else { 17.0 + 16.0 };
                    let (rect, resp) = ui.allocate_exact_size(vec2(cell, h.max(33.0)), Sense::click());
                    let hover = resp.hovered();
                    if active {
                        widgets::shadow(ui, rect, 10, SHADOW_DRAWER_TAB_GLOW);
                        ui.painter().rect_filled(rect, cr(10), ACCENT_DIM);
                    } else if hover {
                        ui.painter().rect_filled(rect, cr(10), BG3);
                    }
                    let fam = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
                    ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, text, egui::FontId::new(TYPE_DRAWER_TAB, fam), if active { ACCENT } else if hover { TEXT } else { MUTED });
                    if resp.clicked() {
                        self.drawer_tab = key;
                    }
                }
            });
        });
        let y = ui.cursor().top();
        ui.painter().hline(ui.max_rect().x_range(), y, Stroke::new(1.0, BORDER));
        // body
        egui::Frame::new().inner_margin(Margin::same(16)).show(ui, |ui| {
            ui.set_width(width - 32.0 - 1.0);
            ui.spacing_mut().item_spacing = vec2(8.0, 14.0);
            match self.drawer_tab {
                "screen" => self.screen_tab(ui, width - 33.0),
                "term" => {
                    egui::Frame::new().fill(BG).stroke(Stroke::new(1.0, TERMINAL_BORDER)).corner_radius(cr(16)).inner_margin(Margin::same(16)).show(ui, |ui| {
                        ui.set_min_height(ui.available_height().max(200.0) - 40.0);
                        ui.set_width(ui.available_width());
                        ui.label(RichText::new(&self.terminal).size(TYPE_TERMINAL).color(ACCENT));
                    });
                }
                "diff" => {
                    ui.label(rt("No file changes recorded in this session yet.", 11.0, MUTED));
                }
                _ => {
                    ui.label(rtb("AUTONOMOUS AGENT RUNNER", 13.0, TEXT));
                }
            }
        });
    }

    fn screen_tab(&mut self, ui: &mut egui::Ui, w: f32) {
        let h = w * 9.0 / 16.0;
        let (rect, _) = ui.allocate_exact_size(vec2(w, h), Sense::hover());
        widgets::shadow(ui, rect, 16, SHADOW_SCREEN);
        ui.painter().rect_filled(rect, cr(16), BG);
        ui.painter().rect_stroke(rect, cr(16), Stroke::new(1.0, BORDER), StrokeKind::Inside);
        ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, "Desktop Screen Preview", egui::FontId::new(12.0, egui::FontFamily::Proportional), MUTED);
        let cell = (w - 16.0) / 3.0;
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 8.0;
            for t in ["Capture Screen", "Toggle Bounds", "Type Text"] {
                widgets::button(ui, t, Kind::Secondary, 13.0, (16.0, 8.0), Some(cell));
            }
        });
        ui.label(rt("Active Window: Detecting...", 11.0, TEXT2));
    }
}

fn paint_logo(ui: &egui::Ui, rect: egui::Rect) {
    let s = rect.width() / 24.0;
    let p = |x: f32, y: f32| rect.min + vec2(x * s, y * s);
    widgets::glow(ui, rect.shrink(4.0), 4, accent_a(0.5), 8);
    for pts in [
        vec![p(12., 2.), p(2., 7.), p(12., 12.), p(22., 7.)],
        vec![p(2., 17.), p(12., 22.), p(22., 17.)],
        vec![p(2., 12.), p(12., 17.), p(22., 12.)],
    ] {
        ui.painter().add(egui::Shape::convex_polygon(pts, ACCENT, Stroke::NONE));
    }
}

impl eframe::App for HydraApp {
    fn ui(&mut self, root: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = root.ctx().clone();
        let ctx = &ctx;
        let time = ctx.input(|i| i.time);
        ctx.request_repaint_after(std::time::Duration::from_millis(33));
        let total = ctx.content_rect().width();
        let border = Stroke::new(1.0, BORDER);

        egui::Panel::top("header")
            .exact_size(LAYOUT_HEADER_HEIGHT)
            .frame(egui::Frame::new().fill(Color32::from_rgb(8, 8, 8)).inner_margin(Margin::symmetric(24, 0)))
            .show(root, |ui| {
                self.header(ui, time);
                ui.painter().hline(ui.max_rect().x_range(), ui.max_rect().bottom() - 0.5, border);
            });
        egui::Panel::top("sessions")
            .exact_size(LAYOUT_SESSION_BAR_HEIGHT)
            .frame(egui::Frame::new().fill(BG).inner_margin(Margin::symmetric(20, 0)))
            .show(root, |ui| {
                self.session_bar(ui);
                ui.painter().hline(ui.max_rect().x_range(), ui.max_rect().bottom() - 0.5, border);
            });
        egui::Panel::left("nav")
            .exact_size(LAYOUT_SIDEBAR_WIDTH)
            .resizable(false)
            .show_separator_line(false)
            .frame(egui::Frame::new().fill(BG2).inner_margin(Margin::symmetric(10, 16)))
            .show(root, |ui| {
                self.nav(ui);
                let r = ui.max_rect();
                ui.painter().vline(r.right() + 10.0 - 0.5, r.y_range(), border);
            });
        let dw = self.drawer_width(total);
        egui::Panel::right("drawer")
            .exact_size(dw)
            .resizable(false)
            .show_separator_line(false)
            .frame(egui::Frame::new().fill(BG2))
            .show(root, |ui| {
                ui.painter().vline(ui.max_rect().left() + 0.5, ui.max_rect().y_range(), border);
                self.drawer(ui, dw);
            });
        egui::Panel::bottom("composer")
            .frame(egui::Frame::new().fill(Color32::from_rgb(8, 8, 8)).inner_margin(Margin::symmetric(24, 16)))
            .show(root, |ui| {
                ui.painter().hline(ui.max_rect().x_range().expand(24.0), ui.max_rect().top() - 15.5, border);
                self.composer(ui);
            });
        egui::CentralPanel::default().frame(egui::Frame::new().fill(BG)).show(root, |ui| {
            self.chat(ui);
        });

        // screenshot hook for the box: HYDRA_SHOT=/path.png renders a few frames, saves, exits
        if let Some(plan) = &mut self.shot {
            plan.frames += 1;
            if plan.frames == 20 && !plan.requested {
                plan.requested = true;
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
            }
            let image = ctx.input(|i| {
                i.events.iter().find_map(|e| if let egui::Event::Screenshot { image, .. } = e { Some(image.clone()) } else { None })
            });
            if let Some(img) = image {
                let (w, h) = (img.width() as u32, img.height() as u32);
                let raw: Vec<u8> = img.pixels.iter().flat_map(|c| c.to_array()).collect();
                if let Some(buf) = image::RgbaImage::from_raw(w, h, raw) {
                    let _ = buf.save(&plan.path);
                }
                ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_drive_the_theme() {
        assert_eq!(ACCENT, Color32::from_rgb(0, 255, 102));
        assert_eq!(BG, Color32::from_rgb(0, 0, 0));
        assert_eq!(BG2, Color32::from_rgb(8, 8, 8));
        assert_eq!(LAYOUT_SIDEBAR_WIDTH, 220.0);
        assert_eq!(LAYOUT_HEADER_HEIGHT, 60.0);
        assert_eq!(TYPE_MESSAGE_BODY, 14.0);
        assert!(!SHADOW_GLOW.is_empty());
    }

    #[test]
    fn nav_and_models_match_the_old_desk() {
        let labels: Vec<_> = NAV.iter().map(|n| n.1).collect();
        assert_eq!(labels, ["Chat Mode", "Agent ReAct Loop", "3-Headed Swarm", "Computer Use", "Terminal Sandbox"]);
        assert_eq!(MODELS.len(), 14);
    }
}
