//! Per-project working state behind the Files and Changes panes: tree cache, open file, git snapshot, diff selection,
//! commit box. All logic lives here so it is tested without a window.

use crate::fsx::{self, Entry};
use crate::git::{self, Status};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

pub struct OpenFile {
    pub rel: String,
    pub text: String,
    pub saved: String,
    pub error: Option<String>,
}

impl OpenFile {
    pub fn dirty(&self) -> bool {
        self.text != self.saved
    }
}

struct Snapshot {
    root: PathBuf,
    status: Option<Status>,
    index: Vec<String>,
}

pub struct Work {
    pub root: PathBuf,
    pub is_repo: bool,
    pub status: Option<Status>,
    pub index: Vec<String>,
    pub expanded: HashSet<String>,
    pub tree: HashMap<String, Vec<Entry>>,
    pub open: Option<OpenFile>,
    pub selected: Option<String>,
    pub selected_staged: bool,
    pub diff: String,
    pub commit_msg: String,
    pub note: String,
    rx: Receiver<Snapshot>,
    tx: std::sync::mpsc::Sender<Snapshot>,
    last: Option<Instant>,
    inflight: bool,
    wake: std::sync::Arc<dyn Fn() + Send + Sync>,
}

impl Work {
    pub fn new(root: PathBuf, wake: impl Fn() + Send + Sync + 'static) -> Work {
        let (tx, rx) = channel();
        let mut w = Work {
            root,
            is_repo: false,
            status: None,
            index: vec![],
            expanded: HashSet::new(),
            tree: HashMap::new(),
            open: None,
            selected: None,
            selected_staged: false,
            diff: String::new(),
            commit_msg: String::new(),
            note: String::new(),
            rx,
            tx,
            last: None,
            inflight: false,
            wake: std::sync::Arc::new(wake),
        };
        w.refresh(true);
        w
    }

    pub fn set_root(&mut self, root: PathBuf) {
        if root == self.root {
            return;
        }
        self.root = root;
        self.status = None;
        self.index.clear();
        self.expanded.clear();
        self.tree.clear();
        self.open = None;
        self.selected = None;
        self.diff.clear();
        self.note.clear();
        self.refresh(true);
    }

    /// Ask for a fresh git status and file index on a worker thread. Throttled unless `force`.
    pub fn refresh(&mut self, force: bool) {
        if self.inflight {
            return;
        }
        if !force && self.last.map(|t| t.elapsed() < Duration::from_secs(3)).unwrap_or(false) {
            return;
        }
        self.inflight = true;
        self.last = Some(Instant::now());
        let (root, tx, wake) = (self.root.clone(), self.tx.clone(), self.wake.clone());
        std::thread::spawn(move || {
            let status = if git::is_repo(&root) { git::status(&root).ok() } else { None };
            let index = fsx::index(&root, 20_000);
            let _ = tx.send(Snapshot { root, status, index });
            wake();
        });
    }

    /// Fold in a finished refresh. Returns true when something changed.
    pub fn poll(&mut self) -> bool {
        let mut changed = false;
        while let Ok(s) = self.rx.try_recv() {
            self.inflight = false;
            if s.root != self.root {
                continue;
            }
            self.is_repo = s.status.is_some();
            self.status = s.status;
            self.index = s.index;
            self.tree.clear();
            changed = true;
            if let Some(sel) = self.selected.clone() {
                if self.status.as_ref().map(|st| st.changes.iter().any(|c| c.path == sel)).unwrap_or(false) {
                    self.diff = git::diff_file(&self.root, &sel, self.selected_staged);
                } else {
                    self.selected = None;
                    self.diff.clear();
                }
            }
        }
        changed
    }

    pub fn entries(&mut self, rel: &str) -> Vec<Entry> {
        if !self.tree.contains_key(rel) {
            let e = fsx::list_dir(&self.root, rel);
            self.tree.insert(rel.to_string(), e);
        }
        self.tree[rel].clone()
    }

    pub fn toggle(&mut self, rel: &str) {
        if !self.expanded.remove(rel) {
            self.expanded.insert(rel.to_string());
        }
    }

    pub fn open_file(&mut self, rel: &str) {
        let res = fsx::safe_join(&self.root, rel).and_then(|p| fsx::read_text(&p));
        self.open = Some(match res {
            Ok(t) => OpenFile { rel: rel.into(), saved: t.clone(), text: t, error: None },
            Err(e) => OpenFile { rel: rel.into(), text: String::new(), saved: String::new(), error: Some(e) },
        });
    }

    pub fn save_open(&mut self) -> Result<(), String> {
        let Some(f) = &mut self.open else { return Err("no file open".into()) };
        if f.error.is_some() {
            return Err("this file cannot be edited here".into());
        }
        fsx::write_text(&self.root, &f.rel, &f.text)?;
        f.saved = f.text.clone();
        self.refresh(true);
        Ok(())
    }

    pub fn select_change(&mut self, path: &str, staged: bool) {
        self.selected = Some(path.into());
        self.selected_staged = staged;
        self.diff = git::diff_file(&self.root, path, staged);
    }

    pub fn hunks(&self) -> Vec<git::Hunk> {
        git::parse_hunks(&self.diff).1
    }

    fn after_change(&mut self) {
        if let Some(sel) = self.selected.clone() {
            self.diff = git::diff_file(&self.root, &sel, self.selected_staged);
        }
        self.refresh(true);
    }

    pub fn reject_hunk(&mut self, i: usize) -> Result<(), String> {
        if self.selected_staged {
            return Err("unstage the file first, then reject hunks in the working tree".into());
        }
        git::reject_hunk(&self.root, &self.diff, i)?;
        self.note = "hunk rejected".into();
        self.after_change();
        Ok(())
    }

    pub fn stage_hunk(&mut self, i: usize) -> Result<(), String> {
        git::stage_hunk(&self.root, &self.diff, i)?;
        self.note = "hunk staged".into();
        self.after_change();
        Ok(())
    }

    pub fn stage(&mut self, paths: &[String]) -> Result<(), String> {
        git::stage(&self.root, paths)?;
        self.after_change();
        Ok(())
    }

    pub fn unstage(&mut self, paths: &[String]) -> Result<(), String> {
        git::unstage(&self.root, paths)?;
        self.after_change();
        Ok(())
    }

    pub fn commit(&mut self) -> Result<String, String> {
        let sha = git::commit(&self.root, &self.commit_msg)?;
        self.commit_msg.clear();
        self.note = format!("committed {sha}");
        self.selected = None;
        self.diff.clear();
        self.refresh(true);
        Ok(sha)
    }

    pub fn push(&mut self) -> Result<String, String> {
        let r = git::push(&self.root)?;
        self.refresh(true);
        Ok(r)
    }

    /// A plain commit message from the staged files, so the box is never blank: "Update a.rs, b.rs and 2 more files".
    pub fn draft_message(&self) -> String {
        let staged: Vec<&str> = self.status.iter().flat_map(|s| s.changes.iter()).filter(|c| c.x != '.' && c.x != '?').map(|c| c.path.as_str()).collect();
        let verb = if self.status.iter().flat_map(|s| s.changes.iter()).filter(|c| c.x != '.' && c.x != '?').all(|c| c.x == 'A') && !staged.is_empty() { "Add" } else { "Update" };
        match staged.len() {
            0 => String::new(),
            1 => format!("{verb} {}", staged[0]),
            2 => format!("{verb} {} and {}", staged[0], staged[1]),
            n => format!("{verb} {}, {} and {} more files", staged[0], staged[1], n - 2),
        }
    }
}
