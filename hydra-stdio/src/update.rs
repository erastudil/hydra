//! Auto-update on launch: check version and commit against GitHub (`origin/main`),
//! fast-forward the Hydra checkout when safe, and synchronize local desktop/TUI binaries.
//!
//! A launch spawns one background thread. The thread fetches `origin/main`,
//! compares the running binary's compiled version (`BUILD_VERSION`) and git commit
//! (`BUILD_SHA`) against `origin/main` and local `HEAD`, fast-forwards the checkout
//! when on `main` with a clean working tree, and rebuilds/installs the local
//! `Hydra` desktop and `hydra-tui` release binaries when new commits arrive or
//! when the installed binary lags behind the git repository.
//! A dirty tree, a diverged history, a missing git, or a non-checkout install stays
//! safe and never overwrites uncommitted local work. `HYDRA_NO_AUTO_UPDATE=1`
//! disables the launch check.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Mutex, OnceLock};

/// Compiled crate version embedded at build time.
pub const BUILD_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Compiled short git commit SHA embedded by `hydra-stdio/build.rs`.
pub const BUILD_SHA: &str = env!("HYDRA_BUILD_SHA");

static SYNC_STATUS: OnceLock<Mutex<String>> = OnceLock::new();

fn status_cell() -> &'static Mutex<String> {
    SYNC_STATUS.get_or_init(|| Mutex::new(format!("{} · checking GitHub...", build_stamp())))
}

/// Human-readable stamp of the compiled binary (`v1.2.2 (6b4ecba)`).
pub fn build_stamp() -> String {
    if BUILD_SHA.is_empty() || BUILD_SHA == "unknown" {
        format!("v{BUILD_VERSION}")
    } else {
        let short = &BUILD_SHA[..BUILD_SHA.len().min(7)];
        format!("v{BUILD_VERSION} ({short})")
    }
}

/// Latest recorded version and GitHub sync status for UI headers and banners.
pub fn status_line() -> String {
    status_cell()
        .lock()
        .map(|s| s.clone())
        .unwrap_or_else(|_| build_stamp())
}

fn set_status_line(s: impl Into<String>) {
    if let Ok(mut guard) = status_cell().lock() {
        *guard = s.into();
    }
}

/// True when the launch check must not run.
pub fn disabled() -> bool {
    matches!(
        std::env::var("HYDRA_NO_AUTO_UPDATE").as_deref(),
        Ok("1") | Ok("true") | Ok("yes")
    )
}

fn hidden_cmd(program: &str) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd
}

fn git(repo: &Path, args: &[&str]) -> Option<String> {
    let out = hidden_cmd("git")
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
    git(repo, &["status", "--porcelain"])
        .map(|s| s.is_empty())
        .unwrap_or(false)
}

/// Commits on `origin/main` that HEAD lacks. Zero when unknown.
pub fn behind_main(repo: &Path) -> u32 {
    git(repo, &["rev-list", "--count", "HEAD..origin/main"])
        .and_then(|s| s.parse().ok())
        .unwrap_or(0)
}

/// Short commit SHA of `HEAD` in `repo`.
pub fn head_sha(repo: &Path) -> Option<String> {
    git(repo, &["rev-parse", "--short=12", "HEAD"]).filter(|s| !s.is_empty())
}

/// Short commit SHA of `origin/main` in `repo`.
pub fn remote_sha(repo: &Path) -> Option<String> {
    git(repo, &["rev-parse", "--short=12", "origin/main"]).filter(|s| !s.is_empty())
}

/// Extract `version = "x.y.z"` from a `Cargo.toml` or `pyproject.toml` string.
pub fn parse_manifest_version(text: &str) -> Option<String> {
    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("version") {
            let rest = rest.trim_start();
            if let Some(rest) = rest.strip_prefix('=') {
                let val = rest.trim().trim_matches('"').trim_matches('\'');
                if !val.is_empty() && val.chars().next().is_some_and(|c| c.is_ascii_digit()) {
                    return Some(val.to_string());
                }
            }
        }
    }
    None
}

