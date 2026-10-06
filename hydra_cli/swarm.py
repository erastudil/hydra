"""
Multi-agent swarm for Hydra CLI.
Specialists run together. Review heads (the auditor) run next, over the specialists' output.
The synthesizer runs once, after every other result exists.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
import time
from typing import Any, Dict, List, Optional

from hydra_cli.config import SWARM_HEADS, resolve_model
from hydra_cli.providers import (
    CredentialsMissingError,
    ProviderError,
    UsageError,
    completion_timeout,
    fetch_chat_completion,
    get_frontier_providers,
    providers_for_model,
    reasoning_fields,
    redact,
)

HEAD_ICONS = {
    "architect": "[ARCH]",
    "coder": "[CODE]",
    "auditor": "[AUDIT]",
    "synthesizer": "[SYNTH]",
}

# Heads that review the other heads' work, so they run after them.
REVIEW_ROLES = ("auditor",)


class SwarmResult:
    def __init__(
        self,
        role: str,
        title: str,
        model: str,
        content: str,
        duration_sec: float,
        error: Optional[str] = None,
        provider: Optional[str] = None,
        served_model: Optional[str] = None,
        cost: Optional[float] = None,
        fallbacks: Optional[List[str]] = None,
    ):
        self.role = role
        self.title = title
        self.model = model
        self.content = content
        self.duration_sec = duration_sec
        self.error = error
        self.provider = provider
        self.served_model = served_model
        self.cost = cost
        self.fallbacks = list(fallbacks or [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "title": self.title,
            "model": self.model,
            "served_model": self.served_model,
            "provider": self.provider,
            "cost_usd": self.cost,
            "fallbacks": self.fallbacks,
            "duration_sec": round(self.duration_sec, 2),
            "content": self.content,
            "error": self.error,
            "status": "failed" if self.error else "ok",
        }


def _provider_list(provider: Any) -> List[Dict[str, Any]]:
    if isinstance(provider, list):
        return provider
    return [provider]


def _base_role(role: str) -> str:
    return role.split(":", 1)[0].strip().lower()


def _short_error(exc: Any, limit: int = 160) -> str:
    text = " ".join(redact(exc).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def run_single_head(
    role: str,
    head_config: Dict[str, str],
    task: str,
    provider: Any,
    temperature: Optional[float] = None,
    timeout: Optional[int] = None,
    wrap_task: bool = True,
    max_tokens: Optional[int] = None,
    context: Optional[str] = None,
) -> SwarmResult:
    """Run one head, trying external agent runners first if configured, then each provider.

    timeout=None picks the idle wait from the head's reasoning settings: 600s for pro or
    high effort, 180s otherwise. context is other heads' output for a review head.
    """
    start_time = time.time()
    title = head_config.get("title", role.capitalize())
    model = resolve_model(head_config.get("model", "anthropic/claude-sonnet-5.5"))
    system_prompt = head_config.get("system", "You are an autonomous engineering agent.")
    reasoning = reasoning_fields(head_config.get("effort"), head_config.get("reasoning_mode"))
    wait = completion_timeout(reasoning, timeout)

    runner = head_config.get("runner")
    if runner == "hermes":
        try:
            from hydra_cli.agent_runners import run_hermes
            content = run_hermes(task, timeout=wait)
            if content:
                duration = time.time() - start_time
                return SwarmResult(role=role, title=title, model="hermes-agent", content=content,
                                   duration_sec=duration, provider="hermes")
        except Exception:
            pass
    elif runner == "pi":
        try:
            from hydra_cli.agent_runners import run_pi
            content = run_pi(task, timeout=wait)
            if content:
                duration = time.time() - start_time
                return SwarmResult(role=role, title=title, model="pi-coder", content=content,
                                   duration_sec=duration, provider="pi")
        except Exception:
            pass

    user_content = task
    if wrap_task:
        user_content = (
            f"Task: {task}\n\n"
            "Execute your specialized mandate with rigorous, production-grade output."
        )
    if context:
        user_content += (
            "\n\nThe other heads produced the work below. Review it directly: name concrete defects "
            "in their design and code, and say what must change.\n\n" + context
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        candidates = providers_for_model(model, _provider_list(provider))
    except CredentialsMissingError as exc:
        return SwarmResult(role=role, title=title, model=model, content="",
                           duration_sec=time.time() - start_time, error=str(exc))

    last_error: Optional[Exception] = None
    fallbacks: List[str] = []
    for candidate in candidates:
        name = candidate.get("name", "provider")
        try:
            res = fetch_chat_completion(
                url=candidate["url"],
                headers=candidate["headers"],
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=wait,
                reasoning=reasoning,
                return_meta=True,
            )
            if isinstance(res, dict):
                content = res.get("content", "")
                served = res.get("model") or model
                usage = res.get("usage") or {}
                cost = usage.get("cost") if isinstance(usage.get("cost"), (int, float)) else None
                upstream = res.get("upstream")
            else:
                content, served, cost, upstream = res, model, None, None
            label = f"{name} ({upstream})" if upstream else name
            duration = time.time() - start_time
            return SwarmResult(role=role, title=title, model=model, content=content, duration_sec=duration,
                               provider=label, served_model=served, cost=cost, fallbacks=fallbacks)
        except UsageError:
            raise
        except Exception as exc:
            last_error = exc
            fallbacks.append(f"{name}: {_short_error(exc)}")
    duration = time.time() - start_time
    return SwarmResult(
        role=role,
        title=title,
        model=model,
        content="",
        duration_sec=duration,
        error=" | ".join(fallbacks) if fallbacks else (redact(last_error) if last_error else "No provider available"),
        fallbacks=fallbacks,
    )


def _print_head(result: SwarmResult) -> None:
    icon = HEAD_ICONS.get(_base_role(result.role), "[HEAD]")
    border = "=" * 64
    route = result.model
    if result.served_model and result.served_model != result.model:
        route = f"{result.model} -> {result.served_model}"
    if result.provider:
        route = f"{route} via {result.provider}"
    cost = f" · ${result.cost:.4f}" if result.cost is not None else ""
    sys.stdout.write(f"\n{border}\n")
    sys.stdout.write(f"{icon} [HEAD: {result.title.upper()}] · {route} ({result.duration_sec:.1f}s){cost}\n")
    if result.fallbacks and not result.error:
        sys.stdout.write(f"    after failures: {'; '.join(result.fallbacks)}\n")
    sys.stdout.write(f"{border}\n\n")
    if result.error:
        sys.stdout.write(f"[ERROR]: {result.error}\n")
    else:
        sys.stdout.write(f"{result.content}\n")
    sys.stdout.flush()


def _head_config(role: str, custom_model: Optional[str]) -> Dict[str, str]:
    runner = None
    clean_role = role
    if ":" in role:
        base_role, runner = role.split(":", 1)
        clean_role = base_role.strip().lower()
        runner = runner.strip().lower()
    elif role.lower() in ("hermes", "pi"):
        runner = role.lower()
        clean_role = role.lower()

    cfg = SWARM_HEADS.get(clean_role, {
        "title": role.capitalize(),
        "model": "anthropic/claude-sonnet-5.5",
        "system": f"You are a specialized agent for {clean_role}. Address the task with high technical precision.",
    }).copy()
    if runner:
        cfg["runner"] = runner
        cfg["title"] = f"{cfg.get('title', clean_role.capitalize())} ({runner})"
    if custom_model:
        cfg["model"] = custom_model
        cfg.pop("effort", None)
        cfg.pop("reasoning_mode", None)
    return cfg


def _run_stage(
    roles: List[str],
    task: str,
    providers: List[Dict[str, Any]],
    custom_model: Optional[str],
    temperature: Optional[float],
    max_tokens: Optional[int],
    json_output: bool,
    context: Optional[str] = None,
) -> Dict[str, SwarmResult]:
    results: Dict[str, SwarmResult] = {}
    if not roles:
        return results
    with ThreadPoolExecutor(max_workers=len(roles)) as executor:
        future_to_role = {
            executor.submit(
                run_single_head,
                role,
                _head_config(role, custom_model),
                task,
                providers,
                temperature,
                None,
                True,
                max_tokens,
                context,
            ): role
            for role in roles
        }
        for future in as_completed(future_to_role):
            result = future.result()
            results[result.role] = result
            if not json_output:
                _print_head(result)
    return results


def _findings_text(results: List[SwarmResult]) -> str:
    return "".join(f"--- {r.title} ({r.served_model or r.model}) ---\n{r.content}\n\n" for r in results)


def execute_swarm(
    task: str,
    heads: Optional[List[str]] = None,
    custom_model: Optional[str] = None,
    temperature: Optional[float] = None,
    json_output: bool = False,
    stream_output: bool = True,
    max_tokens: Optional[int] = None,
) -> List[SwarmResult]:
    """
    Run specialist heads in parallel, then review heads over their output, then one
    synthesizer over everything. stream_output is accepted for CLI compatibility.
    max_tokens caps every head, the synthesizer included.
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

    review_roles = [role for role in worker_roles if _base_role(role) in REVIEW_ROLES]
    first_roles = [role for role in worker_roles if role not in review_roles]
    if not first_roles:
        first_roles, review_roles = review_roles, []

    if not json_output:
        sys.stderr.write(f"\n[HYDRA SWARM] Fanning out {len(first_roles)} specialist heads in parallel...\n")
        sys.stderr.flush()

    results_by_role = _run_stage(first_roles, task, providers, custom_model, temperature, max_tokens, json_output)

    if review_roles:
        reviewed = [results_by_role[r] for r in first_roles if not results_by_role[r].error]
        if not json_output:
            sys.stderr.write(
                f"\n[HYDRA SWARM] Dispatching review head(s) over {len(reviewed)} specialist result(s)...\n"
            )
            sys.stderr.flush()
        context = _findings_text(reviewed) if reviewed else None
        results_by_role.update(
            _run_stage(review_roles, task, providers, custom_model, temperature, max_tokens, json_output, context)
        )

    results = [results_by_role[role] for role in worker_roles]
    successes = [result for result in results if not result.error]
    if successes and (explicit_synth or len(successes) >= 2):
        if not json_output:
            sys.stderr.write("\n[HYDRA SWARM] Dispatching Synthesizer head to unify conclusions...\n")
            sys.stderr.flush()
        synthesis_prompt = f"Original Task: {task}\n\nBelow are the findings from the autonomous heads:\n\n"
        synthesis_prompt += _findings_text(successes)
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
            max_tokens=max_tokens,
        )
        results.append(synth_res)
        if not json_output:
            _print_head(synth_res)

    if not json_output:
        costs = [r.cost for r in results if r.cost is not None]
        if costs:
            sys.stderr.write(f"\n[HYDRA SWARM] Reported cost: ${sum(costs):.4f} across {len(costs)} head(s).\n")
            sys.stderr.flush()

    if json_output:
        sys.stdout.write(json.dumps([result.to_dict() for result in results], indent=2))
        sys.stdout.write("\n")
        sys.stdout.flush()
    return results
