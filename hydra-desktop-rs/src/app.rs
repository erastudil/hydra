//! The web desk layout in egui: header, session bar, nav, chat, composer, inspection drawer, modals.

use crate::layout;
use crate::motion;
use crate::theme::*;
use crate::widgets::{self, Kind, Part};
use eframe::egui::{self, vec2, Align, Color32, CornerRadius, Key, Layout, Margin, Modifiers, RichText, ScrollArea, Sense, Stroke, StrokeKind};
use crate::backend::{Backend, Ev};
use crate::health::{self, Check};
use crate::secrets;
use crate::model::Status;
use crate::workspace::Work;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use crate::model::*;

pub fn is_supported_attachment(path: &Path) -> bool {
    let Some(ext) = path.extension().and_then(|e| e.to_str()).map(|e| e.to_lowercase()) else {
        return false;
    };
    matches!(
        ext.as_str(),
        // image
        "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp" | "svg" | "ico" | "tiff"
        // audio
        | "mp3" | "wav" | "ogg" | "m4a" | "flac" | "aac"
        // video
        | "mp4" | "mkv" | "webm" | "avi" | "mov"
        // pdf
        | "pdf"
        // text
        | "txt" | "md" | "rs" | "py" | "js" | "ts" | "json" | "toml" | "yaml" | "yml"
        | "c" | "cpp" | "h" | "hpp" | "sh" | "bat" | "ps1" | "html" | "css" | "csv" | "log" | "prg"
    )
}

pub fn format_prompt_with_attachments(input: &str, attachments: &[PathBuf]) -> String {
    let base = if input.trim().is_empty() && !attachments.is_empty() {
        "Inspect attached files."
    } else {
        input.trim()
    };
    if attachments.is_empty() {
        return base.to_string();
    }
    let mut out = base.to_string();
    out.push_str("\n\n[Attachments]");
    for path in attachments {
        out.push_str(&format!("\n- {}", path.display()));
    }
    out
}

pub fn fmt_tokens(n: u64) -> String {
    if n >= 1_000_000 {
        format!("{:.1}M", n as f64 / 1_000_000.0)
    } else if n >= 1_000 {
        format!("{:.0}k", n as f64 / 1_000.0)
    } else {
        n.to_string()
    }
}

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
    Options,
    Memories,
    Settings,
    Permission(&'static str),
    Help,
    Search,
    EditInstructions,
}

/// True when the current agent child has not said hello 20 s after it was spawned. Counts from the latest spawn,
/// so a respawn after a crash is never reported as "not answered after 20 seconds".
pub fn agent_stalled(ready: bool, be_started: f64, app_started: f64, now: f64) -> bool {
    let since = if be_started >= 0.0 { be_started } else { app_started };
    !ready && since >= 0.0 && now - since > 20.0
}

/// Heartbeat repaint while nothing happens.
pub const IDLE_REPAINT_MS: u64 = 30_000;

/// Waits before each automatic agent restart inside one minute (seconds).
pub const RESPAWN_BACKOFF: &[f64] = &[0.5, 2.0, 5.0];

/// Slash commands typed at the start of the composer. Enter runs the command instead of sending a message.
pub const SLASH: &[(&str, &str)] = &[
    ("/new", "New thread"),
    ("/clear", "Clear this chat"),
    ("/plan", "Permissions: plan (read only)"),
    ("/ask", "Permissions: ask every time"),
    ("/accept", "Permissions: accept edits"),
    ("/bypass", "Permissions: bypass approvals"),
    ("/skills", "Pick skills for the next turns"),
    ("/mcp", "MCP servers of this project"),
    ("/tasks", "Background tasks"),
    ("/rules", "Approval rules and checkpoints"),
    ("/rewind", "Checkpoints: rewind files"),
    ("/diff", "Diffs of this thread"),
    ("/agents", "Edit this project's AGENTS.md"),
    ("/search", "Search every chat"),
    ("/settings", "Provider keys"),
    ("/help", "Keyboard shortcuts"),
];

/// Matching slash commands for the composer text, or none when the text is not a bare command.
pub fn slash_matches(input: &str) -> Vec<(&'static str, &'static str)> {
    let t = input.trim_start();
    if !t.starts_with('/') || t.contains(char::is_whitespace) {
        return vec![];
    }
    SLASH.iter().filter(|(c, _)| c.starts_with(t)).copied().collect()
}

fn slash_cmd(name: &str) -> Option<Cmd> {
    Some(match name {
        "/new" => Cmd::NewSession,
        "/clear" => Cmd::Clear,
        "/plan" => Cmd::Permission("plan"),
        "/ask" => Cmd::Permission("manual"),
        "/accept" => Cmd::Permission("accept_edits"),
        "/bypass" => Cmd::Permission("bypass"),
        "/skills" => Cmd::Tab("skills"),
        "/mcp" => Cmd::Tab("mcp"),
        "/tasks" => Cmd::Tab("tasks"),
        "/rules" | "/rewind" => Cmd::Tab("rules"),
        "/diff" => Cmd::Tab("diff"),
        "/agents" => Cmd::EditInstructions,
        "/search" => Cmd::Search,
        "/settings" => Cmd::Settings,
        "/help" => Cmd::Help,
        _ => return None,
    })
}

pub const SHORTCUTS: &[(&str, &str)] = &[
    ("Enter", "Send (queues while a turn runs)"),
    ("Shift+Enter", "New line"),
    ("Ctrl+Enter", "Steer: stop the running turn and send this now"),
    ("/", "Slash commands at the start of the composer"),
    ("@", "Attach a file"),
    ("Ctrl+K", "Command palette"),
    ("Ctrl+Shift+F", "Search every chat"),
    ("Ctrl+P", "Find a file"),
    ("Ctrl+O", "Open a folder"),
    ("Ctrl+T", "New thread"),
    ("Ctrl+B", "Toggle the sidebar"),
    ("Ctrl+S", "Save the open file"),
    ("Ctrl+,", "Settings"),
    ("Alt+1 / 2 / 3", "Chat, Agent, Swarm"),
    ("F1 or Ctrl+/", "This sheet"),
    ("Esc", "Close a dialog"),
];

/// Provider-key form state. Inputs start empty: stored values are never shown, only a masked hint.
pub struct SettingsState {
    inputs: Vec<String>,
    message: String,
    ok: bool,
}

/// One displayed diff row with its line numbers.
pub struct DLine {
    pub kind: u8,
    pub old: Option<u32>,
    pub new: Option<u32>,
    pub text: String,
}

/// Turn a unified diff into numbered rows. File headers are dropped; hunk headers stay as separators.
pub fn parse_diff(diff: &str) -> Vec<DLine> {
    let (mut o, mut n) = (0u32, 0u32);
    let mut out = Vec::new();
    for line in diff.lines() {
        if line.starts_with("+++") || line.starts_with("---") || line.starts_with("diff ") || line.starts_with("index ") {
            continue;
        }
        if let Some(rest) = line.strip_prefix("@@") {
            let nums = |tag: char| rest.split_whitespace().find(|t| t.starts_with(tag)).and_then(|t| t[1..].split(',').next()).and_then(|v| v.parse::<u32>().ok()).unwrap_or(1);
            o = nums('-');
            n = nums('+');
            out.push(DLine { kind: b'@', old: None, new: None, text: line.to_string() });
        } else if let Some(t) = line.strip_prefix('+') {
            out.push(DLine { kind: b'+', old: None, new: Some(n), text: t.to_string() });
            n += 1;
        } else if let Some(t) = line.strip_prefix('-') {
            out.push(DLine { kind: b'-', old: Some(o), new: None, text: t.to_string() });
            o += 1;
        } else {
            let t = line.strip_prefix(' ').unwrap_or(line);
            out.push(DLine { kind: b' ', old: Some(o), new: Some(n), text: t.to_string() });
            o += 1;
            n += 1;
        }
    }
    out
}

fn mode_greeting(nav: &str) -> &'static str {
    match nav {
        "agent" => "Describe a task and send. The agent works through it step by step and shows each tool call.",
        "swarm" => "Send a question. Three heads answer in parallel and a synthesis follows.",
        "computer" => "Capture the screen, then tell the agent what to do on it.",
        "terminal" => "Type a command and send. It runs in the project folder.",
        _ => "Ask anything, pick a model, and send.",
    }
}

/// Long text collapses to a short preview with a "show more" link; returns the shown text and how many lines are hidden.
fn clip_text(text: &str, expanded: bool) -> (&str, usize) {
    const KEEP_LINES: usize = 14;
    const MAX_LINES: usize = 30;
    const KEEP_BYTES: usize = 6000;
    let lines = text.bytes().filter(|b| *b == b'\n').count() + 1;
    if expanded {
        return (text, 0);
    }
    if lines > MAX_LINES {
        let mut end = 0;
        for (i, (idx, _)) in text.match_indices('\n').enumerate() {
            if i + 1 == KEEP_LINES {
                end = idx;
                break;
            }
        }
        let end = floor_boundary(text, end.min(KEEP_BYTES));
        return (&text[..end], lines - KEEP_LINES);
    }
    if text.len() > KEEP_BYTES * 2 {
        let end = floor_boundary(text, KEEP_BYTES);
        return (&text[..end], 1);
    }
    (text, 0)
}

fn floor_boundary(s: &str, mut i: usize) -> usize {
    while i > 0 && !s.is_char_boundary(i) {
        i -= 1;
    }
    i
}

/// Approval text for a tool call: a one-line summary and a bounded preview body (D23: never the whole 5,000-line payload).
pub fn approval_preview(args: &serde_json::Value) -> (String, String) {
    const LINES: usize = 60;
    const BYTES: usize = 6000;
    let clip = |t: &str| -> (String, usize) {
        let total = t.lines().count();
        let mut out: String = t.lines().take(LINES).collect::<Vec<_>>().join("\n");
        if out.len() > BYTES {
            out.truncate(floor_boundary(&out, BYTES));
        }
        (out, total)
    };
    if let Some(content) = args.get("content").and_then(|c| c.as_str()) {
        let path = args.get("path").and_then(|p| p.as_str()).unwrap_or("?");
        let (body, total) = clip(content);
        let more = if total > LINES { format!("\n… {} more lines not shown", total - LINES) } else { String::new() };
        return (format!("{path}: {total} lines, {} bytes", content.len()), body + &more);
    }
    let pretty = serde_json::to_string_pretty(args).unwrap_or_default();
    let (body, total) = clip(&pretty);
    let more = if total > LINES || pretty.len() > body.len() + 1 { "\n… (cut, full arguments in Terminal Logs)".to_string() } else { String::new() };
    (String::new(), body + &more)
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
        ("Desktop Options & Local Models", "Ctrl+,", Cmd::Options),
        ("Settings: provider keys", "", Cmd::Settings),
        ("Memory Ledger & Facts", "", Cmd::Memories),
        ("New Session", "Ctrl+T", Cmd::NewSession),
        ("Clear Chat", "", Cmd::Clear),
        ("Search All Chats", "Ctrl+Shift+F", Cmd::Search),
        ("Keyboard Shortcuts", "F1", Cmd::Help),
        ("Edit AGENTS.md (project instructions)", "/agents", Cmd::EditInstructions),
        ("Open Skills", "/skills", Cmd::Tab("skills")),
        ("Open MCP Servers", "/mcp", Cmd::Tab("mcp")),
        ("Open Background Tasks", "/tasks", Cmd::Tab("tasks")),
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
    heights: HashMap<(u32, usize), f32>,
    heights_width: i32,
    always_prefix: (String, String),
    drawer_user: Option<f32>,
    toast: Option<(String, f64)>,
    settings: Option<SettingsState>,
    settings_auto: bool,
    diff_cache: HashMap<String, (usize, std::sync::Arc<Vec<DLine>>)>,
    expanded: std::collections::HashSet<(u32, usize)>,
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
    pub attachments: Vec<PathBuf>,
    pub options_open: bool,
    pub options_form: (String, String, String, String, String, String), // alias, ollama, llamacpp, local_ai, ctx_win, cwd
    pub memories_open: bool,
    pub memories_entries: Vec<MemoryEntry>,
    pub memories_filter: String,
    pub memories_new_fact: (String, String),
    pub memories_status: Option<String>,
    nav_open: bool,
    last_overlay: Option<bool>,
    births: HashMap<(u32, usize), f64>,
    births_seen: bool,
    open_folder: Option<(String, String)>, // (typed path, error)
    rename: Option<(usize, String)>,
    work: Work,
    finder: Option<(String, usize)>,
    rewind_confirm: Option<usize>,
    queue: HashMap<u32, Vec<String>>,
    mention_sel: usize,
    was_busy: bool,
    probe: Option<Probe>,
    child_died: bool,
    deaths: Vec<f64>,
    respawn_at: Option<std::time::Instant>,
    hello_seen: bool,
    last_exit: String,
    be_started: f64,
    help_open: bool,
    had_events: bool,
    search: Option<String>,
    skills_asked: bool,
    mcp_asked: bool,
    tasks_asked: bool,
}

