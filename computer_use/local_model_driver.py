"""local_model_driver.py v2 — a LOCAL multimodal model drives Hydra computer use,
with per-app UI memory (atlas) and a session-level glow heartbeat.

Loop: screenshot (via live-driver IPC) -> downscale -> local model returns ONE
JSON action -> validate -> execute via IPC -> log. The model may name an atlas
target ({"action":"click","target":"menu.singleplayer"}); the click then
resolves to the atlas coordinates ONLY if the fresh screenshot's 16x9 aHash
matches the fingerprint of the state that owns the element (hamming <= 28), or
the element is marked verify=false (fixed-position on a variable screen). On a
failed verification the model's own x,y are used and the entry is logged stale.
Every step logs resolution: "atlas" (with verified true/false) or "model".

Session glow: a heartbeat thread writes session_state.json into the live-driver
workdir every 0.5 s while this loop is alive (including during model inference);
a final {"active": false} write happens on exit. screen_glow.py keeps the glow
lit while that file is fresh (<= 30 s).

Hard stops: step cap, model 'done'/'stuck', two consecutive no-change
screenshots (32x18 grayscale mean-abs-diff < 1.5), malformed reply twice.
"""
import argparse, base64, io, json, re, sys, threading, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HOME = Path(r"C:\Users\jpm05\hydra\local-driver")
LIVE = Path(r"C:\Users\jpm05\hydra\live-driver")
CMD = LIVE / "cmd"; DONE = LIVE / "done"; SHOTS = LIVE / "shots"
RUNS = HOME / "runs"
SESSION_FILE = LIVE / "session_state.json"
MODEL_URL = "http://127.0.0.1:8080/v1/chat/completions"
AHASH_THRESHOLD = 28

SYSTEM = (
    "You are the driver of a desktop computer. You see one screenshot per step and must output "
    "exactly ONE JSON object and nothing else. Fields: \"read\" (one short sentence: what the screen "
    "shows now), \"action\" (one of: click, key, chord, type, wait, done, stuck), and as needed: "
    "\"target\" (the name of a known UI element from the ATLAS list, when you want to click it), "
    "\"x\" and \"y\" (integers 0-1000, your own estimate of the click point; ALWAYS include them on a "
    "click, even when you also give a target), \"key\" (e.g. enter, escape), \"chord\" (e.g. alt+tab), "
    "\"text\" (for type), \"seconds\" (for wait). Coordinates are normalized: x=0 is the left edge, "
    "x=1000 the right edge, y=0 the top, y=1000 the bottom of the screenshot. "
    "Output \"done\" only when the goal is fully achieved. Output \"stuck\" when you cannot make "
    "progress. No prose outside the JSON."
)

# ---------- IPC ----------
_seq = [0]
def ipc(action, params=None, timeout=30):
    _seq[0] += 1
    cid = f"lm{int(time.time()*1000)}{_seq[0]}"
    msg = {"id": cid, "action": action, "params": params or {}}
    (CMD / f"{cid}.json").write_text(json.dumps(msg), encoding="utf-8")
    done = DONE / f"{cid}.json"; t0 = time.time()
    while time.time() - t0 < timeout:
        if done.exists():
            try: return json.loads(done.read_text(encoding="utf-8"))
            except Exception: time.sleep(0.1); continue
        time.sleep(0.12)
    return {"id": cid, "ok": False, "result": {"error": "ipc timeout"}}

# ---------- image helpers ----------
def ahash_hex(img_bytes):
    im = Image.open(io.BytesIO(img_bytes)).convert("L").resize((16, 9))
    px = list(im.getdata()); avg = sum(px) / len(px)
    bits = "".join("1" if p > avg else "0" for p in px)
    return f"{int(bits, 2):036x}"

def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")

def thumb_diff(b1, b2):
    def t(b):
        im = Image.open(io.BytesIO(b)).convert("L").resize((32, 18))
        return list(im.getdata())
    p1, p2 = t(b1), t(b2)
    return sum(abs(a - b) for a, b in zip(p1, p2)) / len(p1)

