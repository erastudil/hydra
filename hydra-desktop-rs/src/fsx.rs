//! Project files: tree listing, file index, fuzzy finder, safe reads and writes, `@file` mentions.

use std::path::{Component, Path, PathBuf};

pub const MAX_VIEW_BYTES: u64 = 1_000_000;
const SKIP_DIRS: &[&str] = &[".git", "node_modules", "target", "__pycache__", ".hydra", ".venv", "dist"];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Entry {
    pub name: String,
    pub dir: bool,
}

/// Join `rel` under `root`, refusing anything that would leave it (.., absolute paths, drive prefixes).
pub fn safe_join(root: &Path, rel: &str) -> Result<PathBuf, String> {
    let p = Path::new(rel);
    if p.is_absolute() {
        return Err("path must be relative to the project".into());
    }
    let mut out = root.to_path_buf();
    for c in p.components() {
        match c {
            Component::Normal(n) => out.push(n),
            Component::CurDir => {}
            _ => return Err("path leaves the project".into()),
        }
    }
    Ok(out)
}

/// One directory level, folders first then files, A to Z. Hidden `.git` and build folders are left out.
pub fn list_dir(root: &Path, rel: &str) -> Vec<Entry> {
    let Ok(dir) = safe_join(root, rel) else { return vec![] };
    let mut v: Vec<Entry> = std::fs::read_dir(dir)
        .map(|rd| {
            rd.flatten()
                .map(|e| Entry { name: e.file_name().to_string_lossy().to_string(), dir: e.path().is_dir() })
                .filter(|e| !(e.dir && SKIP_DIRS.contains(&e.name.as_str())))
                .collect()
        })
        .unwrap_or_default();
    v.sort_by(|a, b| b.dir.cmp(&a.dir).then(a.name.to_lowercase().cmp(&b.name.to_lowercase())));
    v
}

/// Every file in the project as a relative path with `/` separators, capped. Uses git when available so
/// ignored files stay out; walks the folder otherwise.
pub fn index(root: &Path, cap: usize) -> Vec<String> {
    if let Ok(out) = crate::proc::command("git").args(["ls-files", "-co", "--exclude-standard", "-z"]).current_dir(root).output() {
        if out.status.success() {
            let mut v: Vec<String> = String::from_utf8_lossy(&out.stdout).split('\0').filter(|s| !s.is_empty()).map(String::from).collect();
            v.truncate(cap);
            return v;
        }
    }
    let mut out = vec![];
    let mut stack = vec![root.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&d) else { continue };
        for e in rd.flatten() {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            if p.is_dir() {
                if !SKIP_DIRS.contains(&name.as_str()) {
                    stack.push(p);
                }
            } else if let Ok(rel) = p.strip_prefix(root) {
                out.push(rel.to_string_lossy().replace('\\', "/"));
                if out.len() >= cap {
                    return out;
                }
            }
        }
    }
    out.sort();
    out
}

/// Subsequence match score, higher is better. None when `query` is not a subsequence of `path`.
/// Rewards consecutive runs, matches at word starts and in the file name, and shorter paths.
pub fn fuzzy_score(query: &str, path: &str) -> Option<i64> {
    if query.is_empty() {
        return Some(0);
    }
    let q: Vec<char> = query.to_lowercase().chars().collect();
    let p: Vec<char> = path.chars().collect();
    let pl: Vec<char> = path.to_lowercase().chars().collect();
    let name_start = path.rfind('/').map(|i| path[..=i].chars().count()).unwrap_or(0);
    let (mut qi, mut score, mut prev): (usize, i64, Option<usize>) = (0, 0, None);
    for (i, c) in pl.iter().enumerate() {
        if qi < q.len() && *c == q[qi] {
            score += 10;
            if prev == Some(i.wrapping_sub(1)) && i > 0 {
                score += 15;
            }
            if i == 0 || matches!(p[i - 1], '/' | '_' | '-' | '.' | ' ') {
                score += 12;
            }
            if i >= name_start {
                score += 8;
            }
            prev = Some(i);
            qi += 1;
        }
    }
    if qi < q.len() {
        return None;
    }
    Some(score - (p.len() as i64) / 8)
}

