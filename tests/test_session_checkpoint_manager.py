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


def _start_scripted_model_server(tool=None, tokens=("Step 1: inspect\n", "Step 2: verify"), n_ctx=None, tools=None):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    tool_list = list(tools) if tools is not None else ([tool] if tool is not None else [])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.rstrip("/") == "/props" and n_ctx is not None:
                payload = json.dumps({"default_generation_settings": {"n_ctx": n_ctx}}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if not body.get("stream", True):
                payload = json.dumps(
                    {"choices": [{"message": {"content": "".join(tokens)}}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
                self.wfile.flush()
                return
            if tool_list and not any(m.get("role") == "tool" for m in body.get("messages", [])):
                chunks = []
                for idx, t_spec in enumerate(tool_list):
                    args = json.dumps(t_spec["arguments"])
                    chunks.append(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": idx,
                                                "id": f"call_{idx + 1}",
                                                "type": "function",
                                                "function": {"name": t_spec["name"], "arguments": ""},
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    )
                    chunks.append(
                        {"choices": [{"delta": {"tool_calls": [{"index": idx, "function": {"arguments": args}}]}}]}
                    )
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


def test_stdio_context_event_carries_window_and_forced_compaction_emits_compacted(tmp_path, monkeypatch):
    import io
    from hydra_cli import preflight
    from hydra_cli.context import SessionContextLedger
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))
    preflight.reset_preflight_state()

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    httpd, base_url = _start_scripted_model_server(
        tokens=("Summary of folded history.",),
        n_ctx=131072,
    )
    monkeypatch.setenv("LOCAL_AI_BASE", base_url)
    try:
        out = io.StringIO()
        adapter = Adapter(io.BytesIO(b""), out)

        # Normal turn on local route carries n_ctx (131072) as "window" on the context event
        normal_events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_norm_1",
                "thread": "th_ctx_norm",
                "prompt": "hello normal turn",
                "cwd": str(workspace),
                "permission": "bypass",
                "model": "local",
                "checkpoint": False,
            },
        )
        norm_ctx = [e for e in normal_events if e["type"] == "context"]
        assert len(norm_ctx) == 1
        assert norm_ctx[0].get("window") == 131072
        assert "compacted" not in norm_ctx[0]

        # Seed a ledger with enough live turns (> KEEP_RECENT + 1) to force compaction under a tiny context_chars
        ledger = SessionContextLedger(session_id="th_ctx_compact")
        for idx in range(4):
            ledger.append_turn("user", f"user turn {idx} " + ("x" * 300))
            ledger.append_turn("assistant", f"assistant turn {idx} " + ("y" * 300))
        ledger.end_turn()

        compact_events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_comp_1",
                "thread": "th_ctx_compact",
                "prompt": "trigger compaction now",
                "cwd": str(workspace),
                "permission": "bypass",
                "model": "local",
                "mode": "compact",
                "context_chars": 4096,
                "checkpoint": False,
            },
        )
        ctx_indices = [i for i, e in enumerate(compact_events) if e["type"] == "context"]
        done_index = next(i for i, e in enumerate(compact_events) if e["type"] == "done")
        assert len(ctx_indices) == 2
        assert ctx_indices[1] < done_index

        initial_ctx = compact_events[ctx_indices[0]]
        assert initial_ctx.get("window") == 131072
        assert "compacted" not in initial_ctx

        compacted_ctx = compact_events[ctx_indices[1]]
        assert compacted_ctx["turn"] == "t_comp_1"
        assert compacted_ctx.get("window") == 131072
        compacted_info = compacted_ctx.get("compacted")
        assert isinstance(compacted_info, dict)
        assert compacted_info["turns_folded"] == 4
        assert compacted_info["turns_folded"] >= 1
        assert compacted_info["chars_before"] > compacted_info["chars_after"] > 0
    finally:
        httpd.shutdown()

    # When local server /props is unavailable and preflight has no n_ctx, "window" is omitted rather than guessed
    httpd_no_props, base_no_props = _start_scripted_model_server(
        tokens=("Reply without props.",),
        n_ctx=None,
    )
    monkeypatch.setenv("LOCAL_AI_BASE", base_no_props)
    try:
        out_no_props = io.StringIO()
        adapter_no_props = Adapter(io.BytesIO(b""), out_no_props)
        no_win_events = _run_turn_sync(
            adapter_no_props,
            out_no_props,
            {
                "type": "turn",
                "turn": "t_nowin_1",
                "thread": "th_ctx_nowin",
                "prompt": "hello without props",
                "cwd": str(workspace),
                "permission": "bypass",
                "model": "local",
                "checkpoint": False,
            },
        )
        no_win_ctx = [e for e in no_win_events if e["type"] == "context"]
        assert len(no_win_ctx) == 1
        assert "window" not in no_win_ctx[0]
    finally:
        httpd_no_props.shutdown()


