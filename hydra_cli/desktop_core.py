"""Desktop core services shared by the native app, command registry and agent runner.

Token accounting, coordinate scaling, command palette actions and provider fallback.
Moved out of the retired browser-based hydra_cli.desktop; no web server lives here.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from hydra_cli.computer_use import get_computer_use_engine
from hydra_cli.sandbox import SandboxRunner


MODEL_PRICING: Dict[str, Dict[str, Any]] = {
    # Frontier tier ($ / 1M tokens)
    "opus 5.5": {"tier": "frontier", "prompt_rate": 15.00, "completion_rate": 75.00},
    "claude-3-opus": {"tier": "frontier", "prompt_rate": 15.00, "completion_rate": 75.00},
    "sonnet 5.5": {"tier": "frontier", "prompt_rate": 3.00, "completion_rate": 15.00},
    "claude-3.5-sonnet": {"tier": "frontier", "prompt_rate": 3.00, "completion_rate": 15.00},
    "claude-3-7-sonnet": {"tier": "frontier", "prompt_rate": 3.00, "completion_rate": 15.00},
    "gpt-6.1": {"tier": "frontier", "prompt_rate": 2.50, "completion_rate": 10.00},
    "sol 6.1 pro": {"tier": "frontier", "prompt_rate": 2.50, "completion_rate": 10.00},
    "gpt-4o": {"tier": "frontier", "prompt_rate": 2.50, "completion_rate": 10.00},

    # Standard tier ($ / 1M tokens)
    "gemini 3.8": {"tier": "standard", "prompt_rate": 0.50, "completion_rate": 1.50},
    "gemini-2.5-flash": {"tier": "standard", "prompt_rate": 0.15, "completion_rate": 0.60},
    "glm 5.3 flash": {"tier": "standard", "prompt_rate": 0.10, "completion_rate": 0.10},
    "deepseek 4.1 flash": {"tier": "standard", "prompt_rate": 0.14, "completion_rate": 0.28},
    "deepseek-chat": {"tier": "standard", "prompt_rate": 0.14, "completion_rate": 0.28},

    # Free tier ($ / 1M tokens)
    "free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "local": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "qwen": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "qwen-3.8-27b:free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "deepseek-chat:free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},
    "llama-3.3-70b-instruct:free": {"tier": "free", "prompt_rate": 0.0, "completion_rate": 0.0},

    # Default rate
    "default": {"tier": "standard", "prompt_rate": 1.00, "completion_rate": 3.00},
}


class TokenAccountingManager:
    """Manages token accounting, cost calculations, and usage summaries across model tiers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._total_prompt_tokens: int = 0
            self._total_completion_tokens: int = 0
            self._total_cost_usd: float = 0.0
            self._records: List[Dict[str, Any]] = []
            self._tier_breakdown: Dict[str, Dict[str, Any]] = {
                "frontier": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
                "standard": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
                "free": {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
            }

    @staticmethod
    def resolve_pricing(model: str) -> Dict[str, Any]:
        clean = (model or "").lower().strip()
        for k, v in MODEL_PRICING.items():
            if k == clean or k in clean:
                return v
        return MODEL_PRICING["default"]

    def record_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Dict[str, Any]:
        pt = max(0, int(prompt_tokens))
        ct = max(0, int(completion_tokens))
        tot = pt + ct
        pricing = self.resolve_pricing(model)
        tier = pricing["tier"]
        prompt_rate = pricing["prompt_rate"]
        completion_rate = pricing["completion_rate"]

        cost_prompt = (pt / 1_000_000.0) * prompt_rate
        cost_completion = (ct / 1_000_000.0) * completion_rate
        total_cost = cost_prompt + cost_completion

        entry = {
            "model": model,
            "tier": tier,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tot,
            "prompt_cost_usd": round(cost_prompt, 8),
            "completion_cost_usd": round(cost_completion, 8),
            "total_cost_usd": round(total_cost, 8),
            "timestamp": time.time(),
        }

        with self._lock:
            self._total_prompt_tokens += pt
            self._total_completion_tokens += ct
            self._total_cost_usd += total_cost
            self._records.append(entry)
            if tier in self._tier_breakdown:
                self._tier_breakdown[tier]["prompt_tokens"] += pt
                self._tier_breakdown[tier]["completion_tokens"] += ct
                self._tier_breakdown[tier]["cost_usd"] = round(
                    self._tier_breakdown[tier]["cost_usd"] + total_cost, 8
                )

        return entry

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_completion_tokens": self._total_completion_tokens,
                "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
                "total_cost_usd": round(self._total_cost_usd, 8),
                "record_count": len(self._records),
                "tier_breakdown": {
                    k: dict(v) for k, v in self._tier_breakdown.items()
                },
            }


