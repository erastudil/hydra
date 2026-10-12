//! Window-level tests: the real app drawn headless by egui_kittest, driven by keyboard and pointer events.

use eframe::egui;
use egui_kittest::Harness;
use hydra_desktop::app::HydraApp;
use hydra_desktop::model::*;

fn harness(name: &str) -> Harness<'static, HydraApp> {
    let dir = std::env::temp_dir().join(format!("hydra-ui-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let cwd = dir.display().to_string();
    // the fake agent speaks the real stdio protocol; a missing python fails these tests loudly
    std::env::set_var("LOCAL_AI_BASE", "http://127.0.0.1:9"); // counts as a configured model, so first-run Settings stays closed
    std::env::set_var("HYDRA_REPO", format!("{}/tests/fake_agent", env!("CARGO_MANIFEST_DIR")));
    Harness::builder().with_size(egui::vec2(1280.0, 800.0)).build_eframe(move |cc| HydraApp::build(&cc.egui_ctx, None, cwd, true))
}

fn app<'a>(h: &'a mut Harness<'static, HydraApp>) -> &'a mut HydraApp {
    h.state_mut()
}

#[test]
fn first_frame_draws_and_has_one_thread_in_one_project() {
    let mut h = harness("first");
    h.run();
    let a = app(&mut h);
    assert_eq!(a.model().projects.len(), 1);
    assert_eq!(a.model().visible().len(), 1);
}

use egui_kittest::kittest::Queryable;

fn settle(h: &mut Harness<'static, HydraApp>) {
    h.run_steps(30);
}

#[test]
fn an_idle_window_stops_asking_for_frames() {
    let mut h = harness("idle");
    settle(&mut h);
    h.try_run().expect("idle window must not keep repainting (D06)");
}

fn user_messages(a: &HydraApp) -> usize {
    a.model().sessions.iter().flat_map(|s| s.messages.iter()).filter(|m| m.user).count()
}

#[test]
fn shift_enter_inserts_a_newline_and_enter_sends() {
    let mut h = harness("enter");
    settle(&mut h);
    let input = h.get_by_role(egui::accesskit::Role::MultilineTextInput);
    input.click();
    h.run();
    assert_eq!(h.ctx.memory(|m| m.focused()), Some(egui::Id::new("prompt")), "clicking the composer focuses it");
    h.event(egui::Event::Text("first".into()));
    h.run();
    assert_eq!(app(&mut h).input_text(), "first", "typing reaches the composer");
    h.key_press_modifiers(egui::Modifiers::SHIFT, egui::Key::Enter);
    h.run();
    h.event(egui::Event::Text("second".into()));
    h.run();
    assert_eq!(user_messages(app(&mut h)), 0, "Shift+Enter must not send (D04)");
    assert_eq!(app(&mut h).input_text(), "first\nsecond");
    h.key_press(egui::Key::Enter);
    h.run_steps(4); // a running turn animates, so step a fixed count instead of waiting for quiet
    assert_eq!(user_messages(app(&mut h)), 1, "Enter sends");
}

fn say(h: &mut Harness<'static, HydraApp>, text: &str) {
    h.get_by_role(egui::accesskit::Role::MultilineTextInput).click();
    h.run();
    h.event(egui::Event::Text(text.into()));
    h.run();
    h.key_press(egui::Key::Enter);
    h.run_steps(4);
}

fn wait(h: &mut Harness<'static, HydraApp>, secs: u64, mut cond: impl FnMut(&mut HydraApp) -> bool) -> bool {
    let end = std::time::Instant::now() + std::time::Duration::from_secs(secs);
    while std::time::Instant::now() < end {
        h.step();
        if cond(app(h)) {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    false
}

#[test]
fn a_new_thread_asks_before_tools_and_the_approval_modal_and_tab_appear() {
    let mut h = harness("approve");
    settle(&mut h);
    assert_eq!(app(&mut h).model().sessions[0].permission, "manual");
    h.get_by_label_contains("Permissions: Ask every time");
    wait(&mut h, 10, |a| a.model().agent_ready);
    say(&mut h, "ASK please");
    assert!(wait(&mut h, 10, |a| !a.model().approvals.is_empty()), "approval request arrives");
    h.run_steps(3);
    h.get_by_label_contains("APPROVAL REQUIRED");
    app(&mut h).model_mut().drawer_tab = "approvals";
    h.run_steps(3);
    h.get_by_label_contains("Approvals (1)");
}

#[test]
fn each_mode_changes_the_centre_pane() {
    let mut h = harness("modes");
    settle(&mut h);
    for (nav, title) in [("agent", "AGENT REACT LOOP"), ("swarm", "3-HEADED SWARM"), ("computer", "COMPUTER USE"), ("terminal", "TERMINAL SANDBOX")] {
        app(&mut h).model_mut().set_nav(nav);
        h.run_steps(3);
        h.get_by_label_contains(title);
    }
    app(&mut h).model_mut().set_nav("chat");
    h.run_steps(3);
    assert!(h.query_by_label_contains("TERMINAL SANDBOX").is_none());
}

#[test]
fn settings_opens_with_ctrl_comma_and_saves_a_key_without_echoing_it() {
    let home = std::env::temp_dir().join(format!("hydra-ui-home-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&home);
    std::env::set_var("HYDRA_HOME", &home);
    let mut h = harness("settings");
    settle(&mut h);
    h.key_press_modifiers(egui::Modifiers::COMMAND, egui::Key::Comma);
    h.run_steps(3);
    h.get_by_label_contains("SETTINGS: MODEL ACCESS");
    h.get_by_label_contains("OpenRouter key");
    assert!(h.get_all_by_role(egui::accesskit::Role::PasswordInput).count() >= 1, "secret fields are masked");
    h.get_all_by_role(egui::accesskit::Role::PasswordInput).next().unwrap().click();
    h.run();
    h.event(egui::Event::Text("sk-test-123456789".into()));
    h.run();
    h.get_by_label("Save").click();
    h.run_steps(4);
    let text = std::fs::read_to_string(home.join(".env")).expect("env file written");
    assert!(text.contains("OPENROUTER_API_KEY=sk-test-123456789"), "{text}");
    assert!(h.query_by_label_contains("sk-test-123456789").is_none(), "the key is not displayed back");
    std::env::remove_var("HYDRA_HOME");
}

#[test]
fn long_messages_collapse_and_the_window_stays_fast_with_thousands_of_messages() {
    let mut h = harness("long");
    settle(&mut h);
    {
        let m = app(&mut h).model_mut();
        let big: String = (0..5000).map(|i| format!("line {i}\n")).collect();
        for i in 0..3200 {
            m.sessions[0].messages.push(Msg { user: i % 2 == 0, header: "X".into(), text: format!("message number {i}"), actions: vec![] });
        }
        m.sessions[0].messages.push(Msg { user: false, header: "X".into(), text: big, actions: vec![] });
    }
    h.run_steps(12);
    h.get_by_label_contains("Show 4987 more lines");
    // frame time with 3,200 messages stays close to an empty thread's (virtualized), D06
    let t = std::time::Instant::now();
    for _ in 0..10 {
        h.step();
    }
    let per = t.elapsed().as_millis() / 10;
    assert!(per < 120, "{per} ms per frame with 3,200 messages (debug build)");
}

#[test]
fn diff_rows_carry_line_numbers_and_files_collapse() {
    let rows = hydra_desktop::app::parse_diff("--- a/x\n+++ b/x\n@@ -3,2 +3,3 @@\n keep\n-old\n+new\n+added\n");
    let shape: Vec<(u8, Option<u32>, Option<u32>)> = rows.iter().map(|r| (r.kind, r.old, r.new)).collect();
    assert_eq!(shape, vec![(b'@', None, None), (b' ', Some(3), Some(3)), (b'-', Some(4), None), (b'+', None, Some(4)), (b'+', None, Some(5))]);
}

fn click_label(h: &mut Harness<'static, HydraApp>, label: &str) {
    h.get_by_label(label).click();
    h.run_steps(3);
}

#[test]
fn tasks_panel_lists_a_task_that_outlives_its_turn_and_stops_it() {
    let mut h = harness("tasks");
    settle(&mut h);
    say(&mut h, "BGJOB start the dev server");
    assert!(wait(&mut h, 10, |a| !a.model().any_busy() && a.model().running_tasks() == 1), "the turn ends and the task keeps running");
    click_label(&mut h, "Tasks (1)");
    assert_eq!(app(&mut h).model().drawer_tab, "tasks");
    h.get_by_label("npm run dev");
    click_label(&mut h, "Stop");
    assert!(wait(&mut h, 10, |a| a.model().running_tasks() == 0), "Stop reaches the agent");
    assert_eq!(app(&mut h).model().tasks["task1"]["state"], "stopped");
}

#[test]
fn skills_picker_ticks_a_skill_and_the_next_turn_carries_it() {
    let mut h = harness("skills");
    settle(&mut h);
    click_label(&mut h, "Skills");
    assert!(wait(&mut h, 10, |a| !a.model().skills.is_empty()), "skill_list arrives");
    h.run_steps(3);
    click_label(&mut h, "code");
    assert_eq!(app(&mut h).model().picked_skills, vec!["code".to_string()]);
    h.get_by_label("Skills (1)");
    say(&mut h, "hello");
    assert!(wait(&mut h, 10, |a| a.model().sessions[0].messages.last().map(|m| m.actions.iter().any(|x| x == "skills: code")).unwrap_or(false)), "turn carried the skill");
}