/// Frame-time probe for the motion check: HYDRA_PROBE=<file> runs a scripted toggle/arrival/palette sequence and writes one CSV row per frame.
struct Probe {
    path: String,
    rows: Vec<(f64, f64, &'static str)>,
    last: f64,
    stage: u8,
    scroll: Option<f32>,
}

struct ShotPlan {
    path: String,
    frames: u32,
    requested: bool,
}

impl HydraApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let default_cwd = std::env::var("USERPROFILE")
            .map(|u| std::path::PathBuf::from(u).join("Documents").display().to_string())
            .unwrap_or_else(|_| ".".into());
        let cwd = std::env::var("HYDRA_CWD").unwrap_or_else(|_| {
            let current = std::env::current_dir().map(|p| p.display().to_string()).unwrap_or_else(|_| ".".into());
            if current.to_lowercase().ends_with("\\hydra") || current.to_lowercase().ends_with("/hydra") {
                default_cwd
            } else {
                current
            }
        });
        let state = if std::env::var("HYDRA_NO_STATE").is_ok() { None } else { Some(state_dir().join("state.json")) };
        Self::build(&cc.egui_ctx, state, cwd, std::env::var("HYDRA_NO_BACKEND").is_err())
    }

    /// Build the app on any egui context. Tests use this with a temp state file and `spawn_backend` as needed.
    pub fn build(ctx0: &egui::Context, state: Option<PathBuf>, cwd: String, spawn_backend: bool) -> Self {
        apply(ctx0);
        motion::install(ctx0);
        let shot = std::env::var("HYDRA_SHOT").ok().map(|path| ShotPlan { path, frames: 0, requested: false });
        let mut m = Model::new(state, cwd);
        let wake_ctx = ctx0.clone();
        let mut work = Work::new(PathBuf::from(m.work_path()), move || wake_ctx.request_repaint());
        let ctx = ctx0.clone();
        let (be, start_error) = match !spawn_backend {
            true => (None, None),
            false => match Backend::spawn(move || ctx.request_repaint()) {
                Ok(b) => (Some(b), None),
                Err(e) => (None, Some(e)),
            },
        };
        if let Some(e) = &start_error {
            m.notice = e.clone();
        }
        // screenshot and demo hooks
        if let Ok(p) = std::env::var("HYDRA_PERMISSION") {
            if PERMISSIONS.iter().any(|x| x.0 == p) {
                m.sessions[m.active].permission = p;
            }
        }
        if let Some(i) = std::env::var("HYDRA_MODEL").ok().and_then(|k| MODELS.iter().position(|x| x.0 == k)) {
            m.model = i;
        }
        if let Ok(n) = std::env::var("HYDRA_NAV") {
            m.set_nav(&n);
        }
        if let Ok(t) = std::env::var("HYDRA_TAB") {
            if let Some(k) = DRAWER_TABS.iter().chain([("files", ""), ("git", ""), ("approvals", ""), ("rules", ""), ("skills", ""), ("mcp", ""), ("tasks", "")].iter()).find(|d| d.0 == t) {
                m.drawer_tab = k.0;
                m.tab_pinned = true;
            }
        }
        if let Ok(f) = std::env::var("HYDRA_FILE") {
            work.open_file(&f);
        }
        if let Ok(f) = std::env::var("HYDRA_SELECT") {
            work.select_change(&f, false);
        }
        let palette = if std::env::var("HYDRA_OPEN").as_deref() == Ok("palette") { Some((String::new(), 0)) } else { None };
        let options_form = (
            m.local_model_alias.clone(),
            m.ollama_host.clone(),
            m.llamacpp_host.clone(),
            m.local_ai_base.clone(),
            m.context_window.to_string(),
            m.cwd.clone(),
        );
        Self {
            m,
            be,
            input: std::env::var("HYDRA_INPUT").unwrap_or_default(), // screenshot hook: preset composer text
            palette,
            templates_open: false,
            templates_q: String::new(),
            jobs_open: false,
            job_form: ("".into(), "60".into(), "full_automation".into(), "".into()),
            agent_task: String::new(),
            agent_steps: 30,
            heights: HashMap::new(),
            heights_width: 0,
            always_prefix: (String::new(), String::new()),
            drawer_user: None,
            toast: None,
            settings: if std::env::var("HYDRA_OPEN").as_deref() == Ok("settings") { Some(SettingsState { inputs: vec![String::new(); secrets::PROVIDERS.len()], message: String::new(), ok: true }) } else { None },
            settings_auto: false,
            diff_cache: HashMap::new(),
            expanded: Default::default(),
            agent_profile: 0,
            type_text: String::new(),
            bounds: false,
            tex: None,
            last_poll: 0.0,
            shot,
            focus_input: std::env::var("HYDRA_SHOT").is_err() || std::env::var("HYDRA_INPUT").is_ok(),
            composer_rect: egui::Rect::NOTHING,
            health_rx: { let (tx, rx) = std::sync::mpsc::channel(); std::thread::spawn(move || { let _ = tx.send(health::run()); }); rx },
            checks: vec![],
            crash_note: crate::crash::take_last_crash().map(|c| c.1),
            panel_dismissed: std::env::var("HYDRA_OPEN").as_deref() == Ok("dismiss"),
            started: -1.0,
            start_error,
            attachments: Vec::new(),
            options_open: false,
            options_form,
            memories_open: false,
            memories_entries: Vec::new(),
            memories_filter: String::new(),
            memories_new_fact: (String::new(), String::new()),
            memories_status: None,
            nav_open: true,
            last_overlay: None,
            births: HashMap::new(),
            births_seen: false,
            child_died: false,
            deaths: vec![],
            respawn_at: None,
            hello_seen: false,
            last_exit: String::new(),
            be_started: -1.0,
            had_events: false,
            help_open: std::env::var("HYDRA_OPEN").as_deref() == Ok("help"),
            search: if std::env::var("HYDRA_OPEN").as_deref() == Ok("search") { Some(std::env::var("HYDRA_SEARCH").unwrap_or_default()) } else { None },
            skills_asked: false,
            mcp_asked: false,
            tasks_asked: false,
            probe: std::env::var("HYDRA_PROBE").ok().map(|path| Probe { path, rows: vec![], last: 0.0, stage: 0, scroll: None }),
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
        // the stall timer counts from this spawn, not from app start (a respawn is not "20 s without an answer")
        self.be_started = ctx.input(|i| i.time);
        self.m.agent_ready = false;
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
            Cmd::Settings => self.open_settings(),
            Cmd::Permission(p) => self.m.set_permission(p),
            Cmd::Help => self.help_open = true,
            Cmd::Search => self.search = Some(String::new()),
            Cmd::EditInstructions => {
                // the agent reads AGENTS.md from the project (docs/STDIO_PROTOCOL.md, context); create it empty if missing
                let path = std::path::Path::new(&self.m.project_path()).join("AGENTS.md");
                if !path.exists() {
                    let _ = std::fs::write(&path, "# Project instructions for Hydra\n\n");
                }
                self.work.open_file("AGENTS.md");
                self.m.drawer_tab = "files";
            }
            Cmd::Jobs => {
                self.jobs_open = true;
                if let Some(be) = &mut self.be {
                    self.m.load_jobs(be);
                }
            }
            Cmd::Options => {
                self.options_form = (
                    self.m.local_model_alias.clone(),
                    self.m.ollama_host.clone(),
                    self.m.llamacpp_host.clone(),
                    self.m.local_ai_base.clone(),
                    self.m.context_window.to_string(),
                    self.m.cwd.clone(),
                );
                self.options_open = true;
            }
            Cmd::Memories => {
                self.memories_entries = read_memory_ledger(&memory_ledger_path());
                self.memories_open = true;
            }
        }
    }

    fn start_agent(&mut self) {
        let task = if self.agent_task.trim().is_empty() { self.input.clone() } else { self.agent_task.clone() };
        if task.trim().is_empty() {
            self.m.notice = "Describe the task first, in the Agent Runner box or the prompt, then start the agent.".into();
            return;
        }
        if let Some(be) = &mut self.be {
            self.m.agent_start(be, &task, self.agent_steps, PROFILES[self.agent_profile]);
        }
    }

    /// A short message at the bottom of the window that fades after a few seconds.
    fn toast_ui(&mut self, ctx: &egui::Context, time: f64) {
        let Some((text, since)) = &mut self.toast else { return };
        if since.is_nan() {
            *since = time;
        }
        let age = time - *since;
        if age > 5.0 {
            self.toast = None;
            return;
        }
        ctx.request_repaint_after(std::time::Duration::from_millis(250));
        egui::Area::new(egui::Id::new("toast")).order(egui::Order::Tooltip).anchor(egui::Align2::CENTER_BOTTOM, vec2(0.0, -96.0)).show(ctx, |ui| {
            egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, ACCENT_BORDER_HOVER)).corner_radius(cr(12)).inner_margin(Margin::symmetric(16, 10)).show(ui, |ui| {
                ui.set_max_width(520.0);
                ui.add(egui::Label::new(rt(text.as_str(), 12.0, TEXT)).wrap());
            });
        });
    }

    fn open_settings(&mut self) {
        self.settings = Some(SettingsState { inputs: vec![String::new(); secrets::PROVIDERS.len()], message: String::new(), ok: true });
    }

    /// Write every filled-in field to the env file, then restart the agent so it picks the keys up.
    fn save_settings(&mut self, ctx: &egui::Context) {
        let Some(st) = &mut self.settings else { return };
        let path = secrets::env_path();
        let mut saved = 0;
        // D22: check every filled field first, so a bad value saves nothing and is reported where it was typed
        for (i, (name, label, _)) in secrets::PROVIDERS.iter().enumerate() {
            let v = st.inputs[i].trim();
            if !v.is_empty() {
                if let Err(e) = secrets::validate(name, v) {
                    st.message = format!("{label}: {e}. Nothing was saved.");
                    st.ok = false;
                    return;
                }
            }
        }
        for (i, (name, label, _)) in secrets::PROVIDERS.iter().enumerate() {
            let v = st.inputs[i].trim().to_string();
            if v.is_empty() {
                continue;
            }
            match secrets::set_in(&path, name, &v) {
                Ok(()) => {
                    saved += 1;
                    st.inputs[i].clear();
                }
                Err(e) => {
                    st.message = format!("{label}: {e}");
                    st.ok = false;
                    return;
                }
            }
        }
        if saved == 0 {
            st.message = "Nothing to save. Paste a key into a field first.".into();
            st.ok = false;
            return;
        }
        st.ok = true;
        st.message = format!("Saved {saved} to {}. Agent restarted.", path.display());
        if self.m.any_busy() {
            st.message = format!("Saved {saved}. Running threads keep the old keys; the agent restarts when you press Retry or relaunch.");
        } else {
            self.retry_backend(ctx);
        }
    }

    fn send(&mut self) {
        let text = format_prompt_with_attachments(&self.input, &self.attachments);
        if let Some(cmd) = slash_cmd(text.trim()) {
            self.input.clear();
            self.attachments.clear();
            self.run(cmd);
            return;
        }
        if self.m.active_busy() && !text.trim().is_empty() && !matches!(self.m.nav, "terminal" | "swarm") {
            let sid = self.m.sessions[self.m.active].id;
            self.queue.entry(sid).or_default().push(text.trim().to_string());
            self.input.clear();
            self.attachments.clear();
            return;
        }
        let Some(be) = &mut self.be else {
            self.m.notice = "agent process is not running".into();
            return;
        };
        if self.m.submit(be, &text) {
            self.input.clear();
            self.attachments.clear();
        }
    }

    fn drain(&mut self) {
        let mut evs = vec![];
        let mut status = None;
        if let Some(be) = &mut self.be {
            while let Ok(e) = be.rx.try_recv() {
                evs.push(e);
            }
            // poll the child too: its death shows even while a process it started holds its stdout open, and the
            // exit status names the cause. After end of file, give the kernel a moment to report that status.
            let eof = evs.iter().any(|e| matches!(e, Ev::Exited(_)));
            for _ in 0..if eof { 20 } else { 1 } {
                if let Some(why) = be.exited() {
                    status = Some(why);
                    break;
                }
                if eof {
                    std::thread::sleep(std::time::Duration::from_millis(10));
                }
            }
        }
        if let Some(why) = status {
            match evs.iter_mut().find(|e| matches!(e, Ev::Exited(_))) {
                Some(e) => *e = Ev::Exited(why),
                None => evs.push(Ev::Exited(why)),
            }
        }
        self.had_events |= !evs.is_empty();
        for e in evs {
            if let Ev::Exited(why) = &e {
                self.child_died = true;
                self.last_exit = why.clone();
            }
            if matches!(e, Ev::Hello { .. }) {
                self.hello_seen = true;
            }
            self.m.apply(e);
        }
        if !self.m.notice.is_empty() && self.m.notice != "jobs-changed" {
            let t = self.m.notice.clone();
            self.m.notice.clear();
            self.toast = Some((t, f64::NAN));
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
            if widgets::button(ui, if narrow { "Palette" } else { "Ctrl+K Palette" }, Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                self.palette = Some((String::new(), 0));
            }
            if widgets::button(ui, if narrow { "Opts" } else { "Options" }, Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                self.run(Cmd::Options);
            }
            if widgets::button(ui, "Clear", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                self.m.clear_active();
            }
            self.model_select(ui, if narrow { 120.0 } else { 309.0 });
            let (dot_live, label) = match (self.m.agent_ready, narrow) {
                (true, false) => (true, "Agent : ready"),
                (false, false) => (false, "Agent : offline"),
                (true, true) => (true, "ready"),
                (false, true) => (false, "offline"),
            };
            widgets::chip_parts(ui, (12.0, 6.0), 8.0, &[Part::Dot(dot_live), Part::Text(label.into(), TYPE_STATUS_PILL, false, TEXT2)], time);
            let cwd_display = if self.m.cwd.len() > 28 {
                let end = &self.m.cwd[self.m.cwd.len() - 25..];
                format!("…{end}")
            } else {
                self.m.cwd.clone()
            };
            widgets::chip_parts(ui, (12.0, 6.0), 8.0, &[Part::Text(format!("cwd: {cwd_display}"), TYPE_STATUS_PILL, false, TEXT2)], time);
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

            let (prompt, comp, win, pct) = self.m.telemetry();
            let telem_parts = if narrow {
                vec![
                    Part::Text(format!("{pct:.1}%"), TYPE_TOKEN_TALLY, true, if pct > 80.0 { ERROR } else { ACCENT }),
                ]
            } else {
                vec![
                    Part::Text("Context:".into(), TYPE_TOKEN_TALLY, false, TEXT2),
                    Part::Text(format!("{}+{}/{}", fmt_tokens(prompt), fmt_tokens(comp), fmt_tokens(win)), TYPE_TOKEN_TALLY, true, TEXT),
                    Part::Text("•".into(), TYPE_TOKEN_TALLY, false, BORDER),
                    Part::Text(format!("{pct:.1}%"), TYPE_TOKEN_TALLY, true, if pct > 80.0 { ERROR } else { ACCENT }),
                ]
            };
            widgets::chip_parts(ui, (14.0, 6.0), 8.0, &telem_parts, time);
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
        let mut toggle_nav = false;
        let (mut rename, mut pin) = (None, None);
        let mut wt: Option<(usize, bool)> = None;
        let visible = self.m.visible();
        let overlay = self.last_overlay == Some(true);
        ui.horizontal_centered(|ui| {
            if overlay && widgets::button(ui, "Menu", Kind::Secondary, 12.0, (12.0, 5.0), None).clicked() {
                toggle_nav = true;
            }
            for &i in &visible {
                let s = &self.m.sessions[i];
                let status = self.m.status(i);
                let active = i == self.m.active;
                let w = ui.fonts_mut(|f| f.layout_no_wrap(s.name.clone(), egui::FontId::new(12.0, egui::FontFamily::Proportional), TEXT).size().x);
                let dot_w = if status == Status::Idle && !s.pinned { 0.0 } else { 14.0 };
                let (rect, resp) = ui.allocate_exact_size(vec2(14.0 * 2.0 + w + 16.0 + dot_w, 29.0), Sense::click());
                let t = motion::toggle(ui.ctx(), resp.id.with("hv"), resp.hovered() && !active);
                let rect = rect.translate(vec2(0.0, -t));
                if active {
                    widgets::shadow(ui, rect, 12, SHADOW_TAB_ACTIVE_GLOW);
                }
                ui.painter().rect_filled(rect, cr(12), if active { ACCENT_DIM } else { BG2 });
                ui.painter().rect_stroke(rect, cr(12), Stroke::new(1.0, if active { ACCENT } else { motion::mix(BORDER, ACCENT_BORDER_HOVER, t) }), StrokeKind::Inside);
                if dot_w > 0.0 {
                    status_dot(ui, rect.left_center() + vec2(15.0, 0.0), status, 4.0);
                }
                let fam = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
                ui.painter().text(rect.left_center() + vec2(14.0 + dot_w, 0.0), egui::Align2::LEFT_CENTER, &s.name, egui::FontId::new(TYPE_SESSION_TAB, fam), if active { ACCENT } else { motion::mix(MUTED, TEXT, t) });
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
        if toggle_nav {
            self.nav_open = !self.nav_open;
        }
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
            let t = motion::toggle(ui.ctx(), resp.id.with("hv"), resp.hovered() && !active);
            let rect = rect.translate(vec2(3.0 * t, 0.0));
            resp.widget_info(|| egui::WidgetInfo::selected(egui::WidgetType::SelectableLabel, true, active, *label));
            if active {
                ui.painter().rect_filled(rect, cr(12), ACCENT_DIM);
                ui.painter().rect_filled(egui::Rect::from_min_size(rect.min + vec2(0.0, 6.0), vec2(3.0, rect.height() - 12.0)), cr(1), ACCENT);
            } else if t > 0.0 {
                ui.painter().rect_filled(rect, cr(12), BG3.gamma_multiply(t));
            }
            let family = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
            let color = if active { ACCENT } else { motion::mix(MUTED, TEXT, t) };
            ui.painter().text(rect.left_center() + vec2(14.0, 0.0), egui::Align2::LEFT_CENTER, label, egui::FontId::new(TYPE_NAV_ITEM, family), color);
            if resp.clicked() {
                self.m.set_nav(key);
                if self.last_overlay == Some(true) {
                    self.nav_open = false;
                }
            }
        }
    }

    /// Slide-over nav for narrow windows. Opens from the Menu chip or Ctrl+B, closes on pick or outside click.
    fn nav_overlay(&mut self, ctx: &egui::Context, fr: layout::Frame, t: f32) {
        if !fr.overlay_nav || t <= 0.001 {
            return;
        }
        let top = fr.header_height + LAYOUT_SESSION_BAR_HEIGHT;
        let h = ctx.content_rect().height() - top;
        let x = -LAYOUT_SIDEBAR_WIDTH * (1.0 - t);
        let area = egui::Area::new(egui::Id::new("nav-overlay")).order(egui::Order::Foreground).fixed_pos(egui::pos2(x, top)).show(ctx, |ui| {
            let rect = egui::Rect::from_min_size(ui.max_rect().min, vec2(LAYOUT_SIDEBAR_WIDTH, h));
            widgets::shadow(ui, rect, 0, SHADOW_CARD);
            ui.painter().rect_filled(rect, 0, BG2);
            ui.painter().vline(rect.right() - 0.5, rect.y_range(), Stroke::new(1.0, BORDER));
            ui.scope_builder(egui::UiBuilder::new().max_rect(rect.shrink2(vec2(10.0, 16.0))), |ui| {
                self.nav(ui);
                self.projects(ui);
            });
            rect
        });
        let clicked_out = ctx.input(|i| i.pointer.any_click()) && ctx.pointer_interact_pos().map(|p| !area.inner.contains(p)).unwrap_or(false);
        if clicked_out && self.nav_open {
            self.nav_open = false;
        }
    }

    // ---------------------------------------------------------------- chat
    /// One line under the session bar that says what the current mode does and shows its live state.
    fn mode_banner(&mut self, ui: &mut egui::Ui) {
        let (title, blurb) = match self.m.nav {
            "agent" => ("AGENT REACT LOOP", "Give it a task. It plans, calls tools and reports every step in this thread. Stop ends it."),
            "swarm" => ("3-HEADED SWARM", "Three models answer the same prompt in parallel, then a fourth merges them into one reply."),
            "computer" => ("COMPUTER USE", "The agent looks at the screen, then clicks and types. Capture a frame to see what it sees."),
            "terminal" => ("TERMINAL SANDBOX", "Every message is a shell command run in the project folder. Output comes back here."),
            _ => return,
        };
        let mut capture = false;
        egui::Frame::new().fill(BG2).inner_margin(Margin::symmetric(24, 8)).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                ui.label(rtb(title, 11.0, ACCENT));
                ui.label(rt(blurb, 11.0, MUTED));
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| match self.m.nav {
                    "agent" => {
                        let st = &self.m.agent_status;
                        let state = st["status"].as_str().unwrap_or("IDLE").to_uppercase();
                        let step = st["current_step"].as_u64().unwrap_or(0);
                        let max = if state == "IDLE" { self.agent_steps as u64 } else { st["max_steps"].as_u64().unwrap_or(self.agent_steps as u64) };
                        ui.label(rt(format!("{state}  step {step}/{max}"), 11.0, if state == "RUNNING" { ACCENT } else { MUTED }));
                    }
                    "computer" => {
                        if widgets::button(ui, "Capture Screen", Kind::Secondary, 11.0, (10.0, 3.0), None).clicked() {
                            capture = true;
                        }
                        ui.label(rt(format!("Active window: {}", self.m.window_title), 11.0, TEXT2));
                    }
                    "terminal" => {
                        ui.label(rt(self.m.work_path(), 11.0, TEXT2));
                    }
                    _ => {}
                });
            });
        });
        if capture {
            if let Some(be) = &mut self.be {
                self.m.capture(be);
            }
        }
    }

    // ---------------------------------------------------------------- chat
    fn chat(&mut self, ui: &mut egui::Ui) {
        self.mode_banner(ui);
        self.error_banner(ui);
        let now = ui.input(|i| i.time);
        let sid = self.m.sessions[self.m.active].id;
        let count = self.m.sessions[self.m.active].messages.len();
        // messages present when a session is first drawn appear at once; later ones fade in
        for i in 0..count {
            let first_view = !self.births_seen;
            self.births.entry((sid, i)).or_insert(if first_view || motion::reduced_motion() { -1.0 } else { now });
        }
        self.births_seen = true;
        if count == 0 {
            self.empty_state(ui, "No messages yet", "Ask anything, pick a model, and send. Ctrl+K opens the command palette.");
            return;
        }
        let mut rewind_pick: Option<usize> = None;
        let mut toggle: Option<(u32, usize)> = None;
        let nav = self.m.nav;
        let terminal = nav == "terminal";
        let busy = self.m.active_busy();
        let width_key = ui.available_width() as i32;
        if self.heights_width != width_key {
            self.heights.clear();
            self.heights_width = width_key;
        }
        let mut sa = ScrollArea::vertical().auto_shrink([false, false]).stick_to_bottom(true);
        if let Some(y) = self.probe.as_ref().and_then(|p| p.scroll) {
            sa = sa.vertical_scroll_offset(y);
        }
        sa.show_viewport(ui, |ui, viewport| {
            ui.spacing_mut().item_spacing = vec2(0.0, 0.0);
            egui::Frame::new().inner_margin(Margin::same(24)).show(ui, |ui| {
                ui.set_width(ui.available_width());
                let content_w = ui.available_width();
                let max_w = (content_w * RESP_MESSAGE_MAX_PCT).floor();
                let mut y = 24.0f32;
                let msgs = &self.m.sessions[self.m.active].messages;
                let n = msgs.len();
                for (mi, m) in msgs.iter().enumerate() {
                    // Only what is near the viewport is laid out; everything else is a spacer of its measured height.
                    let known = self.heights.get(&(sid, mi)).copied();
                    let guess = known.unwrap_or(96.0);
                    let near = y + guess >= viewport.min.y - 500.0 && y <= viewport.max.y + 500.0;
                    if !near || (mi + 1 < n && known.is_none() && y > viewport.max.y + 500.0) {
                        ui.add_space(guess);
                        y += guess;
                        continue;
                    }
                    let start = ui.cursor().top();
                    let born = self.births.get(&(sid, mi)).copied().unwrap_or(-1.0);
                    let p = motion::arrival(now, born);
                    if p < 1.0 {
                        ui.ctx().request_repaint();
                    }
                    ui.scope(|ui| {
                        ui.set_opacity(p);
                        ui.add_space(MOTION_FADE_FROM_Y * (1.0 - p));
                        let layout = if m.user { Layout::top_down(Align::Max) } else { Layout::top_down(Align::Min) };
                        ui.with_layout(layout, |ui| {
                            ui.spacing_mut().item_spacing = vec2(0.0, 8.0);
                            let greeting = nav != "chat" && !m.user && m.text == "Ask anything, pick a model, and send.";
                            let raw: &str = if greeting { mode_greeting(nav) } else { &m.text };
                            if !raw.is_empty() || m.actions.is_empty() {
                                ui.scope(|ui| {
                                    ui.set_max_width(max_w);
                                    ui.with_layout(Layout::top_down(Align::Min), |ui| {
                                        ui.spacing_mut().item_spacing = vec2(0.0, 8.0);
                                        ui.label(rt(m.header.to_uppercase(), TYPE_MSG_HEADER, MUTED).extra_letter_spacing(0.3));
                                        let (fill, stroke, radius) = if m.user && !terminal {
                                            (USER_BUBBLE, Stroke::new(1.0, USER_BUBBLE_BORDER), CornerRadius { nw: 16, ne: 16, sw: 16, se: 4 })
                                        } else if terminal {
                                            (BG, Stroke::new(1.0, TERMINAL_BORDER), cr(10))
                                        } else {
                                            (BG2, Stroke::new(1.0, BORDER), CornerRadius { nw: 16, ne: 16, sw: 4, se: 16 })
                                        };
                                        let expanded = self.expanded.contains(&(sid, mi));
                                        let (shown, hidden) = clip_text(raw, expanded);
                                        let slot = ui.painter().add(egui::Shape::Noop);
                                        let r = egui::Frame::new().fill(fill).stroke(stroke).corner_radius(radius).inner_margin(Margin::symmetric(18, 14)).show(ui, |ui| {
                                            ui.set_max_width(max_w - 38.0);
                                            let text = if terminal {
                                                let t = if m.user { format!("$ {shown}") } else { shown.to_string() };
                                                RichText::new(t).size(TYPE_TERMINAL).family(egui::FontFamily::Monospace).color(if m.user { ACCENT } else { TEXT })
                                            } else {
                                                rt(shown, TYPE_MESSAGE_BODY, TEXT).line_height(Some(TYPE_MESSAGE_BODY * 1.6))
                                            };
                                            ui.add(egui::Label::new(text).wrap());
                                            if hidden > 0 || expanded {
                                                let label = if expanded { "Show less".to_string() } else { format!("Show {hidden} more lines") };
                                                if ui.add(egui::Label::new(rt(label, 11.0, ACCENT)).sense(Sense::click())).clicked() {
                                                    toggle = Some((sid, mi));
                                                }
                                            }
                                        });
                                        ui.painter().set(slot, widgets::shadow_shape(r.response.rect, 16, SHADOW_CARD));
                                    });
                                });
                            }
                            for a in &m.actions {
                                action_row(ui, a, content_w);
                            }
                            if m.user && !busy && self.m.checkpoint_for(mi).is_some() && ui.add(egui::Label::new(rt("‹ Rewind files to before this message", 11.0, MUTED)).sense(Sense::click())).on_hover_text("Restore the project to how it was before this message").clicked() {
                                rewind_pick = self.m.checkpoint_for(mi);
                            }
                        });
                    });
                    ui.add_space(20.0);
                    let h = ui.cursor().top() - start;
                    self.heights.insert((sid, mi), h);
                    y += h;
                }
            });
        });
        if let Some(k) = toggle {
            if !self.expanded.remove(&k) {
                self.expanded.insert(k);
            }
            self.heights.remove(&k);
        }
        if rewind_pick.is_some() {
            self.rewind_confirm = rewind_pick;
        }
    }

    /// Shown while the agent process is not running (it failed to start or has exited), never for a deliberate HYDRA_NO_BACKEND run.
    fn error_banner(&self, ui: &mut egui::Ui) {
        let Some(err) = self.start_error.as_ref().filter(|e| self.be.is_none() && e.as_str() != "backend disabled") else { return };
        egui::Frame::new().inner_margin(Margin { left: 24, right: 24, top: 16, bottom: 0 }).show(ui, |ui| {
            let w = ui.available_width();
            let slot = ui.painter().add(egui::Shape::Noop);
            let r = egui::Frame::new().fill(ERROR_DIM).stroke(Stroke::new(1.0, ERROR)).corner_radius(cr(12)).inner_margin(Margin::symmetric(16, 12)).show(ui, |ui| {
                ui.set_width(w - 34.0);
                ui.spacing_mut().item_spacing.y = 6.0;
                ui.label(rtb("Agent offline", TYPE_TOOL_TITLE, ERROR));
                ui.add(egui::Label::new(rt(format!("{err}. Messages are kept, but nothing answers until the agent process starts."), TYPE_TOOL_CARD, TEXT)).wrap());
            });
            let rr = r.response.rect;
            ui.painter().set(slot, widgets::shadow_shape(rr, 12, SHADOW_TOOL_CARD));
            ui.painter().rect_filled(egui::Rect::from_min_size(rr.min + vec2(0.0, 6.0), vec2(3.0, rr.height() - 12.0)), cr(1), ERROR);
        });
    }

    /// Dashed placeholder panel with a title and one line of guidance.
    fn empty_state(&self, ui: &mut egui::Ui, title: &str, hint: &str) {
        let avail = ui.available_rect_before_wrap();
        let w = (avail.width() - 48.0).clamp(160.0, 420.0);
        let rect = egui::Rect::from_center_size(avail.center() - vec2(0.0, 20.0), vec2(w, 108.0));
        widgets::dashed_rounded(ui, rect.shrink(0.5), RADIUS_LG as f32, Stroke::new(1.0, BORDER));
        ui.scope_builder(egui::UiBuilder::new().max_rect(rect.shrink(24.0)).layout(Layout::top_down(Align::Center)), |ui| {
            ui.spacing_mut().item_spacing.y = 8.0;
            ui.label(rtb(title, 13.0, TEXT2));
            ui.add(egui::Label::new(rt(hint, 12.0, MUTED)).wrap());
        });
    }

    // ---------------------------------------------------------------- composer
    fn composer(&mut self, ui: &mut egui::Ui) {
        if !self.attachments.is_empty() {
            ui.horizontal_wrapped(|ui| {
                let mut remove_idx = None;
                for (i, p) in self.attachments.iter().enumerate() {
                    let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("file");
                    let kind_icon = match p.extension().and_then(|e| e.to_str()).map(|e| e.to_lowercase()).as_deref() {
                        Some("png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp" | "svg" | "ico" | "tiff") => "🖼",
                        Some("mp3" | "wav" | "ogg" | "m4a" | "flac" | "aac") => "🎵",
                        Some("mp4" | "mkv" | "webm" | "avi" | "mov") => "🎬",
                        Some("pdf") => "📄",
                        _ => "📝",
                    };
                    ui.horizontal(|ui| {
                        ui.label(rt(&format!("{kind_icon} {name}"), 11.0, ACCENT));
                        if ui.small_button("×").clicked() {
                            remove_idx = Some(i);
                        }
                    });
                }
                if let Some(i) = remove_idx {
                    self.attachments.remove(i);
                }
            });
            ui.add_space(4.0);
        }
        ui.spacing_mut().item_spacing.x = 12.0;
        let mut do_send = false;
        let mut do_stop = false;
        let mut do_steer = false;
        ui.horizontal(|ui| {
            let send_w = 62.0;
            let width = ui.available_width() - send_w - 12.0;
            let id = egui::Id::new("prompt");
            let focused = ui.memory(|m| m.has_focus(id));
            // Enter sends; Shift+Enter (and Alt+Enter) fall through to the text box as a newline
            if focused && ui.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::Enter)) {
                do_steer = true;
            }
            if focused && !self.slash_menu(ui) && ui.input_mut(|i| !i.modifiers.shift && !i.modifiers.alt && i.consume_key(Modifiers::NONE, Key::Enter)) {
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
        let (mut cycle, mut undo, mut cycle_ctx) = (false, false, false);
        ui.horizontal(|ui| {
            let perm = self.m.sessions[self.m.active].permission.clone();
            let label = PERMISSIONS.iter().find(|p| p.0 == perm).map(|p| p.1).unwrap_or("Ask every time");
            let _ = &mut cycle;
            let mut pick: Option<&str> = None;
            egui::containers::menu::MenuButton::from_button(egui::Button::new(rtb(format!("Permissions: {label} ▾"), 11.0, TEXT)).fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(10)))
                .ui(ui, |ui| {
                    for (key, name) in PERMISSIONS {
                        let note = match *key {
                            "manual" => "asks before every tool call",
                            "accept_edits" => "file edits run, everything else asks",
                            "plan" => "read only, other tools are skipped",
                            _ => "nothing asks",
                        };
                        if ui.selectable_label(perm == *key, format!("{name}  ·  {note}")).clicked() {
                            pick = Some(key);
                        }
                    }
                });
            if let Some(p) = pick {
                self.m.set_permission(p);
            }
            let ctxm = self.m.sessions[self.m.active].context.clone();
            let (clabel, cnote) = CONTEXT_MODES.iter().find(|c| c.0 == ctxm).map(|c| (c.0, c.1)).unwrap_or(("recall", ""));
            if widgets::button(ui, &format!("Context: {}", clabel), Kind::Secondary, 11.0, (10.0, 3.0), None).on_hover_text(format!("{cnote}\nClick to change how earlier turns are carried forward")).clicked() {
                cycle_ctx = true;
            }
            if self.m.last_prompt > 0 {
                let k = self.m.last_prompt as f64 / 1000.0;
                ui.label(rt(format!("ctx {k:.1}k"), 11.0, if self.m.last_prompt > 100_000 { WARN } else { MUTED })).on_hover_text("Tokens the model read on the last request of this app (prompt plus carried context)");
            }
            // D26: Undo rewind comes before the hint text, which truncates, so the button is never cut off
            if self.m.sessions[self.m.active].pre_rewind.is_some() && !self.m.active_busy() && widgets::button(ui, "Undo rewind", Kind::Secondary, 11.0, (10.0, 3.0), None).clicked() {
                undo = true;
            }
            let q = self.queue.get(&self.m.sessions[self.m.active].id).map(|q| q.len()).unwrap_or(0);
            if q > 0 {
                ui.add(egui::Label::new(rt(format!("{q} queued, sends when this turn ends"), 11.0, ACCENT)).truncate());
            } else if self.m.active_busy() {
                ui.label(rt("Enter queues a follow-up", 11.0, MUTED));
            } else {
                ui.add(egui::Label::new(rt("@ attaches a file   Ctrl+P finds files   Ctrl+O opens a folder", 11.0, MUTED)).truncate());
            }
        });
        if cycle {
            self.m.cycle_permission();
        }
        if cycle_ctx {
            self.m.cycle_context();
        }
        if undo {
            if let Err(e) = self.m.undo_rewind(self.be.as_mut()) {
                self.m.notice = e;
            }
        }
        self.mention_popup(ui);
        if do_stop {
            if let Some(be) = &mut self.be {
                self.m.stop(be);
            }
        }
        if do_steer {
            self.steer();
        }
        if do_send {
            self.send();
        }
    }

    /// Ctrl+Enter while a turn runs: stop it and send this text first; when idle it is a normal send.
    fn steer(&mut self) {
        let text = self.input.trim().to_string();
        if text.is_empty() {
            return;
        }
        if !self.m.active_busy() {
            return self.send();
        }
        let sid = self.m.sessions[self.m.active].id;
        self.queue.entry(sid).or_default().insert(0, text);
        self.input.clear();
        if let Some(be) = &mut self.be {
            self.m.stop(be);
        }
        self.toast = Some(("Steering: the running turn was stopped, your message goes next.".into(), f64::NAN));
    }

    /// Popup of slash commands above the composer. Tab completes the highlighted one. Returns true if it took Enter.
    fn slash_menu(&mut self, ui: &mut egui::Ui) -> bool {
        let hits = slash_matches(&self.input);
        if hits.is_empty() {
            return false;
        }
        let sel = self.mention_sel.min(hits.len() - 1);
        if ui.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Tab)) {
            self.input = hits[sel].0.to_string();
        }
        let exact = hits.iter().any(|h| h.0 == self.input.trim());
        let anchor = self.composer_rect.left_top() - vec2(0.0, 8.0);
        egui::Area::new(egui::Id::new("slash_menu")).order(egui::Order::Foreground).fixed_pos(anchor - vec2(0.0, 26.0 * hits.len() as f32 + 12.0)).show(ui.ctx(), |ui| {
            egui::Frame::new().fill(BG2).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(10)).inner_margin(Margin::same(6)).show(ui, |ui| {
                for (i, (c, d)) in hits.iter().enumerate() {
                    ui.label(rt(format!("{c}   {d}"), 12.0, if i == sel { ACCENT } else { TEXT2 }));
                }
            });
        });
        // Enter on a partial command completes it; on a full command the normal send path runs it
        if !exact && ui.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Enter)) {
            self.input = hits[sel].0.to_string();
            return true;
        }
        false
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
        if let Some((dirty, rel, error)) = self.work.open.as_ref().map(|f| (f.dirty(), f.rel.clone(), f.error.clone())) {
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
                self.work.note = match crate::git::init(&self.work.root) {
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
        let section = |ui: &mut egui::Ui, title: &str, staged: bool, sel: &mut Option<(String, bool)>, act: &mut Option<String>| {
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
        let Some(prefix) = crate::fsx::mention_prefix(&self.input).map(|p| p.to_string()) else { return };
        let hits: Vec<String> = crate::fsx::fuzzy(&prefix, &self.work.index, 6).into_iter().map(String::from).collect();
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
        ui.spacing_mut().item_spacing.y = 0.0;
        egui::Frame::new().inner_margin(Margin::symmetric(20, 14)).show(ui, |ui| {
            ui.set_width(width - 41.0);
            let (rect, _) = ui.allocate_exact_size(vec2(width - 41.0, 14.0), Sense::hover());
            let bold = egui::FontId::new(TYPE_DRAWER_HEADER, egui::FontFamily::Name(BOLD.into()));
            ui.painter().text(rect.left_center(), egui::Align2::LEFT_CENTER, "INSPECTION & AUTOMATION", bold, TEXT2);
            ui.painter().text(rect.right_center(), egui::Align2::RIGHT_CENTER, "X: - | Y: -", egui::FontId::new(TYPE_COORD_READOUT, egui::FontFamily::Proportional), MUTED);
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
                    let h: f32 = if wrapped { 17.0 * 2.0 + 16.0 } else { 30.0 };
                    let (rect, resp) = ui.allocate_exact_size(vec2(cell, h), Sense::click());
                    resp.widget_info(|| egui::WidgetInfo::selected(egui::WidgetType::SelectableLabel, true, active, *label));
                    let t = motion::toggle(ui.ctx(), resp.id.with("hv"), resp.hovered() && !active);
                    if active {
                        widgets::shadow(ui, rect, 10, SHADOW_DRAWER_TAB_GLOW);
                        ui.painter().rect_filled(rect, cr(10), ACCENT_DIM);
                    } else if t > 0.0 {
                        ui.painter().rect_filled(rect, cr(10), BG3.gamma_multiply(t));
                    }
                    let fam = if active { egui::FontFamily::Name(BOLD.into()) } else { egui::FontFamily::Proportional };
                    ui.painter().text(rect.center(), egui::Align2::CENTER_CENTER, text, egui::FontId::new(TYPE_DRAWER_TAB, fam), if active { ACCENT } else { motion::mix(MUTED, TEXT, t) });
                    if resp.clicked() {
                        self.m.drawer_tab = key;
                    }
                }
            });
        });
        egui::Frame::new().fill(BG).inner_margin(Margin::symmetric(8, 2)).show(ui, |ui| {
            ui.set_width(width - 17.0);
            ui.spacing_mut().item_spacing = vec2(4.0, 0.0);
            for row in [&[("files", "Files"), ("git", "Changes"), ("approvals", "Approvals"), ("rules", "Rules")][..], &[("skills", "Skills"), ("mcp", "MCP"), ("tasks", "Tasks")][..]] {
            let n = row.len() as f32;
            ui.horizontal(|ui| {
                // D16: on a narrow drawer the long names ran together; use short ones when a cell is too small
                let tight = (width - 17.0 - 12.0) / n - 4.0 < 96.0;
                for &(key, label) in row {
                    let active = self.m.drawer_tab == key;
                    let label = if tight { match key { "git" => "Git", "approvals" => "Asks", _ => label } } else { label };
                    let mut text = label.to_string();
                    if key == "approvals" && !self.m.approvals.is_empty() {
                        text = format!("{label} ({})", self.m.approvals.len());
                    }
                    if key == "tasks" && self.m.running_tasks() > 0 {
                        text = format!("Tasks ({})", self.m.running_tasks());
                    }
                    if key == "skills" && !self.m.picked_skills.is_empty() {
                        text = format!("Skills ({})", self.m.picked_skills.len());
                    }
                    if key == "git" {
                        if let Some(st) = &self.work.status {
                            if !st.changes.is_empty() {
                                text = format!("{label} ({})", st.changes.len());
                            }
                        }
                    }
                    let (rect, resp) = ui.allocate_exact_size(vec2((width - 17.0 - 12.0) / n - 4.0, 26.0), Sense::click());
                    resp.widget_info(|| egui::WidgetInfo::selected(egui::WidgetType::SelectableLabel, true, active, text.clone()));
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
            }
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
                    "approvals" => self.approvals_tab(ui),
                    "rules" => self.rules_tab(ui),
                    "skills" => self.skills_tab(ui),
                    "mcp" => self.mcp_tab(ui),
                    "tasks" => self.tasks_tab(ui),
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

    /// Saved "always allow" rules for this thread (and its project), plus this thread's checkpoints with Rewind.
    fn skills_tab(&mut self, ui: &mut egui::Ui) {
        if !self.skills_asked {
            self.skills_asked = true;
            if let Some(be) = &mut self.be {
                self.m.pending_skills(be);
            }
        }
        ui.horizontal(|ui| {
            ui.label(rtb("SKILLS", 11.0, TEXT2));
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                if widgets::button(ui, "Reload", Kind::Secondary, 11.0, (8.0, 2.0), None).clicked() {
                    if let Some(be) = &mut self.be {
                        self.m.pending_skills(be);
                    }
                }
            });
        });
        if self.m.skills.is_empty() {
            ui.label(rt("No skills found. Picked skills add their instructions to each turn you send until you untick them.", 11.0, MUTED));
        }
        let mut toggle = None;
        for sk in &self.m.skills {
            let id = sk["id"].as_str().unwrap_or("").to_string();
            let mut on = self.m.picked_skills.contains(&id);
            ui.horizontal(|ui| {
                if ui.checkbox(&mut on, rt(&id, 12.0, TEXT)).changed() {
                    toggle = Some(id.clone());
                }
                ui.label(rt(sk["title"].as_str().unwrap_or(""), 11.0, MUTED));
            });
        }
        if let Some(id) = toggle {
            self.m.toggle_skill(&id);
        }
    }

    fn mcp_tab(&mut self, ui: &mut egui::Ui) {
        let mut refresh = None;
        if !self.mcp_asked {
            self.mcp_asked = true;
            refresh = Some(false);
        }
        ui.horizontal(|ui| {
            ui.label(rtb("MCP SERVERS", 11.0, TEXT2));
            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                if widgets::button(ui, "Refresh", Kind::Secondary, 11.0, (8.0, 2.0), None).clicked() {
                    refresh = Some(true);
                }
            });
        });
        if let (Some(r), Some(be)) = (refresh, &mut self.be) {
            let cwd = self.m.cwd.clone();
            self.m.pending_mcp(be, &cwd, r);
        }
        let mut toggle: Option<(String, bool)> = None;
        match &self.m.mcp {
            None => {
                ui.label(rt("Asking the agent…", 11.0, MUTED));
            }
            Some((config, servers)) => {
                ui.label(rt(format!("config: {}", config.as_deref().unwrap_or("none (.hydra/mcp_servers.json or ~/.hydra/mcp_servers.json)")), 11.0, MUTED));
                for sv in servers {
                    let ok = sv["ok"].as_bool().unwrap_or(false);
                    let mut on = sv["enabled"].as_bool().unwrap_or(true);
                    let name = sv["name"].as_str().unwrap_or("?").to_string();
                    ui.horizontal(|ui| {
                        if ui.checkbox(&mut on, "").on_hover_text("Use this server in this project").changed() {
                            toggle = Some((name.clone(), on));
                        }
                        let (dot, col) = if !on { ("○", MUTED) } else if ok { ("●", ACCENT) } else { ("×", ERROR) };
                        ui.label(rt(dot, 12.0, col));
                        ui.label(rtb(&name, 12.0, if on { TEXT } else { MUTED }));
                        let tools = sv["tools"].as_array().map(|a| a.len()).unwrap_or(0);
                        let what = if !on { "off for this project".to_string() } else if ok { format!("{tools} tools") } else { sv["error"].as_str().unwrap_or("failed").to_string() };
                        ui.label(rt(what, 11.0, MUTED));
                    });
                }
            }
        }
        if let (Some((server, on)), Some(be)) = (toggle, &mut self.be) {
            let cwd = self.m.cwd.clone();
            self.m.toggle_mcp(be, &cwd, &server, on);
        }
    }

    fn tasks_tab(&mut self, ui: &mut egui::Ui) {
        if !self.tasks_asked {
            self.tasks_asked = true;
            if let Some(be) = &mut self.be {
                self.m.pending_tasks(be);
            }
        }
        ui.label(rtb("BACKGROUND TASKS", 11.0, TEXT2));
        if self.m.tasks.is_empty() {
            ui.label(rt("No background tasks. Tasks the agent starts keep running after the turn and show up here.", 11.0, MUTED));
        }
        let mut stop = None;
        for (id, t) in self.m.tasks.iter().rev() {
            let state = t["state"].as_str().unwrap_or("?");
            ui.horizontal(|ui| {
                ui.label(rt(if state == "running" { "●" } else { "○" }, 12.0, if state == "running" { ACCENT } else { MUTED }));
                ui.label(egui::RichText::new(t["command"].as_str().unwrap_or("")).monospace().size(12.0).color(TEXT));
                let tail = match t["exit_code"].as_i64() {
                    Some(c) => format!("{state} ({c})"),
                    None => state.to_string(),
                };
                ui.label(rt(tail, 11.0, MUTED));
                if t["adopted"].as_bool().unwrap_or(false) {
                    ui.label(rt("adopted", 11.0, WARN)).on_hover_text("Started by an earlier agent process; this one took it over after a restart");
                }
                if state == "running" {
                    ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                        if widgets::button(ui, "Stop", Kind::Danger, 11.0, (8.0, 2.0), None).clicked() {
                            stop = Some(id.clone());
                        }
                    });
                }
            });
        }
        if let (Some(id), Some(be)) = (stop, &mut self.be) {
            self.m.stop_task(be, &id);
        }
    }

    fn rules_tab(&mut self, ui: &mut egui::Ui) {
        let thread = self.m.sessions[self.m.active].thread.clone();
        if self.m.rules.0 != thread {
            self.m.rules = (thread.clone(), vec![]);
            if let Some(be) = &mut self.be {
                self.m.load_rules(be);
            }
        }
        ui.label(rtb("APPROVAL RULES", 11.0, TEXT2));
        let mut remove: Option<String> = None;
        if self.m.rules.1.is_empty() {
            ui.label(rt("No saved rules. Choose \"Always in thread\" or \"Always in project\" on an approval to add one.", 11.0, MUTED));
        }
        for r in &self.m.rules.1 {
            let id = r["id"].as_str().unwrap_or("").to_string();
            ui.horizontal(|ui| {
                let what = match r["prefix"].as_str() {
                    Some(p) => format!("{} starting with \"{p}\"", r["tool"].as_str().unwrap_or("?")),
                    None => format!("every {}", r["tool"].as_str().unwrap_or("?")),
                };
                ui.label(rt(what, 12.0, TEXT));
                ui.label(rt(format!("({})", r["scope"].as_str().unwrap_or("thread")), 11.0, MUTED));
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    if widgets::button(ui, "Remove", Kind::Danger, 11.0, (8.0, 2.0), None).clicked() {
                        remove = Some(id.clone());
                    }
                });
            });
        }
        if let (Some(id), Some(be)) = (remove, &mut self.be) {
            self.m.remove_rule(be, &id);
        }
        ui.separator();
        ui.label(rtb("CHECKPOINTS", 11.0, TEXT2));
        let cps = self.m.sessions[self.m.active].checkpoints.clone();
        if cps.is_empty() {
            ui.label(rt("The project is snapshotted before each turn. Snapshots show up here; Rewind restores files only, not the conversation.", 11.0, MUTED));
        }
        let busy = self.m.active_busy();
        let mut pick = None;
        for (i, c) in cps.iter().enumerate().rev() {
            ui.horizontal(|ui| {
                ui.label(rt(format!("before \"{}\"", c.label), 12.0, TEXT));
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    if !busy && widgets::button(ui, "Rewind", Kind::Secondary, 11.0, (8.0, 2.0), None).clicked() {
                        pick = Some(i);
                    }
                });
            });
        }
        if pick.is_some() {
            self.rewind_confirm = pick;
        }
    }

    fn approvals_tab(&mut self, ui: &mut egui::Ui) {
        if self.m.approvals.is_empty() {
            ui.label(rt("Nothing is waiting for you. Tool calls that need a decision show up here from every thread, with the thread they belong to.", 11.0, MUTED));
            return;
        }
        let mut answer: Option<(String, &'static str)> = None;
        let mut go_to: Option<u32> = None;
        for a in self.m.approvals.clone() {
            let thread = self.m.sessions.iter().find(|s| s.id == a.sid).map(|s| s.name.clone()).unwrap_or_default();
            egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, WARN)).corner_radius(cr(12)).inner_margin(Margin::same(12)).show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal(|ui| {
                    ui.label(rtb(&a.tool, 13.0, TEXT));
                    if ui.add(egui::Label::new(rt(format!("in {thread}"), 11.0, ACCENT)).sense(Sense::click())).clicked() {
                        go_to = Some(a.sid);
                    }
                });
                let mut args = serde_json::to_string_pretty(&a.args).unwrap_or_default();
                if args.len() > 600 {
                    let mut end = 600;
                    while !args.is_char_boundary(end) {
                        end -= 1;
                    }
                    args.truncate(end);
                    args.push_str("\n…");
                }
                ui.add(egui::Label::new(RichText::new(args).size(11.0).family(egui::FontFamily::Monospace).color(TEXT2)).wrap());
                ui.horizontal_wrapped(|ui| {
                    for (label, key, kind) in [("Allow", "allow", Kind::Primary), ("Always in thread", "always_thread", Kind::Secondary), ("Always in project", "always_project", Kind::Secondary), ("Skip", "skip", Kind::Secondary), ("Abort", "abort", Kind::Danger)] {
                        if widgets::button(ui, label, kind, 12.0, (12.0, 5.0), None).clicked() {
                            answer = Some((a.id.clone(), key));
                        }
                    }
                });
            });
        }
        if let (Some((id, d)), Some(be)) = (answer, &mut self.be) {
            match d {
                "always_thread" => {
                    self.m.approve_always(be, &id, "thread", None);
                }
                "always_project" => {
                    self.m.approve_always(be, &id, "project", None);
                }
                _ => {
                    self.m.answer_approval_id(be, &id, d);
                }
            }
        }
        if let Some(sid) = go_to {
            if let Some(i) = self.m.sessions.iter().position(|s| s.id == sid) {
                self.m.select(i);
            }
        }
    }

    fn diff_tab(&mut self, ui: &mut egui::Ui) {
        let sid = self.m.sessions[self.m.active].id;
        let n = self.m.sessions[self.m.active].diffs.len();
        if n == 0 {
            ui.label(rt("No file changes recorded in this thread yet. Edits the agent makes show up here, file by file.", 11.0, MUTED));
            return;
        }
        for di in (0..n).rev() {
            let (path, len) = {
                let d = &self.m.sessions[self.m.active].diffs[di];
                (d.path.clone(), d.diff.len())
            };
            let key = format!("{sid}:{di}");
            if self.diff_cache.get(&key).map(|c| c.0) != Some(len) {
                let lines = parse_diff(&self.m.sessions[self.m.active].diffs[di].diff);
                self.diff_cache.insert(key.clone(), (len, std::sync::Arc::new(lines)));
            }
            let lines = self.diff_cache[&key].1.clone();
            let (add, del) = lines.iter().fold((0, 0), |(a, d), l| (a + (l.kind == b'+') as usize, d + (l.kind == b'-') as usize));
            let id = ui.make_persistent_id(("diff", &key));
            let mut state = egui::collapsing_header::CollapsingState::load_with_default_open(ui.ctx(), id, di + 1 == n);
            let mut copy = false;
            let header = ui.horizontal(|ui| {
                state.show_toggle_button(ui, egui::collapsing_header::paint_default_icon);
                ui.label(rtb(path.rsplit(['/', '\\']).next().unwrap_or(&path), TYPE_DIFF, TEXT));
                ui.label(rt(format!("+{add}"), TYPE_DIFF, DIFF_ADD_TEXT));
                ui.label(rt(format!("−{del}"), TYPE_DIFF, DIFF_DEL_TEXT));
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    if widgets::button(ui, "Copy", Kind::Secondary, 11.0, (8.0, 2.0), None).clicked() {
                        copy = true;
                    }
                });
            });
            if header.response.clicked() {
                state.toggle(ui);
            }
            ui.label(rt(&path, 10.0, MUTED));
            if copy {
                let text = self.m.sessions[self.m.active].diffs[di].diff.clone();
                ui.ctx().copy_text(text);
            }
            state.show_body_unindented(ui, |ui| {
                let row_h = TYPE_DIFF + 5.0;
                egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(10)).show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ScrollArea::vertical().id_salt(("diffrows", &key)).max_height(460.0).auto_shrink([false, true]).show_rows(ui, row_h, lines.len(), |ui, range| {
                        ui.spacing_mut().item_spacing.y = 0.0;
                        for l in &lines[range] {
                            let (rect, _) = ui.allocate_exact_size(vec2(ui.available_width(), row_h), Sense::hover());
                            let (bg, fg) = match l.kind {
                                b'+' => (DIFF_ADD, DIFF_ADD_TEXT),
                                b'-' => (DIFF_DEL, DIFF_DEL_TEXT),
                                b'@' => (ELEV, ACCENT),
                                _ => (Color32::TRANSPARENT, MUTED),
                            };
                            let p = ui.painter().with_clip_rect(rect);
                            p.rect_filled(rect, 0.0, bg);
                            let f = egui::FontId::new(TYPE_DIFF, egui::FontFamily::Monospace);
                            let num = |n: Option<u32>| n.map(|n| format!("{n:>5}")).unwrap_or_else(|| "     ".into());
                            p.text(rect.left_center() + vec2(6.0, 0.0), egui::Align2::LEFT_CENTER, format!("{} {}", num(l.old), num(l.new)), f.clone(), MUTED);
                            p.text(rect.left_center() + vec2(92.0, 0.0), egui::Align2::LEFT_CENTER, if l.kind == b'@' { l.text.clone() } else { format!("{} {}", l.kind as char, l.text) }, f, fg);
                        }
                    });
                });
            });
            state.store(ui.ctx());
        }
    }

    fn agent_tab(&mut self, ui: &mut egui::Ui, w: f32) {
        ui.horizontal(|ui| {
            ui.label(rtb("AGENT ROSTER & LINEUP", 13.0, TEXT));
            let active_count = self.m.agents.iter().filter(|a| a.status == "RUNNING").count();
            let badge_text = format!("{} active", active_count);
            let badge_color = if active_count > 0 { ACCENT } else { MUTED };
            ui.label(rtb(&badge_text, 10.0, badge_color));
        });
        if self.m.agents.is_empty() {
            ui.label(rt("No active agents in lineup. Start a task below or dispatch subagents.", 11.0, MUTED));
        } else {
            for agent in &self.m.agents {
                egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(10)).inner_margin(Margin::same(8)).show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ui.horizontal(|ui| {
                        ui.label(rtb(&agent.name, 12.0, TEXT));
                        let c = match agent.status.as_str() {
                            "RUNNING" => ACCENT,
                            "PAUSED" => Color32::from_rgb(234, 179, 8),
                            "FAILED" => ERROR,
                            _ => MUTED,
                        };
                        ui.label(rtb(&agent.status, 10.0, c));
                        ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                            let max_steps = if agent.status == "IDLE" && agent.id == "react-primary" { self.agent_steps } else { agent.max_steps };
                            ui.label(rt(format!("step {}/{}  {:.1}s", agent.step, max_steps, agent.elapsed_sec), 10.0, MUTED));
                        });
                    });
                    if !agent.task.is_empty() {
                        ui.add(egui::Label::new(rt(&agent.task, 11.0, TEXT2)).wrap());
                    }
                });
            }
        }
        ui.add_space(4.0);
        ui.label(rtb("PRIMARY RUNNER CONTROLS", 11.0, MUTED));
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
            // D14: before a run the runner reports its own default; show the limit the next run will use
            let max = if state == "IDLE" { self.agent_steps as u64 } else { st["max_steps"].as_u64().unwrap_or(self.agent_steps as u64) }.max(1);
            ui.label(rt(format!("step {step}/{max}  {:.1}s", st["elapsed_sec"].as_f64().unwrap_or(0.0)), 11.0, MUTED));
        });
        let frac = if state == "IDLE" { 0.0 } else { st["current_step"].as_f64().unwrap_or(0.0) / st["max_steps"].as_f64().unwrap_or(self.agent_steps as f64).max(1.0) };
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
        let stalled = self.be.is_some() && agent_stalled(self.m.agent_ready, self.be_started, self.started, ctx.input(|i| i.time));
        let down = self.be.is_none() && self.start_error.as_deref().is_some_and(|e| e != "backend disabled");
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
            ui.add(egui::Label::new(rt(format!("Logs: {}", crate::crash::log_dir().display()), 11.0, MUTED)).wrap());
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
            if self.always_prefix.0 != a.id {
                let first = a.args["command"].as_str().and_then(|c| c.split_whitespace().next()).unwrap_or("").to_string();
                self.always_prefix = (a.id.clone(), first);
            }
            let is_cmd = a.tool == "run_command";
            let prefix = &mut self.always_prefix.1;
            modal_frame(ctx, "approval", 560.0, |ui| {
                ui.label(rtb("APPROVAL REQUIRED", 13.0, ACCENT));
                ui.label(rtb(&a.tool, 14.0, TEXT));
                // D23: a big write must never push the buttons off screen. Summary line, then a capped, scrollable preview.
                let (summary, body) = approval_preview(&a.args);
                if !summary.is_empty() {
                    ui.label(rt(summary, 12.0, TEXT));
                }
                let cap = (ui.ctx().content_rect().height() * 0.35).clamp(120.0, 320.0);
                egui::Frame::new().fill(BG).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(12)).inner_margin(Margin::same(12)).show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ScrollArea::vertical().id_salt("approval_args").max_height(cap).auto_shrink([false, true]).show(ui, |ui| {
                        ui.add(egui::Label::new(RichText::new(body).size(12.0).family(egui::FontFamily::Monospace).color(TEXT2)).wrap());
                    });
                });
                ui.label(rt("Enter allows · Esc skips", 10.0, MUTED));
                if is_cmd {
                    ui.horizontal(|ui| {
                        ui.label(rt("\"Always\" covers commands starting with", 11.0, MUTED));
                        ui.add(egui::TextEdit::singleline(prefix).desired_width(160.0).font(egui::FontId::new(12.0, egui::FontFamily::Monospace)));
                    });
                    ui.label(rt("Empty means every command. Chained or redirected commands always ask.", 10.0, MUTED));
                }
                ui.horizontal_wrapped(|ui| {
                    if widgets::button(ui, "Allow", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                        decision = Some("allow");
                    }
                    if widgets::button(ui, "Always in thread", Kind::Secondary, 13.0, (16.0, 8.0), None).on_hover_text("Allow now and stop asking for this in this thread").clicked() {
                        decision = Some("always_thread");
                    }
                    if widgets::button(ui, "Always in project", Kind::Secondary, 13.0, (16.0, 8.0), None).on_hover_text("Allow now and stop asking for this in every thread of this project").clicked() {
                        decision = Some("always_project");
                    }
                    if widgets::button(ui, "Skip", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        decision = Some("skip");
                    }
                    if widgets::button(ui, "Abort Turn", Kind::Danger, 13.0, (16.0, 8.0), None).clicked() {
                        decision = Some("abort");
                    }
                });
            });
            ctx.memory_mut(|m| m.surrender_focus(egui::Id::new("prompt")));
            let prefix_focused = ctx.memory(|m| m.focused()).is_some();
            if decision.is_none() && !prefix_focused {
                if ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Enter)) {
                    decision = Some("allow");
                } else if ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Escape)) {
                    decision = Some("skip");
                }
            }
            if let (Some(d), Some(be)) = (decision, &mut self.be) {
                let p = if is_cmd { Some(self.always_prefix.1.clone()) } else { None };
                match d {
                    "always_thread" => {
                        self.m.approve_always(be, &a.id, "thread", p.as_deref());
                    }
                    "always_project" => {
                        self.m.approve_always(be, &a.id, "project", p.as_deref());
                    }
                    _ => self.m.answer_approval(be, d),
                }
            }
            return;
        }
        if self.settings.is_some() {
            let file = secrets::env_path();
            let (mut save, mut close) = (false, ctx.input(|i| i.key_pressed(Key::Escape)));
            let mut clear: Option<usize> = None;
            let stored = secrets::read_from(&file);
            if let Some(st) = &mut self.settings {
                modal_frame(ctx, "settings", 560.0, |ui| {
                    ui.label(rtb("SETTINGS: MODEL ACCESS", 13.0, ACCENT));
                    ui.add(egui::Label::new(rt(format!("Keys are stored in {} (readable only by you) and never shown again. Leave a field empty to keep what is stored.", file.display()), 11.0, MUTED)).wrap());
                    for (i, (name, label, secret)) in secrets::PROVIDERS.iter().enumerate() {
                        ui.label(rt(*label, 12.0, TEXT2));
                        let have = stored.get(*name).filter(|v| !v.is_empty()).map(|v| if *secret { secrets::mask(v) } else { v.clone() });
                        let hint = match &have {
                            Some(h) => format!("saved: {h}"),
                            None => "not set".into(),
                        };
                        ui.horizontal(|ui| {
                            let w = if have.is_some() { ui.available_width() - 70.0 } else { ui.available_width() };
                            ui.add(egui::TextEdit::singleline(&mut st.inputs[i]).password(*secret).hint_text(rt(hint, 12.0, MUTED)).desired_width(w));
                            // D24: a stored value can be removed, not only replaced
                            if have.is_some() && widgets::button(ui, "Clear", Kind::Danger, 11.0, (8.0, 3.0), None).on_hover_text("Remove the stored value").clicked() {
                                clear = Some(i);
                            }
                        });
                    }
                    if !st.message.is_empty() {
                        ui.add(egui::Label::new(rt(&st.message, 11.0, if st.ok { ACCENT } else { ERROR })).wrap());
                    }
                    ui.horizontal(|ui| {
                        if widgets::button(ui, "Save", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                            save = true;
                        }
                        if widgets::button(ui, "Close", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                            close = true;
                        }
                    });
                });
            }
            if save {
                self.save_settings(ctx);
            }
            if let (Some(i), Some(st)) = (clear, &mut self.settings) {
                let (name, label, _) = secrets::PROVIDERS[i];
                match secrets::set_in(&file, name, "") {
                    Ok(()) => {
                        st.ok = true;
                        st.message = format!("{label} removed. Agent restarted.");
                        if !self.m.any_busy() {
                            self.retry_backend(ctx);
                        }
                    }
                    Err(e) => {
                        st.ok = false;
                        st.message = format!("{label}: {e}");
                    }
                }
            }
            if close {
                self.settings = None;
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
                if let Err(e) = self.m.rewind_code(self.be.as_mut(), i) {
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
            let hits: Vec<String> = crate::fsx::fuzzy(&q, &self.work.index, 12).into_iter().map(String::from).collect();
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
        if self.help_open {
            let mut close = ctx.input(|i| i.key_pressed(Key::Escape));
            modal_frame(ctx, "help", 520.0, |ui| {
                ui.label(rtb("KEYBOARD SHORTCUTS", 13.0, ACCENT));
                egui::Grid::new("keys").num_columns(2).spacing(vec2(24.0, 8.0)).show(ui, |ui| {
                    for (k, what) in SHORTCUTS {
                        ui.label(egui::RichText::new(*k).monospace().size(12.0).color(ACCENT));
                        ui.label(rt(*what, 12.0, TEXT));
                        ui.end_row();
                    }
                });
                if widgets::button(ui, "Close", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                    close = true;
                }
            });
            if close {
                self.help_open = false;
            }
            return;
        }
        if let Some(mut q) = self.search.clone() {
            let mut close = ctx.input(|i| i.key_pressed(Key::Escape));
            let mut pick: Option<usize> = None;
            let hits = self.m.search(&q, 50);
            modal_frame(ctx, "search", 640.0, |ui| {
                ui.label(rtb("SEARCH ALL CHATS", 13.0, ACCENT));
                ui.add(egui::TextEdit::singleline(&mut q).hint_text(rt("Words to find in any thread...", 13.0, MUTED)).desired_width(f32::INFINITY)).request_focus();
                if !q.trim().is_empty() && hits.is_empty() {
                    ui.label(rt("No message matches.", 12.0, MUTED));
                }
                ScrollArea::vertical().max_height(380.0).show(ui, |ui| {
                    for h in &hits {
                        let resp = ui.add(egui::Label::new(rt(format!("{}  ·  {}", h.thread_name, h.snippet), 12.0, TEXT)).sense(Sense::click()));
                        if resp.clicked() {
                            pick = Some(h.session);
                        }
                    }
                });
                if ui.input(|i| i.key_pressed(Key::Enter)) {
                    pick = hits.first().map(|h| h.session);
                }
            });
            if let Some(i) = pick {
                self.m.select(i);
                close = true;
            }
            self.search = if close { None } else { Some(q) };
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
                ui.label(rt("Name", 11.0, TEXT2));
                ui.add(egui::TextEdit::singleline(&mut self.job_form.0).hint_text(rt("Job name", 12.0, MUTED)).desired_width(f32::INFINITY));
                ui.label(rt("Repeat every (seconds, 5 or more)", 11.0, TEXT2));
                ui.add(egui::TextEdit::singleline(&mut self.job_form.1).hint_text(rt("60", 12.0, MUTED)).desired_width(120.0));
                ui.label(rt("Task", 11.0, TEXT2));
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
            if create {
                match self.job_form.1.trim().parse::<f64>() {
                    _ if self.job_form.0.trim().is_empty() || self.job_form.3.trim().is_empty() => {
                        self.m.notice = "A job needs a name and a task.".into();
                        create = false;
                    }
                    Ok(v) if v >= 5.0 => {}
                    _ => {
                        self.m.notice = "Repeat interval must be a number of seconds, 5 or more.".into();
                        create = false;
                    }
                }
            }
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
            return;
        }
        if self.options_open {
            let mut close = ctx.input(|i| i.key_pressed(Key::Escape));
            let mut save = false;
            modal_frame(ctx, "options", 580.0, |ui| {
                ui.label(rtb("DESKTOP OPTIONS & LOCAL MODELS", 13.0, ACCENT));
                ui.label(rt("Configure local model routes, hosts, and active context window.", 11.0, MUTED));

                ui.label(rtb("LOCAL MODEL ALIAS", 11.0, TEXT2));
                ui.add(egui::TextEdit::singleline(&mut self.options_form.0).hint_text(rt("e.g. qwen2.5-coder:14b", 12.0, MUTED)).desired_width(f32::INFINITY));

                ui.label(rtb("OLLAMA HOST", 11.0, TEXT2));
                ui.add(egui::TextEdit::singleline(&mut self.options_form.1).hint_text(rt("http://127.0.0.1:11434", 12.0, MUTED)).desired_width(f32::INFINITY));

                ui.label(rtb("LLAMACPP HOST", 11.0, TEXT2));
                ui.add(egui::TextEdit::singleline(&mut self.options_form.2).hint_text(rt("http://127.0.0.1:8080", 12.0, MUTED)).desired_width(f32::INFINITY));

                ui.label(rtb("LOCAL AI BASE", 11.0, TEXT2));
                ui.add(egui::TextEdit::singleline(&mut self.options_form.3).hint_text(rt("http://localhost:8080/v1", 12.0, MUTED)).desired_width(f32::INFINITY));

                ui.label(rtb("CONTEXT WINDOW (TOKENS)", 11.0, TEXT2));
                ui.add(egui::TextEdit::singleline(&mut self.options_form.4).hint_text(rt("1048576", 12.0, MUTED)).desired_width(f32::INFINITY));

                ui.label(rtb("WORKING DIRECTORY (CWD)", 11.0, TEXT2));
                ui.horizontal(|ui| {
                    ui.add(egui::TextEdit::singleline(&mut self.options_form.5).hint_text(rt("C:\\Users\\jpm05\\Documents", 12.0, MUTED)).desired_width(ui.available_width() - 85.0));
                    if widgets::button(ui, "Browse...", Kind::Secondary, 12.0, (10.0, 4.0), None).clicked() {
                        if let Some(folder) = pick_folder_dialog(&self.options_form.5) {
                            self.options_form.5 = folder;
                        }
                    }
                });

                ui.horizontal(|ui| {
                    if widgets::button(ui, "Save Options", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                        save = true;
                    }
                    if widgets::button(ui, "Close", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        close = true;
                    }
                });
            });
            if save {
                let win = self.options_form.4.trim().parse::<u64>().unwrap_or(1_048_576);
                self.m.apply_options(
                    &self.options_form.0,
                    &self.options_form.1,
                    &self.options_form.2,
                    &self.options_form.3,
                    win,
                    &self.options_form.5,
                );
                self.options_open = false;
            }
            if close {
                self.options_open = false;
            }
            return;
        }
        if self.memories_open {
            let mut close = ctx.input(|i| i.key_pressed(Key::Escape));
            let mut append = false;
            modal_frame(ctx, "memories", 660.0, |ui| {
                ui.label(rtb("MEMORY LEDGER (~/.hydra/memory/ledger.jsonl)", 13.0, ACCENT));
                ui.add(egui::TextEdit::singleline(&mut self.memories_filter).hint_text(rt("Filter memory facts...", 12.0, MUTED)).desired_width(f32::INFINITY));

                ScrollArea::vertical().max_height(240.0).show(ui, |ui| {
                    let q = self.memories_filter.to_lowercase();
                    let filtered: Vec<_> = self.memories_entries.iter().filter(|e| {
                        q.is_empty() || e.subject.to_lowercase().contains(&q) || e.body.to_lowercase().contains(&q) || e.actor.to_lowercase().contains(&q)
                    }).collect();

                    if filtered.is_empty() {
                        ui.label(rt("No matching memory facts in ledger.", 11.0, MUTED));
                    }
                    for e in filtered.iter().rev() {
                        egui::Frame::new().fill(BG3).stroke(Stroke::new(1.0, BORDER)).corner_radius(cr(10)).inner_margin(Margin::same(8)).show(ui, |ui| {
                            ui.set_width(ui.available_width());
                            ui.horizontal(|ui| {
                                ui.label(rtb(format!("#{} {}", e.seq, e.subject), 12.0, TEXT));
                                ui.label(rtb(&e.kind, 10.0, ACCENT));
                                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                                    ui.label(rt(format!("{} • {}", e.actor, e.ts), 10.0, MUTED));
                                });
                            });
                            if !e.body.is_empty() {
                                ui.add(egui::Label::new(rt(&e.body, 11.0, TEXT2)).wrap());
                            }
                        });
                    }
                });

                ui.label(rtb("RECORD NEW FACT", 11.0, MUTED));
                ui.add(egui::TextEdit::singleline(&mut self.memories_new_fact.0).hint_text(rt("Subject (e.g. user_preference, project_root)", 12.0, MUTED)).desired_width(f32::INFINITY));
                ui.add(egui::TextEdit::multiline(&mut self.memories_new_fact.1).hint_text(rt("Fact body / content", 12.0, MUTED)).desired_rows(2).desired_width(f32::INFINITY));

                if let Some(st) = &self.memories_status {
                    ui.label(rt(st, 11.0, ACCENT));
                }

                ui.horizontal(|ui| {
                    if widgets::button(ui, "Record Fact", Kind::Primary, 13.0, (16.0, 8.0), None).clicked() {
                        append = true;
                    }
                    if widgets::button(ui, "Refresh", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        self.memories_entries = read_memory_ledger(&memory_ledger_path());
                    }
                    if widgets::button(ui, "Close", Kind::Secondary, 13.0, (16.0, 8.0), None).clicked() {
                        close = true;
                    }
                });
            });
            if append {
                let subj = self.memories_new_fact.0.trim();
                let body = self.memories_new_fact.1.trim();
                if !subj.is_empty() && !body.is_empty() {
                    match append_memory_fact(&memory_ledger_path(), subj, body, "user:desktop") {
                        Ok(entry) => {
                            self.memories_status = Some(format!("Recorded fact #{}", entry.seq));
                            self.memories_new_fact = (String::new(), String::new());
                            self.memories_entries = read_memory_ledger(&memory_ledger_path());
                        }
                        Err(e) => {
                            self.memories_status = Some(format!("Error: {e}"));
                        }
                    }
                }
            }
            if close {
                self.memories_open = false;
            }
            return;
        }
    }

    pub fn is_computer_use_active(&self) -> bool {
        self.m.nav == "computer"
            || self.m.pending.values().any(|p| matches!(p, Pending::Screen | Pending::Click | Pending::Type | Pending::Window))
            || (self.m.drawer_tab == "screen" && self.m.any_busy())
    }

    fn shortcuts(&mut self, ctx: &egui::Context) {
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::K)) {
            self.palette = if self.palette.is_some() { None } else { Some((String::new(), 0)) };
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND | Modifiers::SHIFT, Key::F)) {
            self.search = Some(String::new());
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::F1) || i.consume_key(Modifiers::COMMAND, Key::Slash)) {
            self.help_open = !self.help_open;
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::Comma)) {
            self.open_settings();
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND | Modifiers::SHIFT, Key::Comma)) {
            self.run(Cmd::Options);
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::COMMAND, Key::B)) {
            self.nav_open = !self.nav_open;
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

/// One agent action line: tool calls and failures render as the page's tool card, results as colored text.
fn action_row(ui: &mut egui::Ui, a: &str, width: f32) {
    let card = |ui: &mut egui::Ui, bar: Color32, fill: Color32, title: &str, body: &str, title_color: Color32| {
        let slot = ui.painter().add(egui::Shape::Noop);
        let r = egui::Frame::new().fill(fill).stroke(Stroke::new(1.0, if bar == ERROR { ERROR } else { BORDER })).corner_radius(cr(12)).inner_margin(Margin { left: 16, right: 16, top: 12, bottom: 12 }).show(ui, |ui| {
            ui.set_width(width - 34.0);
            ui.spacing_mut().item_spacing.y = 6.0;
            ui.label(rtb(title, TYPE_TOOL_TITLE, title_color));
            if !body.is_empty() {
                ui.add(egui::Label::new(rt(body, TYPE_TOOL_CARD, TEXT)).wrap());
            }
        });
        let rr = r.response.rect;
        ui.painter().set(slot, widgets::shadow_shape(rr, 12, SHADOW_TOOL_CARD));
        ui.painter().rect_filled(egui::Rect::from_min_size(rr.min + vec2(0.0, 6.0), vec2(3.0, rr.height() - 12.0)), cr(1), bar);
    };
    if let Some(rest) = a.strip_prefix("▸ ") {
        let (tool, args) = rest.split_once("  ").unwrap_or((rest, ""));
        card(ui, ACCENT, BG2, tool, args, ACCENT);
    } else if let Some(rest) = a.strip_prefix("× ") {
        let (tool, msg) = rest.split_once(": ").unwrap_or(("error", rest));
        card(ui, ERROR, ERROR_DIM, tool, msg, ERROR);
    } else {
        let c = if a.starts_with('✓') { ACCENT } else { MUTED };
        ui.add(egui::Label::new(rt(a, 12.0, c)).wrap());
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

/// HYDRA_TIMING=<file>: append "<event> <unix ms>" once per event, for the cold-start measurement.
pub fn timing_mark(event: &str) {
    use std::sync::Mutex;
    static SEEN: Mutex<Vec<String>> = Mutex::new(Vec::new());
    let Ok(path) = std::env::var("HYDRA_TIMING") else { return };
    let mut seen = SEEN.lock().unwrap_or_else(|e| e.into_inner());
    if seen.iter().any(|e| e == event) {
        return;
    }
    seen.push(event.to_string());
    let ms = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        use std::io::Write;
        let _ = writeln!(f, "{event} {ms}");
    }
}

impl eframe::App for HydraApp {
    fn ui(&mut self, root: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let started = std::time::Instant::now();
        self.draw(root);
        if fonts_ready(root.ctx()) {
            timing_mark("first_frame");
        }
        // HYDRA_FRAMES=<file>: running frame count and repaint causes, for the idle-repaint measurement (release bar 4)
        if let Ok(path) = std::env::var("HYDRA_FRAMES") {
            use std::sync::atomic::{AtomicU64, Ordering};
            static N: AtomicU64 = AtomicU64::new(0);
            let n = N.fetch_add(1, Ordering::Relaxed) + 1;
            let causes = root.ctx().repaint_causes().iter().map(|c| c.to_string()).collect::<Vec<_>>().join(";");
            let _ = std::fs::write(path, format!("{n}\n{causes}\n"));
        }
        let ms = started.elapsed().as_secs_f64() * 1000.0;
        let ctx = root.ctx().clone();
        let t = ctx.input(|i| i.time);
        self.probe_step(&ctx, t, ms);
    }
}

impl HydraApp {
    /// Scripted motion check: idle, then sidebar toggle, message arrival and palette open, one second apart.
    fn probe_step(&mut self, ctx: &egui::Context, t: f64, ms: f64) {
        let Some(p) = &mut self.probe else { return };
        if p.stage == 0 {
            p.last = t;
            p.stage = 1;
        }
        if t - p.last > 1.0 {
            p.last = t;
            p.stage += 1;
            match p.stage {
                2 => self.nav_open = !self.nav_open,
                3 => self.m.sessions[self.m.active].messages.push(Msg { user: false, header: "HYDRA ORCHESTRATOR".into(), text: "Probe message arriving with the fade.".into(), actions: vec![] }),
                4 => self.palette = Some((String::new(), 0)),
                5 => {
                    // release bar 4: a 2,000 message transcript, then stream into it and scroll it
                    self.palette = None;
                    let msgs = &mut self.m.sessions[self.m.active].messages;
                    for i in 0..2000 {
                        let text = match i % 4 {
                            0 => format!("Question {i}: what does module {i} do?"),
                            1 => format!("Module {i} parses the config.\n\n```rust\nfn m{i}() -> u32 {{ {i} }}\n```\nIt returns its index."),
                            2 => format!("{} done.", "Line of a longer answer that wraps across the width of the chat column. ".repeat(3)),
                            _ => format!("- item a{i}\n- item b{i}\n- item c{i}"),
                        };
                        msgs.push(Msg { user: i % 4 == 0, header: if i % 4 == 0 { String::new() } else { "HYDRA ORCHESTRATOR".into() }, text, actions: vec![] });
                    }
                    msgs.push(Msg { user: false, header: "HYDRA ORCHESTRATOR".into(), text: String::new(), actions: vec![] });
                }
                7 => p.scroll = Some(0.0),
                _ => {}
            }
        }
        if p.stage == 6 || p.stage == 7 {
            if let Some(last) = self.m.sessions[self.m.active].messages.last_mut() {
                last.text.push_str(" token");
            }
        }
        if p.stage == 7 {
            let k = ((t - p.last) * 4.0).fract() as f32;
            p.scroll = Some(200_000.0 * if k < 0.5 { k * 2.0 } else { 2.0 - k * 2.0 });
        }
        let tag = match p.stage {
            1 => "idle",
            2 => "nav",
            3 => "msg",
            4 => "palette",
            5 => "load2000",
            6 => "stream",
            7 => "stream_scroll",
            _ => "end",
        };
        p.rows.push((t, ms, tag));
        if p.stage >= 8 {
            let mut csv: String = p.rows.iter().map(|(t, ms, tag)| format!("{t:.4},{ms:.3},{tag}\n")).collect();
            if let Ok(st) = std::fs::read_to_string("/proc/self/status") {
                if let Some(l) = st.lines().find(|l| l.starts_with("VmRSS:")) {
                    csv.push_str(&format!("# rss_2000_msgs {}\n", l.split_whitespace().nth(1).unwrap_or("0")));
                }
            }
            let _ = std::fs::write(&p.path, csv);
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        } else {
            ctx.request_repaint();
        }
    }

    pub fn model(&self) -> &Model {
        &self.m
    }

    pub fn model_mut(&mut self) -> &mut Model {
        &mut self.m
    }

    /// Process id of the live agent child, None while it is down.
    pub fn backend_pid(&self) -> Option<u32> {
        self.be.as_ref().map(|b| b.pid())
    }

    pub fn toast_text(&self) -> Option<&str> {
        self.toast.as_ref().map(|t| t.0.as_str())
    }

    pub fn queued(&self, sid: u32) -> Vec<String> {
        self.queue.get(&sid).cloned().unwrap_or_default()
    }

    pub fn input_text(&self) -> &str {
        &self.input
    }

    /// One frame of the whole window. Separate from `eframe::App::ui` so tests can drive it.
    pub fn draw(&mut self, root: &mut egui::Ui) {
        if !fonts_ready(root.ctx()) {
            root.ctx().request_repaint();
            return;
        }
        let ctx = root.ctx().clone();
        let ctx = &ctx;
        let time = ctx.input(|i| i.time);
        self.drain();
        let dropped_files = ctx.input(|i| i.raw.dropped_files.clone());
        for file in dropped_files {
            let pbuf = file.path();
            let pb = pbuf.to_path_buf();
            if is_supported_attachment(pbuf) && !self.attachments.contains(&pb) {
                self.attachments.push(pb);
            }
        }
        if std::mem::take(&mut self.child_died) {
            // protocol "respawn": drop the dead handle at once (so the app knows the agent is down), then start a
            // new child after a backoff of 0.5 s, 2 s, 5 s; a fourth death inside a minute stops and shows the panel
            self.be = None;
            self.deaths.retain(|t| time - *t < 60.0);
            self.deaths.push(time);
            let why = if self.last_exit.is_empty() { "agent process ended".to_string() } else { self.last_exit.clone() };
            match RESPAWN_BACKOFF.get(self.deaths.len() - 1) {
                Some(wait) => {
                    self.respawn_at = Some(std::time::Instant::now() + std::time::Duration::from_secs_f64(*wait));
                    self.toast = Some((format!("The agent stopped ({why}). Restarting in {wait:.1} s; the running turn was lost."), f64::NAN));
                }
                None => {
                    self.respawn_at = None;
                    self.start_error = Some(format!("the agent stopped {} times in a minute (last: {why}). Press Retry to start it again.", self.deaths.len()));
                    self.panel_dismissed = false;
                }
            }
        }
        if std::mem::take(&mut self.hello_seen) {
            // a new agent (first start or respawn): ask again for what lives outside a turn, so adopted tasks show
            if let Some(be) = &mut self.be {
                self.m.pending_tasks(be);
                self.m.list_sessions(be);
                self.m.reload_cut_threads(be);
            }
            self.skills_asked = false;
            self.mcp_asked = false;
            self.m.rules.0.clear();
        }
        if self.respawn_at.is_some_and(|t| std::time::Instant::now() >= t) {
            self.respawn_at = None;
            self.retry_backend(ctx);
        }
        if self.respawn_at.is_some() {
            ctx.request_repaint_after(std::time::Duration::from_millis(100));
        }
        if let Ok(c) = self.health_rx.try_recv() {
            self.checks = c;
            // first run without any key: take the user straight to the place where one is entered
            if !self.settings_auto && self.checks.iter().any(|c| c.name == "Model access" && !c.ok) {
                self.settings_auto = true;
                self.open_settings();
            }
        }
        if self.started < 0.0 {
            self.started = time;
        }
        // Idle means idle: backend events, input and the workspace watcher wake us. Only animate while something runs.
        let animating = self.m.any_busy() || self.checks.is_empty() || time - self.started < 2.0;
        // D21: a frame caused by input or an agent event is followed by one more, so a swap chain that lags a frame
        // (software GL on Xvfb) still presents the current state without waiting for the next pointer move
        let woke = ctx.input(|i| !i.events.is_empty()) || std::mem::take(&mut self.had_events);
        ctx.request_repaint_after(std::time::Duration::from_millis(if animating { 50 } else if woke { 120 } else { IDLE_REPAINT_MS }));
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
        let fr = layout::frame(total);
        let narrow = fr.narrow;
        let border = Stroke::new(1.0, BORDER);
        let header_h = fr.header_height;
        if self.last_overlay != Some(fr.overlay_nav) {
            self.nav_open = !fr.overlay_nav || std::env::var("HYDRA_OPEN").as_deref() == Ok("nav");
            self.last_overlay = Some(fr.overlay_nav);
        }
        let nav_t = motion::toggle(ctx, egui::Id::new("nav-open"), self.nav_open);

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
        if !fr.overlay_nav && nav_t > 0.001 {
            egui::Panel::left("nav")
                .exact_size(LAYOUT_SIDEBAR_WIDTH * nav_t)
                .resizable(false)
                .show_separator_line(false)
                .frame(egui::Frame::new().fill(BG2).inner_margin(Margin::symmetric(10, 16)))
                .show(root, |ui| {
                    let r = ui.max_rect();
                    ui.set_clip_rect(r.expand2(vec2(10.0, 16.0)).intersect(ui.clip_rect()));
                    let full = egui::Rect::from_min_size(r.min, vec2(LAYOUT_SIDEBAR_WIDTH - 20.0, r.height()));
                    ui.scope_builder(egui::UiBuilder::new().max_rect(full), |ui| {
                        self.nav(ui);
                        self.projects(ui);
                    });
                    ui.painter().vline(r.right() + 10.0 - 0.5, r.y_range(), border);
                });
        }
        let dw = fr.drawer_width;
        // Pat's layout width by default; dragging the left edge sets a user width, kept within sane bounds as the window changes
        let lo = dw.min(300.0);
        let hi = (total * 0.6).max(lo);
        let dw = self.drawer_user.map(|w| w.clamp(lo, hi)).unwrap_or(dw);
        egui::Panel::right("drawer")
            .exact_size(dw)
            .resizable(false)
            .show_separator_line(false)
            .frame(egui::Frame::new().fill(BG2))
            .show(root, |ui| {
                ui.painter().vline(ui.max_rect().left() + 0.5, ui.max_rect().y_range(), border);
                let edge = egui::Rect::from_x_y_ranges(ui.max_rect().left() - 3.0..=ui.max_rect().left() + 3.0, ui.max_rect().y_range());
                let grip = ui.interact(edge, egui::Id::new("drawer-grip"), Sense::drag()).on_hover_cursor(egui::CursorIcon::ResizeHorizontal);
                if grip.dragged() {
                    self.drawer_user = Some((dw - grip.drag_delta().x).clamp(lo, hi));
                }
                if grip.double_clicked() {
                    self.drawer_user = None;
                }
                if grip.hovered() || grip.dragged() {
                    ui.painter().vline(ui.max_rect().left() + 0.5, ui.max_rect().y_range(), Stroke::new(2.0, ACCENT));
                }
                let live_w = ui.max_rect().width();
                self.drawer(ui, live_w);
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
        self.nav_overlay(ctx, fr, nav_t);
        self.modals(ctx);
        self.toast_ui(ctx, time);
        let _ = &self.start_error;

        if self.is_computer_use_active() {
            let painter = ctx.layer_painter(egui::LayerId::new(egui::Order::Foreground, egui::Id::new("computer_use_glow")));
            let s_rect = ctx.content_rect();
            let pulse = 0.75 + 0.25 * (time * 3.5).sin().abs() as f32;
            let glow_c1 = Color32::from_rgba_premultiplied(0, 255, 102, (180.0 * pulse) as u8);
            let glow_c2 = Color32::from_rgba_premultiplied(0, 255, 102, (60.0 * pulse) as u8);
            painter.rect_stroke(s_rect, CornerRadius::ZERO, Stroke::new(4.0, glow_c1), StrokeKind::Inside);
            painter.rect_stroke(s_rect.shrink(4.0), CornerRadius::ZERO, Stroke::new(4.0, glow_c2), StrokeKind::Inside);
        }

        if let Some(plan) = &mut self.shot {
            plan.frames += 1;
            ctx.request_repaint_after(std::time::Duration::from_millis(250));
            let secs: f64 = std::env::var("HYDRA_SHOT_SECS").ok().and_then(|v| v.parse().ok()).unwrap_or(f64::MAX);
            if (plan.frames >= std::env::var("HYDRA_SHOT_FRAMES").ok().and_then(|v| v.parse().ok()).unwrap_or(20) || time - self.started >= secs) && !plan.requested {
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

fn pick_folder_dialog(initial: &str) -> Option<String> {
    #[cfg(windows)]
    {
        let escaped = initial.replace('\'', "''");
        let script = format!(
            "Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; if ('{}' -ne '') {{ $d.SelectedPath = '{}' }}; if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output $d.SelectedPath }}",
            escaped, escaped
        );
        let output = std::process::Command::new("powershell")
            .args(["-NoProfile", "-Command", &script])
            .output()
            .ok()?;
        if output.status.success() {
            let res = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !res.is_empty() && std::path::Path::new(&res).is_dir() {
                return Some(res);
            }
        }
    }
    #[cfg(not(windows))]
    {
        let _ = initial;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_drag_and_drop_attachments_and_prompt_formatting() {
        let img = PathBuf::from("diagram.png");
        let aud = PathBuf::from("voice.wav");
        let vid = PathBuf::from("recording.mp4");
        let doc = PathBuf::from("report.pdf");
        let src = PathBuf::from("app.rs");
        let bin = PathBuf::from("binary.iso");

        assert!(is_supported_attachment(&img));
        assert!(is_supported_attachment(&aud));
        assert!(is_supported_attachment(&vid));
        assert!(is_supported_attachment(&doc));
        assert!(is_supported_attachment(&src));
        assert!(!is_supported_attachment(&bin));

        let formatted = format_prompt_with_attachments("Please review this code", &[img.clone(), doc.clone()]);
        assert!(formatted.contains("Please review this code"));
        assert!(formatted.contains("[Attachments]"));
        assert!(formatted.contains("diagram.png"));
        assert!(formatted.contains("report.pdf"));

        let empty = format_prompt_with_attachments("", &[vid]);
        assert!(empty.starts_with("Inspect attached files."));
        assert!(empty.contains("recording.mp4"));
    }

    #[test]
    fn test_fmt_tokens_and_palette_commands() {
        assert_eq!(fmt_tokens(500), "500");
        assert_eq!(fmt_tokens(10_000), "10k");
        assert_eq!(fmt_tokens(1_048_576), "1.0M");

        let entries = palette_entries();
        assert!(entries.iter().any(|e| e.0.contains("Options")));
        assert!(entries.iter().any(|e| e.0.contains("Memory")));
    }
}