/// Version declared in the local checkout's `hydra-stdio/Cargo.toml` or `pyproject.toml`.
pub fn local_manifest_version(repo: &Path) -> Option<String> {
    for rel in ["hydra-stdio/Cargo.toml", "hydra-desktop-rs/Cargo.toml", "pyproject.toml"] {
        if let Ok(raw) = std::fs::read_to_string(repo.join(rel)) {
            if let Some(v) = parse_manifest_version(&raw) {
                return Some(v);
            }
        }
    }
    None
}

/// Version declared on `origin/main` in GitHub.
pub fn remote_manifest_version(repo: &Path) -> Option<String> {
    for spec in [
        "origin/main:hydra-stdio/Cargo.toml",
        "origin/main:hydra-desktop-rs/Cargo.toml",
        "origin/main:pyproject.toml",
    ] {
        if let Some(raw) = git(repo, &["show", spec]) {
            if let Some(v) = parse_manifest_version(&raw) {
                return Some(v);
            }
        }
    }
    None
}

/// True when the current process is the real `Hydra` or `hydra-tui` binary (not a cargo test runner).
pub fn is_production_binary() -> bool {
    let Ok(exe) = std::env::current_exe() else {
        return false;
    };
    let path_str = exe.to_string_lossy().to_lowercase();
    if path_str.contains("\\deps\\") || path_str.contains("/deps/") {
        return false;
    }
    let stem = exe
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    matches!(stem.as_str(), "hydra" | "hydra-desktop" | "hydra-tui")
}

/// Candidate local install directories where `Hydra.exe` / `hydra-tui.exe` live.
pub fn install_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    for var in ["USERPROFILE", "HOME"] {
        if let Ok(home) = std::env::var(var) {
            let hydra_dir = PathBuf::from(home).join("Hydra");
            if hydra_dir.is_dir() && !dirs.contains(&hydra_dir) {
                dirs.push(hydra_dir);
            }
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let lower = exe.to_string_lossy().to_lowercase();
        if !lower.contains("\\target\\") && !lower.contains("/target/") {
            if let Some(parent) = exe.parent() {
                let p = parent.to_path_buf();
                if !dirs.contains(&p) {
                    dirs.push(p);
                }
            }
        }
    }
    dirs
}

/// Remove `.old.exe` files staged during a previous live binary replacement.
pub fn cleanup_old_binaries() {
    for dir in install_dirs() {
        for name in ["Hydra.old.exe", "hydra-tui.old.exe", "Hydra.new.exe", "hydra-tui.new.exe"] {
            let p = dir.join(name);
            if p.exists() {
                let _ = std::fs::remove_file(p);
            }
        }
    }
}

/// Atomically install `src` binary to `dst`, renaming any running `dst` to `.old.exe` on Windows.
/// Returns `Ok(true)` if `dst` was updated, or `Ok(false)` if `dst` was already byte-identical.
pub fn install_binary(src: &Path, dst: &Path) -> Result<bool, String> {
    if !src.is_file() {
        return Err(format!("missing source binary: {}", src.display()));
    }
    let src_bytes = std::fs::read(src).map_err(|e| format!("read {}: {e}", src.display()))?;
    if dst.is_file() {
        if let Ok(dst_bytes) = std::fs::read(dst) {
            if src_bytes == dst_bytes {
                return Ok(false);
            }
        }
    }
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
    }
    let new_path = dst.with_extension("new.exe");
    let old_path = dst.with_extension("old.exe");
    std::fs::write(&new_path, &src_bytes).map_err(|e| format!("write {}: {e}", new_path.display()))?;
    if dst.exists() {
        let _ = std::fs::remove_file(&old_path);
        if std::fs::rename(dst, &old_path).is_err() {
            // If rename failed, try direct overwrite
            std::fs::copy(&new_path, dst).map_err(|e| format!("copy {}: {e}", dst.display()))?;
            let _ = std::fs::remove_file(&new_path);
            return Ok(true);
        }
    }
    if let Err(e) = std::fs::rename(&new_path, dst) {
        // Restore old binary if rename of new failed
        let _ = std::fs::rename(&old_path, dst);
        let _ = std::fs::remove_file(&new_path);
        return Err(format!("install {}: {e}", dst.display()));
    }
    let _ = std::fs::remove_file(&old_path);
    Ok(true)
}

