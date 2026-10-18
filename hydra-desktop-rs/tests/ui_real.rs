//! Window tests against the REAL agent: the app drawn headless by egui_kittest, spawning `python -m hydra_cli stdio`
//! from this checkout, with only the model endpoint stubbed (tests/common). Violet's retest asked for this coverage.

use eframe::egui;
use egui_kittest::kittest::Queryable;
use egui_kittest::Harness;
use hydra_desktop::app::HydraApp;
use hydra_desktop::model::*;

mod common;

fn harness(name: &str) -> (Harness<'static, HydraApp>, std::path::PathBuf) {
    static INIT: std::sync::Once = std::sync::Once::new();
    INIT.call_once(|| {
        let base = common::start();
        let home = std::env::temp_dir().join(format!("hydra-uireal-home-{}", std::process::id()));
        std::fs::create_dir_all(&home).unwrap();
        std::env::set_var("LOCAL_AI_BASE", base);
        std::env::set_var("HOME", &home);
        std::env::set_var("HYDRA_HOME", home.join(".hydra"));
        std::env::set_var("HYDRA_REPO", format!("{}/..", env!("CARGO_MANIFEST_DIR")));
        let py = std::env::var("HYDRA_PYTHON").unwrap_or_else(|_| if cfg!(windows) { "python".into() } else { "python3".into() });
        assert!(std::process::Command::new(&py).arg("--version").output().is_ok(), "python is required: these tests run the real agent");
    });
    let dir = std::env::temp_dir().join(format!("hydra-uireal-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let cwd = dir.display().to_string();
    let mut h = Harness::builder().with_size(egui::vec2(1280.0, 800.0)).build_eframe(move |cc| HydraApp::build(&cc.egui_ctx, None, cwd, true));
    h.state_mut().model_mut().model = MODELS.iter().position(|x| x.0 == "local").unwrap();
    assert!(wait(&mut h, 20, |a| a.model().agent_ready), "real agent says hello");
    (h, dir)
}

fn wait(h: &mut Harness<'static, HydraApp>, secs: u64, mut cond: impl FnMut(&mut HydraApp) -> bool) -> bool {
    let end = std::time::Instant::now() + std::time::Duration::from_secs(secs);
    while std::time::Instant::now() < end {
        h.step();
        if cond(h.state_mut()) {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    false
}

fn say(h: &mut Harness<'static, HydraApp>, text: &str) {
    h.get_by_role(egui::accesskit::Role::MultilineTextInput).click();
    h.run_steps(2);
    h.event(egui::Event::Text(text.into()));
    h.run_steps(2);
    h.key_press(egui::Key::Enter);
    h.run_steps(3);
}

fn approval_waiting(a: &mut HydraApp) -> bool {
    !a.model().approvals.is_empty()
}

#[test]
fn d23_a_5000_line_write_keeps_the_approval_buttons_on_screen_and_enter_allows() {
    let (mut h, dir) = harness("d23");
    say(&mut h, "BIGWRITE please");
    assert!(wait(&mut h, 20, approval_waiting), "approval arrives");
    h.run_steps(10); // let the modal fade in
    for label in ["Allow", "Always in thread", "Always in project", "Skip", "Abort Turn"] {
        let r = h.get_by_label(label).rect();
        assert!(r.max.y <= 800.0 && r.min.y >= 0.0, "{label} is on screen: {r:?}");
    }
    h.get_by_label_contains("big.txt: 5000 lines");
    h.get_by_label_contains("4940 more lines not shown");
    assert!(!dir.join("big.txt").exists());
    h.key_press(egui::Key::Enter);
    h.run_steps(2);
    assert!(wait(&mut h, 20, |a| !a.model().any_busy()), "turn finishes after Enter allows");
    assert_eq!(std::fs::read_to_string(dir.join("big.txt")).unwrap().lines().count(), 5000);
}

#[test]
fn d20_the_rules_tab_shows_a_new_always_rule_without_switching_threads() {
    let (mut h, _dir) = harness("d20");
    h.state_mut().model_mut().drawer_tab = "rules";
    h.run_steps(5);
    h.get_by_label_contains("No saved rules");
    say(&mut h, "WRITE the file");
    assert!(wait(&mut h, 20, approval_waiting));
    h.run_steps(10);
    h.get_by_label("Always in project").click();
    h.run_steps(2);
    assert!(wait(&mut h, 20, |a| !a.model().any_busy()));
    assert!(wait(&mut h, 10, |a| !a.model().rules.1.is_empty()), "rules reloaded by themselves");
    h.run_steps(3);
    h.get_by_label_contains("every write_file");
    h.get_by_label("(project)");
}

#[test]
fn d27_a_skipped_tool_call_leaves_a_line_in_the_chat() {
    let (mut h, dir) = harness("d27");
    say(&mut h, "WRITE the file");
    assert!(wait(&mut h, 20, approval_waiting));
    h.run_steps(10);
    h.key_press(egui::Key::Escape);
    h.run_steps(2);
    assert!(wait(&mut h, 20, |a| !a.model().any_busy()));
    assert!(!dir.join("hello.txt").exists());
    let last = h.state_mut().model().sessions[0].messages.last().unwrap().clone();
    assert!(last.actions.iter().any(|a| a.starts_with("– write_file skipped by you")), "{:?}", last.actions);
    h.run_steps(3);
    h.get_by_label_contains("write_file skipped by you");
}

#[test]
fn d14_the_agent_tab_shows_the_step_limit_the_next_run_uses() {
    let (mut h, _dir) = harness("d14");
    h.state_mut().model_mut().set_nav("agent");
    h.run_steps(5);
    assert!(wait(&mut h, 10, |a| !a.model().agent_status.is_null()), "real runner status arrives");
    h.run_steps(3);
    let n = h.query_all_by_label_contains("step 0/30").count();
    assert!(n >= 1, "header strip and runner both say 0/30");
    assert_eq!(h.query_all_by_label_contains("/15").count(), 0, "the runner's own default never shows");
}

#[test]
fn d22_settings_refuse_a_bad_endpoint_url_name_it_and_save_nothing() {
    let (mut h, _dir) = harness("d22");
    let file = hydra_desktop::secrets::env_path();
    let before = std::fs::read_to_string(&file).unwrap_or_default();
    h.key_press_modifiers(egui::Modifiers::COMMAND, egui::Key::Comma);
    h.run_steps(10);
    let fields: Vec<_> = h.query_all_by_role(egui::accesskit::Role::TextInput).collect();
    fields.last().expect("endpoint field").click();
    h.run_steps(2);
    h.event(egui::Event::Text("not a url".into()));
    h.run_steps(2);
    h.get_by_label("Save").click();
    h.run_steps(3);
    h.get_by_label_contains("\"not a url\" is not a URL");
    h.get_by_label_contains("Nothing was saved");
    assert_eq!(std::fs::read_to_string(&file).unwrap_or_default(), before, "the env file is untouched");
}

#[test]
fn respawn_kill_9_drops_the_handle_restarts_with_backoff_and_shows_adopted_tasks() {
    let (mut h, _dir) = harness("respawn");
    h.state_mut().model_mut().sessions[0].permission = "bypass".into();
    say(&mut h, "BGTASK start a server");
    assert!(wait(&mut h, 20, |a| !a.model().any_busy() && a.model().running_tasks() == 1), "background task runs");
    h.run_steps(1500); // more than 20 s of app time since start, so a stall timer counted from app start would fire
    let old = h.state_mut().backend_pid().expect("agent up");
    unsafe { libc_kill(old as i32, 9) };
    // 1: the dead handle is dropped, so the app knows the agent is down (not only after the 20 s stall timer)
    assert!(wait(&mut h, 5, |a| a.backend_pid().is_none()), "dead backend handle dropped");
    // 2: the notice names the crash and the restart, never "has not answered after 20 seconds"
    let t = h.state_mut().toast_text().unwrap_or_default().to_string();
    let killed_str = if cfg!(windows) { "The agent stopped (" } else { "The agent stopped (killed by signal 9)" };
    assert!(t.contains(killed_str) && t.contains("Restarting in 0.5 s"), "{t}");
    assert!(h.query_by_label_contains("has not answered").is_none());
    // 3: automatic restart after the backoff, not at once
    let died = std::time::Instant::now();
    assert!(wait(&mut h, 10, |a| a.backend_pid().is_some_and(|p| p != old)), "respawned");
    assert!(died.elapsed() >= std::time::Duration::from_millis(400), "respawn waited for the backoff: {:?}", died.elapsed());
    h.run_steps(1);
    assert!(h.query_by_label_contains("has not answered").is_none(), "a fresh child is not reported as stalled");
    assert!(wait(&mut h, 20, |a| a.model().agent_ready), "new agent says hello");
    // 4: after hello the app asks again, so the adopted task shows in Tasks
    assert!(wait(&mut h, 20, |a| a.model().running_tasks() == 1 && a.model().tasks.values().any(|t| t["adopted"].as_bool() == Some(true))), "adopted task listed");
    assert!(wait(&mut h, 10, |a| !a.model().agent_threads.is_empty()), "sessions re-requested");
    assert!(h.query_by_label_contains("has not answered").is_none());
    let pid = h.state_mut().model().tasks.values().next().unwrap()["pid"].as_i64().unwrap();
    unsafe { libc_kill(pid as i32, 9) };
}

/// Top edge of the composer's text box as laid out in the last frame.
fn composer_top(h: &mut Harness<'static, HydraApp>) -> f32 {
    h.get_by_role(egui::accesskit::Role::MultilineTextInput).rect().min.y
}

#[test]
fn the_composer_does_not_move_when_a_turn_starts_or_ends() {
    let (mut h, _dir) = harness("still");
    h.run_steps(5);
    let idle = composer_top(&mut h);
    say(&mut h, "SLOW keep the box still");
    assert!(wait(&mut h, 20, |a| a.model().sessions[0].messages.last().is_some_and(|m| m.text.contains("tick"))), "turn streams");
    h.run_steps(2);
    assert!((composer_top(&mut h) - idle).abs() < 0.5, "busy box moved: idle {idle}, busy {}", composer_top(&mut h));
    assert!(wait(&mut h, 30, |a| !a.model().any_busy()), "turn ends");
    // every frame right after the turn, not only once the layout settles: a click lands where the box was drawn
    for frame in 0..4 {
        let y = composer_top(&mut h);
        assert!((y - idle).abs() < 0.5, "frame {frame} after the turn: box at {y}, idle {idle}");
        h.step();
    }
    // the click that a moving box made miss: straight after the turn, click and type, and the text goes in
    say(&mut h, "second marker-two");
    assert!(wait(&mut h, 20, |a| a.model().sessions[0].messages.iter().any(|m| m.user && m.text.contains("marker-two"))), "second prompt sent");
}

#[test]
fn bug5_a_reply_cut_by_kill_9_is_marked_unsaved_and_the_thread_reloads_from_the_agent() {
    let (mut h, _dir) = harness("bug5");
    say(&mut h, "first marker-one");
    assert!(wait(&mut h, 20, |a| !a.model().any_busy()), "turn one done");
    say(&mut h, "SLOW tell me more");
    assert!(wait(&mut h, 20, |a| a.model().sessions[0].messages.last().is_some_and(|m| m.text.contains("tick tick"))), "partial reply streams");
    let old = h.state_mut().backend_pid().unwrap();
    unsafe { libc_kill(old as i32, 9) };
    assert!(wait(&mut h, 5, |a| a.backend_pid().is_none()), "down");
    let last = h.state_mut().model().sessions[0].messages.last().cloned().unwrap();
    assert!(last.actions.iter().any(|a| a.contains("Not saved")), "cut reply marked unsaved: {last:?}");
    assert!(wait(&mut h, 30, |a| a.model().sessions[0].messages.iter().any(|m| m.text.contains("Reloaded from the agent"))), "thread reloaded after respawn");
    let msgs = h.state_mut().model().sessions[0].messages.clone();
    assert!(!msgs.iter().any(|m| m.text.contains("tick")), "the unsaved partial reply is gone, as in the agent: {msgs:?}");
    let cut = msgs.iter().position(|m| m.user && m.text.contains("tell me more")).expect("cut prompt kept");
    assert_eq!(msgs.get(cut + 1).map(|m| (m.user, m.text.as_str())), Some((false, "[Turn interrupted before a reply]")), "the agent's marker answers the cut prompt: {msgs:?}");
    assert!(msgs.iter().any(|m| m.user && m.text.contains("marker-one")) && msgs.iter().any(|m| !m.user && m.text.contains("alpha")), "stored turn one kept: {msgs:?}");
}

#[test]
fn busy_from_another_agent_holding_the_thread_is_shown() {
    let (mut h, dir) = harness("busy");
    let thread = h.state_mut().model().sessions[0].thread.clone();
    let mut other = hydra_desktop::backend::Backend::spawn(|| {}).expect("second agent");
    other.turn("t-other", "HANG hold the thread", &thread, Some(&dir.display().to_string()), Some("local"), Some("bypass"), None, &[]);
    std::thread::sleep(std::time::Duration::from_millis(1500));
    say(&mut h, "mine now");
    assert!(wait(&mut h, 20, |a| a.toast_text().is_some_and(|t| t.contains("running in another Hydra agent"))), "busy toast: {:?}", h.state_mut().toast_text());
    assert!(!h.state_mut().model().any_busy(), "the turn is not left spinning");
    let last = h.state_mut().model().sessions[0].messages.last().cloned().unwrap();
    assert!(last.text.contains("Error") || !last.actions.is_empty(), "{last:?}");
    other.cancel("t-other");
    drop(other);
}

#[cfg(unix)]
extern "C" {
    #[link_name = "kill"]
    fn libc_kill(pid: i32, sig: i32) -> i32;
}

#[cfg(windows)]
unsafe fn libc_kill(pid: i32, _sig: i32) -> i32 {
    let _ = std::process::Command::new("taskkill")
        .args(["/F", "/PID", &pid.to_string()])
        .output();
    0
}

/// Every painted text that starts with `prefix`, with its font size.
fn painted(h: &Harness<'static, HydraApp>, prefix: &str) -> Vec<(String, f32)> {
    h.output().shapes.iter().filter_map(|c| match &c.shape {
        egui::Shape::Text(t) if t.galley.text().starts_with(prefix) => Some((t.galley.text().to_string(), t.galley.job.sections.first().map(|s| s.format.font_id.size).unwrap_or(0.0))),
        _ => None,
    }).collect()
}

#[test]
fn d09_each_message_keeps_the_look_of_the_mode_it_was_sent_in() {
    let (mut h, _dir) = harness("d09");
    say(&mut h, "hello from chat mode");
    assert!(wait(&mut h, 20, |a| !a.model().any_busy()), "chat turn done");
    h.state_mut().model_mut().set_nav("terminal");
    h.run_steps(3);
    say(&mut h, "echo d09-terminal-output");
    assert!(wait(&mut h, 20, |a| !a.model().any_busy() && a.model().sessions[0].messages.last().is_some_and(|m| m.text == "d09-terminal-output")), "the real sandbox ran the command");
    let msgs = h.state_mut().model().sessions[0].messages.clone();
    let modes: Vec<(bool, &str)> = msgs.iter().skip(1).map(|m| (m.user, m.mode.as_str())).collect();
    assert_eq!(modes, vec![(true, ""), (false, ""), (true, "terminal"), (false, "terminal")], "{msgs:?}");
    for nav in ["terminal", "chat"] {
        h.state_mut().model_mut().set_nav(nav);
        h.run_steps(5);
        assert!(painted(&h, "$ hello").is_empty(), "in {nav} mode the chat prompt is not drawn as a shell command");
        assert_eq!(painted(&h, "hello from chat mode"), vec![("hello from chat mode".to_string(), hydra_desktop::theme::TYPE_MESSAGE_BODY)], "in {nav} mode the chat prompt is a chat bubble");
        assert_eq!(painted(&h, "alpha beta gamma").first().map(|p| p.1), Some(hydra_desktop::theme::TYPE_MESSAGE_BODY), "in {nav} mode the model reply is a chat bubble");
        assert_eq!(painted(&h, "$ echo"), vec![("$ echo d09-terminal-output".to_string(), hydra_desktop::theme::TYPE_TERMINAL)], "in {nav} mode the command stays a command");
        assert_eq!(painted(&h, "d09-terminal-output").first().map(|p| p.1), Some(hydra_desktop::theme::TYPE_TERMINAL), "in {nav} mode the output stays terminal output");
        assert_eq!(painted(&h, "YOU · TERMINAL SANDBOX").len(), 1, "in {nav} mode the command says where it ran");
        assert_eq!(painted(&h, "SHELL").len(), 1, "the output is from the shell, not from a model");
    }
}
