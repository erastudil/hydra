"""
Hydra Sovereign Consolidated Verification Gate (Ponytail Doctrine).
Single-grip runnable verification. Zero mock bloat. Real subprocesses and exit codes.
Progen dialect output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _banner() -> None:
    print("hydra verification : starting consolidated gate.")


def verify_manifests(repo_dir: str) -> None:
    # 1. Package version invariant
    from hydra_cli import __version__

    # 2. Installer manifests match hydra_cli/ files
    pkg_dir = os.path.join(repo_dir, "hydra_cli")
    runtime_files = set()
    for root, _, files in os.walk(pkg_dir):
        for f in files:
            if f.endswith((".py", ".json")):
                rel = os.path.relpath(os.path.join(root, f), repo_dir).replace("\\", "/")
                runtime_files.add(rel)

    for script_name in ("install.ps1", "install.sh"):
        script_path = os.path.join(repo_dir, script_name)
        assert os.path.isfile(script_path), f"Missing {script_name}"
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
        for rf in runtime_files:
            assert rf in content, f"{script_name} missing {rf}"

    # 3. Catalog and MCP default config
    catalog_path = os.path.join(pkg_dir, "catalog.json")
    assert os.path.isfile(catalog_path), "Missing catalog.json"
    with open(catalog_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    assert "model_providers" in catalog and "aliases" in catalog, "catalog.json missing required keys"

    mcp_path = os.path.join(pkg_dir, "mcp_servers.default.json")
    assert os.path.isfile(mcp_path), "Missing mcp_servers.default.json"
    with open(mcp_path, "r", encoding="utf-8") as f:
        mcp = json.load(f)
    assert "mcpServers" in mcp, "mcp_servers.default.json missing 'mcpServers'"

    print("manifest verification : exit 0.")


def verify_cli_subprocesses(repo_dir: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    subproc_cases = [
        (["--help"], "USAGE:"),
        (["--version"], "1.2.2"),
        (["banner"], "Sovereign Multi-Headed AI Shell"),
        (["setup"], "HYDRA SETUP & INTEGRATION GUIDE"),
        (["--list-models"], "Hydra Registered Models & Aliases"),
    ]

    for args, expected_substr in subproc_cases:
        proc = subprocess.run(
            [sys.executable, "-m", "hydra_cli"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=repo_dir,
            env=env,
            timeout=10,
        )
        assert proc.returncode == 0, f"hydra {' '.join(args)} failed with exit {proc.returncode}: {proc.stderr}"
        assert proc.stdout is not None and expected_substr in proc.stdout, (
            f"hydra {' '.join(args)} output missing '{expected_substr}'"
        )

    print("cli subprocess verification : exit 0.")


def verify_agent_repl(repo_dir: str) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_dir + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "-m", "hydra_cli", "agent", "-i", "--model", "glm 5.3 flash"],
        input="/exit\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=repo_dir,
        env=env,
        timeout=15,
    )
    assert proc.returncode == 0, f"Interactive agent REPL failed with exit {proc.returncode}: {proc.stderr}"
    assert proc.stdout is not None and "HYDRA CODING AGENT" in proc.stdout, "Missing coding agent header"
    assert proc.stdout is not None and "Exiting Hydra agent session" in proc.stdout, "Missing exit confirmation"

    print("agent repl verification : exit 0.")


def verify_tui_invariants(repo_dir: str) -> None:
    sys.path.insert(0, repo_dir)
    from hydra_cli.tui import HydraTUI
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.output import DummyOutput

    # 1. Default mouse support disabled for host scrollback
    tui_default = HydraTUI(commands=[], pt_output=DummyOutput())
    assert tui_default._mouse_support is False, "Default mouse_support must be False"

    # Opt-in mouse support
    tui_mouse = HydraTUI(commands=[], mouse_support=True, pt_output=DummyOutput())
    assert tui_mouse._mouse_support is True, "Explicit mouse_support=True must be respected"

    # 2. Root layout pinning: flexible top spacer absorbs viewport height
    app = tui_default._build_app()
    root_container = app.layout.container
    assert isinstance(root_container, HSplit), "Root container must be HSplit"
    top_child = root_container.children[0]
    assert isinstance(top_child, Window), "Top child of root HSplit must be Window spacer"
    assert top_child.dont_extend_height() is False, "Top spacer must expand to absorb upper rows"

    # 3. Windows console input Ctrl+Space handler
    if os.name == "nt":
        from hydra_cli.win_console import _is_ctrl_space, VK_SPACE, LEFT_CTRL_PRESSED
        from prompt_toolkit.win32_types import INPUT_RECORD

        record = INPUT_RECORD()
        event = record.Event.KeyEvent
        event.VirtualKeyCode = VK_SPACE
        event.ControlKeyState = LEFT_CTRL_PRESSED
        event.uChar.UnicodeChar = "\x00"
        assert _is_ctrl_space(event) is True, "Ctrl+Space record must be recognized"

    print("tui invariants verification : exit 0.")


def main() -> int:
    t0 = time.perf_counter()
    _banner()

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    verify_manifests(repo_dir)
    verify_cli_subprocesses(repo_dir)
    verify_agent_repl(repo_dir)
    verify_tui_invariants(repo_dir)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"\nhydra gate status : pass.")
    print(f"execution duration : {elapsed_ms:.1f}ms.")
    print(f"truth invariant : exit 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
