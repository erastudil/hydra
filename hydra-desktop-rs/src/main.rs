#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod app;
mod theme;
mod widgets;

fn main() -> eframe::Result<()> {
    let size = std::env::var("HYDRA_SIZE").ok().and_then(|s| {
        let (w, h) = s.split_once('x')?;
        Some([w.parse::<f32>().ok()?, h.parse::<f32>().ok()?])
    }).unwrap_or([1280.0, 800.0]);
    let mut viewport = eframe::egui::ViewportBuilder::default()
        .with_title("Hydra")
        .with_inner_size(size)
        .with_min_inner_size([820.0, 560.0]);
    if let Some(icon) = theme::window_icon() {
        viewport = viewport.with_icon(icon);
    }
    let options = eframe::NativeOptions { viewport, ..Default::default() };
    eframe::run_native("Hydra", options, Box::new(|cc| Ok(Box::new(app::HydraApp::new(cc)))))
}
