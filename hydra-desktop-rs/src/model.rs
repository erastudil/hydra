//! UI-independent state. Backend events and action replies are folded in here so the logic is testable without a window.

use crate::backend::{Backend, Ev};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;

pub const MODELS: &[(&str, &str)] = &[
    ("bonsai 2 9b", "Bonsai 2 9B · 2GB (Default Agent)"),
    ("bonsai 2 2b", "Bonsai 2 2B · 500MB (Ultra-Light)"),
    ("bonsai 2 4b", "Bonsai 2 4B · 1GB (Compact)"),
    ("bonsai 2 27b", "Bonsai 2 27B · 6GB (Flagship)"),
    ("mimo 2.6 flash", "Xiaomi MiMo 2.6 Flash (Daily Driver)"),
    ("mimo 2.6 pro", "Xiaomi MiMo 2.6 Pro (Speed Dial)"),
    ("glm 5.3 flash", "GLM 5.3 Flash (Fallback)"),
    ("deepseek 4.1 flash", "DeepSeek 4.1 Flash"),
    ("sonnet 5.5", "Claude Sonnet 5.5"),
    ("claude-3-7-sonnet", "Claude 3.7 Sonnet"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("qwen-3.8-27b:free", "Qwen 3.8 27B (Free)"),
    ("deepseek-chat:free", "DeepSeek Chat (Free)"),
    ("llama-3.3-70b-instruct:free", "Llama 3.3 70B (Free)"),
    ("opus 5.5", "Claude Opus 5.5"),
    ("sol 6.1 pro", "GPT-6.1 Sol Pro"),
    ("free", "Free Forge Routing"),
    ("local", "Offline Local (Ollama)"),
];

/// Normalize user/env model strings such as `"mimo.2.6 flash"` to canonical `MODELS` keys.
pub fn normalize_model_key(raw: &str) -> String {
    let s = raw.trim().to_lowercase();
    match s.as_str() {
        "bonsai" | "bonsai 9b" | "bonsai-2-9b" | "bonsai_2_9b" | "bonsai 2 9b" | "bonsai.2.9b" => "bonsai 2 9b".into(),
        "bonsai 2b" | "bonsai-2-2b" | "bonsai_2_2b" | "bonsai 2 2b" | "bonsai.2.2b" => "bonsai 2 2b".into(),
        "bonsai 4b" | "bonsai-2-4b" | "bonsai_2_4b" | "bonsai 2 4b" | "bonsai.2.4b" => "bonsai 2 4b".into(),
        "bonsai 27b" | "bonsai-2-27b" | "bonsai_2_27b" | "bonsai 2 27b" | "bonsai.2.27b" => "bonsai 2 27b".into(),
        "mimo.2.6 flash" | "mimo-2.6-flash" | "mimo_2_6_flash" | "mimo" | "mimo flash" => "mimo 2.6 flash".into(),
        "mimo.2.6 pro" | "mimo-2.6-pro" | "mimo_2_6_pro" | "mimo pro" => "mimo 2.6 pro".into(),
        "glm.5.3 flash" | "glm-5.3-flash" | "glm_5_3_flash" | "glm" | "glm flash" => "glm 5.3 flash".into(),
        _ => s,
    }
}

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
    #[serde(default)]
    pub project: u32,
    #[serde(default)]
    pub unread: bool,
    #[serde(default)]
    pub archived: bool,
    #[serde(default)]
    pub pinned: bool,
    #[serde(default)]
    pub updated: u64,
    #[serde(default)]
    pub diffs: Vec<DiffItem>,
    #[serde(default = "default_permission")]
    pub permission: String,
    #[serde(default = "default_context")]
    pub context: String,
    #[serde(default)]
    pub checkpoints: Vec<Checkpoint>,
    /// Checkpoint taken just before the last rewind, so a rewind can be undone.
    #[serde(default)]
    pub pre_rewind: Option<String>,
    /// Own git worktree for this thread. Tools run here instead of the project folder.
    #[serde(default)]
    pub worktree: Option<String>,
    /// Messages a conversation rewind took away, kept so Undo rewind can put them back.
    #[serde(default)]
    pub rewound_tail: Vec<Msg>,
}

/// A snapshot of the project taken before a message was sent.
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Checkpoint {
    pub sha: String,
    /// Agent checkpoint id (shadow git under ~/.hydra/shadow). When set, rewinds go through the agent.
    #[serde(default)]
    pub agent: Option<String>,
    pub label: String,
    /// Index into `messages` of the user message this checkpoint precedes.
    pub at: usize,
    pub ts: u64,
}

pub const PERMISSIONS: &[(&str, &str)] = &[
    ("manual", "Ask every time"),
    ("accept_edits", "Accept edits"),
    ("plan", "Plan (read only)"),
    ("bypass", "Bypass approvals"),
];

pub const CONTEXT_MODES: &[(&str, &str)] = &[
    ("recall", "Recall: keep every turn, reload only what matches"),
    ("compact", "Compact: summarize older turns near the limit"),
    ("sliding", "Sliding: keep the last few turns whole"),
];

fn default_context() -> String {
    "recall".into()
}

fn default_permission() -> String {
    // a new thread asks before anything that changes files or runs a command; the chip next to the prompt changes it
    "manual".into()
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Project {
    pub id: u32,
    pub name: String,
    pub path: String,
    #[serde(default)]
    pub pinned: bool,
}

/// What a thread is doing right now, for the sidebar dot.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Status {
    Idle,
    Running,
    NeedsApproval,
    Unread,
}

/// D18: errors name the model the user picked (its label, not the provider's id) and say what to do next.
pub fn explain_error(label: &str, raw: &str) -> String {
    let msg = raw.strip_prefix("ProviderError:").unwrap_or(raw).trim();
    let low = msg.to_lowercase();
    let hint = if low.contains("credentials") || low.contains("401") || low.contains("api key") {
        " Open Settings (Ctrl+,) to add a key."
    } else if low.contains("unknown url type") || low.contains("invalid url") || low.contains("no host") {
        " The endpoint URL is not valid: fix it in Settings (Ctrl+,)."
    } else if low.contains("connection refused") || low.contains("name or service not known") || low.contains("failed to establish") {
        " Nothing answered at the endpoint: is the local server running? The URL is in Settings (Ctrl+,)."
    } else if low.contains("timed out") {
        " The provider did not answer in time. Try again or pick another model."
    } else {
        ""
    };
    if msg.contains(label) {
        format!("{msg}{hint}")
    } else {
        format!("{label}: {msg}{hint}")
    }
}

fn now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct DiffItem {
    pub path: String,
    pub diff: String,
}

#[derive(Clone, Debug)]
pub struct SearchHit {
    pub session: usize,
    pub message: usize,
    pub thread_name: String,
    pub snippet: String,
}

