//! The Hydra agent as a child process. Protocol: docs/STDIO_PROTOCOL.md, one JSON object per line over pipes.

pub mod update;

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone)]
pub enum Ev {
    Hello,
    Token { turn: String, text: String },
    ToolStart { turn: String, call: u64, tool: String, args: Value },
    ToolResult { turn: String, tool: String, is_error: bool, output: String },
    Diff { turn: String, path: String, diff: String },
    Usage { model: String, prompt: u64, completion: u64, cost: f64 },
    Approval { turn: String, id: String, tool: String, args: Value },
    Done { turn: String, text: String, ok: bool, cancelled: bool },
    Error { code: String, message: String, turn: Option<String>, id: Option<String> },
    ActionResult { id: String, action: String, result: Value },
    Checkpoint { turn: String, ok: bool, id: Option<String>, reason: String },
    CheckpointList { id: String, thread: String, checkpoints: Vec<Value> },
    RewindResult { id: String, backup: String, restored: Vec<String>, removed: Vec<String>, conversation: bool, turns: Option<u64> },
    RulesResult { id: String, thread: String, rules: Vec<Value> },
    Context { turn: String, rules: Option<String>, skills: Vec<String>, mcp_tools: Vec<String>, mcp_errors: Value },
    Retry { turn: String, attempt: u64, of: u64, wait: f64, reason: String },
    AutoDecision { turn: String, tool: String, allow: bool, reason: String },
    SkillList { id: String, skills: Vec<Value> },
    McpStatus { id: String, config: Option<String>, servers: Vec<Value> },
    TaskList { id: String, tasks: Vec<Value> },
    TaskUpdate { task: Value },
    SessionList { id: String, sessions: Vec<Value> },
    HistoryResult { id: String, thread: String, turns: Vec<Value> },
    Exited(String),
}

