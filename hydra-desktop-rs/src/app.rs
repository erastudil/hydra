//! The web desk layout in egui: header, session bar, nav, chat, composer, inspection drawer, modals.

use crate::theme::*;
use crate::widgets::{self, Kind, Part};
use eframe::egui::{self, vec2, Align, Color32, CornerRadius, Key, Layout, Margin, Modifiers, RichText, ScrollArea, Sense, Stroke, StrokeKind};
use hydra_desktop::backend::Backend;
use hydra_desktop::health::{self, Check};
use hydra_desktop::model::Status;
use hydra_desktop::workspace::Work;
use std::collections::HashMap;
use std::path::PathBuf;
use hydra_desktop::model::*;

const DRAWER_TABS: &[(&str, &str)] = &[("screen", "Live Screen"), ("term", "Terminal Logs"), ("diff", "Diffs & Code"), ("agent", "Agent Runner")];

#[derive(Clone, Copy, PartialEq)]
enum Cmd {
    Nav(&'static str),
    Tab(&'static str),
    AgentStart,
    AgentCtl(&'static str),
    Export(bool),
    NewSession,
    Clear,
    Templates,
    Jobs,
}

fn palette_entries() -> Vec<(&'static str, &'static str, Cmd)> {
    vec![
        ("Switch to Chat Mode", "Alt+1", Cmd::Nav("chat")),
        ("Switch to Agent ReAct Loop", "Alt+2", Cmd::Nav("agent")),
        ("Switch to 3-Headed Swarm", "Alt+3", Cmd::Nav("swarm")),
        ("Switch to Computer Use", "", Cmd::Nav("computer")),
        ("Switch to Terminal Sandbox", "", Cmd::Nav("terminal")),
        ("Open Live Screen", "", Cmd::Tab("screen")),
        ("Open Terminal Logs", "", Cmd::Tab("term")),
        ("Open Diffs & Code", "", Cmd::Tab("diff")),
        ("Open Agent Runner", "", Cmd::Tab("agent")),
        ("Start Agent Task", "", Cmd::AgentStart),
        ("Pause Agent Task", "", Cmd::AgentCtl("pause")),
        ("Resume Agent Task", "", Cmd::AgentCtl("resume")),
        ("Abort Agent Task", "", Cmd::AgentCtl("abort")),
        ("Export Session Trace (JSON)", "", Cmd::Export(false)),
        ("Export Session Trace (Markdown)", "", Cmd::Export(true)),
        ("Workflow Templates", "", Cmd::Templates),
        ("Scheduled Jobs", "", Cmd::Jobs),
        ("New Session", "Ctrl+T", Cmd::NewSession),
        ("Clear Chat", "", Cmd::Clear),
    ]
}

pub struct HydraApp {
    m: Model,
    be: Option<Backend>,
    input: String,
    palette: Option<(String, usize)>,
    templates_open: bool,
    templates_q: String,
    jobs_open: bool,
    job_form: (String, String, String, String), // name, interval, profile, task
    agent_task: String,
    agent_steps: u32,
    agent_profile: usize,
    type_text: String,
    bounds: bool,
    tex: Option<(u64, egui::TextureHandle)>,
    last_poll: f64,
    shot: Option<ShotPlan>,
    focus_input: bool,
    composer_rect: egui::Rect,
    health_rx: std::sync::mpsc::Receiver<Vec<Check>>,
    checks: Vec<Check>,
    crash_note: Option<String>,
    panel_dismissed: bool,
    started: f64,
    start_error: Option<String>,
    open_folder: Option<(String, String)>, // (typed path, error)
    rename: Option<(usize, String)>,
    work: Work,
    finder: Option<(String, usize)>,
    rewind_confirm: Option<usize>,
    queue: HashMap<u32, Vec<String>>,
    mention_sel: usize,
    was_busy: bool,
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
        let cwd = std::env::var("HYDRA_CWD").unwrap_or_else(|_| std::env::current_dir().map(|p| p.display().to_string()).unwrap_or_else(|_| ".".into()));
        let state = if std::env::var("HYDRA_NO_STATE").is_ok() { None } else { Some(state_dir().join("state.json")) };
        let mut m = Model::new(state, cwd);
        let wake_ctx = cc.egui_ctx.clone();
        let mut work = Work::new(PathBuf::from(m.work_path()), move || wake_ctx.request_repaint());
        let ctx = cc.egui_ctx.clone();
        let (be, start_error) = match std::env::var("HYDRA_NO_BACKEND") {
            Ok(_) => (None, Some("backend disabled".to_string())),
            Err(_) => match Backend::spawn(move || ctx.request_repaint()) {
                Ok(b) => (Some(b), None),
                Err(e) => (None, Some(e)),
            },
        };
        if let Some(e) = &start_error {
            m.notice = e.clone();
        }
        if let Ok(t) = std::env::var("HYDRA_TAB") {
            if let Some(k) = DRAWER_TABS.iter().chain([("files", ""), ("git", "")].iter()).find(|d| d.0 == t) {
                m.drawer_tab = k.0;
            }
        }
        if let Ok(f) = std::env::var("HYDRA_FILE") {
            work.open_file(&f);
        }
        if let Ok(f) = std::env::var("HYDRA_SELECT") {
            work.select_change(&f, false);
        }
        let palette = if std::env::var("HYDRA_OPEN").as_deref() == Ok("palette") { Some((String::new(), 0)) } else { None };
        Self {
            m,
            be,
            input: String::new(),
            palette,
            templates_open: false,
            templates_q: String::new(),
            jobs_open: false,
            job_form: ("".into(), "60".into(), "full_automation".into(), "".into()),
            agent_task: String::new(),
            agent_steps: 30,
            agent_profile: 0,
            type_text: String::new(),
            bounds: false,
            tex: None,
            last_poll: 0.0,
            shot,
            focus_input: true,
            composer_rect: egui::Rect::NOTHING,
            health_rx: { let (tx, rx) = std::sync::mpsc::channel(); std::thread::spawn(move || { let _ = tx.send(health::run()); }); rx },
            checks: vec![],
            crash_note: hydra_desktop::crash::take_last_crash().map(|c| c.1),
            panel_dismissed: false,
            started: -1.0,
            start_error,
            open_folder: if std::env::var("HYDRA_OPEN").as_deref() == Ok("folder") { Some((std::env::var("HYDRA_CWD").unwrap_or_default(), String::new())) } else { None },
            rename: None,
            work,
            finder: if std::env::var("HYDRA_OPEN").as_deref() == Ok("finder") { Some((String::new(), 0)) } else { None },
            rewind_confirm: None,
            queue: HashMap::new(),
            mention_sel: 0,
            was_busy: false,
        }
    }

    fn retry_backend(&mut self, ctx: &egui::Context) {
        let c = ctx.clone();
        match Backend::spawn(move || c.request_repaint()) {
            Ok(b) => {
                self.be = Some(b);
                self.start_error = None;
                self.m.notice.clear();
            }
            Err(e) => {
                self.be = None;
                self.start_error = Some(e.clone());
                self.m.notice = e;
            }
        }
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = tx.send(health::run());
        });
        self.health_rx = rx;
        self.started = -1.0;
        self.panel_dismissed = false;
    }

    fn run(&mut self, cmd: Cmd) {
        match cmd {
            Cmd::Nav(k) => self.m.set_nav(k),
            Cmd::Tab(t) => self.m.drawer_tab = t,
            Cmd::AgentStart => self.start_agent(),
            Cmd::AgentCtl(w) => {
                if let Some(be) = &mut self.be {
                    self.m.agent_ctl(be, w);
                }
            }
            Cmd::Export(md) => {
                if let Some(be) = &mut self.be {
                    self.m.export(be, md);
                }
            }
            Cmd::NewSession => self.m.new_session(),
            Cmd::Clear => self.m.clear_active(),
            Cmd::Templates => {
                self.templates_open = true;
                if let Some(be) = &mut self.be {
                    self.m.load_templates(be);
                }
            }
            Cmd::Jobs => {
                self.jobs_open = true;
                if let Some(be) = &mut self.be {
                    self.m.load_jobs(be);
                }
            }
        }
    }

    fn start_agent(&mut self) {
        let task = if self.agent_task.trim().is_empty() { self.input.clone() } else { self.agent_task.clone() };
        if task.trim().is_empty() {
            self.m.term.push_str("\n[AGENT] enter a task first");
            return;
        }
        if let Some(be) = &mut self.be {
            self.m.agent_start(be, &task, self.agent_steps, PROFILES[self.agent_profile]);
        }
    }

    fn send(&mut self) {
        let text = self.input.clone();
        if self.m.active_busy() && !text.trim().is_empty() && !matches!(self.m.nav, "terminal" | "swarm") {
            let sid = self.m.sessions[self.m.active].id;
            self.queue.entry(sid).or_default().push(text.trim().to_string());
            self.input.clear();
            return;
        }
        let Some(be) = &mut self.be else {
            self.m.notice = "agent process is not running".into();
            return;
        };
        if self.m.submit(be, &text) {
            self.input.clear();
        }
    }

    fn drain(&mut self) {
        let mut evs = vec![];
        if let Some(be) = &self.be {
            while let Ok(e) = be.rx.try_recv() {
                evs.push(e);
            }
        }
        for e in evs {
            self.m.apply(e);
        }
        if self.m.notice == "jobs-changed" {
            self.m.notice.clear();
            if let Some(be) = &mut self.be {
                self.m.load_jobs(be);
            }
        }
    }

