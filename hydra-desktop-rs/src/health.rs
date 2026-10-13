//! First-run and startup checks. Problems are shown inside the app; there is no console to print to.

use crate::backend::{python_candidates, repo_dir};
use crate::model::state_dir;
use crate::proc;

#[derive(Clone, Debug)]
pub struct Check {
    pub name: &'static str,
    pub ok: bool,
    pub detail: String,
    pub fatal: bool,
}

pub fn run() -> Vec<Check> {
    static STATIC_CHECKS: std::sync::OnceLock<(bool, String, bool, String)> = std::sync::OnceLock::new();
    let (py_ok, py_detail, git_ok, git_detail) = STATIC_CHECKS
        .get_or_init(|| {
            let mut py_detail = String::from("no interpreter found");
            let mut py_ok = false;
            for (prog, lead) in python_candidates() {
                match proc::command(&prog).args(&lead).arg("--version").output() {
                    Ok(o) if o.status.success() => {
                        let v = String::from_utf8_lossy(if o.stdout.is_empty() { &o.stderr } else { &o.stdout }).trim().to_string();
                        py_detail = format!("{v} ({prog})");
                        py_ok = true;
                        break;
                    }
                    Ok(o) => py_detail = format!("{prog} exited with {}", o.status),
                    Err(e) => py_detail = format!("{prog}: {e}"),
                }
            }
            let git = proc::command("git").arg("--version").output();
            let (git_ok, git_detail) = match git {
                Ok(o) if o.status.success() => (true, String::from_utf8_lossy(&o.stdout).trim().into()),
                _ => (false, "git not found on PATH; git panel, worktrees and rewind are unavailable until it is installed".into()),
            };
            (py_ok, py_detail, git_ok, git_detail)
        })
        .clone();
    let mut out = vec![];
    out.push(Check { name: "Agent runtime (Python)", ok: py_ok, detail: py_detail, fatal: true });
    let repo = repo_dir();
    out.push(Check {
        name: "Agent code (hydra_cli)",
        ok: repo.is_some(),
        detail: repo.unwrap_or_else(|| "not found next to Hydra.exe (expected an app\\hydra_cli folder)".into()),
        fatal: true,
    });
    out.push(Check { name: "Git", ok: git_ok, detail: git_detail, fatal: false });
    // state dir writable
    let dir = state_dir();
    let probe = dir.join(".write-test");
    let writable = std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(&probe, b"x")).is_ok();
    let _ = std::fs::remove_file(&probe);
    out.push(Check { name: "Saved sessions folder", ok: writable, detail: dir.display().to_string(), fatal: true });
    // provider keys (names only, never values)
    let file = crate::secrets::env_path();
    let found: Vec<_> = crate::secrets::PROVIDERS.iter().filter(|k| crate::secrets::is_set(k.0, &file)).collect();
    out.push(Check {
        name: "Model access",
        ok: !found.is_empty(),
        detail: if found.is_empty() { "no provider key or local endpoint yet; open Settings (Ctrl+,) and add one".into() } else { format!("{} configured", found.len()) },
        fatal: false,
    });
    out
}

pub fn fatal_problems(checks: &[Check]) -> Vec<&Check> {
    checks.iter().filter(|c| c.fatal && !c.ok).collect()
}