#[derive(Clone, Debug)]
pub struct Approval {
    pub sid: u32,
    pub turn: String,
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
    Rewind(u32, String, usize),
    UndoRewind(u32),
    Rules,
    Query,
    TaskStop,
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
    #[serde(default)]
    projects: Vec<Project>,
    #[serde(default)]
    active_project: u32,
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
    /// Set by HYDRA_TAB: incoming diffs must not switch the drawer away.
    pub tab_pinned: bool,
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
    pub projects: Vec<Project>,
    pub active_project: u32,
    pub runs: HashMap<String, u32>, // turn id or action id -> session id; at most one run per thread
    pub approvals: Vec<Approval>,
    pub agent_ready: bool,
    /// The agent snapshots the project before each turn (capability "checkpoint").
    pub agent_checkpoints: bool,
    cp_pending: HashMap<String, (u32, usize, String)>,
    /// What this agent process last received per thread: [cwd, model, permission, mode].
    pub sent: HashMap<String, [String; 4]>,
    /// Approval rules of the thread last listed: (thread, rules).
    pub rules: (String, Vec<Value>),
    /// Skills the agent offers (skill_list) and the ids picked for the next turns.
    pub skills: Vec<Value>,
    pub picked_skills: Vec<String>,
    /// Background tasks by task id, from task_update and task_list. They outlive turns.
    pub tasks: std::collections::BTreeMap<String, Value>,
    /// Last mcp_status: (config path, servers).
    pub mcp: Option<(Option<String>, Vec<Value>)>,
    pub caps: Vec<String>,
    state_was_bad: bool,
    /// An always-rule was just added; reload the Rules list after the agent's next event for it.
    pub rules_stale: bool,
    /// Threads the current agent process has on disk (session_list), refreshed after every hello.
    pub agent_threads: Vec<Value>,
    /// Threads whose turn a crash cut short; reloaded from the agent's history after the next hello.
    pub reload: Vec<u32>,
    /// Prompt tokens of the last provider request, for the context meter.
    pub last_prompt: u64,
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
pub fn write_atomic(path: &std::path::Path, bytes: &[u8]) -> bool {
    use std::io::Write;
    let tmp = path.with_extension("tmp");
    let ok = std::fs::File::create(&tmp).and_then(|mut f| f.write_all(bytes).and_then(|_| f.sync_all())).is_ok();
    if !ok {
        return false;
    }
    // only a state that parses becomes the backup, so a torn state.json never overwrites a good .bak
    if path.is_file() && std::fs::read(path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()).is_some() {
        let _ = std::fs::copy(path, path.with_extension("bak"));
    }
    std::fs::rename(&tmp, path).is_ok()
}

pub fn state_dir() -> PathBuf {
    if let Ok(d) = std::env::var("HYDRA_STATE_DIR") {
        return PathBuf::from(d);
    }
    let home = std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")).unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".hydra").join("desktop")
}

pub const UNSAVED: &str = "Not saved: the agent stopped mid-turn, so this partial reply is not in the thread's history";
pub const HELD_ELSEWHERE: &str = "This thread is running in another Hydra agent; wait for it to finish.";

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
    let s = s.replace('\n', " ⏎ ");
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
            tab_pinned: false,
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
            projects: vec![],
            active_project: 0,
            runs: HashMap::new(),
            approvals: vec![],
            agent_ready: false,
            agent_checkpoints: false,
            skills: vec![],
            picked_skills: vec![],
            tasks: Default::default(),
            mcp: None,
            caps: vec![],
            state_was_bad: false,
            rules_stale: false,
            agent_threads: vec![],
            reload: vec![],
            last_prompt: 0,
            cp_pending: HashMap::new(),
            sent: HashMap::new(),
            rules: (String::new(), vec![]),
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
            let main = try_load(p);
            m.state_was_bad = main.is_none() && p.is_file();
            let loaded = main.or_else(|| try_load(&p.with_extension("bak")));
            if let Some(saved) = loaded {
                if !saved.sessions.is_empty() {
                    m.projects = saved.projects;
                    m.active_project = saved.active_project;
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
        if m.projects.is_empty() {
            let id = m.add_project_unchecked(&m.cwd.clone());
            m.active_project = id;
        }
        let first_project = m.projects[0].id;
        for sess in &mut m.sessions {
            if !m.projects.iter().any(|p| p.id == sess.project) {
                sess.project = first_project;
            }
        }
        if !m.projects.iter().any(|p| p.id == m.active_project) {
            m.active_project = first_project;
        }
        if m.sessions.is_empty() {
            m.new_session();
        }
        let mut dirty = m.state_was_bad;
        // a reply that was streaming when the app died cannot resume: say so instead of "Thinking…" forever
        for sess in &mut m.sessions {
            if let Some(last) = sess.messages.last_mut().filter(|x| !x.user && x.actions.iter().any(|a| a == "Thinking…")) {
                last.actions.retain(|a| a != "Thinking…");
                last.actions.push("× interrupted: the app closed during this turn".into());
                dirty = true;
            }
        }
        dirty |= m.replay_journal();
        if dirty {
            m.save();
        }
        m.focus_project_thread();
        m
    }

    /// Streamed tokens go to an append-only journal beside state.json, so a kill -9 mid-turn keeps the streamed text.
    /// A full save truncates it; loading replays it onto each thread's last reply.
    fn journal(&self, sid: u32, text: &str) {
        let Some(p) = &self.state_path else { return };
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(p.with_extension("journal")) {
            let _ = f.write_all(format!("{}\n", json!({"s": sid, "t": text})).as_bytes());
        }
    }

    fn replay_journal(&mut self) -> bool {
        let Some(p) = self.state_path.clone() else { return false };
        let Ok(text) = std::fs::read_to_string(p.with_extension("journal")) else { return false };
        let mut any = false;
        for line in text.lines() {
            // a torn last line from the kill is skipped: at most that token is lost
            let Ok(v) = serde_json::from_str::<Value>(line) else { continue };
            let (Some(sid), Some(t)) = (v["s"].as_u64(), v["t"].as_str()) else { continue };
            if let Some(m) = self.last_assistant(sid as u32) {
                m.actions.retain(|a| a != "Thinking…");
                m.text.push_str(t);
                any = true;
            }
        }
        any
    }

    pub fn save(&self) {
        let Some(p) = &self.state_path else { return };
        let saved = Saved {
            projects: self.projects.clone(),
            active_project: self.active_project,
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
            if write_atomic(p, t.as_bytes()) {
                let _ = std::fs::remove_file(p.with_extension("journal"));
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
        // seconds, then a per-process nonce: two windows started in the same second must not share thread ids,
        // or the agents' one-turn-per-thread lock answers busy for a thread that is not really shared
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
        let stamp = format!("{}-{:x}", now.as_secs(), (now.subsec_nanos() ^ std::process::id().rotate_left(16)) & 0xffffff);
        let first = self.sessions.is_empty();
        let text = if first { greeting().text } else { format!("Session {} primed. Dispatch instruction or summon heads.", self.counter) };
        self.sessions.push(Session {
            id: self.counter,
            thread: format!("desk-{stamp}-{}", self.counter),
            name: format!("Session {}", self.counter),
            messages: vec![Msg { user: false, header: "HYDRA ORCHESTRATOR".into(), text, actions: vec![] }],
            project: self.active_project,
            unread: false,
            archived: false,
            pinned: false,
            updated: now.as_secs(),
            diffs: vec![],
            permission: default_permission(),
            context: default_context(),
            checkpoints: vec![],
            pre_rewind: None,
            worktree: None,
            rewound_tail: vec![],
        });
        self.active = self.sessions.len() - 1;
        self.save();
    }

    /// Close (archive) a thread. A project always keeps one visible thread; a running thread cannot be closed.
    pub fn close_session(&mut self, i: usize) {
        if i >= self.sessions.len() || self.is_running(self.sessions[i].id) {
            return;
        }
        if self.visible().len() <= 1 && self.sessions[i].project == self.active_project {
            return;
        }
        self.sessions[i].archived = true;
        if self.active == i {
            self.focus_project_thread();
        }
        self.save();
    }

    /// Indices of the threads shown in the tab bar: the active project's, not archived, pinned first.
    pub fn visible(&self) -> Vec<usize> {
        let mut v: Vec<usize> = (0..self.sessions.len()).filter(|&i| self.sessions[i].project == self.active_project && !self.sessions[i].archived).collect();
        v.sort_by_key(|&i| (!self.sessions[i].pinned, i));
        v
    }

    pub fn thread_indices(&self, project: u32) -> Vec<usize> {
        (0..self.sessions.len()).filter(|&i| self.sessions[i].project == project && !self.sessions[i].archived).collect()
    }

    fn add_project_unchecked(&mut self, path: &str) -> u32 {
        let id = self.projects.iter().map(|p| p.id).max().unwrap_or(0) + 1;
        let name = std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().to_string()).filter(|n| !n.is_empty()).unwrap_or_else(|| path.to_string());
        self.projects.push(Project { id, name, path: path.to_string(), pinned: false });
        id
    }

    /// Open a folder as a project (or switch to it if it is already open) and give it a first thread.
    pub fn open_project(&mut self, path: &str) -> Result<u32, String> {
        let p = std::path::Path::new(path.trim());
        if !p.is_dir() {
            return Err(format!("not a folder: {}", path.trim()));
        }
        let canon = std::fs::canonicalize(p).map(|c| c.display().to_string()).unwrap_or_else(|_| path.trim().to_string());
        let canon = canon.strip_prefix(r"\\?\").unwrap_or(&canon).to_string();
        let id = match self.projects.iter().find(|q| q.path == canon) {
            Some(q) => q.id,
            None => self.add_project_unchecked(&canon),
        };
        self.set_project(id);
        if self.thread_indices(id).is_empty() {
            self.new_session();
        }
        self.save();
        Ok(id)
    }

    pub fn set_project(&mut self, id: u32) {
        if self.projects.iter().any(|p| p.id == id) {
            self.active_project = id;
            self.focus_project_thread();
        }
    }

    /// Make `active` point at a visible thread of the active project (creating one if there is none).
    pub fn focus_project_thread(&mut self) {
        if self.sessions.get(self.active).map(|s| s.project == self.active_project && !s.archived).unwrap_or(false) {
            return;
        }
        match self.visible().last().copied() {
            Some(i) => self.active = i,
            None => {
                if !self.sessions.is_empty() || self.projects.is_empty() {
                    self.new_session();
                }
            }
        }
        if let Some(s) = self.sessions.get_mut(self.active) {
            s.unread = false;
        }
    }

    pub fn select(&mut self, i: usize) {
        if i < self.sessions.len() {
            self.active_project = self.sessions[i].project;
            self.active = i;
            self.sessions[i].unread = false;
        }
    }

    pub fn project_path(&self) -> String {
        self.projects.iter().find(|p| p.id == self.active_project).map(|p| p.path.clone()).unwrap_or_else(|| self.cwd.clone())
    }

    /// Where the active thread works: its own worktree if it has one, else the project folder.
    pub fn work_path(&self) -> String {
        self.sessions[self.active].worktree.clone().unwrap_or_else(|| self.project_path())
    }

    /// Give the active thread its own git worktree on a new branch `hydra/<slug>`.
    pub fn start_worktree(&mut self) -> Result<String, String> {
        let sid = self.sessions[self.active].id;
        if self.is_running(sid) {
            return Err("stop the running turn first".into());
        }
        if self.sessions[self.active].worktree.is_some() {
            return Err("this thread already has a worktree".into());
        }
        let root = self.project_path();
        let name = format!("{}-{}", self.sessions[self.active].name, self.sessions[self.active].id);
        let path = crate::git::worktree_add(std::path::Path::new(&root), &name)?;
        let shown = path.display().to_string();
        self.sessions[self.active].worktree = Some(shown.clone());
        self.save();
        Ok(shown)
    }

    pub fn remove_worktree(&mut self) -> Result<(), String> {
        let sid = self.sessions[self.active].id;
        if self.is_running(sid) {
            return Err("stop the running turn first".into());
        }
        let wt = self.sessions[self.active].worktree.clone().ok_or("this thread has no worktree")?;
        crate::git::worktree_remove(std::path::Path::new(&self.project_path()), std::path::Path::new(&wt))?;
        self.sessions[self.active].worktree = None;
        self.save();
        Ok(())
    }

    pub fn is_running(&self, sid: u32) -> bool {
        self.runs.values().any(|&s| s == sid)
    }

    pub fn active_busy(&self) -> bool {
        self.is_running(self.sessions[self.active].id)
    }

    pub fn any_busy(&self) -> bool {
        !self.runs.is_empty()
    }

    pub fn status(&self, i: usize) -> Status {
        let s = &self.sessions[i];
        if self.approvals.iter().any(|a| a.sid == s.id) {
            Status::NeedsApproval
        } else if self.is_running(s.id) {
            Status::Running
        } else if s.unread {
            Status::Unread
        } else {
            Status::Idle
        }
    }

    pub fn approval_for_active(&self) -> Option<&Approval> {
        let sid = self.sessions[self.active].id;
        self.approvals.iter().find(|a| a.sid == sid)
    }

    pub fn run_session(&self, turn: &str) -> Option<u32> {
        self.runs.get(turn).copied()
    }

    fn take_checkpoint(&mut self, cwd: &str, label: &str) {
        let root = std::path::Path::new(cwd);
        if !crate::git::is_repo(root) {
            return;
        }
        let (thread, n) = {
            let s = &self.sessions[self.active];
            (s.thread.clone(), s.checkpoints.len() + 1)
        };
        if let Ok(sha) = crate::git::checkpoint(root, &format!("{thread}/{n}")) {
            let s = &mut self.sessions[self.active];
            let at = s.messages.len();
            s.checkpoints.push(Checkpoint { sha, agent: None, label: label.chars().take(60).collect(), at, ts: now() });
        }
    }

    /// The checkpoint that precedes the user message at `msg_index`, if one was taken.
    pub fn checkpoint_for(&self, msg_index: usize) -> Option<usize> {
        self.sessions[self.active].checkpoints.iter().position(|c| c.at == msg_index)
    }

    /// Put the project's files back to how they were before checkpoint `i`. The state just before the rewind is saved
    /// first so it can be undone. Refused while the thread is running.
    pub fn rewind_code(&mut self, be: Option<&mut Backend>, i: usize) -> Result<String, String> {
        let sid = self.sessions[self.active].id;
        if self.is_running(sid) {
            return Err("stop the running turn before rewinding".into());
        }
        let cp = self.sessions[self.active].checkpoints.get(i).cloned().ok_or("no such checkpoint")?;
        let thread = self.sessions[self.active].thread.clone();
        if let Some(agent_id) = &cp.agent {
            // the agent restores from its shadow repository and snapshots the current state first (the undo point)
            let be = be.ok_or("the agent is not running")?;
            let id = be.rewind(&thread, agent_id);
            self.pending.insert(id, Pending::Rewind(sid, cp.label.clone(), cp.at));
            return Ok(cp.label);
        }
        let cwd = self.work_path();
        let root = std::path::Path::new(&cwd);
        let pre = crate::git::checkpoint(root, &format!("{thread}/pre-rewind"))?;
        crate::git::restore(root, &cp.sha)?;
        let s = &mut self.sessions[self.active];
        s.pre_rewind = Some(pre);
        s.messages.push(Msg { user: false, header: "HYDRA".into(), text: format!("Rewound files to before: {}", cp.label), actions: vec!["Undo rewind is available".into()] });
        self.save();
        Ok(cp.label)
    }

    pub fn undo_rewind(&mut self, be: Option<&mut Backend>) -> Result<(), String> {
        let sid = self.sessions[self.active].id;
        if self.is_running(sid) {
            return Err("stop the running turn first".into());
        }
        let pre = self.sessions[self.active].pre_rewind.clone().ok_or("nothing to undo")?;
        if let Some(backup) = pre.strip_prefix("agent:") {
            let be = be.ok_or("the agent is not running")?;
            let thread = self.sessions[self.active].thread.clone();
            let id = be.rewind(&thread, backup);
            self.pending.insert(id, Pending::UndoRewind(sid));
            self.sessions[self.active].pre_rewind = None;
            return Ok(());
        }
        self.sessions[self.active].pre_rewind = None;
        let cwd = self.work_path();
        crate::git::restore(std::path::Path::new(&cwd), &pre)?;
        self.sessions[self.active].messages.push(Msg { user: false, header: "HYDRA".into(), text: "Rewind undone: files are back as they were.".into(), actions: vec![] });
        self.save();
        Ok(())
    }

    pub fn load_rules(&mut self, be: &mut Backend) {
        let thread = self.sessions[self.active].thread.clone();
        let id = be.rules(&thread, None);
        self.pending.insert(id, Pending::Rules);
    }

    pub fn remove_rule(&mut self, be: &mut Backend, rule: &str) {
        let thread = self.sessions[self.active].thread.clone();
        let id = be.rules(&thread, Some(rule));
        self.pending.insert(id, Pending::Rules);
    }

    pub fn set_permission(&mut self, p: &str) {
        if PERMISSIONS.iter().any(|x| x.0 == p) {
            self.sessions[self.active].permission = p.into();
            self.save();
        }
    }

    /// Allow this call and save a rule. scope is "thread" or "project"; prefix only applies to run_command.
    pub fn approve_always(&mut self, be: &mut Backend, id: &str, scope: &str, prefix: Option<&str>) -> bool {
        match self.approvals.iter().position(|a| a.id == id) {
            Some(pos) => {
                let a = self.approvals.remove(pos);
                be.approve_always(&a.id, scope, prefix);
                // D20: the agent stores the rule while it handles the approval; reload the Rules list once it has
                self.rules_stale = true;
                self.rules.0.clear();
                true
            }
            None => false,
        }
    }

    pub fn cycle_permission(&mut self) {
        let cur = self.sessions[self.active].permission.clone();
        let i = PERMISSIONS.iter().position(|p| p.0 == cur).unwrap_or(0);
        self.sessions[self.active].permission = PERMISSIONS[(i + 1) % PERMISSIONS.len()].0.into();
        self.save();
    }

    fn touch(&mut self, sid: u32, background: bool) {
        let active_id = self.sessions[self.active].id;
        let t = now();
        let mut finished = None;
        if let Some(s) = self.session_mut(sid) {
            s.updated = t;
            if background && sid != active_id {
                s.unread = true;
                finished = Some(s.name.clone());
            }
        }
        // a thread you are not looking at finished: say so (toast), the sidebar dot stays until you open it
        if let Some(name) = finished {
            if self.notice.is_empty() {
                self.notice = format!("\u{201c}{name}\u{201d} finished");
            }
        }
    }

    pub fn clear_active(&mut self) {
        self.sessions[self.active].messages.clear();
        self.save();
    }

    pub fn pending_skills(&mut self, be: &mut Backend) {
        let id = be.skills();
        self.act(Pending::Query, id);
    }

    pub fn pending_mcp(&mut self, be: &mut Backend, cwd: &str, refresh: bool) {
        let id = be.mcp(cwd, refresh);
        self.act(Pending::Query, id);
    }

    pub fn toggle_mcp(&mut self, be: &mut Backend, cwd: &str, server: &str, enabled: bool) {
        let id = be.mcp_toggle(cwd, server, enabled);
        self.act(Pending::Query, id);
    }

    /// After a respawn: ask the agent for the stored turns of every thread a crash cut short.
    pub fn reload_cut_threads(&mut self, be: &mut Backend) {
        for sid in std::mem::take(&mut self.reload) {
            if let Some(s) = self.sessions.iter().find(|s| s.id == sid) {
                let id = be.history(&s.thread.clone());
                self.act(Pending::Query, id);
            }
        }
    }

    /// Rebuild a thread's chat from what the agent stored, so the app and the agent transcripts agree.
    pub fn reload_from_history(&mut self, thread: &str, turns: &[Value]) {
        let Some(s) = self.sessions.iter_mut().find(|s| s.thread == thread) else { return };
        let header = s.messages.iter().rev().find(|m| !m.user && m.header != "HYDRA").map(|m| m.header.clone()).unwrap_or_else(|| "HYDRA ORCHESTRATOR".into());
        let text_of = |c: &Value| match c {
            Value::String(t) => t.clone(),
            Value::Array(parts) => parts.iter().filter_map(|p| p.get("text").and_then(|x| x.as_str())).collect::<Vec<_>>().join(""),
            _ => String::new(),
        };
        let mut msgs = vec![s.messages.first().cloned().unwrap_or_else(greeting)];
        for t in turns {
            let role = t.get("role").and_then(|x| x.as_str()).unwrap_or("");
            let text = text_of(t.get("content").unwrap_or(&Value::Null));
            match role {
                "user" => msgs.push(Msg { user: true, header: "YOU".into(), text, actions: vec![] }),
                "assistant" if !text.is_empty() => msgs.push(Msg { user: false, header: header.clone(), text, actions: vec![] }),
                _ => {}
            }
        }
        msgs.push(Msg { user: false, header: "HYDRA".into(), text: "Reloaded from the agent after it restarted. The reply cut off by the crash was not saved.".into(), actions: vec![] });
        let n = msgs.len();
        s.messages = msgs;
        s.checkpoints.retain(|c| c.at < n);
        s.rewound_tail.clear();
        let sid = s.id;
        self.touch(sid, false);
    }

    pub fn list_sessions(&mut self, be: &mut Backend) {
        let id = be.sessions();
        self.act(Pending::Query, id);
    }

    pub fn pending_tasks(&mut self, be: &mut Backend) {
        let id = be.tasks();
        self.act(Pending::Query, id);
    }

    pub fn stop_task(&mut self, be: &mut Backend, task: &str) {
        let id = be.task_stop(task);
        self.act(Pending::TaskStop, id);
    }

    /// Case-insensitive search over every thread's messages, newest thread first.
    pub fn search(&self, q: &str, limit: usize) -> Vec<SearchHit> {
        let q = q.trim().to_lowercase();
        if q.is_empty() {
            return vec![];
        }
        let mut order: Vec<usize> = (0..self.sessions.len()).filter(|i| !self.sessions[*i].archived).collect();
        order.sort_by_key(|i| std::cmp::Reverse(self.sessions[*i].updated));
        let mut out = vec![];
        for i in order {
            for (mi, m) in self.sessions[i].messages.iter().enumerate() {
                let low = m.text.to_lowercase();
                if let Some(at) = low.find(&q) {
                    let start = low[..at].char_indices().rev().nth(40).map(|c| c.0).unwrap_or(0);
                    let snippet: String = m.text.get(start..).unwrap_or(&m.text).chars().take(120).collect::<String>().replace('\n', " ");
                    out.push(SearchHit { session: i, message: mi, thread_name: self.sessions[i].name.clone(), snippet });
                    if out.len() >= limit {
                        return out;
                    }
                }
            }
        }
        out
    }

    pub fn toggle_skill(&mut self, id: &str) {
        if let Some(i) = self.picked_skills.iter().position(|p| p == id) {
            self.picked_skills.remove(i);
        } else {
            self.picked_skills.push(id.to_string());
        }
    }

    pub fn running_tasks(&self) -> usize {
        self.tasks.values().filter(|t| t.get("state").and_then(|x| x.as_str()) == Some("running")).count()
    }

    pub fn set_nav(&mut self, key: &str) {
        self.nav = nav_key(key);
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
        if text.is_empty() || self.active_busy() {
            return false;
        }
        let (sid, thread) = {
            let s = &self.sessions[self.active];
            (s.id, s.thread.clone())
        };
        let cwd = self.work_path();
        let model = MODELS[self.model].0;
        let header = MODELS[self.model].1.to_uppercase();
        let chat_like = !matches!(self.nav, "terminal" | "swarm");
        let (prompt, attached) = if chat_like { crate::fsx::expand_mentions(text, std::path::Path::new(&cwd)) } else { (text.to_string(), vec![]) };
        let agent_cp = chat_like && self.agent_checkpoints;
        if chat_like && !agent_cp {
            self.take_checkpoint(&cwd, text);
        }
        let user_at = self.sessions[self.active].messages.len();
        {
            let s = &mut self.sessions[self.active];
            s.messages.push(Msg { user: true, header: "YOU".into(), text: text.into(), actions: attached.iter().map(|a| format!("attached {a}")).collect() });
            s.messages.push(Msg { user: false, header, text: String::new(), actions: vec!["Thinking…".into()] });
        }
        self.turn_counter += 1;
        let turn = format!("t{}", self.turn_counter);
        match self.nav {
            "terminal" => {
                self.log(&format!("\n$ {text}"));
                let id = be.action("terminal:run", json!({"command": text, "cwd": cwd}));
                self.act(Pending::Terminal(sid), id.clone());
                self.runs.insert(id, sid);
            }
            "swarm" => {
                let id = be.action("swarm:run", json!({"task": text, "model": model}));
                self.act(Pending::Swarm(sid), id.clone());
                self.runs.insert(id, sid);
            }
            _ => {
                let permission = self.sessions[self.active].permission.clone();
                let context = self.sessions[self.active].context.clone();
                if agent_cp {
                    self.cp_pending.insert(turn.clone(), (sid, user_at, text.chars().take(60).collect()));
                }
                let now_meta = [cwd.clone(), model.to_string(), permission.clone(), context.clone()];
                let last = self.sent.get(&thread).cloned();
                let pick = |i: usize| -> Option<&str> { if last.as_ref().map(|l| l[i] == now_meta[i]).unwrap_or(false) { None } else { Some(now_meta[i].as_str()) } };
                be.turn(&turn, &prompt, &thread, pick(0), pick(1), pick(2), pick(3), &self.picked_skills);
                self.sent.insert(thread.clone(), now_meta.clone());
                self.runs.insert(turn, sid);
            }
        }
        self.save();
        true
    }

    /// Stop the running turn. Turns go through the adapter cancel; actions are not interruptible.
    pub fn stop(&mut self, be: &mut Backend) {
        let sid = self.sessions[self.active].id;
        let turns: Vec<String> = self.runs.iter().filter(|(_, &s)| s == sid).map(|(t, _)| t.clone()).collect();
        for t in turns {
            be.cancel(&t);
        }
    }

    /// Answer one specific pending approval, whichever thread it belongs to (the Approvals tab lists all of them).
    pub fn answer_approval_id(&mut self, be: &mut Backend, id: &str, decision: &str) -> bool {
        match self.approvals.iter().position(|a| a.id == id) {
            Some(pos) => {
                let a = self.approvals.remove(pos);
                be.approve(&a.id, decision);
                self.note_decision(&a, decision);
                true
            }
            None => false,
        }
    }

    pub fn cycle_context(&mut self) {
        let cur = self.sessions[self.active].context.clone();
        let i = CONTEXT_MODES.iter().position(|p| p.0 == cur).unwrap_or(0);
        self.sessions[self.active].context = CONTEXT_MODES[(i + 1) % CONTEXT_MODES.len()].0.into();
        self.save();
    }

    pub fn answer_approval(&mut self, be: &mut Backend, decision: &str) {
        let sid = self.sessions[self.active].id;
        if let Some(pos) = self.approvals.iter().position(|a| a.sid == sid) {
            let a = self.approvals.remove(pos);
            be.approve(&a.id, decision);
            self.note_decision(&a, decision);
        }
    }

    /// D27: a skipped or aborted tool call leaves a line in the chat, not just the model's reply.
    fn note_decision(&mut self, a: &Approval, decision: &str) {
        let what = match decision {
            "skip" => "skipped by you",
            "abort" => "denied, turn aborted",
            _ => return,
        };
        let line = format!("– {} {what}  {}", a.tool, short(&a.args));
        if let Some(m) = self.last_assistant(a.sid) {
            m.actions.push(line);
        }
    }

    pub fn agent_start(&mut self, be: &mut Backend, task: &str, max_steps: u32, profile: &str) {
        let id = be.action("agent:run", json!({"task": task, "max_steps": max_steps, "profile": profile, "cwd": self.cwd}));
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
        self.log(&format!("\n[CLICK] mouse_click x={x} y={y}"));
        let id = be.action("computer:click", json!({"x": x, "y": y}));
        self.act(Pending::Click, id);
    }

    pub fn type_text(&mut self, be: &mut Backend, text: &str) {
        self.log(&format!("\n[TYPE] {text}"));
        let id = be.action("computer:type", json!({"text": text}));
        self.act(Pending::Type, id);
    }

    fn finish_sid(&mut self, sid: u32) {
        self.runs.retain(|_, s| *s != sid);
        self.approvals.retain(|a| a.sid != sid);
        self.touch(sid, true);
        self.save();
    }

    /// Append to the terminal log, keeping it bounded so a huge tool result cannot make the window sluggish.
    fn log(&mut self, s: &str) {
        const CAP: usize = 200_000;
        const ONE: usize = 20_000;
        if s.len() > ONE {
            let mut end = ONE;
            while !s.is_char_boundary(end) {
                end -= 1;
            }
            self.term.push_str(&s[..end]);
            self.term.push_str(&format!("\n… {} more bytes not shown", s.len() - end));
        } else {
            self.term.push_str(s);
        }
        if self.term.len() > CAP {
            let mut cut = self.term.len() - CAP / 2;
            while !self.term.is_char_boundary(cut) {
                cut += 1;
            }
            let rest = self.term[cut..].to_string();
            self.term = format!("… earlier output trimmed\n{rest}");
        }
    }

    fn fail_bubble(&mut self, sid: u32, msg: &str) {
        let label = MODELS.get(self.model).map(|m| m.1).unwrap_or("the model");
        let explained = explain_error(label, msg);
        let msg = explained.as_str();
        if let Some(m) = self.last_assistant(sid) {
            m.actions.retain(|a| a != "Thinking…");
            if m.text.is_empty() {
                m.text = format!("Error: {msg}");
            } else {
                m.actions.push(format!("× {msg}"));
            }
        }
    }

    pub fn apply(&mut self, ev: Ev) {
        if self.rules_stale && matches!(ev, Ev::ToolStart { .. } | Ev::ToolResult { .. } | Ev::AutoDecision { .. } | Ev::Done { .. }) {
            self.rules_stale = matches!(ev, Ev::ToolStart { .. }); // reload after start, and once more after it finishes
            self.rules.0.clear();
        }
        match ev {
            Ev::Hello { caps } => {
                crate::app::timing_mark("hello");
                self.agent_ready = true;
                self.sent.clear(); // a new agent process: send every field again on each thread's next turn
                self.agent_checkpoints = caps.iter().any(|c| c == "checkpoint");
                self.tasks.clear(); // a respawned agent does not know the old tasks
                self.caps = caps;
            }
            Ev::Checkpoint { turn, ok, id, reason } => {
                if let Some((sid, at, label)) = self.cp_pending.remove(&turn) {
                    match (ok, id) {
                        (true, Some(id)) => {
                            if let Some(s) = self.session_mut(sid) {
                                s.checkpoints.push(Checkpoint { sha: String::new(), agent: Some(id), label, at, ts: now() });
                            }
                        }
                        _ => self.log(&format!("\n[checkpoint] not taken: {reason}")),
                    }
                }
            }
            Ev::RewindResult { id, backup, restored, removed, conversation, turns } => match self.pending.remove(&id) {
                Some(Pending::Rewind(sid, label, at)) => {
                    if let Some(s) = self.session_mut(sid) {
                        s.pre_rewind = Some(format!("agent:{backup}"));
                        // 0cf3884: the agent put the conversation back too, so the thread shows what it had then
                        let what = if conversation && at <= s.messages.len() {
                            s.rewound_tail = s.messages.split_off(at);
                            s.checkpoints.retain(|c| c.at <= at);
                            format!("files and conversation ({} turns kept)", turns.unwrap_or(0))
                        } else {
                            s.rewound_tail.clear();
                            "files only (this checkpoint kept no conversation)".to_string()
                        };
                        s.messages.push(Msg { user: false, header: "HYDRA".into(), text: format!("Rewound {what} to before: {label}\n{} restored, {} removed.", restored.len(), removed.len()), actions: vec!["Undo rewind is available".into()] });
                    }
                    self.save();
                }
                Some(Pending::UndoRewind(sid)) => {
                    if let Some(s) = self.session_mut(sid) {
                        if conversation && !s.rewound_tail.is_empty() {
                            // drop the rewind notice and anything said since, then put the rewound turns back
                            let at = s.messages.iter().rposition(|m| m.header == "HYDRA" && m.text.starts_with("Rewound ")).unwrap_or(s.messages.len());
                            s.messages.truncate(at);
                            let tail = std::mem::take(&mut s.rewound_tail);
                            s.messages.extend(tail);
                        }
                        s.messages.push(Msg { user: false, header: "HYDRA".into(), text: if conversation { "Rewind undone: files and conversation are back.".into() } else { "Rewind undone: files are back as they were.".into() }, actions: vec![] });
                    }
                    self.save();
                }
                _ => {}
            },
            Ev::Context { turn, rules, skills, mcp_tools, mcp_errors } => {
                if let Some(sid) = self.run_session(&turn) {
                    if let Some(m) = self.last_assistant(sid) {
                        if let Some(r) = &rules {
                            m.actions.push(format!("rules: {r}"));
                        }
                        if !skills.is_empty() {
                            m.actions.push(format!("skills: {}", skills.join(", ")));
                        }
                        if !mcp_tools.is_empty() {
                            m.actions.push(format!("mcp: {} tools", mcp_tools.len()));
                        }
                        if let Some(o) = mcp_errors.as_object() {
                            for (k, v) in o {
                                m.actions.push(format!("× mcp {k}: {}", v.as_str().unwrap_or("failed")));
                            }
                        }
                    }
                }
            }
            Ev::Retry { turn, attempt, of, wait, reason } => {
                if let Some(sid) = self.run_session(&turn) {
                    if let Some(m) = self.last_assistant(sid) {
                        m.actions.retain(|a| !a.starts_with("↻ retry"));
                        m.actions.push(format!("↻ retry {attempt}/{of} in {wait:.1}s: {reason}"));
                    }
                }
                self.log(&format!("\n[retry {attempt}/{of}] {reason}"));
            }
            Ev::TaskUpdate { task } => {
                if let Some(id) = task.get("task").and_then(|x| x.as_str()).map(String::from) {
                    let state = task.get("state").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    let cmd = task.get("command").and_then(|x| x.as_str()).unwrap_or("").to_string();
                    let adopted = if task.get("adopted").and_then(|x| x.as_bool()).unwrap_or(false) { " (adopted from the previous agent)" } else { "" };
                    self.log(&format!("\n[task {id}] {state} {cmd}{adopted}"));
                    self.tasks.insert(id, task);
                }
            }
            Ev::HistoryResult { id, thread, turns } => {
                self.pending.remove(&id);
                self.reload_from_history(&thread, &turns);
            }
            Ev::SessionList { id, sessions } => {
                self.pending.remove(&id);
                self.agent_threads = sessions;
            }
            Ev::SkillList { id, skills } => {
                self.pending.remove(&id);
                self.picked_skills.retain(|p| skills.iter().any(|s| s.get("id").and_then(|x| x.as_str()) == Some(p)));
                self.skills = skills;
            }
            Ev::TaskList { id, tasks } => {
                self.pending.remove(&id);
                self.tasks = tasks.into_iter().filter_map(|t| Some((t.get("task")?.as_str()?.to_string(), t))).collect();
            }
            Ev::McpStatus { id, config, servers } => {
                self.pending.remove(&id);
                self.mcp = Some((config, servers));
            }
            Ev::RulesResult { id, thread, rules } => {
                self.pending.remove(&id);
                self.rules = (thread, rules);
            }
            Ev::Exited(why) => {
                self.agent_ready = false;
                self.notice = why;
                let sids: Vec<u32> = self.runs.values().copied().collect();
                for sid in sids {
                    // the agent never stores a reply cut off by its death, so this text is not in the thread
                    self.fail_bubble(sid, &format!("{UNSAVED} ({})", self.notice));
                    self.finish_sid(sid);
                    if !self.reload.contains(&sid) {
                        self.reload.push(sid);
                    }
                }
            }
            Ev::Token { turn, text } => {
                if let Some(sid) = self.run_session(&turn) {
                    if let Some(m) = self.last_assistant(sid) {
                        m.actions.retain(|a| a != "Thinking…");
                        m.text.push_str(&text);
                    }
                    self.journal(sid, &text);
                    self.touch(sid, false);
                }
            }
            Ev::ToolStart { turn, tool, args, .. } => {
                if let Some(sid) = self.run_session(&turn) {
                    if let Some(m) = self.last_assistant(sid) {
                        m.actions.retain(|a| a != "Thinking…");
                        m.actions.push(format!("▸ {tool}  {}", short(&args)));
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
                self.log(&format!("\n$ {tool} {}", short(&args)));
            }
            Ev::ToolResult { turn, tool, is_error, output } => {
                if let Some(sid) = self.run_session(&turn) {
                    let first = output.lines().next().unwrap_or("").chars().take(100).collect::<String>();
                    if let Some(m) = self.last_assistant(sid) {
                        m.actions.push(format!("{} {tool}: {first}", if is_error { "×" } else { "✓" }));
                    }
                }
                if tool == "invoke_subagent" {
                    let sub_id = format!("sub-{}", self.turn_counter);
                    if let Some(sub) = self.agents.iter_mut().find(|a| a.id == sub_id) {
                        sub.status = if is_error { "FAILED".into() } else { "COMPLETED".into() };
                    }
                }
                self.log(&format!("\n{}{output}", if is_error { "[error] " } else { "" }));
            }
            Ev::Diff { turn, path, diff } => {
                if let Some(sid) = self.run_session(&turn) {
                    if let Some(s) = self.session_mut(sid) {
                        s.diffs.push(DiffItem { path, diff });
                    }
                    // only from the passive tabs: a tab the user opened to work in (Rules, Files, ...) is never yanked away
                    if self.sessions[self.active].id == sid && !self.tab_pinned && matches!(self.drawer_tab, "screen" | "term" | "agent" | "diff") {
                        self.drawer_tab = "diff";
                    }
                }
            }
            Ev::Usage { prompt, completion, cost, .. } => {
                self.prompt_tokens += prompt;
                self.completion_tokens += completion;
                self.tokens += prompt + completion;
                self.last_prompt = prompt;
                self.cost += cost;
                self.save();
            }
            Ev::AutoDecision { turn, tool, allow, reason } => {
                if let Some(sid) = self.run_session(&turn) {
                    if let Some(msg) = self.last_assistant(sid) {
                        msg.actions.push(format!("{} {tool} {} ({reason})", if allow { "·" } else { "–" }, if allow { "auto-allowed" } else { "skipped" }));
                    }
                }
            }
            Ev::Approval { turn, id, tool, args } => {
                if let Some(sid) = self.run_session(&turn) {
                    self.approvals.push(Approval { sid, turn, id, tool, args });
                }
            }
            Ev::Done { turn, text, ok, cancelled } => {
                if let Some(sid) = self.run_session(&turn) {
                    if let Some(m) = self.last_assistant(sid) {
                        m.actions.retain(|a| a != "Thinking…");
                        if cancelled {
                            m.actions.push("■ turn cancelled".into());
                        } else if !text.is_empty() {
                            m.text = text.clone();
                        } else if !ok && m.text.is_empty() {
                            m.text = "Turn failed.".into();
                        }
                    }
                    self.finish_sid(sid);
                }
            }
            Ev::Error { code, message, turn, id } => {
                if let Some(id) = &id {
                    if let Some(kind) = self.pending.remove(id) {
                        self.action_failed(kind, &message);
                        return;
                    }
                }
                self.log(&format!("\n[{code}] {message}"));
                if let Some(sid) = turn.as_deref().and_then(|t| self.run_session(t)) {
                    self.fail_bubble(sid, &message);
                    if code == "bad_request" || code == "busy" {
                        self.finish_sid(sid);
                    }
                    if code == "busy" && message.contains("agent") {
                        // one turn per thread across agents: another Hydra window holds this thread
                        self.notice = format!("{HELD_ELSEWHERE} {message}");
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
                self.finish_sid(sid);
            }
            Pending::Rewind(..) | Pending::UndoRewind(_) => self.notice = format!("Rewind failed: {message}"),
            Pending::Query => self.notice = format!("Agent request failed: {message}"),
            // another live agent owns it (c28b966): only that agent may stop it
            Pending::TaskStop => self.notice = format!("Could not stop the task: {message}"),
            _ => self.log(&format!("\n[error] {message}")),
        }
    }

    fn action_done(&mut self, kind: Pending, r: Value) {
        let is_err = r.get("isError").and_then(|x| x.as_bool()).unwrap_or(false);
        let err_text = r.get("error").and_then(|x| x.as_str()).unwrap_or("action failed").to_string();
        match kind {
            Pending::Rewind(..) | Pending::UndoRewind(_) | Pending::Rules | Pending::Query | Pending::TaskStop => {}
            Pending::Terminal(sid) => {
                if is_err {
                    return self.action_failed(Pending::Terminal(sid), &err_text);
                }
                let res = &r["result"];
                let out = res["stdout"].as_str().unwrap_or("");
                let err = res["stderr"].as_str().unwrap_or("");
                let status = res["status"].as_str().unwrap_or("");
                let code = res["exit_code"].as_i64().unwrap_or(0);
                self.log(&format!("\n{out}{err}"));
                let mut body = format!("{out}{err}").trim_end().to_string();
                if let Some(v) = res["violation"].as_str().filter(|v| !v.is_empty()) {
                    body = format!("Blocked: {v}");
                    self.log(&format!("\n[BLOCKED] {v}"));
                }
                if let Some(d) = res["diff"].as_str().filter(|d| !d.is_empty()) {
                    if let Some(sess) = self.session_mut(sid) {
                        sess.diffs.push(DiffItem { path: "sandbox".into(), diff: d.into() });
                    }
                }
                if let Some(m) = self.last_assistant(sid) {
                    m.actions.retain(|a| a != "Thinking…");
                    m.text = if body.is_empty() { format!("(no output) exit {code}") } else { body };
                    m.actions.push(format!("{status} exit {code}"));
                }
                self.finish_sid(sid);
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
                    synth.actions.retain(|a| a != "Thinking…");
                    synth.header = "SWARM SYNTHESIS".into();
                    synth.text = r["synthesis"].as_str().filter(|t| !t.is_empty()).map(String::from).unwrap_or_else(|| {
                        format!("Error: {}", r["synthesis_error"].as_str().unwrap_or("no synthesis returned"))
                    });
                    s.messages.push(synth);
                }
                self.finish_sid(sid);
            }
            Pending::Screen => {
                if is_err {
                    self.log(&format!("\n[error] capture: {err_text}"));
                } else if let Some(b) = r["png_base64"].as_str().filter(|b| !b.is_empty()) {
                    self.screen_b64 = b.into();
                    self.screen_seq += 1;
                    if r["simulated"].as_bool().unwrap_or(false) {
                        self.log("\n[note] screen capture is simulated on this platform");
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
                    self.log(&format!("\n[error] {err_text}"));
                }
            }
            Pending::AgentStart => {
                if is_err {
                    self.log(&format!("\n[error] agent: {err_text}"));
                } else {
                    self.agent_running = true;
                    self.log("\n[AGENT] task started");
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
                    let max_steps = if state == "IDLE" { 30 } else { st["max_steps"].as_u64().unwrap_or(30) as u32 };
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
            Pending::AgentCtl(which) => self.log(&format!("\n[AGENT] {which}: {}", if is_err { err_text } else { "ok".into() })),
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
                            Ok(_) => {
                                self.log(&format!("\n[EXPORT] wrote {}", p.display()));
                                self.notice = format!("Exported to {}", p.display());
                            }
                            Err(e) => self.log(&format!("\n[EXPORT] failed: {e}")),
                        }
                    }
                    None => self.log("\n[EXPORT] no export directory"),
                }
            }
            Pending::Templates => self.templates = r["templates"].as_array().cloned().unwrap_or_default(),
            Pending::JobsList => self.jobs = r["jobs"].as_array().cloned().unwrap_or_default(),
            Pending::JobCreate | Pending::JobOp => {
                if is_err {
                    self.log(&format!("\n[error] jobs: {err_text}"));
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
        m.runs.insert("t1".into(), m.sessions[0].id);
        m.sessions[0].messages.push(Msg { user: false, header: "X".into(), text: String::new(), actions: vec!["Thinking…".into()] });
        m.apply(Ev::Token { turn: "t1".into(), text: "ab".into() });
        m.apply(Ev::Token { turn: "t1".into(), text: "cd".into() });
        assert_eq!(m.sessions[0].messages.last().unwrap().text, "abcd");
        m.apply(Ev::Usage { model: "x".into(), prompt: 900, completion: 100, cost: 0.0312 });
        assert_eq!(m.tally(), ("1,000".to_string(), "$0.0312".to_string()));
        m.apply(Ev::Done { turn: "t1".into(), text: "abcd!".into(), ok: true, cancelled: false });
        assert!(!m.any_busy());
        assert_eq!(m.sessions[0].messages.last().unwrap().text, "abcd!");
    }

    #[test]
    fn streamed_text_survives_a_kill_through_the_journal_and_the_reply_is_marked_interrupted() {
        let dir = std::env::temp_dir().join(format!("hydra-journal-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("state.json");
        let mut m = Model::new(Some(path.clone()), "/tmp".into());
        running_thread(&mut m, 0, "t1");
        m.save();
        for t in ["al", "pha ", "beta"] {
            m.apply(Ev::Token { turn: "t1".into(), text: t.into() });
        }
        // the kill: no save after the tokens, and a torn half line at the end of the journal
        let j = path.with_extension("journal");
        let mut torn = std::fs::read_to_string(&j).unwrap();
        torn.push_str("{\"s\": 1, \"t\": \"gam");
        std::fs::write(&j, torn).unwrap();
        drop(m);
        let back = Model::new(Some(path.clone()), "/tmp".into());
        let last = back.sessions[0].messages.last().unwrap();
        assert_eq!(last.text, "alpha beta");
        assert!(last.actions.iter().any(|a| a.contains("interrupted")) && !last.actions.iter().any(|a| a == "Thinking…"), "{:?}", last.actions);
        assert!(!j.exists(), "replayed journal folded into state.json");
        let again = Model::new(Some(path.clone()), "/tmp".into());
        assert_eq!(again.sessions[0].messages.last().unwrap().text, "alpha beta", "not replayed twice");
        // a torn state.json comes back from .bak and is rewritten whole
        let good = std::fs::read_to_string(&path).unwrap();
        std::fs::write(path.with_extension("bak"), &good).unwrap();
        std::fs::write(&path, &good[..good.len() / 2]).unwrap();
        let rec = Model::new(Some(path.clone()), "/tmp".into());
        assert_eq!(rec.sessions[0].messages.last().unwrap().text, "alpha beta");
        assert!(serde_json::from_str::<Value>(&std::fs::read_to_string(&path).unwrap()).is_ok(), "state.json rewritten after recovery");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_background_thread_finishing_raises_a_toast_and_the_meter_reads_usage() {
        let mut m = Model::new(None, "/tmp".into());
        m.new_session();
        m.sessions[1].name = "Build".into();
        running_thread(&mut m, 1, "t9");
        m.select(0);
        m.apply(Ev::Usage { model: "x".into(), prompt: 12_345, completion: 10, cost: 0.0 });
        assert_eq!(m.last_prompt, 12_345);
        m.apply(Ev::Done { turn: "t9".into(), text: "ok".into(), ok: true, cancelled: false });
        assert_eq!(m.notice, "\u{201c}Build\u{201d} finished");
        assert!(m.sessions[1].unread);
        let hits = m.search("OK", 10);
        assert_eq!((hits.len(), hits[0].session), (1, 1));
    }

    #[test]
    fn errors_name_the_picked_model_and_give_a_next_step() {
        let e = explain_error("Offline Local (Ollama)", "ProviderError: <urlopen error unknown url type: '<redacted>/v1/chat'>");
        assert!(e.starts_with("Offline Local (Ollama): "), "{e}");
        assert!(e.contains("Settings (Ctrl+,)") && e.contains("not valid"), "{e}");
        assert!(explain_error("X", "Connection refused").contains("local server running"));
        assert!(explain_error("X", "no credentials for openrouter").contains("add a key"));
        assert_eq!(explain_error("X", "X broke"), "X broke");
    }

    fn running_thread(m: &mut Model, idx: usize, turn: &str) {
        let sid = m.sessions[idx].id;
        m.sessions[idx].messages.push(Msg { user: false, header: "X".into(), text: String::new(), actions: vec!["Thinking…".into()] });
        m.runs.insert(turn.into(), sid);
    }

    #[test]
    fn overlapping_turns_never_cross_talk() {
        let mut m = Model::new(None, ".".into());
        m.new_session();
        running_thread(&mut m, 0, "t1");
        running_thread(&mut m, 1, "t2");
        for i in 0..50 {
            m.apply(Ev::Token { turn: "t1".into(), text: "a".into() });
            m.apply(Ev::Token { turn: "t2".into(), text: format!("{}", i % 10) });
        }
        m.select(0); // the user is looking at the other thread while t2 reports a diff
        m.apply(Ev::Diff { turn: "t2".into(), path: "f.txt".into(), diff: "+x".into() });
        m.apply(Ev::Approval { turn: "t1".into(), id: "a1".into(), tool: "bash".into(), args: Value::Null });
        assert_eq!(m.sessions[0].messages.last().unwrap().text, "a".repeat(50));
        assert!(!m.sessions[1].messages.last().unwrap().text.contains('a'));
        assert_eq!(m.sessions[0].diffs.len(), 0);
        assert_eq!(m.sessions[1].diffs.len(), 1);
        assert_eq!(m.status(0), Status::NeedsApproval);
        assert_eq!(m.status(1), Status::Running);
        m.apply(Ev::Done { turn: "t2".into(), text: "".into(), ok: true, cancelled: false });
        assert_eq!(m.status(1), Status::Unread, "finished in the background while the user looked elsewhere");
        assert!(m.is_running(m.sessions[0].id));
        m.apply(Ev::Done { turn: "t1".into(), text: "".into(), ok: false, cancelled: true });
        assert!(!m.any_busy());
        assert!(m.approvals.is_empty());
    }

    #[test]
    fn background_finish_marks_unread_and_selecting_clears_it() {
        let mut m = Model::new(None, ".".into());
        m.new_session();
        running_thread(&mut m, 0, "t1");
        m.apply(Ev::Done { turn: "t1".into(), text: "done".into(), ok: true, cancelled: false });
        assert_eq!(m.status(0), Status::Unread);
        m.select(0);
        assert_eq!(m.status(0), Status::Idle);
    }

    #[test]
    fn projects_scope_threads_and_dedupe() {
        let dir = std::env::temp_dir().join(format!("hydra-proj-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mut m = Model::new(None, ".".into());
        let first = m.active_project;
        let id = m.open_project(dir.to_str().unwrap()).unwrap();
        assert_ne!(id, first);
        assert_eq!(m.visible().len(), 1);
        let exp = std::fs::canonicalize(&dir).unwrap().display().to_string();
        let exp = exp.strip_prefix(r"\\?\").unwrap_or(&exp).to_string();
        assert_eq!(m.project_path(), exp);
        assert_eq!(m.open_project(dir.to_str().unwrap()).unwrap(), id, "same folder is the same project");
        assert_eq!(m.projects.len(), 2);
        assert!(m.open_project("/definitely/not/here").is_err());
        m.new_session();
        assert_eq!(m.visible().len(), 2);
        m.set_project(first);
        assert!(m.visible().iter().all(|&i| m.sessions[i].project == first));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn closing_keeps_one_thread_and_never_closes_a_running_one() {
        let mut m = Model::new(None, ".".into());
        m.close_session(0);
        assert_eq!(m.visible().len(), 1, "last thread stays");
        m.new_session();
        running_thread(&mut m, 0, "t1");
        m.close_session(0);
        assert!(!m.sessions[0].archived, "running thread is not closed");
        m.close_session(1);
        assert!(m.sessions[1].archived);
        assert_eq!(m.active, 0);
    }

    #[test]
    fn projects_and_thread_ownership_persist() {
        let dir = std::env::temp_dir().join(format!("hydra-persist-{}", std::process::id()));
        let proj = dir.join("repo");
        std::fs::create_dir_all(&proj).unwrap();
        let path = dir.join("state.json");
        let mut a = Model::new(Some(path.clone()), ".".into());
        a.open_project(proj.to_str().unwrap()).unwrap();
        a.sessions[a.active].name = "feature thread".into();
        a.save();
        let b = Model::new(Some(path), ".".into());
        assert_eq!(b.projects.len(), 2);
        assert_eq!(b.active_project, a.active_project);
        assert_eq!(b.sessions[b.active].name, "feature thread");
        assert_eq!(b.project_path(), a.project_path());
        let _ = std::fs::remove_dir_all(dir);
    }
}
