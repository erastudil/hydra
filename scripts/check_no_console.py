#!/usr/bin/env python3
"""Fails when the desktop app could open a console window. Static checks plus an optional headless runtime check.

usage: check_no_console.py [--exe path/to/Hydra.exe] [--src hydra-desktop-rs/src] [--adapter hydra_cli/stdio_adapter.py] [--runtime path/to/linux/Hydra]
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_SUBSYSTEM = 2


def pe_subsystem(path):
    with open(path, "rb") as fh:
        data = fh.read(4096)
    if data[:2] != b"MZ":
        raise ValueError("not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("missing PE signature")
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        raise ValueError("bad optional header magic")
    return struct.unpack_from("<H", data, opt + 68)[0]


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def rust_sources(src):
    for base, _dirs, files in os.walk(src):
        for name in files:
            if name.endswith(".rs"):
                yield os.path.join(base, name)


def check_exe(exe, problems):
    if not os.path.isfile(exe):
        problems.append(f"exe missing: {exe}")
        return
    sub = pe_subsystem(exe)
    if sub != GUI_SUBSYSTEM:
        problems.append(f"{os.path.basename(exe)} PE subsystem is {sub}, must be {GUI_SUBSYSTEM} (windows GUI)")


def check_rust_spawns(src, problems):
    main = os.path.join(src, "main.rs")
    text = strip_comments(open(main, encoding="utf-8").read()) if os.path.isfile(main) else ""
    raw = open(main, encoding="utf-8").read() if os.path.isfile(main) else ""
    if not re.search(r'#!\[windows_subsystem\s*=\s*"windows"\]', raw):
        problems.append('main.rs lacks #![windows_subsystem = "windows"]')
    proc = os.path.join(src, "proc.rs")
    if not os.path.isfile(proc):
        problems.append("proc.rs missing")
    else:
        ptext = strip_comments(open(proc, encoding="utf-8").read())
        if "creation_flags(CREATE_NO_WINDOW)" not in ptext or "0x0800_0000" not in ptext.replace("0x08000000", "0x0800_0000"):
            problems.append("proc.rs does not apply CREATE_NO_WINDOW (0x08000000)")
    for path in rust_sources(src):
        rel = os.path.relpath(path, src)
        body = strip_comments(open(path, encoding="utf-8").read())
        if rel == "proc.rs":
            continue
        for m in re.finditer(r"\b(?:std::process::)?Command::new\b", body):
            line = body.count("\n", 0, m.start()) + 1
            problems.append(f"{rel}:{line} uses Command::new; use proc::command so no console window can open")
        if re.search(r"\bprocess::Command\b", body) and "use std::process::{" not in body and "Command" in body.replace("proc::command", ""):
            pass


def check_python(adapter, problems):
    text = open(adapter, encoding="utf-8").read()
    if "CREATE_NO_WINDOW = 0x08000000" not in text:
        problems.append("adapter lacks CREATE_NO_WINDOW = 0x08000000")
    main = text[text.index("def main("):] if "def main(" in text else ""
    if "hide_child_consoles()" not in main:
        problems.append("adapter main() does not call hide_child_consoles()")
    if 'creationflags"] = int(out.get("creationflags") or 0) | CREATE_NO_WINDOW' not in text:
        problems.append("hidden_popen_kwargs does not OR in CREATE_NO_WINDOW")


def check_runtime(binary, problems):
    """Run the app headless; exactly one top-level window may exist and no terminal emulator may start."""
    env = dict(os.environ, HYDRA_NO_STATE="1")
    proc = subprocess.Popen(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", "bash", "-c",
                             f'"{binary}" & P=$!; sleep 5; xdotool search --onlyvisible --name "" 2>/dev/null | wc -l; pgrep -c -f "xterm|gnome-terminal|konsole" || true; kill $P'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    out = proc.communicate(timeout=60)[0].split()
    if not out:
        problems.append("runtime check produced no output")
        return
    windows = int(out[0])
    if windows > 2:
        problems.append(f"runtime: {windows} visible windows, expected the app window only")
    if len(out) > 1 and out[1].isdigit() and int(out[1]) > 0:
        problems.append("runtime: a terminal emulator process appeared")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", action="append", default=[])
    ap.add_argument("--src", default=os.path.join(ROOT, "hydra-desktop-rs", "src"))
    ap.add_argument("--adapter", default=os.path.join(ROOT, "hydra_cli", "stdio_adapter.py"))
    ap.add_argument("--runtime")
    args = ap.parse_args()
    problems = []
    for exe in args.exe:
        check_exe(exe, problems)
    check_rust_spawns(args.src, problems)
    check_python(args.adapter, problems)
    if args.runtime:
        check_runtime(args.runtime, problems)
    for p in problems:
        print("FAIL:", p)
    if problems:
        return 1
    print("no-console checks: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
