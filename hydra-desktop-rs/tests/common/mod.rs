//! Stub model endpoint shared by the real-adapter tests: an OpenAI-style HTTP server whose prompt words steer it.
#![allow(dead_code)]
use serde_json::{json, Value};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::Duration;

fn last_user(body: &Value) -> String {
    body["messages"].as_array().into_iter().flatten().rev().find(|m| m["role"] == "user").map(|m| m["content"].as_str().unwrap_or("").to_string()).unwrap_or_default()
}

/// User message texts of every request, newest last, so tests can see what history the agent sent.
pub static USERS: std::sync::Mutex<Vec<Vec<String>>> = std::sync::Mutex::new(Vec::new());
pub static SYSTEMS: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());
static FLAKY_SEEN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn handle(mut s: TcpStream) {
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
    USERS.lock().unwrap().push(body["messages"].as_array().into_iter().flatten().filter(|m| m["role"] == "user").map(|m| m["content"].as_str().unwrap_or("").to_string()).collect());
    if let Some(sys) = body["messages"].get(0).filter(|m| m["role"] == "system") {
        SYSTEMS.lock().unwrap().push(sys["content"].to_string());
    }
    if prompt.contains("FLAKY") && !FLAKY_SEEN.swap(true, std::sync::atomic::Ordering::SeqCst) {
        let _ = write!(s, "HTTP/1.1 503 Service Unavailable\r\nContent-Length: 4\r\nConnection: close\r\n\r\nbusy");
        return;
    }
    let usage = json!({"prompt_tokens": 10, "completion_tokens": 3});
    if !stream {
        let data = json!({"choices": [{"message": {"role": "assistant", "content": "alpha beta gamma"}, "finish_reason": "stop"}], "usage": usage}).to_string();
        let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", data.len(), data);
        return;
    }
    let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n");
    let send = |s: &mut TcpStream, v: Value| s.write_all(format!("data: {v}\n\n").as_bytes()).and_then(|_| s.flush());
    if prompt.contains("HANG") {
        std::thread::sleep(Duration::from_secs(300));
        return;
    }
    if prompt.contains("CUT") {
        let _ = send(&mut s, json!({"choices": [{"delta": {"content": "partial "}}]}));
        return; // closes without [DONE] or a finish reason
    }
    if prompt.contains("BIGWRITE") && !has_tool {
        let content: String = (0..5000).map(|i| format!("line {i}\n")).collect();
        let args = json!({"path": "big.txt", "content": content}).to_string();
        let _ = send(&mut s, json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_big", "type": "function", "function": {"name": "write_file", "arguments": args}}]}}]}));
    } else if prompt.contains("BGTASK") && !has_tool {
        let args = json!({"command": "sleep 60"}).to_string();
        let _ = send(&mut s, json!({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_bg", "type": "function", "function": {"name": "start_background_task", "arguments": args}}]}}]}));
    } else if prompt.contains("WRITE") && !has_tool {
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


/// Start the stub on a free port; returns the base URL for LOCAL_AI_BASE.
pub fn start() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for c in listener.incoming().flatten() {
            std::thread::spawn(move || handle(c));
        }
    });
    format!("http://127.0.0.1:{port}")
}
