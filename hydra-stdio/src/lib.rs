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
    Exited(String),
}

pub fn parse_line(line: &str) -> Option<Ev> {
    let v: Value = serde_json::from_str(line).ok()?;
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    let b = |k: &str| v.get(k).and_then(|x| x.as_bool()).unwrap_or(false);
    let n = |k: &str| v.get(k).and_then(|x| x.as_u64()).unwrap_or(0);
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
        _ => return None,
    })
}

pub struct Backend {
    child: Child,
    stdin: Arc<Mutex<ChildStdin>>,
    pub rx: Receiver<Ev>,
    next: u64,
}

/// The checkout that holds `hydra_cli`: HYDRA_REPO, else a parent of the exe or of the working directory.
pub fn repo_dir() -> Option<String> {
    if let Ok(r) = std::env::var("HYDRA_REPO") {
        return Some(r);
    }
    let mut starts = vec![];
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    for start in starts {
        for dir in start.ancestors().skip(1).take(8) {
            if dir.join("hydra_cli").join("__init__.py").is_file() {
                return Some(dir.display().to_string());
            }
        }
    }
    None
}

/// Interpreters to try, in order, as (program, leading args).
pub fn python_candidates() -> Vec<(String, Vec<String>)> {
    if let Ok(p) = std::env::var("HYDRA_PYTHON") {
        return vec![(p, vec![])];
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
        let repo = repo_dir();
        let mut spawned: Option<Child> = None;
        let mut last_err = String::from("no python interpreter found");
        for (py, lead) in python_candidates() {
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

    /// Fire an `action`; the reply comes back as Ev::ActionResult with the returned id.
    pub fn action(&mut self, name: &str, params: Value) -> String {
        self.next += 1;
        let id = format!("r{}", self.next);
        self.send(json!({"type": "action", "id": id, "action": name, "params": params}));
        id
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

impl Drop for Backend {
    fn drop(&mut self) {
        if !matches!(self.child.try_wait(), Ok(Some(_))) {
            self.shutdown();
        }
    }
}
