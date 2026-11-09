"""
Unit tests for Hydra Desktop Multi-Session Branching Checkpoint Manager.
Verifies DAG session tree forks, branch merging, parent-child provenance,
and fast binary and JSON disk serialization.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import os
import tempfile
import pytest
from desktop.session_checkpoint_manager import (
    MergeStrategy,
    SessionCheckpoint,
    SessionCheckpointManager,
    compute_checkpoint_hash,
    get_checkpoint_manager,
    reset_checkpoint_manager,
)


def test_checkpoint_hash_and_serialization():
    parents = ["chk_001", "chk_002"]
    state = {"model": "qwen-3.8", "temperature": 0.7}
    msgs = [{"role": "user", "content": "analyze code"}]

    h1 = compute_checkpoint_hash(parents, 1000.0, 1, state, msgs)
    h2 = compute_checkpoint_hash(parents, 1000.0, 1, state, msgs)
    assert h1 == h2
    assert len(h1) == 64

    chk = SessionCheckpoint(
        checkpoint_id="chk_test",
        session_id="session_1",
        branch_name="main",
        parent_ids=parents,
        timestamp=1000.0,
        step_index=1,
        state_diff=state,
        messages=msgs,
        author="tester",
        tags=["verified"],
    )

    data = chk.to_dict()
    assert data["checkpoint_id"] == "chk_test"
    assert data["checksum"] == h1

    reconstructed = SessionCheckpoint.from_dict(data)
    assert reconstructed.checkpoint_id == chk.checkpoint_id
    assert reconstructed.checksum == chk.checksum
    assert reconstructed.state_diff == state


def test_linear_checkpoint_progression():
    mgr = SessionCheckpointManager(default_session_id="sess_alpha")
    assert mgr.current_branch == "main"

    c1 = mgr.create_checkpoint(state_diff={"step": 1}, messages=[{"role": "system", "text": "start"}])
    assert c1.step_index == 0
    assert len(c1.parent_ids) == 0

    c2 = mgr.create_checkpoint(state_diff={"step": 2}, messages=[{"role": "user", "text": "continue"}])
    assert c2.step_index == 1
    assert c2.parent_ids == [c1.checkpoint_id]

    tips = mgr.get_branch_tips()
    assert tips["main"] == c2.checkpoint_id

    history = mgr.get_history("main")
    assert len(history) == 2
    assert history[0].checkpoint_id == c1.checkpoint_id
    assert history[1].checkpoint_id == c2.checkpoint_id


def test_forking_branch():
    mgr = SessionCheckpointManager()
    root = mgr.create_checkpoint(state_diff={"env": "prod"})
    step1 = mgr.create_checkpoint(state_diff={"env": "prod", "run": 1})

    fork_chk = mgr.fork_branch("feature/speedup", source_branch_or_id="main")
    assert fork_chk.branch_name == "feature/speedup"
    assert fork_chk.parent_ids == [step1.checkpoint_id]
    assert "fork" in fork_chk.tags

    assert mgr.current_branch == "feature/speedup"

    tips = mgr.get_branch_tips()
    assert "feature/speedup" in tips
    assert tips["feature/speedup"] == fork_chk.checkpoint_id


def test_lowest_common_ancestor_and_branch_merge():
    mgr = SessionCheckpointManager()
    c0 = mgr.create_checkpoint(state_diff={"base": True})

    # Branch A: dev_a
    fork_a = mgr.fork_branch("dev_a", source_branch_or_id="main")
    ca1 = mgr.create_checkpoint(
        state_diff={"branch_a_val": 10},
        messages=[{"msg": "a1"}],
        branch_name="dev_a",
    )

    # Branch B: dev_b
    fork_b = mgr.fork_branch("dev_b", source_branch_or_id="main")
    cb1 = mgr.create_checkpoint(
        state_diff={"branch_b_val": 20},
        messages=[{"msg": "b1"}],
        branch_name="dev_b",
    )

    lca = mgr.get_lowest_common_ancestor(ca1.checkpoint_id, cb1.checkpoint_id)
    assert lca == c0.checkpoint_id

    # 3-way merge dev_b into dev_a with UNION strategy
    merge_chk = mgr.merge_branches("dev_b", target_branch="dev_a", strategy=MergeStrategy.UNION)
    assert len(merge_chk.parent_ids) == 2
    assert ca1.checkpoint_id in merge_chk.parent_ids
    assert cb1.checkpoint_id in merge_chk.parent_ids
    assert merge_chk.state_diff.get("branch_a_val") == 10
    assert merge_chk.state_diff.get("branch_b_val") == 20
    assert len(merge_chk.messages) == 2

    # Fast-forward merge test
    mgr.fork_branch("feature/ff", source_branch_or_id="main")
    c_ff1 = mgr.create_checkpoint(state_diff={"ff": 1}, branch_name="feature/ff")
    ff_res = mgr.merge_branches("feature/ff", target_branch="main")
    assert ff_res.checkpoint_id == c_ff1.checkpoint_id


def test_provenance_tracking():
    mgr = SessionCheckpointManager()
    r = mgr.create_checkpoint(state_diff={"root": True})
    c1 = mgr.create_checkpoint(state_diff={"child": 1})
    c2 = mgr.create_checkpoint(state_diff={"child": 2})

    prov = mgr.get_provenance(c2.checkpoint_id)
    assert prov["checkpoint_id"] == c2.checkpoint_id
    assert prov["step_index"] == 2
    assert prov["ancestor_count"] == 2
    assert r.checkpoint_id in prov["root_ids"]


def test_disk_serialization_json_and_binary():
    mgr = SessionCheckpointManager()
    c1 = mgr.create_checkpoint(state_diff={"k1": "v1"}, tags=["first"])
    mgr.fork_branch("exp", source_branch_or_id="main")
    c2 = mgr.create_checkpoint(state_diff={"k2": "v2"}, branch_name="exp", tags=["second"])

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path = os.path.join(tmpdir, "session_dag.json")
        bin_path = os.path.join(tmpdir, "session_dag.bin")

        # JSON Roundtrip
        mgr.export_json(json_path)
        assert os.path.exists(json_path)

        mgr_json = SessionCheckpointManager()
        mgr_json.import_json(json_path)
        assert len(mgr_json.get_branch_tips()) == 2
        assert mgr_json.get_checkpoint(c1.checkpoint_id).tags == ["first"]
        assert mgr_json.get_checkpoint(c2.checkpoint_id).tags == ["second"]

        # Binary Roundtrip
        mgr.export_binary(bin_path)
        assert os.path.exists(bin_path)

        mgr_bin = SessionCheckpointManager()
        mgr_bin.import_binary(bin_path)
        assert len(mgr_bin.get_branch_tips()) == 2
        assert mgr_bin.get_checkpoint(c1.checkpoint_id).tags == ["first"]
        assert mgr_bin.get_checkpoint(c2.checkpoint_id).tags == ["second"]


def test_singleton_getter_and_reset():
    m1 = get_checkpoint_manager()
    m2 = get_checkpoint_manager()
    assert m1 is m2

    m3 = reset_checkpoint_manager()
    assert m3 is not m1
    assert get_checkpoint_manager() is m3


def test_shadow_snapshot_unchanged_tree_dedupe(tmp_path, monkeypatch):
    import io
    from hydra_cli.stdio_adapter import Adapter, _conversation_copy

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    sub = workspace / "sub"
    sub.mkdir()
    (sub / "beta.txt").write_text("beta\n", encoding="utf-8")

    adapter = Adapter(io.BytesIO(b""), io.StringIO())
    snap1 = adapter._snapshot(str(workspace), "turn=t1", "th_dedupe")
    assert snap1["ok"] is True
    assert snap1["id"] and len(snap1["id"]) == 40
    assert os.path.isfile(_conversation_copy("th_dedupe", snap1["id"]))

    snap2 = adapter._snapshot(str(workspace), "turn=t2", "th_dedupe")
    assert snap2["ok"] is True
    assert snap2["id"] == snap1["id"]
    assert os.path.isfile(_conversation_copy("th_dedupe", snap2["id"]))

    rev_count = adapter._git(str(workspace), "rev-list", "--count", "HEAD")
    assert rev_count.returncode == 0
    assert int(rev_count.stdout.decode("utf-8").strip()) == 1


def _count_git_objects(gitdir: str) -> int:
    objects_root = os.path.join(gitdir, "objects")
    if not os.path.isdir(objects_root):
        return 0
    count = 0
    for root, dirs, files in os.walk(objects_root):
        dirs[:] = [d for d in dirs if d not in ("info", "pack")]
        count += len(files)
    return count


def test_shadow_snapshot_staged_size_guard_skips_and_preserves_objects(tmp_path, monkeypatch):
    import io
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")

    adapter = Adapter(io.BytesIO(b""), io.StringIO())
    first = adapter._snapshot(str(workspace), "turn=t1", "th_size")
    assert first["ok"] is True

    gitdir = str(adapter._shadow_gitdir(str(workspace)))
    objects_before = _count_git_objects(gitdir)
    assert objects_before > 0

    monkeypatch.setenv("HYDRA_SHADOW_MAX_STAGE_MB", "0.001")
    (workspace / "large_file.bin").write_bytes(b"x" * 4096)
    untracked_dir = workspace / "untracked_dir"
    untracked_dir.mkdir()
    (untracked_dir / "nested_big.bin").write_bytes(b"y" * 2048)

    skipped = adapter._snapshot(str(workspace), "turn=t2", "th_size")
    assert skipped["ok"] is False
    assert skipped["id"] is None
    assert skipped["reason"] is not None
    assert "6144" in skipped["reason"]
    assert "large_file.bin" in skipped["reason"]
    assert "untracked_dir/nested_big.bin" in skipped["reason"]

    objects_after = _count_git_objects(gitdir)
    assert objects_after == objects_before
    head_now = adapter._git(str(workspace), "rev-parse", "HEAD").stdout.decode("utf-8").strip()
    assert head_now == first["id"]


def test_shadow_startup_gc_hygiene(tmp_path, monkeypatch):
    import io
    from hydra_cli import stdio_adapter
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.txt").write_text("payload\n" * 50, encoding="utf-8")

    seed_adapter = Adapter(io.BytesIO(b""), io.StringIO())
    snap = seed_adapter._snapshot(str(workspace), "turn=t1", "th_gc")
    assert snap["ok"] is True
    repo_dir = seed_adapter._shadow_gitdir(str(workspace))

    shadow_root = home_dir / ".hydra" / "shadow"
    inflight_dir = shadow_root / "inflight.git"
    inflight_dir.mkdir(parents=True, exist_ok=True)
    (inflight_dir / "dummy.bin").write_bytes(b"z" * 2048)
    (inflight_dir / "index.lock").write_bytes(b"")

    broken_dir = shadow_root / "broken.git"
    broken_dir.mkdir(parents=True, exist_ok=True)
    (broken_dir / "corrupt.bin").write_bytes(b"w" * 2048)

    monkeypatch.setattr(stdio_adapter, "SHADOW_GC_THRESHOLD_BYTES", 100)
    Adapter(io.BytesIO(b""), io.StringIO())

    gc_log = shadow_root / "gc.log"
    assert gc_log.is_file()
    lines = gc_log.read_text(encoding="utf-8").splitlines()
    repo_lines = [ln for ln in lines if ln.startswith(repo_dir.name + " ") and "gc_error" not in ln]
    assert len(repo_lines) == 1
    parts = repo_lines[0].split()
    assert parts[0] == repo_dir.name
    assert int(parts[1]) > 100
    assert int(parts[2]) > 0
    assert not any(ln.startswith("inflight.git ") for ln in lines)


def test_stdio_sessions_listing_shape(tmp_path, monkeypatch):
    import io
    import json
    from hydra_cli.stdio_adapter import Adapter, _save_meta

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    sessions_root = home_dir / ".hydra" / "sessions"
    sess_dir = sessions_root / "th_sess_1"
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "ledger.db").write_bytes(b"")
    _save_meta(
        "th_sess_1",
        {
            "cwd": str(workspace),
            "mode": "compact",
            "model": "qwen3-coder",
            "permission": "accept_edits",
            "title": "Session One",
        },
    )

    empty_dir = sessions_root / "th_no_ledger"
    empty_dir.mkdir(parents=True, exist_ok=True)

    out = io.StringIO()
    adapter = Adapter(io.BytesIO(b""), out)
    adapter._on_sessions({"type": "sessions", "id": "req_sessions"})

    msg = json.loads(out.getvalue().strip())
    assert msg["type"] == "session_list"
    assert msg["id"] == "req_sessions"
    assert len(msg["sessions"]) == 1
    entry = msg["sessions"][0]
    assert entry["thread"] == "th_sess_1"
    assert entry["cwd"] == str(workspace)
    assert entry["mode"] == "compact"
    assert entry["model"] == "qwen3-coder"
    assert entry["permission"] == "accept_edits"
    assert entry["title"] == "Session One"
    assert isinstance(entry["updated"], int)


def test_stdio_history_result_turns_and_meta(tmp_path, monkeypatch):
    import io
    import json
    from hydra_cli.context import SessionContextLedger
    from hydra_cli.stdio_adapter import Adapter, _save_meta

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    ledger = SessionContextLedger(session_id="th_hist_1")
    ledger.append_turn("user", "hello hydra")
    ledger.append_turn("assistant", "hello human")
    ledger.end_turn()

    meta = {
        "cwd": str(workspace),
        "mode": "recall",
        "model": "deepseek-v3",
        "permission": "manual",
        "title": "History Thread",
    }
    _save_meta("th_hist_1", meta)

    out = io.StringIO()
    adapter = Adapter(io.BytesIO(b""), out)
    adapter._on_history({"type": "history", "id": "req_hist", "thread": "th_hist_1"})

    msg = json.loads(out.getvalue().strip())
    assert msg["type"] == "history_result"
    assert msg["id"] == "req_hist"
    assert msg["thread"] == "th_hist_1"
    assert msg["turns"] == [
        {"role": "user", "content": "hello hydra"},
        {"role": "assistant", "content": "hello human"},
    ]
    assert msg["meta"] == meta


def test_stdio_turn_meta_restore_before_validation(tmp_path, monkeypatch):
    import io
    from hydra_cli.stdio_adapter import Adapter, _save_meta

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _save_meta(
        "th_restore",
        {
            "cwd": str(workspace),
            "mode": "compact",
            "model": "qwen3-coder",
            "permission": "accept_edits",
            "title": "Restored Thread",
        },
    )

    out = io.StringIO()
    adapter = Adapter(io.BytesIO(b""), out)
    captured = []

    def intercept_run_turn(t, turn_msg):
        captured.append((t, dict(turn_msg)))
        with adapter._state:
            if t.thread_lock is not None:
                t.thread_lock.__exit__(None, None, None)
            adapter._turns.pop(t.id, None)
            adapter._busy_threads.pop(t.thread, None)

    monkeypatch.setattr(adapter, "_run_turn", intercept_run_turn)

    adapter._on_turn({"type": "turn", "turn": "t_restore_1", "thread": "th_restore", "prompt": "continue work"})
    adapter._stop_turns()

    assert out.getvalue() == ""
    assert len(captured) == 1
    t_obj, filled = captured[0]
    assert filled["cwd"] == str(workspace)
    assert filled["model"] == "qwen3-coder"
    assert filled["permission"] == "accept_edits"
    assert filled["mode"] == "compact"
    assert t_obj.permission == "accept_edits"


def test_stdio_meta_roundtrip(tmp_path, monkeypatch):
    from hydra_cli.stdio_adapter import _load_meta, _save_meta

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert _load_meta("th_roundtrip") == {}

    expected = {
        "cwd": str(workspace),
        "mode": "sliding",
        "model": "gemini-pro",
        "permission": "plan",
        "title": "Roundtrip Title",
    }
    _save_meta("th_roundtrip", expected)
    loaded = _load_meta("th_roundtrip")

    assert loaded["cwd"] == expected["cwd"]
    assert loaded["mode"] == expected["mode"]
    assert loaded["model"] == expected["model"]
    assert loaded["permission"] == expected["permission"]
    assert loaded["title"] == expected["title"]


def _start_scripted_model_server(tool=None, tokens=("Step 1: inspect\n", "Step 2: verify")):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if tool and not any(m.get("role") == "tool" for m in body.get("messages", [])):
                args = json.dumps(tool["arguments"])
                chunks = [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {"name": tool["name"], "arguments": ""},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": args}}]}}]},
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in chunks:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for token in tokens:
                chunk = {"choices": [{"delta": {"content": token}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _run_turn_sync(adapter, out, msg):
    import json

    before = len(out.getvalue().splitlines())
    adapter._on_turn(msg)
    with adapter._state:
        workers = [t.worker for t in adapter._turns.values() if t.worker is not None]
    for w in workers:
        w.join(timeout=15)
    lines = out.getvalue().splitlines()[before:]
    return [json.loads(line) for line in lines if line.strip()]


def test_stdio_plan_turn_writes_artifact_and_sets_done_field(tmp_path, monkeypatch):
    from datetime import datetime
    import io
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    httpd, base_url = _start_scripted_model_server(tokens=("1. Read files\n", "2. Draft plan"))
    monkeypatch.setenv("LOCAL_AI_BASE", base_url)
    try:
        out = io.StringIO()
        adapter = Adapter(io.BytesIO(b""), out)
        events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_plan_1",
                "thread": "th_plan",
                "prompt": "make a plan",
                "cwd": str(workspace),
                "permission": "plan",
                "model": "local",
                "checkpoint": False,
            },
        )
        done = events[-1]
        assert done["type"] == "done"
        assert done["ok"] is True
        expected_path = workspace / ".hydra" / "plans" / "th_plan-turn-1.md"
        assert done.get("plan") == str(expected_path)
        assert "plan_error" not in done
        assert expected_path.is_file()

        content = expected_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert lines[0] == "thread: th_plan"
        assert lines[1] == "turn: t_plan_1"
        assert lines[2].startswith("timestamp: ")
        datetime.fromisoformat(lines[2].split("timestamp: ", 1)[1])
        assert lines[3] == f"cwd: {workspace}"
        assert "1. Read files\n2. Draft plan" in content

        # Second completed turn in the same thread writes turn-2 and failed write reports plan_error without failing turn
        events2 = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_plan_2",
                "thread": "th_plan",
                "prompt": "refine plan",
                "cwd": str(workspace),
                "permission": "plan",
                "model": "local",
                "checkpoint": False,
            },
        )
        done2 = events2[-1]
        expected_path2 = workspace / ".hydra" / "plans" / "th_plan-turn-2.md"
        assert done2["ok"] is True
        assert done2.get("plan") == str(expected_path2)
        assert expected_path2.is_file()

        def fail_write(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(adapter, "_write_plan_artifact", fail_write)
        events3 = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_plan_3",
                "thread": "th_plan",
                "prompt": "third plan",
                "cwd": str(workspace),
                "permission": "plan",
                "model": "local",
                "checkpoint": False,
            },
        )
        done3 = events3[-1]
        assert done3["ok"] is True
        assert "plan" not in done3
        assert done3.get("plan_error") == "disk full"
    finally:
        httpd.shutdown()


def test_stdio_non_plan_turn_writes_no_artifact(tmp_path, monkeypatch):
    import io
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    httpd, base_url = _start_scripted_model_server(tokens=("Normal reply",))
    monkeypatch.setenv("LOCAL_AI_BASE", base_url)
    try:
        out = io.StringIO()
        adapter = Adapter(io.BytesIO(b""), out)
        events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_exec_1",
                "thread": "th_exec",
                "prompt": "run normal turn",
                "cwd": str(workspace),
                "permission": "accept_edits",
                "model": "local",
                "checkpoint": False,
            },
        )
        done = events[-1]
        assert done["type"] == "done"
        assert done["ok"] is True
        assert "plan" not in done
        assert "plan_error" not in done
        assert not (workspace / ".hydra" / "plans").exists()
    finally:
        httpd.shutdown()


def test_stdio_plan_mode_denies_write_tool_call_regression(tmp_path, monkeypatch):
    import io
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    write_tool = {"name": "write_file", "arguments": {"path": "denied.txt", "content": "should not exist\n"}}
    httpd, base_url = _start_scripted_model_server(tool=write_tool, tokens=("Plan after write skip",))
    monkeypatch.setenv("LOCAL_AI_BASE", base_url)
    try:
        out = io.StringIO()
        adapter = Adapter(io.BytesIO(b""), out)
        events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_deny_1",
                "thread": "th_deny",
                "prompt": "try to write",
                "cwd": str(workspace),
                "permission": "plan",
                "model": "local",
                "checkpoint": False,
            },
        )
        auto = [e for e in events if e["type"] == "auto_decision"]
        assert len(auto) == 1
        assert auto[0]["tool"] == "write_file"
        assert auto[0]["decision"] == "skip"
        assert not any(e["type"] in ("approval_request", "tool_start") for e in events)
        assert not (workspace / "denied.txt").exists()

        done = events[-1]
        assert done["type"] == "done"
        assert done["ok"] is True
        expected_plan = workspace / ".hydra" / "plans" / "th_deny-turn-1.md"
        assert done.get("plan") == str(expected_plan)
        assert expected_plan.is_file()
        assert "Plan after write skip" in expected_plan.read_text(encoding="utf-8")
    finally:
        httpd.shutdown()