pub fn parse_line(line: &str) -> Option<Ev> {
    let v: Value = serde_json::from_str(line).ok()?;
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    let b = |k: &str| v.get(k).and_then(|x| x.as_bool()).unwrap_or(false);
    let n = |k: &str| v.get(k).and_then(|x| x.as_u64()).unwrap_or(0);
    let arr = |k: &str| v.get(k).and_then(|x| x.as_array()).cloned().unwrap_or_default();
    let str_arr = |k: &str| -> Vec<String> {
        v.get(k)
            .and_then(|x| x.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default()
    };
    Some(match v.get("type")?.as_str()? {
        "hello" => Ev::Hello,
        "token" => Ev::Token { turn: s("turn"), text: s("text") },
        "tool_start" => Ev::ToolStart { turn: s("turn"), call: n("call"), tool: s("tool"), args: v.get("arguments").cloned().unwrap_or(Value::Null) },
        "tool_result" => Ev::ToolResult { turn: s("turn"), tool: s("tool"), is_error: b("is_error"), output: s("output") },
        "diff" => Ev::Diff { turn: s("turn"), path: s("path"), diff: s("diff") },
        "usage" => Ev::Usage {
            model: s("model"),
            prompt: n("prompt_tokens"),
            completion: n("completion_tokens"),
            cost: v.get("cost_usd").and_then(|x| x.as_f64()).unwrap_or(0.0),
        },
        "approval_request" => Ev::Approval { turn: s("turn"), id: s("id"), tool: s("tool"), args: v.get("arguments").cloned().unwrap_or(Value::Null) },
        "done" => Ev::Done { turn: s("turn"), text: s("text"), ok: b("ok"), cancelled: b("cancelled") },
        "error" => Ev::Error {
            code: s("code"),
            message: s("message"),
            turn: v.get("turn").and_then(|x| x.as_str()).map(String::from),
            id: v.get("id").and_then(|x| x.as_str()).map(String::from),
        },
        "action_result" => Ev::ActionResult { id: s("id"), action: s("action"), result: v.get("result").cloned().unwrap_or(Value::Null) },
        "checkpoint" => Ev::Checkpoint {
            turn: s("turn"),
            ok: b("ok"),
            id: v.get("id").and_then(|x| x.as_str()).map(String::from),
            reason: s("reason"),
        },
        "checkpoint_list" => Ev::CheckpointList {
            id: s("id"),
            thread: s("thread"),
            checkpoints: arr("checkpoints"),
        },
        "rewind_result" => Ev::RewindResult {
            id: s("id"),
            backup: s("backup"),
            restored: str_arr("restored"),
            removed: str_arr("removed"),
            conversation: b("conversation"),
            turns: v.get("turns").and_then(|x| x.as_u64()),
        },
        "rules_result" => Ev::RulesResult {
            id: s("id"),
            thread: s("thread"),
            rules: arr("rules"),
        },
        "context" => Ev::Context {
            turn: s("turn"),
            rules: v.get("rules").and_then(|x| x.as_str()).map(String::from),
            skills: str_arr("skills"),
            mcp_tools: str_arr("mcp_tools"),
            mcp_errors: v.get("mcp_errors").cloned().unwrap_or(Value::Null),
        },
        "retry" => Ev::Retry {
            turn: s("turn"),
            attempt: n("attempt"),
            of: v.get("of").or_else(|| v.get("max_attempts")).and_then(|x| x.as_u64()).unwrap_or(0),
            wait: v.get("wait").or_else(|| v.get("delay")).and_then(|x| x.as_f64()).unwrap_or(0.0),
            reason: s("reason"),
        },
        "auto_decision" => Ev::AutoDecision {
            turn: s("turn"),
            tool: s("tool"),
            allow: v.get("allow").and_then(|x| x.as_bool()).unwrap_or_else(|| s("decision") == "allow"),
            reason: s("reason"),
        },
        "skill_list" => Ev::SkillList {
            id: s("id"),
            skills: arr("skills"),
        },
        "mcp_status" => Ev::McpStatus {
            id: s("id"),
            config: v.get("config").and_then(|x| x.as_str()).map(String::from),
            servers: arr("servers"),
        },
        "task_list" => Ev::TaskList {
            id: s("id"),
            tasks: arr("tasks"),
        },
        "task_update" => Ev::TaskUpdate {
            task: v.clone(),
        },
        "session_list" => Ev::SessionList {
            id: s("id"),
            sessions: arr("sessions"),
        },
        "history_result" => Ev::HistoryResult {
            id: s("id"),
            thread: s("thread"),
            turns: arr("turns"),
        },
        _ => return None,
    })
}

pub struct Backend {
    child: Child,
    stdin: Arc<Mutex<ChildStdin>>,
    pub rx: Receiver<Ev>,
    next: u64,
}

fn candidate_repo_dirs() -> Vec<std::path::PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors().skip(1).take(8) {
            dirs.push(dir.to_path_buf());
        }
        if let Some(parent) = exe.parent() {
            dirs.push(parent.join("repo"));
            dirs.push(parent.join("app"));
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        for dir in cwd.ancestors().take(8) {
            dirs.push(dir.to_path_buf());
        }
        dirs.push(cwd.join("hydra"));
    }
    for var in ["USERPROFILE", "HOME"] {
        if let Ok(home) = std::env::var(var) {
            let hp = std::path::PathBuf::from(home);
            dirs.push(hp.join("Documents").join("hydra"));
            dirs.push(hp.join("documents").join("hydra"));
            dirs.push(hp.join("hydra"));
            dirs.push(hp.join("Hydra").join("repo"));
        }
    }
    dirs
}

/// Folder that holds Hydra.exe (or HYDRA_BUNDLE_DIR for tests). A release folder carries `runtime/` and `app/` beside it.
pub fn bundle_dir() -> Option<std::path::PathBuf> {
    if let Ok(d) = std::env::var("HYDRA_BUNDLE_DIR") {
        if !d.trim().is_empty() {
            return Some(d.into());
        }
    }
    std::env::current_exe().ok().and_then(|e| e.parent().map(|p| p.to_path_buf()))
}

/// Embedded interpreter path inside a release folder, if present.
pub fn bundled_python(dir: &std::path::Path) -> Option<std::path::PathBuf> {
    let names: &[&str] = if cfg!(windows) { &["python.exe"] } else { &["python3", "python"] };
    let rt = dir.join("runtime");
    names.iter().map(|n| rt.join(n)).find(|p| p.is_file())
}

