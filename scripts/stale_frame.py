#!/usr/bin/env python3
"""D21 check on the real build under Xvfb: after input and a finished turn, the presented frame must already be current.
Grab the screen 3 s after Enter, nudge the pointer (which forces a repaint), grab again: the two must match.
Runs with reduced motion, so the whole screen is compared (a fixed-pixel mask for the pulsing dot went stale when the header moved).
Needs mss and pillow (python -m venv /tmp/qa-venv && /tmp/qa-venv/bin/pip install mss pillow). usage: stale_frame.py [runs]"""
import os, subprocess, sys, tempfile, time
import mss
from PIL import Image, ImageChops

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.environ.get("HYDRA_BIN", os.path.join(ROOT, "hydra-desktop-rs", "target", "release", "Hydra"))
PORT = 18996


def grab(disp):
    os.environ["DISPLAY"] = disp
    with mss.MSS() as s:
        g = s.grab({"left": 0, "top": 0, "width": 1280, "height": 800})
        return Image.frombytes("RGB", g.size, g.rgb)


def main(runs):
    stub = subprocess.Popen([sys.executable if "qa-venv" not in sys.executable else "/usr/bin/python3", os.path.join(ROOT, "scripts", "qa_stub.py"), str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    disp = ":%d" % (400 + os.getpid() % 300)
    x = subprocess.Popen(["Xvfb", disp, "-screen", "0", "1280x800x24", "-nolisten", "tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    h = tempfile.mkdtemp(prefix="hydra-stale-")
    os.makedirs(os.path.join(h, "proj"))
    env = dict(os.environ, DISPLAY=disp, HOME=h, HYDRA_REPO=ROOT, HYDRA_MODEL="local", LOCAL_AI_BASE=f"http://127.0.0.1:{PORT}", HYDRA_SIZE="1280x800", HYDRA_CWD=os.path.join(h, "proj"),
               HYDRA_NO_ANIM="1")  # reduced motion: the status dot holds still, so any pixel change is stale state, with no mask to drift
    app = subprocess.Popen([BIN], cwd=os.path.join(h, "proj"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    xd = lambda *a: subprocess.run(["xdotool", *a], env=env, capture_output=True)
    stale = 0
    try:
        time.sleep(4)
        # with no window manager nothing gives the window keyboard focus, and typed keys would go nowhere
        for wid in xd("search", "--name", "Hydra").stdout.split():
            xd("windowfocus", wid)
        for i in range(runs):
            xd("mousemove", "1270", "790")
            time.sleep(0.6)
            before = grab(disp)
            xd("mousemove", "500", "708", "click", "1")  # the composer at 1280x800
            time.sleep(0.3)
            ask = i % 2 == 1  # odd runs open the approval modal (the case Violet saw), even runs finish a plain turn
            xd("type", f"WRITE file {i}" if ask else f"hello {i}")
            xd("key", "Return")
            xd("mousemove", "1270", "790")  # park the pointer; no motion from here until the first grab
            time.sleep(3)
            a = grab(disp)
            xd("mousemove", "1260", "780")
            time.sleep(0.6)
            b = grab(disp)
            diff = ImageChops.difference(a, b).getbbox()
            sent = ImageChops.difference(before, b).getbbox() is not None
            if os.environ.get("KEEP"):
                keep = os.environ["KEEP"]; a.save(f"{keep}_a{i}.png"); b.save(f"{keep}_b{i}.png")
            # a run whose input never reached the app proves nothing, so it fails the check instead of passing it
            print(f"run {i}: {'INPUT NOT DELIVERED' if not sent else 'STALE ' + str(diff) if diff else 'current'}", flush=True)
            stale += bool(diff) or not sent
            if ask:
                xd("key", "Escape")
                time.sleep(1.5)
    finally:
        app.kill(); x.kill(); stub.kill()
    print(f"stale or undelivered: {stale}/{runs}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