    // ---------------------------------------------------------------- header
    fn header(&mut self, ui: &mut egui::Ui, time: f64, narrow: bool) {
        ui.spacing_mut().item_spacing = vec2(12.0, 0.0);
        let rect = ui.max_rect();
        ui.scope_builder(egui::UiBuilder::new().max_rect(rect).layout(Layout::left_to_right(Align::Center)), |ui| {
            let (r, _) = ui.allocate_exact_size(vec2(28.0, 28.0), Sense::hover());
            paint_logo(ui, r);
            if narrow {
                ui.vertical(|ui| {
                    ui.spacing_mut().item_spacing.y = 0.0;
                    ui.label(rtb("HYDRA", TYPE_BRAND, ACCENT));
                    ui.label(rtb("DESKTOP", TYPE_BRAND, ACCENT));
                });
            } else {
                ui.label(rtb("HYDRA DESKTOP", TYPE_BRAND, ACCENT));
            }
            let (r, _) = ui.allocate_exact_size(vec2(66.0, 22.0), Sense::hover());
            widgets::glow(ui, r, 11, ACCENT_DIM, 8);
            ui.painter().rect_filled(r, cr(11), ACCENT_DIM);
            ui.painter().rect_stroke(r, cr(11), Stroke::new(1.0, ACCENT), StrokeKind::Inside);
            ui.painter().text(r.center(), egui::Align2::CENTER_CENTER, "v1.2.2", egui::FontId::new(TYPE_BADGE, egui::FontFamily::Name(BOLD.into())), ACCENT);
        });
        ui.scope_builder(egui::UiBuilder::new().max_rect(rect).layout(Layout::right_to_left(Align::Center)), |ui| {
            ui.spacing_mut().item_spacing.x = 12.0;
            if widgets::button(ui, if narrow { "Palette" } else { "⌘K Palette" }, Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                self.palette = Some((String::new(), 0));
            }
            if widgets::button(ui, "Clear", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                self.m.clear_active();
            }
            self.model_select(ui, if narrow { 120.0 } else { 300.0 });
            let (dot_live, label) = match (self.m.agent_ready, narrow) {
                (true, false) => (true, "Agent : ready"),
                (false, false) => (false, "Agent : offline"),
                (true, true) => (true, "ready"),
                (false, true) => (false, "offline"),
            };
            widgets::chip_parts(ui, (12.0, 6.0), 8.0, &[Part::Dot(dot_live), Part::Text(label.into(), TYPE_STATUS_PILL, false, TEXT2)], time);
            let (tok, cost) = self.m.tally();
            let parts = if narrow {
                vec![Part::Text(tok, TYPE_TOKEN_TALLY, true, TEXT), Part::Text("•".into(), TYPE_TOKEN_TALLY, false, BORDER), Part::Text(cost, TYPE_TOKEN_TALLY, true, ACCENT)]
            } else {
                vec![
                    Part::Text("Tokens:".into(), TYPE_TOKEN_TALLY, false, TEXT2),
                    Part::Text(tok, TYPE_TOKEN_TALLY, true, TEXT),
                    Part::Text("•".into(), TYPE_TOKEN_TALLY, false, BORDER),
                    Part::Text("Cost:".into(), TYPE_TOKEN_TALLY, false, TEXT2),
                    Part::Text(cost, TYPE_TOKEN_TALLY, true, ACCENT),
                ]
            };
            widgets::chip_parts(ui, (14.0, 6.0), 8.0, &parts, time);
        });
    }

    fn model_select(&mut self, ui: &mut egui::Ui, width: f32) {
        let label = if width < 200.0 { MODELS[self.m.model].0 } else { MODELS[self.m.model].1 };
        ui.scope(|ui| {
            ui.spacing_mut().button_padding = vec2(14.0, 8.0);
            style_combo(ui);
            let before = self.m.model;
            egui::ComboBox::from_id_salt("model-select").width(width).selected_text(rt(label, 13.0, TEXT)).show_ui(ui, |ui| {
                for (i, (_, name)) in MODELS.iter().enumerate() {
                    ui.selectable_value(&mut self.m.model, i, rt(*name, 13.0, TEXT));
                }
            });
            if before != self.m.model {
                self.m.save();
            }
        });
    }

