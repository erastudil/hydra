"""
Multi-Agent Swarm Orchestrator for Hydra CLI.
Fans out parallel task heads to decompose and analyze tasks concurrently.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from hydra_cli.config import SWARM_HEADS, resolve_model
from hydra_cli.providers import (
    ProviderError,
    fetch_chat_completion,
    get_frontier_providers,
)

HEAD_ICONS = {
    "architect": "🏛️ ",
    "coder": "⚡",
    "auditor": "🛡️ ",
    "synthesizer": "🔮",
}


class SwarmResult:
    def __init__(self, role: str, title: str, model: str, content: str, duration_sec: float, error: Optional[str] = None):
        self.role = role
        self.title = title
        self.model = model
        self.content = content
        self.duration_sec = duration_sec
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "title": self.title,
            "model": self.model,
            "duration_sec": round(self.duration_sec, 2),
            "content": self.content,
            "error": self.error,
        }


def run_single_head(
    role: str,
    head_config: Dict[str, str],
    task: str,
    provider: Dict[str, Any],
    temperature: float = 0.7,
    timeout: int = 180,
) -> SwarmResult:
    """Execute a single agent head against the provider."""
    start_time = time.time()
    title = head_config.get("title", role.capitalize())
    model = resolve_model(head_config.get("model", "anthropic/claude-3.7-sonnet"))
    system_prompt = head_config.get("system", "You are an autonomous engineering agent.")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Task: {task}\n\nExecute your specialized mandate with rigorous, production-grade output."},
    ]

    try:
        content = fetch_chat_completion(
            url=provider["url"],
            headers=provider["headers"],
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
        )
        duration = time.time() - start_time
        return SwarmResult(role=role, title=title, model=model, content=content, duration_sec=duration)
    except Exception as e:
        duration = time.time() - start_time
        return SwarmResult(role=role, title=title, model=model, content="", duration_sec=duration, error=str(e))


def execute_swarm(
    task: str,
    heads: Optional[List[str]] = None,
    custom_model: Optional[str] = None,
    temperature: float = 0.7,
    json_output: bool = False,
    stream_output: bool = True,
) -> List[SwarmResult]:
    """
    Launch the Hydra Multi-Agent Swarm.
    Fans out parallel task heads and synthesizes results.
    """
    providers = get_frontier_providers()
    if not providers:
        raise ProviderError(
            "Multi-agent swarm requires frontier credentials.\n"
            "Set OPENROUTER_API_KEY or AI_GATEWAY_API_KEY in your environment."
        )
    primary_provider = providers[0]

    selected_roles = heads or ["architect", "coder", "auditor"]
    active_configs: Dict[str, Dict[str, str]] = {}

    for role in selected_roles:
        cfg = SWARM_HEADS.get(role, {
            "title": role.capitalize(),
            "model": "anthropic/claude-3.7-sonnet",
            "system": f"You are a specialized agent for {role}. Address the task with high technical precision.",
        }).copy()

        if custom_model:
            cfg["model"] = custom_model
        active_configs[role] = cfg

    if not json_output:
        sys.stderr.write(f"\n[HYDRA SWARM] Fanning out {len(active_configs)} autonomous heads in parallel...\n")
        sys.stderr.flush()

    results: List[SwarmResult] = []

    with ThreadPoolExecutor(max_workers=len(active_configs)) as executor:
        future_to_role = {
            executor.submit(
                run_single_head,
                role,
                cfg,
                task,
                primary_provider,
                temperature,
            ): role
            for role, cfg in active_configs.items()
        }

        for future in as_completed(future_to_role):
            res = future.result()
            results.append(res)
            if not json_output and stream_output:
                icon = HEAD_ICONS.get(res.role, "🐉")
                border = "=" * 64
                sys.stdout.write(f"\n{border}\n")
                sys.stdout.write(f"{icon} [HEAD: {res.title.upper()}] · {res.model} ({res.duration_sec:.1f}s)\n")
                sys.stdout.write(f"{border}\n\n")
                if res.error:
                    sys.stdout.write(f"[ERROR]: {res.error}\n")
                else:
                    sys.stdout.write(f"{res.content}\n")
                sys.stdout.flush()

    # Optional synthesis if 3 or more heads completed successfully
    if "synthesizer" not in selected_roles and not json_output and len([r for r in results if not r.error]) >= 2:
        sys.stderr.write("\n[HYDRA SWARM] Dispatching Synthesizer head to unify conclusions...\n")
        sys.stderr.flush()

        synthesis_prompt = (
            f"Original Task: {task}\n\n"
            "Below are the findings from the autonomous heads:\n\n"
        )
        for r in results:
            if not r.error:
                synthesis_prompt += f"--- {r.title} ({r.model}) ---\n{r.content}\n\n"
        synthesis_prompt += (
            "Consolidate these findings into a unified, decisive action roadmap. "
            "Resolve any contradictions and provide the final engineering consensus."
        )

        synth_cfg = SWARM_HEADS.get("synthesizer", {
            "title": "Synthesizer",
            "model": "google/gemini-2.5-pro",
            "system": "You are the Swarm Lead Synthesizer. Review all perspectives and emit the final prioritized execution roadmap.",
        }).copy()
        if custom_model:
            synth_cfg["model"] = custom_model

        synth_res = run_single_head("synthesizer", synth_cfg, synthesis_prompt, primary_provider, temperature)
        results.append(synth_res)

        if not json_output:
            border = "=" * 64
            sys.stdout.write(f"\n{border}\n")
            sys.stdout.write(f"🔮 [HEAD: FINAL SYNTHESIS] · {synth_res.model} ({synth_res.duration_sec:.1f}s)\n")
            sys.stdout.write(f"{border}\n\n")
            if synth_res.error:
                sys.stdout.write(f"[ERROR]: {synth_res.error}\n")
            else:
                sys.stdout.write(f"{synth_res.content}\n")
            sys.stdout.flush()

    if json_output:
        output_payload = [r.to_dict() for r in results]
        sys.stdout.write(json.dumps(output_payload, indent=2))
        sys.stdout.write("\n")
        sys.stdout.flush()

    return results
