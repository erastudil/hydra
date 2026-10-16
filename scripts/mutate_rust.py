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
    ("idle window keeps repainting", "src/app.rs", "pub const IDLE_REPAINT_MS: u64 = 30_000;", "pub const IDLE_REPAINT_MS: u64 = 50;", ["--test", "ui"]),
    ("respawn keeps dead handle", "src/app.rs", "            self.be = None;\n            self.deaths.retain", "            self.deaths.retain", ["--test", "ui_real"]),
    ("respawn without backoff", "src/app.rs", "pub const RESPAWN_BACKOFF: &[f64] = &[0.5, 2.0, 5.0];", "pub const RESPAWN_BACKOFF: &[f64] = &[0.0, 0.0, 0.0];", ["--test", "ui_real"]),
    ("stall timer counts from app start", "src/app.rs", "let since = if be_started >= 0.0 { be_started } else { app_started };", "let since = app_started;", ["--test", "ui"]),
    ("no re-request after respawn", "src/app.rs", "                self.m.list_sessions(be);\n", "", ["--test", "ui_real"]),
    ("cut reply not reloaded", "src/app.rs", "                self.m.reload_cut_threads(be);\n", "", ["--test", "ui_real"]),
    ("cut reply not marked unsaved", M, "self.fail_bubble(sid, &format!(\"{UNSAVED} ({})\", self.notice));", "self.fail_bubble(sid, \"agent process ended\");", ["--test", "ui_real"]),
    ("busy elsewhere not shown", M, "self.notice = format!(\"{HELD_ELSEWHERE} {message}\");", "{}", ["--test", "ui_real"]),
    ("thread ids collide across windows", M, "let stamp = format!(\"{}-{:x}\", now.as_secs(), (now.subsec_nanos() ^ std::process::id().rotate_left(16)) & 0xffffff);", "let stamp = now.as_secs();", ["--test", "ui_real"]),
    ("new threads bypass approvals", M, "    \"manual\".into()\n}", "    \"accept_edits\".into()\n}", ["--test", "ui"]),
    ("chat not virtualized", "src/app.rs", "if !near || (mi + 1 < n && known.is_none() && y > viewport.max.y + 500.0) {", "if false {", ["--test", "ui"]),
    ("long messages never collapse", "src/app.rs", "    if expanded {\n        return (text, 0);\n    }", "    if true || expanded {\n        return (text, 0);\n    }", ["--test", "ui"]),
    ("cut stream kept silently", "../hydra_cli/agent.py", "    if not saw_done and not state.get(\"finished\"):", "    if False:", ["--test", "stdio_integration"]),
    ("env file accepts newlines", "src/secrets.rs", "if value.contains('\\n') || value.contains('\\r') {", "if false {", ["--lib"]),
    ("picked skills not sent", "src/backend.rs", "        if !skills.is_empty() {\n            m[\"skills\"] = json!(skills);", "        if false {\n            m[\"skills\"] = json!(skills);", ["--test", "stdio_integration", "skills_"]),
    ("retry notice dropped", M, "m.actions.push(format!(\"↻ retry {attempt}/{of} in {wait:.1}s: {reason}\"));", "let _ = (attempt, of, wait, &reason);", ["--test", "stdio_integration", "retry"]),
    ("task_update ignored", M, "                    self.tasks.insert(id, task);", "                    let _ = (id, task);", ["--test", "stdio_integration", "background_task"]),
    ("diff yanks a pinned tab", M, "if self.sessions[self.active].id == sid && !self.tab_pinned && matches!", "if self.sessions[self.active].id == sid && matches!", ["--test", "stdio_integration", "pinned"]),
    ("mcp toggle ignored", "src/backend.rs", "\"server\": server, \"enabled\": enabled}", "\"server\": server, \"enabled\": true}", ["--test", "stdio_integration", "mcp_status"]),
    ("token journal off", M, "                    self.journal(sid, &text);\n", "", ["--lib", "journal"]),
    ("slash command sent as a message", "src/app.rs", "        if let Some(cmd) = slash_cmd(text.trim()) {", "        if let Some(cmd) = None::<Cmd>.filter(|_| text.is_empty()) {", ["--test", "ui", "slash"]),
    ("steer only queues", "src/app.rs", "        if let Some(be) = &mut self.be {\n            self.m.stop(be);\n        }\n        self.toast = Some((\"Steering", "        if let Some(be) = None::<&mut Backend> {\n            self.m.stop(be);\n        }\n        self.toast = Some((\"Steering", ["--test", "ui", "steer"]),
    ("background finish silent", M, "            if self.notice.is_empty() {\n                self.notice = format!(", "            if false {\n                self.notice = format!(", ["--lib", "background_thread"]),
    ("search ignores case", M, "        let q = q.trim().to_lowercase();", "        let q = q.trim().to_string();", ["--test", "ui", "search"]),
    ("D23 approval shows full args", "src/app.rs", "                let (summary, body) = approval_preview(&a.args);", "                let (summary, body) = (String::new(), serde_json::to_string_pretty(&a.args).unwrap_or_default());", ["--test", "ui_real", "d23"]),
    ("D23 no keyboard answer", "src/app.rs", "                if ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Enter)) {\n                    decision = Some(\"allow\");", "                if false {\n                    decision = Some(\"allow\");", ["--test", "ui_real", "d23"]),
    ("D20 rules not reloaded", M, "                self.rules_stale = true;\n                self.rules.0.clear();", "                self.rules_stale = false;", ["--test", "ui_real", "d20"]),
    ("D27 skip leaves no trace", M, "            \"skip\" => \"skipped by you\",", "            \"skip\" => return,", ["--test", "ui_real", "d27"]),
    ("D22 settings not validated", "src/app.rs", "                if let Err(e) = secrets::validate(name, v) {", "                if let Err(e) = Ok::<(), String>(()) {", ["--test", "ui_real", "d22"]),
    ("D14 runner default shown", "src/app.rs", "            let max = if state == \"IDLE\" { self.agent_steps as u64 }", "            let max = if false { self.agent_steps as u64 }", ["--test", "ui_real", "d14"]),
    ("D25 chunks counted as characters", "../hydra_cli/agent.py", "{len(''.join(state['content']))} characters", "{len(state['content'])} characters", ["--test", "stdio_integration", "cut_off"]),
    ("D16 long tab names when narrow", "src/app.rs", "let tight = (width - 17.0 - 12.0) / n - 4.0 < 96.0;", "let tight = false;", ["--test", "ui", "d16"]),
    ("D18 error without model label", M, "    if msg.contains(label) {", "    if true {", ["--lib", "errors_name"]),
    ("unchanged fields resent", M, "if last.as_ref().map(|l| l[i] == now_meta[i]).unwrap_or(false) { None }", "if false { None }", ["--test", "concurrency"]),
    ("D08 diff rows reserve item spacing", "src/app.rs", "                    ui.spacing_mut().item_spacing.y = 0.0;\n                    ScrollArea::vertical().id_salt((\"diffrows\", &key)).max_height(460.0).auto_shrink([false, true]).show_rows(ui, row_h, lines.len(), |ui, range| {\n", "                    ScrollArea::vertical().id_salt((\"diffrows\", &key)).max_height(460.0).auto_shrink([false, true]).show_rows(ui, row_h, lines.len(), |ui, range| {\n                        ui.spacing_mut().item_spacing.y = 0.0;\n", ["--test", "ui", "a_long_diff"]),
    ("header chips not fitted", "src/app.rs", "let mut room = ui.available_rect_before_wrap().right() - brand_right - gap;", "let mut room = f32::INFINITY;", ["--test", "ui", "header_chips"]),
    ("D19 no library check", "src/main.rs", "if let Some(msg) = hydra_desktop::syslibs::preflight() {", "if let Some(msg) = None::<String> {", ["--test", "syslibs_real"]),
    ("D19 xkb-x11 not checked", "src/syslibs.rs", "    lib(\"libxkbcommon-x11.so.0\", \"keyboard input on X11\", \"libxkbcommon-x11-0\", \"libxkbcommon-x11\"),\n", "", ["--test", "syslibs_real"]),
    ("D19 wayland gaps hide x11", "src/syslibs.rs", "        if w.is_empty() || !set(env(\"DISPLAY\")) {", "        if true {", ["--lib", "syslibs"]),
    ("composer status row only while busy", "src/app.rs", "        {\n            const BRAILLE", "        if recording || waiting_approval || thinking {\n            const BRAILLE", ["--test", "ui_real", "the_composer"]),
    ("a color hard-coded outside the tokens", "src/app.rs", '"PAUSED" => PAUSED,', '"PAUSED" => Color32::from_rgb(234, 179, 8),', ["--test", "tokens_only"]),
    ("composer layout follows the pulse stroke", "src/app.rs", ".stroke(Stroke::new(stroke_max, Color32::TRANSPARENT))", ".stroke(Stroke::new(stroke_w, border_col))", ["--test", "ui_real", "the_composer"]),
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