def test_stdio_turn_emits_preflight_result_before_done_and_survives_preflight_exception(tmp_path, monkeypatch, capsys):
    import io
    from hydra_cli import modes, preflight
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))
    monkeypatch.delenv("HYDRA_MODE", raising=False)
    monkeypatch.setattr(modes, "_read_settings", lambda: {"mode": "local"})

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    seen_pf_calls = []
    fake_report = preflight.PreflightReport(
        mode="local",
        default_route="local",
        local=preflight.RouteProbeResult(
            route="local",
            status="PASS",
            model_id="gemma-e2b",
            short_label="gemma-e2b",
            n_ctx=131072,
            port=8080,
            endpoint="http://127.0.0.1:8080",
        ),
        light=preflight.RouteProbeResult(
            route="light",
            status="SKIPPED",
            model_id="muse-spark-1.3-contributor",
            short_label="muse-spark",
        ),
        free=preflight.RouteProbeResult(
            route="free",
            status="SKIPPED",
            model_id="nvidia/nemotron-3.5-lightning:free",
            short_label="nemotron",
        ),
    )

    def stub_run_preflight(mode=None, force=False):
        seen_pf_calls.append((mode, force))
        return fake_report

    monkeypatch.setattr(preflight, "run_preflight", stub_run_preflight)
    monkeypatch.setattr(
        "hydra_cli.agent.run_agent_loop",
        lambda **kwargs: "stubbed turn reply",
    )

    out = io.StringIO()
    adapter = Adapter(io.BytesIO(b""), out)
    events = _run_turn_sync(
        adapter,
        out,
        {
            "type": "turn",
            "turn": "t_pf_1",
            "thread": "th_pf",
            "prompt": "hello preflight",
            "cwd": str(workspace),
            "permission": "bypass",
            "checkpoint": False,
        },
    )

    assert seen_pf_calls == [("local", False)]
    pf_indices = [i for i, e in enumerate(events) if e["type"] == "preflight_result"]
    ctx_index = next(i for i, e in enumerate(events) if e["type"] == "context")
    done_index = next(i for i, e in enumerate(events) if e["type"] == "done")
    assert len(pf_indices) == 1
    assert pf_indices[0] < ctx_index < done_index

    pf_ev = events[pf_indices[0]]
    assert pf_ev["id"] == "t_pf_1"
    assert pf_ev["turn"] == "t_pf_1"
    assert pf_ev["strip"] == preflight.format_route_strip(fake_report)
    assert pf_ev["notice"] == preflight.format_preflight_notice(fake_report, mode="local")
    assert pf_ev["report"] == fake_report.to_dict()
    assert events[done_index]["ok"] is True

    # A preflight exception must never fail the turn
    def raising_preflight(mode=None, force=False):
        raise RuntimeError("preflight probe boom")

    monkeypatch.setattr(preflight, "run_preflight", raising_preflight)
    events_err = _run_turn_sync(
        adapter,
        out,
        {
            "type": "turn",
            "turn": "t_pf_2",
            "thread": "th_pf",
            "prompt": "hello after preflight failure",
            "cwd": str(workspace),
            "permission": "bypass",
            "checkpoint": False,
        },
    )
    assert not any(e["type"] == "preflight_result" for e in events_err)
    done_err = next(e for e in events_err if e["type"] == "done")
    assert done_err["ok"] is True
    assert done_err["text"] == "stubbed turn reply"
    captured = capsys.readouterr()
    assert "preflight probe boom" in captured.err


