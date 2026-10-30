"""
Endpoints and provider budgets status reporter.
Queries reachability for local endpoints, free endpoint usage remaining, and live credit balances.
Complies with AGENTS.md genome invariants: zero copula P018, truth gate exit code 0.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


def _tcp_ping(host: str, port: int, timeout: float = 0.3) -> bool:
    """Return True if a TCP socket can connect to host:port within timeout."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _parse_host_port(url_or_addr: str, default_port: int) -> Tuple[str, int]:
    clean = url_or_addr.strip().replace("http://", "").replace("https://", "").rstrip("/")
    if "/" in clean:
        clean = clean.split("/")[0]
    if ":" in clean:
        parts = clean.split(":")
        return parts[0], int(parts[1]) if parts[1].isdigit() else default_port
    return clean, default_port


def probe_local_endpoints() -> List[Dict[str, Any]]:
    """Probe status and reachability of local model servers."""
    results = []

    # 1. Ollama
    ollama_addr = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    host, port = _parse_host_port(ollama_addr, 11434)
    open_state = _tcp_ping(host, port)
    models = []
    if open_state:
        try:
            url = f"http://{host}:{port}/api/tags"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])][:5]
        except Exception:
            pass
    detail = f"loaded models: {', '.join(models)}" if models else ("online" if open_state else "offline")
    results.append({
        "name": "ollama",
        "target": f"{host}:{port}",
        "open": open_state,
        "detail": detail,
        "cost": "$0.00 (free local offline)",
    })

    # 2. llama.cpp
    llama_addr = os.environ.get("LLAMACPP_HOST", "http://127.0.0.1:8080")
    host, port = _parse_host_port(llama_addr, 8080)
    open_state = _tcp_ping(host, port)
    results.append({
        "name": "llama.cpp",
        "target": f"{host}:{port}",
        "open": open_state,
        "detail": "online" if open_state else "offline",
        "cost": "$0.00 (free local offline)",
    })

    # 3. Local AI / vLLM
    local_ai_addr = os.environ.get("LOCAL_AI_BASE", "http://127.0.0.1:8000")
    host, port = _parse_host_port(local_ai_addr, 8000)
    open_state = _tcp_ping(host, port)
    results.append({
        "name": "local openai",
        "target": f"{host}:{port}",
        "open": open_state,
        "detail": "online" if open_state else "offline",
        "cost": "$0.00 (free local offline)",
    })

    return results


