//! Drives the real `python -m hydra_cli stdio` adapter from the Rust client and model, against a stub model endpoint.
//! Needs python3 with this repo importable (HYDRA_REPO, default ..). Fails loudly when python is missing.

use hydra_desktop::backend::{Backend, Ev};
use hydra_desktop::model::*;
use serde_json::json;
use std::time::{Duration, Instant};

mod common;
use common::SYSTEMS;

struct Rig {
    m: Model,
    be: Backend,
    dir: std::path::PathBuf,
    envs: Vec<(String, String)>,
}

fn rig(name: &str) -> Option<Rig> {
    let base = common::start();
    let dir = std::env::temp_dir().join(format!("hydra-rs-{name}-{}", std::process::id()));
    let proj = dir.join("proj");
    std::fs::create_dir_all(&proj).unwrap();
    let repo = std::env::var("HYDRA_REPO").unwrap_or_else(|_| format!("{}/..", env!("CARGO_MANIFEST_DIR")));
    let py = std::env::var("HYDRA_PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
    if std::process::Command::new(&py).arg("--version").output().is_err() {
        panic!("python is required for these tests (HYDRA_PYTHON={py}); a missing interpreter must fail, not skip (D11)");
    }
    std::env::set_var("HYDRA_PYTHON", &py);
    std::env::set_var("HYDRA_REPO", &repo);
    let envs = vec![
        ("LOCAL_AI_BASE".to_string(), base.clone()),
        ("HOME".to_string(), dir.display().to_string()),
        ("USERPROFILE".to_string(), dir.display().to_string()),
        ("HYDRA_BACKOFF_BASE".to_string(), "0.1".to_string()),
    ];
    let be = Backend::spawn_with(&envs, || {}).expect("spawn adapter");
    let mut m = Model::new(None, proj.display().to_string());
    m.model = "local".into();
    Some(Rig { m, be, dir, envs })
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
    let mut r = rig("chat").expect("rig");
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
    let mut r = rig("stop").expect("rig");
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
    let mut r = rig("approve").expect("rig");
    assert_eq!(r.m.sessions[0].permission, "manual", "a new thread asks by default (D01)");
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
    let mut r = rig("skip").expect("rig");
    assert_eq!(r.m.sessions[0].permission, "manual", "a new thread asks by default (D01)");
    assert!(r.m.submit(&mut r.be, "WRITE the file"));
    pump(&mut r, 40, |m| !m.approvals.is_empty());
    r.m.answer_approval(&mut r.be, "skip");
    done(&mut r);
    assert!(!std::path::Path::new(&r.m.cwd).join("hello.txt").exists());
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn terminal_sandbox_runs_a_real_command() {
    let mut r = rig("term").expect("rig");
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
    let mut r = rig("swarm").expect("rig");
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
    let mut r = rig("misc").expect("rig");
    r.m.capture(&mut r.be);
    pump(&mut r, 30, |m| m.screen_seq > 0 && m.window_title != "Detecting...");
    assert!(r.m.screen_seq > 0 && r.m.screen_b64.len() >= 90, "screen capture returned a png");
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

#[test]
fn stop_cancels_a_provider_that_never_answers_and_the_next_turn_runs() {
    let mut r = rig("hang").expect("rig");
    assert!(r.m.submit(&mut r.be, "HANG please"));
    std::thread::sleep(Duration::from_millis(800));
    assert!(r.m.any_busy(), "waiting on the provider");
    let t0 = Instant::now();
    r.m.stop(&mut r.be);
    pump(&mut r, 5, |m| !m.any_busy());
    assert!(!r.m.any_busy(), "Stop frees the thread (D02)");
    assert!(t0.elapsed() < Duration::from_secs(3), "cancel took {:?}", t0.elapsed());
    assert!(r.m.submit(&mut r.be, "say something"), "new input is accepted after a cancelled hang");
    done(&mut r);
    assert_eq!(r.m.sessions[0].messages.last().unwrap().text, "alpha beta gamma");
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn a_reply_cut_off_mid_stream_is_reported_not_silently_kept() {
    let mut r = rig("cut").expect("rig");
    assert!(r.m.submit(&mut r.be, "CUT test"));
    done(&mut r);
    let last = r.m.sessions[0].messages.last().unwrap();
    let shown = format!("{} {}", last.text, last.actions.join(" ")).to_lowercase();
    assert!(shown.contains("cut off"), "D05: {shown}");
    assert!(shown.contains("after 8 characters"), "D25: \"partial \" is 8 characters, not one chunk: {shown}");
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn agent_checkpoints_rewind_undo_and_scoped_always_rules() {
    let mut r = rig("rules").expect("rig");
    pump(&mut r, 20, |m| m.agent_ready);
    assert!(r.m.agent_checkpoints, "the adapter advertises checkpoints");
    assert!(r.m.submit(&mut r.be, "WRITE the file"));
    pump(&mut r, 40, |m| !m.approvals.is_empty());
    let id = r.m.approvals[0].id.clone();
    assert!(r.m.approve_always(&mut r.be, &id, "project", None));
    done(&mut r);
    let file = std::path::Path::new(&r.m.cwd).join("hello.txt");
    assert!(file.exists());
    let cps = r.m.sessions[0].checkpoints.clone();
    assert_eq!(cps.len(), 1, "one agent checkpoint per turn");
    assert!(cps[0].agent.is_some());
    assert_eq!(r.m.checkpoint_for(1), Some(0), "checkpoint is attached to the user message");
    r.m.load_rules(&mut r.be);
    pump(&mut r, 10, |m| !m.rules.1.is_empty());
    assert_eq!(r.m.rules.1[0]["tool"], "write_file");
    assert_eq!(r.m.rules.1[0]["scope"], "project");
    // the rule now settles the same call without asking
    assert!(r.m.submit(&mut r.be, "WRITE the file"));
    done(&mut r);
    assert!(r.m.approvals.is_empty(), "rule answered it");
    r.m.rewind_code(Some(&mut r.be), 0).unwrap();
    pump(&mut r, 10, |m| m.sessions[0].pre_rewind.is_some());
    assert!(!file.exists(), "rewind removed the file the turn created");
    r.m.undo_rewind(Some(&mut r.be)).unwrap();
    pump(&mut r, 10, |m| m.sessions[0].messages.last().map(|x| x.text.contains("undone")).unwrap_or(false));
    assert!(file.exists(), "undo brings it back");
    let rid = r.m.rules.1[0]["id"].as_str().unwrap().to_string();
    r.m.remove_rule(&mut r.be, &rid);
    pump(&mut r, 10, |m| m.rules.1.is_empty());
    assert!(r.m.rules.1.is_empty());
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn a_pinned_drawer_tab_is_not_switched_by_an_incoming_diff() {
    for pinned in [false, true] {
        let mut r = rig(if pinned { "pin1" } else { "pin0" }).expect("rig");
        r.m.drawer_tab = "term";
        r.m.tab_pinned = pinned;
        assert!(r.m.submit(&mut r.be, "WRITE the file"));
        pump(&mut r, 40, |m| !m.approvals.is_empty());
        r.m.answer_approval(&mut r.be, "allow");
        done(&mut r);
        assert!(!r.m.sessions[0].diffs.is_empty(), "diff arrived");
        assert_eq!(r.m.drawer_tab, if pinned { "term" } else { "diff" });
        let _ = std::fs::remove_dir_all(&r.dir);
    }
}

#[test]
fn skills_are_listed_picked_and_reach_the_model() {
    let mut r = rig("skills").expect("rig");
    r.m.pending_skills(&mut r.be);
    pump(&mut r, 20, |m| !m.skills.is_empty());
    assert!(r.m.skills.iter().any(|s| s["id"] == "code" && s["title"].as_str().map(|t| !t.is_empty()).unwrap_or(false)), "{:?}", r.m.skills);
    r.m.toggle_skill("code");
    assert!(r.m.submit(&mut r.be, "hello skills"));
    done(&mut r);
    let last = r.m.sessions[0].messages.last().unwrap().clone();
    assert!(last.actions.iter().any(|a| a == "skills: code"), "context notice in the stream: {:?}", last.actions);
    assert!(SYSTEMS.lock().unwrap().iter().any(|s| s.contains("[SKILL: code]")), "skill body reached the provider");
    r.m.toggle_skill("code");
    assert!(r.m.submit(&mut r.be, "hello again"));
    done(&mut r);
    assert!(!r.m.sessions[0].messages.last().unwrap().actions.iter().any(|a| a.starts_with("skills:")));
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn mcp_status_shows_good_and_broken_servers() {
    let mut r = rig("mcp").expect("rig");
    let cwd = r.m.cwd.clone();
    r.m.pending_mcp(&mut r.be, &cwd, false);
    pump(&mut r, 20, |m| m.mcp.is_some());
    assert_eq!(r.m.mcp.as_ref().unwrap().1.len(), 0, "no config, no servers");
    let hydra = std::path::Path::new(&cwd).join(".hydra");
    std::fs::create_dir_all(&hydra).unwrap();
    let server = format!("{}/tests/tiny_mcp.py", env!("CARGO_MANIFEST_DIR"));
    let py = std::env::var("HYDRA_PYTHON").unwrap();
    let cfg = json!({"mcpServers": {"demo": {"command": py, "args": [server, r.dir.join("calls.log").display().to_string()]}, "broken": {"command": r.dir.join("no-such-binary").display().to_string()}}});
    std::fs::write(hydra.join("mcp_servers.json"), cfg.to_string()).unwrap();
    r.m.mcp = None;
    r.m.pending_mcp(&mut r.be, &cwd, true);
    pump(&mut r, 30, |m| m.mcp.is_some());
    let (config, servers) = r.m.mcp.clone().unwrap();
    assert!(config.unwrap().ends_with("mcp_servers.json"));
    let by = |n: &str| servers.iter().find(|s| s["name"] == n).cloned().unwrap();
    assert_eq!(by("demo")["ok"], true);
    assert_eq!(by("demo")["tools"], json!(["demo__echo"]));
    assert_eq!(by("broken")["ok"], false);
    assert!(by("broken")["error"].as_str().map(|e| !e.is_empty()).unwrap_or(false));
    assert!(r.m.submit(&mut r.be, "hello mcp"));
    done(&mut r);
    let acts = r.m.sessions[0].messages.last().unwrap().actions.clone();
    assert!(acts.iter().any(|a| a == "mcp: 1 tools") && acts.iter().any(|a| a.starts_with("× mcp broken")), "{acts:?}");
    // per-server toggle: broken off for this project, so its error is gone from the next turn
    r.m.mcp = None;
    r.m.toggle_mcp(&mut r.be, &cwd, "broken", false);
    pump(&mut r, 20, |m| m.mcp.is_some());
    let servers = r.m.mcp.clone().unwrap().1;
    let broken = servers.iter().find(|s| s["name"] == "broken").unwrap();
    assert_eq!(broken["enabled"], false);
    assert!(broken["error"].is_null() && broken["tools"] == json!([]));
    assert_eq!(servers.iter().find(|s| s["name"] == "demo").unwrap()["enabled"], true);
    assert!(r.m.submit(&mut r.be, "hello mcp again"));
    done(&mut r);
    let acts = r.m.sessions[0].messages.last().unwrap().actions.clone();
    assert!(acts.iter().any(|a| a == "mcp: 1 tools") && !acts.iter().any(|a| a.contains("broken")), "{acts:?}");
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn a_background_task_outlives_its_turn_and_stops_from_the_app() {
    let mut r = rig("tasks").expect("rig");
    r.m.sessions[0].permission = "bypass".into();
    assert!(r.m.submit(&mut r.be, "BGTASK start a server"));
    done(&mut r);
    pump(&mut r, 10, |m| m.running_tasks() == 1);
    assert_eq!(r.m.running_tasks(), 1, "task_update after the turn: {:?}", r.m.tasks);
    let (id, t) = r.m.tasks.iter().next().map(|(k, v)| (k.clone(), v.clone())).unwrap();
    let pid = t["pid"].as_i64().unwrap();
    assert!(is_pid_alive(pid), "process alive after the turn ended");
    assert!(!r.m.any_busy());
    r.m.tasks.clear();
    r.m.pending_tasks(&mut r.be);
    pump(&mut r, 10, |m| !m.tasks.is_empty());
    assert_eq!(r.m.running_tasks(), 1, "task_list lists it");
    r.m.stop_task(&mut r.be, &id);
    pump(&mut r, 15, |m| m.running_tasks() == 0);
    assert_eq!(r.m.tasks[&id]["state"], "stopped");
    std::thread::sleep(Duration::from_millis(300));
    assert!(!is_pid_alive(pid), "process gone after stop");
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn a_503_before_the_first_token_shows_a_retry_notice_then_answers() {
    let mut r = rig("retry").expect("rig");
    assert!(r.m.submit(&mut r.be, "FLAKY hello"));
    let evs = pump(&mut r, 30, |m| !m.any_busy());
    assert!(evs.iter().any(|e| matches!(e, Ev::Retry { attempt: 1, reason, .. } if reason == "HTTP 503")), "{evs:?}");
    let last = r.m.sessions[0].messages.last().unwrap();
    assert!(last.actions.iter().any(|a| a.starts_with("↻ retry 1/") && a.contains("HTTP 503")), "{:?}", last.actions);
    assert_eq!(last.text, "alpha beta gamma");
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn a_respawned_agent_adopts_a_running_task_and_the_app_can_stop_it() {
    let mut r = rig("adopt").expect("rig");
    r.m.sessions[0].permission = "bypass".into();
    assert!(r.m.submit(&mut r.be, "BGTASK start a server"));
    done(&mut r);
    pump(&mut r, 10, |m| m.running_tasks() == 1);
    let (id, t) = r.m.tasks.iter().next().map(|(k, v)| (k.clone(), v.clone())).unwrap();
    let pid = t["pid"].as_i64().unwrap();
    // kill -9 the agent: its task keeps running; a new agent with the same home adopts it after hello
    unsafe_kill(r.be.pid());
    pump(&mut r, 10, |m| !m.agent_ready);
    r.be = Backend::spawn_with(&r.envs, || {}).expect("respawn");
    let evs = pump(&mut r, 20, |m| m.agent_ready && m.running_tasks() == 1);
    assert!(evs.iter().any(|e| matches!(e, Ev::TaskUpdate { task } if task["adopted"] == true && task["task"] == json!(id))), "{evs:?}");
    assert_eq!(r.m.tasks[&id]["adopted"], true);
    assert!(is_pid_alive(pid));
    r.m.stop_task(&mut r.be, &id);
    pump(&mut r, 15, |m| m.running_tasks() == 0);
    assert_eq!(r.m.tasks[&id]["state"], "stopped");
    std::thread::sleep(Duration::from_millis(300));
    assert!(!is_pid_alive(pid), "the adopted process is gone after Stop");
    let _ = std::fs::remove_dir_all(&r.dir);
}

fn is_pid_alive(pid: i64) -> bool {
    if cfg!(windows) {
        let out = std::process::Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output();
        out.map(|o| {
            let s = String::from_utf8_lossy(&o.stdout);
            s.contains(&pid.to_string()) && !s.contains("No tasks") && !s.contains("INFO:")
        }).unwrap_or(false)
    } else {
        let p = format!("/proc/{pid}");
        if !std::path::Path::new(&p).exists() {
            return false;
        }
        std::fs::read_to_string(format!("{p}/stat")).map(|s| !s.contains(") Z ")).unwrap_or(false)
    }
}

fn unsafe_kill(pid: u32) {
    if cfg!(windows) {
        let _ = std::process::Command::new("taskkill").args(["/F", "/PID", &pid.to_string()]).status();
    } else {
        let _ = std::process::Command::new("kill").args(["-9", &pid.to_string()]).status();
    }
}

#[test]
fn stopping_a_task_another_live_agent_owns_is_refused_and_shown() {
    let mut r = rig("owned").expect("rig");
    r.m.sessions[0].permission = "bypass".into();
    assert!(r.m.submit(&mut r.be, "BGTASK start a server"));
    done(&mut r);
    pump(&mut r, 10, |m| m.running_tasks() == 1);
    let id = r.m.tasks.keys().next().cloned().unwrap();
    // a second, live agent on the same home does not own the task
    let mut other = Backend::spawn_with(&r.envs, || {}).expect("second agent");
    let mut m2 = Model::new(None, r.m.cwd.clone());
    let end = Instant::now() + Duration::from_secs(10);
    while !m2.agent_ready && Instant::now() < end {
        if let Ok(ev) = other.rx.recv_timeout(Duration::from_millis(50)) {
            m2.apply(ev);
        }
    }
    m2.stop_task(&mut other, &id);
    let end = Instant::now() + Duration::from_secs(10);
    while m2.notice.is_empty() && Instant::now() < end {
        if let Ok(ev) = other.rx.recv_timeout(Duration::from_millis(50)) {
            m2.apply(ev);
        }
    }
    assert!(m2.notice.starts_with("Could not stop the task:"), "toast text: {:?}", m2.notice);
    pump(&mut r, 1, |_| false);
    assert_eq!(r.m.running_tasks(), 1, "the owner's task keeps running");
    r.m.stop_task(&mut r.be, &id);
    pump(&mut r, 15, |m| m.running_tasks() == 0);
    assert_eq!(r.m.tasks[&id]["state"], "stopped", "the owner can stop it");
    drop(other);
    let _ = std::fs::remove_dir_all(&r.dir);
}

#[test]
fn rewind_puts_the_conversation_back_and_undo_restores_it() {
    let mut r = rig("convrewind").expect("rig");
    pump(&mut r, 20, |m| m.agent_ready);
    for p in ["first marker-one", "second marker-two"] {
        assert!(r.m.submit(&mut r.be, p));
        done(&mut r);
    }
    let cps = r.m.sessions[0].checkpoints.clone();
    assert_eq!(cps.len(), 2);
    let before = r.m.sessions[0].messages.len();
    r.m.rewind_code(Some(&mut r.be), 1).unwrap();
    pump(&mut r, 10, |m| m.sessions[0].pre_rewind.is_some());
    let texts: Vec<String> = r.m.sessions[0].messages.iter().map(|m| m.text.clone()).collect();
    let said: Vec<&String> = r.m.sessions[0].messages.iter().filter(|m| m.header != "HYDRA").map(|m| &m.text).collect();
    assert!(!said.iter().any(|t| t.contains("marker-two")), "the rewound turn is gone from the thread: {said:?}");
    assert!(texts.iter().any(|t| t.contains("marker-one")));
    assert!(texts.last().unwrap().contains("files and conversation"), "{texts:?}");
    // the agent's history agrees: the next request carries turn one but not turn two
    assert!(r.m.submit(&mut r.be, "third marker-three"));
    done(&mut r);
    let users = common::USERS.lock().unwrap().iter().rev().find(|u| u.iter().any(|x| x.contains("marker-three"))).cloned().unwrap(); // other tests share the stub
    assert!(users.iter().any(|u| u.contains("marker-one")) && !users.iter().any(|u| u.contains("marker-two")), "{users:?}");
    // undo: rewinding to the backup brings turn two back, in the agent and in the thread
    let mut r2 = r;
    r2.m.sessions[0].pre_rewind.as_ref().expect("undo point");
    r2.m.undo_rewind(Some(&mut r2.be)).unwrap();
    pump(&mut r2, 10, |m| m.sessions[0].messages.last().map(|x| x.text.contains("undone")).unwrap_or(false));
    let texts: Vec<String> = r2.m.sessions[0].messages.iter().map(|m| m.text.clone()).collect();
    let said: Vec<&String> = r2.m.sessions[0].messages.iter().filter(|m| m.header != "HYDRA").map(|m| &m.text).collect();
    assert!(said.iter().any(|t| t.as_str() == "second marker-two"), "undo restored the turn: {texts:?}");
    assert!(r2.m.sessions[0].messages.len() >= before, "{texts:?}");
    let _ = std::fs::remove_dir_all(&r2.dir);
}
