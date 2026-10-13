"""Actions for the native desk panels that the command palette table does not cover.

Each handler takes a params dict and returns a JSON-safe dict with isError. Nothing here starts a server.
The stdio adapter tries these names first, then falls back to desktop_core.dispatch_command_palette_action.
"""

from __future__ import annotations

from typing import Any, Callable, Dict


def _terminal_run(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.sandbox import SandboxRunner

    command = str(p.get("command") or "").strip()
    if not command:
        return {"isError": True, "error": "command is required"}
    res = SandboxRunner().run_command(command, cwd=p.get("cwd") or None)
    out = res.to_dict()
    return {"isError": False, "result": out}


def _swarm_run(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.swarm import execute_swarm

    task = str(p.get("task") or "").strip()
    if not task:
        return {"isError": True, "error": "task is required"}
    model = str(p.get("model") or "")
    tier = "local" if model == "local" else "free" if model == "free" or ":free" in model else None
    results = execute_swarm(task, custom_model=None if tier else (model or None), stream_output=False, tier=tier)
    rows = [r.to_dict() for r in results]
    synth = next((r for r in rows if r["role"] == "synthesizer"), None)
    heads = [r for r in rows if r["role"] != "synthesizer"]
    return {"isError": False, "heads": heads, "synthesis": (synth or {}).get("content", ""),
            "synthesis_error": (synth or {}).get("error")}


def _engine():
    from hydra_cli.desktop_core import get_computer_use_engine

    return get_computer_use_engine()


def _screen(p: Dict[str, Any]) -> Dict[str, Any]:
    cap = _engine().screen.capture(as_base64=True)
    if isinstance(cap, dict):
        b64 = cap.get("base64") or cap.get("image_base64") or cap.get("data") or ""
        return {"isError": bool(cap.get("isError")), "png_base64": b64, "width": cap.get("width"), "height": cap.get("height"),
                "simulated": bool(cap.get("simulated"))}
    return {"isError": False, "png_base64": str(cap)}


def _click(p: Dict[str, Any]) -> Dict[str, Any]:
    return _engine().dispatch("mouse_click", x=int(p.get("x", 0)), y=int(p.get("y", 0)))


def _type(p: Dict[str, Any]) -> Dict[str, Any]:
    return _engine().dispatch("type_text", text=str(p.get("text", "")))


def _window(p: Dict[str, Any]) -> Dict[str, Any]:
    return _engine().dispatch("window_action", sub_action="active")


def _agent_run(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.agent_runner import get_agent_runner

    runner = get_agent_runner()
    cwd = p.get("cwd")
    if cwd and getattr(runner, "tools", None) is not None:
        runner.tools.cwd = str(cwd)
    prof = p.get("profile")
    if prof:
        runner.set_tool_profile(str(prof))
    task = str(p.get("task") or "").strip()
    if not task:
        return {"isError": True, "error": "task is required"}
    runner.run_task_async(task, max_steps=int(p.get("max_steps") or 30))
    return {"isError": False, "started": True}


def _agent_status(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.agent_runner import get_agent_runner

    return {"isError": False, "status": get_agent_runner().get_status()}


def _job_create(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.agent_runner import get_job_scheduler

    interval = p.get("interval_sec", p.get("interval_seconds"))
    job = get_job_scheduler().schedule_job(
        name=str(p.get("name") or "job"), task=p.get("task") or p.get("task_description"),
        interval_sec=float(interval) if interval else None, recurring=bool(interval),
        tool_profile=str(p.get("profile") or "full_automation"))
    return {"isError": False, "job": job.to_dict()}


def _job_run(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.agent_runner import get_job_scheduler

    return get_job_scheduler().run_job_now(str(p.get("job_id") or ""))


def _job_cancel(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.agent_runner import get_job_scheduler

    return {"isError": False, "cancelled": bool(get_job_scheduler().cancel_job(str(p.get("job_id") or "")))}


def _models(p: Dict[str, Any]) -> Dict[str, Any]:
    from hydra_cli.config import MODEL_MAP

    return {"isError": False, "models": sorted(MODEL_MAP)}


DESK_ACTIONS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "terminal:run": _terminal_run, "swarm:run": _swarm_run, "computer:screen": _screen, "computer:click": _click,
    "computer:type": _type, "computer:window": _window, "agent:run": _agent_run, "agent:status": _agent_status,
    "jobs:create": _job_create, "jobs:run": _job_run, "jobs:cancel": _job_cancel, "models:list": _models,
}


def run(name: str, params: Dict[str, Any]) -> Any:
    """Run a desk action, or return None when the name is not one of ours."""
    handler = DESK_ACTIONS.get(name)
    if handler is None:
        return None
    return handler(params)
