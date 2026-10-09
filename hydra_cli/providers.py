"""
Provider integrations and streaming transport for Hydra CLI.
Supports OpenRouter, Vercel AI Gateway, Cloudflare Workers AI, and local inference.
"""

import hashlib
import json
import os
import re
import socket
import urllib.error
import urllib.request
import random
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple
from urllib.parse import urlparse

from hydra_cli.config import (
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_FREE_MODEL,
    DEFAULT_HF_BASE,
    DEFAULT_HF_MODEL,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    FREE_MODELS,
    PROVIDER_KEY_NAMES,
    load_dotenv,
    model_providers,
    model_rejects_temperature,
    resolve_route,
    sensitive_env_key,
)


class ProviderError(Exception):
    """Base exception for provider invocation failures."""


class CredentialsMissingError(ProviderError):
    """Raised when required credentials are missing."""


class UsageError(ProviderError):
    """Raised when the request is invalid before any provider is called."""


def clean_secret(value: Optional[str]) -> str:
    """Strip a credential and reject values that could break a header."""
    text = (value or "").strip()
    if any(char in text for char in "\r\n"):
        return ""
    return text


_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>()]+")
_ACCOUNT_PATH_RE = re.compile(r"(/accounts/)[^/\s\"']+")
_MIN_SECRET_LEN = 6


def describe_endpoint(url: str) -> str:
    """Host (and port) only. Paths can carry account or endpoint ids, so they stay out of messages."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host and parsed.port:
            host = f"{host}:{parsed.port}"
        return host or "<endpoint>"
    except Exception:
        return "<endpoint>"


def _short_url(match: "re.Match") -> str:
    raw = match.group(0)
    try:
        parsed = urlparse(raw)
    except Exception:
        return "<url>"
    host = describe_endpoint(raw)
    has_more = bool(parsed.path.strip("/") or parsed.query or parsed.fragment)
    return f"{parsed.scheme}://{host}/..." if has_more else f"{parsed.scheme}://{host}"


def redact(text: Any) -> str:
    """Scrub credential values, account ids, and URL paths from text meant for a terminal."""
    out = str(text)
    for key, value in os.environ.items():
        if not sensitive_env_key(key):
            continue
        secret = (value or "").strip()
        if len(secret) >= _MIN_SECRET_LEN:
            out = out.replace(secret, "<redacted>")
    out = _URL_RE.sub(_short_url, out)
    out = _ACCOUNT_PATH_RE.sub(r"\1<redacted>", out)
    return out


def gateway_api_key() -> str:
    """Prefer AI_GATEWAY_API_KEY. Accept the older documented name as an alias."""
    return clean_secret(os.environ.get("AI_GATEWAY_API_KEY")) or clean_secret(
        os.environ.get("VERCEL_AI_GATEWAY_TOKEN")
    )


def is_port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    """Check if a TCP port is open with a fast timeout."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def chat_completions_url(base: str) -> str:
    """Normalize a server origin to an OpenAI-compatible chat completions URL."""
    root = base.strip().rstrip("/")
    if root.endswith("/chat/completions"):
        return root
    if not root.endswith("/v1"):
        root = f"{root}/v1"
    return f"{root}/chat/completions"


def detect_local_endpoint() -> Tuple[str, str]:
    """
    Return (endpoint_url, provider_name).
    An explicit host variable is used as given. Ports are probed only for unset defaults.
    """
    custom_base = os.environ.get("LOCAL_AI_BASE", "").strip()
    if custom_base:
        return chat_completions_url(custom_base), "Custom Local AI"

    ollama_host = os.environ.get("OLLAMA_HOST", "").strip()
    if ollama_host:
        return chat_completions_url(ollama_host), "Ollama"
    if is_port_open("127.0.0.1", 11434):
        return chat_completions_url("http://127.0.0.1:11434"), "Ollama"

    llama_host = os.environ.get("LLAMACPP_HOST", "").strip()
    if llama_host:
        return chat_completions_url(llama_host), "llama.cpp"
    if is_port_open("127.0.0.1", 8080):
        return chat_completions_url("http://127.0.0.1:8080"), "llama.cpp"

    if is_port_open("127.0.0.1", 8000):
        return chat_completions_url("http://127.0.0.1:8000"), "EasyLM"

    return chat_completions_url("http://127.0.0.1:11434"), "Ollama (unverified)"


# ===========================================================================
# Health-Based Dynamic Routing & Circuit Breaker Engine (WO-05)
# ===========================================================================