/// The checkout that holds `hydra_cli`: HYDRA_REPO, the release folder's `app/`, else a git checkout with `hydra_cli`, else a parent/bundle folder with `hydra_cli`.
pub fn repo_dir() -> Option<String> {
    if let Ok(r) = std::env::var("HYDRA_REPO") {
        if !r.trim().is_empty() {
            return Some(r);
        }
    }
    if let Some(b) = bundle_dir() {
        let app = b.join("app");
        if app.join("hydra_cli").join("__init__.py").is_file() {
            return Some(app.display().to_string());
        }
    }
    let cands = candidate_repo_dirs();
    for dir in &cands {
        if dir.join(".git").exists() && dir.join("hydra_cli").join("__init__.py").is_file() {
            return Some(dir.display().to_string());
        }
    }
    for dir in &cands {
        if dir.join("hydra_cli").join("__init__.py").is_file() {
            return Some(dir.display().to_string());
        }
    }
    None
}


/// Interpreters to try, in order, as (program, leading args). The bundled runtime wins; system python is a dev fallback.
pub fn python_candidates() -> Vec<(String, Vec<String>)> {
    if let Ok(p) = std::env::var("HYDRA_PYTHON") {
        if !p.trim().is_empty() {
            return vec![(p, vec![])];
        }
    }
    if let Some(p) = bundle_dir().and_then(|d| bundled_python(&d)) {
        return vec![(p.display().to_string(), vec![])];
    }
    if cfg!(windows) {
        vec![("python".into(), vec![]), ("py".into(), vec!["-3".into()])]
    } else {
        vec![("python3".into(), vec![]), ("python".into(), vec![])]
    }
}

impl Backend {
    /// Spawn `python -m hydra_cli stdio`. `repaint` is called after every event so the UI wakes up.
    pub fn spawn(repaint: impl Fn() + Send + 'static) -> Result<Backend, String> {
        Self::spawn_with(&[], repaint)
    }

    /// Same as `spawn` with extra environment for the child.
    pub fn spawn_with(envs: &[(String, String)], repaint: impl Fn() + Send + 'static) -> Result<Backend, String> {
        Self::spawn_using(python_candidates(), envs, repaint)
    }