def downscale_b64(img_bytes, width=1408):
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    if im.width > width:
        im = im.resize((width, int(im.height * width / im.width)))
    buf = io.BytesIO(); im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()

# ---------- atlas ----------
class Atlas:
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.data = None; self.mtime = 0
        self.reload()
    def reload(self):
        if not self.path or not self.path.exists(): return
        m = self.path.stat().st_mtime
        if m != self.mtime:
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                self.mtime = m
            except Exception:
                pass
    def elements(self):
        out = {}
        if not self.data: return out
        for st in self.data.get("states", []):
            for name, el in st.get("elements", {}).items():
                out[name] = (st, el)
        return out
    def prompt_block(self):
        if not self.data: return ""
        lines = ["ATLAS (taught UI memory for this app — prefer \"target\" with these names when the screen matches):"]
        for st in self.data.get("states", []):
            for name, el in st.get("elements", {}).items():
                note = el.get("note", "")
                lines.append(f"  {name} [screen: {st['name']}] {note}")
        return "\n".join(lines)
    def resolve(self, target, shot_hash):
        """Return (x, y, resolution, verified) using normalized 0-1000 atlas coords."""
        els = self.elements()
        if target not in els: return None
        st, el = els[target]
        if el.get("x") is None or el.get("y") is None:
            return None  # entry exists but not grounded yet -> model grounds it
        if el.get("verify") is False:
            return (el["x"], el["y"], "atlas", False)
        fp = st.get("fingerprint")
        if fp and shot_hash and hamming(fp, shot_hash) <= AHASH_THRESHOLD:
            return (el["x"], el["y"], "atlas", True)
        return ("stale", st.get("name"), el)

