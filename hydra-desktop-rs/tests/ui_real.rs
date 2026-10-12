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
        let py = std::env::var("HYDRA_PYTHON").unwrap_or_else(|_| "python3".into());
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
    let old = h.state_mut().backend_pid().expect("agent up");
    unsafe { libc_kill(old as i32, 9) };
    // 1: the dead handle is dropped, so the app knows the agent is down (not only after the 20 s stall timer)
    assert!(wait(&mut h, 5, |a| a.backend_pid().is_none()), "dead backend handle dropped");
    // 2: the notice names the crash and the restart, never "has not answered after 20 seconds"
    let t = h.state_mut().toast_text().unwrap_or_default().to_string();
    assert!(t.contains("The agent stopped") && t.contains("Restarting in 0.5 s"), "{t}");
    assert!(h.query_by_label_contains("has not answered").is_none());
    // 3: automatic restart after the backoff, not at once
    let died = std::time::Instant::now();
    assert!(wait(&mut h, 10, |a| a.backend_pid().is_some_and(|p| p != old)), "respawned");
    assert!(died.elapsed() >= std::time::Duration::from_millis(400), "respawn waited for the backoff: {:?}", died.elapsed());
    assert!(wait(&mut h, 20, |a| a.model().agent_ready), "new agent says hello");
    // 4: after hello the app asks again, so the adopted task shows in Tasks
    assert!(wait(&mut h, 20, |a| a.model().running_tasks() == 1 && a.model().tasks.values().any(|t| t["adopted"].as_bool() == Some(true))), "adopted task listed");
    assert!(wait(&mut h, 10, |a| !a.model().agent_threads.is_empty()), "sessions re-requested");
    assert!(h.query_by_label_contains("has not answered").is_none());
    let pid = h.state_mut().model().tasks.values().next().unwrap()["pid"].as_i64().unwrap();
    unsafe { libc_kill(pid as i32, 9) };
}

extern "C" {
    #[link_name = "kill"]
    fn libc_kill(pid: i32, sig: i32) -> i32;
}
