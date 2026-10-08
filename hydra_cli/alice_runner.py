"""
Alice cognitive engine native agent runner for Hydra CLI.
Integrates Alice (snowgate-alice) as native orchestrator and baseline worker.
Evaluates prompts through Alice's cognitive grammar and deterministic tool pipeline
before falling back to model generation.
"""

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple


def find_node_binary() -> Optional[str]:
    """Locate Node.js executable in environment, PATH, or standard installation paths."""
    env_bin = os.environ.get("NODE_BIN", "").strip()
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    which_bin = shutil.which("node")
    if which_bin:
        return which_bin

    candidates = [
        "C:\\Program Files\\nodejs\\node.exe",
        "C:\\Program Files (x86)\\nodejs\\node.exe",
        os.path.expanduser("~\\AppData\\Roaming\\nvm\\node.exe"),
        os.path.expanduser("~\\AppData\\Local\\Programs\\node\\node.exe"),
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def find_alice_core() -> Optional[str]:
    """Locate alice_core.js in environment or known workspace locations."""
    env_path = os.environ.get("ALICE_CORE_JS", "").strip()
    if env_path and os.path.isfile(env_path):
        return os.path.abspath(env_path)

    env_home = os.environ.get("ALICE_HOME", "").strip()
    if env_home:
        candidate = os.path.join(env_home, "alice_core.js")
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    candidates = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "snowgate-alice", "alice_core.js")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "snowgate-alice", "alice_core.js")),
        "C:\\Users\\jpm05\\Documents\\snowgate-alice\\alice_core.js",
        os.path.expanduser("~/Documents/snowgate-alice/alice_core.js"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None


def alice_available() -> bool:
    """Return True if Node.js and alice_core.js are discoverable and available for invocation."""
    return find_node_binary() is not None and find_alice_core() is not None


def evaluate_with_alice(prompt: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """
    Execute proposition query directly through Alice Core engine via Node.js.
    Returns parsed dictionary payload from Alice, or None on failure.
    """
    node_bin = find_node_binary()
    core_path = find_alice_core()
    if not node_bin or not core_path:
        return None

    norm_path = core_path.replace("\\", "/")
    js_code = (
        f"const {{ AliceCore }} = require('{norm_path}');\n"
        "const core = new AliceCore();\n"
        "const res = core.process(process.argv[1]);\n"
        "console.log(JSON.stringify(res));\n"
    )

    cmd = [node_bin, "-e", js_code, prompt]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
    except Exception:
        return None
    return None


def run_alice(
    prompt: str,
    model: Optional[str] = None,
    timeout: int = 60,
    fallback_model: Optional[str] = "glm 5.3 flash",
    env: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Run prompt through Alice cognitive engine.
    Evaluates prompts through Alice's cognitive grammar and deterministic tool pipeline
    before falling back to model generation.
    Returns agent string output or None if Alice is unavailable.
    """
    from hydra_cli.alice_retrieve import answer as retrieve, is_inquiry

    alice_eval = evaluate_with_alice(prompt, timeout=timeout) if alice_available() else None
    strict = {"DETERMINISTIC_EVAL", "DETERMINISTIC_LOGIC", "SYLLOGISTIC_DEDUCTION", "SYSTEM_COMMAND", "VERBATIM_RECALL"}
    if (not alice_eval or alice_eval.get("route") not in strict) and is_inquiry(prompt):
        found = retrieve(prompt, session="runner")
        if found.action in ("answer", "ask"):
            return found.text
    if not alice_eval:
        return None

    route = alice_eval.get("route", "")
    epistemic_modality = alice_eval.get("epistemicModality", "")
    progen_stream = alice_eval.get("progenStream", "")
    answer = alice_eval.get("answer", "")

    # Routes where Alice provides verified deterministic evaluation or exact recall
    deterministic_routes = {
        "DETERMINISTIC_EVAL",
        "DETERMINISTIC_LOGIC",
        "ASSOCIATIVE_LOOKUP",
        "VERBATIM_RECALL",
        "PARADOX_NAVIGATED",
        "PHILOSOPHICAL_CANON",
        "INVENTION_LOOP",
        "SILENCE_BOUNDARY",
        "SYSTEM_COMMAND",
        "VAGUENESS_DETECTED",
    }

    if route in deterministic_routes:
        output_parts = [f"{epistemic_modality} {answer}"]
        if progen_stream:
            output_parts.append("\n" + progen_stream)
        return "\n".join(output_parts).strip()

    # Route requires synthetic generation or epistemic gap research
    target_fallback = model or fallback_model
    if target_fallback:
        try:
            from hydra_cli.providers import complete
            kantian = alice_eval.get("kantian", {})
            tractatus = alice_eval.get("tractatus", {})
            system_prompt = (
                "You are Alice, sovereign cognitive reasoning agent.\n"
                f"Tractatus Picture: {tractatus.get('picture', 'atomic fact')}\n"
                f"Kantian Judgment: {kantian.get('judgmentType', 'Synthetic a posteriori')}\n"
                "Invariants: Enforce Progen syntax (topic : comment). Zero copula (no leading is/are)."
            )
            response = complete(
                prompt=prompt,
                model=target_fallback,
                system_prompt=system_prompt,
                max_tokens=1024
            )
            if response:
                return f"[SYNTHETIC_INFERENCE] {response}\n\n{progen_stream}".strip()
        except Exception:
            pass

    # If fallback not invoked or failed, return Alice's epistemic gap answer
    return f"{epistemic_modality} {answer}\n\n{progen_stream}".strip()


class AliceOrchestrator:
    """
    Alice as Native Orchestrator.
    Decomposes task into Tractatus atomic facts, classifies Kantian categories,
    queries philosophical canon, and emits a structured Progen execution packet.
    """

    def __init__(self):
        self.node_bin = find_node_binary()
        self.core_path = find_alice_core()

    def is_ready(self) -> bool:
        return self.node_bin is not None and self.core_path is not None

    def plan_task(self, task: str) -> Dict[str, Any]:
        """Decompose incoming prompt and formulate canonical state vectors."""
        eval_result = evaluate_with_alice(task) or {}
        route = eval_result.get("route", "SYNTHETIC_ROUTING")
        kantian = eval_result.get("kantian", {})
        tractatus = eval_result.get("tractatus", {})

        plan = {
            "task": task,
            "route": route,
            "epistemic_modality": eval_result.get("epistemicModality", "[UNKNOWN]"),
            "tractatus_picture": tractatus.get("picture", f"[{task}]"),
            "judgment_type": kantian.get("judgmentType", "Synthetic a posteriori"),
            "categories": kantian.get("categories", {}),
            "progen_plan": (
                f"intention : execute task through sovereign cognitive pipeline.\n\n"
                f"requirement : input decomposed into atomic tractatus propositions.\n\n"
                f"course of action : route via {route}; verify deterministic predicates.\n\n"
                f"end result : verified state with exit code zero.\n\n"
                f"reason : grounded in wittgenstein logical atomism and kantian critique."
            )
        }
        return plan


class AliceWorker:
    """
    Alice as Native Baseline Worker.
    Executes deterministic steps, validates Progen topic : comment invariants,
    and enforces P018 zero copula constraints.
    """

    def execute_and_verify(self, task: str) -> Dict[str, Any]:
        """Execute task through Alice and verify Progen invariants."""
        res_text = run_alice(task) or ""
        valid_progen = True
        violations = []

        for line in res_text.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("["):
                continue
            if ":" in line_str:
                parts = line_str.split(":", 1)
                comment = parts[1].strip()
                words = comment.split()
                if words and words[0].lower() in ("is", "are", "was", "were"):
                    valid_progen = False
                    violations.append(f"P018 leading copula: {line_str}")

        return {
            "output": res_text,
            "valid_progen": valid_progen,
            "violations": violations,
            "verified": len(violations) == 0 and bool(res_text),
            "exit_code": 0 if (len(violations) == 0 and bool(res_text)) else 1
        }
