"""A protocol-conformant stdio agent that runs turns in parallel. Test double for the Rust client: it lets the app's
concurrency, routing and approval handling be tested without a model. Spec: docs/STDIO_PROTOCOL.md plus per-turn parallelism.

Prompt words steer it: SLOW (40 ticks, 100 ms), ASK (approval before finishing), WRITE (diff event), FAIL (error then done)."""
import json
import sys
import threading
import time

out_lock = threading.Lock()
cancel = {}
waits = {}
state = threading.Lock()
counter = [0]
thread_cwd = {}
fields_seen = []
# background tasks: a BGJOB prompt starts one that outlives its turn (task_update after done)
tasks = {}


def emit(m):
    with out_lock:
        sys.stdout.write(json.dumps(m) + "\n")
        sys.stdout.flush()


def turn(msg):
    t, prompt, thread = msg["turn"], msg["prompt"], msg["thread"]
    # like the real adapter: cwd is required for a new thread, later turns reuse the thread's last one
    with state:
        if "cwd" in msg:
            thread_cwd[thread] = msg["cwd"]
        cwd = thread_cwd.get(thread)
    if cwd is None:
        emit({"type": "error", "code": "bad_request", "turn": t, "message": "cwd is required for a new thread"})
        emit({"type": "done", "turn": t, "text": "", "ok": False, "cancelled": False})
        return
    with state:
        fields_seen.append(sorted(k for k in ("cwd", "model", "permission", "mode") if k in msg))
    if "FIELDS" in prompt:
        emit({"type": "token", "turn": t, "text": json.dumps(fields_seen[-1])})
    stop = cancel[t]
    if msg.get("skills"):
        emit({"type": "context", "turn": t, "rules": None, "skills": msg["skills"], "mcp_tools": [], "mcp_errors": {}})
    if "BGJOB" in prompt:
        tid = f"task{len(tasks) + 1}"
        tasks[tid] = {"task": tid, "thread": thread, "turn": t, "command": "npm run dev", "cwd": cwd, "pid": 4242, "state": "running", "exit_code": None, "started": time.time(), "ended": None}
        threading.Timer(0.3, lambda: emit({"type": "task_update", **tasks[tid]})).start()  # arrives after done, between turns
    emit({"type": "token", "turn": t, "text": f"[{thread}@{cwd}] "})
    if "ASK" in prompt:
        with state:
            counter[0] += 1
            rid = f"a{counter[0]}"
            ev = waits[rid] = {"event": threading.Event(), "decision": ""}
        emit({"type": "approval_request", "turn": t, "id": rid, "tool": "bash", "arguments": {"command": "echo " + thread}})
        while not ev["event"].wait(0.05):
            if stop.is_set():
                break
        if stop.is_set() or ev["decision"] == "abort":
            emit({"type": "done", "turn": t, "text": "", "ok": False, "cancelled": stop.is_set()})
            return
        emit({"type": "tool_start", "turn": t, "call": 1, "tool": "bash", "arguments": {"command": "echo " + thread}})
        emit({"type": "tool_result", "turn": t, "call": 1, "tool": "bash", "is_error": False, "output": thread + "\n", "truncated": False})
    if "TOUCH" in prompt:
        import os
        with open(os.path.join(cwd, "a.txt"), "w") as fh:
            fh.write("agent was here\n")
        with open(os.path.join(cwd, "touched.txt"), "w") as fh:
            fh.write("new\n")
    if "ECHO" in prompt:
        emit({"type": "token", "turn": t, "text": "PROMPT<" + prompt + ">"})
    if "WRITE" in prompt:
        emit({"type": "diff", "turn": t, "path": f"{thread}.txt", "diff": f"--- a\n+++ b\n+{thread}\n"})
    if "FAIL" in prompt:
        emit({"type": "error", "code": "turn_failed", "message": "boom " + thread, "turn": t})
        emit({"type": "done", "turn": t, "text": "", "ok": False, "cancelled": False})
        return
    n, pause = (40, 0.1) if "SLOW" in prompt else (5, 0.005)
    for i in range(n):
        if stop.is_set():
            emit({"type": "done", "turn": t, "text": "cancelled", "ok": False, "cancelled": True})
            return
        emit({"type": "token", "turn": t, "text": f"{thread}."})
        time.sleep(pause)
    emit({"type": "usage", "turn": t, "model": "fake", "prompt_tokens": 10, "completion_tokens": 3, "cost_usd": 0.001})
    emit({"type": "done", "turn": t, "text": "", "ok": True, "cancelled": False})


def main():
    emit({"type": "hello", "protocol": 1, "agent": "fake", "capabilities": ["turn", "cancel", "approval", "permission", "action", "skills", "tasks"], "context_modes": ["recall"]})
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        k = msg.get("type")
        if k == "turn":
            if msg["turn"] in cancel:
                emit({"type": "error", "code": "busy", "message": "duplicate turn id", "turn": msg["turn"]})
                continue
            cancel[msg["turn"]] = threading.Event()
            threading.Thread(target=turn, args=(msg,), daemon=True).start()
        elif k == "cancel":
            ev = cancel.get(msg.get("turn"))
            if ev:
                ev.set()
        elif k == "approval":
            w = waits.get(msg.get("id"))
            if w:
                w["decision"] = msg.get("decision")
                w["event"].set()
        elif k == "skills":
            emit({"type": "skill_list", "id": msg.get("id"), "skills": [{"id": "code", "keys": ["code"], "title": "Read before you edit."}]})
        elif k == "tasks":
            emit({"type": "task_list", "id": msg.get("id"), "tasks": list(tasks.values())})
        elif k == "task_stop":
            t = tasks.get(msg.get("task"))
            if t is None:
                emit({"type": "error", "code": "bad_request", "message": "unknown task", "id": msg.get("id")})
                continue
            t.update(state="stopped", exit_code=-15, ended=time.time())
            emit({"type": "task_update", **t})
            emit({"type": "task_list", "id": msg.get("id"), "tasks": list(tasks.values())})
        elif k == "shutdown":
            break


main()