    /// Spawn with an explicit interpreter list, as (program, leading args), tried in order.
    pub fn spawn_using(candidates: Vec<(String, Vec<String>)>, envs: &[(String, String)], repaint: impl Fn() + Send + 'static) -> Result<Backend, String> {
        let repo = repo_dir();
        let mut spawned: Option<Child> = None;
        let mut last_err = String::from("no python interpreter found");
        for (py, lead) in candidates {
            let mut cmd = Command::new(&py);
            cmd.args(&lead).args(["-m", "hydra_cli", "stdio"]).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
            if let Some(r) = &repo {
                cmd.current_dir(r);
                let sep = if cfg!(windows) { ";" } else { ":" };
                let pp = match std::env::var("PYTHONPATH") {
                    Ok(old) if !old.is_empty() => format!("{r}{sep}{old}"),
                    _ => r.clone(),
                };
                cmd.env("PYTHONPATH", pp);
            }
            cmd.env("PYTHONIOENCODING", "utf-8");
            for (k, v) in envs {
                cmd.env(k, v);
            }
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
            }
            match cmd.spawn() {
                Ok(c) => {
                    spawned = Some(c);
                    break;
                }
                Err(e) => last_err = format!("could not start {py}: {e}"),
            }
        }
        let mut child = spawned.ok_or(last_err)?;
        let stdin = Arc::new(Mutex::new(child.stdin.take().ok_or("no stdin")?));
        let stdout = child.stdout.take().ok_or("no stdout")?;
        let (tx, rx): (Sender<Ev>, Receiver<Ev>) = channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                match line {
                    Ok(l) => {
                        if let Some(ev) = parse_line(&l) {
                            if tx.send(ev).is_err() {
                                return;
                            }
                            repaint();
                        }
                    }
                    Err(e) => {
                        let _ = tx.send(Ev::Exited(e.to_string()));
                        repaint();
                        return;
                    }
                }
            }
            let _ = tx.send(Ev::Exited("agent process ended".into()));
            repaint();
        });
        let mut be = Backend { child, stdin, rx, next: 0 };
        be.send(json!({"type": "hello", "protocol": 1}));
        Ok(be)
    }

    pub fn send(&mut self, v: Value) {
        if let Ok(mut w) = self.stdin.lock() {
            let _ = writeln!(w, "{v}");
            let _ = w.flush();
        }
    }

    pub fn turn(&mut self, turn: &str, prompt: &str, thread: &str, cwd: &str, model: &str, approve: bool) {
        self.send(json!({"type": "turn", "turn": turn, "prompt": prompt, "thread": thread, "cwd": cwd, "model": model, "approve": approve}));
    }

    /// Turn with an explicit context mode and character budget. `mode` is compact, sliding, or recall.
    pub fn turn_mode(&mut self, turn: &str, prompt: &str, thread: &str, cwd: &str, model: &str, approve: bool, mode: &str, context_chars: u64) {
        self.send(json!({
            "type": "turn",
            "turn": turn,
            "prompt": prompt,
            "thread": thread,
            "cwd": cwd,
            "model": model,
            "approve": approve,
            "mode": mode,
            "context_chars": context_chars.max(4096),
        }));
    }

    pub fn cancel(&mut self, turn: &str) {
        self.send(json!({"type": "cancel", "turn": turn}));
    }

    pub fn approve(&mut self, id: &str, decision: &str) {
        self.send(json!({"type": "approval", "id": id, "decision": decision}));
    }

    pub fn approve_always(&mut self, id: &str, scope: &str, prefix: Option<&str>) {
        let mut msg = json!({"type": "approval", "id": id, "decision": "allow_always", "scope": scope});
        if let Some(p) = prefix {
            if let Some(obj) = msg.as_object_mut() {
                obj.insert("prefix".into(), json!(p));
            }
        }
        self.send(msg);
    }

    pub fn turn_with_options(
        &mut self,
        turn: &str,
        prompt: &str,
        thread: &str,
        cwd: &str,
        model: &str,
        permission: &str,
        mode: Option<&str>,
        skills: &[String],
    ) {
        let mut msg = json!({
            "type": "turn",
            "turn": turn,
            "prompt": prompt,
            "thread": thread,
            "cwd": cwd,
            "model": model,
            "permission": permission,
            "skills": skills,
        });
        if let Some(m) = mode {
            if let Some(obj) = msg.as_object_mut() {
                obj.insert("mode".into(), json!(m));
            }
        }
        self.send(msg);
    }

    fn next_req_id(&mut self) -> String {
        self.next += 1;
        format!("r{}", self.next)
    }

    /// Fire an `action`; the reply comes back as Ev::ActionResult with the returned id.
    pub fn action(&mut self, name: &str, params: Value) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "action", "id": id, "action": name, "params": params}));
        id
    }

    pub fn rules(&mut self, thread: &str, remove: Option<&str>) -> String {
        let id = self.next_req_id();
        if let Some(rule_id) = remove {
            self.send(json!({"type": "rules", "id": id, "thread": thread, "op": "remove", "rule": rule_id}));
        } else {
            self.send(json!({"type": "rules", "id": id, "thread": thread, "op": "list"}));
        }
        id
    }

    pub fn checkpoints(&mut self, thread: &str) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "checkpoints", "id": id, "thread": thread}));
        id
    }

    pub fn rewind(&mut self, thread: &str, checkpoint: &str, conversation: bool) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "rewind", "id": id, "thread": thread, "checkpoint": checkpoint, "conversation": conversation}));
        id
    }

    pub fn skills(&mut self) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "skills", "id": id}));
        id
    }

    pub fn mcp(&mut self, cwd: &str, refresh: bool) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "mcp", "id": id, "cwd": cwd, "refresh": refresh}));
        id
    }

    pub fn mcp_toggle(&mut self, cwd: &str, server: &str, enabled: bool) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "mcp_toggle", "id": id, "cwd": cwd, "server": server, "enabled": enabled}));
        id
    }

    pub fn tasks(&mut self) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "tasks", "id": id}));
        id
    }

    pub fn task_stop(&mut self, task: &str) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "task_stop", "id": id, "task": task}));
        id
    }

    pub fn sessions(&mut self) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "sessions", "id": id}));
        id
    }

    pub fn history(&mut self, thread: &str, limit: u64) -> String {
        let id = self.next_req_id();
        self.send(json!({"type": "history", "id": id, "thread": thread, "limit": limit}));
        id
    }

    /// Why the agent child ended, or None while it runs. Polls without blocking.
    pub fn exited(&mut self) -> Option<String> {
        match self.child.try_wait() {
            Ok(Some(status)) => Some(exit_reason(status)),
            Ok(None) => None,
            Err(e) => Some(format!("the agent process could not be checked: {e}")),
        }
    }

    /// Process id of the agent child.
    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    pub fn shutdown(&mut self) {
        self.send(json!({"type": "shutdown"}));
        for _ in 0..50 {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        let _ = self.child.kill();
    }
}