def test_stdio_hello_carries_mode_and_default_model_when_stubbed_to_local(tmp_path, monkeypatch):
    import io
    import json
    from hydra_cli import modes
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))
    monkeypatch.delenv("HYDRA_MODE", raising=False)
    monkeypatch.setattr(modes, "_read_settings", lambda: {"mode": "local"})

    out = io.StringIO()
    adapter = Adapter(io.BytesIO(b'{"type":"shutdown"}\n'), out)
    rc = adapter.serve()
    assert rc == 0
    hello_events = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert len(hello_events) >= 1
    hello = hello_events[0]
    assert hello["type"] == "hello"
    assert hello.get("mode") == "local"
    assert hello.get("default_model") == "gemma-e2b"

    # When mode/default_model resolution fails, omit the fields rather than guessing
    def broken_mode():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(modes, "current_mode", broken_mode)
    out_broken = io.StringIO()
    adapter_broken = Adapter(io.BytesIO(b'{"type":"shutdown"}\n'), out_broken)
    assert adapter_broken.serve() == 0
    hello_broken = json.loads(out_broken.getvalue().splitlines()[0])
    assert hello_broken["type"] == "hello"
    assert "mode" not in hello_broken
    assert "default_model" not in hello_broken


def test_stdio_modelless_metaless_turn_resolves_local_default_model_and_respects_precedence(tmp_path, monkeypatch):
    import io
    from hydra_cli import modes, preflight
    from hydra_cli.config import DEFAULT_ORCHESTRATOR_MODEL
    from hydra_cli.stdio_adapter import Adapter, _save_meta

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))
    monkeypatch.delenv("HYDRA_MODE", raising=False)
    monkeypatch.setattr(modes, "_read_settings", lambda: {"mode": "local"})

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    fake_report = preflight.PreflightReport(
        mode="local",
        default_route="local",
        local=preflight.RouteProbeResult(
            route="local",
            status="PASS",
            model_id="gemma-e2b",
            short_label="gemma-e2b",
            n_ctx=131072,
            port=8080,
            endpoint="http://127.0.0.1:8080",
        ),
        light=preflight.RouteProbeResult(
            route="light",
            status="SKIPPED",
            model_id="muse-spark-1.3-contributor",
            short_label="muse-spark",
        ),
        free=preflight.RouteProbeResult(
            route="free",
            status="SKIPPED",
            model_id="nvidia/nemotron-3.5-lightning:free",
            short_label="nemotron",
        ),
    )
    monkeypatch.setattr(preflight, "run_preflight", lambda mode=None, force=False: fake_report)

    resolved_aliases = []

    def stub_run_agent_loop(**kwargs):
        resolved_aliases.append(kwargs.get("alias"))
        return "ok"

    monkeypatch.setattr("hydra_cli.agent.run_agent_loop", stub_run_agent_loop)

    out = io.StringIO()
    adapter = Adapter(io.BytesIO(b""), out)

    # 1. Model-less, meta-less turn in local mode resolves "gemma-e2b", not DEFAULT_ORCHESTRATOR_MODEL
    events1 = _run_turn_sync(
        adapter,
        out,
        {
            "type": "turn",
            "turn": "t_def_1",
            "thread": "th_metaless",
            "prompt": "no model field",
            "cwd": str(workspace),
            "permission": "bypass",
            "checkpoint": False,
        },
    )
    assert events1[-1]["type"] == "done"
    assert events1[-1]["ok"] is True
    assert resolved_aliases[-1] == "gemma-e2b"
    assert resolved_aliases[-1] != DEFAULT_ORCHESTRATOR_MODEL

    # 2. Stored thread meta takes precedence over mode default when turn omits model
    _save_meta("th_with_meta", {"cwd": str(workspace), "model": "bonsai 2 9b"})
    events2 = _run_turn_sync(
        adapter,
        out,
        {
            "type": "turn",
            "turn": "t_def_2",
            "thread": "th_with_meta",
            "prompt": "use stored meta model",
            "cwd": str(workspace),
            "permission": "bypass",
            "checkpoint": False,
        },
    )
    assert events2[-1]["ok"] is True
    assert resolved_aliases[-1] == "bonsai 2 9b"

    # 3. Explicit turn model takes precedence over stored meta and mode default
    events3 = _run_turn_sync(
        adapter,
        out,
        {
            "type": "turn",
            "turn": "t_def_3",
            "thread": "th_with_meta",
            "prompt": "use explicit turn model",
            "cwd": str(workspace),
            "model": "mimo 2.6 flash",
            "permission": "bypass",
            "checkpoint": False,
        },
    )
    assert events3[-1]["ok"] is True
    assert resolved_aliases[-1] == "mimo 2.6 flash"


