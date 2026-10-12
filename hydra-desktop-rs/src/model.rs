//! UI-independent state. Backend events and action replies are folded in here so the logic is testable without a window.

use crate::backend::{Backend, Ev};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;

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
pub const PROFILES: &[&str] = &["full_automation", "browser_only", "workspace_only", "minimal"];

#[derive(Clone, Serialize, Deserialize, Debug, Default)]
pub struct Msg {
    pub user: bool,
    pub header: String,
    pub text: String,
    #[serde(default)]
    pub actions: Vec<String>,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Session {
    pub id: u32,
    pub thread: String,
    pub name: String,
    pub messages: Vec<Msg>,
}

#[derive(Clone, Debug)]
pub struct DiffItem {
    pub path: String,
    pub diff: String,
}

#[derive(Clone, Debug)]
pub struct Approval {
    pub id: String,
    pub tool: String,
    pub args: Value,
}

#[derive(Clone, Debug)]
pub enum Pending {
    Swarm(u32),
    Terminal(u32),
    Screen,
    Click,
    Type,
    Window,
    AgentStart,
    AgentStatus,
    AgentCtl(String),
    Export(&'static str),
    Templates,
    JobsList,
    JobCreate,
    JobOp,
}

#[derive(Clone, Serialize, Deserialize, Debug, Default)]
pub struct AgentRow {
    pub id: String,
    pub name: String,
    pub status: String,
    pub task: String,
    pub step: u32,
    pub max_steps: u32,
    pub elapsed_sec: f64,
}

#[derive(Clone, Serialize, Deserialize, Debug, Default)]
pub struct MemoryEntry {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub seq: u64,
    #[serde(default)]
    pub prev: String,
    #[serde(default)]
    pub hash: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub subject: String,
    #[serde(default)]
    pub actor: String,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub ts: String,
    #[serde(default)]
    pub repo: Option<String>,
}

pub fn default_context_window() -> u64 {
    1_048_576
}

pub fn default_local_model() -> String {
    "qwen2.5-coder:14b".into()
}

pub fn default_ollama_host() -> String {
    "http://127.0.0.1:11434".into()
}

pub fn default_llamacpp_host() -> String {
    "http://127.0.0.1:8080".into()
}

pub fn default_local_ai_base() -> String {
    "http://localhost:8080/v1".into()
}

#[derive(Serialize, Deserialize, Default)]
struct Saved {
    sessions: Vec<Session>,
    active: usize,
    counter: u32,
    nav: String,
    model: usize,
    tokens: u64,
    cost: f64,
    #[serde(default)]
    prompt_tokens: u64,
    #[serde(default)]
    completion_tokens: u64,
    #[serde(default = "default_context_window")]
    context_window: u64,
    #[serde(default = "default_local_model")]
    local_model_alias: String,
    #[serde(default = "default_ollama_host")]
    ollama_host: String,
    #[serde(default = "default_llamacpp_host")]
    llamacpp_host: String,
    #[serde(default = "default_local_ai_base")]
    local_ai_base: String,
    #[serde(default)]
    pub cwd: Option<String>,
}

pub struct Model {
    pub sessions: Vec<Session>,
    pub active: usize,
    pub counter: u32,
    pub nav: &'static str,
    pub drawer_tab: &'static str,
    pub model: usize,
    pub tokens: u64,
    pub cost: f64,
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub context_window: u64,
    pub local_model_alias: String,
    pub ollama_host: String,
    pub llamacpp_host: String,
    pub local_ai_base: String,
    pub agents: Vec<AgentRow>,
    pub term: String,
    pub diffs: Vec<DiffItem>,
    pub busy: Option<(String, u32)>, // turn id, session id
    pub approval: Option<Approval>,
    pub approve_tools: bool,
    pub agent_ready: bool,
    pub notice: String,
    pub turn_counter: u64,
    pub cwd: String,
    pub pending: HashMap<String, Pending>,
    pub templates: Vec<Value>,
    pub jobs: Vec<Value>,
    pub screen_b64: String,
    pub screen_seq: u64,
    pub window_title: String,
    pub agent_status: Value,
    pub agent_running: bool,
    pub state_path: Option<PathBuf>,
    pub export_dir: Option<PathBuf>,
}

pub fn state_dir() -> PathBuf {
    if let Ok(d) = std::env::var("HYDRA_STATE_DIR") {
        return PathBuf::from(d);
    }
    let home = std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")).unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".hydra").join("desktop")
}

fn greeting() -> Msg {
    Msg { user: false, header: "HYDRA ORCHESTRATOR".into(), text: "Ask anything, pick a model, and send.".into(), actions: vec![] }
}

fn nav_key(s: &str) -> &'static str {
    NAV.iter().find(|n| n.0 == s).map(|n| n.0).unwrap_or("chat")
}

fn short(v: &Value) -> String {
    let s = match v {
        Value::Object(m) => m.iter().map(|(k, v)| format!("{k}={}", v.as_str().map(String::from).unwrap_or_else(|| v.to_string()))).collect::<Vec<_>>().join(" "),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    if s.chars().count() > 90 {
        format!("{}…", s.chars().take(90).collect::<String>())
    } else {
        s
    }
}

impl Model {
    pub fn new(state_path: Option<PathBuf>, cwd: String) -> Model {
        let local_model_alias = std::env::var("HYDRA_LOCAL_MODEL").unwrap_or_else(|_| default_local_model());
        let ollama_host = std::env::var("OLLAMA_HOST").unwrap_or_else(|_| default_ollama_host());
        let llamacpp_host = std::env::var("LLAMACPP_HOST").unwrap_or_else(|_| default_llamacpp_host());
        let local_ai_base = std::env::var("LOCAL_AI_BASE").unwrap_or_else(|_| default_local_ai_base());
        let context_window = std::env::var("HYDRA_CONTEXT_WINDOW")
            .ok()
            .and_then(|w| w.parse().ok())
            .unwrap_or_else(default_context_window);

        let mut m = Model {
            sessions: vec![],
            active: 0,
            counter: 0,
            nav: "chat",
            drawer_tab: "screen",
            model: 0,
            tokens: 0,
            cost: 0.0,
            prompt_tokens: 0,
            completion_tokens: 0,
            context_window,
            local_model_alias,
            ollama_host,
            llamacpp_host,
            local_ai_base,
            agents: vec![],
            term: "> Hydra Terminal Sandbox ready.".into(),
            diffs: vec![],
            busy: None,
            approval: None,
            approve_tools: false,
            agent_ready: false,
            notice: String::new(),
            turn_counter: 0,
            cwd,
            pending: HashMap::new(),
            templates: vec![],
            jobs: vec![],
            screen_b64: String::new(),
            screen_seq: 0,
            window_title: "Detecting...".into(),
            agent_status: Value::Null,
            agent_running: false,
            export_dir: state_path.as_ref().and_then(|p| p.parent().map(|d| d.join("exports"))),
            state_path,
        };
        if let Some(p) = &m.state_path {
            if let Some(saved) = std::fs::read_to_string(p).ok().and_then(|t| serde_json::from_str::<Saved>(&t).ok()) {
                if !saved.sessions.is_empty() {
                    m.active = saved.active.min(saved.sessions.len() - 1);
                    m.sessions = saved.sessions;
                    m.counter = saved.counter;
                    m.nav = nav_key(&saved.nav);
                    m.model = saved.model.min(MODELS.len() - 1);
                    m.tokens = saved.tokens;
                    m.cost = saved.cost;
                    m.prompt_tokens = saved.prompt_tokens;
                    m.completion_tokens = saved.completion_tokens;
                    if saved.context_window > 0 {
                        m.context_window = saved.context_window;
                    }
                    if !saved.local_model_alias.is_empty() {
                        m.local_model_alias = saved.local_model_alias;
                    }
                    if !saved.ollama_host.is_empty() {
                        m.ollama_host = saved.ollama_host;
                    }
                    if !saved.llamacpp_host.is_empty() {
                        m.llamacpp_host = saved.llamacpp_host;
                    }
                    if !saved.local_ai_base.is_empty() {
                        m.local_ai_base = saved.local_ai_base;
                    }
                    if let Some(c) = saved.cwd {
                        if !c.trim().is_empty() {
                            m.cwd = c;
                        }
                    }
                }
            }
        }
        if m.sessions.is_empty() {
            m.new_session();
        }
        m
    }

    pub fn save(&self) {
        let Some(p) = &self.state_path else { return };
        let saved = Saved {
            sessions: self.sessions.clone(),
            active: self.active,
            counter: self.counter,
            nav: self.nav.into(),
            model: self.model,
            tokens: self.tokens,
            cost: self.cost,
            prompt_tokens: self.prompt_tokens,
            completion_tokens: self.completion_tokens,
            context_window: self.context_window,
            local_model_alias: self.local_model_alias.clone(),
            ollama_host: self.ollama_host.clone(),
            llamacpp_host: self.llamacpp_host.clone(),
            local_ai_base: self.local_ai_base.clone(),
            cwd: Some(self.cwd.clone()),
        };
        if let Some(dir) = p.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(t) = serde_json::to_string_pretty(&saved) {
            let tmp = p.with_extension("tmp");
            if std::fs::write(&tmp, t).is_ok() {
                let _ = std::fs::rename(&tmp, p);
            }
        }
    }

    pub fn telemetry(&self) -> (u64, u64, u64, f64) {
        let win = if self.context_window > 0 { self.context_window } else { default_context_window() };
        let total = self.prompt_tokens + self.completion_tokens;
        let pct = (total as f64 / win as f64) * 100.0;
        (self.prompt_tokens, self.completion_tokens, win, pct)
    }

    pub fn apply_options(&mut self, alias: &str, ollama: &str, llamacpp: &str, local_ai: &str, ctx_win: u64, cwd: &str) {
        self.local_model_alias = alias.trim().to_string();
        self.ollama_host = ollama.trim().to_string();
        self.llamacpp_host = llamacpp.trim().to_string();
        self.local_ai_base = local_ai.trim().to_string();
        self.context_window = if ctx_win > 0 { ctx_win } else { default_context_window() };
        let clean_cwd = cwd.trim();
        if !clean_cwd.is_empty() {
            self.cwd = clean_cwd.to_string();
        }
        std::env::set_var("HYDRA_LOCAL_MODEL", &self.local_model_alias);
        std::env::set_var("OLLAMA_HOST", &self.ollama_host);
        std::env::set_var("LLAMACPP_HOST", &self.llamacpp_host);
        std::env::set_var("LOCAL_AI_BASE", &self.local_ai_base);
        std::env::set_var("HYDRA_CONTEXT_WINDOW", self.context_window.to_string());
        std::env::set_var("HYDRA_CWD", &self.cwd);
        self.save();
    }

    pub fn new_session(&mut self) {
        self.counter += 1;
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let first = self.sessions.is_empty();
        let text = if first { greeting().text } else { format!("Session {} primed. Dispatch instruction or summon heads.", self.counter) };
        self.sessions.push(Session {
            id: self.counter,
            thread: format!("desk-{stamp}-{}", self.counter),
            name: format!("Session {}", self.counter),
            messages: vec![Msg { user: false, header: "HYDRA ORCHESTRATOR".into(), text, actions: vec![] }],
        });
        self.active = self.sessions.len() - 1;
        self.save();
    }

    pub fn close_session(&mut self, i: usize) {
        if self.sessions.len() > 1 && i < self.sessions.len() {
            self.sessions.remove(i);
            self.active = self.active.min(self.sessions.len() - 1);
            self.save();
        }
    }

    pub fn clear_active(&mut self) {
        self.sessions[self.active].messages.clear();
        self.save();
    }

    pub fn set_nav(&mut self, key: &str) {
        self.nav = nav_key(key);
        self.term.push_str(&format!("\n[MODE] Switched to mode: {}", self.nav));
        match self.nav {
            "agent" => self.drawer_tab = "agent",
            "computer" => self.drawer_tab = "screen",
            "terminal" => self.drawer_tab = "term",
            _ => {}
        }
    }

    fn session_mut(&mut self, id: u32) -> Option<&mut Session> {
        self.sessions.iter_mut().find(|s| s.id == id)
    }

    fn last_assistant(&mut self, sid: u32) -> Option<&mut Msg> {
        self.session_mut(sid).and_then(|s| s.messages.last_mut()).filter(|m| !m.user)
    }

    fn act(&mut self, kind: Pending, id: String) {
        self.pending.insert(id, kind);
    }

    /// Send the composer text according to the nav mode. Returns false if it was not accepted.
    pub fn submit(&mut self, be: &mut Backend, text: &str) -> bool {
        let text = text.trim();
        if text.is_empty() || self.busy.is_some() {
            return false;
        }
        let (sid, thread) = {
            let s = &self.sessions[self.active];
            (s.id, s.thread.clone())
        };
        let model = MODELS[self.model].0;
        let header = MODELS[self.model].1.to_uppercase();
        {
            let s = &mut self.sessions[self.active];
            s.messages.push(Msg { user: true, header: "YOU".into(), text: text.into(), actions: vec![] });
            s.messages.push(Msg { user: false, header, text: String::new(), actions: vec!["◐ Thinking".into()] });
        }
        self.turn_counter += 1;
        let turn = format!("t{}", self.turn_counter);
        match self.nav {
            "terminal" => {
                self.term.push_str(&format!("\n$ {text}"));
                let id = be.action("terminal:run", json!({"command": text, "cwd": self.cwd}));
                self.act(Pending::Terminal(sid), id);
                self.busy = Some((turn, sid));
            }
            "swarm" => {
                let id = be.action("swarm:run", json!({"task": text, "model": model}));
                self.act(Pending::Swarm(sid), id);
                self.busy = Some((turn, sid));
            }
            _ => {
                be.turn(&turn, text, &thread, &self.cwd, model, self.approve_tools);
                self.busy = Some((turn, sid));
            }
        }
        self.save();
        true
    }

    /// Stop the running turn. Turns go through the adapter cancel; actions are not interruptible.
    pub fn stop(&mut self, be: &mut Backend) {
        if let Some((turn, _)) = &self.busy {
            be.cancel(turn);
        }
    }

    pub fn answer_approval(&mut self, be: &mut Backend, decision: &str) {
        if let Some(a) = self.approval.take() {
            be.approve(&a.id, decision);
        }
    }

    pub fn agent_start(&mut self, be: &mut Backend, task: &str, max_steps: u32, profile: &str) {
        let id = be.action("agent:run", json!({"task": task, "max_steps": max_steps, "profile": profile}));
        self.act(Pending::AgentStart, id);
        self.drawer_tab = "agent";
        self.agents.retain(|a| a.id != "react-primary");
        self.agents.insert(
            0,
            AgentRow {
                id: "react-primary".into(),
                name: "Primary ReAct Runner".into(),
                status: "RUNNING".into(),
                task: task.to_string(),
                step: 0,
                max_steps,
                elapsed_sec: 0.0,
            },
        );
    }

    pub fn agent_ctl(&mut self, be: &mut Backend, which: &str) {
        let id = be.action(&format!("agent:{which}"), json!({}));
        self.act(Pending::AgentCtl(which.into()), id);
    }

    pub fn poll_agent(&mut self, be: &mut Backend) {
        let id = be.action("agent:status", json!({}));
        self.act(Pending::AgentStatus, id);
    }

    pub fn export(&mut self, be: &mut Backend, markdown: bool) {
        let name = if markdown { "agent:export_markdown" } else { "agent:export_trace" };
        let id = be.action(name, json!({}));
        self.act(Pending::Export(if markdown { "md" } else { "json" }), id);
    }

    pub fn load_templates(&mut self, be: &mut Backend) {
        let id = be.action("templates:list", json!({}));
        self.act(Pending::Templates, id);
    }

    pub fn load_jobs(&mut self, be: &mut Backend) {
        let id = be.action("jobs:list", json!({}));
        self.act(Pending::JobsList, id);
    }

    pub fn job_create(&mut self, be: &mut Backend, name: &str, interval: f64, profile: &str, task: &str) {
        let id = be.action("jobs:create", json!({"name": name, "interval_sec": interval, "profile": profile, "task": task}));
        self.act(Pending::JobCreate, id);
    }

    pub fn job_op(&mut self, be: &mut Backend, op: &str, job_id: &str) {
        let id = be.action(&format!("jobs:{op}"), json!({"job_id": job_id}));
        self.act(Pending::JobOp, id);
    }

    pub fn capture(&mut self, be: &mut Backend) {
        let id = be.action("computer:screen", json!({}));
        self.act(Pending::Screen, id);
        let id = be.action("computer:window", json!({}));
        self.act(Pending::Window, id);
    }

    pub fn click(&mut self, be: &mut Backend, x: i64, y: i64) {
        self.term.push_str(&format!("\n[CLICK] mouse_click x={x} y={y}"));
        let id = be.action("computer:click", json!({"x": x, "y": y}));
        self.act(Pending::Click, id);
    }

    pub fn type_text(&mut self, be: &mut Backend, text: &str) {
        self.term.push_str(&format!("\n[TYPE] {text}"));
        let id = be.action("computer:type", json!({"text": text}));
        self.act(Pending::Type, id);
    }

    fn finish_busy(&mut self) {
        self.busy = None;
        self.save();
    }

    fn fail_bubble(&mut self, sid: u32, msg: &str) {
        if let Some(m) = self.last_assistant(sid) {
            m.actions.retain(|a| a != "◐ Thinking");
            if m.text.is_empty() {
                m.text = format!("Error: {msg}");
            } else {
                m.actions.push(format!("✗ {msg}"));
            }
        }
    }

    pub fn apply(&mut self, ev: Ev) {
        match ev {
            Ev::Hello => self.agent_ready = true,
            Ev::Exited(why) => {
                self.agent_ready = false;
                self.notice = why;
                if let Some((_, sid)) = self.busy.clone() {
                    self.fail_bubble(sid, "agent process ended");
                    self.finish_busy();
                }
            }
            Ev::Token { turn, text } => {
                if let Some((t, sid)) = self.busy.clone() {
                    if t == turn {
                        if let Some(m) = self.last_assistant(sid) {
                            m.actions.retain(|a| a != "◐ Thinking");
                            m.text.push_str(&text);
                        }
                    }
                }
            }
            Ev::ToolStart { turn, tool, args, .. } => {
                if let Some((t, sid)) = self.busy.clone() {
                    if t == turn {
                        if let Some(m) = self.last_assistant(sid) {
                            m.actions.retain(|a| a != "◐ Thinking");
                            m.actions.push(format!("▸ {tool}  {}", short(&args)));
                        }
                    }
                }
                if tool == "invoke_subagent" {
                    let sub_task = args.get("prompt").or_else(|| args.get("task")).and_then(|v| v.as_str()).unwrap_or("Child task").to_string();
                    let sub_id = format!("sub-{}", self.turn_counter);
                    if !self.agents.iter().any(|a| a.id == sub_id) {
                        self.agents.push(AgentRow {
                            id: sub_id,
                            name: "Child Subagent".into(),
                            status: "RUNNING".into(),
                            task: sub_task,
                            step: 1,
                            max_steps: 10,
                            elapsed_sec: 0.0,
                        });
                    }
                }
                self.term.push_str(&format!("\n$ {tool} {}", short(&args)));
            }
            Ev::ToolResult { turn, tool, is_error, output } => {
                if let Some((t, sid)) = self.busy.clone() {
                    if t == turn {
                        let first = output.lines().next().unwrap_or("").chars().take(100).collect::<String>();
                        if let Some(m) = self.last_assistant(sid) {
                            m.actions.push(format!("{} {tool}: {first}", if is_error { "✗" } else { "✓" }));
                        }
                    }
                }
                if tool == "invoke_subagent" {
                    let sub_id = format!("sub-{}", self.turn_counter);
                    if let Some(sub) = self.agents.iter_mut().find(|a| a.id == sub_id) {
                        sub.status = if is_error { "FAILED".into() } else { "COMPLETED".into() };
                    }
                }
                self.term.push_str(&format!("\n{}{output}", if is_error { "[error] " } else { "" }));
            }
            Ev::Diff { path, diff, .. } => {
                self.diffs.push(DiffItem { path, diff });
                self.drawer_tab = "diff";
            }
            Ev::Usage { prompt, completion, cost, .. } => {
                self.prompt_tokens += prompt;
                self.completion_tokens += completion;
                self.tokens += prompt + completion;
                self.cost += cost;
                self.save();
            }
            Ev::Approval { id, tool, args, .. } => self.approval = Some(Approval { id, tool, args }),
            Ev::Done { turn, text, ok, cancelled } => {
                if let Some((t, sid)) = self.busy.clone() {
                    if t == turn {
                        if let Some(m) = self.last_assistant(sid) {
                            m.actions.retain(|a| a != "◐ Thinking");
                            if cancelled {
                                m.actions.push("■ turn cancelled".into());
                            } else if !text.is_empty() {
                                m.text = text.clone();
                            } else if !ok && m.text.is_empty() {
                                m.text = "Turn failed.".into();
                            }
                        }
                        self.approval = None;
                        self.finish_busy();
                    }
                }
            }
            Ev::Error { code, message, turn, id } => {
                if let Some(id) = &id {
                    if let Some(kind) = self.pending.remove(id) {
                        self.action_failed(kind, &message);
                        return;
                    }
                }
                let line = format!("\n[{code}] {message}");
                self.term.push_str(&line);
                if let Some((t, sid)) = self.busy.clone() {
                    if turn.as_deref() == Some(t.as_str()) {
                        self.fail_bubble(sid, &message);
                    }
                }
                if code == "bad_request" || code == "busy" {
                    if let Some((_, sid)) = self.busy.clone() {
                        if turn.is_some() {
                            self.fail_bubble(sid, &message);
                            self.finish_busy();
                        }
                    }
                }
            }
            Ev::ActionResult { id, result, .. } => {
                if let Some(kind) = self.pending.remove(&id) {
                    self.action_done(kind, result);
                }
            }
        }
    }

    fn action_failed(&mut self, kind: Pending, message: &str) {
        match kind {
            Pending::Swarm(sid) | Pending::Terminal(sid) => {
                self.fail_bubble(sid, message);
                self.finish_busy();
            }
            _ => self.term.push_str(&format!("\n[error] {message}")),
        }
    }

    fn action_done(&mut self, kind: Pending, r: Value) {
        let is_err = r.get("isError").and_then(|x| x.as_bool()).unwrap_or(false);
        let err_text = r.get("error").and_then(|x| x.as_str()).unwrap_or("action failed").to_string();
        match kind {
            Pending::Terminal(sid) => {
                if is_err {
                    return self.action_failed(Pending::Terminal(sid), &err_text);
                }
                let res = &r["result"];
                let out = res["stdout"].as_str().unwrap_or("");
                let err = res["stderr"].as_str().unwrap_or("");
                let status = res["status"].as_str().unwrap_or("");
                let code = res["exit_code"].as_i64().unwrap_or(0);
                self.term.push_str(&format!("\n{out}{err}"));
                let mut body = format!("{out}{err}").trim_end().to_string();
                if let Some(v) = res["violation"].as_str().filter(|v| !v.is_empty()) {
                    body = format!("Blocked: {v}");
                    self.term.push_str(&format!("\n[BLOCKED] {v}"));
                }
                if let Some(d) = res["diff"].as_str().filter(|d| !d.is_empty()) {
                    self.diffs.push(DiffItem { path: "sandbox".into(), diff: d.into() });
                }
                if let Some(m) = self.last_assistant(sid) {
                    m.actions.retain(|a| a != "◐ Thinking");
                    m.text = if body.is_empty() { format!("(no output) exit {code}") } else { body };
                    m.actions.push(format!("{status} exit {code}"));
                }
                self.finish_busy();
            }
            Pending::Swarm(sid) => {
                if is_err {
                    return self.action_failed(Pending::Swarm(sid), &err_text);
                }
                let heads = r["heads"].as_array().cloned().unwrap_or_default();
                if let Some(s) = self.session_mut(sid) {
                    // the placeholder bubble becomes the synthesis, heads go before it
                    let placeholder = s.messages.pop();
                    for h in &heads {
                        let status = h["status"].as_str().unwrap_or("ok");
                        let body = if status == "failed" { format!("Error: {}", h["error"].as_str().unwrap_or("failed")) } else { h["content"].as_str().unwrap_or("").to_string() };
                        s.messages.push(Msg {
                            user: false,
                            header: format!("{} · {}", h["title"].as_str().unwrap_or("HEAD"), h["model"].as_str().unwrap_or("")).to_uppercase(),
                            text: body,
                            actions: vec![format!("{}s", h["duration_sec"].as_f64().unwrap_or(0.0))],
                        });
                    }
                    let mut synth = placeholder.unwrap_or_default();
                    synth.actions.retain(|a| a != "◐ Thinking");
                    synth.header = "SWARM SYNTHESIS".into();
                    synth.text = r["synthesis"].as_str().filter(|t| !t.is_empty()).map(String::from).unwrap_or_else(|| {
                        format!("Error: {}", r["synthesis_error"].as_str().unwrap_or("no synthesis returned"))
                    });
                    s.messages.push(synth);
                }
                self.finish_busy();
            }
            Pending::Screen => {
                if is_err {
                    self.term.push_str(&format!("\n[error] capture: {err_text}"));
                } else if let Some(b) = r["png_base64"].as_str().filter(|b| !b.is_empty()) {
                    self.screen_b64 = b.into();
                    self.screen_seq += 1;
                    if r["simulated"].as_bool().unwrap_or(false) {
                        self.term.push_str("\n[note] screen capture is simulated on this platform");
                    }
                }
            }
            Pending::Window => {
                if let Some(t) = r["window"]["title"].as_str() {
                    self.window_title = t.into();
                }
            }
            Pending::Click | Pending::Type => {
                if is_err {
                    self.term.push_str(&format!("\n[error] {err_text}"));
                }
            }
            Pending::AgentStart => {
                if is_err {
                    self.term.push_str(&format!("\n[error] agent: {err_text}"));
                } else {
                    self.agent_running = true;
                    self.term.push_str("\n[AGENT] task started");
                }
            }
            Pending::AgentStatus => {
                let st = r["status"].clone();
                let state = st["status"].as_str().unwrap_or("").to_uppercase();
                self.agent_running = state == "RUNNING" || state == "PAUSED";
                self.agent_status = st.clone();

                let mut lineup = Vec::new();
                if !state.is_empty() {
                    let task = st["current_task"].as_str().unwrap_or("").to_string();
                    let step = st["current_step"].as_u64().unwrap_or(0) as u32;
                    let max_steps = st["max_steps"].as_u64().unwrap_or(30) as u32;
                    let elapsed = st["elapsed_sec"].as_f64().unwrap_or(0.0);
                    lineup.push(AgentRow {
                        id: "react-primary".into(),
                        name: "Primary ReAct Runner".into(),
                        status: state,
                        task: if task.is_empty() { "Idle".into() } else { task },
                        step,
                        max_steps,
                        elapsed_sec: elapsed,
                    });
                }
                if let Some(list) = st["subagents"].as_array().or_else(|| r["agents"].as_array()) {
                    for a in list {
                        lineup.push(AgentRow {
                            id: a["id"].as_str().unwrap_or("subagent").to_string(),
                            name: a["name"].as_str().unwrap_or("Subagent").to_string(),
                            status: a["status"].as_str().unwrap_or("RUNNING").to_uppercase(),
                            task: a["task"].as_str().unwrap_or("").to_string(),
                            step: a["step"].as_u64().unwrap_or(0) as u32,
                            max_steps: a["max_steps"].as_u64().unwrap_or(10) as u32,
                            elapsed_sec: a["elapsed_sec"].as_f64().unwrap_or(0.0),
                        });
                    }
                }
                if lineup.is_empty() && self.agent_running {
                    lineup.push(AgentRow {
                        id: "react-primary".into(),
                        name: "Primary ReAct Runner".into(),
                        status: "RUNNING".into(),
                        task: "Active execution".into(),
                        step: 1,
                        max_steps: 30,
                        elapsed_sec: 0.0,
                    });
                }
                for existing in &self.agents {
                    if existing.id.starts_with("sub-") && !lineup.iter().any(|a| a.id == existing.id) {
                        lineup.push(existing.clone());
                    }
                }
                self.agents = lineup;
            }
            Pending::AgentCtl(which) => self.term.push_str(&format!("\n[AGENT] {which}: {}", if is_err { err_text } else { "ok".into() })),
            Pending::Export(ext) => {
                let body = if ext == "md" { r["markdown"].as_str().unwrap_or("").to_string() } else { serde_json::to_string_pretty(&r["result"]).unwrap_or_default() };
                let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
                let path = self.export_dir.clone().map(|d| d.join(format!("agent-trace-{stamp}.{ext}")));
                match path {
                    Some(p) => {
                        if let Some(d) = p.parent() {
                            let _ = std::fs::create_dir_all(d);
                        }
                        match std::fs::write(&p, body) {
                            Ok(_) => self.term.push_str(&format!("\n[EXPORT] wrote {}", p.display())),
                            Err(e) => self.term.push_str(&format!("\n[EXPORT] failed: {e}")),
                        }
                    }
                    None => self.term.push_str("\n[EXPORT] no export directory"),
                }
            }
            Pending::Templates => self.templates = r["templates"].as_array().cloned().unwrap_or_default(),
            Pending::JobsList => self.jobs = r["jobs"].as_array().cloned().unwrap_or_default(),
            Pending::JobCreate | Pending::JobOp => {
                if is_err {
                    self.term.push_str(&format!("\n[error] jobs: {err_text}"));
                }
                self.notice = "jobs-changed".into();
            }
        }
    }

    pub fn tally(&self) -> (String, String) {
        let t = self.tokens;
        let tokens = if t >= 1000 { format!("{},{:03}", t / 1000, t % 1000) } else { t.to_string() };
        (tokens, format!("${:.4}", self.cost))
    }
}

pub fn memory_dir() -> PathBuf {
    if let Ok(d) = std::env::var("HYDRA_HOME") {
        return PathBuf::from(d).join("memory");
    }
    let home = std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")).unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".hydra").join("memory")
}

pub fn memory_ledger_path() -> PathBuf {
    memory_dir().join("ledger.jsonl")
}

pub fn read_memory_ledger(path: &std::path::Path) -> Vec<MemoryEntry> {
    let Ok(content) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    content
        .lines()
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| serde_json::from_str::<MemoryEntry>(line).ok())
        .collect()
}

pub fn append_memory_fact(path: &std::path::Path, subject: &str, body: &str, actor: &str) -> Result<MemoryEntry, String> {
    let entries = read_memory_ledger(path);
    let seq = (entries.len() as u64) + 1;
    let prev = entries.last().map(|e| e.hash.clone()).unwrap_or_else(|| "0".repeat(64));
    let id = format!("L{seq}");
    let ts = current_iso_timestamp();

    let mut map = serde_json::Map::new();
    map.insert("actor".into(), json!(actor));
    map.insert("body".into(), json!(body));
    map.insert("id".into(), json!(id));
    map.insert("kind".into(), json!("fact"));
    map.insert("prev".into(), json!(prev));
    map.insert("seq".into(), json!(seq));
    map.insert("subject".into(), json!(subject));
    map.insert("ts".into(), json!(ts));

    let canonical = serde_json::to_string(&Value::Object(map.clone())).map_err(|e| e.to_string())?;
    let hash = sha256_hex(canonical.as_bytes());
    map.insert("hash".into(), json!(hash));

    let entry_val = Value::Object(map);
    let line = serde_json::to_string(&entry_val).map_err(|e| e.to_string())?;

    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(path).map_err(|e| e.to_string())?;
    writeln!(f, "{line}").map_err(|e| e.to_string())?;

    let entry: MemoryEntry = serde_json::from_value(entry_val).map_err(|e| e.to_string())?;
    Ok(entry)
}

fn days_to_ymd(days: u64) -> (u32, u32, u32) {
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as u32, m as u32, d as u32)
}