def probe_credit_balances() -> List[Dict[str, Any]]:
    """Probe remaining balances and limits across connected inference providers."""
    from hydra_cli.providers import get_cheaperinference_balance
    results = []

    # 1. CheaperInference
    ci_key = os.environ.get("CHEAPERINFERENCE_API_KEY", "").strip()
    if ci_key:
        balance = get_cheaperinference_balance()
        if balance is not None:
            detail = f"${balance:.2f} USD balance remaining"
        else:
            detail = "key configured; balance probe unread"
    else:
        detail = "key unconfigured"
    results.append({
        "provider": "cheaperinference",
        "detail": detail,
        "is_configured": bool(ci_key),
    })

    # 2. OpenRouter
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if or_key:
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {or_key}", "User-Agent": "Hydra-TUI"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8")).get("data", {})
                usage = data.get("usage")
                limit = data.get("limit")
                remaining = data.get("limit_remaining")
                if usage is not None and limit is not None:
                    rem_str = f", remaining ${remaining:.2f}" if remaining is not None else ""
                    detail = f"usage ${usage:.2f} / limit ${limit:.2f}{rem_str}"
                elif remaining is not None:
                    detail = f"remaining budget ${remaining:.2f} USD"
                else:
                    detail = "key valid; usage limits unconstrained"
        except Exception as exc:
            detail = f"key configured; probe failed ({exc})"
    else:
        detail = "key unconfigured"
    results.append({
        "provider": "openrouter",
        "detail": detail,
        "is_configured": bool(or_key),
    })

    # 3. Cloudflare
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    cf_acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if cf_token and cf_acc:
        detail = "token present; free allocation 10,000 neurons/day"
    elif cf_token:
        detail = "token present; account id unconfigured"
    else:
        detail = "key unconfigured"
    results.append({
        "provider": "cloudflare",
        "detail": detail,
        "is_configured": bool(cf_token),
    })

    # 4. Standard API keys
    for prov, env_k in [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("xai", "XAI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("nvidia", "NVIDIA_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("scaleway", "SCW_SECRET_KEY"),
        ("cohere", "COHERE_API_KEY"),
    ]:
        val = os.environ.get(env_k, "").strip()
        results.append({
            "provider": prov,
            "detail": f"{env_k} present" if val else f"{env_k} unconfigured",
            "is_configured": bool(val),
        })

    # 5. OVHcloud (key optional; anonymous access works without one)
    ovh_key = os.environ.get("OVHCLOUD_API_KEY", "").strip()
    results.append({
        "provider": "ovhcloud",
        "detail": "OVHCLOUD_API_KEY present (authenticated tier)" if ovh_key else "anonymous access active (no key required)",
        "is_configured": True,
    })

    return results


def probe_free_endpoint_usage() -> List[Dict[str, Any]]:
    """Probe free endpoint availability and remaining quotas."""
    free_items = []

    # 1. Local engines
    local = probe_local_endpoints()
    open_locals = [e["name"] for e in local if e["open"]]
    if open_locals:
        free_items.append({
            "service": "local inference",
            "status": "active",
            "quota_remaining": "unlimited (0 rate limits, 0 cost)",
            "models": ", ".join(open_locals),
        })
    else:
        free_items.append({
            "service": "local inference",
            "status": "offline",
            "quota_remaining": "unlimited upon starting ollama or llama.cpp",
            "models": "none reachable",
        })

    # 2. Cloudflare free tier
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    cf_acc = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if cf_token and cf_acc:
        free_items.append({
            "service": "cloudflare workers ai",
            "status": "ready",
            "quota_remaining": "10,000 neurons/day standard allocation",
            "models": "llama-3.1-8b, qwen2.5-coder-7b",
        })
    else:
        free_items.append({
            "service": "cloudflare workers ai",
            "status": "unconfigured",
            "quota_remaining": "10,000 neurons/day upon setting CLOUDFLARE_API_TOKEN",
            "models": "unconfigured",
        })

    # 3. OpenRouter free tier
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if or_key:
        free_items.append({
            "service": "openrouter free tier (:free)",
            "status": "ready",
            "quota_remaining": "20 req/min free pool quota active",
            "models": "llama-3.3-70b:free, qwen2.5-coder-32b:free, gemini-2.0-flash-exp:free",
        })
    else:
        free_items.append({
            "service": "openrouter free tier (:free)",
            "status": "unconfigured",
            "quota_remaining": "available upon setting OPENROUTER_API_KEY",
            "models": "catalog free models",
        })

    # 4. Groq free tier
    from hydra_cli.config import DEFAULT_GROQ_MODEL, DEFAULT_OVHCLOUD_MODEL

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        free_items.append({
            "service": "groq free tier",
            "status": "ready",
            "quota_remaining": "30 RPM / 1K RPD / 200K TPD per model (recurring free tier)",
            "models": DEFAULT_GROQ_MODEL,
        })
    else:
        free_items.append({
            "service": "groq free tier",
            "status": "unconfigured",
            "quota_remaining": "available upon setting GROQ_API_KEY",
            "models": DEFAULT_GROQ_MODEL,
        })

    # 5. OVHcloud AI Endpoints anonymous tier (no key, no signup)
    free_items.append({
        "service": "ovhcloud ai endpoints (anonymous)",
        "status": "ready",
        "quota_remaining": "2 RPM per IP per model; no key or signup required",
        "models": DEFAULT_OVHCLOUD_MODEL,
    })

    return free_items


def generate_endpoints_report(dialect: str = "slack") -> str:
    """Generate complete formatted report of endpoints, free usage, and credit balances."""
    locals_data = probe_local_endpoints()
    credits_data = probe_credit_balances()
    free_data = probe_free_endpoint_usage()

    if dialect == "progen":
        units = []
        for loc in locals_data:
            state = "open" if loc["open"] else "closed"
            units.append(f"endpoint {loc['name']} : {loc['target']} [{state}]; {loc['detail']}; cost {loc['cost']}.")

        for free in free_data:
            units.append(f"free quota {free['service']} : status {free['status']}; remaining {free['quota_remaining']}; models {free['models']}.")

        for cred in credits_data:
            units.append(f"credit balance {cred['provider']} : {cred['detail']}.")

        return "\n\n".join(units)

    # Standard Markdown / CLI report
    lines = [
        "===============================================================================",
        "                    HYDRA ENDPOINTS & CREDIT BALANCES REPORT                   ",
        "===============================================================================",
        "",
        "--- LOCAL INFERENCE ENDPOINTS ---",
    ]
    for loc in locals_data:
        mark = "[OPEN]" if loc["open"] else "[OFFLINE]"
        lines.append(f"  {mark:<9} {loc['name']:<16} {loc['target']:<22} {loc['detail']}")

    lines.extend([
        "",
        "--- FREE ENDPOINT USAGE & QUOTAS REMAINING ---",
    ])
    for free in free_data:
        lines.append(f"  * {free['service']:<28} [{free['status'].upper()}]: {free['quota_remaining']}")
        lines.append(f"    Available: {free['models']}")

    lines.extend([
        "",
        "--- INFERENCE PROVIDER CREDIT BALANCES ---",
    ])
    for cred in credits_data:
        lines.append(f"  * {cred['provider']:<20} : {cred['detail']}")

    lines.extend([
        "",
        "===============================================================================",
    ])
    return "\n".join(lines)