def test_stdio_tool_result_emits_monotonic_duration_ms_and_omits_without_matching_start(tmp_path, monkeypatch):
    import io
    from hydra_cli.stdio_adapter import Adapter
    from hydra_cli.ui import tool_sink

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sample = workspace / "sample.txt"
    sample.write_text("line one\nline two\n", encoding="utf-8")

    read_tool = {"name": "read_file", "arguments": {"path": "sample.txt"}}
    httpd, base_url = _start_scripted_model_server(tool=read_tool, tokens=("Read complete.",))
    monkeypatch.setenv("LOCAL_AI_BASE", base_url)
    try:
        out = io.StringIO()
        adapter = Adapter(io.BytesIO(b""), out)
        events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_tool_dur_1",
                "thread": "th_tool_dur",
                "prompt": "read sample.txt",
                "cwd": str(workspace),
                "permission": "bypass",
                "model": "local",
                "checkpoint": False,
            },
        )
        starts = [e for e in events if e["type"] == "tool_start"]
        results = [e for e in events if e["type"] == "tool_result"]
        assert len(starts) == 1
        assert len(results) == 1
        assert starts[0]["turn"] == "t_tool_dur_1"
        assert starts[0]["call"] == 1
        assert results[0]["turn"] == "t_tool_dur_1"
        assert results[0]["call"] == 1
        assert results[0]["tool"] == "read_file"
        assert results[0]["is_error"] is False
        assert "duration_ms" in results[0]
        assert isinstance(results[0]["duration_ms"], int)
        assert not isinstance(results[0]["duration_ms"], bool)
        assert results[0]["duration_ms"] >= 0
    finally:
        httpd.shutdown()

    # Result sink called without a matching start (or after start already consumed) omits duration_ms
    def stub_unmatched_loop(**_kwargs):
        sink = tool_sink()
        assert sink is not None
        sink("read_file", '{"path":"sample.txt"}', False, "orphan result")
        return "done"

    monkeypatch.setattr("hydra_cli.agent.run_agent_loop", stub_unmatched_loop)
    out2 = io.StringIO()
    adapter2 = Adapter(io.BytesIO(b""), out2)
    events2 = _run_turn_sync(
        adapter2,
        out2,
        {
            "type": "turn",
            "turn": "t_tool_orphan",
            "thread": "th_tool_orphan",
            "prompt": "orphan tool result",
            "cwd": str(workspace),
            "permission": "bypass",
            "model": "local",
            "checkpoint": False,
        },
    )
    orphan_results = [e for e in events2 if e["type"] == "tool_result"]
    assert len(orphan_results) == 1
    assert "duration_ms" not in orphan_results[0]


