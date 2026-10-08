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


def verify_provider_priority(repo_dir: str) -> None:
    sys.path.insert(0, repo_dir)
    from hydra_cli.providers import get_frontier_providers

    old_env = {k: os.environ.get(k) for k in [
        "HF_TOKEN", "CHEAPERINFERENCE_API_KEY", "AI_GATEWAY_API_KEY",
        "OPENROUTER_API_KEY", "MODAL_ENDPOINT_URL", "RUNPOD_API_KEY", "RUNPOD_ENDPOINT_ID"
    ]}
    try:
        os.environ["HF_TOKEN"] = "hf-dummy"
        os.environ["CHEAPERINFERENCE_API_KEY"] = "ci-dummy"
        os.environ["AI_GATEWAY_API_KEY"] = "vercel-dummy"
        os.environ["OPENROUTER_API_KEY"] = "or-dummy"
        os.environ["MODAL_ENDPOINT_URL"] = "https://modal.dummy"
        os.environ["RUNPOD_API_KEY"] = "rp-dummy"
        os.environ["RUNPOD_ENDPOINT_ID"] = "pod-dummy"

        providers = get_frontier_providers()
        names = [p["name"] for p in providers]
        assert names == [
            "Hugging Face Inference",
            "CheaperInference",
            "Vercel AI Gateway",
            "OpenRouter",
            "Modal",
            "RunPod",
        ], f"Provider priority mismatch: {names}"
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("provider priority verification : exit 0.")


def verify_alice_core(repo_dir: str) -> None:
    sys.path.insert(0, repo_dir)
    from hydra_cli.alice_runner import evaluate_with_alice, find_alice_core, find_node_binary

    assert find_node_binary() is not None, "Node.js binary not discoverable"
    assert find_alice_core() is not None, "alice_core.js not discoverable"

    # 1. Deterministic evaluation (arithmetic)
    eval_math = evaluate_with_alice("2 + 2")
    assert eval_math is not None, "Alice math evaluation failed"
    assert eval_math.get("route") == "DETERMINISTIC_EVAL", f"Expected DETERMINISTIC_EVAL, got {eval_math.get('route')}"
    assert "4" in str(eval_math.get("answer")), f"Expected 4 in answer, got {eval_math.get('answer')}"

    # 2. Philosophical canon inquiry
    eval_canon = evaluate_with_alice("navigate uncertainty")
    assert eval_canon is not None, "Alice canon evaluation failed"
    assert eval_canon.get("route") == "PHILOSOPHICAL_CANON", f"Expected PHILOSOPHICAL_CANON, got {eval_canon.get('route')}"
    assert "Socrates" in str(eval_canon.get("answer")), f"Expected Socrates in canon advice, got {eval_canon.get('answer')}"

    # 3. Epistemic gap inquiry with citation
    eval_gap = evaluate_with_alice("hello alice. how are you doing today?")
    assert eval_gap is not None, "Alice epistemic gap evaluation failed"
    assert eval_gap.get("route") == "EPISTEMIC_GAP", f"Expected EPISTEMIC_GAP, got {eval_gap.get('route')}"
    assert len(eval_gap.get("candidates", [])) > 0, "Expected candidate citations in epistemic gap"

    # 4. Progen P018 invariant check (zero copula across stream)
    progen = eval_gap.get("progenStream", "")
    assert progen, "Expected non-empty progenStream"
    for line in progen.splitlines():
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("["):
            continue
        if ":" in line_clean:
            _, comment = line_clean.split(":", 1)
            words = comment.strip().split()
            if words:
                assert words[0].lower() not in ("is", "are", "was", "were"), f"P018 copula violation: {line_clean}"

    print("alice core verification : exit 0.")


def verify_alice_standalone_gate(repo_dir: str) -> None:
    sys.path.insert(0, repo_dir)
    from hydra_cli.alice_gate import consult

    for query in ["hello alice. how are you doing today?", "2 + 2", "refactor the parser module"]:
        dec = consult(query, summon_alias="none")
        assert dec.action in ("local", "ask"), f"Query '{query}' produced action '{dec.action}', expected local or ask"
        assert dec.action != "summon", f"Query '{query}' must not summon in standalone mode"
        assert dec.text, f"Query '{query}' produced empty text"

    print("alice standalone gate verification : exit 0.")


def main() -> int:
    t0 = time.perf_counter()
    _banner()

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    verify_manifests(repo_dir)
    verify_cli_subprocesses(repo_dir)
    verify_agent_repl(repo_dir)
    verify_tui_invariants(repo_dir)
    verify_provider_priority(repo_dir)
    verify_alice_core(repo_dir)
    verify_alice_standalone_gate(repo_dir)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"\nhydra gate status : pass.")
    print(f"execution duration : {elapsed_ms:.1f}ms.")
    print(f"truth invariant : exit 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
