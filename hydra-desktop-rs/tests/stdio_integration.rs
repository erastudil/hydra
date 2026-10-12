//! Drives the real `python -m hydra_cli stdio` adapter from the Rust client and model, against a stub model endpoint.
//! Needs python3 with this repo importable (HYDRA_REPO, default ..). Skips with a message when python is missing.

use hydra_desktop::backend::{Backend, Ev};
use hydra_desktop::model::*;
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::{Duration, Instant};

fn last_user(body: &Value) -> String {
    body["messages"].as_array().into_iter().flatten().rev().find(|m| m["role"] == "user").map(|m| m["content"].as_str().unwrap_or("").to_string()).unwrap_or_default()
}

fn handle(mut s: TcpStream) {
    let mut buf = Vec::new();
    let mut tmp = [0u8; 4096];
    let (head_end, len) = loop {
        let n = s.read(&mut tmp).unwrap_or(0);
        if n == 0 {
            return;
        }
        buf.extend_from_slice(&tmp[..n]);
        if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            let head = String::from_utf8_lossy(&buf[..i]).to_lowercase();
            let len = head.lines().find_map(|l| l.strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0))).unwrap_or(0);
            break (i + 4, len);
        }
    };
    while buf.len() < head_end + len {
        let n = s.read(&mut tmp).unwrap_or(0);
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
    }
    let body: Value = serde_json::from_slice(&buf[head_end..head_end + len]).unwrap_or(Value::Null);
    let prompt = last_user(&body);
    let stream = body["stream"].as_bool().unwrap_or(false);
    let has_tool = body["messages"].as_array().into_iter().flatten().any(|m| m["role"] == "tool");
    let usage = json!({"prompt_tokens": 10, "completion_tokens": 3});
    if !stream {
        let data = json!({"choices": [{"message": {"role": "assistant", "content": "alpha beta gamma"}, "finish_reason": "stop"}], "usage": usage}).to_string();
        let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", data.len(), data);
        return;
    }
    let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n");
    let send = |s: &mut TcpStream, v: Value| s.write_all(format!("data: {v}\n\n").as_bytes()).and_then(|_| s.flush());
    if prompt.contains("WRITE") && !has_tool {
        let args = json!({"path": "hello.txt", "content": "hi\n"}).to_string();
        let _ = send(&mut s, json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "write_file", "arguments": ""}}]}}]}));
        let _ = send(&mut s, json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": args}}]}}]}));
    } else {
        let (tokens, pause): (Vec<&str>, u64) = if prompt.contains("SLOW") { (vec!["tick "; 60], 150) } else if has_tool { (vec!["finished"], 0) } else { (vec!["alpha ", "beta ", "gamma"], 20) };
        for t in tokens {
            if send(&mut s, json!({"choices": [{"delta": {"content": t}}]})).is_err() {
                return;
            }
            std::thread::sleep(Duration::from_millis(pause));
        }
        let _ = send(&mut s, json!({"choices": [], "usage": usage}));
    }
    let _ = s.write_all(b"data: [DONE]\n\n");
}

struct Rig {
    m: Model,
    be: Backend,
    dir: std::path::PathBuf,
}

fn rig(name: &str) -> Option<Rig> {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for c in listener.incoming().flatten() {
            std::thread::spawn(move || handle(c));
        }
    });
    let dir = std::env::temp_dir().join(format!("hydra-rs-{name}-{}", std::process::id()));
    let proj = dir.join("proj");
    std::fs::create_dir_all(&proj).unwrap();
    let repo = std::env::var("HYDRA_REPO").unwrap_or_else(|_| format!("{}/..", env!("CARGO_MANIFEST_DIR")));
    let py = std::env::var("HYDRA_PYTHON").unwrap_or_else(|_| "python3".into());
    if std::process::Command::new(&py).arg("--version").output().is_err() {
        eprintln!("skip: no python");
        return None;
    }
    std::env::set_var("HYDRA_PYTHON", &py);
    std::env::set_var("HYDRA_REPO", &repo);
    let envs = vec![
        ("LOCAL_AI_BASE".to_string(), format!("http://127.0.0.1:{port}")),
        ("HOME".to_string(), dir.display().to_string()),
        ("USERPROFILE".to_string(), dir.display().to_string()),
    ];
    let be = Backend::spawn_with(&envs, || {}).expect("spawn adapter");
    let mut m = Model::new(None, proj.display().to_string());
    m.model = MODELS.iter().position(|x| x.0 == "local").unwrap();
    Some(Rig { m, be, dir })
}

fn pump(r: &mut Rig, secs: u64, mut until: impl FnMut(&Model) -> bool) -> Vec<Ev> {
    let end = Instant::now() + Duration::from_secs(secs);
    let mut seen = vec![];
    while Instant::now() < end {
        if let Ok(ev) = r.be.rx.recv_timeout(Duration::from_millis(50)) {
            seen.push(ev.clone());
            r.m.apply(ev);
        }
        if until(&r.m) {
            break;
        }
    }
    seen
}

fn done(r: &mut Rig) -> Vec<Ev> {
    pump(r, 60, |m| !m.any_busy())
}