def test_stdio_diff_turn_writes_patch_artifacts_increments_seq_and_survives_write_failure(tmp_path, monkeypatch):
    import io
    from pathlib import Path
    from hydra_cli.stdio_adapter import Adapter

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Two file writes in the same turn (even to the same basename) get distinct seq numbers and never overwrite
    write_tools = [
        {"name": "write_file", "arguments": {"path": "src/hello.py", "content": "print('v1')\n"}},
        {"name": "write_file", "arguments": {"path": "src/hello.py", "content": "print('v1')\nprint('v2')\n"}},
    ]
    httpd, base_url = _start_scripted_model_server(tools=write_tools, tokens=("Wrote files.",))
    monkeypatch.setenv("LOCAL_AI_BASE", base_url)
    try:
        out = io.StringIO()
        adapter = Adapter(io.BytesIO(b""), out)
        events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_diff_1",
                "thread": "th_diff",
                "prompt": "write src/hello.py twice",
                "cwd": str(workspace),
                "permission": "accept_edits",
                "model": "local",
                "checkpoint": False,
            },
        )
        diffs = [e for e in events if e["type"] == "diff"]
        assert len(diffs) == 2
        done = events[-1]
        assert done["type"] == "done"
        assert done["ok"] is True

        diffs_dir = workspace / ".hydra" / "diffs" / "th_diff"
        assert diffs_dir.is_dir()

        art1 = Path(diffs[0]["artifact"])
        art2 = Path(diffs[1]["artifact"])
        assert art1 != art2
        assert art1.parent == diffs_dir
        assert art2.parent == diffs_dir
        assert art1.name.endswith("-1-hello.py.patch")
        assert art2.name.endswith("-2-hello.py.patch")
        assert art1.is_file()
        assert art2.is_file()
        assert "artifact_error" not in diffs[0]
        assert "artifact_error" not in diffs[1]

        # Each .patch file contains the exact diff text the sink received
        assert art1.read_text(encoding="utf-8") == diffs[0]["diff"]
        assert art2.read_text(encoding="utf-8") == diffs[1]["diff"]
        assert "+print('v1')" in art1.read_text(encoding="utf-8")
        assert "+print('v2')" in art2.read_text(encoding="utf-8")

        # Write failure still emits the diff event with artifact_error and never breaks the turn
        def fail_diff_write(*_args, **_kwargs):
            raise OSError("read-only diff store")

        monkeypatch.setattr(adapter, "_write_diff_artifact", fail_diff_write)
        events_err = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_diff_2",
                "thread": "th_diff",
                "prompt": "write again with broken artifact store",
                "cwd": str(workspace),
                "permission": "accept_edits",
                "model": "local",
                "checkpoint": False,
            },
        )
        diffs_err = [e for e in events_err if e["type"] == "diff"]
        assert len(diffs_err) >= 1
        for d_ev in diffs_err:
            assert "artifact" not in d_ev
            assert d_ev.get("artifact_error") == "read-only diff store"
            assert d_ev.get("diff")
        done_err = events_err[-1]
        assert done_err["type"] == "done"
        assert done_err["ok"] is True
    finally:
        httpd.shutdown()


