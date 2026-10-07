"""
External agent runner integrations for Hydra CLI.
Summons autonomous external agents Hermes and Pi in sovereign execution loops.
"""

import os
import shutil
import subprocess
from hydra_cli.alice_runner import (
    AliceOrchestrator,
    AliceWorker,
    alice_available,
    evaluate_with_alice,
    find_alice_core,
    run_alice,
)
from typing import Any, Dict, List, Optional, Union


def find_hermes_binary() -> Optional[str]:
    """Locate hermes CLI in environment, PATH, or local installation directories."""
    env_bin = os.environ.get("HERMES_BIN", "").strip()
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    which_bin = shutil.which("hermes")
    if which_bin:
        return which_bin

    candidates = [
        os.path.expanduser("~/.local/bin/hermes"),
        os.path.expanduser("~/.cargo/bin/hermes"),
        os.path.expanduser("~/bin/hermes"),
        os.path.expanduser("~/AppData/Roaming/npm/hermes.cmd"),
        os.path.expanduser("~/AppData/Roaming/npm/hermes"),
        os.path.expanduser("~/.local/bin/hermes.exe"),
        os.path.expanduser("~/.cargo/bin/hermes.exe"),
        "C:\\Program Files\\hermes\\hermes.exe",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def find_pi_binary() -> Optional[str]:
    """Locate pi CLI in environment, PATH, or local installation directories."""
    env_bin = os.environ.get("PI_BIN", "").strip()
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    which_bin = shutil.which("pi")
    if which_bin:
        return which_bin

    candidates = [
        os.path.expanduser("~/.local/bin/pi"),
        os.path.expanduser("~/.cargo/bin/pi"),
        os.path.expanduser("~/bin/pi"),
        os.path.expanduser("~/AppData/Roaming/npm/pi.cmd"),
        os.path.expanduser("~/AppData/Roaming/npm/pi"),
        os.path.expanduser("~/.local/bin/pi.exe"),
        os.path.expanduser("~/.cargo/bin/pi.exe"),
        "C:\\Program Files\\pi\\pi.exe",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def hermes_available() -> bool:
    """Return True if hermes CLI is discoverable and available for invocation."""
    return find_hermes_binary() is not None


def pi_available() -> bool:
    """Return True if pi CLI is discoverable and available for invocation."""
    return find_pi_binary() is not None


def run_hermes(
    prompt: str,
    model: Optional[str] = None,
    toolsets: Optional[Union[str, List[str]]] = None,
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Invoke hermes in one-shot mode (-z "<prompt>").
    Returns agent stdout output, or None if hermes is not installed.
    """
    binary = find_hermes_binary()
    if not binary:
        return None

    cmd = [binary, "-z", prompt]
    if model:
        cmd.extend(["--model", model])
    if toolsets:
        if isinstance(toolsets, (list, tuple)):
            cmd.extend(["--toolsets", ",".join(toolsets)])
        else:
            cmd.extend(["--toolsets", str(toolsets)])

    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or f"hermes exited with code {proc.returncode}"
        raise RuntimeError(f"Hermes execution failed: {err}")

    return proc.stdout.strip()


def run_pi(
    prompt: str,
    model: Optional[str] = None,
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
    flag: Optional[str] = None,
) -> Optional[str]:
    """
    Invoke pi in one-shot mode (-z "<prompt>" or --oneshot "<prompt>").
    Returns agent stdout output, or None if pi is not installed.
    """
    binary = find_pi_binary()
    if not binary:
        return None

    run_flag = flag or "-z"
    cmd = [binary, run_flag, prompt]
    if model:
        cmd.extend(["--model", model])

    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
        check=False,
    )

    # Automatic fallback from -z to --oneshot if -z is an unrecognized option
    if proc.returncode != 0 and run_flag == "-z" and flag is None:
        stderr_lower = proc.stderr.lower()
        if any(term in stderr_lower for term in ("unrecognized", "invalid option", "unknown option", "error: no such option")):
            retry_cmd = [binary, "--oneshot", prompt]
            if model:
                retry_cmd.extend(["--model", model])
            proc = subprocess.run(
                retry_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                check=False,
            )

    if proc.returncode != 0:
        err = proc.stderr.strip() or f"pi exited with code {proc.returncode}"
        raise RuntimeError(f"Pi execution failed: {err}")

    return proc.stdout.strip()


def get_agent_status() -> Dict[str, Dict[str, Any]]:
    """Discover capability and status for Hermes, Pi, and Alice agent runners."""
    hermes_bin = find_hermes_binary()
    pi_bin = find_pi_binary()
    alice_core_path = find_alice_core()
    return {
        "hermes": {
            "available": hermes_bin is not None,
            "path": hermes_bin,
            "capabilities": ["one-shot", "toolsets", "reasoning", "frontier-loop"],
        },
        "pi": {
            "available": pi_bin is not None,
            "path": pi_bin,
            "capabilities": ["terminal-coding", "one-shot", "bash-execution", "qwen3-coder"],
        },
        "alice": {
            "available": alice_available(),
            "path": alice_core_path,
            "capabilities": [
                "cognitive-grammar",
                "tractatus-logic",
                "kantian-analytics",
                "philosophical-canon",
                "deterministic-eval",
                "memory-graph",
                "continuous-learning",
                "orchestrator",
                "baseline-worker",
            ],
        },
    }