class ProviderHealthTracker:
    """Health-based dynamic model routing and circuit breaker engine (WO-05).

    Tracks provider health status, error rates, consecutive failures, and HTTP 429 rate limits.
    Enforces exponential backoff and circuit breaker state transitions.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        backoff_factor: float = 2.0,
        max_cooldown: float = 300.0,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.backoff_factor = backoff_factor
        self.max_cooldown = max_cooldown
        self._stats: Dict[str, Dict[str, Any]] = {}

    def _get_key(self, target: Any) -> str:
        if isinstance(target, dict):
            return str(target.get("id") or describe_endpoint(target.get("url", "")))
        if isinstance(target, str):
            return describe_endpoint(target) if "://" in target else target
        return str(target)

    def _entry(self, key: str) -> Dict[str, Any]:
        if key not in self._stats:
            self._stats[key] = {
                "status": "healthy",
                "consecutive_failures": 0,
                "consecutive_successes": 0,
                "total_requests": 0,
                "total_failures": 0,
                "total_429s": 0,
                "last_failure_time": 0.0,
                "last_success_time": 0.0,
                "last_error": None,
                "circuit_open_until": 0.0,
                "current_cooldown": self.cooldown_seconds,
            }
        return self._stats[key]

    def record_success(self, target: Any) -> None:
        key = self._get_key(target)
        entry = self._entry(key)
        entry["total_requests"] += 1
        entry["consecutive_successes"] += 1
        entry["consecutive_failures"] = 0
        entry["last_success_time"] = time.time()
        entry["circuit_open_until"] = 0.0
        entry["current_cooldown"] = self.cooldown_seconds
        entry["status"] = "healthy"

    def record_failure(self, target: Any, error: Optional[Any] = None, is_429: bool = False) -> None:
        key = self._get_key(target)
        entry = self._entry(key)
        entry["total_requests"] += 1
        entry["total_failures"] += 1
        entry["consecutive_failures"] += 1
        entry["consecutive_successes"] = 0
        entry["last_failure_time"] = time.time()
        entry["last_error"] = str(error) if error else "Unknown error"

        if is_429:
            entry["total_429s"] += 1

        if is_429 or entry["consecutive_failures"] >= self.failure_threshold:
            entry["status"] = "unhealthy"
            entry["circuit_open_until"] = time.time() + entry["current_cooldown"]
            entry["current_cooldown"] = min(entry["current_cooldown"] * self.backoff_factor, self.max_cooldown)
        else:
            entry["status"] = "degraded"

    def is_healthy(self, target: Any) -> bool:
        key = self._get_key(target)
        entry = self._entry(key)
        if entry["circuit_open_until"] > 0:
            if time.time() >= entry["circuit_open_until"]:
                entry["status"] = "degraded"
                entry["circuit_open_until"] = 0.0
                return True
            return False
        return True

    def get_health_status(self, target: Any) -> str:
        key = self._get_key(target)
        if not self.is_healthy(key):
            return "unhealthy"
        return self._entry(key)["status"]

    def get_stats(self, target: Optional[Any] = None) -> Any:
        if target is not None:
            return dict(self._entry(self._get_key(target)))
        return {k: dict(v) for k, v in self._stats.items()}

    def rank_providers(self, providers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Dynamically prioritize healthy providers over degraded and unhealthy providers.

        Preserves original catalog preference order within each health tier.
        """
        def _tier(p: Dict[str, Any]) -> int:
            status = self.get_health_status(p)
            if status == "healthy":
                return 0
            if status == "degraded":
                return 1
            return 2

        return sorted(providers, key=_tier)

    def reset(self) -> None:
        self._stats.clear()


GLOBAL_HEALTH_TRACKER = ProviderHealthTracker()


def get_provider_health(target: Optional[Any] = None) -> Any:
    return GLOBAL_HEALTH_TRACKER.get_stats(target)


def reset_provider_health() -> None:
    GLOBAL_HEALTH_TRACKER.reset()


def record_provider_success(target: Any) -> None:
    GLOBAL_HEALTH_TRACKER.record_success(target)


def record_provider_failure(target: Any, error: Optional[Any] = None, is_429: bool = False) -> None:
    GLOBAL_HEALTH_TRACKER.record_failure(target, error=error, is_429=is_429)