/// Rebuild release binaries (when `rebuild` is true) and sync them to local install directories.
pub fn sync_local_install(repo: &Path, rebuild: bool) -> Result<Vec<String>, String> {
    let desktop_manifest = repo.join("hydra-desktop-rs").join("Cargo.toml");
    let tui_manifest = repo.join("hydra-tui-rs").join("Cargo.toml");
    if rebuild {
        for manifest in [&desktop_manifest, &tui_manifest] {
            if manifest.is_file() {
                let out = hidden_cmd("cargo")
                    .args(["build", "--release", "--quiet", "--manifest-path"])
                    .arg(manifest)
                    .current_dir(repo)
                    .output()
                    .map_err(|e| format!("cargo build {}: {e}", manifest.display()))?;
                if !out.status.success() {
                    let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
                    return Err(format!("cargo build failed: {err}"));
                }
            }
        }
    }
    let bin_pairs = if cfg!(windows) {
        vec![
            (
                repo.join("hydra-desktop-rs/target/release/Hydra.exe"),
                "Hydra.exe",
            ),
            (
                repo.join("hydra-tui-rs/target/release/hydra-tui.exe"),
                "hydra-tui.exe",
            ),
        ]
    } else {
        vec![
            (repo.join("hydra-desktop-rs/target/release/Hydra"), "Hydra"),
            (
                repo.join("hydra-tui-rs/target/release/hydra-tui"),
                "hydra-tui",
            ),
        ]
    };
    let mut updated = Vec::new();
    for dir in install_dirs() {
        for (src, name) in &bin_pairs {
            if src.is_file() {
                let dst = dir.join(name);
                if install_binary(src, &dst)? && !updated.contains(&name.to_string()) {
                    updated.push((*name).to_string());
                }
            }
        }
        let secondary_repo = dir.join("repo");
        if secondary_repo != repo
            && secondary_repo.join(".git").exists()
            && tree_clean(&secondary_repo)
            && git(&secondary_repo, &["branch", "--show-current"]).as_deref() == Some("main")
        {
            let _ = git(&secondary_repo, &["fetch", "--quiet", "origin", "main"]);
            let _ = git(&secondary_repo, &["pull", "--ff-only", "--quiet", "origin", "main"]);
        }
    }
    Ok(updated)
}

/// True when `BUILD_SHA` and `repo_sha` refer to the same commit prefix.
pub fn sha_matches(a: &str, b: &str) -> bool {
    let a = a.trim();
    let b = b.trim();
    if a.is_empty() || b.is_empty() || a == "unknown" || b == "unknown" {
        return false;
    }
    a.starts_with(b) || b.starts_with(a)
}