# ---------- model ----------
def ask_model(goal, atlas_block, b64, history):
    user = f"GOAL: {goal}\n\n{atlas_block}\n\nRecent steps: {history}\n\nWhat is the single next action? Reply with the JSON object only."
    payload = {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": user},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "temperature": 0.15, "max_tokens": 1024,
    }
    req = urllib.request.Request(MODEL_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"].get("content") or ""

def extract_action(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m: raise ValueError("no JSON object in reply")
    obj = json.loads(m.group(0))
    act = obj.get("action")
    if act not in ("click", "key", "chord", "type", "wait", "done", "stuck"):
        raise ValueError(f"bad action {act!r}")
    if act == "click":
        if obj.get("target"):
            pass  # atlas may resolve; x/y still required as fallback
        else:
            obj["x"] = int(obj["x"]); obj["y"] = int(obj["y"])
        if "x" in obj and obj["x"] is not None: obj["x"] = max(0, min(1000, int(obj["x"])))
        if "y" in obj and obj["y"] is not None: obj["y"] = max(0, min(1000, int(obj["y"])))
    return obj

# ---------- session glow heartbeat ----------
_stop_hb = threading.Event()
def heartbeat():
    while not _stop_hb.is_set():
        try:
            SESSION_FILE.write_text(json.dumps({
                "active": True, "loop_pid": _pid,
                "updated": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        except Exception:
            pass
        _stop_hb.wait(0.5)

_pid = None

def main():
    global _pid
    _pid = __import__("os").getpid()
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal-file", required=True)
    ap.add_argument("--label", default="run")
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--atlas", default="")
    args = ap.parse_args()
    goal = Path(args.goal_file).read_text(encoding="utf-8").strip()
    atlas = Atlas(args.atlas or None)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / f"{stamp}-{args.label}"; run_dir.mkdir(parents=True, exist_ok=True)
    logf = (run_dir / "run.log").open("a", encoding="utf-8")
    def log(*a):
        s = " ".join(str(x) for x in a)
        print(s, flush=True); logf.write(s + "\n"); logf.flush()
    hb = threading.Thread(target=heartbeat, daemon=True); hb.start()
    log(f"RUN {run_dir} label={args.label} max_steps={args.max_steps} atlas={args.atlas} pid={_pid}")
    log(f"GOAL: {goal}")
    if atlas.data:
        log(f"ATLAS loaded: {len(atlas.elements())} elements across {len(atlas.data.get('states', []))} states")
    history = []
    prev_bytes = None; nochange = 0
    outcome = "step-cap"
    try:
        for step in range(1, args.max_steps + 1):
            atlas.reload()
            name = f"step{step:02d}.png"
            r = ipc("screenshot", {"name": name})
            if not r.get("ok"):
                log(f"step {step}: screenshot FAILED {r}"); outcome = "ipc-failure"; break
            src = SHOTS / name
            img = src.read_bytes()
            (run_dir / name).write_bytes(img)
            shot_hash = ahash_hex(img)
            if prev_bytes is not None:
                d = thumb_diff(prev_bytes, img)
                if d < 1.5:
                    nochange += 1
                    log(f"step {step}: screen unchanged (diff {d:.2f}), streak {nochange}")
                    if nochange >= 2:
                        outcome = "no-change"; break
                else:
                    nochange = 0
            prev_bytes = img
            b64 = downscale_b64(img)
            raw = ask_model(goal, atlas.prompt_block(), b64, "; ".join(history[-4:]) or "none")
            try:
                act = extract_action(raw)
            except Exception as e:
                log(f"step {step}: MALFORMED ({e}); raw={raw[:300]!r}; retrying once")
                raw = ask_model(goal, atlas.prompt_block(), b64, "; ".join(history[-4:]) or "none")
                try:
                    act = extract_action(raw)
                except Exception as e2:
                    log(f"step {step}: MALFORMED again ({e2}); raw={raw[:300]!r}; STOP")
                    outcome = "malformed"; break
            read = act.get("read", "")
            a = act["action"]
            resolution = "-"; verified = "-"
            log(f"step {step}: read={read!r} action={a} target={act.get('target')} xy=({act.get('x')},{act.get('y')}) key={act.get('key')} text={act.get('text')!r} hash={shot_hash}")
            history.append(f"step{step}:{a}({act.get('target') or act.get('key') or ''})")
            if a in ("done", "stuck"):
                outcome = a; break
            if a == "click":
                x, y = act.get("x"), act.get("y")
                tgt = act.get("target")
                if tgt:
                    res = atlas.resolve(tgt, shot_hash)
                    if res and res[0] == "stale":
                        log(f"step {step}: atlas target {tgt} STALE (state {res[1]} fingerprint mismatch); falling back to model coords")
                        resolution = "model"
                    elif res:
                        x, y, resolution, verified = res
                        log(f"step {step}: atlas resolved {tgt} -> ({x},{y}) verified={verified}")
                    else:
                        resolution = "model"
                        log(f"step {step}: target {tgt} not resolvable in atlas; using model coords")
                else:
                    resolution = "model"
                if x is None or y is None:
                    log(f"step {step}: click without coordinates; STOP"); outcome = "malformed"; break
                px, py = int(x * 1920 / 1000), int(y * 1080 / 1000)
                r = ipc("click", {"x": px, "y": py})
                log(f"step {step}: executed click px=({px},{py}) resolution={resolution} verified={verified} ok={r.get('ok')}")
            elif a == "key":
                r = ipc("key", {"key": act["key"]}); log(f"step {step}: executed key {act['key']} ok={r.get('ok')}")
            elif a == "chord":
                r = ipc("chord", {"chord": act["chord"]}); log(f"step {step}: executed chord {act['chord']} ok={r.get('ok')}")
            elif a == "type":
                r = ipc("type", {"text": act["text"]}); log(f"step {step}: executed type ok={r.get('ok')}")
            elif a == "wait":
                time.sleep(float(act.get("seconds", 2)))
                log(f"step {step}: waited {act.get('seconds', 2)}s")
            time.sleep(1.2)
    finally:
        _stop_hb.set()
        try:
            SESSION_FILE.write_text(json.dumps({
                "active": False, "loop_pid": _pid,
                "updated": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        except Exception:
            pass
    log(f"OUTCOME: {outcome}")
    logf.close()

if __name__ == "__main__":
    main()