pub fn fuzzy<'a>(query: &str, files: &'a [String], limit: usize) -> Vec<&'a str> {
    let mut scored: Vec<(i64, &str)> = files.iter().filter_map(|f| fuzzy_score(query, f).map(|s| (s, f.as_str()))).collect();
    scored.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.len().cmp(&b.1.len())).then(a.1.cmp(b.1)));
    scored.into_iter().take(limit).map(|s| s.1).collect()
}

/// Read a text file for the viewer. Refuses binary files and files over 1 MB with a plain message.
pub fn read_text(path: &Path) -> Result<String, String> {
    let meta = std::fs::metadata(path).map_err(|e| e.to_string())?;
    if meta.len() > MAX_VIEW_BYTES {
        return Err(format!("file is {} KB, too large to open here", meta.len() / 1000));
    }
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    if bytes.contains(&0) {
        return Err("binary file".into());
    }
    String::from_utf8(bytes).map_err(|_| "not valid UTF-8 text".into())
}

/// Save text inside the project. Atomic, creates parents, refuses paths outside `root`.
pub fn write_text(root: &Path, rel: &str, text: &str) -> Result<(), String> {
    let path = safe_join(root, rel)?;
    if let Some(d) = path.parent() {
        std::fs::create_dir_all(d).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension("hydra-tmp");
    std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

/// Replace `@relative/path` tokens that name real files with the file text appended to the prompt.
/// Returns the new prompt and the paths that were attached.
pub fn expand_mentions(prompt: &str, root: &Path) -> (String, Vec<String>) {
    let mut used: Vec<String> = vec![];
    for tok in prompt.split_whitespace() {
        if let Some(rel) = tok.strip_prefix('@') {
            let rel = rel.trim_end_matches(|c: char| ",.;:)]}\"'".contains(c));
            if rel.is_empty() || used.iter().any(|u| u == rel) {
                continue;
            }
            // inside the project by relative path, or an absolute path the user typed or dropped
            let p = if Path::new(rel).is_absolute() { Ok(PathBuf::from(rel)) } else { safe_join(root, rel) };
            if let Ok(p) = p {
                if p.is_file() {
                    used.push(rel.to_string());
                }
            }
        }
    }
    if used.is_empty() {
        return (prompt.to_string(), used);
    }
    let mut out = prompt.to_string();
    for rel in &used {
        let full = if Path::new(rel).is_absolute() { Some(PathBuf::from(rel)) } else { safe_join(root, rel).ok() };
        let body = full.and_then(|p| read_text(&p).ok()).unwrap_or_else(|| "(could not read this file)".into());
        out.push_str(&format!("\n\n--- attached file: {rel} ---\n{body}\n--- end of {rel} ---"));
    }
    (out, used)
}

/// The `@partial` word being typed at the end of `text`, for the mention popup.
pub fn mention_prefix(text: &str) -> Option<&str> {
    let word = text.rsplit(|c: char| c.is_whitespace()).next()?;
    word.strip_prefix('@')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("hydra-fsx-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn safe_join_refuses_escapes() {
        let root = Path::new("/p");
        assert!(safe_join(root, "a/b.txt").is_ok());
        assert!(safe_join(root, "../x").is_err());
        assert!(safe_join(root, "a/../../x").is_err());
        assert!(safe_join(root, "/etc/passwd").is_err());
    }

    #[test]
    fn tree_lists_folders_first_and_hides_build_dirs() {
        let d = tmp("tree");
        std::fs::create_dir_all(d.join("src")).unwrap();
        std::fs::create_dir_all(d.join("target")).unwrap();
        std::fs::create_dir_all(d.join(".git")).unwrap();
        std::fs::write(d.join("b.txt"), "x").unwrap();
        std::fs::write(d.join("A.md"), "x").unwrap();
        let names: Vec<_> = list_dir(&d, "").into_iter().map(|e| (e.name, e.dir)).collect();
        assert_eq!(names, vec![("src".into(), true), ("A.md".into(), false), ("b.txt".into(), false)]);
        let _ = std::fs::remove_dir_all(d);
    }

    #[test]
    fn index_respects_gitignore_in_a_repo_and_walks_otherwise() {
        let d = tmp("index");
        std::fs::write(d.join("keep.rs"), "x").unwrap();
        let walked = index(&d, 100);
        assert_eq!(walked, vec!["keep.rs".to_string()]);
        let ok = |a: &[&str]| crate::proc::command("git").args(a).current_dir(&d).output().map(|o| o.status.success()).unwrap_or(false);
        if ok(&["init", "-q"]) {
            std::fs::write(d.join(".gitignore"), "secret.txt\n").unwrap();
            std::fs::write(d.join("secret.txt"), "x").unwrap();
            let mut v = index(&d, 100);
            v.sort();
            assert_eq!(v, vec![".gitignore".to_string(), "keep.rs".to_string()]);
        }
        let _ = std::fs::remove_dir_all(d);
    }

    #[test]
    fn fuzzy_prefers_name_matches_and_consecutive_runs() {
        let files: Vec<String> = ["src/app.rs", "src/model.rs", "docs/PLAN.md", "src/backend/mod.rs", "tests/stdio_integration.rs"].iter().map(|s| s.to_string()).collect();
        assert_eq!(fuzzy("model", &files, 5)[0], "src/model.rs");
        assert_eq!(fuzzy("plan", &files, 5)[0], "docs/PLAN.md");
        assert_eq!(fuzzy("sint", &files, 5)[0], "tests/stdio_integration.rs");
        assert!(fuzzy("zzz", &files, 5).is_empty());
        assert_eq!(fuzzy("", &files, 3).len(), 3);
    }

    #[test]
    fn read_refuses_binary_and_huge_files() {
        let d = tmp("read");
        std::fs::write(d.join("t.txt"), "héllo").unwrap();
        std::fs::write(d.join("b.bin"), [0u8, 1, 2]).unwrap();
        std::fs::write(d.join("big.txt"), vec![b'a'; 1_100_000]).unwrap();
        assert_eq!(read_text(&d.join("t.txt")).unwrap(), "héllo");
        assert_eq!(read_text(&d.join("b.bin")).unwrap_err(), "binary file");
        assert!(read_text(&d.join("big.txt")).unwrap_err().contains("too large"));
        let _ = std::fs::remove_dir_all(d);
    }

    #[test]
    fn write_is_confined_and_atomic() {
        let d = tmp("write");
        write_text(&d, "a/b/c.txt", "one").unwrap();
        write_text(&d, "a/b/c.txt", "two").unwrap();
        assert_eq!(std::fs::read_to_string(d.join("a/b/c.txt")).unwrap(), "two");
        assert!(write_text(&d, "../escape.txt", "x").is_err());
        assert!(!d.parent().unwrap().join("escape.txt").exists());
        assert!(!d.join("a/b/c.hydra-tmp").exists());
        let _ = std::fs::remove_dir_all(d);
    }

    #[test]
    fn mentions_attach_real_files_only() {
        let d = tmp("mention");
        std::fs::write(d.join("notes.txt"), "remember the milk").unwrap();
        let (p, used) = expand_mentions("look at @notes.txt, and @missing.txt and @../x", &d);
        assert_eq!(used, vec!["notes.txt".to_string()]);
        assert!(p.contains("remember the milk") && p.starts_with("look at @notes.txt"));
        let (p2, u2) = expand_mentions("no mentions here", &d);
        assert_eq!((p2.as_str(), u2.len()), ("no mentions here", 0));
        let outside = tmp("mention-out");
        std::fs::write(outside.join("o.txt"), "from elsewhere").unwrap();
        let (p3, u3) = expand_mentions(&format!("read @{}", outside.join("o.txt").display()), &d);
        assert_eq!(u3.len(), 1);
        assert!(p3.contains("from elsewhere"));
        let _ = std::fs::remove_dir_all(outside);
        assert_eq!(mention_prefix("see @src/ap"), Some("src/ap"));
        assert_eq!(mention_prefix("see src"), None);
        let _ = std::fs::remove_dir_all(d);
    }
}
