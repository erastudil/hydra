//! Window-level tests: the real app drawn headless by egui_kittest, driven by keyboard and pointer events.

use eframe::egui;
use egui_kittest::Harness;
use hydra_desktop::app::HydraApp;
use hydra_desktop::model::*;

fn harness(name: &str) -> Harness<'static, HydraApp> {
    harness_sized(name, 1280.0, 800.0)
}

fn harness_sized(name: &str, w: f32, hgt: f32) -> Harness<'static, HydraApp> {
    let dir = std::env::temp_dir().join(format!("hydra-ui-{name}-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let cwd = dir.display().to_string();
    // the fake agent speaks the real stdio protocol; a missing python fails these tests loudly
    std::env::set_var("LOCAL_AI_BASE", "http://127.0.0.1:9"); // counts as a configured model, so first-run Settings stays closed
    std::env::set_var("HYDRA_REPO", format!("{}/tests/fake_agent", env!("CARGO_MANIFEST_DIR")));
    Harness::builder().with_size(egui::vec2(w, hgt)).build_eframe(move |cc| HydraApp::build(&cc.egui_ctx, None, cwd, true))
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
            m.sessions[0].messages.push(Msg { user: i % 2 == 0, header: "X".into(), text: format!("message number {i}"), actions: vec![], segments: vec![] });
        }
        m.sessions[0].messages.push(Msg { user: false, header: "X".into(), text: big, actions: vec![], segments: vec![] });
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

fn type_and_enter(h: &mut Harness<'static, HydraApp>, text: &str) {
    h.get_by_role(egui::accesskit::Role::MultilineTextInput).click();
    h.run();
    h.event(egui::Event::Text(text.into()));
    h.run();
    h.key_press(egui::Key::Enter);
    h.run_steps(3);
}

#[test]
fn slash_commands_run_instead_of_sending_and_tab_completes() {
    let mut h = harness("slash");
    settle(&mut h);
    type_and_enter(&mut h, "/plan");
    assert_eq!(app(&mut h).model().sessions[0].permission, "plan");
    assert_eq!(user_messages(app(&mut h)), 0, "a slash command is not a message");
    h.get_by_role(egui::accesskit::Role::MultilineTextInput).click();
    h.run();
    h.event(egui::Event::Text("/ta".into()));
    h.run();
    h.get_by_label_contains("/tasks");
    h.key_press(egui::Key::Tab);
    h.run();
    assert_eq!(app(&mut h).input_text(), "/tasks");
    h.key_press(egui::Key::Enter);
    h.run_steps(3);
    assert_eq!(app(&mut h).model().drawer_tab, "tasks");
    assert_eq!(app(&mut h).input_text(), "");
    // a partial command completes on Enter rather than being sent
    h.get_by_role(egui::accesskit::Role::MultilineTextInput).click();
    h.run();
    h.event(egui::Event::Text("/bypa".into()));
    h.run();
    h.key_press(egui::Key::Enter);
    h.run();
    assert_eq!(app(&mut h).input_text(), "/bypass");
    assert_eq!(user_messages(app(&mut h)), 0);
}

#[test]
fn ctrl_enter_steers_a_running_turn() {
    let mut h = harness("steer");
    settle(&mut h);
    say(&mut h, "SLOW count for a while");
    assert!(app(&mut h).model().active_busy());
    h.get_by_role(egui::accesskit::Role::MultilineTextInput).click();
    h.run_steps(2);
    h.event(egui::Event::Text("do this instead".into()));
    h.run_steps(2);
    h.key_press_modifiers(egui::Modifiers::COMMAND, egui::Key::Enter);
    h.run_steps(2);
    assert!(app(&mut h).toast_text().unwrap_or("").starts_with("Steering"));
    // SLOW runs 4 s; the steer message must go out well before that, so the first turn was really stopped
    assert!(wait(&mut h, 2, |a| a.model().sessions[0].messages.iter().any(|m| m.user && m.text == "do this instead")), "the steer message is sent right after the stop");
    let msgs = &app(&mut h).model().sessions[0].messages;
    let first = msgs.iter().position(|m| m.text == "SLOW count for a while").unwrap();
    assert!(msgs[first + 1].actions.iter().any(|a| a.contains("cancel")), "the first turn shows it was cancelled: {:?}", msgs[first + 1].actions);
}

#[test]
fn f1_shows_the_shortcuts_sheet_and_ctrl_shift_f_searches_every_chat() {
    let mut h = harness("help");
    settle(&mut h);
    h.key_press(egui::Key::F1);
    h.run();
    h.get_by_label("Ctrl+Shift+F");
    h.get_by_label("Steer: stop the running turn and send this now");
    h.key_press(egui::Key::Escape);
    h.run();
    {
        let m = app(&mut h).model_mut();
        m.sessions[0].messages.push(Msg { user: true, header: String::new(), text: "the zebra parser is broken".into(), actions: vec![], segments: vec![] });
        m.new_session();
    }
    h.run();
    assert_eq!(app(&mut h).model().active, 1);
    h.key_press_modifiers(egui::Modifiers::COMMAND | egui::Modifiers::SHIFT, egui::Key::F);
    h.run();
    h.event(egui::Event::Text("ZEBRA".into()));
    h.run_steps(2);
    h.get_by_label_contains("zebra parser").click();
    h.run_steps(2);
    assert_eq!(app(&mut h).model().active, 0, "clicking a hit opens its thread");
}

#[test]
fn slash_agents_creates_and_opens_the_instruction_file() {
    let mut h = harness("agentsmd");
    settle(&mut h);
    let root = app(&mut h).model().project_path();
    let _ = std::fs::remove_file(std::path::Path::new(&root).join("AGENTS.md"));
    type_and_enter(&mut h, "/agents");
    assert!(std::path::Path::new(&root).join("AGENTS.md").is_file());
    assert_eq!(app(&mut h).model().drawer_tab, "files");
    h.get_by_label_contains("AGENTS.md");
}

#[test]
fn d16_narrow_drawer_uses_short_tab_names_that_fit() {
    let mut h = harness_sized("narrow", 820.0, 560.0);
    settle(&mut h);
    app(&mut h).model_mut().drawer_tab = "files";
    h.run_steps(5);
    for label in ["Git", "Asks"] {
        let r = h.get_by_label(label).rect();
        assert!(r.width() >= 30.0, "{label} cell has room: {r:?}");
    }
    assert!(h.query_by_label("Approvals").is_none(), "the long name is not squeezed in");
}

#[test]
fn the_stall_timer_counts_from_the_latest_spawn_not_from_app_start() {
    use hydra_desktop::app::agent_stalled;
    assert!(agent_stalled(false, -1.0, 0.0, 21.0), "first start, 21 s, no hello");
    assert!(!agent_stalled(false, 100.0, 0.0, 101.0), "respawned 1 s ago: not stalled");
    assert!(agent_stalled(false, 100.0, 0.0, 121.0), "respawned 21 s ago, still no hello");
    assert!(!agent_stalled(true, 100.0, 0.0, 500.0));
}

fn on_screen(r: egui::Rect, w: f32, h: f32) -> bool {
    r.min.x >= 0.0 && r.min.y >= 0.0 && r.max.x <= w + 0.5 && r.max.y <= h + 0.5
}

#[test]
fn a_huge_write_approval_keeps_its_buttons_on_screen() {
    // D23: a 5,000-line write_file used to push Allow/Skip/Abort below the window
    for (w, hgt) in [(1280.0, 800.0), (900.0, 700.0)] {
        let mut h = harness_sized(&format!("bigapproval{w}"), w, hgt);
        settle(&mut h);
        let content: String = (0..5000).map(|i| format!("line {i} of a very large generated file\n")).collect();
        let a = app(&mut h);
        let sid = a.model().sessions[a.model().active].id;
        a.model_mut().approvals.push(Approval { sid, turn: "t1".into(), id: "ap1".into(), tool: "write_file".into(), args: serde_json::json!({"path": "big.txt", "content": content}) });
        h.run_steps(10);
        for label in ["Allow", "Skip", "Abort Turn"] {
            let r = h.get_by_label(label).rect();
            assert!(on_screen(r, w, hgt), "{label} at {r:?} is off a {w}x{hgt} window");
        }
    }
}

#[test]
fn undo_rewind_is_fully_visible_at_desk_and_narrow_widths() {
    // D26: the button was clipped to "U…" at the right edge of the composer meta row
    for (w, hgt) in [(1280.0, 800.0), (900.0, 700.0), (820.0, 560.0)] {
        let mut h = harness_sized(&format!("undo{w}"), w, hgt);
        settle(&mut h);
        let a = app(&mut h);
        let i = a.model().active;
        a.model_mut().sessions[i].pre_rewind = Some("0000000000000000000000000000000000000000".into());
        h.run_steps(5);
        let r = h.get_by_label("Undo rewind").rect();
        // the composer column ends where the inspection drawer starts
        let column_right = w - hydra_desktop::layout::frame(w).drawer_width;
        assert!(on_screen(r, w, hgt) && r.max.x <= column_right, "Undo rewind at {r:?} runs past the composer column (right edge {column_right})");
    }
}


/// Painted text shapes whose text starts with `prefix`, with their screen rects.
fn painted_texts(h: &Harness<'static, HydraApp>, prefix: &str) -> Vec<egui::Rect> {
    h.output().shapes.iter().filter_map(|c| match &c.shape {
        egui::Shape::Text(t) if t.galley.text().starts_with(prefix) => Some(t.visual_bounding_rect()),
        _ => None,
    }).collect()
}

#[test]
fn a_long_diff_fills_its_box_with_rows() {
    // D08: rows were packed at the line height while the list reserved line height plus item spacing,
    // so the bottom third of the diff box stayed empty
    for (w, hgt) in [(1280.0, 800.0), (900.0, 700.0)] {
        let mut h = harness_sized(&format!("diffbox{w}"), w, hgt);
        settle(&mut h);
        let body: String = (0..5000).map(|i| format!("+line {i}\n")).collect();
        let a = app(&mut h);
        let i = a.model().active;
        a.model_mut().sessions[i].diffs.push(DiffItem { path: "big.txt".into(), diff: format!("--- /dev/null\n+++ b/big.txt\n@@ -0,0 +1,5000 @@\n{body}") });
        a.model_mut().drawer_tab = "diff";
        a.model_mut().tab_pinned = true;
        h.run_steps(5);
        let rows = painted_texts(&h, "+ line ");
        assert!(rows.len() > 5, "diff rows are painted at {w}x{hgt}: {}", rows.len());
        let last = rows.iter().map(|r| r.max.y).fold(f32::MIN, f32::max);
        let pitch = (rows[1].center().y - rows[0].center().y).abs();
        // the box is the BG3 frame that holds the rows
        let boxr = h.output().shapes.iter().filter_map(|c| match &c.shape {
            egui::Shape::Rect(r) if r.fill == hydra_desktop::theme::BG3 && r.rect.contains(rows[0].center()) => Some(r.rect),
            _ => None,
        }).min_by(|a, b| a.area().total_cmp(&b.area())).expect("diff box frame");
        let gap = boxr.max.y - last;
        assert!(gap < 2.0 * pitch, "{w}x{hgt}: {} rows end at {last}, box ends at {}: {gap:.0} px empty (row pitch {pitch:.0})", rows.len(), boxr.max.y);
    }
}

#[test]
fn header_chips_never_cover_the_brand_and_the_cost_stays_visible() {
    // the cwd and context chips pushed the right-hand group over the brand and off the left edge
    for (w, hgt) in [(1920.0, 1080.0), (1280.0, 800.0), (900.0, 700.0), (820.0, 560.0)] {
        let mut h = harness_sized(&format!("header{w}"), w, hgt);
        settle(&mut h);
        app(&mut h).model_mut().cwd = "/home/someone/projects/a-rather-long-project-name/src/ünïcode-dir".into();
        h.run_steps(5);
        let header_h = hydra_desktop::layout::frame(w).header_height;
        let brand: Vec<egui::Rect> = ["HYDRA", "DESKTOP", "v1.2.2"].iter().flat_map(|p| painted_texts(&h, p)).filter(|r| r.max.y < header_h).collect();
        assert!(!brand.is_empty(), "{w}: brand painted");
        let brand_right = brand.iter().map(|r| r.max.x).fold(f32::MIN, f32::max);
        let texts: Vec<(String, egui::Rect)> = h.output().shapes.iter().filter_map(|c| match &c.shape {
            egui::Shape::Text(t) => Some((t.galley.text().to_string(), t.visual_bounding_rect())),
            _ => None,
        }).filter(|(s, r)| r.max.y < header_h && !s.is_empty() && !["HYDRA DESKTOP", "HYDRA", "DESKTOP", "v1.2.2"].contains(&s.as_str())).collect();
        for (s, r) in &texts {
            assert!(r.min.x > brand_right && r.max.x <= w, "{w}x{hgt}: header text {s:?} at {r:?} overlaps the brand (ends {brand_right}) or leaves the window");
        }
        assert!(texts.iter().any(|(s, _)| s == "$0.0000"), "{w}: cost chip is shown");
        if w >= 1920.0 {
            assert!(texts.iter().any(|(s, _)| s.starts_with("cwd: ") && s.ends_with("ünïcode-dir")), "{w}: a wide window shows the cwd: {texts:?}");
        }
    }
}

#[test]
fn cwd_chip_elides_from_the_left_on_char_boundaries() {
    use hydra_desktop::app::fit_cwd;
    let p = "/home/josé/проекты/hydra";
    assert_eq!(fit_cwd(p, |_| true).as_deref(), Some("cwd: /home/josé/проекты/hydra"));
    // a width budget in characters stands in for pixels; the tail is kept and never split inside a character
    assert_eq!(fit_cwd(p, |t| t.chars().count() <= 20).as_deref(), Some("cwd: …/проекты/hydra"));
    assert_eq!(fit_cwd(p, |t| t.chars().count() <= 6), None, "too tight: no chip rather than a useless stub");
}

#[test]
fn default_model_is_mimo_flash_and_computer_use_renders_screen_gradient_mesh() {
    let mut h = harness("cu_gradient");
    settle(&mut h);
    // No baked default: the selection is unresolved until the backend's
    // live catalog lands, then it resolves to the catalog default.
    assert_eq!(app(&mut h).model().model, "");
    app(&mut h).model_mut().apply(hydra_desktop::backend::Ev::ModelList {
        id: "rX".into(),
        default: "mimo 2.6 flash".into(),
        aliases: vec![("mimo 2.6 flash".into(), "mimo-2.6-flash".into())],
        providers: vec![],
        local: vec![],
        unavailable: vec![],
    });
    assert_eq!(app(&mut h).model().model, "mimo 2.6 flash");
    // A busy chat turn — even on the Computer Use page — must NOT light the
    // screen glow: the glow means the machine is being driven, and a chat
    // turn's busy signal is the composer's spinner row instead.
    app(&mut h).model_mut().set_nav("computer");
    say(&mut h, "SLOW inspect screen");
    assert!(app(&mut h).model().active_busy());
    assert!(!app(&mut h).is_computer_use_active());
    h.run_steps(3);
    h.get_by_label_contains("Thinking · working...");
    let has_gradient_mesh = h.output().shapes.iter().any(|c| match &c.shape {
        egui::Shape::Mesh(m) => m.vertices.len() == 64 * 3 && m.indices.len() == 64 * 4 * 3,
        _ => false,
    });
    assert!(!has_gradient_mesh, "a busy chat turn must not paint the screen glow mesh");
    // A computer_* tool call in flight lights it; its result clears it.
    app(&mut h).model_mut().apply(hydra_desktop::backend::Ev::ToolStart {
        turn: "t-cu".into(),
        call: 1,
        tool: "computer_screenshot".into(),
        args: serde_json::json!({}),
    });
    assert!(app(&mut h).is_computer_use_active());
    h.run_steps(2);
    let lit = h.output().shapes.iter().any(|c| match &c.shape {
        egui::Shape::Mesh(m) => m.vertices.len() == 64 * 3 && m.indices.len() == 64 * 4 * 3,
        _ => false,
    });
    assert!(lit, "a computer tool in flight must paint the screen glow mesh");
    app(&mut h).model_mut().apply(hydra_desktop::backend::Ev::ToolResult {
        turn: "t-cu".into(),
        tool: "computer_screenshot".into(),
        is_error: false,
        output: "ok".into(),
    });
    assert!(!app(&mut h).is_computer_use_active());
}