pub fn current_iso_timestamp() -> String {
    let now = std::time::SystemTime::now();
    let dur = now.duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let secs = dur.as_secs();
    let s = secs % 60;
    let m = (secs / 60) % 60;
    let h = (secs / 3600) % 24;
    let days = secs / 86400;
    let (year, month, day) = days_to_ymd(days);
    format!("{year:04}-{month:02}-{day:02}T{h:02}:{m:02}:{s:02}Z")
}

pub fn sha256_hex(data: &[u8]) -> String {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    let k: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let bit_len = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while (msg.len() % 64) != 56 {
        msg.push(0x00);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());

    for chunk in msg.chunks_exact(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([chunk[4 * i], chunk[4 * i + 1], chunk[4 * i + 2], chunk[4 * i + 3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let mut a = h[0];
        let mut b = h[1];
        let mut c = h[2];
        let mut d = h[3];
        let mut e = h[4];
        let mut f = h[5];
        let mut g = h[6];
        let mut hh = h[7];
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(k[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    format!("{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}{:08x}", h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sha256_known_vectors() {
        assert_eq!(sha256_hex(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        assert_eq!(sha256_hex(b"hello"), "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824");
    }

    #[test]
    fn test_memory_ledger_read_and_append() {
        let tmp = std::env::temp_dir().join(format!("hydra-mem-test-{}", std::process::id()));
        let ledger = tmp.join("ledger.jsonl");
        let _ = std::fs::remove_dir_all(&tmp);

        let initial = read_memory_ledger(&ledger);
        assert!(initial.is_empty());

        let f1 = append_memory_fact(&ledger, "user_preference", "Dark theme preferred", "user:1").unwrap();
        assert_eq!(f1.seq, 1);
        assert_eq!(f1.prev, "0".repeat(64));
        assert!(!f1.hash.is_empty());

        let f2 = append_memory_fact(&ledger, "compiler_target", "x86_64-pc-windows-msvc", "agent:hydra").unwrap();
        assert_eq!(f2.seq, 2);
        assert_eq!(f2.prev, f1.hash);

        let read_back = read_memory_ledger(&ledger);
        assert_eq!(read_back.len(), 2);
        assert_eq!(read_back[0].subject, "user_preference");
        assert_eq!(read_back[1].body, "x86_64-pc-windows-msvc");

        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_telemetry_and_options_persistence() {
        let dir = std::env::temp_dir().join(format!("hydra-opts-{}", std::process::id()));
        let path = dir.join("state.json");
        let mut m = Model::new(Some(path.clone()), ".".into());

        m.apply_options("codellama:34b", "http://localhost:11434", "http://localhost:8080", "http://localhost:9000/v1", 2_000_000, "C:\\Users\\jpm05\\Documents");
        assert_eq!(m.local_model_alias, "codellama:34b");
        assert_eq!(m.context_window, 2_000_000);
        assert_eq!(m.cwd, "C:\\Users\\jpm05\\Documents");

        m.apply(Ev::Usage {
            model: "mimo".into(),
            prompt: 10_000,
            completion: 2_000,
            cost: 0.05,
        });

        let (p, c, win, pct) = m.telemetry();
        assert_eq!((p, c, win), (10_000, 2_000, 2_000_000));
        assert!((pct - 0.6).abs() < 1e-4);

        let reloaded = Model::new(Some(path), ".".into());
        assert_eq!(reloaded.local_model_alias, "codellama:34b");
        assert_eq!(reloaded.context_window, 2_000_000);
        assert_eq!(reloaded.cwd, "C:\\Users\\jpm05\\Documents");
        assert_eq!(reloaded.prompt_tokens, 10_000);
        assert_eq!(reloaded.completion_tokens, 2_000);

        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn test_agent_roster_lineup() {
        let mut m = Model::new(None, ".".into());
        assert!(m.agents.is_empty());

        m.apply(Ev::ToolStart {
            turn: "t1".into(),
            call: 1,
            tool: "invoke_subagent".into(),
            args: json!({"prompt": "Audit memory leaks"}),
        });
        assert_eq!(m.agents.len(), 1);
        assert_eq!(m.agents[0].name, "Child Subagent");
        assert_eq!(m.agents[0].task, "Audit memory leaks");
        assert_eq!(m.agents[0].status, "RUNNING");

        m.apply(Ev::ToolResult {
            turn: "t1".into(),
            tool: "invoke_subagent".into(),
            is_error: false,
            output: "Passed".into(),
        });
        assert_eq!(m.agents[0].status, "COMPLETED");
    }

    #[test]
    fn state_round_trips_through_disk() {
        let dir = std::env::temp_dir().join(format!("hydra-model-{}", std::process::id()));
        let path = dir.join("state.json");
        let mut a = Model::new(Some(path.clone()), ".".into());
        a.new_session();
        a.sessions[0].messages.push(Msg { user: true, header: "YOU".into(), text: "hello".into(), actions: vec![] });
        a.tokens = 1234;
        a.cost = 0.5;
        a.model = 3;
        a.set_nav("swarm");
        a.save();
        let b = Model::new(Some(path), ".".into());
        assert_eq!(b.sessions.len(), 2);
        assert_eq!(b.sessions[0].messages.last().unwrap().text, "hello");
        assert_eq!((b.tokens, b.model, b.nav), (1234, 3, "swarm"));
        assert_eq!(b.sessions[0].thread, a.sessions[0].thread);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn events_fold_into_the_bubble_and_the_tally() {
        let mut m = Model::new(None, ".".into());
        m.busy = Some(("t1".into(), m.sessions[0].id));
        m.sessions[0].messages.push(Msg { user: false, header: "X".into(), text: String::new(), actions: vec!["◐ Thinking".into()] });
        m.apply(Ev::Token { turn: "t1".into(), text: "ab".into() });
        m.apply(Ev::Token { turn: "t1".into(), text: "cd".into() });
        assert_eq!(m.sessions[0].messages.last().unwrap().text, "abcd");
        m.apply(Ev::Usage { model: "x".into(), prompt: 900, completion: 100, cost: 0.0312 });
        assert_eq!(m.tally(), ("1,000".to_string(), "$0.0312".to_string()));
        m.apply(Ev::Done { turn: "t1".into(), text: "abcd!".into(), ok: true, cancelled: false });
        assert!(m.busy.is_none());
        assert_eq!(m.sessions[0].messages.last().unwrap().text, "abcd!");
    }
}