def get_cheaperinference_provider() -> Optional[Dict[str, Any]]:
    """Return CheaperInference provider dict when credentials are present."""
    key = clean_secret(os.environ.get("CHEAPERINFERENCE_API_KEY"))
    if not key:
        return None
    base = os.environ.get("CHEAPERINFERENCE_API_BASE", "https://api.cheaperinference.com/v1").strip()
    return {
        "id": "cheaperinference",
        "name": "CheaperInference",
        "url": chat_completions_url(base),
        "headers": {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    }


CHEAPERINFERENCE_BALANCE_FLOOR = 2.00


def get_cheaperinference_balance(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 3.0,
) -> Optional[float]:
    """Query CheaperInference credit balance via environment or lightweight HTTP probe.

    Returns float balance in USD, or None when offline or unconfigured.
    """
    env_override = os.environ.get("CHEAPERINFERENCE_CREDIT_BALANCE", "").strip()
    if env_override:
        try:
            return float(env_override)
        except ValueError:
            pass

    key = clean_secret(api_key or os.environ.get("CHEAPERINFERENCE_API_KEY"))
    if not key:
        return None

    base = (base_url or os.environ.get("CHEAPERINFERENCE_API_BASE", "https://api.cheaperinference.com/v1")).strip().rstrip("/")
    if base.endswith("/v1"):
        root_base = base
    else:
        root_base = f"{base}/v1"

    probe_urls = [
        f"{root_base}/user/balance",
        f"{root_base}/balance",
        f"{root_base}/dashboard/billing/credit_grants",
    ]

    for url in probe_urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {key}", "User-Agent": "Hydra-Balance"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw)
                    for field in ("balance", "credit", "credits", "total_available"):
                        if field in data and isinstance(data[field], (int, float)):
                            return float(data[field])
                    if "data" in data and isinstance(data["data"], dict):
                        inner = data["data"]
                        for field in ("balance", "credit", "credits", "total_available"):
                            if field in inner and isinstance(inner[field], (int, float)):
                                return float(inner[field])
        except Exception:
            continue

    return None


def check_cheaperinference_balance_guard(
    threshold: float = CHEAPERINFERENCE_BALANCE_FLOOR,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Tuple[bool, Optional[float], str]:
    """Verify CheaperInference balance exceeds minimum floor before expensive swarm dispatch.

    Emits circuit breaker trip when credit balance drops to or below threshold.
    """
    balance = get_cheaperinference_balance(api_key=api_key, base_url=base_url)
    if balance is None:
        return True, None, "balance unverified; proceed under default quota"

    if balance <= threshold:
        return (
            False,
            balance,
            f"circuit breaker : CheaperInference balance ${balance:.2f} at or below ${threshold:.2f} floor; swarm dispatch halted; dirty worktree preserved.",
        )

    return True, balance, f"CheaperInference balance ${balance:.2f} above ${threshold:.2f} floor." 


def get_runpod_provider() -> Optional[Dict[str, Any]]:
    """Return RunPod provider dict when credentials/endpoints are present."""
    key = clean_secret(os.environ.get("RUNPOD_API_KEY"))
    endpoint_url = os.environ.get("RUNPOD_ENDPOINT_URL", "").strip()
    endpoint_id = os.environ.get("RUNPOD_ENDPOINT_ID", "").strip()

    if endpoint_url:
        target_url = chat_completions_url(endpoint_url)
    elif endpoint_id:
        target_url = chat_completions_url(f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1")
    else:
        return None

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    elif not endpoint_url:
        return None

    return {
        "id": "runpod",
        "name": "RunPod",
        "url": target_url,
        "headers": headers,
    }


def get_modal_provider() -> Optional[Dict[str, Any]]:
    """Return Modal provider dict when endpoint URL is present."""
    endpoint_url = os.environ.get("MODAL_ENDPOINT_URL", "").strip()
    if not endpoint_url:
        return None
    key = clean_secret(os.environ.get("MODAL_API_KEY"))
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return {
        "id": "modal",
        "name": "Modal",
        "url": chat_completions_url(endpoint_url),
        "headers": headers,
    }


def get_huggingface_provider() -> Optional[Dict[str, Any]]:
    """Return Hugging Face Inference provider dict when credentials or endpoints are present."""
    token = (
        clean_secret(os.environ.get("HF_TOKEN"))
        or clean_secret(os.environ.get("HUGGINGFACE_API_KEY"))
        or clean_secret(os.environ.get("HUGGING_FACE_HUB_TOKEN"))
    )
    endpoint_url = os.environ.get("HF_ENDPOINT_URL", "").strip() or os.environ.get("HUGGINGFACE_ENDPOINT_URL", "").strip()
    base_url = os.environ.get("HF_INFERENCE_BASE", DEFAULT_HF_BASE).strip()

    if endpoint_url:
        target_url = chat_completions_url(endpoint_url)
    elif token or base_url != "https://router.huggingface.co/v1":
        target_url = chat_completions_url(base_url)
    else:
        return None

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/erastudil/hydra",
        "X-Title": "Hydra Multi-Headed AI Summoning CLI",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    return {
        "id": "huggingface",
        "name": "Hugging Face Inference",
        "url": target_url,
        "headers": headers,
    }


def get_hf_provider() -> Tuple[Dict[str, Any], str]:
    """Resolve provider and default model for Hugging Face Inference API."""
    provider = get_huggingface_provider()
    if not provider:
        raise CredentialsMissingError(
            "Hugging Face Inference requires HF_TOKEN or HUGGINGFACE_API_KEY.\n"
            "Export HF_TOKEN in ~/.hydra/.env or run 'hf auth login'."
        )
    default_model = os.environ.get("HYDRA_HF_MODEL", DEFAULT_HF_MODEL)
    return provider, default_model


def get_frontier_providers() -> List[Dict[str, Any]]:
    """Return configured frontier cloud providers in sovereign priority order:
    1. Hugging Face (top priority if configured and model available)
    2. CheaperInference (ultra low-cost endpoints)
    3. Vercel AI Gateway (fast / free endpoints)
    4. OpenRouter (aggregator fallback)
    5. Modal (serverless container compute)
    6. RunPod (serverless GPU compute)
    """
    providers = []

    hf = get_huggingface_provider()
    if hf:
        providers.append(hf)

    cheaper = get_cheaperinference_provider()
    if cheaper:
        providers.append(cheaper)

    vercel_key = gateway_api_key()
    if vercel_key:
        gateway_base = os.environ.get("AI_GATEWAY_API_BASE", "https://ai-gateway.vercel.sh/v1").rstrip("/")
        providers.append({
            "id": "vercel",
            "name": "Vercel AI Gateway",
            "url": f"{gateway_base}/chat/completions",
            "headers": {
                "Authorization": f"Bearer {vercel_key}",
                "Content-Type": "application/json",
            },
        })

    openrouter_key = clean_secret(os.environ.get("OPENROUTER_API_KEY"))
    if openrouter_key:
        providers.append({
            "id": "openrouter",
            "name": "OpenRouter",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/erastudil/hydra",
                "X-Title": "Hydra Multi-Headed AI Summoning CLI",
            },
        })

    modal = get_modal_provider()
    if modal:
        providers.append(modal)

    runpod = get_runpod_provider()
    if runpod:
        providers.append(runpod)

    return providers


def providers_for_model(
    model: str,
    providers: Optional[List[Dict[str, Any]]] = None,
    health_aware: bool = True,
) -> List[Dict[str, Any]]:
    """Configured providers that may serve this model, in fallback order.

    The catalog's model_providers map limits a model to the providers that carry it
    (for example a Vercel-only alias). Other models may use every configured provider.
    When health_aware is True, healthy providers are dynamically prioritized ahead of
    degraded or tripped circuit-breaker providers (WO-05).
    """
    configured = get_frontier_providers() if providers is None else list(providers)
    allowed = model_providers(model)
    if not allowed:
        usable = configured
    else:
        rank = {pid: index for index, pid in enumerate(allowed)}
        usable = [p for p in configured if p.get("id") in rank]
        usable.sort(key=lambda p: rank[p["id"]])
        if configured and not usable:
            names = ", ".join(PROVIDER_KEY_NAMES.get(pid, pid) for pid in allowed)
            raise CredentialsMissingError(
                f"{model} is only served by: {', '.join(allowed)}. Set {names} to use it."
            )
    if health_aware:
        return GLOBAL_HEALTH_TRACKER.rank_providers(usable)
    return usable


def _cloudflare_provider() -> Optional[Dict[str, Any]]:
    cf_token = clean_secret(os.environ.get("CLOUDFLARE_API_TOKEN"))
    cf_account = clean_secret(os.environ.get("CLOUDFLARE_ACCOUNT_ID"))
    if not (cf_token and cf_account):
        return None
    return {
        "id": "cloudflare",
        "name": "Cloudflare Workers AI",
        "url": f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/v1/chat/completions",
        "headers": {
            "Authorization": f"Bearer {cf_token}",
            "Content-Type": "application/json",
        },
    }


def _openrouter_free_provider() -> Optional[Dict[str, Any]]:
    openrouter_key = clean_secret(os.environ.get("OPENROUTER_API_KEY"))
    if not openrouter_key:
        return None
    return {
        "id": "openrouter-free",
        "name": "OpenRouter Free Forge",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "headers": {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/erastudil/hydra",
            "X-Title": "Hydra Free Forge",
        },
    }


def _vercel_free_provider() -> Optional[Dict[str, Any]]:
    vercel_key = gateway_api_key()
    if not vercel_key:
        return None
    gateway_base = os.environ.get("AI_GATEWAY_API_BASE", "https://ai-gateway.vercel.sh/v1").rstrip("/")
    return {
        "id": "vercel-free",
        "name": "Vercel AI Gateway Free",
        "url": f"{gateway_base}/chat/completions",
        "headers": {
            "Authorization": f"Bearer {vercel_key}",
            "Content-Type": "application/json",
        },
    }


def get_free_candidates(model_override: Optional[str] = None) -> List[Tuple[Dict[str, Any], str]]:
    """Ordered (provider, model) pairs for Free Forge.

    Cloudflare Workers AI goes first when both Cloudflare variables are set. Vercel AI Gateway
    and OpenRouter follow. An @cf/ override stays on Cloudflare.
    """
    cloudflare = _cloudflare_provider()
    vercel = _vercel_free_provider()
    openrouter = _openrouter_free_provider()
    if not cloudflare and not vercel and not openrouter:
        raise CredentialsMissingError(
            "Free Forge requires either:\n"
            "  - CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID, or\n"
            "  - AI_GATEWAY_API_KEY, or\n"
            "  - OPENROUTER_API_KEY\n"
            "For a machine with no cloud key, use: hydra local \"<prompt>\""
        )

    candidates: List[Tuple[Dict[str, Any], str]] = []
    if model_override and model_override.startswith("@cf/"):
        if not cloudflare:
            raise CredentialsMissingError(
                f"{model_override} is a Cloudflare model. Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID."
            )
        return [(cloudflare, model_override)]

    if model_override:
        if vercel:
            candidates.append((vercel, model_override))
        if openrouter:
            candidates.append((openrouter, model_override))
        elif cloudflare:
            candidates.append((cloudflare, model_override))
        return candidates

    if cloudflare:
        candidates.append((cloudflare, DEFAULT_CLOUDFLARE_MODEL))
    if vercel:
        candidates.append((vercel, "alibaba/qwen3.8-27b"))
    if openrouter:
        seen = set()
        for model in [DEFAULT_FREE_MODEL] + list(FREE_MODELS):
            if model and model not in seen:
                seen.add(model)
                candidates.append((openrouter, model))
    return candidates


def get_free_provider() -> Tuple[Dict[str, Any], str]:
    """First Free Forge choice. Cloudflare wins when both of its variables are set."""
    return get_free_candidates()[0]


def attach_tool_capability(url: str, payload: Dict[str, Any]) -> None:
    """Keep OpenRouter on an endpoint that accepts the tools field.

    Existing provider routing keys stay in place.
    """
    if not payload.get("tools"):
        return
    host = (urlparse(url).hostname or url).lower()
    if "openrouter.ai" not in host:
        return
    route = payload.get("provider")
    if not isinstance(route, dict):
        payload["provider"] = {"require_parameters": True}
        return
    route.setdefault("require_parameters", True)





class TimestampSanitizer:
    """Detects and strips volatile timestamps from prompt text and prefix messages."""

    PATTERNS = [
        re.compile(r"timestamp=[\w\-:+.]+"),
        re.compile(r"\[\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]"),
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"),
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
        re.compile(r"\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]"),
        re.compile(r"\b\d{2}:\d{2}:\d{2}\b"),
    ]

    @classmethod
    def strip_text(cls, text: str, replacement: str = "") -> Tuple[str, List[str]]:
        """Strip volatile timestamps from a string and return cleaned text and extracted timestamps."""
        if not text:
            return "", []
        extracted = []
        cleaned = text
        for p in cls.PATTERNS:
            for m in p.finditer(cleaned):
                extracted.append(m.group(0))
            cleaned = p.sub(replacement, cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned).strip()
        return cleaned, extracted

    @classmethod
    def sanitize_messages(
        cls,
        messages: List[Dict[str, Any]],
        target_roles: Tuple[str, ...] = ("system", "developer"),
        replacement: str = "",
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Strip timestamps from messages with matching roles to preserve cache prefix invariants."""
        all_extracted: List[str] = []
        sanitized_messages: List[Dict[str, Any]] = []
        modified_count = 0

        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role in target_roles and isinstance(content, str):
                cleaned_text, extracted = cls.strip_text(content, replacement=replacement)
                if extracted:
                    modified_count += 1
                    all_extracted.extend(extracted)
                    msg_copy = dict(m)
                    msg_copy["content"] = cleaned_text
                    sanitized_messages.append(msg_copy)
                    continue
            sanitized_messages.append(dict(m))

        metrics = {
            "timestamps_stripped": len(all_extracted),
            "modified_messages": modified_count,
            "extracted": all_extracted,
        }
        return sanitized_messages, metrics


def strip_timestamps(text: str, replacement: str = "") -> Tuple[str, List[str]]:
    """Strip volatile timestamps from text using compiled temporal patterns."""
    return TimestampSanitizer.strip_text(text, replacement=replacement)


def sanitize_messages_timestamps(
    messages: List[Dict[str, Any]],
    target_roles: Tuple[str, ...] = ("system", "developer"),
    replacement: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Sanitize timestamps from messages to preserve prompt cache stability."""
    return TimestampSanitizer.sanitize_messages(messages, target_roles=target_roles, replacement=replacement)


class PrefixIsolationManager:
    """Manager for prompt prefix isolation, token cache boundary alignment, and prefix hashing."""

    def __init__(self, min_prefix_tokens: int = 64):
        self.min_prefix_tokens = min_prefix_tokens
        self._prefix_hits: Dict[str, int] = {}

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count based on whitespace and character heuristics."""
        if not text:
            return 0
        words = len(text.split())
        chars = len(text)
        return max(words, chars // 4)

    @staticmethod
    def compute_prefix_hash(prefix_messages: List[Dict[str, Any]]) -> str:
        """Compute deterministic SHA-256 hash of invariant prefix messages."""
        serialized = []
        for m in prefix_messages:
            role = str(m.get("role", ""))
            content = str(m.get("content", ""))
            serialized.append(f"{role}:{content}")
        raw = "\n---\n".join(serialized).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def isolate_prefix(
        self,
        messages: List[Dict[str, Any]],
        attach_cache_control: bool = False,
        strip_timestamps: bool = True,
    ) -> Dict[str, Any]:
        """
        Partition messages into static prefix and dynamic suffix turns.
        Returns isolated messages, prefix hash, token estimates, and cache metadata.
        """
        if not messages:
            return {
                "messages": [],
                "prefix_messages": [],
                "dynamic_messages": [],
                "prefix_hash": "",
                "prefix_tokens": 0,
                "is_isolated": False,
                "cache_hit_count": 0,
            }

        prefix_msgs: List[Dict[str, Any]] = []
        dynamic_msgs: List[Dict[str, Any]] = []

        collecting_prefix = True
        for m in messages:
            role = m.get("role", "")
            if collecting_prefix and role in ("system", "developer"):
                prefix_msgs.append(dict(m))
            else:
                collecting_prefix = False
                dynamic_msgs.append(dict(m))

        if not prefix_msgs and messages and messages[0].get("role") == "user":
            first_content = str(messages[0].get("content", ""))
            if len(messages) > 1 and self.estimate_tokens(first_content) >= self.min_prefix_tokens:
                prefix_msgs.append(dict(messages[0]))
                dynamic_msgs = [dict(m) for m in messages[1:]]

        if strip_timestamps and prefix_msgs:
            prefix_msgs, _ = TimestampSanitizer.sanitize_messages(prefix_msgs)

        prefix_text = " ".join(str(m.get("content", "")) for m in prefix_msgs)
        prefix_tokens = self.estimate_tokens(prefix_text)
        prefix_hash = self.compute_prefix_hash(prefix_msgs) if prefix_msgs else ""

        if prefix_hash:
            self._prefix_hits[prefix_hash] = self._prefix_hits.get(prefix_hash, 0) + 1

        isolated_messages = []
        for idx, m in enumerate(prefix_msgs):
            msg_copy = dict(m)
            if attach_cache_control and idx == len(prefix_msgs) - 1:
                msg_copy["cache_control"] = {"type": "ephemeral"}
            isolated_messages.append(msg_copy)

        isolated_messages.extend(dynamic_msgs)

        return {
            "messages": isolated_messages,
            "prefix_messages": prefix_msgs,
            "dynamic_messages": dynamic_msgs,
            "prefix_hash": prefix_hash,
            "prefix_tokens": prefix_tokens,
            "is_isolated": len(prefix_msgs) > 0,
            "cache_hit_count": self._prefix_hits.get(prefix_hash, 0) if prefix_hash else 0,
        }


_DEFAULT_PREFIX_MANAGER = PrefixIsolationManager()


def isolate_cache_prefix(
    messages: List[Dict[str, Any]],
    attach_cache_control: bool = False,
    min_prefix_tokens: int = 64,
    strip_timestamps: bool = True,
) -> Dict[str, Any]:
    """Isolate static prompt prefix from dynamic suffix turns for token cache efficiency."""
    manager = PrefixIsolationManager(min_prefix_tokens=min_prefix_tokens)
    return manager.isolate_prefix(
        messages,
        attach_cache_control=attach_cache_control,
        strip_timestamps=strip_timestamps,
    )


def attach_prefix_isolation(
    payload: Dict[str, Any],
    url: Optional[str] = None,
    enable_cache_control: bool = True,
) -> None:
    """
    Enforce prefix isolation on payload messages and attach ephemeral cache control for supported providers.
    Supports OpenRouter, Anthropic, and Vercel AI Gateway cache endpoints.
    """
    msgs = payload.get("messages")
    if not msgs or not isinstance(msgs, list):
        return

    should_cache_control = enable_cache_control
    if url:
        host = (urlparse(url).hostname or url).lower()
        if "openrouter.ai" not in host and "vercel" not in host and "anthropic" not in host:
            should_cache_control = False

    isolated = isolate_cache_prefix(msgs, attach_cache_control=should_cache_control)
    payload["messages"] = isolated["messages"]
    if isolated["prefix_hash"]:
        payload["_cache_prefix_hash"] = isolated["prefix_hash"]
        payload["_cache_prefix_tokens"] = isolated["prefix_tokens"]




class KvCacheFingerprinter:
    """Computes block-level and cumulative KV cache fingerprints for token caching efficiency."""

    def __init__(self, block_size: int = 32):
        self.block_size = block_size
        self._cache_index: Dict[str, Dict[str, Any]] = {}

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"\w+|[^\w\s]", text, re.UNICODE)

    def fingerprint_messages(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate deterministic block-level and cumulative KV fingerprints for messages."""
        if not messages:
            return {
                "root_fingerprint": "",
                "blocks_count": 0,
                "total_tokens": 0,
                "blocks": [],
            }

        tokens = []
        for m in messages:
            role = str(m.get("role", ""))
            content = str(m.get("content", ""))
            tokens.extend(["<|im_start|>", role, "\n"])
            tokens.extend(self._tokenize(content))
            tokens.extend(["\n", "<|im_end|>", "\n"])

        total_tokens = len(tokens)
        blocks = []
        cumulative_hash = hashlib.sha256(b"kv_init").hexdigest()

        for idx in range(0, total_tokens, self.block_size):
            chunk = tokens[idx : idx + self.block_size]
            chunk_raw = " ".join(chunk).encode("utf-8")
            block_hash = hashlib.sha256(chunk_raw).hexdigest()[:16]

            combined = f"{cumulative_hash}:{block_hash}".encode("utf-8")
            cumulative_hash = hashlib.sha256(combined).hexdigest()[:16]

            blocks.append({
                "block_index": len(blocks),
                "tokens_count": len(chunk),
                "block_hash": block_hash,
                "cumulative_fingerprint": cumulative_hash,
            })

        root_fingerprint = cumulative_hash if blocks else ""

        return {
            "root_fingerprint": root_fingerprint,
            "blocks_count": len(blocks),
            "total_tokens": total_tokens,
            "blocks": blocks,
        }

    def register(self, fingerprint_result: Dict[str, Any]) -> None:
        """Register block fingerprints into local cache index."""
        for b in fingerprint_result.get("blocks", []):
            cum_fp = b["cumulative_fingerprint"]
            self._cache_index[cum_fp] = {
                "block_index": b["block_index"],
                "tokens_count": b["tokens_count"],
            }

    def match_prefix(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Match longest common KV prefix against registered cache index."""
        fp_res = self.fingerprint_messages(messages)
        matched_blocks = 0
        matched_tokens = 0

        for b in fp_res["blocks"]:
            cum_fp = b["cumulative_fingerprint"]
            if cum_fp in self._cache_index:
                matched_blocks += 1
                matched_tokens += b["tokens_count"]
            else:
                break

        total = fp_res["total_tokens"]
        ratio = round((matched_tokens / total), 4) if total > 0 else 0.0

        return {
            "root_fingerprint": fp_res["root_fingerprint"],
            "total_blocks": fp_res["blocks_count"],
            "total_tokens": total,
            "matched_blocks": matched_blocks,
            "matched_tokens": matched_tokens,
            "hit_ratio": ratio,
            "is_full_hit": matched_tokens == total and total > 0,
        }


_DEFAULT_KV_FINGERPRINTER = KvCacheFingerprinter()


def get_default_kv_fingerprinter() -> KvCacheFingerprinter:
    """Return module-level default KV cache fingerprinter."""
    return _DEFAULT_KV_FINGERPRINTER


def fingerprint_kv_cache(messages: List[Dict[str, Any]], block_size: int = 32) -> Dict[str, Any]:
    """Generate block-level and root KV cache fingerprints for a message list."""
    fingerprinter = KvCacheFingerprinter(block_size=block_size)
    return fingerprinter.fingerprint_messages(messages)


def match_kv_prefix(messages: List[Dict[str, Any]], block_size: int = 32) -> Dict[str, Any]:
    """Match messages against module-level KV cache index and report matched prefix tokens."""
    fingerprinter = get_default_kv_fingerprinter()
    if block_size != fingerprinter.block_size:
        fingerprinter.block_size = block_size
    return fingerprinter.match_prefix(messages)


def adapt_model_for_url(url: str, model: str) -> str:
    """Translate provider namespaces. Unknown ids pass through unchanged."""
    parsed = urlparse(url)
    host = (parsed.hostname or url).lower()
    if host.endswith("vercel.sh") or ".vercel." in host:
        if model.startswith("x-ai/"):
            return "spacexai/" + model[len("x-ai/"):]
        if model.startswith("meta-llama/"):
            rewritten = "meta/" + model[len("meta-llama/"):]
            if rewritten.endswith("-instruct"):
                rewritten = rewritten[: -len("-instruct")]
            return rewritten
        if model.startswith("qwen/"):
            return "alibaba/" + model[len("qwen/"):]
    elif "openrouter.ai" in host:
        if model.startswith("spacexai/"):
            return "x-ai/" + model[len("spacexai/"):]
        if model.startswith("alibaba/"):
            return "qwen/" + model[len("alibaba/"):]
        if model.startswith("meta/") and not model.startswith("meta-llama/"):
            return "meta-llama/" + model[len("meta/"):]
        if model.startswith("glm-"):
            return "z-ai/" + model
        if model.startswith("deepseek-"):
            return "deepseek/" + model
    elif "cheaperinference.com" in host or "cheaperinference" in host:
        if "/" in model:
            return model.split("/", 1)[1]
    return model


_LONG_EFFORTS = {"high", "xhigh", "max"}


def completion_timeout(reasoning: Optional[Dict[str, str]], explicit: Optional[int] = None) -> int:
    """Idle wait in seconds. High effort and pro mode get a longer quiet period."""
    if explicit is not None:
        return explicit
    if not reasoning:
        return 180
    effort = str(reasoning.get("effort") or "").lower()
    if reasoning.get("mode") or effort in _LONG_EFFORTS:
        return 600
    return 180


def ensure_temperature(model: str, temperature: Optional[float]) -> None:
    """Refuse a temperature the catalog says this model will reject."""
    if temperature is not None and model_rejects_temperature(model):
        raise UsageError(f"{model} rejects temperature. Omit --temperature.")


def reasoning_fields(effort: Optional[str] = None, reasoning_mode: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Build the gateway reasoning object. Empty means the field stays off the request."""
    reasoning: Dict[str, str] = {}
    if effort:
        reasoning["effort"] = effort
    if reasoning_mode:
        reasoning["mode"] = reasoning_mode
    return reasoning or None


def _build_payload(
    model: str,
    messages: List[Dict[str, str]],
    stream: bool,
    temperature: Optional[float],
    max_tokens: Optional[int],
    reasoning: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if reasoning:
        payload["reasoning"] = reasoning
    return payload


def _error_message(body: str, fallback: str) -> str:
    try:
        parsed = json.loads(body)
        return parsed.get("error", {}).get("message", body) or fallback
    except Exception:
        return body or fallback


def _raise_if_provider_error(chunk: Dict[str, Any]) -> None:
    error = chunk.get("error")
    if not error:
        return
    if isinstance(error, dict):
        message = error.get("message") or json.dumps(error)
    else:
        message = str(error)
    raise ProviderError(redact(message))


def _http_error(url: str, exc: "urllib.error.HTTPError") -> ProviderError:
    error_body = ""
    try:
        error_body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        error_body = ""
    detail = _error_message(error_body, str(exc))
    return ProviderError(redact(f"HTTP {exc.code} error from {describe_endpoint(url)}: {detail}"))


def _delta_text(delta: Any) -> str:
    """Extract visible assistant text from an OpenAI-style streaming delta."""
    if isinstance(delta, str):
        return delta
    if not isinstance(delta, dict):
        return ""
    for key in ("content", "text"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for part in value:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    piece = part.get("text") or part.get("content") or ""
                    if isinstance(piece, str) and piece:
                        parts.append(piece)
            if parts:
                return "".join(parts)
    return ""


class SSEParser:
    """Incremental SSE parser. Feed arbitrary byte chunks; emit complete events."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> List[Dict[str, str]]:
        if not chunk:
            return []
        self._buf.extend(chunk)
        events: List[Dict[str, str]] = []
        while True:
            sep = self._buf.find(b"\n\n")
            if sep < 0:
                # Some gateways use CRLF event framing.
                sep = self._buf.find(b"\r\n\r\n")
                if sep < 0:
                    break
                raw = bytes(self._buf[:sep])
                del self._buf[: sep + 4]
            else:
                raw = bytes(self._buf[:sep])
                del self._buf[: sep + 2]
            parsed = self._parse_event(raw)
            if parsed is not None:
                events.append(parsed)
        return events

    def flush(self) -> List[Dict[str, str]]:
        """Parse any trailing unterminated event when the socket closes."""
        if not self._buf.strip():
            self._buf.clear()
            return []
        raw = bytes(self._buf)
        self._buf.clear()
        parsed = self._parse_event(raw)
        return [parsed] if parsed is not None else []

    @staticmethod
    def _parse_event(raw: bytes) -> Optional[Dict[str, str]]:
        data_lines: List[bytes] = []
        event_type = "message"
        for line in raw.split(b"\n"):
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line or line.startswith(b":"):
                continue
            if b":" not in line:
                continue
            field, _, value = line.partition(b":")
            if value.startswith(b" "):
                value = value[1:]
            if field == b"data":
                data_lines.append(value)
            elif field == b"event":
                event_type = value.decode("utf-8", errors="replace") or "message"
        if not data_lines:
            return None
        return {
            "type": event_type,
            "data": b"\n".join(data_lines).decode("utf-8", errors="replace"),
        }


def _iter_sse_payloads(resp: Any) -> Generator[str, None, None]:
    """Yield `data:` payload strings from an HTTP response body.

    Prefers byte reads (correct across TCP chunk boundaries). Falls back to
    line iteration for simple mocks and exotic transports.
    """
    parser = SSEParser()
    read = getattr(resp, "read", None)
    if callable(read):
        first = read(4096)
        if isinstance(first, (bytes, bytearray)):
            chunk: Optional[bytes] = bytes(first)
            while chunk:
                for event in parser.feed(chunk):
                    yield event["data"]
                nxt = read(4096)
                if not isinstance(nxt, (bytes, bytearray)) or not nxt:
                    break
                chunk = bytes(nxt)
            for event in parser.flush():
                yield event["data"]
            return

    for raw_line in resp:
        if isinstance(raw_line, str):
            raw_line = raw_line.encode("utf-8", errors="replace")
        elif not isinstance(raw_line, (bytes, bytearray)):
            continue
        piece = bytes(raw_line)
        if not piece.endswith(b"\n"):
            piece += b"\n"
        for event in parser.feed(piece):
            yield event["data"]
    for event in parser.flush():
        yield event["data"]


def stream_chat_completion(
    url: str,
    headers: Dict[str, str],
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    reasoning: Optional[Dict[str, str]] = None,
    max_retries: Optional[int] = None,
    backoff_base: Optional[float] = None,
) -> Generator[str, None, None]:
    """Send a streaming chat completion with health tracking and 429 exponential backoff."""
    ensure_temperature(model, temperature)
    effective_model = adapt_model_for_url(url, model)
    payload = _build_payload(effective_model, messages, True, temperature, max_tokens, reasoning)
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data_bytes, method="POST")
    wait = completion_timeout(reasoning, timeout)
    where = describe_endpoint(url)

    retries = int(os.environ.get("HYDRA_MAX_RETRIES", "3")) if max_retries is None else max_retries
    base_wait = float(os.environ.get("HYDRA_BACKOFF_BASE", "0.5")) if backoff_base is None else backoff_base

    for attempt in range(retries + 1):
        saw_token = False
        try:
            with urllib.request.urlopen(req, timeout=wait) as resp:
                saw_done = False
                for data in _iter_sse_payloads(resp):
                    payload_text = data.strip()
                    if not payload_text:
                        continue
                    if payload_text == "[DONE]":
                        saw_done = True
                        break
                    try:
                        chunk = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue
                    _raise_if_provider_error(chunk)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    delta = choice.get("delta")
                    if delta is None and "text" in choice:
                        delta = {"text": choice.get("text")}
                    content = _delta_text(delta or {})
                    if content:
                        saw_token = True
                        yield content
                # Providers sometimes close after the last token without [DONE].
                if not saw_done and not saw_token:
                    raise ProviderError(f"Stream from {where} ended before data: [DONE]")
                record_provider_success(url)
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries and not saw_token:
                record_provider_failure(url, exc, is_429=True)
                retry_after_str = exc.headers.get("Retry-After") if exc.headers else None
                backoff = None
                if retry_after_str:
                    try:
                        backoff = min(float(retry_after_str), 10.0)
                    except ValueError:
                        pass
                if backoff is None:
                    backoff = min(8.0, base_wait * (2 ** attempt)) + random.uniform(0.05, 0.15)
                time.sleep(backoff)
                continue
            record_provider_failure(url, exc, is_429=(exc.code == 429))
            raise _http_error(url, exc) from exc
        except ProviderError as exc:
            record_provider_failure(url, exc)
            raise
        except urllib.error.URLError as exc:
            record_provider_failure(url, exc)
            raise ProviderError(redact(f"Connection failed to {where}: {exc.reason}")) from exc
        except Exception as exc:
            record_provider_failure(url, exc)
            raise ProviderError(redact(f"Streaming error from {where}: {exc}")) from exc


def fetch_chat_completion(
    url: str,
    headers: Dict[str, str],
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    reasoning: Optional[Dict[str, str]] = None,
    return_meta: bool = False,
    max_retries: Optional[int] = None,
    backoff_base: Optional[float] = None,
) -> Any:
    """Fetch one complete chat completion with health tracking and 429 exponential backoff.

    Returns the assistant text. With return_meta=True returns a dict with the text,
    the model id the provider reports, the upstream provider if given, and usage.
    """
    ensure_temperature(model, temperature)
    effective_model = adapt_model_for_url(url, model)
    payload = _build_payload(effective_model, messages, False, temperature, max_tokens, reasoning)
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data_bytes, method="POST")
    wait = completion_timeout(reasoning, timeout)
    where = describe_endpoint(url)

    retries = int(os.environ.get("HYDRA_MAX_RETRIES", "3")) if max_retries is None else max_retries
    base_wait = float(os.environ.get("HYDRA_BACKOFF_BASE", "0.5")) if backoff_base is None else backoff_base

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=wait) as resp:
                raw_body = resp.read().decode("utf-8", errors="replace")
                res_json = json.loads(raw_body)
                _raise_if_provider_error(res_json)
                choices = res_json.get("choices") or []
                if not choices:
                    raise ProviderError(f"Empty completion from {where}")
                content = choices[0].get("message", {}).get("content")
                if not isinstance(content, str) or not content:
                    if choices[0].get("finish_reason") == "length":
                        raise ProviderError(
                            f"Empty completion from {where}: the token limit ran out before any answer text "
                            "(finish_reason=length). Raise --max-tokens."
                        )
                    raise ProviderError(f"Empty completion from {where}")
                record_provider_success(url)
                if return_meta:
                    upstream = res_json.get("provider")
                    return {
                        "content": content,
                        "model": res_json.get("model") or effective_model,
                        "upstream": upstream if isinstance(upstream, str) else None,
                        "usage": res_json.get("usage") if isinstance(res_json.get("usage"), dict) else None,
                    }
                return content
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries:
                record_provider_failure(url, exc, is_429=True)
                retry_after_str = exc.headers.get("Retry-After") if exc.headers else None
                backoff = None
                if retry_after_str:
                    try:
                        backoff = min(float(retry_after_str), 10.0)
                    except ValueError:
                        pass
                if backoff is None:
                    backoff = min(8.0, base_wait * (2 ** attempt)) + random.uniform(0.05, 0.15)
                time.sleep(backoff)
                continue
            record_provider_failure(url, exc, is_429=(exc.code == 429))
            raise _http_error(url, exc) from exc
        except ProviderError as exc:
            record_provider_failure(url, exc)
            raise
        except urllib.error.URLError as exc:
            record_provider_failure(url, exc)
            raise ProviderError(redact(f"Connection failed to {where}: {exc.reason}")) from exc
        except Exception as exc:
            record_provider_failure(url, exc)
            raise ProviderError(redact(f"Request to {where} failed: {exc}")) from exc


# ===========================================================================
# Dynamic Fallback Routing Cascade (WO-05)
# ===========================================================================

def fallback_cascade(
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    frontier_error: Optional[Exception] = None,
    on_tier_transition: Optional[Callable[[str, str], None]] = None,
) -> str:
    """Execute universal fallback cascade across inference tiers (WO-05):
    Frontier -> Cloudflare Workers AI -> OpenRouter Free Forge -> Local/Ollama.
    """
    sys_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]
    tier_errors: List[str] = []
    if frontier_error:
        tier_errors.append(f"Frontier: {frontier_error}")

    # Tier 2: Cloudflare Workers AI
    cf = _cloudflare_provider()
    if cf:
        if on_tier_transition:
            on_tier_transition("cloudflare", DEFAULT_CLOUDFLARE_MODEL)
        try:
            return fetch_chat_completion(
                url=cf["url"],
                headers=cf["headers"],
                model=DEFAULT_CLOUDFLARE_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            tier_errors.append(f"Cloudflare ({DEFAULT_CLOUDFLARE_MODEL}): {exc}")

    # Tier 3: OpenRouter Free Forge
    or_free = _openrouter_free_provider()
    if or_free:
        models = [DEFAULT_FREE_MODEL] + [m for m in FREE_MODELS if m != DEFAULT_FREE_MODEL]
        for m in models:
            if on_tier_transition:
                on_tier_transition("openrouter-free", m)
            try:
                return fetch_chat_completion(
                    url=or_free["url"],
                    headers=or_free["headers"],
                    model=m,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                tier_errors.append(f"OpenRouter ({m}): {exc}")
                continue

    # Tier 4: Local / Ollama
    try:
        local_url, local_name = detect_local_endpoint()
        if on_tier_transition:
            on_tier_transition(local_name, DEFAULT_LOCAL_MODEL)
        return fetch_chat_completion(
            url=local_url,
            headers={"Content-Type": "application/json"},
            model=DEFAULT_LOCAL_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        tier_errors.append(f"Local ({DEFAULT_LOCAL_MODEL}): {exc}")

    summary = "; ".join(tier_errors)
    raise ProviderError(
        redact(f"All fallback cascade tiers failed (Frontier -> Cloudflare -> OpenRouter -> Local). Errors: {summary}")
    )


def complete(
    alias: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    fallback_cascade_enabled: bool = True,
) -> str:
    """Summon one alias and return the assistant text. Tries each configured provider.
    On persistent provider failures, automatically cascades across tiers:
    Frontier -> Cloudflare -> OpenRouter -> Local (WO-05).
    """
    load_dotenv()
    route = resolve_route(alias)
    messages = [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    reasoning = reasoning_fields(route.get("effort"), route.get("reasoning_mode"))
    ensure_temperature(route["model"], temperature)

    cascade_active = fallback_cascade_enabled and os.environ.get("HYDRA_NO_FALLBACK", "").strip() != "1"

    providers = []
    try:
        providers = providers_for_model(route["model"])
    except CredentialsMissingError as cme:
        if not cascade_active:
            raise
        return fallback_cascade(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            frontier_error=cme,
        )

    if not providers:
        if not cascade_active:
            raise CredentialsMissingError(
                "No frontier credentials found. Export OPENROUTER_API_KEY, AI_GATEWAY_API_KEY, or CHEAPERINFERENCE_API_KEY."
            )
        return fallback_cascade(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            frontier_error=ProviderError("No frontier credentials configured"),
        )

    last_error: Optional[Exception] = None
    for provider in providers:
        try:
            return fetch_chat_completion(
                url=provider["url"],
                headers=provider["headers"],
                model=route["model"],
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning=reasoning,
            )
        except UsageError:
            raise
        except Exception as exc:
            last_error = exc
            continue

    if cascade_active:
        return fallback_cascade(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            frontier_error=last_error,
        )

    raise ProviderError(redact(f"All configured providers failed for '{route['model']}'. Last error: {last_error}"))
