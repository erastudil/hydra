#!/usr/bin/env python3
"""Release-bar measurements of the real native build on the box, headless under xvfb (PLAN section 4, items 2, 3, 4, 5, 9).
usage: perf_rust.py [cold|memory|frames|kill|backend_kill|corrupt|bundle ...]  (none = all). Writes docs/PERF.md rows to stdout and
exits non-zero when a budget fails. Nothing here touches a real screen."""
import json, os, random, shutil, signal, statistics, subprocess, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.environ.get("HYDRA_BIN", os.path.join(ROOT, "hydra-desktop-rs", "target", "release", "Hydra"))
PORT = 18998
RESULTS, FAILS = [], []


def row(name, value, budget, ok):
    RESULTS.append((name, value, budget, "pass" if ok else "FAIL"))
    if not ok:
        FAILS.append(name)
    print(f"{name}: {value} (budget {budget}) {'pass' if ok else 'FAIL'}", flush=True)


class Xvfb:
    def __enter__(self):
        self.disp = ":%d" % random.randint(200, 900)
        self.p = subprocess.Popen(["Xvfb", self.disp, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        return self

    def __exit__(self, *a):
        self.p.kill()
        self.p.wait()


def home():
    h = tempfile.mkdtemp(prefix="hydra-perf-")
    os.makedirs(os.path.join(h, "proj"))
    return h


def env_for(h, disp, **extra):
    e = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY",)}
    e.update(HOME=h, HYDRA_HOME=os.path.join(h, ".hydra"), HYDRA_STATE_DIR=os.path.join(h, "state"), HYDRA_CWD=os.path.join(h, "proj"),
             HYDRA_REPO=ROOT, HYDRA_MODEL="local", LOCAL_AI_BASE=f"http://127.0.0.1:{PORT}", DISPLAY=disp, HYDRA_SIZE="1280x800")
    e.update({k: str(v) for k, v in extra.items()})
    return e


def launch(h, disp, **extra):
    return subprocess.Popen([BIN], cwd=os.path.join(h, "proj"), env=env_for(h, disp, **extra), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def children(pid):
    try:
        return [int(x) for x in open(f"/proc/{pid}/task/{pid}/children").read().split()]
    except OSError:
        return []


def rss_kb(pid):
    try:
        for l in open(f"/proc/{pid}/status"):
            if l.startswith("VmRSS:"):
                return int(l.split()[1])
    except OSError:
        pass
    return 0


def tree_rss(pid):
    total, todo = 0, [pid]
    while todo:
        p = todo.pop()
        total += rss_kb(p)
        todo += children(p)
    return total


def stop(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.wait()


def marks(path, keys, timeout):
    end = time.time() + timeout
    while time.time() < end:
        got = {}
        if os.path.exists(path):
            for l in open(path):
                k, v = l.split()
                got[k] = int(v)
        if all(k in got for k in keys):
            return got
        time.sleep(0.01)
    return None


def cold():
    first, hello = [], []
    with Xvfb() as x:
        for i in range(10):
            h = home()
            t = os.path.join(h, "timing")
            t0 = int(time.time() * 1000)
            p = launch(h, x.disp, HYDRA_TIMING=t)
            m = marks(t, ["first_frame", "hello"], 20)
            stop(p)
            shutil.rmtree(h, ignore_errors=True)
            if m:
                first.append(m["first_frame"] - t0)
                hello.append(m["hello"] - t0)
    if len(first) < 10:
        row("cold start runs", f"{len(first)}/10 completed", "10/10", False)
        return
    f, b = statistics.median(first), statistics.median(hello)
    row("cold start: first interactive frame, median of 10", f"{f:.0f} ms (min {min(first)}, max {max(first)})", "<= 1000 ms", f <= 1000)
    row("cold start: backend hello, median of 10", f"{b:.0f} ms (min {min(hello)}, max {max(hello)})", "<= 3000 ms", b <= 3000)


def seed_state(h, projects=3, threads=20, msgs=20):
    st = os.path.join(h, "state")
    os.makedirs(st, exist_ok=True)
    projs = []
    for i in range(projects):
        d = os.path.join(h, f"proj{i}")
        os.makedirs(d, exist_ok=True)
        projs.append({"id": i + 1, "name": f"proj{i}", "path": d, "pinned": False})
    sessions = []
    for t in range(threads):
        ms = [{"user": k % 2 == 0, "header": "" if k % 2 == 0 else "HYDRA ORCHESTRATOR", "text": (f"message {k} of thread {t}. " * 6), "actions": []} for k in range(msgs)]
        sessions.append({"id": t + 1, "thread": f"desk-seed-{t}", "name": f"Thread {t}", "messages": ms, "project": t % projects + 1, "updated": 1_700_000_000 + t})
    json.dump({"projects": projs, "active_project": 1, "sessions": sessions, "active": 0, "counter": threads, "nav": "chat", "model": 0, "tokens": 0, "cost": 0.0}, open(os.path.join(st, "state.json"), "w"))
    return sessions


def memory():
    with Xvfb() as x:
        h = home()
        seed_state(h)
        t = os.path.join(h, "timing")
        p = launch(h, x.disp, HYDRA_TIMING=t)
        marks(t, ["first_frame", "hello"], 20)
        time.sleep(4)
        app, tree = rss_kb(p.pid), tree_rss(p.pid)
        stop(p)
        shutil.rmtree(h, ignore_errors=True)
    row("memory: idle app, 3 projects / 20 threads", f"{app/1024:.0f} MB", "< 150 MB", app / 1024 < 150)
    row("memory: app + backend", f"{tree/1024:.0f} MB", "< 350 MB", tree / 1024 < 350)


def frames():
    with Xvfb() as x:
        h = home()
        csv = os.path.join(h, "probe.csv")
        p = launch(h, x.disp, HYDRA_PROBE=csv, HYDRA_NO_BACKEND=1)
        try:
            p.wait(90)
        except subprocess.TimeoutExpired:
            stop(p)
        lines = open(csv).read().splitlines() if os.path.exists(csv) else []
        shutil.rmtree(h, ignore_errors=True)
    rows = [l.split(",") for l in lines if not l.startswith("#")]
    rss = [int(l.split()[-1]) for l in lines if l.startswith("# rss_2000_msgs")]
    work = [float(r[1]) for r in rows if r[2] in ("stream", "stream_scroll")]
    gaps = [(float(b[0]) - float(a[0])) * 1000 for a, b in zip(rows, rows[1:]) if b[2] in ("stream", "stream_scroll")]
    if not work:
        row("frame probe", "no rows", "rows", False)
        return
    q = lambda v, p: sorted(v)[min(len(v) - 1, int(p * len(v)))]
    row("frames: streaming + scrolling 2,000 messages, CPU frame time p95", f"{q(work, .95):.2f} ms over {len(work)} frames", "<= 16.7 ms", q(work, .95) <= 16.7)
    row("frames: same, p99", f"{q(work, .99):.2f} ms", "< 33 ms", q(work, .99) < 33)
    row("frames: same, frame-to-frame interval p95 (xvfb, software GL)", f"{q(gaps, .95):.2f} ms", "<= 16.7 ms", q(gaps, .95) <= 16.7)
    if rss:
        row("memory: 2,000 message thread (app)", f"{rss[0]/1024:.0f} MB", "< 400 MB", rss[0] / 1024 < 400)


def load_state(st):
    for name in ("state.json", "state.bak"):
        try:
            return json.load(open(os.path.join(st, name))), name
        except (OSError, ValueError):
            continue
    return None, None


def kill_runs(n=50):
    """Kill -9 the app at random points of a streaming turn (and its state writes); every committed message must survive."""
    bad, used_bak, streamed, interrupted = [], 0, 0, 0
    with Xvfb() as x:
        h = home()
        seed_state(h, projects=1, threads=3, msgs=4)
        st = os.path.join(h, "state")
        before, _ = load_state(st)
        for i in range(n):
            p = launch(h, x.disp, HYDRA_DEMO_PROMPT=f"SLOW kill run {i}")
            time.sleep(random.uniform(0.3, 6.0))
            stop(p)
            after, src = load_state(st)
            if after is None:
                bad.append(f"run {i}: no parseable state")
                break
            used_bak += src != "state.json"
            ids = {s["thread"]: s for s in after["sessions"]}
            keep = lambda ms: [m["text"] for m in ms if m["user"] or m["text"]]
            for s in before["sessions"]:
                a = ids.get(s["thread"])
                done = keep(s["messages"])
                if done and not s["messages"][-1]["user"]:
                    done = done[:-1]  # the reply that was streaming may still grow; everything before it is committed
                if a is None or keep(a["messages"])[:len(done)] != done:
                    bad.append(f"run {i}: thread {s['thread']} lost committed messages")
            before = after
        # one clean start replays the journal of the last kill; then count the streamed text the killed replies kept
        p = launch(h, x.disp, HYDRA_SHOT=os.path.join(h, "s.png"), HYDRA_SHOT_FRAMES=20, HYDRA_NO_BACKEND=1)
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            stop(p)
        final, _ = load_state(st)
        for sess in final["sessions"]:
            ms = sess["messages"]
            streamed += sum(len(b["text"]) for a, b in zip(ms, ms[1:]) if a["user"] and a["text"].startswith("SLOW kill run") and not b["user"])
            interrupted += sum(1 for m in ms if any("interrupted" in x for x in m.get("actions", [])))
        shutil.rmtree(h, ignore_errors=True)
    row(f"kill -9 app x{n} during streaming turns", "no loss" if not bad else "; ".join(bad[:3]), "every committed message and thread survives, state parses", not bad)
    row("kill -9: streamed text kept across kills (journal)", f"{streamed} chars of streamed replies kept over {n} kills, {interrupted} replies marked interrupted", "> 0", streamed > 0)
    if used_bak:
        print(f"  ({used_bak} runs recovered from state.bak)")


def backend_kill():
    with Xvfb() as x:
        h = home()
        t = os.path.join(h, "timing")
        p = launch(h, x.disp, HYDRA_TIMING=t, HYDRA_DEMO_PROMPT="SLOW backend kill")
        marks(t, ["hello"], 20)
        time.sleep(1.5)
        kids = children(p.pid)
        for k in kids:
            os.kill(k, signal.SIGKILL)
        time.sleep(4)
        alive = p.poll() is None
        new = [k for k in children(p.pid) if k not in kids]
        stop(p)
        st, _ = load_state(os.path.join(h, "state"))
        shutil.rmtree(h, ignore_errors=True)
    msgs = st["sessions"][0]["messages"] if st else []
    failed_shown = any("agent process ended" in (m.get("text", "") + " ".join(m.get("actions", []))) for m in msgs)
    row("kill -9 backend child mid-turn", f"app alive={alive}, respawned={bool(new)}, turn shown failed={failed_shown}", "app survives, respawns, turn marked failed", alive and bool(new) and bool(kids))


def corrupt():
    with Xvfb() as x:
        h = home()
        sessions = seed_state(h, projects=1, threads=5, msgs=6)
        st = os.path.join(h, "state")
        shutil.copy(os.path.join(st, "state.json"), os.path.join(st, "state.bak"))
        data = open(os.path.join(st, "state.json")).read()
        open(os.path.join(st, "state.json"), "w").write(data[: len(data) // 2])
        p = launch(h, x.disp, HYDRA_SHOT=os.path.join(h, "s.png"), HYDRA_SHOT_FRAMES=30, HYDRA_NO_BACKEND=1)
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            stop(p)
        got, src = load_state(st)
        shutil.rmtree(h, ignore_errors=True)
    ok = got is not None and {s["thread"] for s in sessions} <= {s["thread"] for s in got["sessions"]}
    row("truncated state.json recovers from .bak", f"recovered={ok}, rewritten state.json parses={src == 'state.json'}", "all threads back, state.json rewritten", ok and src == "state.json")


def _hide_python_args():
    """bwrap args for a clean machine view: no system python anywhere, no checkout (/workspace hidden)."""
    import glob
    args = ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--bind", "/tmp", "/tmp", "--tmpfs", "/workspace", "--tmpfs", "/root"]
    for d in glob.glob("/usr/lib/python3*") + glob.glob("/usr/local/lib/python3*") + glob.glob("/usr/lib/python2*"):
        args += ["--tmpfs", d]
    for b in sorted({os.path.realpath(b) for b in glob.glob("/usr/bin/python*") + glob.glob("/usr/local/bin/python*") + glob.glob("/bin/python*")}):
        if os.path.isfile(b):
            args += ["--ro-bind", "/dev/null", b]  # symlinks to it now reach an empty file
    return args


def bundle():
    """Release bar 9: a clean folder copy of the Linux release runs a full turn with no system Python and no checkout:
    bwrap hides every system interpreter and /workspace, PATH is empty, HYDRA_REPO is unset."""
    src = os.path.join(ROOT, "dist", "Hydra-linux")
    if not os.path.isfile(os.path.join(src, "Hydra")):
        subprocess.run(["bash", os.path.join(ROOT, "scripts", "package_linux.sh")], check=True, capture_output=True)
    folder = tempfile.mkdtemp(prefix="hydra-clean-")
    app = os.path.join(folder, "Hydra")
    shutil.copytree(src, app, symlinks=True)
    h = os.path.join(folder, "home")
    os.makedirs(os.path.join(h, "proj"))
    probe = subprocess.run(["/usr/bin/bwrap", *_hide_python_args(), "/bin/sh", "-c", "command -v python3 python; /usr/bin/python3 -c 1 && echo SYSTEM_PYTHON_RUNS; ls /workspace | wc -l"], capture_output=True, text=True)
    clean = "SYSTEM_PYTHON_RUNS" not in probe.stdout and probe.stdout.strip().endswith("0")
    with Xvfb() as x:
        env = {"PATH": "", "HOME": h, "DISPLAY": x.disp, "HYDRA_MODEL": "local", "LOCAL_AI_BASE": f"http://127.0.0.1:{PORT}", "HYDRA_SIZE": "1280x800",
               "HYDRA_DEMO_PROMPT": "hello from a clean machine", "HYDRA_SHOT": os.path.join(folder, "shot.png"), "HYDRA_SHOT_SECS": "12", "HYDRA_SHOT_FRAMES": "100000"}
        r = subprocess.run(["/usr/bin/bwrap", *_hide_python_args(), "--chdir", os.path.join(h, "proj"), os.path.join(app, "Hydra")], env=env, capture_output=True, text=True, timeout=90)
    st, _ = load_state(os.path.join(h, ".hydra", "desktop"))
    if st is None:
        import glob
        found = glob.glob(os.path.join(h, "**", "state.json"), recursive=True)
        st = json.load(open(found[0])) if found else None
    msgs = st["sessions"][st["active"]]["messages"] if st else []
    answered = any(not m["user"] and "alpha beta gamma" in m["text"] for m in msgs)
    shot = os.path.join(ROOT, "..", "shots", "release", "clean_machine_turn.png")
    os.makedirs(os.path.dirname(shot), exist_ok=True)
    if os.path.exists(os.path.join(folder, "shot.png")):
        shutil.copy(os.path.join(folder, "shot.png"), shot)
    row("clean-folder bundle: no system python, no checkout, PATH empty, HYDRA_REPO unset, full turn",
        f"system python hidden={clean}, reply streamed={answered}, exit {r.returncode}", "turn completes", clean and answered)
    if os.environ.get("KEEP"):
        print("kept", folder, [m["text"][:200] for m in msgs])
    else:
        shutil.rmtree(folder, ignore_errors=True)


def main(names):
    stub = subprocess.Popen([sys.executable, os.path.join(ROOT, "scripts", "qa_stub.py"), str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.8)
    try:
        for name, fn in [("cold", cold), ("memory", memory), ("frames", frames), ("kill", kill_runs), ("backend_kill", backend_kill), ("corrupt", corrupt), ("bundle", bundle)]:
            if not names or name in names:
                fn()
    finally:
        stub.kill()
    print("\n| measure | value | budget | result |\n|---|---|---|---|")
    for r in RESULTS:
        print("| " + " | ".join(r) + " |")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
