//! Terminal session state. Context mode defaults to compact. Recall stays available and is not the default.

use std::time::Instant;
use hydra_stdio::{Backend, Ev};

use crate::endpoints::{budgets, free_usage, local_endpoints};
use crate::layout::SPIN;

#[derive(Clone)]
pub struct AgentRow {
    pub id: String,
    pub state: String,
    pub label: String,
}

pub struct App {
    pub input: String,
    pub cursor_pos: usize,
    pub transcript: Vec<String>,
    pub scroll: usize,
    pub autoscroll: bool,
    pub mode: String,
    pub model: String,
    pub dialect: String,
    pub window: u64,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub cost: f64,
    pub busy_turn: Option<String>,
    pub busy_start: Option<Instant>,
    pub spin_i: usize,
    pub status_label: String,
    pub agents: Vec<AgentRow>,
    pub approval: Option<(String, String)>,
    pub be: Option<Backend>,
    pub update_rx: Option<std::sync::mpsc::Receiver<String>>,
    pub thread: String,
    pub cwd: String,
    pub turn_n: u64,
    pub log_once: bool,
    pub quit: bool,
    assistant_at: Option<usize>,
}

impl App {
    pub fn new() -> Self {
        let cwd = std::env::current_dir().map(|p| p.display().to_string()).unwrap_or_else(|_| ".".into());
        let rules_detected = std::path::Path::new(&cwd).join("AGENTS.md").exists()
            || std::path::Path::new(&cwd).join(".cursorrules").exists()
            || std::path::Path::new(&cwd).join("CLAUDE.md").exists();
        let dialect = std::env::var("HYDRA_DIALECT")
            .unwrap_or_else(|_| if rules_detected { "progen".into() } else { "slack".into() });
        let model = std::env::var("HYDRA_ORCHESTRATOR_MODEL")
            .or_else(|_| std::env::var("HYDRA_DEFAULT_MODEL"))
            .unwrap_or_else(|_| "mimo 2.6 pro".into());

        let mut app = Self {
            input: String::new(),
            cursor_pos: 0,
            transcript: Vec::new(),
            scroll: 0,
            autoscroll: true,
            mode: "compact".into(),
            model,
            dialect,
            window: std::env::var("HYDRA_CONTEXT_WINDOW").ok().and_then(|s| s.parse().ok()).unwrap_or(1_048_576),
            prompt_tokens: 0,
            completion_tokens: 0,
            cost: 0.0,
            busy_turn: None,
            busy_start: None,
            spin_i: 0,
            status_label: "idle".into(),
            agents: Vec::new(),
            approval: None,
            be: None,
            update_rx: Some(hydra_stdio::update::spawn_launch_check()),
            thread: "tui-main".into(),
            cwd,
            turn_n: 0,
            log_once: false,
            quit: false,
            assistant_at: None,
        };
        match Backend::spawn(|| {}) {
            Ok(be) => app.be = Some(be),
            Err(e) => app.transcript.push(format!("agent : {e}")),
        }
        app
    }

    pub fn session_info(&self) -> Vec<(&'static str, String)> {
        let rules_detected = std::path::Path::new(&self.cwd).join("AGENTS.md").exists()
            || std::path::Path::new(&self.cwd).join(".cursorrules").exists()
            || std::path::Path::new(&self.cwd).join("CLAUDE.md").exists();
        vec![
            ("Session", self.thread.clone()),
            ("Model", format!("{} (frontier)", self.model)),
            ("Dialect", self.dialect.clone()),
            ("Project", if rules_detected { "Detected (AGENTS.md)".into() } else { "None".into() }),
            ("Working Dir", self.cwd.clone()),
            ("Strategy", format!("{} (sliding, recall)", self.mode)),
        ]
    }

    pub fn tick(&mut self) {
        if let Some(rx) = &self.update_rx {
            while let Ok(line) = rx.try_recv() {
                self.transcript.push(format!("update : {line}"));
            }
        }
        if self.busy_turn.is_some() {
            self.spin_i = self.spin_i.wrapping_add(1);
        }
        self.drain();
    }

    pub fn on_char(&mut self, c: char) {
        if self.cursor_pos >= self.input.len() {
            self.input.push(c);
        } else {
            self.input.insert(self.cursor_pos, c);
        }
        self.cursor_pos += c.len_utf8();
    }

    pub fn backspace(&mut self) {
        if self.cursor_pos > 0 && !self.input.is_empty() {
            let mut prev_char_idx = 0;
            for (idx, _) in self.input.char_indices() {
                if idx < self.cursor_pos {
                    prev_char_idx = idx;
                } else {
                    break;
                }
            }
            self.input.remove(prev_char_idx);
            self.cursor_pos = prev_char_idx;
        }
    }

