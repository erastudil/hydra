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

use egui_kittest::kittest::{NodeT, Queryable};

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
    h.run();
    assert_eq!(user_messages(app(&mut h)), 1, "Enter sends");
}
