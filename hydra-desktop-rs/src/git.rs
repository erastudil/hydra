//! Git for the project: status, diff, stage, commit, push (guarded), per-hunk reject, checkpoints and rewind, worktrees.
//! Every call goes through `proc::command` so no console window appears. Pushes obey the same rules as the agent gate:
//! never to main or master, never forced.

use crate::proc;
use std::path::Path;
use std::process::Output;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Change {
    pub path: String,
    pub x: char, // index state
    pub y: char, // worktree state
}

#[derive(Clone, Debug, Default)]
pub struct Status {
    pub branch: String,
    pub upstream: Option<String>,
    pub ahead: u32,
    pub behind: u32,
    pub changes: Vec<Change>,
}

fn run(root: &Path, args: &[&str]) -> Result<Output, String> {
    proc::command("git").args(args).current_dir(root).env("GIT_TERMINAL_PROMPT", "0").output().map_err(|e| format!("git: {e}"))
}

fn ok(root: &Path, args: &[&str]) -> Result<String, String> {
    let o = run(root, args)?;
    if o.status.success() {
        Ok(String::from_utf8_lossy(&o.stdout).to_string())
    } else {
        let e = String::from_utf8_lossy(&o.stderr).trim().to_string();
        Err(if e.is_empty() { String::from_utf8_lossy(&o.stdout).trim().to_string() } else { e })
    }
}

pub fn is_repo(root: &Path) -> bool {
    ok(root, &["rev-parse", "--is-inside-work-tree"]).map(|s| s.trim() == "true").unwrap_or(false)
}

pub fn init(root: &Path) -> Result<(), String> {
    ok(root, &["init", "-q"]).map(|_| ())
}

pub fn status(root: &Path) -> Result<Status, String> {
    let out = ok(root, &["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"])?;
    let mut st = Status::default();
    let mut parts = out.split('\0');
    while let Some(rec) = parts.next() {
        if let Some(h) = rec.strip_prefix("# branch.head ") {
            st.branch = h.to_string();
        } else if let Some(u) = rec.strip_prefix("# branch.upstream ") {
            st.upstream = Some(u.to_string());
        } else if let Some(ab) = rec.strip_prefix("# branch.ab ") {
            let mut it = ab.split_whitespace();
            st.ahead = it.next().and_then(|a| a.trim_start_matches('+').parse().ok()).unwrap_or(0);
            st.behind = it.next().and_then(|b| b.trim_start_matches('-').parse().ok()).unwrap_or(0);
        } else if let Some(r) = rec.strip_prefix("1 ") {
            let f: Vec<&str> = r.splitn(8, ' ').collect();
            if f.len() == 8 {
                let xy: Vec<char> = f[0].chars().collect();
                st.changes.push(Change { path: f[7].to_string(), x: xy[0], y: xy[1] });
            }
        } else if let Some(r) = rec.strip_prefix("2 ") {
            let f: Vec<&str> = r.splitn(9, ' ').collect();
            if f.len() == 9 {
                let xy: Vec<char> = f[0].chars().collect();
                st.changes.push(Change { path: f[8].to_string(), x: xy[0], y: xy[1] });
            }
            parts.next(); // original path of a rename
        } else if let Some(p) = rec.strip_prefix("? ") {
            st.changes.push(Change { path: p.to_string(), x: '?', y: '?' });
        } else if let Some(r) = rec.strip_prefix("u ") {
            let f: Vec<&str> = r.splitn(10, ' ').collect();
            if f.len() == 10 {
                st.changes.push(Change { path: f[9].to_string(), x: 'U', y: 'U' });
            }
        }
    }
    Ok(st)
}

/// Unified diff of one file. Untracked files diff against nothing.
pub fn diff_file(root: &Path, path: &str, staged: bool) -> String {
    let untracked = status(root).map(|s| s.changes.iter().any(|c| c.path == path && c.x == '?')).unwrap_or(false);
    let args: Vec<&str> = if untracked {
        vec!["diff", "--no-index", "--no-color", "--", if cfg!(windows) { "NUL" } else { "/dev/null" }, path]
    } else if staged {
        vec!["diff", "--cached", "--no-color", "--", path]
    } else {
        vec!["diff", "--no-color", "--", path]
    };
    run(root, &args).map(|o| String::from_utf8_lossy(&o.stdout).to_string()).unwrap_or_default()
}

pub fn stage(root: &Path, paths: &[String]) -> Result<(), String> {
    let mut a = vec!["add", "--"];
    a.extend(paths.iter().map(|s| s.as_str()));
    ok(root, &a).map(|_| ())
}

