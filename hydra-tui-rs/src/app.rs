//! Terminal session state. Context mode defaults to compact. Recall stays available and is not the default.

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
    pub transcript: Vec<String>,
    pub scroll: usize,
    pub autoscroll: bool,
    pub mode: String,
    pub model: String,
    pub window: u64,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub cost: f64,
    pub busy_turn: Option<String>,
    pub spin_i: usize,
    pub status_label: String,
    pub agents: Vec<AgentRow>,
    pub approval: Option<(String, String)>,
    pub be: Option<Backend>,
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
        let mut app = Self {
            input: String::new(),
            transcript: vec![
                "hydra tui : rust".into(),
                "mode compact. /help lists commands.".into(),
            ],
            scroll: 0,
            autoscroll: true,
            mode: "compact".into(),
            model: "mimo 2.6 pro".into(),
            window: std::env::var("HYDRA_CONTEXT_WINDOW").ok().and_then(|s| s.parse().ok()).unwrap_or(1_048_576),
            prompt_tokens: 0,
            completion_tokens: 0,
            cost: 0.0,
            busy_turn: None,
            spin_i: 0,
            status_label: "idle".into(),
            agents: Vec::new(),
            approval: None,
            be: None,
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

    pub fn tick(&mut self) {
        if self.busy_turn.is_some() {
            self.spin_i = self.spin_i.wrapping_add(1);
        }
        self.drain();
    }

    pub fn on_char(&mut self, c: char) {
        self.input.push(c);
    }

    pub fn backspace(&mut self) {
        self.input.pop();
    }

    pub fn scroll_by(&mut self, delta: isize) {
        if delta < 0 {
            self.autoscroll = false;
            self.scroll = self.scroll.saturating_sub((-delta) as usize);
        } else {
            self.scroll = self.scroll.saturating_add(delta as usize);
        }
    }

    pub fn submit(&mut self) {
        self.autoscroll = true;
        let text = self.input.trim().to_string();
        if text.is_empty() {
            return;
        }
        self.input.clear();
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
                for line in [
                    "/help",
                    "/mode compact | sliding | recall",
                    "/model <alias>",
                    "/window <tokens>",
                    "/endpoints",
                    "/budgets (or /credits)",
                    "/usage (or /free)",
                    "/agents",
                    "/allow  /skip  /abort",
                    "/cancel",
                    "/quit",
                ] {
                    self.transcript.push(line.into());
                }
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
                for row in budgets() {
                    self.transcript.push(format!("credit balance {} : {}", row.provider, row.detail));
                }
            }
            "usage" | "free" => {
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
                    self.transcript.push(text);
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

