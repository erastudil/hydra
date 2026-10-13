//! Panic hook and log folder. A crash leaves a report that the next start offers to show.

use std::path::PathBuf;

pub fn log_dir() -> PathBuf {
    crate::model::state_dir().join("logs")
}

/// Install a hook that writes `crash-<unix>.txt` with the panic message and location, then runs the default hook.
pub fn install() {
    let dir = log_dir();
    let default = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let _ = std::fs::create_dir_all(&dir);
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let body = format!("Hydra {} crashed at {stamp}\n{info}\n{:?}\n", env!("CARGO_PKG_VERSION"), std::backtrace::Backtrace::force_capture());
        let _ = std::fs::write(dir.join(format!("crash-{stamp}.txt")), body);
        default(info);
    }));
}

/// Newest unreported crash file, if any. Reporting renames it to `.seen`.
pub fn take_last_crash() -> Option<(PathBuf, String)> {
    let dir = log_dir();
    let mut files: Vec<_> = std::fs::read_dir(&dir).ok()?.flatten().map(|e| e.path()).filter(|p| p.extension().map(|e| e == "txt").unwrap_or(false) && p.file_name().map(|n| n.to_string_lossy().starts_with("crash-")).unwrap_or(false)).collect();
    files.sort();
    let last = files.pop()?;
    let text = std::fs::read_to_string(&last).unwrap_or_default();
    let _ = std::fs::rename(&last, last.with_extension("seen"));
    Some((last, text.lines().take(3).collect::<Vec<_>>().join("\n")))
}