_GLOBAL_TOKEN_ACCOUNTING: Optional[TokenAccountingManager] = None


def get_token_accounting() -> TokenAccountingManager:
    global _GLOBAL_TOKEN_ACCOUNTING
    if _GLOBAL_TOKEN_ACCOUNTING is None:
        _GLOBAL_TOKEN_ACCOUNTING = TokenAccountingManager()
    return _GLOBAL_TOKEN_ACCOUNTING


def reset_token_accounting() -> None:
    global _GLOBAL_TOKEN_ACCOUNTING
    if _GLOBAL_TOKEN_ACCOUNTING is not None:
        _GLOBAL_TOKEN_ACCOUNTING.reset()
    else:
        _GLOBAL_TOKEN_ACCOUNTING = TokenAccountingManager()


class ResponsiveCoordinateScaler:
    """
    Computes responsive canvas scaling and coordinate transformations between
    physical screen pixels, dynamic viewport/canvas dimensions, and normalized [0, 1000] grid.
    """

    def __init__(
        self,
        screen_width: int = 1920,
        screen_height: int = 1080,
        canvas_width: int = 960,
        canvas_height: int = 540,
    ) -> None:
        self.screen_width = max(1, int(screen_width))
        self.screen_height = max(1, int(screen_height))
        self.canvas_width = max(1, int(canvas_width))
        self.canvas_height = max(1, int(canvas_height))

    @property
    def scale_x(self) -> float:
        return self.canvas_width / self.screen_width

    @property
    def scale_y(self) -> float:
        return self.canvas_height / self.screen_height

    def update_canvas_dimensions(self, width: int, height: int) -> None:
        self.canvas_width = max(1, int(width))
        self.canvas_height = max(1, int(height))

    def update_screen_dimensions(self, width: int, height: int) -> None:
        self.screen_width = max(1, int(width))
        self.screen_height = max(1, int(height))

    def canvas_to_normalized(self, cx: float, cy: float) -> Tuple[float, float]:
        """Convert dynamic canvas pixel coordinate to normalized [0.0, 1000.0] grid."""
        nx = max(0.0, min(1000.0, round((cx / self.canvas_width) * 1000.0, 2)))
        ny = max(0.0, min(1000.0, round((cy / self.canvas_height) * 1000.0, 2)))
        return nx, ny

    def normalized_to_canvas(self, nx: float, ny: float) -> Tuple[int, int]:
        """Convert normalized [0.0, 1000.0] grid coordinate to canvas pixel coordinate."""
        cx = int(round((max(0.0, min(1000.0, nx)) / 1000.0) * self.canvas_width))
        cy = int(round((max(0.0, min(1000.0, ny)) / 1000.0) * self.canvas_height))
        return cx, cy

    def normalized_to_screen(self, nx: float, ny: float) -> Tuple[int, int]:
        """Convert normalized [0.0, 1000.0] coordinate to physical screen pixels."""
        sx = int(round((max(0.0, min(1000.0, nx)) / 1000.0) * self.screen_width))
        sy = int(round((max(0.0, min(1000.0, ny)) / 1000.0) * self.screen_height))
        return sx, sy

    def screen_to_normalized(self, sx: float, sy: float) -> Tuple[float, float]:
        """Convert physical screen pixels to normalized [0.0, 1000.0] coordinate."""
        nx = max(0.0, min(1000.0, round((sx / self.screen_width) * 1000.0, 2)))
        ny = max(0.0, min(1000.0, round((sy / self.screen_height) * 1000.0, 2)))
        return nx, ny

    def canvas_to_screen(self, cx: float, cy: float) -> Tuple[int, int]:
        """Convert canvas pixel coordinate directly to physical screen pixels."""
        nx, ny = self.canvas_to_normalized(cx, cy)
        return self.normalized_to_screen(nx, ny)

    def screen_to_canvas(self, sx: float, sy: float) -> Tuple[int, int]:
        """Convert physical screen pixels directly to canvas pixel coordinate."""
        nx, ny = self.screen_to_normalized(sx, sy)
        return self.normalized_to_canvas(nx, ny)