#[test]
fn chat_streams_tokens_and_tallies_real_usage() {
    let Some(mut r) = rig("chat") else { return };
    pump(&mut r, 20, |m| m.agent_ready);
    assert!(r.m.agent_ready, "adapter said hello");
    assert!(r.m.submit(&mut r.be, "say something"));
    let evs = done(&mut r);
    let tokens = evs.iter().filter(|e| matches!(e, Ev::Token { .. })).count();
    assert!(tokens >= 3, "tokens arrive one by one, got {tokens}");
    let last = r.m.sessions[0].messages.last().unwrap();
    assert_eq!(last.text, "alpha beta gamma");
    assert_eq!(r.m.tokens, 13, "tally comes from the provider usage numbers");
    assert_eq!(r.m.cost, 0.0, "local tier is free");
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn stop_cancels_a_running_turn() {
    let Some(mut r) = rig("stop") else { return };
    assert!(r.m.submit(&mut r.be, "SLOW please"));
    pump(&mut r, 30, |m| m.sessions[0].messages.last().map(|x| !x.text.is_empty()).unwrap_or(false));
    r.m.stop(&mut r.be);
    done(&mut r);
    assert!(!r.m.any_busy());
    let last = r.m.sessions[0].messages.last().unwrap();
    assert!(last.actions.iter().any(|a| a.contains("cancelled")), "{:?}", last);
    assert!(last.text.matches("tick").count() < 60);
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn approval_gates_a_write_and_the_diff_event_lands() {
    let Some(mut r) = rig("approve") else { return };
    r.m.sessions[0].permission = "manual".into();
    assert!(r.m.submit(&mut r.be, "WRITE the file"));
    pump(&mut r, 40, |m| !m.approvals.is_empty());
    let a = r.m.approvals.first().cloned().expect("approval request arrives");
    assert_eq!(a.tool, "write_file");
    assert!(!std::path::Path::new(&r.m.cwd).join("hello.txt").exists(), "nothing written before approval");
    r.m.answer_approval(&mut r.be, "allow");
    done(&mut r);
    assert!(std::path::Path::new(&r.m.cwd).join("hello.txt").exists());
    assert!(r.m.sessions[0].diffs.iter().any(|d| d.path.ends_with("hello.txt") && d.diff.contains("+hi")), "{:?}", r.m.sessions[0].diffs);
    assert!(r.m.term.contains("write_file"), "terminal log shows the tool");
    assert_eq!(r.m.sessions[0].messages.last().unwrap().text, "finished");
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn skipping_an_approval_writes_nothing() {
    let Some(mut r) = rig("skip") else { return };
    r.m.sessions[0].permission = "manual".into();
    assert!(r.m.submit(&mut r.be, "WRITE the file"));
    pump(&mut r, 40, |m| !m.approvals.is_empty());
    r.m.answer_approval(&mut r.be, "skip");
    done(&mut r);
    assert!(!std::path::Path::new(&r.m.cwd).join("hello.txt").exists());
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn terminal_sandbox_runs_a_real_command() {
    let Some(mut r) = rig("term") else { return };
    r.m.set_nav("terminal");
    assert!(r.m.submit(&mut r.be, "echo hydra-native"));
    done(&mut r);
    assert!(r.m.term.contains("hydra-native"), "{}", r.m.term);
    assert!(r.m.sessions[0].messages.last().unwrap().text.contains("hydra-native"));
    r.m.set_nav("terminal");
    assert!(r.m.submit(&mut r.be, "rm -rf /"));
    done(&mut r);
    assert!(r.m.term.contains("[BLOCKED]") || r.m.sessions[0].messages.last().unwrap().text.to_lowercase().contains("block"), "{}", r.m.term);
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn swarm_returns_heads_and_synthesis() {
    let Some(mut r) = rig("swarm") else { return };
    r.m.set_nav("swarm");
    assert!(r.m.submit(&mut r.be, "compare two designs"));
    pump(&mut r, 90, |m| !m.any_busy());
    let msgs = &r.m.sessions[0].messages;
    let last = msgs.last().unwrap();
    assert_eq!(last.header, "SWARM SYNTHESIS");
    assert!(msgs.len() >= 5, "heads then synthesis, got {}", msgs.len());
    // heads route to their own provider and need gateway keys; with none the real error must be shown, never faked
    assert!(last.text.contains("alpha") || last.text.starts_with("Error:"), "{}", last.text);
    assert!(msgs[2..msgs.len() - 1].iter().all(|m| m.text.contains("alpha") || m.text.starts_with("Error:")));
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn computer_use_capture_and_agent_status_and_jobs() {
    let Some(mut r) = rig("misc") else { return };
    r.m.capture(&mut r.be);
    pump(&mut r, 30, |m| m.screen_seq > 0 && m.window_title != "Detecting...");
    assert!(r.m.screen_seq > 0 && r.m.screen_b64.len() > 100, "screen capture returned a png");
    r.m.agent_start(&mut r.be, "write_file notes.txt", 5, "full_automation");
    pump(&mut r, 5, |_| false);
    r.m.poll_agent(&mut r.be);
    pump(&mut r, 10, |m| !m.agent_status.is_null());
    assert!(r.m.agent_status["status"].is_string(), "{:?}", r.m.agent_status);
    r.m.load_templates(&mut r.be);
    pump(&mut r, 10, |m| !m.templates.is_empty());
    assert!(!r.m.templates.is_empty());
    r.m.job_create(&mut r.be, "nightly", 3600.0, "full_automation", "echo hi");
    pump(&mut r, 5, |_| false);
    r.m.load_jobs(&mut r.be);
    pump(&mut r, 10, |m| !m.jobs.is_empty());
    assert!(r.m.jobs.iter().any(|j| j["name"] == "nightly"), "{:?}", r.m.jobs);
    let _ = std::fs::remove_dir_all(&r.dir);
}