/// One launch check. Returns a user-facing line only when state changed or
/// an update waits on the user; silence means nothing to report.
pub fn check_once(repo: &Path) -> Option<String> {
    cleanup_old_binaries();
    if disabled() || !repo.join(".git").exists() {
        return None;
    }
    let branch = git(repo, &["branch", "--show-current"])?;
    if branch != "main" {
        let sha = head_sha(repo).unwrap_or_default();
        let short = &sha[..sha.len().min(7)];
        set_status_line(format!("v{BUILD_VERSION} ({short}) · branch {branch}"));
        return None;
    }
    if git(repo, &["fetch", "--quiet", "origin", "main"]).is_none() {
        set_status_line(format!("{} · offline (local git)", build_stamp()));
        return None;
    }
    let remote_ver = remote_manifest_version(repo).unwrap_or_else(|| BUILD_VERSION.to_string());
    let behind = behind_main(repo);
    if behind > 0 {
        let ver_tag = if remote_ver != BUILD_VERSION {
            format!("v{BUILD_VERSION} -> v{remote_ver}, ")
        } else {
            String::new()
        };
        if !tree_clean(repo) {
            let msg = format!(
                "update available: {ver_tag}{behind} commit(s) on origin/main; working tree has local changes, so nothing was pulled"
            );
            set_status_line(format!("{} · {behind} commit(s) behind origin/main (dirty tree)", build_stamp()));
            return Some(msg);
        }
        if git(repo, &["pull", "--ff-only", "--quiet", "origin", "main"]).is_none() {
            let msg = format!(
                "update available: {ver_tag}{behind} commit(s) on origin/main; fast-forward failed, so nothing was pulled"
            );
            set_status_line(format!("{} · {behind} commit(s) behind origin/main", build_stamp()));
            return Some(msg);
        }
        let sha = git(repo, &["rev-parse", "--short", "HEAD"]).unwrap_or_default();
        let new_ver = local_manifest_version(repo).unwrap_or(remote_ver);
        let mut bins_note = String::new();
        if is_production_binary() {
            if let Ok(bins) = sync_local_install(repo, true) {
                if !bins.is_empty() {
                    bins_note = format!(", {} rebuilt & synced", bins.join(" + "));
                }
            }
        }
        set_status_line(format!("v{new_ver} ({sha}) · updated from GitHub origin/main"));
        return Some(format!(
            "updated to v{new_ver} ({sha}, {behind} commit(s) pulled{bins_note}); restart Hydra to apply interface changes"
        ));
    }

    // Repo is at origin/main (behind == 0). Verify that the installed binary matches repo HEAD.
    let current_head = head_sha(repo).unwrap_or_default();
    let short_head = &current_head[..current_head.len().min(7)];
    let local_ver = local_manifest_version(repo).unwrap_or_else(|| BUILD_VERSION.to_string());
    if is_production_binary() {
        let code_drifted = !current_head.is_empty() && !sha_matches(BUILD_SHA, &current_head);
        if let Ok(bins) = sync_local_install(repo, code_drifted) {
            if !bins.is_empty() {
                set_status_line(format!("v{local_ver} ({short_head}) · binaries synced to HEAD"));
                return Some(format!(
                    "synced local install ({}) to v{local_ver} ({short_head}); restart Hydra to run updated build",
                    bins.join(" + ")
                ));
            }
        }
    }
    set_status_line(format!("v{local_ver} ({short_head}) · synced with GitHub origin/main"));
    None
}

/// Explicit on-demand check (e.g. `/update` command). Always returns a human-readable status line.
pub fn check_now() -> String {
    let Some(repo) = crate::repo_dir() else {
        return format!("{} · no git checkout found", build_stamp());
    };
    let path = Path::new(&repo);
    if let Some(changed_msg) = check_once(path) {
        return changed_msg;
    }
    status_line()
}

/// Spawn the launch check against the discovered checkout. The receiver
/// yields at most one line and then closes.
pub fn spawn_launch_check() -> Receiver<String> {
    spawn_launch_check_with(|| {})
}