class TokenTracker:
    """Thread-safe token usage and cost accounting ledger for Hydra Desktop."""

    RATES: Dict[str, Dict[str, float]] = {
        "claude-3-7-sonnet": {"prompt": 3.00, "completion": 15.00},
        "sonnet 5.5": {"prompt": 3.00, "completion": 15.00},
        "opus 5.5": {"prompt": 15.00, "completion": 75.00},
        "sol 6.1 pro": {"prompt": 2.50, "completion": 10.00},
        "gpt-6.1": {"prompt": 2.50, "completion": 10.00},
        "gemini-2.5-flash": {"prompt": 0.15, "completion": 0.60},
        "gemini 3.8": {"prompt": 0.15, "completion": 0.60},
        "glm 5.3 flash": {"prompt": 0.10, "completion": 0.40},
        "deepseek 4.1 flash": {"prompt": 0.10, "completion": 0.40},
        "qwen-3.8-27b:free": {"prompt": 0.0, "completion": 0.0},
        "deepseek-chat:free": {"prompt": 0.0, "completion": 0.0},
        "llama-3.3-70b-instruct:free": {"prompt": 0.0, "completion": 0.0},
        "free": {"prompt": 0.0, "completion": 0.0},
        "local": {"prompt": 0.0, "completion": 0.0},
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.total_cost_usd: float = 0.0
        self.by_model: Dict[str, Dict[str, Any]] = {}

    def _get_rates_for_model(self, model: str) -> Dict[str, float]:
        m = model.lower()
        if ":free" in m or m == "free" or m == "local" or "@cf/" in m:
            return {"prompt": 0.0, "completion": 0.0}
        for key, r in self.RATES.items():
            if key in m:
                return r
        return {"prompt": 1.00, "completion": 3.00}

    def record_usage(self, model: str, prompt_tokens: int, completion_tokens: int) -> Dict[str, Any]:
        with self._lock:
            pt = max(0, int(prompt_tokens))
            ct = max(0, int(completion_tokens))
            tot = pt + ct
            rates = self._get_rates_for_model(model)
            cost = (pt * rates["prompt"] + ct * rates["completion"]) / 1_000_000.0

            self.prompt_tokens += pt
            self.completion_tokens += ct
            self.total_tokens += tot
            self.total_cost_usd += cost

            m_key = model.lower()
            if m_key not in self.by_model:
                self.by_model[m_key] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
            self.by_model[m_key]["prompt_tokens"] += pt
            self.by_model[m_key]["completion_tokens"] += ct
            self.by_model[m_key]["total_tokens"] += tot
            self.by_model[m_key]["cost_usd"] += cost

            return self.get_usage()

    def get_usage(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "total_cost_usd": round(self.total_cost_usd, 6),
                "formatted_cost": f"${self.total_cost_usd:.4f}",
                "by_model": dict(self.by_model),
            }

    def reset(self) -> None:
        with self._lock:
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.total_tokens = 0
            self.total_cost_usd = 0.0
            self.by_model.clear()


_TOKEN_TRACKER: Optional[TokenTracker] = None


def get_token_tracker() -> TokenTracker:
    global _TOKEN_TRACKER
    if _TOKEN_TRACKER is None:
        _TOKEN_TRACKER = TokenTracker()
    return _TOKEN_TRACKER


COMMAND_PALETTE_ACTIONS: Dict[str, Dict[str, Any]] = {
    "agent:start": {
        "id": "agent:start",
        "title": "Start Agent ReAct Task",
        "description": "Initialize and launch autonomous agent execution loop",
        "category": "agent",
        "endpoint": "/api/agent/run",
        "method": "POST",
    },
    "agent:pause": {
        "id": "agent:pause",
        "title": "Pause Agent Task",
        "description": "Temporarily halt active agent step dispatch",
        "category": "agent",
        "endpoint": "/api/agent/pause",
        "method": "POST",
    },
    "agent:resume": {
        "id": "agent:resume",
        "title": "Resume Agent Task",
        "description": "Resume execution of paused agent task",
        "category": "agent",
        "endpoint": "/api/agent/resume",
        "method": "POST",
    },
    "agent:abort": {
        "id": "agent:abort",
        "title": "Emergency Abort Agent",
        "description": "Immediately abort active task and stop child processes within 100ms",
        "category": "agent",
        "endpoint": "/api/agent/abort",
        "method": "POST",
    },
    "agent:export_trace": {
        "id": "agent:export_trace",
        "title": "Export Session Trace (JSON)",
        "description": "Download serialized session execution history in JSON format",
        "category": "session",
        "endpoint": "/api/agent/export",
        "method": "GET",
    },
    "agent:export_markdown": {
        "id": "agent:export_markdown",
        "title": "Export Session Report (Markdown)",
        "description": "Generate and download comprehensive markdown execution audit report",
        "category": "session",
        "endpoint": "/api/agent/report/markdown",
        "method": "GET",
    },
    "agent:set_profile": {
        "id": "agent:set_profile",
        "title": "Set Tool Preset Profile",
        "description": "Configure agent tool constraints (full, browser_only, workspace_only, readonly)",
        "category": "agent",
        "endpoint": "/api/agent/profile",
        "method": "POST",
    },
    "screen:capture": {
        "id": "screen:capture",
        "title": "Capture Screen Preview",
        "description": "Grab full screenshot from primary display",
        "category": "screen",
        "endpoint": "/api/computer/screen",
        "method": "GET",
    },
    "terminal:execute": {
        "id": "terminal:execute",
        "title": "Execute Terminal Command",
        "description": "Run sandboxed bash/powershell command",
        "category": "terminal",
        "endpoint": "/api/terminal/run",
        "method": "POST",
    },
    "token:summary": {
        "id": "token:summary",
        "title": "View Token Ledger Summary",
        "description": "Inspect cumulative token accounting and cost breakdowns across model tiers",
        "category": "tokens",
        "endpoint": "/api/token/accounting",
        "method": "GET",
    },
    "token:reset": {
        "id": "token:reset",
        "title": "Reset Token Ledger",
        "description": "Zero out recorded token counts and cost totals",
        "category": "tokens",
        "endpoint": "/api/token/accounting/reset",
        "method": "POST",
    },
    "gateway:complete": {
        "id": "gateway:complete",
        "title": "Gateway Model Completion",
        "description": "Summon model with 429 provider failover",
        "category": "gateway",
        "endpoint": "/api/gateway/complete",
        "method": "POST",
    },
    "template:browse": {
        "id": "template:browse",
        "title": "Browse Workflow Templates",
        "description": "Select from pre-configured multi-step autonomous workflows",
        "category": "templates",
        "endpoint": "/api/agent/templates",
        "method": "GET",
    },
    "templates:list": {
        "id": "templates:list",
        "title": "Browse Workflow Templates",
        "description": "Explore pre-defined parameterized agent workflow templates",
        "category": "templates",
        "endpoint": "/api/templates",
        "method": "GET",
    },
    "jobs:list": {
        "id": "jobs:list",
        "title": "List Scheduled Agent Jobs",
        "description": "Inspect active and recurring scheduled agent tasks",
        "category": "jobs",
        "endpoint": "/api/jobs",
        "method": "GET",
    },
    "jobs:clear": {
        "id": "jobs:clear",
        "title": "Cancel All Scheduled Jobs",
        "description": "Terminate all recurring jobs and clear scheduler timers",
        "category": "jobs",
        "endpoint": "/api/jobs/clear",
        "method": "POST",
    },
}


def dispatch_command_palette_action(action_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispatch action by ID through the command palette dispatcher."""
    if action_id not in COMMAND_PALETTE_ACTIONS:
        return {"isError": True, "error": f"Unknown command palette action: {action_id}"}

    action_meta = COMMAND_PALETTE_ACTIONS[action_id]
    p = params or {}

    from hydra_cli.agent_runner import get_agent_runner
    runner = get_agent_runner()

    if action_id == "agent:start":
        task = p.get("task", "default command palette task")
        steps = p.get("steps")
        max_steps = int(p.get("max_steps", 15))
        res = runner.run_task(task, steps=steps, max_steps=max_steps)
        return {"isError": False, "dispatched": action_id, "result": res}
    elif action_id == "agent:pause":
        return {"isError": False, "dispatched": action_id, "result": runner.pause()}
    elif action_id == "agent:resume":
        return {"isError": False, "dispatched": action_id, "result": runner.resume()}
    elif action_id == "agent:abort":
        return {"isError": False, "dispatched": action_id, "result": runner.abort()}
    elif action_id == "agent:export_trace":
        return {"isError": False, "dispatched": action_id, "result": runner.export_session_trace()}
    elif action_id == "agent:export_markdown":
        from hydra_cli.agent_runner import generate_markdown_report
        trace = runner.export_session_trace()
        return {"isError": False, "dispatched": action_id, "markdown": generate_markdown_report(trace)}
    elif action_id == "agent:set_profile":
        prof = p.get("profile", "full")
        runner.set_tool_profile(prof)
        return {"isError": False, "dispatched": action_id, "profile": prof}
    elif action_id == "token:summary":
        return {"isError": False, "dispatched": action_id, "summary": get_token_accounting().get_summary()}
    elif action_id == "token:reset":
        reset_token_accounting()
        return {"isError": False, "dispatched": action_id, "summary": get_token_accounting().get_summary()}
    elif action_id == "screen:capture":
        engine = get_computer_use_engine()
        cap = engine.screen.capture(as_base64=True)
        return {"isError": False, "dispatched": action_id, "captured": True, "data_length": len(cap)}
    elif action_id == "terminal:execute":
        cmd = p.get("command", "echo palette")
        runner_sb = SandboxRunner()
        sb_res = runner_sb.run_command(cmd)
        return {"isError": False, "dispatched": action_id, "result": sb_res.to_dict()}
    elif action_id == "gateway:complete":
        m = p.get("model", "sonnet 5.5")
        prompt = p.get("prompt", "ping")
        gw_res = resolve_and_complete_with_fallback(m, prompt)
        return {"isError": False, "dispatched": action_id, "result": gw_res}
    elif action_id in ("templates:list", "template:browse"):
        from hydra_cli.agent_runner import list_workflow_templates
        return {"isError": False, "dispatched": action_id, "templates": list_workflow_templates()}
    elif action_id == "jobs:list":
        from hydra_cli.agent_runner import get_job_scheduler
        return {"isError": False, "dispatched": action_id, "jobs": get_job_scheduler().list_jobs()}
    elif action_id == "jobs:clear":
        from hydra_cli.agent_runner import get_job_scheduler
        get_job_scheduler().reset()
        return {"isError": False, "dispatched": action_id, "cleared": True}

    return {"isError": False, "dispatched": action_id, "meta": action_meta}


FALLBACK_CHAINS: Dict[str, List[str]] = {
    "sonnet 5.5": ["gemini 3.8", "gpt-6.1", "qwen"],
    "opus 5.5": ["sonnet 5.5", "gpt-6.1", "gemini 3.8"],
    "gpt-6.1": ["sonnet 5.5", "gemini 3.8", "qwen"],
    "default": ["gemini 3.8", "free", "glm 5.3 flash"],
}


def resolve_and_complete_with_fallback(
    model: str,
    prompt: str,
    fallbacks: Optional[List[str]] = None,
    completer: Optional[Callable[[str, str], str]] = None,
) -> Dict[str, Any]:
    """
    Resolve model alias and execute completion with fallback resilience under 429 rate limits.
    """
    from hydra_cli.config import MODEL_MAP
    from hydra_cli import complete

    completer_fn = completer or complete
    chain = [model]
    if fallbacks:
        chain.extend(fallbacks)
    else:
        chain.extend(FALLBACK_CHAINS.get(model.lower(), FALLBACK_CHAINS["default"]))

    attempts = []
    last_err = None

    for m in chain:
        resolved_id = MODEL_MAP.get(m.lower(), m)
        try:
            ans = completer_fn(resolved_id, prompt)
            pt = len(prompt.split())
            ct = len(ans.split())
            token_rec = get_token_accounting().record_usage(m, pt, ct)
            return {
                "isError": False,
                "content": ans,
                "model_requested": model,
                "model_used": m,
                "resolved_model_id": resolved_id,
                "fallback_triggered": (m != model),
                "attempts": attempts,
                "usage": {
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "total_tokens": pt + ct,
                },
                "token_accounting": token_rec,
            }
        except Exception as exc:
            err_str = str(exc)
            attempts.append({"model": m, "error": err_str})
            last_err = exc
            is_rate_limit = "429" in err_str or "rate limit" in err_str.lower() or "quota" in err_str.lower()
            if not is_rate_limit and len(attempts) > 1:
                pass

    return {
        "isError": True,
        "error": f"All model providers in fallback chain failed: {last_err}",
        "model_requested": model,
        "attempts": attempts,
    }
