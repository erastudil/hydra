//! Auto-update on launch: fast-forward the Hydra checkout when safe.
//!
//! A launch spawns one background thread. The thread fetches `origin/main`
//! and, only when the checkout sits on `main` with a clean working tree,
//! fast-forwards it. A dirty tree, a diverged history, a missing git, or a
//! non-checkout install stays silent or reports that an update waits; no
//! local work is ever overwritten. `HYDRA_NO_AUTO_UPDATE=1` disables the
//! check. The running binary keeps its own code until the next build; the
//! Python backend and any pulled sources apply on the next launch.

use std::path::Path;
use std::process::Command;
use std::sync::mpsc::{channel, Receiver, Sender};

/// True when the launch check must not run.
pub fn disabled() -> bool {
    matches!(std::env::var("HYDRA_NO_AUTO_UPDATE").as_deref(), Ok("1") | Ok("true") | Ok("yes"))
}

fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .env("GIT_TERMINAL_PROMPT", "0")
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// Working tree carries no staged, unstaged, or untracked changes.
pub fn tree_clean(repo: &Path) -> bool {
    git(repo, &["status", "--porcelain"]).map(|s| s.is_empty()).unwrap_or(false)
}

/// Commits on `origin/main` that HEAD lacks. Zero when unknown.
pub fn behind_main(repo: &Path) -> u32 {
    git(repo, &["rev-list", "--count", "HEAD..origin/main"]).and_then(|s| s.parse().ok()).unwrap_or(0)
}

/// One launch check. Returns a user-facing line only when state changed or
/// an update waits on the user; silence means nothing to report.
pub fn check_once(repo: &Path) -> Option<String> {
    if disabled() || !repo.join(".git").exists() {
        return None;
    }
    if git(repo, &["branch", "--show-current"]).as_deref() != Some("main") {
        return None;
    }
    git(repo, &["fetch", "--quiet", "origin", "main"])?;
    let behind = behind_main(repo);
    if behind == 0 {
        return None;
    }
    if !tree_clean(repo) {
        return Some(format!("update available: {behind} commit(s) on origin/main; working tree has local changes, so nothing was pulled"));
    }
    if git(repo, &["pull", "--ff-only", "--quiet", "origin", "main"]).is_none() {
        return Some(format!("update available: {behind} commit(s) on origin/main; fast-forward failed, so nothing was pulled"));
    }
    let sha = git(repo, &["rev-parse", "--short", "HEAD"]).unwrap_or_default();
    Some(format!("updated to {sha} ({behind} commit(s) pulled); restart Hydra to apply interface changes"))
}

/// Spawn the launch check against the discovered checkout. The receiver
/// yields at most one line and then closes.
pub fn spawn_launch_check() -> Receiver<String> {
    let (tx, rx): (Sender<String>, Receiver<String>) = channel();
    std::thread::spawn(move || {
        if let Some(repo) = crate::repo_dir() {
            if let Some(line) = check_once(Path::new(&repo)) {
                let _ = tx.send(line);
            }
        }
    });
    rx
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_repo(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("hydra-update-test-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let run = |args: &[&str]| {
            assert!(Command::new("git").arg("-C").arg(&dir).args(args).env("GIT_AUTHOR_NAME", "t").env("GIT_AUTHOR_EMAIL", "t@t").env("GIT_COMMITTER_NAME", "t").env("GIT_COMMITTER_EMAIL", "t@t").status().unwrap().success(), "git {args:?}");
        };
        run(&["init", "--quiet", "-b", "main"]);
        std::fs::write(dir.join("a.txt"), "one\n").unwrap();
        run(&["add", "."]);
        run(&["commit", "--quiet", "-m", "one"]);
        dir
    }

    #[test]
    fn clean_tree_and_behind_count_on_real_repo() {
        let dir = temp_repo("clean");
        assert!(tree_clean(&dir));
        assert_eq!(behind_main(&dir), 0);
        std::fs::write(dir.join("a.txt"), "dirty\n").unwrap();
        assert!(!tree_clean(&dir));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn non_checkout_reports_nothing() {
        let dir = std::env::temp_dir().join(format!("hydra-update-test-{}-plain", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(check_once(&dir), None);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