/// Same as `spawn_launch_check`, invoking `on_message` when an update message is emitted.
pub fn spawn_launch_check_with(on_message: impl Fn() + Send + 'static) -> Receiver<String> {
    let (tx, rx): (Sender<String>, Receiver<String>) = channel();
    std::thread::spawn(move || {
        if let Some(repo) = crate::repo_dir() {
            if let Some(line) = check_once(Path::new(&repo)) {
                let _ = tx.send(line);
                on_message();
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
            assert!(
                hidden_cmd("git")
                    .arg("-C")
                    .arg(&dir)
                    .args(args)
                    .env("GIT_AUTHOR_NAME", "t")
                    .env("GIT_AUTHOR_EMAIL", "t@t")
                    .env("GIT_COMMITTER_NAME", "t")
                    .env("GIT_COMMITTER_EMAIL", "t@t")
                    .status()
                    .unwrap()
                    .success(),
                "git {args:?}"
            );
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

    #[test]
    fn fast_forwards_from_remote_and_checks_manifest_version() {
        let origin = temp_repo("origin");
        let clone = std::env::temp_dir().join(format!("hydra-update-test-{}-clone", std::process::id()));
        let _ = std::fs::remove_dir_all(&clone);
        assert!(hidden_cmd("git")
            .args(["clone", "--quiet", &origin.display().to_string(), &clone.display().to_string()])
            .status()
            .unwrap()
            .success());
        // No new commits on origin/main -> check_once returns None and records synced status
        std::env::remove_var("HYDRA_NO_AUTO_UPDATE");
        assert_eq!(check_once(&clone), None);
        assert!(status_line().contains("synced with GitHub origin/main"), "{}", status_line());

        // Advance origin/main with a version bump
        std::fs::create_dir_all(origin.join("hydra-stdio")).unwrap();
        std::fs::write(
            origin.join("hydra-stdio/Cargo.toml"),
            "[package]\nname = \"hydra-stdio\"\nversion = \"9.9.9\"\n",
        )
        .unwrap();
        for args in [&["add", "."][..], &["commit", "--quiet", "-m", "bump to 9.9.9"]] {
            assert!(hidden_cmd("git")
                .arg("-C")
                .arg(&origin)
                .args(args)
                .env("GIT_AUTHOR_NAME", "t")
                .env("GIT_AUTHOR_EMAIL", "t@t")
                .env("GIT_COMMITTER_NAME", "t")
                .env("GIT_COMMITTER_EMAIL", "t@t")
                .status()
                .unwrap()
                .success());
        }

        // Dirty clone refuses to overwrite local work
        std::fs::write(clone.join("a.txt"), "local edit\n").unwrap();
        let dirty_msg = check_once(&clone).expect("reports waiting update on dirty tree");
        assert!(dirty_msg.contains("9.9.9") && dirty_msg.contains("local changes"), "{dirty_msg}");

        // Clean clone pulls --ff-only automatically
        assert!(hidden_cmd("git").arg("-C").arg(&clone).args(["checkout", "--", "a.txt"]).status().unwrap().success());
        let pulled_msg = check_once(&clone).expect("pulls update on clean tree");
        assert!(pulled_msg.contains("updated to v9.9.9") && pulled_msg.contains("1 commit(s) pulled"), "{pulled_msg}");
        assert_eq!(local_manifest_version(&clone).as_deref(), Some("9.9.9"));

        let _ = std::fs::remove_dir_all(&origin);
        let _ = std::fs::remove_dir_all(&clone);
    }

    #[test]
    fn atomic_binary_install_replaces_target_and_skips_identical() {
        let dir = std::env::temp_dir().join(format!("hydra-update-test-{}-bin", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("src.exe");
        let dst = dir.join("sub/Hydra.exe");
        std::fs::write(&src, b"binary-v1").unwrap();
        assert_eq!(install_binary(&src, &dst).unwrap(), true);
        assert_eq!(std::fs::read(&dst).unwrap(), b"binary-v1");
        // Identical bytes -> no-op
        assert_eq!(install_binary(&src, &dst).unwrap(), false);
        // Updated bytes -> atomic replace
        std::fs::write(&src, b"binary-v2").unwrap();
        assert_eq!(install_binary(&src, &dst).unwrap(), true);
        assert_eq!(std::fs::read(&dst).unwrap(), b"binary-v2");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