pub fn unstage(root: &Path, paths: &[String]) -> Result<(), String> {
    let mut a = vec!["reset", "-q", "--"];
    a.extend(paths.iter().map(|s| s.as_str()));
    ok(root, &a).map(|_| ())
}

/// Commit what is staged. An empty message or nothing staged is an error, never a silent no-op.
pub fn commit(root: &Path, message: &str) -> Result<String, String> {
    if message.trim().is_empty() {
        return Err("commit message is empty".into());
    }
    let staged = ok(root, &["diff", "--cached", "--name-only"])?;
    if staged.trim().is_empty() {
        return Err("nothing is staged".into());
    }
    ok(root, &["commit", "-q", "-m", message.trim()])?;
    Ok(ok(root, &["rev-parse", "--short", "HEAD"])?.trim().to_string())
}

pub fn branches(root: &Path) -> Vec<String> {
    ok(root, &["for-each-ref", "--format=%(refname:short)", "refs/heads"]).map(|s| s.lines().map(String::from).collect()).unwrap_or_default()
}

pub fn create_branch(root: &Path, name: &str) -> Result<(), String> {
    check_branch_name(name)?;
    ok(root, &["switch", "-c", name]).map(|_| ())
}

pub fn switch_branch(root: &Path, name: &str) -> Result<(), String> {
    check_branch_name(name)?;
    ok(root, &["switch", name]).map(|_| ())
}

fn check_branch_name(name: &str) -> Result<(), String> {
    if name.trim().is_empty() || name.starts_with('-') {
        return Err("invalid branch name".into());
    }
    ok(Path::new("."), &["check-ref-format", "--branch", name]).map(|_| ()).map_err(|_| format!("invalid branch name: {name}"))
}

pub fn log(root: &Path, n: usize) -> Vec<(String, String)> {
    let count = format!("-{n}");
    ok(root, &["log", &count, "--format=%h%x09%s"]).map(|s| s.lines().filter_map(|l| l.split_once('\t').map(|(a, b)| (a.to_string(), b.to_string()))).collect()).unwrap_or_default()
}

/// Push the current branch. Refuses main and master and never passes a force flag, like the agent gate.
pub fn push(root: &Path) -> Result<String, String> {
    let st = status(root)?;
    if st.branch == "main" || st.branch == "master" {
        return Err(format!("refusing to push {}: work lands on a branch and goes through a PR", st.branch));
    }
    if st.branch.starts_with('(') {
        return Err("detached HEAD: switch to a branch first".into());
    }
    ok(root, &["push", "--set-upstream", "origin", &st.branch])
}

// ------------------------------------------------------------------ hunks

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Hunk {
    pub header: String,
    pub lines: Vec<String>,
}

/// Split a unified diff of one file into (file header lines, hunks).
pub fn parse_hunks(diff: &str) -> (String, Vec<Hunk>) {
    let mut head = String::new();
    let mut hunks: Vec<Hunk> = vec![];
    for line in diff.lines() {
        if line.starts_with("@@") {
            hunks.push(Hunk { header: line.to_string(), lines: vec![] });
        } else if let Some(h) = hunks.last_mut() {
            h.lines.push(line.to_string());
        } else {
            head.push_str(line);
            head.push('\n');
        }
    }
    (head, hunks)
}

fn patch_text(head: &str, h: &Hunk) -> String {
    let mut p = String::from(head);
    p.push_str(&h.header);
    p.push('\n');
    for l in &h.lines {
        p.push_str(l);
        p.push('\n');
    }
    p
}

fn apply(root: &Path, patch: &str, args: &[&str]) -> Result<(), String> {
    use std::io::Write;
    let mut cmd = proc::command("git");
    cmd.arg("apply").args(args).current_dir(root).stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    child.stdin.take().ok_or("no stdin")?.write_all(patch.as_bytes()).map_err(|e| e.to_string())?;
    let o = child.wait_with_output().map_err(|e| e.to_string())?;
    if o.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&o.stderr).trim().to_string())
    }
}

/// Undo one hunk in the working tree by applying it in reverse. Other hunks of the file are untouched.
pub fn reject_hunk(root: &Path, diff: &str, index: usize) -> Result<(), String> {
    let (head, hunks) = parse_hunks(diff);
    let h = hunks.get(index).ok_or("no such hunk")?;
    apply(root, &patch_text(&head, h), &["-R", "--recount", "--whitespace=nowarn"])
}

/// Stage one hunk.
pub fn stage_hunk(root: &Path, diff: &str, index: usize) -> Result<(), String> {
    let (head, hunks) = parse_hunks(diff);
    let h = hunks.get(index).ok_or("no such hunk")?;
    apply(root, &patch_text(&head, h), &["--cached", "--recount", "--whitespace=nowarn"])
}

// ------------------------------------------------------------------ checkpoints

