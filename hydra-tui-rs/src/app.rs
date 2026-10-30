//! Terminal session state. Context mode defaults to compact. Recall stays available and is not the default.

use std::path::PathBuf;
use std::process::Command;
use std::time::Instant;
use hydra_stdio::{Backend, Ev};
use serde_json::{json, Value};

use crate::endpoints::{budgets, free_usage, local_endpoints};
use crate::layout::SPIN;

pub const DEFAULT_AGENT_MODEL: &str = "bonsai 2 9b";
pub const DEFAULT_FLASH_MODEL: &str = "mimo 2.6 flash";
pub const SPEED_DIAL_PRO_MODEL: &str = "mimo 2.6 pro";
pub const FALLBACK_GLM_MODEL: &str = "glm 5.3 flash";

pub const MODEL_COMPLETIONS: &[(&str, &str)] = &[
    ("bonsai 2 9b", "Ternary Bonsai 2 9B · 2GB RAM · default agent (Hydra + Progen adapters)"),
    ("bonsai 2 2b", "Ternary Bonsai 2 2B · 500MB RAM · ultra-light local agent"),
    ("bonsai 2 4b", "Ternary Bonsai 2 4B · 1GB RAM · compact local agent"),
    ("bonsai 2 27b", "Ternary Bonsai 2 27B · 6GB RAM · flagship ternary reasoning"),
    ("mimo 2.6 flash", "Xiaomi MiMo-V2-Flash · fast coding default"),
    ("mimo 2.6 pro", "Xiaomi MiMo-V2-Pro · heavy frontier reasoning"),
    ("glm 5.3 flash", "GLM-4.7-Flash · reliable fallback"),
    ("deepseek 4.1 flash", "DeepSeek V3.2 · fast reasoning"),
    ("opus 5.5", "Claude Opus 4.6 · frontier architecture"),
    ("sonnet 5.5", "Claude Sonnet 4.6 · balanced coding"),
    ("sol 6.1 pro", "GPT-5.4 Pro · deep reasoning"),
    ("free", "OpenRouter / Cloudflare free pool"),
    ("local", "Local Ollama / llama.cpp endpoint"),
];