    pub fn delete(&mut self) {
        if self.cursor_pos < self.input.len() {
            self.input.remove(self.cursor_pos);
        }
    }

    pub fn cursor_left(&mut self) {
        if self.cursor_pos > 0 {
            let mut prev = 0;
            for (idx, _) in self.input.char_indices() {
                if idx < self.cursor_pos {
                    prev = idx;
                } else {
                    break;
                }
            }
            self.cursor_pos = prev;
        }
    }

    pub fn cursor_right(&mut self) {
        for (idx, ch) in self.input.char_indices() {
            if idx == self.cursor_pos {
                self.cursor_pos += ch.len_utf8();
                break;
            }
        }
    }

    pub fn scroll_by(&mut self, delta: isize) {
        if delta < 0 {
            self.autoscroll = false;
            self.scroll = self.scroll.saturating_sub((-delta) as usize);
        } else {
            self.scroll = self.scroll.saturating_add(delta as usize);
        }
    }

    pub fn candidates(&self) -> Vec<(&'static str, &'static str)> {
        let cmds = [
            ("/model", "alias picker; provider id listed here"),
            ("/endpoints", "query all local endpoints, free quota, and credit balances"),
            ("/budgets", "query inference credit balances and limits"),
            ("/credits", "alias for /budgets"),
            ("/usage", "display free endpoint quota usage remaining"),
            ("/free", "alias for /usage"),
            ("/mode", "switch context strategy: compact, sliding, recall"),
            ("/window", "set context window size in tokens"),
            ("/agents", "list active runner tasks"),
            ("/allow", "approve pending tool call"),
            ("/skip", "skip pending tool call"),
            ("/abort", "abort running turn"),
            ("/clear", "reset session transcript and memory"),
            ("/help", "slash command and key reference"),
            ("/quit", "leave hydra"),
            ("/exit", "leave hydra"),
        ];
        if !self.input.starts_with('/') || self.input.contains(' ') {
            return Vec::new();
        }
        let prefix = self.input.to_lowercase();
        cmds.iter()
            .filter(|(cmd, _)| cmd.starts_with(&prefix))
            .copied()
            .collect()
    }

    pub fn complete(&mut self) {
        let cands = self.candidates();
        if cands.is_empty() {
            return;
        }
        self.input = format!("{} ", cands[0].0);
        self.cursor_pos = self.input.len();
    }

    pub fn submit(&mut self) {
        self.autoscroll = true;
        let text = self.input.trim().to_string();
        if text.is_empty() {
            return;
        }
        self.input.clear();
        self.cursor_pos = 0;
        self.transcript.push(format!("you : {text}"));
        if let Some(rest) = text.strip_prefix('/') {
            self.command(rest);
            return;
        }
        if self.busy_turn.is_some() {
            self.transcript.push("busy : one turn at a time".into());
            return;
        }
        let Some(be) = self.be.as_mut() else {
            self.transcript.push("agent : not connected".into());
            return;
        };
        self.turn_n += 1;
        let turn = format!("t{}", self.turn_n);
        let chars = self.window.saturating_mul(4).max(4096);
        be.turn_mode(&turn, &text, &self.thread, &self.cwd, &self.model, false, &self.mode, chars);
        self.busy_turn = Some(turn.clone());
        self.busy_start = Some(Instant::now());
        self.status_label = "working".into();
        self.transcript.push(String::new());
        self.assistant_at = Some(self.transcript.len() - 1);
        self.agents.push(AgentRow { id: turn, state: "running".into(), label: clip_label(&text) });
    }

    pub fn cancel(&mut self) {
        if let Some(turn) = self.busy_turn.clone() {
            if let Some(be) = self.be.as_mut() {
                be.cancel(&turn);
            }
            self.status_label = "cancel".into();
            self.busy_turn = None;
            self.busy_start = None;
            self.transcript.push("hydra : turn cancelled by user".into());
        } else if self.approval.is_some() {
            self.decide("abort");
        }
    }

    pub fn decide(&mut self, decision: &str) {
        if let Some((id, _)) = self.approval.take() {
            if let Some(be) = self.be.as_mut() {
                be.approve(&id, decision);
            }
            self.transcript.push(format!("approval {id} : {decision}"));
        }
    }