fn temp_index(root: &Path) -> std::path::PathBuf {
    let git_dir = ok(root, &["rev-parse", "--absolute-git-dir"]).map(|s| s.trim().to_string()).unwrap_or_default();
    Path::new(&git_dir).join(format!("hydra-index-{}", std::process::id()))
}

fn with_index(root: &Path, idx: &Path, args: &[&str]) -> Result<String, String> {
    let o = proc::command("git").args(args).current_dir(root).env("GIT_INDEX_FILE", idx).output().map_err(|e| e.to_string())?;
    if o.status.success() {
        Ok(String::from_utf8_lossy(&o.stdout).to_string())
    } else {
        Err(String::from_utf8_lossy(&o.stderr).trim().to_string())
    }
}

/// Snapshot the working tree (tracked and untracked, not ignored) as a commit on `refs/hydra/checkpoints/<name>`
/// without touching the real index, the working tree or any branch. Returns the commit id.
pub fn checkpoint(root: &Path, name: &str) -> Result<String, String> {
    if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || "-_./".contains(c)) {
        return Err("bad checkpoint name".into());
    }
    let idx = temp_index(root);
    let _ = std::fs::remove_file(&idx);
    let result = (|| {
        if ok(root, &["rev-parse", "--verify", "-q", "HEAD"]).is_ok() {
            with_index(root, &idx, &["read-tree", "HEAD"])?;
        }
        with_index(root, &idx, &["add", "-A", "--", "."])?;
        let tree = with_index(root, &idx, &["write-tree"])?.trim().to_string();
        let mut args = vec!["-c", "user.name=Hydra", "-c", "user.email=hydra@localhost", "commit-tree", &tree, "-m", "hydra checkpoint"];
        let parent = ok(root, &["rev-parse", "--verify", "-q", "HEAD"]).ok().map(|s| s.trim().to_string());
        if let Some(p) = &parent {
            args.push("-p");
            args.push(p);
        }
        let sha = ok(root, &args)?.trim().to_string();
        ok(root, &["update-ref", &format!("refs/hydra/checkpoints/{name}"), &sha])?;
        Ok(sha)
    })();
    let _ = std::fs::remove_file(&idx);
    result
}

/// Put the working tree back exactly as it was at `sha`: restores changed files, recreates deleted ones, removes files
/// created since. Ignored files are left alone. The branch and its commits are not touched.
pub fn restore(root: &Path, sha: &str) -> Result<(), String> {
    let idx = temp_index(root);
    let _ = std::fs::remove_file(&idx);
    let result = (|| {
        with_index(root, &idx, &["read-tree", sha])?;
        let keep: std::collections::HashSet<String> = ok(root, &["ls-tree", "-r", "--name-only", "-z", sha])?.split('\0').filter(|s| !s.is_empty()).map(String::from).collect();
        let now = ok(root, &["ls-files", "-co", "--exclude-standard", "-z"])?;
        for f in now.split('\0').filter(|s| !s.is_empty()) {
            if !keep.contains(f) {
                let _ = std::fs::remove_file(root.join(f));
            }
        }
        with_index(root, &idx, &["checkout-index", "-a", "-f"])?;
        Ok(())
    })();
    let _ = std::fs::remove_file(&idx);
    result
}

// ------------------------------------------------------------------ worktrees

/// `git worktree add -b <branch> <path>`. The path is created under `<repo>/.hydra/worktrees`.
pub fn worktree_add(root: &Path, slug: &str) -> Result<std::path::PathBuf, String> {
    let slug: String = slug.chars().map(|c| if c.is_ascii_alphanumeric() || c == '-' || c == '_' { c } else { '-' }).collect();
    if slug.is_empty() {
        return Err("empty worktree name".into());
    }
    let path = root.join(".hydra").join("worktrees").join(&slug);
    if path.exists() {
        return Err(format!("worktree already exists: {}", path.display()));
    }
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    ok(root, &["worktree", "add", "-b", &format!("hydra/{slug}"), &path.display().to_string()])?;
    // keep the folder out of status
    let exclude = root.join(".git").join("info").join("exclude");
    if exclude.parent().map(|p| p.is_dir()).unwrap_or(false) {
        let cur = std::fs::read_to_string(&exclude).unwrap_or_default();
        if !cur.lines().any(|l| l.trim() == ".hydra/") {
            let _ = std::fs::write(&exclude, format!("{cur}{}.hydra/\n", if cur.ends_with('\n') || cur.is_empty() { "" } else { "\n" }));
        }
    }
    Ok(path)
}

pub fn worktree_remove(root: &Path, path: &Path) -> Result<(), String> {
    ok(root, &["worktree", "remove", "--force", &path.display().to_string()]).map(|_| ())
}
