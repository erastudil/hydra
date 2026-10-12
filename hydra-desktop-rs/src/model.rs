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

#[derive(Serialize, Deserialize, Default)]
struct Saved {
    sessions: Vec<Session>,
    active: usize,
    counter: u32,
    nav: String,
    model: usize,
    tokens: u64,
    cost: f64,
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

/// Write a file so a crash at any point leaves either the old or the new content: temp file, fsync, keep `.bak`, rename.
pub fn write_atomic(path: &std::path::Path, bytes: &[u8]) {
    use std::io::Write;
    let tmp = path.with_extension("tmp");
    let ok = std::fs::File::create(&tmp).and_then(|mut f| f.write_all(bytes).and_then(|_| f.sync_all())).is_ok();
    if !ok {
        return;
    }
    if path.is_file() {
        let _ = std::fs::copy(path, path.with_extension("bak"));
    }
    let _ = std::fs::rename(&tmp, path);
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
        let mut m = Model {
            sessions: vec![],
            active: 0,
            counter: 0,
            nav: "chat",
            drawer_tab: "screen",
            model: 0,
            tokens: 0,
            cost: 0.0,
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
            let try_load = |path: &std::path::Path| std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str::<Saved>(&t).ok());
            let loaded = try_load(p).or_else(|| try_load(&p.with_extension("bak")));
            if let Some(saved) = loaded {
                if !saved.sessions.is_empty() {
                    m.active = saved.active.min(saved.sessions.len() - 1);
                    m.sessions = saved.sessions;
                    m.counter = saved.counter;
                    m.nav = nav_key(&saved.nav);
                    m.model = saved.model.min(MODELS.len() - 1);
                    m.tokens = saved.tokens;
                    m.cost = saved.cost;
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
        };
        if let Some(dir) = p.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(t) = serde_json::to_string_pretty(&saved) {
            write_atomic(p, t.as_bytes());
        }
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
                m.actions.push(format!("× {msg}"));
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
                self.term.push_str(&format!("\n$ {tool} {}", short(&args)));
            }
            Ev::ToolResult { turn, tool, is_error, output } => {
                if let Some((t, sid)) = self.busy.clone() {
                    if t == turn {
                        let first = output.lines().next().unwrap_or("").chars().take(100).collect::<String>();
                        if let Some(m) = self.last_assistant(sid) {
                            m.actions.push(format!("{} {tool}: {first}", if is_error { "×" } else { "✓" }));
                        }
                    }
                }
                self.term.push_str(&format!("\n{}{output}", if is_error { "[error] " } else { "" }));
            }
            Ev::Diff { path, diff, .. } => {
                self.diffs.push(DiffItem { path, diff });
                self.drawer_tab = "diff";
            }
            Ev::Usage { prompt, completion, cost, .. } => {
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
                self.agent_status = st;
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

#[cfg(test)]
mod tests {
    use super::*;

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
    fn truncated_state_recovers_from_backup() {
        let dir = std::env::temp_dir().join(format!("hydra-bak-{}", std::process::id()));
        let path = dir.join("state.json");
        let mut a = Model::new(Some(path.clone()), ".".into());
        a.sessions[0].messages.push(Msg { user: true, header: "YOU".into(), text: "keep me".into(), actions: vec![] });
        a.save();
        a.save(); // second save leaves the first as .bak
        std::fs::write(&path, b"{\"sessions\": [ {tru").unwrap(); // simulated torn write
        let b = Model::new(Some(path), ".".into());
        assert_eq!(b.sessions[0].messages.last().unwrap().text, "keep me");
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
