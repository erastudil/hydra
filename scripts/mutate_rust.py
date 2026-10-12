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
    ("state backup ignored", M, "let loaded = main.or_else(|| try_load(&p.with_extension(\"bak\")));", "let loaded = main;", ["--lib"]),
 ("shift+enter sends", "src/app.rs", "!i.modifiers.shift && !i.modifiers.alt && i.consume_key", "i.consume_key", ["--test", "ui"]),
    ("idle window keeps repainting", "src/app.rs", "if animating { 50 } else { 2000 }", "if animating { 50 } else { 50 }", ["--test", "ui"]),
    ("new threads bypass approvals", M, "    \"manual\".into()\n}", "    \"accept_edits\".into()\n}", ["--test", "ui"]),
    ("chat not virtualized", "src/app.rs", "if !near || (mi + 1 < n && known.is_none() && y > viewport.max.y + 500.0) {", "if false {", ["--test", "ui"]),
    ("long messages never collapse", "src/app.rs", "    if expanded {\n        return (text, 0);\n    }", "    if true || expanded {\n        return (text, 0);\n    }", ["--test", "ui"]),
    ("cut stream kept silently", "../hydra_cli/agent.py", "    if not saw_done and not state.get(\"finished\"):", "    if False:", ["--test", "stdio_integration"]),
    ("env file accepts newlines", "src/secrets.rs", "if value.contains('\\n') || value.contains('\\r') {", "if false {", ["--lib"]),
    ("picked skills not sent", "src/backend.rs", "        if !skills.is_empty() {\n            m[\"skills\"] = json!(skills);", "        if false {\n            m[\"skills\"] = json!(skills);", ["--test", "stdio_integration", "skills_"]),
    ("retry notice dropped", M, "m.actions.push(format!(\"↻ retry {attempt}/{of} in {wait:.1}s: {reason}\"));", "let _ = (attempt, of, wait, &reason);", ["--test", "stdio_integration", "retry"]),
    ("task_update ignored", M, "                    self.tasks.insert(id, task);", "                    let _ = (id, task);", ["--test", "stdio_integration", "background_task"]),
    ("diff yanks a pinned tab", M, "if self.sessions[self.active].id == sid && !self.tab_pinned {", "if self.sessions[self.active].id == sid {", ["--test", "stdio_integration", "pinned"]),
    ("mcp toggle ignored", "src/backend.rs", "\"server\": server, \"enabled\": enabled}", "\"server\": server, \"enabled\": true}", ["--test", "stdio_integration", "mcp_status"]),
    ("token journal off", M, "                    self.journal(sid, &text);\n", "", ["--lib", "journal"]),
    ("unchanged fields resent", M, "if last.as_ref().map(|l| l[i] == now_meta[i]).unwrap_or(false) { None }", "if false { None }", ["--test", "concurrency"]),
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
