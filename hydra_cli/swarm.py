"""
Multi-agent swarm for Hydra CLI.
Specialists run together. The synthesizer runs once, after their results exist.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
import time
from typing import Any, Dict, List, Optional

from hydra_cli.config import SWARM_HEADS, resolve_model
from hydra_cli.providers import (
    ProviderError,
    UsageError,
    fetch_chat_completion,
    get_frontier_providers,
    reasoning_fields,
)

HEAD_ICONS = {
    "architect": "[ARCH]",
    "coder": "[CODE]",
    "auditor": "[AUDIT]",
    "synthesizer": "[SYNTH]",
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
            "status": "failed" if self.error else "ok",
        }


def _provider_list(provider: Any) -> List[Dict[str, Any]]:
    if isinstance(provider, list):
        return provider
    return [provider]


def run_single_head(
    role: str,
    head_config: Dict[str, str],
    task: str,
    provider: Any,
    temperature: Optional[float] = None,
    timeout: int = 180,
    wrap_task: bool = True,
) -> SwarmResult:
    """Run one head, trying each provider until one returns text."""
    start_time = time.time()
    title = head_config.get("title", role.capitalize())
    model = resolve_model(head_config.get("model", "anthropic/claude-sonnet-5.5"))
    system_prompt = head_config.get("system", "You are an autonomous engineering agent.")
    user_content = task
    if wrap_task:
        user_content = (
            f"Task: {task}\n\n"
            "Execute your specialized mandate with rigorous, production-grade output."
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    reasoning = reasoning_fields(head_config.get("effort"), head_config.get("reasoning_mode"))
    last_error: Optional[Exception] = None
    for candidate in _provider_list(provider):
        try:
            content = fetch_chat_completion(
                url=candidate["url"],
                headers=candidate["headers"],
                model=model,
                messages=messages,
                temperature=temperature,
                timeout=timeout,
                reasoning=reasoning,
            )
            duration = time.time() - start_time
            return SwarmResult(role=role, title=title, model=model, content=content, duration_sec=duration)
        except UsageError:
            raise
        except Exception as exc:
            last_error = exc
    duration = time.time() - start_time
    return SwarmResult(
        role=role,
        title=title,
        model=model,
        content="",
        duration_sec=duration,
        error=str(last_error) if last_error else "No provider available",
    )


def _print_head(result: SwarmResult) -> None:
    icon = HEAD_ICONS.get(result.role, "[HEAD]")
    border = "=" * 64
    sys.stdout.write(f"\n{border}\n")
    sys.stdout.write(f"{icon} [HEAD: {result.title.upper()}] · {result.model} ({result.duration_sec:.1f}s)\n")
    sys.stdout.write(f"{border}\n\n")
    if result.error:
        sys.stdout.write(f"[ERROR]: {result.error}\n")
    else:
        sys.stdout.write(f"{result.content}\n")
    sys.stdout.flush()


def _head_config(role: str, custom_model: Optional[str]) -> Dict[str, str]:
    cfg = SWARM_HEADS.get(role, {
        "title": role.capitalize(),
        "model": "anthropic/claude-sonnet-5.5",
        "system": f"You are a specialized agent for {role}. Address the task with high technical precision.",
    }).copy()
    if custom_model:
        cfg["model"] = custom_model
        cfg.pop("effort", None)
        cfg.pop("reasoning_mode", None)
    return cfg


def execute_swarm(
    task: str,
    heads: Optional[List[str]] = None,
    custom_model: Optional[str] = None,
    temperature: Optional[float] = None,
    json_output: bool = False,
    stream_output: bool = True,
) -> List[SwarmResult]:
    """
    Run specialist heads in parallel, then one synthesizer over their finished text.
    stream_output is accepted for CLI compatibility. Head text is always kept.
    """
    del stream_output
    providers = get_frontier_providers()
    if not providers:
        raise ProviderError(
            "Multi-agent swarm requires frontier credentials.\n"
            "Set OPENROUTER_API_KEY or AI_GATEWAY_API_KEY in your environment."
        )

    requested = heads or ["architect", "coder", "auditor"]
    explicit_synth = "synthesizer" in requested
    worker_roles = [role for role in requested if role != "synthesizer"]
    if not worker_roles:
        worker_roles = ["architect", "coder", "auditor"]

    if not json_output:
        sys.stderr.write(f"\n[HYDRA SWARM] Fanning out {len(worker_roles)} specialist heads in parallel...\n")
        sys.stderr.flush()

    results_by_role: Dict[str, SwarmResult] = {}
    with ThreadPoolExecutor(max_workers=len(worker_roles)) as executor:
        future_to_role = {
            executor.submit(
                run_single_head,
                role,
                _head_config(role, custom_model),
                task,
                providers,
                temperature,
            ): role
            for role in worker_roles
        }
        for future in as_completed(future_to_role):
            result = future.result()
            results_by_role[result.role] = result
            if not json_output:
                _print_head(result)

    results = [results_by_role[role] for role in worker_roles]
    successes = [result for result in results if not result.error]
    if successes and (explicit_synth or len(successes) >= 2):
        if not json_output:
            sys.stderr.write("\n[HYDRA SWARM] Dispatching Synthesizer head to unify conclusions...\n")
            sys.stderr.flush()
        synthesis_prompt = f"Original Task: {task}\n\nBelow are the findings from the autonomous heads:\n\n"
        for result in successes:
            synthesis_prompt += f"--- {result.title} ({result.model}) ---\n{result.content}\n\n"
        synthesis_prompt += (
            "Consolidate these findings into a unified, decisive action roadmap. "
            "Resolve any contradictions and provide the final engineering consensus."
        )
        synth_res = run_single_head(
            "synthesizer",
            _head_config("synthesizer", custom_model),
            synthesis_prompt,
            providers,
            temperature,
            wrap_task=False,
        )
        results.append(synth_res)
        if not json_output:
            _print_head(synth_res)

    if json_output:
        sys.stdout.write(json.dumps([result.to_dict() for result in results], indent=2))
        sys.stdout.write("\n")
        sys.stdout.flush()
    return results