    // ---------------------------------------------------------------- session bar
    fn session_bar(&mut self, ui: &mut egui::Ui) {
        ui.spacing_mut().item_spacing = vec2(8.0, 0.0);
        let (mut close, mut select, mut add) = (None, None, false);
        let (mut rename, mut pin) = (None, None);
        let mut wt: Option<(usize, bool)> = None;
        let visible = self.m.visible();
        ui.horizontal_centered(|ui| {
            for &i in &visible {
                let s = &self.m.sessions[i];
                let status = self.m.status(i);
                let active = i == self.m.active;
                let w = ui.fonts_mut(|f| f.layout_no_wrap(s.name.clone(), egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT).size().x);
                let dot_w = if status == Status::Idle && !s.pinned { 0.0 } else { 14.0 };
                let (rect, resp) = ui.allocate_exact_size(vec2(14.0 * 2.0 + w + 16.0 + dot_w, 29.0), Sense::click());
                let hover = resp.hovered();
                if active {
                    widgets::shadow(ui, rect, 12, SHADOW_TAB_ACTIVE_GLOW);
                }
                ui.painter().rect_filled(rect, cr(12), if active { ACCENT_DIM } else { BG2 });
                ui.painter().rect_stroke(rect, cr(12), Stroke::new(1.0, if active { ACCENT } else if hover { ACCENT_BORDER_HOVER } else { BORDER }), StrokeKind::Inside);
                if dot_w > 0.0 {
                    status_dot(ui, rect.left_center() + vec2(15.0, 0.0), status, 4.0);
                }
                let fam = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
                ui.painter().text(rect.left_center() + vec2(14.0 + dot_w, 0.0), egui::Align2::LEFT_CENTER, &s.name, egui::FontId::new(TYPE_SESSION_TAB, fam), if active { ACCENT } else if hover { TEXT } else { MUTED });
                let xr = egui::Rect::from_center_size(rect.right_center() - vec2(18.0, 0.0), vec2(14.0, 20.0));
                let xhover = ui.rect_contains_pointer(xr);
                ui.painter().text(xr.center(), egui::Align2::CENTER_CENTER, "×", egui::FontId::new(14.0, egui::FontFamily::Proportional), if xhover { ERROR } else if active { ACCENT } else { MUTED });
                if resp.clicked() {
                    if xhover {
                        close = Some(i)
                    } else {
                        select = Some(i)
                    }
                }
                if resp.double_clicked() {
                    rename = Some((i, s.name.clone()));
                }
                resp.context_menu(|ui| {
                    if ui.button("Rename").clicked() {
                        rename = Some((i, s.name.clone()));
                        ui.close();
                    }
                    if ui.button(if s.pinned { "Unpin" } else { "Pin" }).clicked() {
                        pin = Some(i);
                        ui.close();
                    }
                    if s.worktree.is_some() {
                        if ui.button("Remove worktree").clicked() {
                            wt = Some((i, false));
                            ui.close();
                        }
                    } else if ui.button("Work in its own git worktree").clicked() {
                        wt = Some((i, true));
                        ui.close();
                    }
                    if ui.button("Archive").clicked() {
                        close = Some(i);
                        ui.close();
                    }
                });
            }
            let gw = ui.fonts_mut(|f| f.layout_no_wrap("+ New Session".into(), egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT).size().x);
            let (rect, resp) = ui.allocate_exact_size(vec2(gw + 26.0, 29.0), Sense::click());
            if resp.hovered() {
                ui.painter().rect_filled(rect, cr(12), ACCENT_DIM);
                ui.painter().rect_stroke(rect, cr(12), Stroke::new(1.0, ACCENT), StrokeKind::Inside);
            } else {
                widgets::dashed_rounded(ui, rect.shrink(0.5), 12.0, Stroke::new(1.0, ACCENT_BORDER_DASHED));
            }
            ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, "+ New Session", egui::FontId::new(12.0, egui::FontFamily::Proportional), ACCENT);
            add = resp.clicked();
        });
        if let Some(i) = select {
            self.m.select(i);
            self.m.save();
        }
        if let Some(i) = close {
            self.m.close_session(i);
        }
        if let Some(i) = pin {
            self.m.sessions[i].pinned = !self.m.sessions[i].pinned;
            self.m.save();
        }
        if rename.is_some() {
            self.rename = rename;
        }
        if let Some((i, make)) = wt {
            let keep = self.m.active;
            self.m.active = i;
            let r = if make { self.m.start_worktree().map(|p| format!("Thread now works in its own worktree: {p}")) } else { self.m.remove_worktree().map(|_| "Worktree removed".to_string()) };
            self.m.active = keep;
            self.work.note = match r {
                Ok(t) => t,
                Err(e) => format!("error: {e}"),
            };
            self.m.notice = self.work.note.clone();
        }
        if add {
            self.m.new_session();
        }
    }

    // ---------------------------------------------------------------- projects (left sidebar, under the mode list)
    fn projects(&mut self, ui: &mut egui::Ui) {
        ui.add_space(14.0);
        ui.label(rt("PROJECTS", 10.0, MUTED).extra_letter_spacing(1.0));
        ui.spacing_mut().item_spacing = vec2(0.0, 2.0);
        let (mut pick_project, mut pick_thread, mut open) = (None, None, false);
        let projects: Vec<_> = self.m.projects.iter().map(|p| (p.id, p.name.clone(), p.path.clone())).collect();
        for (id, name, path) in projects {
            let active = id == self.m.active_project;
            let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 28.0), Sense::click());
            if active {
                ui.painter().rect_filled(rect, cr(8), BG3);
            } else if resp.hovered() {
                ui.painter().rect_filled(rect, cr(8), Color32::from_white_alpha(6));
            }
            ui.painter().text(rect.left_center() + vec2(8.0, 0.0), egui::Align2::LEFT_CENTER, if active { "▾" } else { "▸" }, egui::FontId::new(11.0, egui::FontFamily::Proportional), MUTED);
            let mut label = name.clone();
            while label.chars().count() > 17 {
                label.pop();
            }
            ui.painter().text(rect.left_center() + vec2(24.0, 0.0), egui::Align2::LEFT_CENTER, label, egui::FontId::new(12.0, if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional }), if active { TEXT } else { TEXT2 });
            let resp = resp.on_hover_text(path);
            if resp.clicked() {
                pick_project = Some(id);
            }
            if active {
                for i in self.m.thread_indices(id) {
                    let t = &self.m.sessions[i];
                    let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 24.0), Sense::click());
                    if i == self.m.active {
                        ui.painter().rect_filled(rect, cr(8), ACCENT_DIM);
                    } else if resp.hovered() {
                        ui.painter().rect_filled(rect, cr(8), Color32::from_white_alpha(6));
                    }
                    status_dot(ui, rect.left_center() + vec2(22.0, 0.0), self.m.status(i), 3.5);
                    let mut label = t.name.clone();
                    while label.chars().count() > 16 {
                        label.pop();
                    }
                    ui.painter().text(rect.left_center() + vec2(36.0, 0.0), egui::Align2::LEFT_CENTER, label, egui::FontId::new(11.5, egui::FontFamily::Proportional), if i == self.m.active { ACCENT } else { MUTED });
                    if resp.clicked() {
                        pick_thread = Some(i);
                    }
                }
            }
        }
        ui.add_space(6.0);
        let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 28.0), Sense::click());
        if resp.hovered() {
            ui.painter().rect_filled(rect, cr(8), ACCENT_DIM);
        } else {
            widgets::dashed_rounded(ui, rect.shrink(0.5), 8.0, Stroke::new(1.0, ACCENT_BORDER_DASHED));
        }
        ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, "+ Open Folder", egui::FontId::new(12.0, egui::FontFamily::Proportional), ACCENT);
        open = open || resp.clicked();
        if let Some(id) = pick_project {
            self.m.set_project(id);
            self.m.save();
        }
        if let Some(i) = pick_thread {
            self.m.select(i);
        }
        if open {
            self.open_folder = Some((self.m.project_path(), String::new()));
        }
    }

    // ---------------------------------------------------------------- nav
    fn nav(&mut self, ui: &mut egui::Ui) {
        ui.spacing_mut().item_spacing = vec2(0.0, 6.0);
        for (key, label) in NAV {
            let active = self.m.nav == *key;
            let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 35.5), Sense::click());
            let hover = resp.hovered();
            let rect = rect.translate(vec2(if hover && !active { 3.0 } else { 0.0 }, 0.0));
            if active {
                ui.painter().rect_filled(rect, cr(12), ACCENT_DIM);
                ui.painter().rect_filled(egui::Rect::from_min_size(rect.min + vec2(0.0, 6.0), vec2(3.0, rect.height() - 12.0)), cr(1), ACCENT);
            } else if hover {
                ui.painter().rect_filled(rect, cr(12), BG3);
            }
            let family = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
            ui.painter().text(rect.left_center() + vec2(14.0, 0.0), egui::Align2::LEFT_CENTER, label, egui::FontId::new(TYPE_NAV_ITEM, family), if active { ACCENT } else if hover { TEXT } else { MUTED });
            if resp.clicked() {
                self.m.set_nav(key);
            }
        }
    }

    // ---------------------------------------------------------------- chat
    fn chat(&mut self, ui: &mut egui::Ui) {
        let mut rewind_pick: Option<usize> = None;
        let max_w = (ui.available_width() - 48.0).min(760.0).max(150.0);
        ScrollArea::vertical().auto_shrink([false, false]).stick_to_bottom(true).show(ui, |ui| {
            ui.spacing_mut().item_spacing = vec2(0.0, 20.0);
            egui::Frame::new().inner_margin(Margin::same(24)).show(ui, |ui| {
                ui.set_width(ui.available_width());
                for (mi, m) in self.m.sessions[self.m.active].messages.iter().enumerate() {
                    let layout = if m.user { Layout::top_down(Align::Max) } else { Layout::top_down(Align::Min) };
                    ui.with_layout(layout, |ui| {
                        ui.spacing_mut().item_spacing = vec2(0.0, 8.0);
                        ui.label(rt(m.header.to_uppercase(), TYPE_MSG_HEADER, MUTED).extra_letter_spacing(0.3));
                        let (fill, stroke, radius) = if m.user {
                            (USER_BUBBLE, Stroke::new(1.0, USER_BUBBLE_BORDER), CornerRadius { nw: 16, ne: 16, sw: 16, se: 4 })
                        } else {
                            (BG2, Stroke::new(1.0, BORDER), CornerRadius { nw: 16, ne: 16, sw: 4, se: 16 })
                        };
                        if !m.text.is_empty() || m.actions.is_empty() {
                            egui::Frame::new().fill(fill).stroke(stroke).corner_radius(radius).inner_margin(Margin::symmetric(18, 14)).show(ui, |ui| {
                                ui.set_max_width(max_w - 36.0);
                                ui.add(egui::Label::new(rt(&m.text, TYPE_MESSAGE_BODY, TEXT)).wrap());
                            });
                        }
                        for a in &m.actions {
                            let c = if a.starts_with('×') { ERROR } else if a.starts_with('✓') { ACCENT } else { MUTED };
                            ui.add(egui::Label::new(rt(a, 12.0, c)).wrap());
                        }
                        if m.user && self.m.checkpoint_for(mi).is_some() && !self.m.active_busy() {
                            if ui.add(egui::Label::new(rt("‹ Rewind files to before this message", 11.0, MUTED)).sense(Sense::click())).on_hover_text("Restore the project to how it was before this message").clicked() {
                                rewind_pick = self.m.checkpoint_for(mi);
                            }
                        }
                    });
                }
            });
        });
        if rewind_pick.is_some() {
            self.rewind_confirm = rewind_pick;
        }
    }

    // ---------------------------------------------------------------- composer
    fn composer(&mut self, ui: &mut egui::Ui) {
        ui.spacing_mut().item_spacing.x = 12.0;
        let mut do_send = false;
        let mut do_stop = false;
        ui.horizontal(|ui| {
            let send_w = 62.0;
            let width = ui.available_width() - send_w - 12.0;
            let id = egui::Id::new("prompt");
            let focused = ui.memory(|m| m.has_focus(id));
            if focused && ui.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Enter)) {
                do_send = true;
            }
            if focused && self.composer_rect.is_positive() {
                widgets::shadow(ui, self.composer_rect, 16, SHADOW_GLOW);
            }
            let resp = egui::Frame::new()
                .fill(BG3)
                .corner_radius(cr(16))
                .inner_margin(Margin::symmetric(16, 12))
                .stroke(Stroke::new(1.0, if focused { ACCENT } else { BORDER }))
                .show(ui, |ui| {
                    ui.set_width(width - 34.0);
                    let hint = match self.m.nav {
                        "terminal" => "Run a command in the sandbox...",
                        _ => "Summon frontier model, instruct agent, or issue bash command...",
                    };
                    let r = ui.add(
                        egui::TextEdit::multiline(&mut self.input)
                            .id(id)
                            .hint_text(rt(hint, TYPE_TEXTAREA, MUTED))
                            .font(egui::FontId::new(TYPE_TEXTAREA, egui::FontFamily::Proportional))
                            .desired_rows(2)
                            .frame(egui::Frame::NONE)
                            .desired_width(f32::INFINITY),
                    );
                    ui.set_min_height(30.0);
                    r
                });
            if self.focus_input {
                resp.inner.request_focus();
                self.focus_input = false;
            }
            self.composer_rect = resp.response.rect;
            ui.with_layout(Layout::bottom_up(Align::Max), |ui| {
                if self.m.active_busy() {
                    if widgets::button(ui, "Stop", Kind::Danger, 13.0, (16.0, 8.0), Some(send_w)).clicked() {
                        do_stop = true;
                    }
                } else if widgets::button(ui, "Send", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                    do_send = true;
                }
            });
        });
        // meta row: permission mode, queue, undo rewind, hints
        let (mut cycle, mut undo) = (false, false);
        ui.horizontal(|ui| {
            let perm = self.m.sessions[self.m.active].permission.clone();
            let label = PERMISSIONS.iter().find(|p| p.0 == perm).map(|p| p.1).unwrap_or("Accept edits");
            if widgets::button(ui, &format!("Permissions: {label}"), Kind::Secondary, 11.0, (10.0, 3.0), None).on_hover_text("Click to change what the agent may do without asking").clicked() {
                cycle = true;
            }
            let q = self.queue.get(&self.m.sessions[self.m.active].id).map(|q| q.len()).unwrap_or(0);
            if q > 0 {
                ui.label(rt(format!("{q} queued, sends when this turn ends"), 11.0, ACCENT));
            } else if self.m.active_busy() {
                ui.label(rt("Enter queues a follow-up", 11.0, MUTED));
            } else {
                ui.label(rt("@ attaches a file   Ctrl+P finds files   Ctrl+O opens a folder", 11.0, MUTED));
            }
            if self.m.sessions[self.m.active].pre_rewind.is_some() && !self.m.active_busy() {
                if widgets::button(ui, "Undo rewind", Kind::Secondary, 11.0, (10.0, 3.0), None).clicked() {
                    undo = true;
                }
            }
        });
        if cycle {
            self.m.cycle_permission();
        }
        if undo {
            if let Err(e) = self.m.undo_rewind() {
                self.m.notice = e;
            }
        }
        self.mention_popup(ui);
        if do_stop {
            if let Some(be) = &mut self.be {
                self.m.stop(be);
            }
        }
        if do_send {
            self.send();
        }
    }

    // ---------------------------------------------------------------- files
    fn tree_rows(&mut self, ui: &mut egui::Ui, rel: &str, depth: usize, open: &mut Option<String>) {
        for e in self.work.entries(rel) {
            let path = if rel.is_empty() { e.name.clone() } else { format!("{rel}/{}", e.name) };
            let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 22.0), Sense::click());
            let is_open = self.work.open.as_ref().map(|f| f.rel == path).unwrap_or(false);
            if is_open {
                ui.painter().rect_filled(rect, cr(6), ACCENT_DIM);
            } else if resp.hovered() {
                ui.painter().rect_filled(rect, cr(6), BG3);
            }
            let x = rect.left() + 6.0 + depth as f32 * 14.0;
            let expanded = self.work.expanded.contains(&path);
            let mark = if e.dir { if expanded { "▾" } else { "▸" } } else { " " };
            let dirty = self.work.status.as_ref().map(|s| s.changes.iter().any(|c| c.path == path)).unwrap_or(false);
            ui.painter().text(egui::pos2(x, rect.center().y), egui::Align2::LEFT_CENTER, mark, egui::FontId::new(11.0, egui::FontFamily::Proportional), MUTED);
            ui.painter().text(egui::pos2(x + 14.0, rect.center().y), egui::Align2::LEFT_CENTER, &e.name, egui::FontId::new(12.0, egui::FontFamily::Proportional), if e.dir { TEXT } else if dirty { ACCENT } else { TEXT2 });
            if resp.clicked() {
                if e.dir {
                    self.work.toggle(&path);
                } else {
                    *open = Some(path.clone());
                }
            }
            if e.dir && expanded {
                self.tree_rows(ui, &path, depth + 1, open);
            }
        }
    }

    fn files_tab(&mut self, ui: &mut egui::Ui) {
        let (mut save, mut close, mut find) = (false, false, false);
        if self.work.open.is_some() {
            let (dirty, rel, error) = {
                let f = self.work.open.as_ref().unwrap();
                (f.dirty(), f.rel.clone(), f.error.clone())
            };
            ui.horizontal(|ui| {
                if widgets::button(ui, "‹ Files", Kind::Secondary, 12.0, (10.0, 5.0), None).clicked() {
                    close = true;
                }
                ui.label(rtb(format!("{rel}{}", if dirty { " ●" } else { "" }), 12.0, if dirty { ACCENT } else { TEXT2 }));
            });
            if let Some(e) = error {
                ui.label(rt(format!("Cannot show this file: {e}"), 12.0, ERROR));
            } else {
                if let Some(f) = self.work.open.as_mut() {
                    egui::Frame::new().fill(BG).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).inner_margin(Margin::same(8)).show(ui, |ui| {
                        ui.add(egui::TextEdit::multiline(&mut f.text).code_editor().font(egui::FontId::monospace(12.0)).desired_width(f32::INFINITY).desired_rows(24).frame(egui::Frame::NONE));
                    });
                }
                ui.horizontal(|ui| {
                    if widgets::button(ui, "Save  Ctrl+S", Kind::Primary, 12.0, (12.0, 6.0), None).clicked() {
                        save = true;
                    }
                    ui.label(rt(self.work.note.clone(), 11.0, if self.work.note.starts_with("error") { ERROR } else { MUTED }));
                });
            }
        } else {
            ui.horizontal(|ui| {
                ui.label(rtb(self.m.projects.iter().find(|p| p.id == self.m.active_project).map(|p| p.name.clone()).unwrap_or_default(), 12.0, TEXT));
                ui.label(rt(format!("{} files", self.work.index.len()), 11.0, MUTED));
                if widgets::button(ui, "Find  Ctrl+P", Kind::Secondary, 11.0, (10.0, 4.0), None).clicked() {
                    find = true;
                }
            });
            ui.spacing_mut().item_spacing.y = 0.0;
            let mut open = None;
            self.tree_rows(ui, "", 0, &mut open);
            if let Some(p) = open {
                self.work.open_file(&p);
            }
        }
        if close {
            self.work.open = None;
            self.work.note.clear();
        }
        if save {
            self.save_open_file();
        }
        if find {
            self.finder = Some((String::new(), 0));
        }
    }

    fn save_open_file(&mut self) {
        self.work.note = match self.work.save_open() {
            Ok(_) => "saved".into(),
            Err(e) => format!("error: {e}"),
        };
    }

    // ---------------------------------------------------------------- git
    fn git_tab(&mut self, ui: &mut egui::Ui) {
        if !self.work.is_repo {
            ui.label(rt("This folder is not a git repository. Checkpoints, rewind and change review need git.", 12.0, MUTED));
            if widgets::button(ui, "Initialize git here", Kind::Primary, 12.0, (12.0, 6.0), None).clicked() {
                self.work.note = match hydra_desktop::git::init(&self.work.root) {
                    Ok(_) => "initialized".into(),
                    Err(e) => format!("error: {e}"),
                };
                self.work.refresh(true);
            }
            ui.label(rt(self.work.note.clone(), 11.0, MUTED));
            return;
        }
        let st = self.work.status.clone().unwrap_or_default();
        ui.horizontal(|ui| {
            ui.label(rtb(format!("branch  {}", st.branch), 13.0, ACCENT));
            if st.ahead > 0 || st.behind > 0 {
                ui.label(rt(format!("↑{} ↓{}", st.ahead, st.behind), 12.0, MUTED));
            }
            if let Some(u) = &st.upstream {
                ui.label(rt(u.clone(), 11.0, MUTED));
            }
        });
        let (mut sel, mut stage, mut unstage) = (None, None, None);
        let mut section = |ui: &mut egui::Ui, title: &str, staged: bool, sel: &mut Option<(String, bool)>, act: &mut Option<String>| {
            let rows: Vec<_> = st.changes.iter().filter(|c| if staged { c.x != '.' && c.x != '?' } else { c.y != '.' || c.x == '?' }).collect();
            if rows.is_empty() {
                return;
            }
            ui.label(rt(format!("{title} ({})", rows.len()), 11.0, MUTED).extra_letter_spacing(0.5));
            for c in rows {
                let letter = if staged { c.x } else if c.x == '?' { '?' } else { c.y };
                let col = match letter { 'M' => TEXT2, 'A' => ACCENT, 'D' => ERROR, '?' => ACCENT2, _ => ERROR };
                let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 22.0), Sense::click());
                let on = self.work.selected.as_deref() == Some(c.path.as_str()) && self.work.selected_staged == staged;
                if on {
                    ui.painter().rect_filled(rect, cr(6), ACCENT_DIM);
                } else if resp.hovered() {
                    ui.painter().rect_filled(rect, cr(6), BG3);
                }
                ui.painter().text(egui::pos2(rect.left() + 8.0, rect.center().y), egui::Align2::LEFT_CENTER, letter, egui::FontId::monospace(12.0), col);
                ui.painter().text(egui::pos2(rect.left() + 26.0, rect.center().y), egui::Align2::LEFT_CENTER, &c.path, egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT2);
                let br = egui::Rect::from_center_size(egui::pos2(rect.right() - 14.0, rect.center().y), vec2(22.0, 20.0));
                let bhover = ui.rect_contains_pointer(br);
                ui.painter().text(br.center(), egui::Align2::CENTER_CENTER, if staged { "−" } else { "+" }, egui::FontId::new(15.0, egui::FontFamily::Proportional), if bhover { ACCENT } else { MUTED });
                if resp.clicked() {
                    if bhover {
                        *act = Some(c.path.clone());
                    } else {
                        *sel = Some((c.path.clone(), staged));
                    }
                }
            }
        };
        let mut unstage_pick = None;
        section(ui, "STAGED", true, &mut sel, &mut unstage_pick);
        let mut stage_pick = None;
        section(ui, "CHANGES", false, &mut sel, &mut stage_pick);
        if st.changes.is_empty() {
            ui.label(rt("Working tree clean.", 12.0, MUTED));
        }
        if let Some(p) = stage_pick {
            stage = Some(p);
        }
        if let Some(p) = unstage_pick {
            unstage = Some(p);
        }
        // diff of the selected file with per-hunk actions
        let (mut reject, mut stage_h) = (None, None);
        if self.work.selected.is_some() {
            let hunks = self.work.hunks();
            let untracked = st.changes.iter().any(|c| Some(&c.path) == self.work.selected.as_ref() && c.x == '?');
            ui.add_space(4.0);
            ui.label(rtb(self.work.selected.clone().unwrap_or_default(), 12.0, TEXT));
            for (hi, h) in hunks.iter().enumerate() {
                egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    egui::Frame::new().fill(ELEV).inner_margin(Margin::symmetric(12, 6)).show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.horizontal(|ui| {
                            ui.label(rt(&h.header, 11.0, MUTED));
                            if !untracked && !self.work.selected_staged {
                                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                                    if widgets::button(ui, "Reject", Kind::Danger, 11.0, (8.0, 3.0), None).clicked() {
                                        reject = Some(hi);
                                    }
                                    if widgets::button(ui, "Stage", Kind::Secondary, 11.0, (8.0, 3.0), None).clicked() {
                                        stage_h = Some(hi);
                                    }
                                });
                            }
                        });
                    });
                    ui.spacing_mut().item_spacing.y = 0.0;
                    for line in &h.lines {
                        let (bg, fg) = if line.starts_with('+') { (DIFF_ADD, DIFF_ADD_TEXT) } else if line.starts_with('-') { (DIFF_DEL, DIFF_DEL_TEXT) } else { (Color32::TRANSPARENT, MUTED) };
                        egui::Frame::new().fill(bg).inner_margin(Margin::symmetric(12, 1)).show(ui, |ui| {
                            ui.set_width(ui.available_width());
                            ui.add(egui::Label::new(RichText::new(line).size(TYPE_DIFF).color(fg)).wrap());
                        });
                    }
                });
            }
        }
        // commit box
        ui.add_space(6.0);
        ui.add(egui::TextEdit::multiline(&mut self.work.commit_msg).hint_text(rt("Commit message", 12.0, MUTED)).desired_rows(2).desired_width(f32::INFINITY));
        let (mut draft, mut commit, mut push) = (false, false, false);
        ui.horizontal(|ui| {
            if widgets::button(ui, "Draft", Kind::Secondary, 12.0, (10.0, 5.0), None).clicked() {
                draft = true;
            }
            if widgets::button(ui, "Commit", Kind::Primary, 12.0, (12.0, 5.0), None).clicked() {
                commit = true;
            }
            if widgets::button(ui, "Push", Kind::Secondary, 12.0, (10.0, 5.0), None).clicked() {
                push = true;
            }
        });
        if !self.work.note.is_empty() {
            ui.add(egui::Label::new(rt(self.work.note.clone(), 11.0, if self.work.note.starts_with("error") { ERROR } else { MUTED })).wrap());
        }
        if let Some((p, staged)) = sel {
            self.work.select_change(&p, staged);
        }
        let report = |w: &mut Work, r: Result<(), String>| {
            if let Err(e) = r {
                w.note = format!("error: {e}");
            }
        };
        if let Some(p) = stage {
            let r = self.work.stage(&[p]);
            report(&mut self.work, r);
        }
        if let Some(p) = unstage {
            let r = self.work.unstage(&[p]);
            report(&mut self.work, r);
        }
        if let Some(i) = reject {
            let r = self.work.reject_hunk(i);
            report(&mut self.work, r);
        }
        if let Some(i) = stage_h {
            let r = self.work.stage_hunk(i);
            report(&mut self.work, r);
        }
        if draft {
            self.work.commit_msg = self.work.draft_message();
        }
        if commit {
            let r = self.work.commit().map(|_| ());
            report(&mut self.work, r);
        }
        if push {
            self.work.note = match self.work.push() {
                Ok(o) => if o.trim().is_empty() { "pushed".into() } else { o.trim().to_string() },
                Err(e) => format!("error: {e}"),
            };
        }
    }

    /// Popup above the composer while an `@file` word is being typed. Up/Down pick, Tab or Enter inserts.
    fn mention_popup(&mut self, ui: &mut egui::Ui) {
        let Some(prefix) = hydra_desktop::fsx::mention_prefix(&self.input).map(|p| p.to_string()) else { return };
        let hits: Vec<String> = hydra_desktop::fsx::fuzzy(&prefix, &self.work.index, 6).into_iter().map(String::from).collect();
        if hits.is_empty() {
            return;
        }
        self.mention_sel = self.mention_sel.min(hits.len() - 1);
        let ctx = ui.ctx().clone();
        if ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::ArrowDown)) {
            self.mention_sel = (self.mention_sel + 1) % hits.len();
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::ArrowUp)) {
            self.mention_sel = (self.mention_sel + hits.len() - 1) % hits.len();
        }
        let mut pick = ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Tab)).then(|| hits[self.mention_sel].clone());
        let rect = self.composer_rect;
        egui::Area::new(egui::Id::new("mentions")).order(egui::Order::Foreground).fixed_pos(egui::pos2(rect.left(), rect.top() - 8.0 - hits.len() as f32 * 24.0 - 12.0)).show(&ctx, |ui| {
            egui::Frame::new().fill(BG2).stroke(Stroke::new(1.0, ACCENT_BORDER_HOVER)).corner_radius(cr(12)).inner_margin(Margin::same(6)).show(ui, |ui| {
                ui.set_width(rect.width().min(520.0));
                for (i, h) in hits.iter().enumerate() {
                    let (r, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 24.0), Sense::click());
                    if i == self.mention_sel {
                        ui.painter().rect_filled(r, cr(8), ACCENT_DIM);
                    }
                    ui.painter().text(egui::pos2(r.left() + 8.0, r.center().y), egui::Align2::LEFT_CENTER, h, egui::FontId::new(12.0, egui::FontFamily::Proportional), if i == self.mention_sel { ACCENT } else { TEXT2 });
                    if resp.clicked() {
                        pick = Some(h.clone());
                    }
                }
            });
        });
        if let Some(p) = pick {
            let at = self.input.rfind('@').unwrap_or(0);
            self.input.truncate(at);
            self.input.push('@');
            self.input.push_str(&p);
            self.input.push(' ');
            self.focus_input = true;
        }
    }

    /// Send queued messages for threads that just went idle.
    fn run_queue(&mut self) {
        let ready: Vec<u32> = self.queue.iter().filter(|(sid, q)| !q.is_empty() && !self.m.is_running(**sid)).map(|(sid, _)| *sid).collect();
        for sid in ready {
            let Some(idx) = self.m.sessions.iter().position(|s| s.id == sid) else { self.queue.remove(&sid); continue };
            let text = self.queue.get_mut(&sid).map(|q| q.remove(0)).unwrap_or_default();
            let keep = self.m.active;
            let keep_project = self.m.active_project;
            self.m.active = idx;
            self.m.active_project = self.m.sessions[idx].project;
            if let Some(be) = &mut self.be {
                self.m.submit(be, &text);
            }
            self.m.active = keep;
            self.m.active_project = keep_project;
        }
    }

    // ---------------------------------------------------------------- drawer
    fn drawer(&mut self, ui: &mut egui::Ui, width: f32) {
        egui::Frame::new().inner_margin(Margin::symmetric(20, 14)).show(ui, |ui| {
            ui.set_width(width - 41.0);
            ui.horizontal(|ui| {
                ui.label(rtb("INSPECTION & AUTOMATION", TYPE_DRAWER_HEADER, TEXT2).extra_letter_spacing(0.3));
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    ui.label(rt("X: - | Y: -", TYPE_COORD_READOUT, MUTED));
                });
            });
        });
        let y = ui.cursor().top();
        ui.painter().hline(ui.max_rect().x_range(), y, Stroke::new(1.0, BORDER));
        egui::Frame::new().fill(BG).inner_margin(Margin::symmetric(8, 4)).show(ui, |ui| {
            ui.set_width(width - 17.0);
            ui.spacing_mut().item_spacing = vec2(4.0, 0.0);
            let cell = (width - 17.0 - 12.0) / 4.0;
            ui.horizontal(|ui| {
                for (key, label) in DRAWER_TABS {
                    let active = self.m.drawer_tab == *key;
                    let w = ui.fonts_mut(|f| f.layout_no_wrap(label.to_string(), egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT).size().x);
                    let wrapped = w + 12.0 > cell;
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
                        self.m.drawer_tab = key;
                    }
                }
            });
        });
        egui::Frame::new().fill(BG).inner_margin(Margin::symmetric(8, 2)).show(ui, |ui| {
            ui.set_width(width - 17.0);
            ui.spacing_mut().item_spacing = vec2(4.0, 0.0);
            ui.horizontal(|ui| {
                for (key, label) in [("files", "Files"), ("git", "Changes")] {
                    let active = self.m.drawer_tab == key;
                    let mut text = label.to_string();
                    if key == "git" {
                        if let Some(st) = &self.work.status {
                            if !st.changes.is_empty() {
                                text = format!("Changes ({})", st.changes.len());
                            }
                        }
                    }
                    let (rect, resp) = ui.allocate_exact_size(vec2((width - 17.0 - 4.0) / 2.0 - 4.0, 26.0), Sense::click());
                    if active {
                        ui.painter().rect_filled(rect, cr(10), ACCENT_DIM);
                    } else if resp.hovered() {
                        ui.painter().rect_filled(rect, cr(10), BG3);
                    }
                    let fam = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
                    ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, text, egui::FontId::new(TYPE_DRAWER_TAB, fam), if active { ACCENT } else if resp.hovered() { TEXT } else { MUTED });
                    if resp.clicked() {
                        self.m.drawer_tab = key;
                    }
                }
            });
        });
        let y = ui.cursor().top();
        ui.painter().hline(ui.max_rect().x_range(), y, Stroke::new(1.0, BORDER));
        ScrollArea::vertical().auto_shrink([false, false]).show(ui, |ui| {
            egui::Frame::new().inner_margin(Margin::same(16)).show(ui, |ui| {
                ui.set_width(width - 33.0);
                ui.spacing_mut().item_spacing = vec2(8.0, 14.0);
                match self.m.drawer_tab {
                    "screen" => self.screen_tab(ui, width - 33.0),
                    "term" => self.term_tab(ui),
                    "diff" => self.diff_tab(ui),
                    "files" => self.files_tab(ui),
                    "git" => self.git_tab(ui),
                    _ => self.agent_tab(ui, width - 33.0),
                }
            });
        });
    }

    fn texture(&mut self, ctx: &egui::Context) {
        if self.m.screen_seq == 0 || self.tex.as_ref().map(|t| t.0) == Some(self.m.screen_seq) {
            return;
        }
        use base64::Engine;
        if let Ok(bytes) = base64::engine::general_purpose::STANDARD.decode(self.m.screen_b64.as_bytes()) {
            if let Ok(img) = image::load_from_memory(&bytes) {
                let rgba = img.to_rgba8();
                let ci = egui::ColorImage::from_rgba_unmultiplied([rgba.width() as usize, rgba.height() as usize], rgba.as_raw());
                self.tex = Some((self.m.screen_seq, ctx.load_texture("screen", ci, Default::default())));
            }
        }
    }

    fn screen_tab(&mut self, ui: &mut egui::Ui, w: f32) {
        self.texture(ui.ctx());
        let h = w * 9.0 / 16.0;
        let (rect, resp) = ui.allocate_exact_size(vec2(w, h), Sense::click());
        widgets::shadow(ui, rect, 16, SHADOW_SCREEN);
        ui.painter().rect_filled(rect, cr(16), BG);
        let mut click: Option<(i64, i64)> = None;
        if let Some((_, tex)) = &self.tex {
            let size = tex.size_vec2();
            let scale = (w / size.x).min(h / size.y);
            let img_rect = egui::Rect::from_center_size(rect.center(), size * scale);
            ui.painter().image(tex.id(), img_rect, egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)), Color32::WHITE);
            if let Some(p) = resp.hover_pos().filter(|p| img_rect.contains(*p)) {
                let (x, y) = (((p.x - img_rect.left()) / scale) as i64, ((p.y - img_rect.top()) / scale) as i64);
                ui.painter().hline(img_rect.x_range(), p.y, Stroke::new(1.0, accent_a(0.6)));
                ui.painter().vline(p.x, img_rect.y_range(), Stroke::new(1.0, accent_a(0.6)));
                ui.painter().text(rect.right_top() + vec2(-10.0, 8.0), egui::Align2::RIGHT_TOP, format!("X: {x} | Y: {y}"), egui::FontId::new(11.0, egui::FontFamily::Proportional), ACCENT);
                if resp.clicked() {
                    click = Some((x, y));
                }
            }
            if self.bounds {
                ui.painter().rect_stroke(img_rect.shrink(8.0), cr(4), Stroke::new(1.0, accent_a(0.7)), StrokeKind::Inside);
            }
        } else {
            ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, "Desktop Screen Preview", egui::FontId::new(12.0, egui::FontFamily::Proportional), MUTED);
        }
        ui.painter().rect_stroke(rect, cr(16), Stroke::new(1.0, BORDER), StrokeKind::Inside);
        if let (Some((x, y)), Some(be)) = (click, &mut self.be) {
            self.m.click(be, x, y);
        }
        let cell = (w - 16.0) / 3.0;
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = 8.0;
            if widgets::button(ui, "Capture Screen", Kind::Secondary, 13.0, (16.0, 8.0), Some(cell)).clicked() {
                if let Some(be) = &mut self.be {
                    self.m.capture(be);
                }
            }
            if widgets::button(ui, "Toggle Bounds", Kind::Secondary, 13.0, (16.0, 8.0), Some(cell)).clicked() {
                self.bounds = !self.bounds;
            }
            if widgets::button(ui, "Type Text", Kind::Secondary, 13.0, (16.0, 8.0), Some(cell)).clicked() {
                let t = self.type_text.clone();
                if let (false, Some(be)) = (t.is_empty(), &mut self.be) {
                    self.m.type_text(be, &t);
                }
            }
        });
        ui.add(egui::TextEdit::singleline(&mut self.type_text).hint_text(rt("Text to type into the focused window", 12.0, MUTED)).desired_width(f32::INFINITY));
        ui.label(rt(format!("Active Window: {}", self.m.window_title), 11.0, TEXT2));
    }

    fn term_tab(&mut self, ui: &mut egui::Ui) {
        egui::Frame::new().fill(BG).stroke(Stroke::new(1.0, TERMINAL_BORDER)).corner_radius(cr(16)).inner_margin(Margin::same(16)).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.set_min_height(240.0);
            ScrollArea::vertical().max_height(520.0).stick_to_bottom(true).auto_shrink([false, true]).show(ui, |ui| {
                ui.add(egui::Label::new(RichText::new(&self.m.term).size(TYPE_TERMINAL).color(ACCENT)).wrap());
            });
        });
    }

    fn diff_tab(&mut self, ui: &mut egui::Ui) {
        let diffs = self.m.sessions[self.m.active].diffs.clone();
        if diffs.is_empty() {
            ui.label(rt("No file changes recorded in this session yet.", 11.0, MUTED));
            return;
        }
        for d in diffs.iter().rev() {
            egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).show(ui, |ui| {
                ui.set_width(ui.available_width());
                egui::Frame::new().fill(ELEV).inner_margin(Margin::symmetric(12, 8)).show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ui.label(rtb(&d.path, TYPE_DIFF, TEXT2));
                });
                ui.spacing_mut().item_spacing.y = 0.0;
                for line in d.diff.lines() {
                    let (bg, fg) = if line.starts_with('+') && !line.starts_with("+++") {
                        (DIFF_ADD, DIFF_ADD_TEXT)
                    } else if line.starts_with('-') && !line.starts_with("---") {
                        (DIFF_DEL, DIFF_DEL_TEXT)
                    } else {
                        (Color32::TRANSPARENT, MUTED)
                    };
                    egui::Frame::new().fill(bg).inner_margin(Margin::symmetric(12, 1)).show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.add(egui::Label::new(RichText::new(line).size(TYPE_DIFF).color(fg)).wrap());
                    });
                }
            });
        }
    }

    fn agent_tab(&mut self, ui: &mut egui::Ui, w: f32) {
        ui.label(rtb("AUTONOMOUS AGENT RUNNER", 13.0, TEXT));
        let st = self.m.agent_status.clone();
        let state = st["status"].as_str().unwrap_or("IDLE").to_uppercase();
        ui.horizontal(|ui| {
            let c = match state.as_str() {
                "RUNNING" => ACCENT,
                "FAILED" | "ABORTED" => ERROR,
                _ => MUTED,
            };
            ui.label(rtb(&state, TYPE_BADGE, c));
            let step = st["current_step"].as_u64().unwrap_or(0);
            let max = st["max_steps"].as_u64().unwrap_or(self.agent_steps as u64).max(1);
            ui.label(rt(format!("step {step}/{max}  {:.1}s", st["elapsed_sec"].as_f64().unwrap_or(0.0)), 11.0, MUTED));
        });
        let frac = st["current_step"].as_f64().unwrap_or(0.0) / st["max_steps"].as_f64().unwrap_or(self.agent_steps as f64).max(1.0);
        let (bar, _) = ui.allocate_exact_size(vec2(w, 6.0), Sense::hover());
        ui.painter().rect_filled(bar, cr(3), BG3);
        ui.painter().rect_filled(egui::Rect::from_min_size(bar.min, vec2(bar.width() * frac.clamp(0.0, 1.0) as f32, 6.0)), cr(3), ACCENT);
        ui.add(egui::TextEdit::multiline(&mut self.agent_task).hint_text(rt("Describe the task for the agent...", 12.0, MUTED)).desired_rows(3).desired_width(f32::INFINITY));
        ui.horizontal(|ui| {
            ui.label(rt("Max steps", 11.0, MUTED));
            ui.add(egui::DragValue::new(&mut self.agent_steps).range(1..=100));
            style_combo(ui);
            egui::ComboBox::from_id_salt("profile").selected_text(rt(PROFILES[self.agent_profile], 12.0, TEXT)).show_ui(ui, |ui| {
                for (i, p) in PROFILES.iter().enumerate() {
                    ui.selectable_value(&mut self.agent_profile, i, rt(*p, 12.0, TEXT));
                }
            });
        });
        let cell = (w - 16.0) / 3.0;
        let mut cmds: Vec<Cmd> = vec![];
        ui.horizontal_wrapped(|ui| {
            ui.spacing_mut().item_spacing = vec2(8.0, 8.0);
            if widgets::button(ui, "Start Agent", Kind::Primary, 13.0, (16.0, 8.0), Some(cell)).clicked() {
                cmds.push(Cmd::AgentStart);
            }
            for (label, c, kind) in [
                ("Pause", Cmd::AgentCtl("pause"), Kind::Secondary),
                ("Resume", Cmd::AgentCtl("resume"), Kind::Secondary),
                ("Abort", Cmd::AgentCtl("abort"), Kind::Danger),
                ("Export Trace", Cmd::Export(false), Kind::Secondary),
                ("Export Markdown", Cmd::Export(true), Kind::Secondary),
                ("Templates", Cmd::Templates, Kind::Secondary),
                ("Jobs", Cmd::Jobs, Kind::Secondary),
            ] {
                if widgets::button(ui, label, kind, 13.0, (16.0, 8.0), Some(cell)).clicked() {
                    cmds.push(c);
                }
            }
        });
        for c in cmds {
            self.run(c);
        }
        ui.label(rtb("ACTION TRACE LOG", 11.0, MUTED));
        let actions = st["actions"].as_array().cloned().unwrap_or_default();
        if actions.is_empty() {
            ui.label(rt("No actions yet.", 11.0, MUTED));
        }
        for a in actions.iter().rev().take(30) {
            egui::Frame::new().fill(BG2).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).inner_margin(Margin::same(10)).show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.label(rtb(format!("#{} {}", a["step"].as_u64().unwrap_or(0), a["action"].as_str().unwrap_or("")), 12.0, ACCENT));
                if let Some(t) = a["thought"].as_str().filter(|t| !t.is_empty()) {
                    ui.add(egui::Label::new(rt(t, 11.0, TEXT2)).wrap());
                }
                ui.label(rt(format!("{}  {}", a["status"].as_str().unwrap_or(""), a["duration_ms"].as_f64().map(|d| format!("{d:.0}ms")).unwrap_or_default()), 11.0, MUTED));
            });
        }
    }

    // ---------------------------------------------------------------- modals
    fn startup_panel(&mut self, ctx: &egui::Context) -> bool {
        let fatal = health::fatal_problems(&self.checks).len();
        let stalled = self.be.is_some() && !self.m.agent_ready && self.started >= 0.0 && ctx.input(|i| i.time) - self.started > 20.0;
        let down = self.be.is_none() && self.start_error.is_some();
        let show = (fatal > 0 || stalled || down || self.crash_note.is_some()) && !self.panel_dismissed;
        if !show {
            return false;
        }
        let (mut retry, mut dismiss) = (false, false);
        modal_frame(ctx, "startup", 640.0, |ui| {
            ui.label(rtb("HYDRA NEEDS ATTENTION", 14.0, ACCENT));
            if let Some(n) = &self.crash_note {
                ui.add(egui::Label::new(rt(format!("Last session ended unexpectedly. Saved sessions were kept.\n{n}"), 12.0, ERROR)).wrap());
            }
            if let Some(e) = &self.start_error {
                ui.add(egui::Label::new(rt(format!("Agent did not start: {e}"), 12.0, ERROR)).wrap());
            } else if stalled {
                ui.add(egui::Label::new(rt("The agent started but has not answered after 20 seconds.", 12.0, ERROR)).wrap());
            }
            for c in &self.checks {
                let (mark, col) = if c.ok { ("✓", ACCENT) } else if c.fatal { ("×", ERROR) } else { ("!", MUTED) };
                ui.horizontal_top(|ui| {
                    ui.label(rtb(mark, 13.0, col));
                    ui.vertical(|ui| {
                        ui.label(rtb(c.name, 12.0, TEXT));
                        ui.add(egui::Label::new(rt(&c.detail, 11.0, MUTED)).wrap());
                    });
                });
            }
            ui.add(egui::Label::new(rt(format!("Logs: {}", hydra_desktop::crash::log_dir().display()), 11.0, MUTED)).wrap());
            ui.horizontal(|ui| {
                if widgets::button(ui, "Retry", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                    retry = true;
                }
                if widgets::button(ui, if fatal > 0 { "Continue without agent" } else { "Dismiss" }, Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                    dismiss = true;
                }
            });
        });
        if retry {
            self.crash_note = None;
            self.retry_backend(ctx);
        }
        if dismiss {
            self.crash_note = None;
            self.panel_dismissed = true;
        }
        true
    }

    fn modals(&mut self, ctx: &egui::Context) {
        if self.startup_panel(ctx) {
            return;
        }
        if let Some(a) = self.m.approval_for_active().cloned() {
            let mut decision: Option<&str> = None;
            modal_frame(ctx, "approval", 520.0, |ui| {
                ui.label(rtb("APPROVAL REQUIRED", 13.0, ACCENT));
                ui.label(rtb(&a.tool, 14.0, TEXT));
                egui::Frame::new().fill(BG).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).inner_margin(Margin::same(12)).show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ui.add(egui::Label::new(RichText::new(serde_json::to_string_pretty(&a.args).unwrap_or_default()).size(12.0).color(TEXT2)).wrap());
                });
                ui.horizontal(|ui| {
                    if widgets::button(ui, "Allow", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                        decision = Some("allow");
                    }
                    if widgets::button(ui, "Always allow", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        decision = Some("allow_always");
                    }
                    if widgets::button(ui, "Skip", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        decision = Some("skip");
                    }
                    if widgets::button(ui, "Abort Turn", Kind::Danger, 13.0, (16.0, 8.0), None).clicked() {
                        decision = Some("abort");
                    }
                });
            });
            if let (Some(d), Some(be)) = (decision, &mut self.be) {
                self.m.answer_approval(be, d);
            }
            return;
        }
        if let Some(i) = self.rewind_confirm {
            let label = self.m.sessions[self.m.active].checkpoints.get(i).map(|c| c.label.clone()).unwrap_or_default();
            let (mut go, mut cancel) = (false, false);
            modal_frame(ctx, "rewind", 520.0, |ui| {
                ui.label(rtb("REWIND FILES", 13.0, ACCENT));
                ui.add(egui::Label::new(rt(format!("Put the project back to how it was before:\n\"{label}\"\n\nEverything changed since then is replaced. The state right now is saved first, so you can Undo rewind."), 12.0, TEXT2)).wrap());
                ui.horizontal(|ui| {
                    if widgets::button(ui, "Rewind", Kind::Danger, 13.0, (16.0, 8.0), None).clicked() {
                        go = true;
                    }
                    if widgets::button(ui, "Cancel", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        cancel = true;
                    }
                });
            });
            if ctx.input(|i| i.key_pressed(Key::Escape)) {
                cancel = true;
            }
            if go {
                if let Err(e) = self.m.rewind_code(i) {
                    self.m.notice = e.clone();
                    self.work.note = format!("error: {e}");
                }
                self.work.refresh(true);
            }
            if go || cancel {
                self.rewind_confirm = None;
            }
            return;
        }
        if let Some((q, sel)) = self.finder.clone() {
            let (mut q, mut sel) = (q, sel);
            let hits: Vec<String> = hydra_desktop::fsx::fuzzy(&q, &self.work.index, 12).into_iter().map(String::from).collect();
            if ctx.input(|i| i.key_pressed(Key::ArrowDown)) {
                sel = (sel + 1).min(hits.len().saturating_sub(1));
            }
            if ctx.input(|i| i.key_pressed(Key::ArrowUp)) {
                sel = sel.saturating_sub(1);
            }
            sel = sel.min(hits.len().saturating_sub(1));
            let (mut open, mut close) = (None::<String>, false);
            modal_frame(ctx, "finder", 560.0, |ui| {
                let r = ui.add(egui::TextEdit::singleline(&mut q).hint_text(rt("Find a file by name", 13.0, MUTED)).desired_width(f32::INFINITY).font(egui::FontId::new(14.0, egui::FontFamily::Proportional)));
                r.request_focus();
                if r.changed() {
                    sel = 0;
                }
                if ui.input(|i| i.key_pressed(Key::Enter)) {
                    open = hits.get(sel).cloned();
                }
                for (i, h) in hits.iter().enumerate() {
                    let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 24.0), Sense::click());
                    if i == sel {
                        ui.painter().rect_filled(rect, cr(8), ACCENT_DIM);
                    }
                    ui.painter().text(egui::pos2(rect.left() + 8.0, rect.center().y), egui::Align2::LEFT_CENTER, h, egui::FontId::new(12.0, egui::FontFamily::Proportional), if i == sel { ACCENT } else { TEXT2 });
                    if resp.clicked() {
                        open = Some(h.clone());
                    }
                }
                if hits.is_empty() {
                    ui.label(rt("No matching files.", 12.0, MUTED));
                }
            });
            if ctx.input(|i| i.key_pressed(Key::Escape)) {
                close = true;
            }
            if let Some(p) = open {
                self.work.open_file(&p);
                self.m.drawer_tab = "files";
                close = true;
            }
            self.finder = if close { None } else { Some((q, sel)) };
            return;
        }
        if let Some((path, err)) = self.open_folder.clone() {
            let (mut path, mut err) = (path, err);
            let (mut open, mut close, mut go): (bool, bool, Option<String>) = (false, false, None);
            modal_frame(ctx, "openfolder", 560.0, |ui| {
                ui.label(rtb("OPEN FOLDER AS A PROJECT", 13.0, ACCENT));
                let r = ui.add(egui::TextEdit::singleline(&mut path).desired_width(f32::INFINITY).font(egui::FontId::monospace(13.0)));
                r.request_focus();
                if r.changed() {
                    err.clear();
                }
                if ui.input(|i| i.key_pressed(Key::Enter)) {
                    open = true;
                }
                let here = std::path::Path::new(path.trim());
                let base = if here.is_dir() { here.to_path_buf() } else { here.parent().map(|p| p.to_path_buf()).unwrap_or_default() };
                let prefix = if here.is_dir() { String::new() } else { here.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default() };
                ScrollArea::vertical().max_height(260.0).show(ui, |ui| {
                    ui.spacing_mut().item_spacing.y = 2.0;
                    if let Some(parent) = base.parent() {
                        if ui.add(egui::Label::new(rt("..", 12.0, MUTED)).sense(Sense::click())).clicked() {
                            go = Some(parent.display().to_string());
                        }
                    }
                    let mut dirs: Vec<_> = std::fs::read_dir(&base).map(|rd| rd.flatten().filter(|e| e.path().is_dir()).map(|e| e.file_name().to_string_lossy().to_string()).filter(|n| !n.starts_with('.') && n.to_lowercase().starts_with(&prefix)).collect()).unwrap_or_default();
                    dirs.sort_by_key(|d| d.to_lowercase());
                    for d in dirs.into_iter().take(300) {
                        if ui.add(egui::Label::new(rt(format!("▸ {d}"), 12.0, TEXT)).sense(Sense::click())).clicked() {
                            go = Some(base.join(&d).display().to_string());
                        }
                    }
                });
                if !err.is_empty() {
                    ui.label(rt(&err, 11.0, ERROR));
                }
                ui.horizontal(|ui| {
                    if widgets::button(ui, "Open", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                        open = true;
                    }
                    if widgets::button(ui, "Cancel", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        close = true;
                    }
                });
            });
            if ctx.input(|i| i.key_pressed(Key::Escape)) {
                close = true;
            }
            if let Some(g) = go {
                path = if g.ends_with(std::path::MAIN_SEPARATOR) { g } else { format!("{g}{}", std::path::MAIN_SEPARATOR) };
            }
            if open {
                match self.m.open_project(&path) {
                    Ok(_) => close = true,
                    Err(e) => err = e,
                }
            }
            self.open_folder = if close { None } else { Some((path, err)) };
            return;
        }
        if let Some((idx, name)) = self.rename.clone() {
            let mut name = name;
            let (mut ok, mut cancel) = (false, false);
            modal_frame(ctx, "rename", 420.0, |ui| {
                ui.label(rtb("RENAME THREAD", 13.0, ACCENT));
                let r = ui.add(egui::TextEdit::singleline(&mut name).desired_width(f32::INFINITY));
                r.request_focus();
                if ui.input(|i| i.key_pressed(Key::Enter)) {
                    ok = true;
                }
                ui.horizontal(|ui| {
                    if widgets::button(ui, "Rename", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                        ok = true;
                    }
                    if widgets::button(ui, "Cancel", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        cancel = true;
                    }
                });
            });
            if ctx.input(|i| i.key_pressed(Key::Escape)) {
                cancel = true;
            }
            if ok && !name.trim().is_empty() && idx < self.m.sessions.len() {
                self.m.sessions[idx].name = name.trim().to_string();
                self.m.save();
            }
            self.rename = if ok || cancel { None } else { Some((idx, name)) };
            return;
        }
        if let Some((q, sel)) = self.palette.clone() {
            let (mut q, mut sel) = (q, sel);
            let entries: Vec<_> = palette_entries().into_iter().filter(|e| q.is_empty() || e.0.to_lowercase().contains(&q.to_lowercase())).collect();
            let mut run: Option<Cmd> = None;
            let mut close = false;
            if ctx.input(|i| i.key_pressed(Key::ArrowDown)) {
                sel = (sel + 1).min(entries.len().saturating_sub(1));
            }
            if ctx.input(|i| i.key_pressed(Key::ArrowUp)) {
                sel = sel.saturating_sub(1);
            }
            if ctx.input(|i| i.key_pressed(Key::Enter)) {
                run = entries.get(sel).map(|e| e.2);
            }
            if ctx.input(|i| i.key_pressed(Key::Escape)) {
                close = true;
            }
            modal_frame(ctx, "palette", 560.0, |ui| {
                let r = ui.add(egui::TextEdit::singleline(&mut q).hint_text(rt("Type a command...", 13.0, MUTED)).desired_width(f32::INFINITY));
                r.request_focus();
                if r.changed() {
                    sel = 0;
                }
                ScrollArea::vertical().max_height(340.0).show(ui, |ui| {
                    for (i, (title, hint, cmd)) in entries.iter().enumerate() {
                        let (rect, resp) = ui.allocate_exact_size(vec2(ui.available_width(), 32.0), Sense::click());
                        let on = i == sel || resp.hovered();
                        if on {
                            ui.painter().rect_filled(rect, cr(10), ACCENT_DIM);
                        }
                        ui.painter().text(rect.left_center() + vec2(12.0, 0.0), egui::Align2::LEFT_CENTER, title, egui::FontId::new(13.0, egui::FontFamily::Proportional), if on { ACCENT } else { TEXT });
                        ui.painter().text(rect.right_center() - vec2(12.0, 0.0), egui::Align2::RIGHT_CENTER, hint, egui::FontId::new(11.0, egui::FontFamily::Proportional), MUTED);
                        if resp.clicked() {
                            run = Some(*cmd);
                        }
                    }
                });
            });
            if close {
                self.palette = None;
            } else if let Some(c) = run {
                self.palette = None;
                self.run(c);
            } else {
                self.palette = Some((q, sel));
            }
            return;
        }
        if self.templates_open {
            let mut close = ctx.input(|i| i.key_pressed(Key::Escape));
            let mut apply: Option<String> = None;
            modal_frame(ctx, "templates", 600.0, |ui| {
                ui.label(rtb("WORKFLOW TEMPLATES", 13.0, ACCENT));
                ui.add(egui::TextEdit::singleline(&mut self.templates_q).hint_text(rt("Search templates...", 12.0, MUTED)).desired_width(f32::INFINITY));
                ScrollArea::vertical().max_height(360.0).show(ui, |ui| {
                    if self.m.templates.is_empty() {
                        ui.label(rt("No templates loaded.", 11.0, MUTED));
                    }
                    for t in &self.m.templates {
                        let name = t["name"].as_str().unwrap_or(t["id"].as_str().unwrap_or("template"));
                        let desc = t["description"].as_str().unwrap_or("");
                        if !self.templates_q.is_empty() && !format!("{name} {desc}").to_lowercase().contains(&self.templates_q.to_lowercase()) {
                            continue;
                        }
                        egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).inner_margin(Margin::same(12)).show(ui, |ui| {
                            ui.set_width(ui.available_width());
                            ui.label(rtb(name, 13.0, TEXT));
                            ui.add(egui::Label::new(rt(desc, 11.0, MUTED)).wrap());
                            if widgets::button(ui, "Apply Template", Kind::Secondary, 12.0, (12.0, 6.0), None).clicked() {
                                apply = Some(if desc.is_empty() { name.to_string() } else { desc.to_string() });
                            }
                        });
                    }
                });
                if widgets::button(ui, "Close", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                    close = true;
                }
            });
            if let Some(t) = apply {
                self.agent_task = t;
                self.m.drawer_tab = "agent";
                self.templates_open = false;
            }
            if close {
                self.templates_open = false;
            }
            return;
        }
        if self.jobs_open {
            let mut close = ctx.input(|i| i.key_pressed(Key::Escape));
            let mut op: Option<(&'static str, String)> = None;
            let mut create = false;
            modal_frame(ctx, "jobs", 640.0, |ui| {
                ui.label(rtb("SCHEDULED JOBS", 13.0, ACCENT));
                ScrollArea::vertical().max_height(220.0).show(ui, |ui| {
                    if self.m.jobs.is_empty() {
                        ui.label(rt("No jobs scheduled.", 11.0, MUTED));
                    }
                    for j in &self.m.jobs {
                        let id = j["job_id"].as_str().or(j["id"].as_str()).unwrap_or("").to_string();
                        egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).inner_margin(Margin::same(10)).show(ui, |ui| {
                            ui.set_width(ui.available_width());
                            ui.label(rtb(j["name"].as_str().unwrap_or("job"), 12.0, TEXT));
                            ui.label(rt(format!("{}  {}", j["status"].as_str().unwrap_or(""), j["task"].as_str().unwrap_or("")), 11.0, MUTED));
                            ui.horizontal(|ui| {
                                if widgets::button(ui, "Run Now", Kind::Secondary, 12.0, (12.0, 6.0), None).clicked() {
                                    op = Some(("run", id.clone()));
                                }
                                if widgets::button(ui, "Cancel", Kind::Danger, 12.0, (12.0, 6.0), None).clicked() {
                                    op = Some(("cancel", id.clone()));
                                }
                            });
                        });
                    }
                });
                ui.label(rtb("NEW JOB", 11.0, MUTED));
                ui.add(egui::TextEdit::singleline(&mut self.job_form.0).hint_text(rt("Job name", 12.0, MUTED)).desired_width(f32::INFINITY));
                ui.add(egui::TextEdit::singleline(&mut self.job_form.1).hint_text(rt("Interval seconds", 12.0, MUTED)).desired_width(f32::INFINITY));
                ui.add(egui::TextEdit::multiline(&mut self.job_form.3).hint_text(rt("Task", 12.0, MUTED)).desired_rows(2).desired_width(f32::INFINITY));
                ui.horizontal(|ui| {
                    if widgets::button(ui, "Schedule", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                        create = true;
                    }
                    if widgets::button(ui, "Close", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        close = true;
                    }
                });
            });
            if let Some(be) = &mut self.be {
                if create {
                    let interval = self.job_form.1.trim().parse::<f64>().unwrap_or(60.0);
                    self.m.job_create(be, &self.job_form.0, interval, &self.job_form.2, &self.job_form.3);
                }
                if let Some((o, id)) = op {
                    self.m.job_op(be, o, &id);
                }
            }
            if close {
                self.jobs_open = false;
            }
        }
    }

    fn shortcuts(&mut self, ctx: &egui::Context) {
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::K)) {
            self.palette = if self.palette.is_some() { None } else { Some((String::new(), 0)) };
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::P)) {
            self.finder = Some((String::new(), 0));
        }
        if self.m.drawer_tab == "files" && ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::S)) {
            self.save_open_file();
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::O)) {
            self.open_folder = Some((self.m.project_path(), String::new()));
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::T)) {
            self.m.new_session();
        }
        for (k, nav) in [(Key::Num1, "chat"), (Key::Num2, "agent"), (Key::Num3, "swarm")] {
            if ctx.input_mut(|i| i.consume_key(Modifiers::ALT, k)) {
                self.m.set_nav(nav);
            }
        }
    }
}

