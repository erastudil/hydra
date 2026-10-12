#!/usr/bin/env python3
"""Checks dist/Hydra is self-contained: layout, GUI subsystem, search path, and that the agent boots with only
the stdlib plus the bundled pure-python wheels (isolated interpreter, no site-packages, no checkout on the path)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_no_console import pe_subsystem  # noqa: E402


def main(root):
    root = os.path.abspath(root)
    problems = []
    exe = os.path.join(root, "Hydra.exe")
    if not os.path.isfile(exe):
        problems.append("Hydra.exe missing")
    elif pe_subsystem(exe) != 2:
        problems.append("Hydra.exe is not a GUI subsystem exe")
    rt = os.path.join(root, "runtime")
    if not os.path.isfile(os.path.join(rt, "python.exe")):
        problems.append("runtime/python.exe missing")
    pths = [f for f in os.listdir(rt) if f.endswith("._pth")] if os.path.isdir(rt) else []
    if not pths:
        problems.append("runtime has no ._pth file")
    else:
        text = open(os.path.join(rt, pths[0])).read().split()
        for needed in ("..\\app", "Lib\\site-packages", "import"):
            if not any(needed in t for t in text):
                problems.append(f"._pth lacks {needed}")
    app = os.path.join(root, "app")
    if not os.path.isfile(os.path.join(app, "hydra_cli", "__init__.py")):
        problems.append("app/hydra_cli missing")
    sp = os.path.join(rt, "Lib", "site-packages")
    if not os.path.isdir(os.path.join(sp, "prompt_toolkit")):
        problems.append("prompt_toolkit wheel missing from runtime")
    if not problems:
        env = {"PATH": "", "HOME": "/tmp/hydra-bundle-home", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": app + os.pathsep + sp}
        os.makedirs(env["HOME"], exist_ok=True)
        code = ("import sys; sys.path[:0]=[%r,%r]; from hydra_cli.stdio_adapter import main; sys.exit(main([]))" % (app, sp))
        p = subprocess.Popen([sys.executable, "-I", "-S", "-c", code], cwd=app, env={"PATH": "", "HOME": env["HOME"], "PYTHONIOENCODING": "utf-8"},
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = p.stdout.readline()
            hello = json.loads(line)
            if hello.get("type") != "hello":
                problems.append(f"adapter did not say hello: {line!r}")
            p.stdin.write('{"type":"shutdown"}\n')
            p.stdin.flush()
            p.wait(timeout=20)
        except Exception as exc:
            err = p.stderr.read() if p.poll() is not None else ""
            problems.append(f"agent failed to boot isolated: {exc} {err[-400:]}")
        finally:
            if p.poll() is None:
                p.kill()
    for pr in problems:
        print("FAIL:", pr)
    if problems:
        return 1
    print("bundle checks: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/Hydra"))