def test_set_todos_tool_validation_and_session_replacement(tmp_path):
    from hydra_cli.agent import DEFAULT_AGENT_SYSTEM_PROMPT
    from hydra_cli.native_tools import NativeToolRegistry

    assert "set_todos" in DEFAULT_AGENT_SYSTEM_PROMPT

    reg = NativeToolRegistry(cwd=str(tmp_path))
    assert reg.has_tool("set_todos")
    schema_names = [t["function"]["name"] for t in reg.get_openai_tools()]
    assert "set_todos" in schema_names

    valid_items = [
        {"title": "Inspect tool registry", "status": "done"},
        {"title": "Implement set_todos", "status": "active"},
        {"title": "Verify stdio event", "status": "pending"},
    ]
    summary = reg.dispatch("set_todos", {"todos": valid_items})
    assert summary == "todos: 3 (1 done, 1 active, 1 pending)"
    assert reg.todos == valid_items

    # Each call replaces the session's todo list whole
    next_items = [
        {"title": "Implement set_todos", "status": "done"},
        {"title": "Verify stdio event", "status": "done"},
    ]
    summary2 = reg.set_todos(next_items)
    assert summary2 == "todos: 2 (2 done, 0 active, 0 pending)"
    assert reg.todos == next_items

    # Bad status produces a tool error naming status, no exception escaping, previous list preserved
    bad_status = reg.dispatch("set_todos", {"todos": [{"title": "Step 1", "status": "in_progress"}]})
    assert isinstance(bad_status, dict) and bad_status.get("isError") is True
    assert "status" in bad_status.get("error", "")
    assert reg.todos == next_items

    # Empty or whitespace title produces a tool error naming title
    for empty_title in ("", "   "):
        bad_title = reg.dispatch("set_todos", {"todos": [{"title": empty_title, "status": "pending"}]})
        assert isinstance(bad_title, dict) and bad_title.get("isError") is True
        assert "title" in bad_title.get("error", "")
        assert reg.todos == next_items

    # Non-list payloads produce a tool error naming list, no exception escaping
    for non_list in ("not a list", 42, {"title": "Single item", "status": "pending"}, {"todos": "not a list"}):
        bad_payload = reg.dispatch("set_todos", non_list)
        assert isinstance(bad_payload, dict) and bad_payload.get("isError") is True
        assert "list" in bad_payload.get("error", "").lower()
        assert reg.todos == next_items


def test_stdio_adapter_emits_todos_event_on_success_and_none_on_failure(tmp_path, monkeypatch):
    import io
    from hydra_cli.stdio_adapter import CAPABILITIES, Adapter

    assert "todos" in CAPABILITIES

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HYDRA_HOME", str(home_dir / ".hydra"))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    todo_list = [
        {"title": "Add set_todos tool", "status": "done"},
        {"title": "Emit todos event", "status": "active"},
        {"title": "Render TUI block", "status": "pending"},
    ]
    httpd, base_url = _start_scripted_model_server(
        tools=[{"name": "set_todos", "arguments": {"todos": todo_list}}],
        tokens=("Published todos.",),
    )
    monkeypatch.setenv("LOCAL_AI_BASE", base_url)
    try:
        out = io.StringIO()
        adapter = Adapter(io.BytesIO(b""), out)
        events = _run_turn_sync(
            adapter,
            out,
            {
                "type": "turn",
                "turn": "t_todos_ok",
                "thread": "th_todos",
                "prompt": "publish todos",
                "cwd": str(workspace),
                "permission": "plan",
                "model": "local",
                "checkpoint": False,
            },
        )
        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["tool"] == "set_todos"
        assert results[0]["is_error"] is False
        assert results[0]["output"] == "todos: 3 (1 done, 1 active, 1 pending)"

        todos_events = [e for e in events if e["type"] == "todos"]
        assert len(todos_events) == 1
        assert todos_events[0] == {
            "type": "todos",
            "turn": "t_todos_ok",
            "todos": todo_list,
        }
    finally:
        httpd.shutdown()

    # Failed set_todos call (invalid status) emits tool_result with is_error=True and NO todos event
    httpd_fail, base_url_fail = _start_scripted_model_server(
        tools=[{"name": "set_todos", "arguments": {"todos": [{"title": "Broken", "status": "invalid"}]}}],
        tokens=("Failed todos.",),
    )
    monkeypatch.setenv("LOCAL_AI_BASE", base_url_fail)
    try:
        out_fail = io.StringIO()
        adapter_fail = Adapter(io.BytesIO(b""), out_fail)
        events_fail = _run_turn_sync(
            adapter_fail,
            out_fail,
            {
                "type": "turn",
                "turn": "t_todos_fail",
                "thread": "th_todos",
                "prompt": "publish invalid todos",
                "cwd": str(workspace),
                "permission": "plan",
                "model": "local",
                "checkpoint": False,
            },
        )
        results_fail = [e for e in events_fail if e["type"] == "tool_result"]
        assert len(results_fail) == 1
        assert results_fail[0]["tool"] == "set_todos"
        assert results_fail[0]["is_error"] is True
        assert [e for e in events_fail if e["type"] == "todos"] == []
    finally:
        httpd_fail.shutdown()








