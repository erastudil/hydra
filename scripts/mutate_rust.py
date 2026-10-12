#!/usr/bin/env python3
"""Mutation pass for the Rust model checks: each mutation changes one behaviour and the named cargo tests must then FAIL.
usage: mutate_rust.py [name ...]   (no names = all). Slow: run it in the background."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS = os.path.join(ROOT, "hydra-desktop-rs")
M = "src/model.rs"
MUTATIONS = [
    ("token goes to the active thread", M, "Ev::Token { turn, text } => {\n                if let Some(sid) = self.run_session(&turn) {", "Ev::Token { turn, text } => {\n                let _ = &turn;\n                if let Some(sid) = Some(self.sessions[self.active].id) {", ["--test", "concurrency"]),
    ("approval attached to the active thread", M, "if let Some(sid) = self.run_session(&turn) {\n                    self.approvals.push(Approval { sid, turn, id, tool, args });", "if let Some(sid) = Some(self.sessions[self.active].id) {\n                    self.approvals.push(Approval { sid, turn, id, tool, args });", ["--test", "concurrency"]),
    ("done finishes every run", M, "self.runs.retain(|_, s| *s != sid);", "self.runs.clear();", ["--test", "concurrency"]),
    ("diff goes to the active thread", M, "if let Some(s) = self.session_mut(sid) {\n                        s.diffs.push(DiffItem { path, diff });", "if let Some(s) = self.sessions.get_mut(self.active) {\n                        s.diffs.push(DiffItem { path, diff });", ["--lib"]),
    ("running thread can be closed", M, "if i >= self.sessions.len() || self.is_running(self.sessions[i].id) {", "if i >= self.sessions.len() {", ["--lib"]),
    ("projects not deduped", M, "let id = match self.projects.iter().find(|q| q.path == canon) {\n            Some(q) => q.id,\n            None => self.add_project_unchecked(&canon),\n        };", "let id = self.add_project_unchecked(&canon);", ["--lib"]),
    ("background finish not unread", M, "if background && sid != active_id {", "if false && sid != active_id {", ["--lib"]),
    ("state backup ignored", M, "let loaded = try_load(p).or_else(|| try_load(&p.with_extension(\"bak\")));", "let loaded = try_load(p);", ["--lib"]),
 ("shift+enter sends", "src/app.rs", "!i.modifiers.shift && !i.modifiers.alt && i.consume_key", "i.consume_key", ["--test", "ui"]),
    ("idle window keeps repainting", "src/app.rs", "if animating { 50 } else { 2000 }", "if animating { 50 } else { 50 }", ["--test", "ui"]),
    ("new threads bypass approvals", M, "    \"manual\".into()\n}", "    \"accept_edits\".into()\n}", ["--test", "ui"]),
    ("chat not virtualized", "src/app.rs", "if !near || (mi + 1 < n && known.is_none() && y > viewport.max.y + 500.0) {", "if false {", ["--test", "ui"]),
    ("long messages never collapse", "src/app.rs", "    if expanded {\n        return (text, 0);\n    }", "    if true || expanded {\n        return (text, 0);\n    }", ["--test", "ui"]),
    ("cut stream kept silently", "../hydra_cli/agent.py", "    if not saw_done and not state.get(\"finished\"):", "    if False:", ["--test", "stdio_integration"]),
    ("env file accepts newlines", "src/secrets.rs", "if value.contains('\\n') || value.contains('\\r') {", "if false {", ["--lib"]),
]


def main(names):
    bad = 0
    for name, rel, old, new, cargo in MUTATIONS:
        if names and name not in names:
            continue
        path = os.path.join(RS, rel)
        src = open(path, encoding="utf-8").read()
        if old not in src:
            print(f"MUTATION NOT APPLIED: {name}", flush=True)
            bad += 1
            continue
        open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
        try:
            r = subprocess.run(["cargo", "test", *cargo], cwd=RS, capture_output=True, text=True, timeout=300)
            code = r.returncode
        except subprocess.TimeoutExpired:
            code = 1
        finally:
            open(path, "w", encoding="utf-8").write(src)
        if code == 0:
            print(f"SURVIVED (bad): {name}", flush=True)
            bad += 1
        else:
            print(f"killed: {name}", flush=True)
    print("mutation pass (rust model): " + ("FAILED" if bad else "all killed"), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
