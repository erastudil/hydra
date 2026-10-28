"""local_model_driver.py v3 — local multimodal model drives Hydra computer use.

v3 changes over v2 (owner-directed, 2026-10-27):
- KEYBOARD-FIRST actuation: new actions play_card {slot} (presses the slot's
  number key, per the game's own Settings > Input bindings recorded in the
  atlas) and end_turn (presses E). Every executed action logs its input mode:
  input=keyboard | click | atlas | guard.
- COMBAT PRIMER: an optional primer file is included in every prompt (screen
  anatomy + card rules: curses are unplayable, cost <= energy, attacks need a
  target click, skills self-resolve, End Turn when spent).
- CROPS: optional native-resolution region crops (hand / map) are sent as
  extra images with their full-screen bounds labelled, so a small VLM can
  read card text and map nodes that vanish at full-screen downscale. All
  returned coordinates remain full-screen 0-1000.
- NO-CONFIRM GUARD (permanent): a click is REFUSED when the model's own read
  describes an abandon/quit/delete confirmation state (are-you-sure, give up,
  abandon, result in a loss, save and quit), or when the click's target name
  matches a forbidden target (abandon/give up/quit/delete). On a dialog state
  the only allowed actions are escape/wait/stuck; anything else is refused
  and Escape is executed instead to back out. Every refusal is logged as
  GUARD-REFUSED with the read quoted.
- REPEAT GUARD: an action signature repeated 3 times with no screenshot
  change (32x18 thumbnail diff < 1.5) is blocked for the rest of the run,
  logged as GUARD-BLOCKED, and listed in every later prompt with its reason;
  the model must choose a different action.

v2 behaviour kept: atlas resolution with aHash fingerprint verification,
session-glow heartbeat, one malformed-reply retry, hard stops (step cap,
done/stuck, N consecutive no-change screenshots; N defaults to 3 in v3).
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
    "You are the driver of a desktop computer. You see screenshots and must output exactly ONE JSON "
    "object and nothing else. Fields: \"read\" (one short sentence: what the screen shows now), "
    "\"action\" (one of: click, key, chord, type, wait, play_card, end_turn, done, stuck), and as "
    "needed: \"target\" (name of a known UI element from the ATLAS list, when you want to click it), "
    "\"x\" and \"y\" (integers 0-1000, your own estimate of the click point; ALWAYS include them on a "
    "click), \"slot\" (for play_card: the hand slot number printed above the card), \"key\" (e.g. "
    "enter, escape), \"chord\" (e.g. alt+tab), \"text\" (for type), \"seconds\" (for wait). "
    "Coordinates are normalized full-screen: x=0 left edge, x=1000 right edge, y=0 top, y=1000 bottom. "
    "Extra images after the first are native-resolution CROPS of regions of the same screen; their "
    "labels give their names and full-screen bounds. Use crops to READ details (card names, costs, "
    "node icons). To CLICK something inside a crop region, answer the click with \"frame\" set to "
    "that crop's name (hand, map, enemy) and x,y in the CROP's own 0-1000 space (0,0 = the crop's "
    "top-left, 1000,1000 = its bottom-right); the driver converts to full-screen. Clicks without a "
    "frame use full-screen coordinates. "
    "HARD RULE: never confirm an abandon, give up, quit, save-and-quit, or delete dialog in any app. "
    "If such a dialog is on screen, answer key escape to back out. "
    "Output \"done\" only when the goal is fully achieved. Output \"stuck\" when you cannot make "
    "progress. No prose outside the JSON."
)

DANGER_READ = ["are you sure", "abandon", "give up", "result in a loss", "save and quit", "delete this", "delete the"]
DANGER_TARGET = ["abandon", "give up", "give_up", "quit", "delete"]

CROPS = {
    "hand": {"box": (380, 790, 1560, 1080), "label": "HAND CROP (name: hand; native resolution): the bottom strip of the screen holding your cards. Full-screen bounds: x 198-812, y 731-1000 (0-1000 scale). Slot numbers are printed above the cards."},
    "map": {"box": (330, 90, 1620, 1010), "label": "MAP CROP (name: map; native resolution): the map area of the screen. Full-screen bounds: x 172-844, y 83-935 (0-1000 scale). Node icons: small face = Enemy, horned = Elite, ? = Unknown, chest = Treasure, flame = Rest, shop bag = Shop, big icon at top = Boss."},
    "enemy": {"box": (1240, 430, 1700, 830), "label": "ENEMY CROP (name: enemy; native resolution): the enemy area of the combat screen. Full-screen bounds: x 646-885, y 398-769 (0-1000 scale). To target the enemy, click with frame \"enemy\" at the CENTER of its body in this crop."},
}

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

def crop_b64(img_bytes, box):
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB").crop(box)
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
                lines.append(f"  {name} [screen: {st['name']}] {el.get('note', '')}")
        kb = self.data.get("keybinds")
        if kb:
            b = kb.get("bindings", {})
            lines.append("KEYBINDS (from the game's own Settings > Input screen; profile: mouse+keyboard): "
                         f"Select Card #N = number key N (slot 10 = 0); End Turn = E; Confirm = E; "
                         f"Cancel/Exit = Escape; View Map = M; Peek = Space.")
        return "\n".join(lines)
    def resolve(self, target, shot_hash):
        els = self.elements()
        if target not in els: return None
        st, el = els[target]
        if el.get("x") is None or el.get("y") is None:
            return None
        if el.get("verify") is False:
            return (el["x"], el["y"], "atlas", False)
        fp = st.get("fingerprint")
        if fp and shot_hash and hamming(fp, shot_hash) <= AHASH_THRESHOLD:
            return (el["x"], el["y"], "atlas", True)
        return ("stale", st.get("name"), el)

# ---------- model ----------
def ask_model(goal, primer, atlas_block, notes, images):
    user = f"GOAL: {goal}\n\n{primer}\n\n{atlas_block}\n\n{notes}\n\nWhat is the single next action? Reply with the JSON object only."
    content = [{"type": "text", "text": user}]
    for label, b64 in images:
        content.append({"type": "text", "text": label})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    payload = {"messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": content}],
               "temperature": 0.15, "max_tokens": 1024}
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
    if act not in ("click", "key", "chord", "type", "wait", "play_card", "end_turn", "done", "stuck"):
        raise ValueError(f"bad action {act!r}")
    if act == "click" and not obj.get("target"):
        obj["x"] = max(0, min(1000, int(obj["x"]))); obj["y"] = max(0, min(1000, int(obj["y"])))
    if act == "click" and obj.get("target"):
        if obj.get("x") is not None: obj["x"] = max(0, min(1000, int(obj["x"])))
        if obj.get("y") is not None: obj["y"] = max(0, min(1000, int(obj["y"])))
    if act == "click" and obj.get("frame"):
        if obj["frame"] not in CROPS: raise ValueError(f"bad frame {obj['frame']!r}")
        obj["x"] = max(0, min(1000, int(obj["x"]))); obj["y"] = max(0, min(1000, int(obj["y"])))
    if act == "play_card":
        obj["slot"] = int(obj["slot"])
        if not (1 <= obj["slot"] <= 10): raise ValueError("slot out of range")
    return obj

def signature(act):
    a = act["action"]
    if a == "play_card": return f"play_card:{act['slot']}"
    if a == "end_turn": return "end_turn"
    if a == "click": return f"click:{act.get('target') or ''}:{(act.get('x') or 0)//50},{(act.get('y') or 0)//50}"
    if a == "key": return f"key:{act.get('key')}"
    if a == "chord": return f"chord:{act.get('chord')}"
    return a

# ---------- session glow heartbeat ----------
_stop_hb = threading.Event()
_pid = None
def heartbeat():
    while not _stop_hb.is_set():
        try:
            SESSION_FILE.write_text(json.dumps({
                "active": True, "loop_pid": _pid,
                "updated": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
        except Exception:
            pass
        _stop_hb.wait(0.5)

def main():
    global _pid
    _pid = __import__("os").getpid()
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal-file", required=True)
    ap.add_argument("--label", default="run")
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--atlas", default="")
    ap.add_argument("--primer-file", default="")
    ap.add_argument("--crops", default="")
    ap.add_argument("--nochange-limit", type=int, default=3)
    ap.add_argument("--focus-target", default="",
                    help="window title substring to focus before keyboard actions (keyboard input goes to the focused window; a stray console window in front silently eats keypresses)")
    args = ap.parse_args()
    goal = Path(args.goal_file).read_text(encoding="utf-8").strip()
    primer = Path(args.primer_file).read_text(encoding="utf-8").strip() if args.primer_file else ""
    crop_names = [c.strip() for c in args.crops.split(",") if c.strip() in CROPS]
    atlas = Atlas(args.atlas or None)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / f"{stamp}-{args.label}"; run_dir.mkdir(parents=True, exist_ok=True)
    logf = (run_dir / "run.log").open("a", encoding="utf-8")
    def log(*a):
        s = " ".join(str(x) for x in a)
        try: print(s, flush=True)
        except Exception: pass
        logf.write(s + "\n"); logf.flush()
    hb = threading.Thread(target=heartbeat, daemon=True); hb.start()
    def focus_game():
        if not args.focus_target: return
        fr = ipc("focus", {"target": args.focus_target})
        if (fr.get("result") or {}).get("isError"):
            log(f"focus '{args.focus_target}' FAILED: {fr.get('result')}")
        time.sleep(0.3)
    log(f"RUN {run_dir} label={args.label} max_steps={args.max_steps} atlas={args.atlas} crops={crop_names} pid={_pid}")
    log(f"GOAL: {goal}")
    if atlas.data:
        log(f"ATLAS loaded: {len(atlas.elements())} elements across {len(atlas.data.get('states', []))} states; keybinds={'yes' if atlas.data.get('keybinds') else 'no'}")
    history = []
    prev_bytes = None; nochange = 0
    focus_game()
    blocked = {}          # signature -> reason
    last_sig = None; sig_noeffect = 0
    guard_note = ""
    outcome = "step-cap"
    try:
        for step in range(1, args.max_steps + 1):
            atlas.reload()
            name = f"step{step:02d}.png"
            r = ipc("screenshot", {"name": name})
            if not r.get("ok"):
                log(f"step {step}: screenshot FAILED {r}"); outcome = "ipc-failure"; break
            img = (SHOTS / name).read_bytes()
            (run_dir / name).write_bytes(img)
            shot_hash = ahash_hex(img)
            if prev_bytes is not None:
                d = thumb_diff(prev_bytes, img)
                if d < 1.5:
                    nochange += 1
                    if last_sig:
                        sig_noeffect += 1
                        if sig_noeffect >= 3 and last_sig not in blocked:
                            blocked[last_sig] = "produced no screen change 3 times"
                            log(f"step {step}: GUARD-BLOCKED {last_sig} (no effect x{sig_noeffect})")
                    log(f"step {step}: screen unchanged (diff {d:.2f}), streak {nochange}")
                    if nochange >= args.nochange_limit:
                        outcome = "no-change"; break
                else:
                    nochange = 0; sig_noeffect = 0
            prev_bytes = img
            images = [("FULL SCREEN:", downscale_b64(img))]
            for cn in crop_names:
                images.append((CROPS[cn]["label"], crop_b64(img, CROPS[cn]["box"])))
            notes = f"Recent steps: {'; '.join(history[-4:]) or 'none'}"
            if blocked:
                notes += "\nBLOCKED ACTIONS (do not repeat these; pick a different card or end the turn): " + \
                         "; ".join(f"{k} ({v})" for k, v in blocked.items())
            if guard_note:
                notes += f"\nGUARD NOTE: {guard_note}"
            raw = ask_model(goal, primer, atlas.prompt_block(), notes, images)
            try:
                act = extract_action(raw)
            except Exception as e:
                log(f"step {step}: MALFORMED ({e}); raw={raw[:300]!r}; retrying once")
                raw = ask_model(goal, primer, atlas.prompt_block(), notes, images)
                try:
                    act = extract_action(raw)
                except Exception as e2:
                    log(f"step {step}: MALFORMED again ({e2}); raw={raw[:300]!r}; STOP")
                    outcome = "malformed"; break
            read = str(act.get("read", ""))
            a = act["action"]
            sig = signature(act)
            log(f"step {step}: read={read!r} action={a} target={act.get('target')} slot={act.get('slot')} xy=({act.get('x')},{act.get('y')}) key={act.get('key')} sig={sig} hash={shot_hash}")
            history.append(f"step{step}:{a}({act.get('target') or act.get('slot') or act.get('key') or ''})")
            if a in ("done", "stuck"):
                outcome = a; break
            # ---- NO-CONFIRM GUARD ----
            read_low = read.lower()
            danger_state = any(p in read_low for p in DANGER_READ)
            tgt_low = str(act.get("target") or "").lower()
            danger_target = any(p in tgt_low for p in DANGER_TARGET)
            if a == "click" and (danger_state or danger_target):
                log(f"step {step}: GUARD-REFUSED click (danger_state={danger_state} danger_target={danger_target}) read={read!r}; pressing Escape to back out input=guard")
                ipc("key", {"key": "escape"})
                guard_note = "A confirmation-style dialog was refused and backed out of with Escape. NEVER confirm abandon/give up/quit/delete dialogs."
                last_sig = "guard:escape"; time.sleep(1.2); continue
            if danger_state and a not in ("key", "wait"):
                log(f"step {step}: GUARD-REFUSED {a} during dialog state read={read!r}; pressing Escape input=guard")
                ipc("key", {"key": "escape"})
                guard_note = "A confirmation-style dialog was refused and backed out of with Escape. NEVER confirm abandon/give up/quit/delete dialogs."
                last_sig = "guard:escape"; time.sleep(1.2); continue
            # ---- REPEAT GUARD ----
            if sig in blocked:
                log(f"step {step}: GUARD-BLOCKED repeat of {sig}; not executed (blocked: {blocked[sig]}) input=guard")
                guard_note = f"Your last action {sig} is BLOCKED: {blocked[sig]}. Choose a different action now."
                last_sig = sig; time.sleep(0.5); continue
            # ---- EXECUTE ----
            if a == "click":
                x, y = act.get("x"), act.get("y")
                resolution = "model"; verified = "-"
                frame = act.get("frame")
                if frame:
                    bx0, by0, bx1, by1 = CROPS[frame]["box"]
                    fx = int((bx0 + (x / 1000.0) * (bx1 - bx0)) / 1920 * 1000)
                    fy = int((by0 + (y / 1000.0) * (by1 - by0)) / 1080 * 1000)
                    log(f"step {step}: frame={frame} crop_xy=({x},{y}) -> full_xy=({fx},{fy})")
                    x, y = fx, fy
                tgt = act.get("target")
                if tgt:
                    res = atlas.resolve(tgt, shot_hash)
                    if res and res[0] == "stale":
                        log(f"step {step}: atlas target {tgt} STALE (state {res[1]} fingerprint mismatch); falling back to model coords")
                    elif res:
                        x, y, resolution, verified = res
                        log(f"step {step}: atlas resolved {tgt} -> ({x},{y}) verified={verified}")
                    else:
                        log(f"step {step}: target {tgt} not resolvable in atlas; using model coords")
                if x is None or y is None:
                    log(f"step {step}: click without coordinates; STOP"); outcome = "malformed"; break
                px, py = int(x * 1920 / 1000), int(y * 1080 / 1000)
                r = ipc("click", {"x": px, "y": py})
                log(f"step {step}: executed click px=({px},{py}) resolution={resolution} verified={verified} input={'atlas' if resolution == 'atlas' else 'click'} ok={r.get('ok')}")
            elif a == "play_card":
                focus_game()
                digit = str(act["slot"] % 10)
                r = ipc("key", {"key": digit})
                log(f"step {step}: executed play_card slot={act['slot']} key={digit} input=keyboard ok={r.get('ok')}")
            elif a == "end_turn":
                focus_game()
                r = ipc("key", {"key": "e"})
                log(f"step {step}: executed end_turn key=e input=keyboard ok={r.get('ok')}")
            elif a == "key":
                focus_game()
                r = ipc("key", {"key": act["key"]}); log(f"step {step}: executed key {act['key']} input=keyboard ok={r.get('ok')}")
            elif a == "chord":
                focus_game()
                r = ipc("chord", {"chord": act["chord"]}); log(f"step {step}: executed chord {act['chord']} input=keyboard ok={r.get('ok')}")
            elif a == "type":
                r = ipc("type", {"text": act["text"]}); log(f"step {step}: executed type input=keyboard ok={r.get('ok')}")
            elif a == "wait":
                time.sleep(float(act.get("seconds", 2)))
                log(f"step {step}: waited {act.get('seconds', 2)}s")
            last_sig = sig
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