/// Plain words for how the agent process ended.
pub fn exit_reason(status: std::process::ExitStatus) -> String {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(sig) = status.signal() {
            return format!("killed by signal {sig}");
        }
    }
    match status.code() {
        Some(0) => "exited".into(),
        Some(c) => format!("exited with code {c}"),
        None => "agent process ended".into(),
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        if !matches!(self.child.try_wait(), Ok(Some(_))) {
            self.shutdown();
        }
    }
}

#[cfg(test)]
mod proto_tests {
    use super::*;

    #[test]
    fn parse_extended_protocol_events() {
        let cp = parse_line(r#"{"type":"checkpoint","turn":"t1","ok":true,"id":"abc123","reason":""}"#).unwrap();
        assert!(matches!(cp, Ev::Checkpoint { ok: true, id: Some(ref id), .. } if id == "abc123"));

        let cpl = parse_line(r#"{"type":"checkpoint_list","id":"r1","thread":"main","checkpoints":[{"id":"abc"}]}"#).unwrap();
        assert!(matches!(cpl, Ev::CheckpointList { ref checkpoints, .. } if checkpoints.len() == 1));

        let rw = parse_line(r#"{"type":"rewind_result","id":"r2","backup":"bak","restored":["a.rs"],"removed":["b.rs"],"conversation":true,"turns":4}"#).unwrap();
        assert!(matches!(rw, Ev::RewindResult { conversation: true, turns: Some(4), .. }));

        let auto = parse_line(r#"{"type":"auto_decision","turn":"t1","tool":"read_file","decision":"allow","reason":"accept_edits mode"}"#).unwrap();
        assert!(matches!(auto, Ev::AutoDecision { allow: true, ref tool, .. } if tool == "read_file"));

        let retry = parse_line(r#"{"type":"retry","turn":"t1","attempt":1,"of":3,"wait":1.5,"reason":"429"}"#).unwrap();
        assert!(matches!(retry, Ev::Retry { attempt: 1, of: 3, .. }));
    }

    #[test]
    fn bundled_python_is_preferred_when_present() {
        let dir = std::env::temp_dir().join(format!("hydra-stdio-bundle-{}", std::process::id()));
        let rt = dir.join("runtime");
        std::fs::create_dir_all(&rt).unwrap();
        assert!(bundled_python(&dir).is_none());
        let name = if cfg!(windows) { "python.exe" } else { "python3" };
        std::fs::write(rt.join(name), b"").unwrap();
        assert_eq!(bundled_python(&dir).unwrap(), rt.join(name));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn exited_reports_exit_code_across_platforms() {
        let cands = if cfg!(windows) {
            vec![("cmd".to_string(), vec!["/C".to_string(), "exit /b 3".to_string()])]
        } else {
            vec![("sh".to_string(), vec!["-c".to_string(), "exit 3".to_string()])]
        };
        let mut be = Backend::spawn_using(cands, &[], || {}).expect("spawn short-lived command");
        assert!(be.pid() > 0);
        let end = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut why = None;
        while why.is_none() && std::time::Instant::now() < end {
            why = be.exited();
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert_eq!(why.as_deref(), Some("exited with code 3"));
    }
}