    fn command(&mut self, rest: &str) {
        let mut parts = rest.split_whitespace();
        let name = parts.next().unwrap_or("");
        match name {
            "help" => {
                self.transcript.push("--- hydra slash commands ---".into());
                for (cmd, desc) in [
                    ("/model [alias]", "alias picker; provider id listed here"),
                    ("/endpoints", "query all local endpoints, free quota, and credit balances"),
                    ("/budgets", "query inference credit balances and limits"),
                    ("/usage", "display free endpoint quota usage remaining"),
                    ("/mode [compact|sliding|recall]", "switch context strategy"),
                    ("/window [tokens]", "set context window size in tokens"),
                    ("/agents", "list active runner tasks"),
                    ("/allow", "approve pending tool call"),
                    ("/skip", "skip pending tool call"),
                    ("/abort", "abort running turn"),
                    ("/clear", "reset session transcript"),
                    ("/help", "slash command and key reference"),
                    ("/quit", "leave hydra"),
                ] {
                    self.transcript.push(format!("  {cmd:30} : {desc}"));
                }
            }
            "clear" => {
                self.transcript.clear();
            }
            "quit" | "exit" => {
                self.quit = true;
            }
            "mode" | "strategy" => {
                let mode = parts.next().unwrap_or("").to_string();
                if matches!(mode.as_str(), "compact" | "sliding" | "recall") {
                    self.mode = mode;
                    self.transcript.push(format!("mode : {}", self.mode));
                } else {
                    self.transcript.push("mode : compact, sliding, recall".into());
                }
            }
            "model" => {
                let model = parts.collect::<Vec<_>>().join(" ");
                if model.is_empty() {
                    self.transcript.push(format!("model : {}", self.model));
                } else {
                    self.model = model;
                    self.transcript.push(format!("model : {}", self.model));
                }
            }
            "window" => {
                if let Some(n) = parts.next().and_then(|s| s.parse::<u64>().ok()) {
                    self.window = n.max(1024);
                    self.transcript.push(format!("window : {} tokens", self.window));
                } else {
                    self.transcript.push(format!("window : {} tokens", self.window));
                }
            }
            "endpoints" => {
                self.transcript.push("--- local inference endpoints ---".into());
                for row in local_endpoints() {
                    let state = if row.open { "open" } else { "closed" };
                    self.transcript.push(format!("endpoint {} : {} [{state}]", row.name, row.target));
                }
                self.transcript.push("--- free quota remaining ---".into());
                for row in free_usage() {
                    self.transcript.push(format!("free quota {} : {}", row.provider, row.detail));
                }
                self.transcript.push("--- provider credit balances ---".into());
                for row in budgets() {
                    self.transcript.push(format!("credit balance {} : {}", row.provider, row.detail));
                }
            }
            "budgets" | "credits" => {
                self.transcript.push("--- provider credit balances ---".into());
                for row in budgets() {
                    self.transcript.push(format!("credit balance {} : {}", row.provider, row.detail));
                }
            }
            "usage" | "free" => {
                self.transcript.push("--- free quota remaining ---".into());
                for row in free_usage() {
                    self.transcript.push(format!("free quota {} : {}", row.provider, row.detail));
                }
            }
            "agents" => {
                if self.agents.is_empty() {
                    self.transcript.push("agents : none".into());
                }
                for row in &self.agents {
                    self.transcript.push(format!("{} : {} {}", row.id, row.state, row.label));
                }
            }
            "cancel" => self.cancel(),
            "allow" => self.decide("allow"),
            "skip" => self.decide("skip"),
            "abort" => self.decide("abort"),
            other => self.transcript.push(format!("unknown command : {other}")),
        }
    }

    fn drain(&mut self) {
        let evs: Vec<Ev> = if let Some(be) = &self.be {
            let mut v = Vec::new();
            while let Ok(ev) = be.rx.try_recv() {
                v.push(ev);
            }
            v
        } else {
            Vec::new()
        };
        for ev in evs {
            self.apply(ev);
        }
    }

