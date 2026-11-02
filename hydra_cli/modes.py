"""Hydra modes: local / free / light / heavy / max (owner doctrine, 2026-11-01).

The routing tables live in modes.json beside this module: inspectable data,
not buried code. This module loads them, tracks the current mode (env
HYDRA_MODE wins, then ~/.hydra/settings.json, then the modes.json default),
classifies models, and answers the one question the router asks before it
spends anything: may this model be served under the current mode?

Mode changes made through `hydra mode`, `/mode`, or the desktop are also
written to an NDJSON decision log (~/.hydra/logs/decisions.jsonl): one line
per decision, no prompt content, in the OpenMausBot decider style.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DATA: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _DATA
    if _DATA is None:
        path = Path(__file__).with_name("modes.json")
        _DATA = json.loads(path.read_text(encoding="utf-8"))
    return _DATA


CANONICAL_ORDER = ["local", "free", "light", "heavy", "max"]


def mode_names() -> List[str]:
    known = _load()["modes"]
    return [name for name in CANONICAL_ORDER if name in known] + [n for n in known if n not in CANONICAL_ORDER]


def settings_path() -> Path:
    return Path.home() / ".hydra" / "settings.json"


def _read_settings() -> Dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def current_mode() -> str:
    env = os.environ.get("HYDRA_MODE", "").strip().lower()
    if env in _load()["modes"]:
        return env
    saved = str(_read_settings().get("mode") or "").strip().lower()
    if saved in _load()["modes"]:
        return saved
    default = str(_load().get("default_mode") or "light").strip().lower()
    return default if default in _load()["modes"] else "light"


def set_mode(name: str, source: str = "cli") -> Dict[str, Any]:
    """Persist a mode change and log the decision. Returns the mode spec."""
    name = name.strip().lower()
    if name not in _load()["modes"]:
        raise ValueError(f"unknown mode {name!r}; modes are: {', '.join(mode_names())}")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = _read_settings()
    settings["mode"] = name
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    log_decision("mode_set", {"mode": name, "source": source})
    return mode_spec(name)


def mode_spec(name: Optional[str] = None) -> Dict[str, Any]:
    return _load()["modes"][name or current_mode()]


def default_alias(name: Optional[str] = None) -> Optional[str]:
    return mode_spec(name).get("default_alias")


def routes(name: Optional[str] = None) -> List[Dict[str, Any]]:
    return list(mode_spec(name).get("routes") or [])


def light_cap() -> float:
    return float(_load().get("light_price_cap_usd_per_million") or 1.0)


def log_decision(event: str, fields: Dict[str, Any]) -> None:
    """Append one NDJSON decision line. Never carries prompt content."""
    try:
        log_dir = Path.home() / ".hydra" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        line = {"ts": int(time.time()), "event": event}
        line.update(fields)
        with open(log_dir / "decisions.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception:
        pass


def classify_model(model_id: str) -> str:
    """One of 'local', 'free', 'paid', from the catalog's own data."""
    from hydra_cli.config import (
        DEFAULT_CLOUDFLARE_MODEL,
        DEFAULT_FREE_MODEL,
        FREE_MODELS,
        model_providers,
    )

    mid = (model_id or "").strip()
    allowed = model_providers(mid)
    if allowed and set(allowed) <= {"local"}:
        return "local"
    if mid in FREE_MODELS or mid == DEFAULT_FREE_MODEL or mid == DEFAULT_CLOUDFLARE_MODEL:
        return "free"
    if mid.endswith(":free") or mid.startswith("@cf/"):
        return "free"
    return "paid"


def _light_entry(model_id: str) -> Optional[Dict[str, Any]]:
    from hydra_cli.config import resolve_route

    spec = mode_spec("light")
    for entry in spec.get("eligible") or []:
        if entry.get("model") == model_id:
            return entry
        alias = entry.get("alias")
        if alias:
            try:
                if resolve_route(alias).get("model") == model_id:
                    return entry
            except Exception:
                continue
    return None


def light_eligible(model_id: str) -> Tuple[bool, str]:
    entry = _light_entry(model_id)
    if entry is None:
        return False, "not in the light table in modes.json"
    cap = light_cap()
    inp, outp = entry.get("input_per_million"), entry.get("output_per_million")
    if isinstance(inp, (int, float)) and isinstance(outp, (int, float)):
        if inp < cap and outp < cap:
            return True, f"light table: ${inp}/M in, ${outp}/M out, under the ${cap}/M cap"
        return False, f"light table prices reach the cap: ${inp}/M in, ${outp}/M out (cap ${cap}/M both ways)"
    return True, "light table (PROVISIONAL: named in the plan, prices unverified until CENSUS-2026-11-01.md lands)"


def check_request(model_id: str, mode: Optional[str] = None) -> Tuple[bool, str]:
    """May `model_id` be served under `mode` (default: the current mode)?"""
    mode = mode or current_mode()
    kind = classify_model(model_id)
    if mode == "local":
        if kind == "local":
            return True, "local model under local mode"
        return False, f"local mode serves local models only; {model_id} is a {kind} model. Switch with: hydra mode free|light|heavy|max"
    if mode == "free":
        if kind in ("local", "free"):
            return True, f"{kind} route under free mode"
        return False, f"free mode serves free endpoints and local models only; {model_id} is a paid model. Switch with: hydra mode light|heavy|max"
    if mode == "light":
        if kind in ("local", "free"):
            return True, f"{kind} route under light mode"
        ok, why = light_eligible(model_id)
        if ok:
            return True, why
        return False, f"light mode caps paid models at ${light_cap()}/M tokens both ways; {model_id}: {why}. Switch with: hydra mode heavy|max"
    if mode == "heavy":
        max_class = {str(m) for m in _load().get("max_class_models") or []}
        if model_id in max_class:
            return False, f"heavy mode tops out at Opus-level pricing; {model_id} is Fable/Astra class. Switch with: hydra mode max"
        return True, "under the heavy ceiling"
    if mode == "max":
        return True, "max mode: every configured route is fair game"
    return False, f"unknown mode {mode!r}"


def cascade_tiers(mode: Optional[str] = None) -> Dict[str, bool]:
    """Which fallback tiers execute_summon may use under the mode.

    The tiers only ever descend in price (frontier -> Cloudflare free ->
    OpenRouter free -> local), so the binding constraint is local mode:
    nothing may leave the machine, which the router expresses through its
    existing local_only path.
    """
    mode = mode or current_mode()
    if mode == "local":
        return {"frontier": True, "cloudflare": False, "openrouter_free": False, "local": True}
    return {"frontier": True, "cloudflare": True, "openrouter_free": True, "local": True}


def describe(name: Optional[str] = None) -> str:
    """Human-readable mode sheet for `hydra mode`, `/mode`, and the desktop."""
    name = name or current_mode()
    spec = mode_spec(name)
    lines = [f"mode: {name}", f"  {spec.get('summary', '')}"]
    alias = spec.get("default_alias")
    lines.append(f"  default driver: {alias or '(free forge chain / incumbent default)'}")
    for route in spec.get("routes") or []:
        ctx = route.get("context", "max")
        provs = ", ".join(route.get("providers") or [])
        lines.append(f"  route: {route.get('model')} via {provs} (context {ctx})")
        if route.get("note"):
            lines.append(f"         {route['note']}")
    if name == "light":
        lines.append(f"  eligible paid models (cap ${light_cap()}/M in AND out):")
        for entry in spec.get("eligible") or []:
            lines.append(f"    - {entry.get('model')} [{entry.get('price_status', '')}]")
        lines.append(f"  {_load().get('light_table_status', '')}")
    return "\n".join(lines)