fn style_combo(ui: &mut egui::Ui) {
    let v = &mut ui.style_mut().visuals;
    for w in [&mut v.widgets.inactive, &mut v.widgets.hovered, &mut v.widgets.active, &mut v.widgets.open] {
        w.weak_bg_fill = BG3;
        w.bg_fill = BG3;
        w.bg_stroke = Stroke::new(1.0, BORDER);
        w.corner_radius = cr(12);
    }
    v.widgets.hovered.bg_stroke = Stroke::new(1.0, ACCENT);
}

/// Sidebar and tab marker: pulsing dot while running, amber when it needs an answer, ring when unread.
fn status_dot(ui: &egui::Ui, c: egui::Pos2, status: Status, r: f32) {
    let p = ui.painter();
    match status {
        Status::Running => {
            let t = ui.input(|i| i.time) as f32;
            p.circle_filled(c, r - 1.0 + (t * 4.0).sin().abs(), ACCENT);
        }
        Status::NeedsApproval => {
            p.circle_filled(c, r, WARN);
        }
        Status::Unread => {
            p.circle_stroke(c, r - 0.5, Stroke::new(1.5, ACCENT));
        }
        Status::Idle => {
            p.circle_filled(c, 1.5, MUTED);
        }
    }
}

fn modal_frame(ctx: &egui::Context, id: &str, width: f32, add: impl FnOnce(&mut egui::Ui)) {
    let frame = egui::Frame::new().fill(BG2).stroke(Stroke::new(1.0, ACCENT_BORDER_HOVER)).corner_radius(cr(16)).inner_margin(Margin::same(20));
    egui::Modal::new(egui::Id::new(id)).backdrop_color(OVERLAY).frame(frame).show(ctx, |ui| {
        ui.set_width(width);
        ui.spacing_mut().item_spacing = vec2(8.0, 12.0);
        add(ui);
    });
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
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
        self.drain();
        if let Ok(c) = self.health_rx.try_recv() {
            self.checks = c;
        }
        if self.started < 0.0 {
            self.started = time;
        }
        if self.m.agent_ready && !self.m.any_busy() {
            if let Ok(p) = std::env::var("HYDRA_DEMO_PROMPT") {
                std::env::remove_var("HYDRA_DEMO_PROMPT");
                self.input = p;
                self.send();
            }
        }
        if self.m.agent_ready {
            if let Ok(script) = std::env::var("HYDRA_DEMO_SCRIPT") {
                std::env::remove_var("HYDRA_DEMO_SCRIPT");
                // "index:prompt|index:prompt": start one turn in each listed thread (screenshot and demo hook)
                let keep = self.m.active;
                for part in script.split('|') {
                    if let Some((i, prompt)) = part.split_once(':') {
                        if let Ok(i) = i.parse::<usize>() {
                            if i < self.m.sessions.len() {
                                self.m.select(i);
                                if let Some(be) = &mut self.be {
                                    self.m.submit(be, prompt);
                                }
                            }
                        }
                    }
                }
                self.m.select(keep.min(self.m.sessions.len() - 1));
            }
        }
        let dropped: Vec<PathBuf> = ctx.input(|i| i.raw.dropped_files.iter().map(|f| f.path().to_path_buf()).collect());
        for p in dropped {
            let shown = match p.strip_prefix(&self.work.root) {
                Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
                Err(_) => p.display().to_string(),
            };
            if shown.contains(' ') {
                self.m.notice = format!("cannot attach a path with spaces: {shown}");
                continue;
            }
            if !self.input.is_empty() && !self.input.ends_with(' ') {
                self.input.push(' ');
            }
            self.input.push_str(&format!("@{shown} "));
            self.focus_input = true;
        }
        self.shortcuts(ctx);
        self.work.set_root(PathBuf::from(self.m.work_path()));
        self.work.poll();
        if matches!(self.m.drawer_tab, "files" | "git") {
            self.work.refresh(false);
        }
        let busy_now = self.m.any_busy();
        if self.was_busy && !busy_now {
            self.work.refresh(true);
        }
        self.was_busy = busy_now;
        self.run_queue();
        if self.be.is_some() && (self.m.agent_running || self.m.drawer_tab == "agent") && time - self.last_poll > 0.5 && !self.m.pending.values().any(|p| matches!(p, Pending::AgentStatus)) {
            self.last_poll = time;
            if let Some(be) = &mut self.be {
                self.m.poll_agent(be);
            }
        }
        let total = ctx.content_rect().width();
        let narrow = total < 1180.0;
        let border = Stroke::new(1.0, BORDER);
        let header_h = if narrow { 73.0 } else { LAYOUT_HEADER_HEIGHT };

        egui::Panel::top("header")
            .exact_size(header_h)
            .frame(egui::Frame::new().fill(Color32::from_rgb(8, 8, 8)).inner_margin(Margin::symmetric(24, 0)))
            .show_separator_line(false)
            .show(root, |ui| {
                self.header(ui, time, narrow);
                ui.painter().hline(ui.max_rect().x_range().expand(24.0), ui.max_rect().bottom() - 0.5, border);
            });
        egui::Panel::top("sessions")
            .exact_size(LAYOUT_SESSION_BAR_HEIGHT)
            .frame(egui::Frame::new().fill(BG).inner_margin(Margin::symmetric(20, 0)))
            .show_separator_line(false)
            .show(root, |ui| {
                self.session_bar(ui);
                ui.painter().hline(ui.max_rect().x_range().expand(20.0), ui.max_rect().bottom() - 0.5, border);
            });
        egui::Panel::left("nav")
            .exact_size(LAYOUT_SIDEBAR_WIDTH)
            .resizable(false)
            .show_separator_line(false)
            .frame(egui::Frame::new().fill(BG2).inner_margin(Margin::symmetric(10, 16)))
            .show(root, |ui| {
                self.nav(ui);
                self.projects(ui);
                let r = ui.max_rect();
                ui.painter().vline(r.right() + 10.0 - 0.5, r.y_range(), border);
            });
        let dw = (total * 0.34).min(420.0);
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
            .show_separator_line(false)
            .frame(egui::Frame::new().fill(Color32::from_rgb(8, 8, 8)).inner_margin(Margin::symmetric(24, 16)))
            .show(root, |ui| {
                ui.painter().hline(ui.max_rect().x_range().expand(24.0), ui.max_rect().top() - 15.5, border);
                self.composer(ui);
            });
        egui::CentralPanel::default().frame(egui::Frame::new().fill(BG)).show(root, |ui| {
            self.chat(ui);
        });
        self.modals(ctx);
        let _ = &self.start_error;

        if let Some(plan) = &mut self.shot {
            plan.frames += 1;
            if plan.frames >= std::env::var("HYDRA_SHOT_FRAMES").ok().and_then(|v| v.parse().ok()).unwrap_or(20) && !plan.requested {
                plan.requested = true;
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
            }
            let image = ctx.input(|i| i.events.iter().find_map(|e| if let egui::Event::Screenshot { image, .. } = e { Some(image.clone()) } else { None }));
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