    fn apply(&mut self, ev: Ev) {
        match ev {
            Ev::Hello => self.transcript.push("agent : hello".into()),
            Ev::Token { text, .. } => {
                if let Some(i) = self.assistant_at {
                    if let Some(line) = self.transcript.get_mut(i) {
                        line.push_str(&text);
                    }
                } else {
                    self.transcript.push(text);
                    self.assistant_at = Some(self.transcript.len() - 1);
                }
                self.status_label = "working".into();
            }
            Ev::ToolStart { tool, .. } => {
                self.assistant_at = None;
                self.status_label = format!("tool {tool}");
                self.transcript.push(format!("tool : {tool}"));
            }
            Ev::ToolResult { tool, is_error, output, .. } => {
                let flag = if is_error { "error" } else { "ok" };
                let out_clean = output.trim();
                if out_clean.contains('\n') {
                    self.transcript.push(format!("tool {tool} {flag} :"));
                    for line in out_clean.lines() {
                        self.transcript.push(format!("  {line}"));
                    }
                } else if !out_clean.is_empty() {
                    self.transcript.push(format!("tool {tool} {flag} : {out_clean}"));
                } else {
                    self.transcript.push(format!("tool {tool} {flag}"));
                }
            }
            Ev::Diff { path, .. } => self.transcript.push(format!("diff : {path}")),
            Ev::Usage { prompt, completion, cost, model } => {
                self.prompt_tokens = self.prompt_tokens.saturating_add(prompt);
                self.completion_tokens = self.completion_tokens.saturating_add(completion);
                self.cost += cost;
                self.model = if model.is_empty() { self.model.clone() } else { model };
            }
            Ev::Approval { id, tool, .. } => {
                self.approval = Some((id.clone(), tool.clone()));
                self.status_label = format!("approval {tool}");
                self.transcript.push(format!("approval {id} : {tool}  /allow /skip /abort"));
            }
            Ev::Done { turn, text, ok, cancelled } => {
                if !text.is_empty() {
                    if let Some(i) = self.assistant_at {
                        if let Some(line) = self.transcript.get_mut(i) {
                            if line.is_empty() {
                                *line = text;
                            }
                        }
                    } else {
                        self.transcript.push(text);
                    }
                }
                let state = if cancelled {
                    "cancelled"
                } else if ok {
                    "done"
                } else {
                    "failed"
                };
                self.mark_agent(&turn, state);
                if self.busy_turn.as_deref() == Some(turn.as_str()) {
                    self.busy_turn = None;
                    self.busy_start = None;
                    self.status_label = "idle".into();
                    self.assistant_at = None;
                }
            }
            Ev::Error { code, message, turn, .. } => {
                self.transcript.push(format!("error {code} : {message}"));
                if let Some(turn) = turn {
                    self.mark_agent(&turn, "error");
                    if self.busy_turn.as_deref() == Some(turn.as_str()) {
                        self.busy_turn = None;
                        self.busy_start = None;
                        self.status_label = "idle".into();
                        self.assistant_at = None;
                    }
                }
            }
            Ev::ActionResult { id, action, result } => {
                let res_str = result.to_string();
                let clean = res_str.trim();
                if clean.contains('\n') {
                    self.transcript.push(format!("action {action} {id} :"));
                    for line in clean.lines() {
                        self.transcript.push(format!("  {line}"));
                    }
                } else {
                    self.transcript.push(format!("action {action} {id} : {clean}"));
                }
            }
            Ev::Exited(reason) => {
                self.transcript.push(format!("agent exited : {reason}"));
                self.busy_turn = None;
                self.busy_start = None;
                self.status_label = "idle".into();
                self.be = None;
            }
        }
    }

    fn mark_agent(&mut self, id: &str, state: &str) {
        if let Some(row) = self.agents.iter_mut().rev().find(|a| a.id == id) {
            row.state = state.into();
        }
    }

    pub fn quit_requested(&self) -> bool {
        self.quit
    }

    pub fn frame(&self) -> char {
        SPIN[self.spin_i % SPIN.len()]
    }

    #[allow(dead_code)]
    pub fn footer(&self) -> String {
        let used = self.prompt_tokens.saturating_add(self.completion_tokens);
        let running = self.agents.iter().filter(|a| a.state == "running").count();
        format!(
            "mode {}  model {}  ctx {}/{}  cost ${:.4}  agents {}",
            self.mode, self.model, used, self.window, self.cost, running
        )
    }
}

fn clip_label(text: &str) -> String {
    let flat: String = text.chars().map(|c| if c == '\n' { ' ' } else { c }).take(80).collect();
    flat
}

pub fn log_layout(width: u16, fit_spin: usize, fit_label: usize, overflow: bool, thinking_in_status: bool, same_row: bool) {
    // #region agent log
    let path = r"C:\Users\jpm05\documents\debug-ada62c.log";
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0);
    let rows = [
        ("A", "status row width", serde_json::json!({"width": width, "spin_cols": fit_spin, "label_cols": fit_label, "overflow": overflow})),
        ("B", "thinking stays off the status row", serde_json::json!({"thinking_in_status": thinking_in_status})),
        ("C", "spinner column width", serde_json::json!({"spin_cols": fit_spin})),
        ("D", "status and footer are separate rows", serde_json::json!({"same_row": same_row})),
    ];
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        use std::io::Write;
        for (id, message, data) in rows {
            let line = serde_json::json!({
                "sessionId": "ada62c",
                "timestamp": now,
                "location": "hydra-tui-rs/src/app.rs",
                "message": message,
                "data": data,
                "hypothesisId": id,
                "runId": "tui-layout"
            });
            let _ = writeln!(f, "{line}");
        }
    }
    // #endregion
}