pub fn normalize_model_alias(raw: &str) -> String {
    let trimmed = raw.trim();
    match trimmed.to_lowercase().as_str() {
        "bonsai" | "bonsai 9b" | "bonsai-2-9b" | "bonsai 2 9b" | "bonsai.2.9b" => DEFAULT_AGENT_MODEL.into(),
        "bonsai 2b" | "bonsai-2-2b" | "bonsai 2 2b" | "bonsai.2.2b" => "bonsai 2 2b".into(),
        "bonsai 4b" | "bonsai-2-4b" | "bonsai 2 4b" | "bonsai.2.4b" => "bonsai 2 4b".into(),
        "bonsai 27b" | "bonsai-2-27b" | "bonsai 2 27b" | "bonsai.2.27b" => "bonsai 2 27b".into(),
        "mimo.2.6 flash" | "mimo-2.6-flash" | "mimo 2.6 flash" => DEFAULT_FLASH_MODEL.into(),
        "mimo.2.6 pro" | "mimo-2.6-pro" | "mimo 2.6 pro" => SPEED_DIAL_PRO_MODEL.into(),
        "glm.5.3 flash" | "glm-5.3-flash" | "glm 5.3 flash" => FALLBACK_GLM_MODEL.into(),
        _ => trimmed.to_string(),
    }
}

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
    pub fallback_model: String,
    pub speed_dial_model: String,
    pub permission: String,
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
    pub approval_args: Value,
    pub queued: Vec<String>,
    pub steer_after_cancel: bool,
    pub history: Vec<String>,
    pub history_idx: Option<usize>,
    pub history_draft: String,
    pub popup_idx: usize,
    pub diffs: Vec<(String, String)>,
    pub active_skills: Vec<String>,
    pub last_checkpoint: Option<String>,
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
        let dialect = std::env::var("HYDRA_DIALECT")
            .ok()
            .map(|s| s.trim().to_lowercase())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "slack".into());
        let raw_model = std::env::var("HYDRA_DEFAULT_MODEL")
            .or_else(|_| std::env::var("HYDRA_MODEL"))
            .unwrap_or_else(|_| DEFAULT_AGENT_MODEL.into());
        let model = normalize_model_alias(&raw_model);
        let permission = std::env::var("HYDRA_PERMISSION")
            .ok()
            .filter(|s| matches!(s.as_str(), "plan" | "manual" | "accept_edits" | "bypass"))
            .unwrap_or_else(|| "accept_edits".into());

        let no_state = cfg!(test) || std::env::var("HYDRA_NO_STATE").ok().as_deref() == Some("1");
        let history = if no_state { Vec::new() } else { load_history_file() };

        let mut app = Self {
            input: String::new(),
            cursor_pos: 0,
            transcript: Vec::new(),
            scroll: 0,
            autoscroll: true,
            mode: "compact".into(),
            model,
            fallback_model: FALLBACK_GLM_MODEL.into(),
            speed_dial_model: SPEED_DIAL_PRO_MODEL.into(),
            permission,
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
            approval_args: Value::Null,
            queued: Vec::new(),
            steer_after_cancel: false,
            history,
            history_idx: None,
            history_draft: String::new(),
            popup_idx: 0,
            diffs: Vec::new(),
            active_skills: Vec::new(),
            last_checkpoint: None,
            be: None,
            update_rx: if cfg!(test) { None } else { Some(hydra_stdio::update::spawn_launch_check()) },
            thread: "tui-main".into(),
            cwd,
            turn_n: 0,
            log_once: false,
            quit: false,
            assistant_at: None,
        };
        if !cfg!(test) && std::env::var("HYDRA_NO_BACKEND").ok().as_deref() != Some("1") {
            match Backend::spawn(|| {}) {
                Ok(be) => app.be = Some(be),
                Err(e) => app.transcript.push(format!("agent : {e}")),
            }
        }
        app
    }

    pub fn session_info(&self) -> Vec<(&'static str, String)> {
        let rules_detected = std::path::Path::new(&self.cwd).join("AGENTS.md").exists()
            || std::path::Path::new(&self.cwd).join(".cursorrules").exists()
            || std::path::Path::new(&self.cwd).join("CLAUDE.md").exists();
        vec![
            ("Session", self.thread.clone()),
            ("Version", hydra_stdio::update::status_line()),
            ("Model", format!("{} (fallback: {})", self.model, self.fallback_model)),
            ("Permission", self.permission.clone()),
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
        self.popup_idx = 0;
    }

    pub fn insert_newline(&mut self) {
        self.on_char('\n');
    }

    pub fn paste_text(&mut self, raw: &str) {
        let cleaned = raw.replace("\r\n", "\n").replace('\r', "\n");
        if cleaned.is_empty() {
            return;
        }
        if self.cursor_pos >= self.input.len() {
            self.input.push_str(&cleaned);
        } else {
            self.input.insert_str(self.cursor_pos, &cleaned);
        }
        self.cursor_pos += cleaned.len();
        self.popup_idx = 0;
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
            self.popup_idx = 0;
        }
    }

    pub fn delete(&mut self) {
        if self.cursor_pos < self.input.len() {
            self.input.remove(self.cursor_pos);
            self.popup_idx = 0;
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

    pub fn cursor_home(&mut self) {
        let pos = self.cursor_pos.min(self.input.len());
        let line_start = self.input[..pos].rfind('\n').map(|i| i + 1).unwrap_or(0);
        self.cursor_pos = line_start;
    }

    pub fn cursor_end(&mut self) {
        let pos = self.cursor_pos.min(self.input.len());
        let line_end = self.input[pos..].find('\n').map(|i| pos + i).unwrap_or(self.input.len());
        self.cursor_pos = line_end;
    }

    fn prev_word_boundary(&self, from: usize) -> usize {
        let pos = from.min(self.input.len());
        let chars: Vec<(usize, char)> = self.input[..pos].char_indices().collect();
        let mut i = chars.len();
        while i > 0 && !is_word_char(chars[i - 1].1) {
            i -= 1;
        }
        while i > 0 && is_word_char(chars[i - 1].1) {
            i -= 1;
        }
        if i == 0 { 0 } else { chars[i].0 }
    }

    fn next_word_boundary(&self, from: usize) -> usize {
        let pos = from.min(self.input.len());
        let chars: Vec<(usize, char)> = self.input[pos..].char_indices().collect();
        let mut i = 0;
        while i < chars.len() && !is_word_char(chars[i].1) {
            i += 1;
        }
        while i < chars.len() && is_word_char(chars[i].1) {
            i += 1;
        }
        if i >= chars.len() {
            self.input.len()
        } else {
            pos + chars[i].0
        }
    }

    pub fn cursor_word_left(&mut self) {
        self.cursor_pos = self.prev_word_boundary(self.cursor_pos);
    }

    pub fn cursor_word_right(&mut self) {
        self.cursor_pos = self.next_word_boundary(self.cursor_pos);
    }

    pub fn delete_word_back(&mut self) {
        let target = self.prev_word_boundary(self.cursor_pos);
        if target < self.cursor_pos {
            self.input.replace_range(target..self.cursor_pos, "");
            self.cursor_pos = target;
            self.popup_idx = 0;
        }
    }

    pub fn delete_to_line_start(&mut self) {
        let pos = self.cursor_pos.min(self.input.len());
        let line_start = self.input[..pos].rfind('\n').map(|i| i + 1).unwrap_or(0);
        if line_start < pos {
            self.input.replace_range(line_start..pos, "");
            self.cursor_pos = line_start;
            self.popup_idx = 0;
        }
    }

    pub fn delete_to_line_end(&mut self) {
        let pos = self.cursor_pos.min(self.input.len());
        let line_end = self.input[pos..].find('\n').map(|i| pos + i).unwrap_or(self.input.len());
        if pos < line_end {
            self.input.replace_range(pos..line_end, "");
            self.popup_idx = 0;
        } else if pos < self.input.len() && self.input.as_bytes()[pos] == b'\n' {
            self.input.remove(pos);
            self.popup_idx = 0;
        }
    }

    pub fn history_up(&mut self) {
        if self.history.is_empty() {
            return;
        }
        let next_idx = match self.history_idx {
            None => {
                self.history_draft = self.input.clone();
                self.history.len() - 1
            }
            Some(0) => 0,
            Some(i) => i - 1,
        };
        self.history_idx = Some(next_idx);
        self.input = self.history[next_idx].clone();
        self.cursor_pos = self.input.len();
        self.popup_idx = 0;
    }

    pub fn history_down(&mut self) {
        let Some(idx) = self.history_idx else {
            return;
        };
        if idx + 1 < self.history.len() {
            let next_idx = idx + 1;
            self.history_idx = Some(next_idx);
            self.input = self.history[next_idx].clone();
            self.cursor_pos = self.input.len();
        } else {
            self.history_idx = None;
            self.input = std::mem::take(&mut self.history_draft);
            self.cursor_pos = self.input.len();
        }
        self.popup_idx = 0;
    }

    pub fn on_up(&mut self) {
        let cands = self.candidates();
        if !cands.is_empty() && self.history_idx.is_none() {
            if self.popup_idx > 0 {
                self.popup_idx -= 1;
            } else {
                self.popup_idx = cands.len().saturating_sub(1);
            }
            return;
        }
        let pos = self.cursor_pos.min(self.input.len());
        if let Some(prev_nl) = self.input[..pos].rfind('\n') {
            let col = pos - (prev_nl + 1);
            let prev_line_start = self.input[..prev_nl].rfind('\n').map(|i| i + 1).unwrap_or(0);
            let prev_line_len = prev_nl - prev_line_start;
            self.cursor_pos = prev_line_start + col.min(prev_line_len);
            return;
        }
        self.history_up();
    }

    pub fn on_down(&mut self) {
        let cands = self.candidates();
        if !cands.is_empty() && self.history_idx.is_none() {
            self.popup_idx = (self.popup_idx + 1) % cands.len();
            return;
        }
        let pos = self.cursor_pos.min(self.input.len());
        if let Some(rel_nl) = self.input[pos..].find('\n') {
            let curr_nl = pos + rel_nl;
            let curr_line_start = self.input[..pos].rfind('\n').map(|i| i + 1).unwrap_or(0);
            let col = pos - curr_line_start;
            let next_line_start = curr_nl + 1;
            let next_line_end = self.input[next_line_start..]
                .find('\n')
                .map(|i| next_line_start + i)
                .unwrap_or(self.input.len());
            let next_line_len = next_line_end - next_line_start;
            self.cursor_pos = next_line_start + col.min(next_line_len);
            return;
        }
        self.history_down();
    }

    pub fn scroll_by(&mut self, delta: isize) {
        if delta < 0 {
            self.autoscroll = false;
            self.scroll = self.scroll.saturating_sub((-delta) as usize);
        } else {
            self.scroll = self.scroll.saturating_add(delta as usize);
        }
    }

    fn current_at_token(&self) -> Option<(usize, usize, String)> {
        let pos = self.cursor_pos.min(self.input.len());
        let before = &self.input[..pos];
        let token_start = before
            .rfind(|c: char| c.is_whitespace())
            .map(|i| i + 1)
            .unwrap_or(0);
        let token = &before[token_start..];
        if let Some(query) = token.strip_prefix('@') {
            Some((token_start, pos, query.to_string()))
        } else {
            None
        }
    }

    pub fn candidates(&self) -> Vec<(String, String)> {
        if let Some((_, _, query)) = self.current_at_token() {
            let files = find_workspace_files(&self.cwd, &query, 8);
            return files
                .into_iter()
                .map(|rel| (format!("@{rel}"), "workspace file".to_string()))
                .collect();
        }

        if let Some(rest) = self.input.strip_prefix("/model ") {
            let q = rest.trim().to_lowercase();
            return MODEL_COMPLETIONS
                .iter()
                .filter(|(alias, _)| alias.contains(&q) && *alias != q)
                .map(|(alias, desc)| (format!("/model {alias}"), (*desc).to_string()))
                .collect();
        }

        let cmds: &[(&str, &str)] = &[
            ("/model", "switch active model alias (default: mimo 2.6 flash)"),
            ("/pro", "speed dial heavy frontier model (mimo 2.6 pro)"),
            ("/flash", "switch back to default fast coding model (mimo 2.6 flash)"),
            ("/fallback", "switch to fallback model (glm 5.3 flash)"),
            ("/plan", "read-only planning permission mode"),
            ("/accept", "accept file edits automatically, gate shell commands"),
            ("/ask", "manual approval for edits and commands"),
            ("/bypass", "auto-approve all tool calls in this session"),
            ("/permission", "set permission mode: plan, manual, accept_edits, bypass"),
            ("/steer", "interrupt running turn and dispatch queued instruction now"),
            ("/diff", "inspect unified diffs from this session"),
            ("/checkpoints", "list shadow git checkpoints for this thread"),
            ("/rewind", "rewind workspace and conversation to a checkpoint"),
            ("/rules", "list or remove always-allow approval rules"),
            ("/always", "approve pending tool call and save always-allow rule"),
            ("/skills", "list available skills or toggle a skill on/off"),
            ("/mcp", "inspect, refresh, or toggle MCP servers"),
            ("/tasks", "list background shell tasks"),
            ("/stop", "stop a running background task"),
            ("/sessions", "list saved conversation threads"),
            ("/resume", "resume a saved conversation thread"),
            ("/new", "start a fresh conversation thread"),
            ("/swarm", "dispatch multi-head paid swarm on a task"),
            ("/sh", "run a local shell command in the working directory"),
            ("/endpoints", "query all local endpoints, free quota, and credit balances"),
            ("/budgets", "query inference credit balances and limits"),
            ("/credits", "alias for /budgets"),
            ("/usage", "display free endpoint quota usage remaining"),
            ("/free", "alias for /usage"),
            ("/mode", "switch context strategy: compact, sliding, recall"),
            ("/window", "set context window size in tokens"),
            ("/agents", "list active runner tasks"),
            ("/update", "check version and pull latest from github"),
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
            .map(|(cmd, desc)| ((*cmd).to_string(), (*desc).to_string()))
            .collect()
    }

    pub fn complete(&mut self) {
        let cands = self.candidates();
        if cands.is_empty() {
            return;
        }
        let idx = self.popup_idx.min(cands.len() - 1);
        let chosen = &cands[idx].0;
        if let Some((start, end, _)) = self.current_at_token() {
            let replacement = format!("{chosen} ");
            self.input.replace_range(start..end, &replacement);
            self.cursor_pos = start + replacement.len();
            self.popup_idx = 0;
            return;
        }
        self.input = format!("{chosen} ");
        self.cursor_pos = self.input.len();
        self.popup_idx = 0;
    }

    fn record_history(&mut self, text: &str) {
        if text.is_empty() {
            return;
        }
        if self.history.last().map(|s| s.as_str()) != Some(text) {
            self.history.push(text.to_string());
            if self.history.len() > 500 {
                let drain_n = self.history.len() - 500;
                self.history.drain(0..drain_n);
            }
            let no_state = cfg!(test) || std::env::var("HYDRA_NO_STATE").ok().as_deref() == Some("1");
            if !no_state {
                append_history_file(text);
            }
        }
        self.history_idx = None;
        self.history_draft.clear();
    }

    pub fn steer(&mut self) {
        let text = self.input.trim().to_string();
        if !text.is_empty() {
            self.input.clear();
            self.cursor_pos = 0;
            self.record_history(&text);
            self.queued.insert(0, text);
        }
        if self.queued.is_empty() {
            return;
        }
        if let Some(turn) = self.busy_turn.clone() {
            if let Some(be) = self.be.as_mut() {
                be.cancel(&turn);
            }
            self.steer_after_cancel = true;
            self.status_label = "steering".into();
            self.transcript.push("steering : interrupting current turn to dispatch queued instruction".into());
        } else {
            let next = self.queued.remove(0);
            self.transcript.push(format!("you : {next}"));
            self.dispatch_turn(&next);
        }
    }

    pub fn submit(&mut self) {
        self.autoscroll = true;
        let text = self.input.trim().to_string();
        if text.is_empty() {
            if self.approval.is_some() {
                self.decide("allow");
            } else if self.busy_turn.is_some() && !self.queued.is_empty() {
                self.steer();
            }
            return;
        }
        self.input.clear();
        self.cursor_pos = 0;
        self.popup_idx = 0;
        self.record_history(&text);

        if let Some(cmd) = text.strip_prefix('!') {
            self.transcript.push(format!("you : {text}"));
            self.run_shell_command(cmd);
            return;
        }
        if let Some(rest) = text.strip_prefix('/') {
            self.transcript.push(format!("you : {text}"));
            self.command(rest);
            return;
        }
        if self.busy_turn.is_some() {
            self.queued.push(text.clone());
            let n = self.queued.len();
            let preview = clip_label(&text);
            self.transcript.push(format!(
                "queued (#{n}) : {preview} (press Enter on empty box or Ctrl+Enter to steer now)"
            ));
            return;
        }
        self.transcript.push(format!("you : {text}"));
        self.dispatch_turn(&text);
    }

    fn dispatch_turn(&mut self, text: &str) {
        if self.be.is_none() && !cfg!(test) {
            self.transcript.push("agent : not connected".into());
            return;
        }
        self.turn_n += 1;
        let turn = format!("t{}", self.turn_n);
        let chars = self.window.saturating_mul(4).max(4096);
        if let Some(be) = self.be.as_mut() {
            be.send(json!({
                "type": "turn",
                "turn": turn,
                "prompt": text,
                "thread": self.thread,
                "cwd": self.cwd,
                "model": self.model,
                "permission": self.permission,
                "mode": self.mode,
                "context_chars": chars,
                "skills": self.active_skills,
            }));
        }
        self.busy_turn = Some(turn.clone());
        self.busy_start = Some(Instant::now());
        self.status_label = "working".into();
        self.transcript.push(String::new());
        self.assistant_at = Some(self.transcript.len() - 1);
        self.agents.push(AgentRow { id: turn, state: "running".into(), label: clip_label(text) });
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
            self.approval_args = Value::Null;
            if let Some(be) = self.be.as_mut() {
                be.approve(&id, decision);
            }
            self.transcript.push(format!("approval {id} : {decision}"));
        }
    }

    pub fn decide_always(&mut self, scope: &str, prefix: Option<&str>) {
        if let Some((id, _)) = self.approval.take() {
            self.approval_args = Value::Null;
            if let Some(be) = self.be.as_mut() {
                be.approve_always(&id, scope, prefix);
            }
            if let Some(p) = prefix {
                self.transcript.push(format!("approval {id} : allow_always ({scope}, prefix: {p})"));
            } else {
                self.transcript.push(format!("approval {id} : allow_always ({scope})"));
            }
        }
    }

    fn run_shell_command(&mut self, cmd_str: &str) {
        let trimmed = cmd_str.trim();
        if trimmed.is_empty() {
            self.transcript.push("sh : provide a command".into());
            return;
        }
        self.transcript.push(format!("$ {trimmed}"));
        let mut cmd = if cfg!(windows) {
            let mut c = Command::new("cmd");
            c.args(["/C", trimmed]);
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                c.creation_flags(0x0800_0000);
            }
            c
        } else {
            let mut c = Command::new("sh");
            c.args(["-c", trimmed]);
            c
        };
        cmd.current_dir(&self.cwd);
        match cmd.output() {
            Ok(out) => {
                let stdout = String::from_utf8_lossy(&out.stdout);
                let stderr = String::from_utf8_lossy(&out.stderr);
                for line in stdout.lines().chain(stderr.lines()).take(80) {
                    self.transcript.push(format!("  {line}"));
                }
                let code = out.status.code().unwrap_or(-1);
                if code != 0 {
                    self.transcript.push(format!("sh exit : {code}"));
                }
            }
            Err(e) => self.transcript.push(format!("sh error : {e}")),
        }
    }

    fn command(&mut self, rest: &str) {
        let mut parts = rest.split_whitespace();
        let name = parts.next().unwrap_or("");
        match name {
            "help" => {
                self.transcript.push("--- hydra slash commands ---".into());
                for (cmd, desc) in [
                    ("/model [alias]", "switch active model (default: mimo 2.6 flash)"),
                    ("/pro", "speed dial heavy frontier model (mimo 2.6 pro)"),
                    ("/flash", "switch to fast daily driver (mimo 2.6 flash)"),
                    ("/fallback", "switch to fallback model (glm 5.3 flash)"),
                    ("/plan | /accept | /ask | /bypass", "switch tool permission mode"),
                    ("/steer [prompt]", "interrupt running turn and dispatch queued prompt"),
                    ("/diff [n|path]", "render unified diffs recorded in this session"),
                    ("/checkpoints | /rewind [id]", "list shadow git snapshots or rewind workspace"),
                    ("/always [prefix] | /rules", "manage always-allow tool approval rules"),
                    ("/skills [id]", "list skills or toggle a skill active for turns"),
                    ("/mcp [refresh|on|off]", "inspect or toggle MCP servers"),
                    ("/tasks | /stop <id>", "inspect or stop background tasks"),
                    ("/sessions | /resume <id> | /new", "manage conversation threads"),
                    ("/swarm <task>", "dispatch multi-head paid swarm synthesis"),
                    ("/sh <cmd> or !<cmd>", "execute local shell command in working dir"),
                    ("/endpoints | /budgets | /usage", "query local endpoints, credits, and free quota"),
                    ("/mode [compact|sliding|recall]", "switch context strategy"),
                    ("/window [tokens]", "set context window size in tokens"),
                    ("/agents", "list active runner tasks"),
                    ("/update", "check version and pull latest from github"),
                    ("/allow | /skip | /abort", "respond to pending tool approval"),
                    ("/clear", "reset session transcript"),
                    ("/quit", "leave hydra"),
                ] {
                    self.transcript.push(format!("  {cmd:32} : {desc}"));
                }
            }
            "clear" => {
                self.transcript.clear();
            }
            "quit" | "exit" | "q" => {
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
                let raw = parts.collect::<Vec<_>>().join(" ");
                if raw.is_empty() {
                    self.transcript.push(format!(
                        "model : {} (pro: {}, fallback: {})",
                        self.model, self.speed_dial_model, self.fallback_model
                    ));
                } else {
                    self.model = normalize_model_alias(&raw);
                    self.transcript.push(format!("model : {}", self.model));
                }
            }
            "fallback" => {
                self.model = self.fallback_model.clone();
                self.transcript.push(format!("model : {}", self.model));
            }
            "pro" => {
                self.model = self.speed_dial_model.clone();
                self.transcript.push(format!("model : {}", self.model));
            }
            "flash" => {
                self.model = DEFAULT_FLASH_MODEL.into();
                self.transcript.push(format!("model : {}", self.model));
            }
            "plan" => {
                self.permission = "plan".into();
                self.transcript.push("permission : plan".into());
            }
            "ask" | "manual" => {
                self.permission = "manual".into();
                self.transcript.push("permission : manual".into());
            }
            "accept" | "accept_edits" => {
                self.permission = "accept_edits".into();
                self.transcript.push("permission : accept_edits".into());
            }
            "bypass" | "auto" => {
                self.permission = "bypass".into();
                self.transcript.push("permission : bypass".into());
            }
            "permission" | "perm" => {
                let arg = parts.next().unwrap_or("");
                let mapped = match arg {
                    "plan" => Some("plan"),
                    "ask" | "manual" => Some("manual"),
                    "accept" | "accept_edits" => Some("accept_edits"),
                    "bypass" | "auto" => Some("bypass"),
                    _ => None,
                };
                if let Some(p) = mapped {
                    self.permission = p.into();
                    self.transcript.push(format!("permission : {}", self.permission));
                } else {
                    self.transcript.push(format!(
                        "permission : {} (options: plan, manual, accept_edits, bypass)",
                        self.permission
                    ));
                }
            }
            "progen" => {
                self.dialect = "progen".into();
                self.transcript.push("dialect : progen".into());
            }
            "slack" | "english" => {
                self.dialect = "slack".into();
                self.transcript.push("dialect : slack".into());
            }
            "dialect" | "register" => {
                let arg = parts.next().unwrap_or("");
                if matches!(arg, "progen" | "syntax" | "instruct" | "slack") {
                    self.dialect = arg.into();
                    self.transcript.push(format!("dialect : {}", self.dialect));
                } else {
                    self.transcript.push(format!(
                        "dialect : {} (options: slack, progen, syntax, instruct)",
                        self.dialect
                    ));
                }
            }
            "steer" => {
                let prompt = parts.collect::<Vec<_>>().join(" ");
                if !prompt.is_empty() {
                    self.queued.insert(0, prompt);
                }
                self.steer();
            }
            "always" => {
                let prefix = parts.collect::<Vec<_>>().join(" ");
                let p_opt = if prefix.is_empty() { None } else { Some(prefix.as_str()) };
                self.decide_always("thread", p_opt);
            }
            "rules" => {
                let sub = parts.next().unwrap_or("");
                let remove_id = if sub == "remove" { parts.next() } else { None };
                if let Some(be) = self.be.as_mut() {
                    be.rules(&self.thread, remove_id);
                } else {
                    self.transcript.push("agent : not connected".into());
                }
            }
            "diff" | "diffs" => {
                if self.diffs.is_empty() {
                    self.transcript.push("diffs : none recorded in this session".into());
                    return;
                }
                let arg = parts.collect::<Vec<_>>().join(" ");
                let chosen = if arg.is_empty() {
                    self.diffs.last()
                } else if let Ok(idx) = arg.parse::<usize>() {
                    let i = idx.saturating_sub(1).min(self.diffs.len() - 1);
                    self.diffs.get(i)
                } else {
                    self.diffs.iter().rev().find(|(p, _)| p.contains(&arg))
                };
                if let Some((path, diff_text)) = chosen {
                    self.transcript.push(format!("diff : {path}"));
                    for line in diff_text.lines() {
                        self.transcript.push(format!("  {line}"));
                    }
                } else {
                    self.transcript.push(format!("diffs : no match for {arg}"));
                }
            }
            "checkpoints" => {
                if let Some(be) = self.be.as_mut() {
                    be.checkpoints(&self.thread);
                } else {
                    self.transcript.push("agent : not connected".into());
                }
            }
            "rewind" => {
                let target = parts.next().unwrap_or("last");
                let resolved = if target == "last" {
                    self.last_checkpoint.clone().unwrap_or_default()
                } else {
                    target.to_string()
                };
                if resolved.is_empty() {
                    self.transcript.push("rewind : specify a checkpoint id or run /checkpoints first".into());
                    return;
                }
                if let Some(be) = self.be.as_mut() {
                    be.rewind(&self.thread, &resolved, true);
                } else {
                    self.transcript.push("agent : not connected".into());
                }
            }
            "skills" => {
                let sk = parts.next().unwrap_or("");
                if sk.is_empty() {
                    if !self.active_skills.is_empty() {
                        self.transcript.push(format!("active skills : {}", self.active_skills.join(", ")));
                    }
                    if let Some(be) = self.be.as_mut() {
                        be.skills();
                    } else if self.active_skills.is_empty() {
                        self.transcript.push("skills : none active".into());
                    }
                } else if let Some(pos) = self.active_skills.iter().position(|x| x == sk) {
                    self.active_skills.remove(pos);
                    self.transcript.push(format!("skills : disabled {sk}"));
                } else {
                    self.active_skills.push(sk.to_string());
                    self.transcript.push(format!("skills : enabled {sk}"));
                }
            }
            "mcp" => {
                let sub = parts.next().unwrap_or("");
                if sub == "on" || sub == "enable" {
                    if let Some(srv) = parts.next() {
                        if let Some(be) = self.be.as_mut() {
                            be.mcp_toggle(&self.cwd, srv, true);
                        } else {
                            self.transcript.push("agent : not connected".into());
                        }
                    }
                } else if sub == "off" || sub == "disable" {
                    if let Some(srv) = parts.next() {
                        if let Some(be) = self.be.as_mut() {
                            be.mcp_toggle(&self.cwd, srv, false);
                        } else {
                            self.transcript.push("agent : not connected".into());
                        }
                    }
                } else {
                    let refresh = sub == "refresh";
                    if let Some(be) = self.be.as_mut() {
                        be.mcp(&self.cwd, refresh);
                    } else {
                        self.transcript.push("agent : not connected".into());
                    }
                }
            }
            "tasks" => {
                if let Some(be) = self.be.as_mut() {
                    be.tasks();
                } else {
                    self.transcript.push("agent : not connected".into());
                }
            }
            "stop" => {
                let Some(tid) = parts.next() else {
                    self.transcript.push("stop : provide a task id".into());
                    return;
                };
                if let Some(be) = self.be.as_mut() {
                    be.task_stop(tid);
                } else {
                    self.transcript.push("agent : not connected".into());
                }
            }
            "sessions" => {
                if let Some(be) = self.be.as_mut() {
                    be.sessions();
                } else {
                    self.transcript.push("agent : not connected".into());
                }
            }
            "resume" => {
                let Some(tid) = parts.next() else {
                    self.transcript.push("resume : provide a thread id".into());
                    return;
                };
                let sanitized: String = tid.chars().filter(|c| c.is_alphanumeric() || matches!(c, '-' | '_' | '.')).collect();
                if sanitized.is_empty() {
                    self.transcript.push("resume : invalid thread id".into());
                    return;
                }
                self.thread = sanitized;
                self.transcript.push(format!("session : resumed {}", self.thread));
                if let Some(be) = self.be.as_mut() {
                    be.history(&self.thread, 50);
                }
            }
            "new" => {
                let custom = parts.next().unwrap_or("");
                let sanitized: String = custom.chars().filter(|c| c.is_alphanumeric() || matches!(c, '-' | '_' | '.')).collect();
                self.thread = if sanitized.is_empty() {
                    let secs = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_secs())
                        .unwrap_or(1);
                    format!("tui-{secs}")
                } else {
                    sanitized
                };
                self.transcript.clear();
                self.diffs.clear();
                self.turn_n = 0;
                self.transcript.push(format!("session : new thread {}", self.thread));
            }
            "swarm" => {
                let task = parts.collect::<Vec<_>>().join(" ");
                if task.is_empty() {
                    self.transcript.push("swarm : provide a task description".into());
                    return;
                }
                self.transcript.push(format!("swarm : dispatching paid swarm for {task}"));
                if let Some(be) = self.be.as_mut() {
                    be.action("run_swarm", json!({"task": task, "tier": "paid"}));
                } else {
                    self.transcript.push("agent : not connected".into());
                }
            }
            "sh" => {
                let cmd = parts.collect::<Vec<_>>().join(" ");
                self.run_shell_command(&cmd);
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
            "update" => {
                let (tx, rx) = std::sync::mpsc::channel();
                std::thread::spawn(move || {
                    let _ = tx.send(hydra_stdio::update::check_now());
                });
                self.update_rx = Some(rx);
                self.transcript.push("update : checking github origin/main...".into());
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

    pub fn apply(&mut self, ev: Ev) {
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
            Ev::ToolStart { tool, args, .. } => {
                self.assistant_at = None;
                let preview = summarize_tool_args(&args);
                if preview.is_empty() {
                    self.status_label = format!("tool {tool}");
                    self.transcript.push(format!("tool : {tool}"));
                } else {
                    self.status_label = format!("tool {tool} ({preview})");
                    self.transcript.push(format!("tool : {tool} ({preview})"));
                }
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
            Ev::Diff { path, diff, .. } => {
                self.diffs.push((path.clone(), diff.clone()));
                self.transcript.push(format!("diff : {path}"));
                for line in diff.lines().take(40) {
                    self.transcript.push(format!("  {line}"));
                }
            }
            Ev::Usage { prompt, completion, cost, model } => {
                self.prompt_tokens = self.prompt_tokens.saturating_add(prompt);
                self.completion_tokens = self.completion_tokens.saturating_add(completion);
                self.cost += cost;
                if !model.is_empty() {
                    self.model = normalize_model_alias(&model);
                }
            }
            Ev::Approval { id, tool, args, .. } => {
                let preview = summarize_tool_args(&args);
                self.approval = Some((id.clone(), tool.clone()));
                self.approval_args = args;
                self.status_label = format!("approval {tool}");
                let detail = if preview.is_empty() { tool } else { format!("{tool} ({preview})") };
                self.transcript.push(format!(
                    "approval {id} : {detail}  [y] allow · [a] always thread · [p] always project · [n] skip · [Esc] abort"
                ));
            }
            Ev::Checkpoint { ok, id, reason, .. } => {
                if ok {
                    if let Some(cid) = id {
                        self.last_checkpoint = Some(cid);
                    }
                } else if !reason.is_empty() {
                    self.transcript.push(format!("checkpoint : {reason}"));
                }
            }
            Ev::CheckpointList { checkpoints, .. } => {
                self.transcript.push("--- checkpoints ---".into());
                if checkpoints.is_empty() {
                    self.transcript.push("  none".into());
                }
                for (idx, cp) in checkpoints.iter().enumerate() {
                    let id = cp.get("id").and_then(|x| x.as_str()).unwrap_or("");
                    let turn = cp.get("turn").and_then(|x| x.as_str()).unwrap_or("");
                    let turns = cp.get("turns").and_then(|x| x.as_u64()).unwrap_or(0);
                    if idx == 0 && !id.is_empty() {
                        self.last_checkpoint = Some(id.to_string());
                    }
                    let short = if id.len() > 12 { &id[..12] } else { id };
                    self.transcript.push(format!("  {short} : turn={turn} ({turns} turns)"));
                }
            }
            Ev::RewindResult { backup, restored, removed, conversation, turns, .. } => {
                let short_bak = if backup.len() > 12 { &backup[..12] } else { &backup };
                self.transcript.push(format!(
                    "rewind : restored {} files, removed {} files (backup {short_bak})",
                    restored.len(),
                    removed.len()
                ));
                if conversation {
                    self.transcript.push(format!("rewind : conversation restored ({} turns)", turns.unwrap_or(0)));
                }
            }
            Ev::RulesResult { rules, .. } => {
                self.transcript.push("--- approval rules ---".into());
                if rules.is_empty() {
                    self.transcript.push("  none".into());
                }
                for r in rules {
                    let id = r.get("id").and_then(|x| x.as_str()).unwrap_or("");
                    let scope = r.get("scope").and_then(|x| x.as_str()).unwrap_or("thread");
                    let tool = r.get("tool").and_then(|x| x.as_str()).unwrap_or("");
                    let prefix = r.get("prefix").and_then(|x| x.as_str()).unwrap_or("*");
                    self.transcript.push(format!("  {id} ({scope}) : {tool} [{prefix}]"));
                }
            }
            Ev::Context { .. } => {}
            Ev::Retry { attempt, of, wait, reason, .. } => {
                self.status_label = format!("retry {attempt}/{of}");
                self.transcript.push(format!("retry : attempt {attempt}/{of} in {wait:.1}s ({reason})"));
            }
            Ev::AutoDecision { tool, allow, reason, .. } => {
                if !allow {
                    self.transcript.push(format!("auto skip : {tool} ({reason})"));
                }
            }
            Ev::SkillList { skills, .. } => {
                self.transcript.push("--- skills ---".into());
                if skills.is_empty() {
                    self.transcript.push("  none".into());
                }
                for sk in skills {
                    let id = sk.get("id").and_then(|x| x.as_str()).unwrap_or("");
                    let title = sk.get("title").and_then(|x| x.as_str()).unwrap_or("");
                    let active = if self.active_skills.iter().any(|x| x == id) { " [active]" } else { "" };
                    self.transcript.push(format!("  {id}{active} : {title}"));
                }
            }
            Ev::McpStatus { config, servers, .. } => {
                let cfg = config.unwrap_or_else(|| "none".into());
                self.transcript.push(format!("--- mcp servers ({cfg}) ---"));
                if servers.is_empty() {
                    self.transcript.push("  none configured".into());
                }
                for s in servers {
                    let name = s.get("name").and_then(|x| x.as_str()).unwrap_or("");
                    let enabled = s.get("enabled").and_then(|x| x.as_bool()).unwrap_or(false);
                    let ok = s.get("ok").and_then(|x| x.as_bool()).unwrap_or(false);
                    let tools_n = s.get("tools").and_then(|x| x.as_array()).map(|a| a.len()).unwrap_or(0);
                    let state = if !enabled { "disabled" } else if ok { "ok" } else { "error" };
                    self.transcript.push(format!("  {name} : {state} ({tools_n} tools)"));
                }
            }
            Ev::TaskList { tasks, .. } => {
                self.transcript.push("--- background tasks ---".into());
                if tasks.is_empty() {
                    self.transcript.push("  none".into());
                }
                for t in tasks {
                    let id = t.get("task").and_then(|x| x.as_str()).unwrap_or("");
                    let state = t.get("state").and_then(|x| x.as_str()).unwrap_or("");
                    let cmd = t.get("command").and_then(|x| x.as_str()).unwrap_or("");
                    self.transcript.push(format!("  {id} [{state}] : {cmd}"));
                }
            }
            Ev::TaskUpdate { task } => {
                let id = task.get("task").and_then(|x| x.as_str()).unwrap_or("");
                let state = task.get("state").and_then(|x| x.as_str()).unwrap_or("");
                if !id.is_empty() && !state.is_empty() {
                    self.transcript.push(format!("task {id} : {state}"));
                }
            }
            Ev::SessionList { sessions, .. } => {
                self.transcript.push("--- saved sessions ---".into());
                if sessions.is_empty() {
                    self.transcript.push("  none".into());
                }
                for s in sessions {
                    let thread = s.get("thread").and_then(|x| x.as_str()).unwrap_or("");
                    let model = s.get("model").and_then(|x| x.as_str()).unwrap_or("");
                    let title = s.get("title").and_then(|x| x.as_str()).unwrap_or("");
                    self.transcript.push(format!("  {thread} ({model}) : {title}"));
                }
            }
            Ev::HistoryResult { thread, turns, .. } => {
                self.transcript.push(format!("--- history ({thread}) ---"));
                for t in turns {
                    let role = t.get("role").and_then(|x| x.as_str()).unwrap_or("");
                    let content = t.get("content").and_then(|x| x.as_str()).unwrap_or("");
                    if role == "user" {
                        self.transcript.push(format!("you : {content}"));
                    } else if !content.is_empty() {
                        self.transcript.push(content.to_string());
                    }
                }
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
                    self.steer_after_cancel = false;
                }
                if self.busy_turn.is_none() && !self.queued.is_empty() {
                    let next = self.queued.remove(0);
                    self.transcript.push(format!("you : {next}"));
                    self.dispatch_turn(&next);
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
                if let Some(synth) = result.get("synthesis").and_then(|x| x.as_str()) {
                    self.transcript.push(format!("--- swarm synthesis ({id}) ---"));
                    for line in synth.lines() {
                        self.transcript.push(line.to_string());
                    }
                    return;
                }
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
            "mode {}  model {}  perm {}  ctx {}/{}  cost ${:.4}  agents {}",
            self.mode, self.model, self.permission, used, self.window, self.cost, running
        )
    }
}

fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || matches!(c, '_' | '-' | '/' | '.' | '@')
}

fn summarize_tool_args(args: &Value) -> String {
    let Some(obj) = args.as_object() else {
        return String::new();
    };
    for key in ["path", "file_path", "target_file", "command", "cmd", "query", "pattern", "url", "task"] {
        if let Some(val) = obj.get(key).and_then(|x| x.as_str()) {
            let clean = clip_label(val.trim());
            if !clean.is_empty() {
                return clean;
            }
        }
    }
    String::new()
}

fn clip_label(text: &str) -> String {
    text.chars().map(|c| if c == '\n' { ' ' } else { c }).take(80).collect()
}

fn history_file_path() -> Option<PathBuf> {
    for var in ["USERPROFILE", "HOME"] {
        if let Ok(home) = std::env::var(var) {
            if !home.trim().is_empty() {
                return Some(PathBuf::from(home).join(".hydra").join("tui_history.txt"));
            }
        }
    }
    None
}

fn load_history_file() -> Vec<String> {
    let Some(path) = history_file_path() else {
        return Vec::new();
    };
    let Ok(raw) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    let mut items: Vec<String> = raw
        .lines()
        .map(|l| l.replace("\\n", "\n"))
        .filter(|l| !l.trim().is_empty())
        .collect();
    if items.len() > 500 {
        let drain_n = items.len() - 500;
        items.drain(0..drain_n);
    }
    items
}

fn append_history_file(entry: &str) {
    let Some(path) = history_file_path() else {
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        use std::io::Write;
        let escaped = entry.replace('\n', "\\n");
        let _ = writeln!(f, "{escaped}");
    }
}

pub fn find_workspace_files(root: &str, query: &str, limit: usize) -> Vec<String> {
    let root_path = std::path::Path::new(root);
    if !root_path.is_dir() {
        return Vec::new();
    }
    let q = query.trim().replace('\\', "/").to_lowercase();
    let mut matches = Vec::new();
    let mut stack = vec![(root_path.to_path_buf(), String::new(), 0usize)];
    let mut visited = 0usize;

    while let Some((dir, rel_prefix, depth)) = stack.pop() {
        if depth > 4 || visited > 1500 {
            continue;
        }
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        let mut sorted_entries: Vec<_> = entries.flatten().collect();
        sorted_entries.sort_by_key(|e| e.file_name());
        for entry in sorted_entries {
            visited += 1;
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.')
                || matches!(
                    name.as_str(),
                    "target" | "node_modules" | "__pycache__" | "dist" | "build" | "human only" | "frozen"
                )
            {
                continue;
            }
            let rel = if rel_prefix.is_empty() {
                name.clone()
            } else {
                format!("{rel_prefix}/{name}")
            };
            let Ok(ft) = entry.file_type() else {
                continue;
            };
            if ft.is_dir() {
                stack.push((entry.path(), rel, depth + 1));
            } else if ft.is_file() {
                let rel_low = rel.to_lowercase();
                if q.is_empty() || rel_low.contains(&q) {
                    matches.push(rel);
                }
            }
        }
    }

    matches.sort_by(|a, b| {
        let a_low = a.to_lowercase();
        let b_low = b.to_lowercase();
        let a_starts = a_low.starts_with(&q);
        let b_starts = b_low.starts_with(&q);
        let a_lock = a_low.ends_with(".lock");
        let b_lock = b_low.ends_with(".lock");
        b_starts
            .cmp(&a_starts)
            .then_with(|| a_lock.cmp(&b_lock))
            .then_with(|| a.len().cmp(&b.len()))
            .then_with(|| a.cmp(b))
    });
    matches.truncate(limit);
    matches
}

pub fn read_clipboard_text() -> Option<String> {
    if cfg!(windows) {
        let mut cmd = Command::new("powershell");
        cmd.args(["-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"]);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000);
        }
        let out = cmd.output().ok()?;
        if out.status.success() {
            return Some(String::from_utf8_lossy(&out.stdout).trim_end_matches(['\r', '\n']).to_string());
        }
        None
    } else {
        for (prog, args) in [("wl-paste", &["--no-newline"][..]), ("xclip", &["-selection", "clipboard", "-o"][..]), ("pbpaste", &[][..])] {
            if let Ok(out) = Command::new(prog).args(args).output() {
                if out.status.success() {
                    return Some(String::from_utf8_lossy(&out.stdout).to_string());
                }
            }
        }
        None
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_model_is_mimo_flash_and_speed_dial_commands_work() {
        std::env::remove_var("HYDRA_DEFAULT_MODEL");
        std::env::remove_var("HYDRA_MODEL");
        std::env::remove_var("HYDRA_DIALECT");
        let mut app = App::new();
        assert_eq!(app.model, "bonsai 2 9b");
        assert_eq!(app.fallback_model, "glm 5.3 flash");
        assert_eq!(app.speed_dial_model, "mimo 2.6 pro");
        assert_eq!(app.dialect, "slack");

        for ch in "/progen".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.dialect, "progen");

        for ch in "/slack".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.dialect, "slack");

        for ch in "/fallback".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.model, "glm 5.3 flash");

        for ch in "/pro".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.model, "mimo 2.6 pro");

        for ch in "/flash".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.model, "mimo 2.6 flash");

        for ch in "/model mimo.2.6 pro".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.model, "mimo 2.6 pro");

        for ch in "/model mimo.2.6 flash".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.model, "mimo 2.6 flash");

        for ch in "/model bonsai 9b".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.model, "bonsai 2 9b");
    }

    #[test]
    fn in_flight_queueing_steering_and_auto_drain_on_done() {
        let mut app = App::new();
        for ch in "first prompt".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.busy_turn.as_deref(), Some("t1"));

        for ch in "second queued prompt".chars() {
            app.on_char(ch);
        }
        app.submit();
        assert_eq!(app.queued, vec!["second queued prompt".to_string()]);
        assert!(app.transcript.iter().any(|l| l.starts_with("queued (#1) : second queued prompt")));

        // Pressing Enter on empty input while busy with queued item steers immediately
        app.submit();
        assert!(app.steer_after_cancel);
        assert!(app.transcript.iter().any(|l| l.contains("steering : interrupting current turn")));

        // When Done arrives for t1, queued prompt is popped and dispatched as t2
        app.apply(Ev::Done {
            turn: "t1".into(),
            text: "stopped".into(),
            ok: false,
            cancelled: true,
        });
        assert!(app.queued.is_empty());
        assert_eq!(app.busy_turn.as_deref(), Some("t2"));
        assert!(app.transcript.iter().any(|l| l == "you : second queued prompt"));
    }

    #[test]
    fn permission_mode_commands_and_approval_shortcuts() {
        let mut app = App::new();
        assert_eq!(app.permission, "accept_edits");

        for (cmd, expected) in [("/plan", "plan"), ("/ask", "manual"), ("/bypass", "bypass"), ("/accept", "accept_edits")] {
            app.input = cmd.into();
            app.cursor_pos = app.input.len();
            app.submit();
            assert_eq!(app.permission, expected);
        }

        app.apply(Ev::Approval {
            turn: "t1".into(),
            id: "a1".into(),
            tool: "run_command".into(),
            args: json!({"command": "cargo test"}),
        });
        assert!(app.approval.is_some());
        // Empty Enter approves pending approval
        app.submit();
        assert!(app.approval.is_none());
        assert!(app.transcript.iter().any(|l| l == "approval a1 : allow"));
    }

    #[test]
    fn multiline_editing_word_deletion_history_and_diff_rendering() {
        let mut app = App::new();
        app.paste_text("alpha beta gamma");
        app.delete_word_back();
        assert_eq!(app.input, "alpha beta ");

        app.insert_newline();
        app.paste_text("second line");
        assert_eq!(app.input, "alpha beta \nsecond line");

        app.cursor_home();
        assert_eq!(app.cursor_pos, "alpha beta \n".len());
        app.delete_to_line_end();
        assert_eq!(app.input, "alpha beta \n");

        app.input = "cmd one".into();
        app.cursor_pos = app.input.len();
        app.submit();
        // Finish t1 so next submit isn't queued
        app.busy_turn = None;

        app.input = "cmd two".into();
        app.cursor_pos = app.input.len();
        app.submit();
        app.busy_turn = None;

        app.input = "draft".into();
        app.cursor_pos = app.input.len();
        app.history_up();
        assert_eq!(app.input, "cmd two");
        app.history_up();
        assert_eq!(app.input, "cmd one");
        app.history_down();
        assert_eq!(app.input, "cmd two");
        app.history_down();
        assert_eq!(app.input, "draft");

        // Test @file completion and /diff command
        app.input = "inspect @Cargo".into();
        app.cursor_pos = app.input.len();
        let cands = app.candidates();
        assert!(cands.iter().any(|(c, _)| c.starts_with("@Cargo.toml")));
        app.complete();
        assert_eq!(app.input, "inspect @Cargo.toml ");

        app.apply(Ev::Diff {
            turn: "t1".into(),
            path: "src/app.rs".into(),
            diff: "@@ -1,1 +1,1 @@\n-old\n+new".into(),
        });
        assert_eq!(app.diffs.len(), 1);
        app.input = "/diff src/app.rs".into();
        app.cursor_pos = app.input.len();
        app.submit();
        assert!(app.transcript.iter().any(|l| l == "  +new"));
    }
}
