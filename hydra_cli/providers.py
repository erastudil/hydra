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

        template_id = None
        template_hash = None
        preamble_frozen = False
        preamble_hash = None
        if messages and isinstance(messages[0], dict):
            template_id = messages[0].get("_template_id")
            template_hash = messages[0].get("_template_hash")
            preamble_frozen = bool(messages[0].get("_preamble_frozen") or messages[0].get("_frozen"))
            preamble_hash = messages[0].get("_preamble_hash")

        return {
            "messages": isolated_messages,
            "prefix_messages": prefix_msgs,
            "dynamic_messages": dynamic_msgs,
            "prefix_hash": prefix_hash,
            "prefix_tokens": prefix_tokens,
            "is_isolated": len(prefix_msgs) > 0,
            "cache_hit_count": self._prefix_hits.get(prefix_hash, 0) if prefix_hash else 0,
            "template_id": template_id,
            "template_hash": template_hash,
            "preamble_frozen": preamble_frozen,
            "preamble_hash": preamble_hash,
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
        payload["_cache_expiration"] = record_cache_expiration(isolated["prefix_hash"], provider=url or "generic")
    if isolated.get("template_hash"):
        payload["_template_hash"] = isolated["template_hash"]
    if isolated.get("template_id"):
        payload["_template_id"] = isolated["template_id"]
    if isolated.get("preamble_hash"):
        payload["_preamble_hash"] = isolated["preamble_hash"]
        payload["_preamble_frozen"] = True
    payload["_cache_boundary"] = evaluate_cache_boundary(isolated["prefix_tokens"], provider_or_url=url)




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




class CacheHitTelemetry:
    """Tracks token cache hit metrics, savings ratios, and per-provider telemetry."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset all tracked cache hit counters and provider records."""
        self.total_requests: int = 0
        self.cache_hit_requests: int = 0
        self.total_prompt_tokens: int = 0
        self.total_cached_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.by_provider: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def extract_cache_tokens(usage: Dict[str, Any]) -> Tuple[int, int]:
        """Extract cached prompt tokens and total prompt tokens across provider formats."""
        if not isinstance(usage, dict):
            return 0, 0
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        cached_tokens = 0

        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached_tokens = int(details.get("cached_tokens") or 0)

        if not cached_tokens and "prompt_cache_hit_tokens" in usage:
            cached_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)

        if not cached_tokens and "cache_read_input_tokens" in usage:
            cached_tokens = int(usage.get("cache_read_input_tokens") or 0)

        if not prompt_tokens and cached_tokens:
            prompt_tokens = cached_tokens

        return cached_tokens, prompt_tokens

    def record_usage(self, provider: str, usage: Dict[str, Any]) -> Dict[str, Any]:
        """Record token usage metrics and update cache hit statistics."""
        cached_tokens, prompt_tokens = self.extract_cache_tokens(usage)
        completion_tokens = int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0

        self.total_requests += 1
        if cached_tokens > 0:
            self.cache_hit_requests += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_cached_tokens += cached_tokens
        self.total_completion_tokens += completion_tokens

        prov_key = provider.lower()
        if prov_key not in self.by_provider:
            self.by_provider[prov_key] = {
                "requests": 0,
                "hit_requests": 0,
                "prompt_tokens": 0,
                "cached_tokens": 0,
                "completion_tokens": 0,
            }

        rec = self.by_provider[prov_key]
        rec["requests"] += 1
        if cached_tokens > 0:
            rec["hit_requests"] += 1
        rec["prompt_tokens"] += prompt_tokens
        rec["cached_tokens"] += cached_tokens
        rec["completion_tokens"] += completion_tokens

        hit_ratio = round(cached_tokens / prompt_tokens, 4) if prompt_tokens > 0 else 0.0

        return {
            "cached_tokens": cached_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "hit_ratio": hit_ratio,
            "is_cache_hit": cached_tokens > 0,
        }

    def get_summary(self) -> Dict[str, Any]:
        """Return aggregated cache hit telemetry summary."""
        hit_ratio = (
            round(self.total_cached_tokens / self.total_prompt_tokens, 4)
            if self.total_prompt_tokens > 0
            else 0.0
        )
        request_hit_ratio = (
            round(self.cache_hit_requests / self.total_requests, 4)
            if self.total_requests > 0
            else 0.0
        )
        return {
            "total_requests": self.total_requests,
            "cache_hit_requests": self.cache_hit_requests,
            "request_hit_ratio": request_hit_ratio,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "total_uncached_tokens": max(0, self.total_prompt_tokens - self.total_cached_tokens),
            "total_completion_tokens": self.total_completion_tokens,
            "overall_hit_ratio": hit_ratio,
            "providers_count": len(self.by_provider),
            "by_provider": dict(self.by_provider),
        }


_DEFAULT_CACHE_HIT_TELEMETRY = CacheHitTelemetry()


def get_default_cache_hit_telemetry() -> CacheHitTelemetry:
    """Return default singleton cache hit telemetry instance."""
    return _DEFAULT_CACHE_HIT_TELEMETRY


def record_cache_hit_telemetry(provider: str, usage: Dict[str, Any]) -> Dict[str, Any]:
    """Record token usage and cache hit metrics into global telemetry."""
    return _DEFAULT_CACHE_HIT_TELEMETRY.record_usage(provider, usage)


def get_cache_hit_telemetry_summary() -> Dict[str, Any]:
    """Return summary dictionary of global cache hit metrics."""
    return _DEFAULT_CACHE_HIT_TELEMETRY.get_summary()


def reset_cache_hit_telemetry() -> None:
    """Reset global cache hit telemetry metrics."""
    _DEFAULT_CACHE_HIT_TELEMETRY.reset()


def extract_cache_tokens_from_usage(usage: Dict[str, Any]) -> Tuple[int, int]:
    """Extract cached prompt tokens and total prompt tokens across provider formats."""
    return CacheHitTelemetry.extract_cache_tokens(usage)


class TemplateHasher:
    """Track deterministic template hashes, skeletons, and slot bindings for prompt caching."""

    SLOT_PATTERN = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}|\{([a-zA-Z0-9_]+)\}|<([a-zA-Z0-9_]+)>|\[([a-zA-Z0-9_]+)\]")

    def __init__(self) -> None:
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._skeleton_index: Dict[str, str] = {}

    def extract_slots(self, template: str) -> List[str]:
        """Extract variable slot identifiers from template string in occurrence order."""
        if not template:
            return []
        slots: List[str] = []
        for match in self.SLOT_PATTERN.finditer(template):
            slot = match.group(1) or match.group(2) or match.group(3) or match.group(4)
            if slot and slot not in slots:
                slots.append(slot)
        return slots

    def get_skeleton(self, template: str) -> str:
        """Generate canonical skeleton string with normalized slot placeholders."""
        if not template:
            return ""
        normalized = self.SLOT_PATTERN.sub("{{_SLOT_}}", template)
        return re.sub(r"\s+", " ", normalized).strip()

    def compute_hash(self, template: str, length: int = 16) -> str:
        """Compute deterministic hex digest of template content."""
        if not template:
            return ""
        digest = hashlib.sha256(template.strip().encode("utf-8")).hexdigest()
        return digest[:length] if length > 0 else digest

    def compute_skeleton_hash(self, template: str, length: int = 16) -> str:
        """Compute structural hash of template skeleton invariant to slot identifier names."""
        skeleton = self.get_skeleton(template)
        if not skeleton:
            return ""
        digest = hashlib.sha256(skeleton.encode("utf-8")).hexdigest()
        return digest[:length] if length > 0 else digest

    def register_template(self, template_id: str, template: str) -> Dict[str, Any]:
        """Register named template in template catalog with structural metadata."""
        template_hash = self.compute_hash(template)
        skeleton_hash = self.compute_skeleton_hash(template)
        slots = self.extract_slots(template)
        record = {
            "template_id": template_id,
            "template": template,
            "template_hash": template_hash,
            "skeleton_hash": skeleton_hash,
            "slots": slots,
            "slot_count": len(slots),
            "length": len(template),
        }
        self._registry[template_id] = record
        self._skeleton_index[skeleton_hash] = template_id
        return record

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve registered template entry by identifier."""
        return self._registry.get(template_id)

    def find_by_skeleton_hash(self, skeleton_hash: str) -> Optional[Dict[str, Any]]:
        """Find registered template matching given skeleton hash."""
        tid = self._skeleton_index.get(skeleton_hash)
        return self._registry.get(tid) if tid else None

    def render(self, template: str, params: Dict[str, Any]) -> str:
        """Render template replacing slot placeholders with provided parameter values."""
        if not template:
            return ""

        def _replacer(m: re.Match) -> str:
            key = m.group(1) or m.group(2) or m.group(3) or m.group(4)
            if key in params:
                return str(params[key])
            return m.group(0)

        return self.SLOT_PATTERN.sub(_replacer, template)

    def render_and_hash(self, template: str, params: Dict[str, Any]) -> Tuple[str, str]:
        """Render template and compute associated template hash."""
        rendered = self.render(template, params)
        template_hash = self.compute_hash(template)
        return rendered, template_hash

    def attach_metadata(
        self,
        messages: List[Dict[str, Any]],
        template_id: str,
        template: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Annotate messages list with template identifier and template hash metadata."""
        if not messages:
            return []
        tmpl = template
        if not tmpl and template_id in self._registry:
            tmpl = self._registry[template_id]["template"]
        tmpl_hash = self.compute_hash(tmpl) if tmpl else ""

        enriched: List[Dict[str, Any]] = []
        for idx, msg in enumerate(messages):
            new_msg = dict(msg)
            if idx == 0:
                new_msg["_template_id"] = template_id
                if tmpl_hash:
                    new_msg["_template_hash"] = tmpl_hash
            enriched.append(new_msg)
        return enriched


_DEFAULT_TEMPLATE_HASHER = TemplateHasher()


def get_default_template_hasher() -> TemplateHasher:
    """Return default singleton template hasher instance."""
    return _DEFAULT_TEMPLATE_HASHER


def compute_template_hash(template: str, length: int = 16) -> str:
    """Compute deterministic hex digest of template content."""
    return _DEFAULT_TEMPLATE_HASHER.compute_hash(template, length=length)


def compute_skeleton_hash(template: str, length: int = 16) -> str:
    """Compute structural hash of template skeleton invariant to slot identifier names."""
    return _DEFAULT_TEMPLATE_HASHER.compute_skeleton_hash(template, length=length)


def extract_template_slots(template: str) -> List[str]:
    """Extract variable slot identifiers from template string."""
    return _DEFAULT_TEMPLATE_HASHER.extract_slots(template)


def render_template_with_hash(template: str, params: Dict[str, Any]) -> Tuple[str, str]:
    """Render template replacing slot placeholders and return rendered text with template hash."""
    return _DEFAULT_TEMPLATE_HASHER.render_and_hash(template, params)


def register_cache_template(template_id: str, template: str) -> Dict[str, Any]:
    """Register named template in global template registry."""
    return _DEFAULT_TEMPLATE_HASHER.register_template(template_id, template)


def get_cache_template(template_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve registered template entry from global template registry."""
    return _DEFAULT_TEMPLATE_HASHER.get_template(template_id)


def attach_template_metadata(
    messages: List[Dict[str, Any]],
    template_id: str,
    template: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Annotate messages list with template identifier and template hash metadata."""
    return _DEFAULT_TEMPLATE_HASHER.attach_metadata(messages, template_id, template=template)


class CacheBoundaryAligner:
    """Align token boundaries and evaluate caching eligibility across model providers."""

    DEFAULT_PROFILES: Dict[str, Dict[str, int]] = {
        "anthropic": {"min_tokens": 1024, "block_size": 64},
        "openai": {"min_tokens": 1024, "block_size": 128},
        "deepseek": {"min_tokens": 64, "block_size": 64},
        "openrouter": {"min_tokens": 1024, "block_size": 64},
        "generic": {"min_tokens": 64, "block_size": 32},
    }

    def __init__(self, custom_profiles: Optional[Dict[str, Dict[str, int]]] = None) -> None:
        self.profiles = dict(self.DEFAULT_PROFILES)
        if custom_profiles:
            self.profiles.update(custom_profiles)

    def get_profile(self, provider_or_url: Optional[str] = None) -> Dict[str, int]:
        """Resolve provider caching profile from provider name or endpoint url."""
        if not provider_or_url:
            return dict(self.profiles["generic"])

        target = provider_or_url.lower()
        if "://" in target:
            target = (urlparse(target).hostname or target).lower()

        for key in ("anthropic", "openai", "deepseek", "openrouter"):
            if key in target:
                return dict(self.profiles[key])

        return dict(self.profiles["generic"])

    def align_token_count(self, token_count: int, block_size: int = 32, mode: str = "floor") -> int:
        """Align token count to nearest multiple of block size using specified rounding mode."""
        if token_count <= 0 or block_size <= 0:
            return 0
        if mode == "ceil":
            return ((token_count + block_size - 1) // block_size) * block_size
        if mode == "nearest":
            return int(round(token_count / block_size)) * block_size
        return (token_count // block_size) * block_size

    def evaluate_boundary(
        self,
        prefix_tokens: int,
        provider_or_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate cache boundary alignment and eligibility for given token count."""
        prof = self.get_profile(provider_or_url)
        min_tokens = prof["min_tokens"]
        block_size = prof["block_size"]

        provider_name = "generic"
        if provider_or_url:
            raw = provider_or_url.lower()
            for key in ("anthropic", "openai", "deepseek", "openrouter"):
                if key in raw:
                    provider_name = key
                    break

        aligned = self.align_token_count(prefix_tokens, block_size=block_size, mode="floor")
        remainder = prefix_tokens % block_size if block_size > 0 else 0
        padding = (block_size - remainder) % block_size if block_size > 0 else 0
        is_eligible = prefix_tokens >= min_tokens

        return {
            "provider": provider_name,
            "prefix_tokens": prefix_tokens,
            "min_tokens": min_tokens,
            "block_size": block_size,
            "is_eligible": is_eligible,
            "aligned_tokens": aligned,
            "remainder_tokens": remainder,
            "padding_needed": padding,
            "wasted_tokens": remainder,
            "blocks_count": aligned // block_size if block_size > 0 else 0,
        }

    def align_messages_boundary(
        self,
        messages: List[Dict[str, Any]],
        provider_or_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find optimal message boundary index maximizing cached blocks for provider."""
        if not messages:
            return {
                "optimal_index": -1,
                "prefix_tokens": 0,
                "aligned_tokens": 0,
                "is_eligible": False,
                "messages": [],
            }

        prof = self.get_profile(provider_or_url)
        min_tokens = prof["min_tokens"]

        cum_tokens = 0
        turn_counts = []
        for m in messages:
            content = str(m.get("content", ""))
            est = max(1, len(content) // 4)
            cum_tokens += est
            turn_counts.append(cum_tokens)

        prefix_indices = [
            idx for idx, m in enumerate(messages)
            if m.get("role") in ("system", "developer")
        ]
        if not prefix_indices:
            prefix_indices = [0]

        best_idx = prefix_indices[0]
        for idx in prefix_indices:
            if turn_counts[idx] >= min_tokens:
                best_idx = idx

        eval_res = self.evaluate_boundary(turn_counts[best_idx], provider_or_url=provider_or_url)

        output_msgs = []
        for idx, m in enumerate(messages):
            copy_m = dict(m)
            if idx == best_idx and eval_res["is_eligible"]:
                copy_m["cache_control"] = {"type": "ephemeral"}
            output_msgs.append(copy_m)

        return {
            "optimal_index": best_idx,
            "prefix_tokens": turn_counts[best_idx],
            "aligned_tokens": eval_res["aligned_tokens"],
            "is_eligible": eval_res["is_eligible"],
            "boundary_info": eval_res,
            "messages": output_msgs,
        }


_DEFAULT_CACHE_BOUNDARY_ALIGNER = CacheBoundaryAligner()


def get_default_cache_boundary_aligner() -> CacheBoundaryAligner:
    """Return default singleton cache boundary aligner instance."""
    return _DEFAULT_CACHE_BOUNDARY_ALIGNER


def align_cache_tokens(token_count: int, block_size: int = 64, mode: str = "floor") -> int:
    """Align token count to block boundary using specified rounding mode."""
    return _DEFAULT_CACHE_BOUNDARY_ALIGNER.align_token_count(token_count, block_size=block_size, mode=mode)


def evaluate_cache_boundary(prefix_tokens: int, provider_or_url: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate cache boundary alignment metrics for given prefix token count."""
    return _DEFAULT_CACHE_BOUNDARY_ALIGNER.evaluate_boundary(prefix_tokens, provider_or_url=provider_or_url)


def get_cache_boundary_profile(provider_or_url: Optional[str] = None) -> Dict[str, int]:
    """Retrieve boundary and caching profile for provider or endpoint url."""
    return _DEFAULT_CACHE_BOUNDARY_ALIGNER.get_profile(provider_or_url)


def align_cache_messages_boundary(
    messages: List[Dict[str, Any]],
    provider_or_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Determine optimal message boundary index for prompt cache placement."""
    return _DEFAULT_CACHE_BOUNDARY_ALIGNER.align_messages_boundary(messages, provider_or_url=provider_or_url)


class PreambleFreezer:
    """Enforce immutable preamble blocks and verify prompt cache freeze invariants."""

    def __init__(self) -> None:
        self._frozen_catalog: Dict[str, Dict[str, Any]] = {}
        self._hit_counters: Dict[str, int] = {}

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4) if text else 0

    def freeze_preamble(
        self,
        messages: List[Dict[str, Any]],
        lock_id: Optional[str] = None,
        attach_cache_control: bool = True,
    ) -> Dict[str, Any]:
        """Extract static preamble messages and freeze into immutable byte-stable block."""
        if not messages:
            return {
                "frozen": False,
                "preamble_hash": "",
                "preamble_tokens": 0,
                "lock_id": "",
                "frozen_messages": [],
                "dynamic_messages": [],
                "all_messages": [],
            }

        preamble_msgs: List[Dict[str, Any]] = []
        dynamic_msgs: List[Dict[str, Any]] = []

        collecting = True
        for m in messages:
            role = str(m.get("role", ""))
            if collecting and role in ("system", "developer"):
                preamble_msgs.append(dict(m))
            else:
                collecting = False
                dynamic_msgs.append(dict(m))

        if not preamble_msgs:
            return {
                "frozen": False,
                "preamble_hash": "",
                "preamble_tokens": 0,
                "lock_id": "",
                "frozen_messages": [],
                "dynamic_messages": [dict(m) for m in messages],
                "all_messages": [dict(m) for m in messages],
            }

        serialized = []
        for m in preamble_msgs:
            role = str(m.get("role", ""))
            content = str(m.get("content", ""))
            serialized.append(f"{role}:{content}")

        raw_block = "\n---\n".join(serialized).encode("utf-8")
        preamble_hash = hashlib.sha256(raw_block).hexdigest()[:16]
        assigned_lock = lock_id or f"lock_{preamble_hash[:8]}"

        self._hit_counters[preamble_hash] = self._hit_counters.get(preamble_hash, 0) + 1

        frozen_output = []
        for idx, m in enumerate(preamble_msgs):
            item = dict(m)
            item["_frozen"] = True
            item["_preamble_frozen"] = True
            item["_preamble_hash"] = preamble_hash
            item["_lock_id"] = assigned_lock
            if attach_cache_control and idx == len(preamble_msgs) - 1:
                item["cache_control"] = {"type": "ephemeral"}
            frozen_output.append(item)

        total_tokens = sum(self._estimate_tokens(str(m.get("content", ""))) for m in preamble_msgs)

        all_msgs = list(frozen_output) + [dict(m) for m in dynamic_msgs]

        return {
            "frozen": True,
            "preamble_hash": preamble_hash,
            "preamble_tokens": total_tokens,
            "lock_id": assigned_lock,
            "frozen_messages": frozen_output,
            "dynamic_messages": dynamic_msgs,
            "all_messages": all_msgs,
            "freeze_hit_count": self._hit_counters[preamble_hash],
        }

    def is_preamble_frozen(self, messages: List[Dict[str, Any]]) -> bool:
        """Check whether leading message marks frozen preamble block."""
        if not messages or not isinstance(messages[0], dict):
            return False
        return bool(messages[0].get("_preamble_frozen") or messages[0].get("_frozen"))

    def verify_freeze_integrity(self, messages: List[Dict[str, Any]], expected_hash: str) -> bool:
        """Verify cryptographic digest integrity of frozen preamble against expected digest."""
        if not messages:
            return False
        frozen_res = self.freeze_preamble(messages, attach_cache_control=False)
        return frozen_res["preamble_hash"] == expected_hash

    def register_frozen_preamble(self, name: str, content: str, role: str = "system") -> Dict[str, Any]:
        """Register static preamble content in frozen catalog."""
        msg = [{"role": role, "content": content}]
        res = self.freeze_preamble(msg, lock_id=f"cat_{name}")
        record = {
            "name": name,
            "content": content,
            "role": role,
            "preamble_hash": res["preamble_hash"],
            "preamble_tokens": res["preamble_tokens"],
            "lock_id": res["lock_id"],
        }
        self._frozen_catalog[name] = record
        return record

    def get_frozen_preamble(self, name: str) -> Optional[Dict[str, Any]]:
        """Retrieve registered frozen preamble entry from catalog."""
        return self._frozen_catalog.get(name)

    def recombine_with_frozen(
        self,
        name_or_content: str,
        dialogue_messages: List[Dict[str, Any]],
        role: str = "system",
    ) -> List[Dict[str, Any]]:
        """Recombine frozen preamble with dynamic dialogue turns."""
        entry = self.get_frozen_preamble(name_or_content)
        content = entry["content"] if entry else name_or_content
        used_role = entry["role"] if entry else role

        preamble = [{"role": used_role, "content": content}]
        frozen_res = self.freeze_preamble(preamble, lock_id=name_or_content if entry else None)
        return frozen_res["all_messages"] + [dict(m) for m in dialogue_messages]


_DEFAULT_PREAMBLE_FREEZER = PreambleFreezer()


def get_default_preamble_freezer() -> PreambleFreezer:
    """Return default singleton preamble freezer instance."""
    return _DEFAULT_PREAMBLE_FREEZER


def freeze_prompt_preamble(
    messages: List[Dict[str, Any]],
    lock_id: Optional[str] = None,
    attach_cache_control: bool = True,
) -> Dict[str, Any]:
    """Freeze static preamble messages into immutable cacheable prefix block."""
    return _DEFAULT_PREAMBLE_FREEZER.freeze_preamble(
        messages,
        lock_id=lock_id,
        attach_cache_control=attach_cache_control,
    )


def is_preamble_frozen(messages: List[Dict[str, Any]]) -> bool:
    """Check whether leading message in message list is frozen."""
    return _DEFAULT_PREAMBLE_FREEZER.is_preamble_frozen(messages)


def verify_preamble_freeze(messages: List[Dict[str, Any]], expected_hash: str) -> bool:
    """Verify integrity of frozen preamble against expected hash."""
    return _DEFAULT_PREAMBLE_FREEZER.verify_freeze_integrity(messages, expected_hash)


def register_frozen_preamble(name: str, content: str, role: str = "system") -> Dict[str, Any]:
    """Register named static preamble in global frozen catalog."""
    return _DEFAULT_PREAMBLE_FREEZER.register_frozen_preamble(name, content, role=role)


def get_frozen_preamble(name: str) -> Optional[Dict[str, Any]]:
    """Retrieve named frozen preamble entry from catalog."""
    return _DEFAULT_PREAMBLE_FREEZER.get_frozen_preamble(name)


def recombine_frozen_preamble(
    name_or_content: str,
    dialogue_messages: List[Dict[str, Any]],
    role: str = "system",
) -> List[Dict[str, Any]]:
    """Recombine registered or raw frozen preamble with dialogue turns."""
    return _DEFAULT_PREAMBLE_FREEZER.recombine_with_frozen(
        name_or_content,
        dialogue_messages,
        role=role,
    )


class TurnSeparator:
    """Separate multi-turn dialogues into discrete cacheable turns and isolate active turn."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def _hash_block(items: List[Dict[str, Any]]) -> str:
        serialized = []
        for m in items:
            role = str(m.get("role", ""))
            content = str(m.get("content", ""))
            serialized.append(f"{role}:{content}")
        raw = "\n---\n".join(serialized).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def separate(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Separate message list into preamble, historical turns, and active turn."""
        if not messages:
            return {
                "preamble": [],
                "historical_turns": [],
                "active_turn": [],
                "turn_count": 0,
                "preamble_hash": "",
                "turn_hashes": [],
                "cumulative_hashes": [],
                "stable_prefix_messages": [],
                "active_messages": [],
            }

        preamble: List[Dict[str, Any]] = []
        dialogue: List[Dict[str, Any]] = []

        collecting = True
        for m in messages:
            role = str(m.get("role", ""))
            if collecting and role in ("system", "developer"):
                preamble.append(dict(m))
            else:
                collecting = False
                dialogue.append(dict(m))

        preamble_hash = self._hash_block(preamble) if preamble else ""

        if not dialogue:
            return {
                "preamble": preamble,
                "historical_turns": [],
                "active_turn": [],
                "turn_count": 0,
                "preamble_hash": preamble_hash,
                "turn_hashes": [],
                "cumulative_hashes": [preamble_hash] if preamble_hash else [],
                "stable_prefix_messages": preamble,
                "active_messages": [],
            }

        raw_turns: List[List[Dict[str, Any]]] = []
        current_turn: List[Dict[str, Any]] = []

        for m in dialogue:
            role = str(m.get("role", ""))
            if role == "user" and current_turn:
                raw_turns.append(current_turn)
                current_turn = [dict(m)]
            else:
                current_turn.append(dict(m))

        if current_turn:
            raw_turns.append(current_turn)

        historical_turns = raw_turns[:-1] if len(raw_turns) > 1 else []
        active_turn = raw_turns[-1] if raw_turns else []

        turn_hashes = [self._hash_block(t) for t in raw_turns]

        cumulative_hashes = []
        running_block = list(preamble)
        for t in raw_turns:
            running_block.extend(t)
            cumulative_hashes.append(self._hash_block(running_block))

        stable_prefix = list(preamble)
        for t in historical_turns:
            stable_prefix.extend(t)

        return {
            "preamble": preamble,
            "historical_turns": historical_turns,
            "active_turn": active_turn,
            "turn_count": len(raw_turns),
            "preamble_hash": preamble_hash,
            "turn_hashes": turn_hashes,
            "cumulative_hashes": cumulative_hashes,
            "stable_prefix_messages": stable_prefix,
            "active_messages": list(active_turn),
        }

    def attach_turn_cache_control(
        self,
        messages: List[Dict[str, Any]],
        mark_preamble: bool = True,
    ) -> List[Dict[str, Any]]:
        """Attach cache control breakpoint to final message of historical turns."""
        sep = self.separate(messages)
        if not sep["stable_prefix_messages"]:
            return [dict(m) for m in messages]

        annotated_prefix = [dict(m) for m in sep["stable_prefix_messages"]]

        if mark_preamble and sep["preamble"] and sep["historical_turns"]:
            preamble_last_idx = len(sep["preamble"]) - 1
            annotated_prefix[preamble_last_idx]["cache_control"] = {"type": "ephemeral"}

        annotated_prefix[-1]["cache_control"] = {"type": "ephemeral"}

        return annotated_prefix + [dict(m) for m in sep["active_messages"]]

    def validate_sequence(self, messages: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Validate turn sequencing and tool pairing consistency."""
        issues: List[str] = []
        if not messages:
            return True, issues

        last_role = None
        for idx, m in enumerate(messages):
            role = str(m.get("role", ""))
            if role == "tool" and last_role not in ("assistant", "tool"):
                issues.append(f"message {idx}: tool role without preceding assistant invocation")
            last_role = role

        return len(issues) == 0, issues


_DEFAULT_TURN_SEPARATOR = TurnSeparator()


def get_default_turn_separator() -> TurnSeparator:
    """Return default singleton turn separator instance."""
    return _DEFAULT_TURN_SEPARATOR


def separate_chat_turns(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Separate chat message history into preamble, completed turns, and active turn."""
    return _DEFAULT_TURN_SEPARATOR.separate(messages)


def attach_turn_cache_control(
    messages: List[Dict[str, Any]],
    mark_preamble: bool = True,
) -> List[Dict[str, Any]]:
    """Attach prompt cache breakpoint to end of completed dialogue history."""
    return _DEFAULT_TURN_SEPARATOR.attach_turn_cache_control(messages, mark_preamble=mark_preamble)


def get_turn_prefix_hashes(messages: List[Dict[str, Any]]) -> List[str]:
    """Compute running cumulative prefix hashes for each turn boundary."""
    return _DEFAULT_TURN_SEPARATOR.separate(messages)["cumulative_hashes"]


def validate_chat_turn_sequence(messages: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """Validate dialogue turn sequencing and report detected structural issues."""
    return _DEFAULT_TURN_SEPARATOR.validate_sequence(messages)


class CacheExpirationMonitor:
    """Monitor prompt cache time-to-live expiration and track active cache lease lifetimes."""

    DEFAULT_TTLS: Dict[str, float] = {
        "anthropic": 300.0,
        "openai": 600.0,
        "deepseek": 300.0,
        "openrouter": 300.0,
        "generic": 300.0,
    }

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Reset monitor tracking state and clear recorded cache entries."""
        self._entries: Dict[str, Dict[str, Any]] = {}

    def get_default_ttl(self, provider: str) -> float:
        """Resolve default expiration time-to-live seconds for provider."""
        prov_key = provider.lower()
        for key in ("anthropic", "openai", "deepseek", "openrouter"):
            if key in prov_key:
                return self.DEFAULT_TTLS[key]
        return self.DEFAULT_TTLS["generic"]

    def record_access(
        self,
        cache_key: str,
        provider: str = "generic",
        ttl_seconds: Optional[float] = None,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record cache entry access and update expiration lease timestamp."""
        now = timestamp if timestamp is not None else time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self.get_default_ttl(provider)

        if cache_key in self._entries:
            entry = self._entries[cache_key]
            entry["hit_count"] += 1
            entry["last_accessed"] = now
            entry["ttl"] = ttl
            entry["expires_at"] = now + ttl
            entry["provider"] = provider
        else:
            entry = {
                "cache_key": cache_key,
                "provider": provider,
                "created_at": now,
                "last_accessed": now,
                "ttl": ttl,
                "expires_at": now + ttl,
                "hit_count": 1,
            }
            self._entries[cache_key] = entry

        return dict(entry)

    def is_expired(self, cache_key: str, current_time: Optional[float] = None) -> bool:
        """Check whether recorded cache entry has expired."""
        if cache_key not in self._entries:
            return True
        now = current_time if current_time is not None else time.time()
        return now >= self._entries[cache_key]["expires_at"]

    def get_remaining_ttl(self, cache_key: str, current_time: Optional[float] = None) -> float:
        """Compute remaining time-to-live seconds before entry expiration."""
        if cache_key not in self._entries:
            return 0.0
        now = current_time if current_time is not None else time.time()
        remaining = self._entries[cache_key]["expires_at"] - now
        return max(0.0, round(remaining, 2))

    def get_status(self, cache_key: str, current_time: Optional[float] = None) -> Dict[str, Any]:
        """Retrieve comprehensive expiration telemetry for cache entry."""
        if cache_key not in self._entries:
            return {
                "cache_key": cache_key,
                "found": False,
                "is_expired": True,
                "remaining_ttl": 0.0,
            }

        entry = self._entries[cache_key]
        now = current_time if current_time is not None else time.time()
        rem = max(0.0, round(entry["expires_at"] - now, 2))
        is_exp = now >= entry["expires_at"]

        return {
            "cache_key": cache_key,
            "found": True,
            "provider": entry["provider"],
            "is_expired": is_exp,
            "is_active": not is_exp,
            "remaining_ttl": rem,
            "elapsed": round(now - entry["last_accessed"], 2),
            "ttl": entry["ttl"],
            "hit_count": entry["hit_count"],
        }

    def get_expiring_soon(
        self,
        threshold_seconds: float = 60.0,
        current_time: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Identify cache entries approaching expiration within threshold seconds."""
        now = current_time if current_time is not None else time.time()
        results: List[Dict[str, Any]] = []

        for key, entry in self._entries.items():
            rem = entry["expires_at"] - now
            if 0 < rem <= threshold_seconds:
                results.append({
                    "cache_key": key,
                    "provider": entry["provider"],
                    "remaining_ttl": round(rem, 2),
                })

        return results

    def prune_expired(self, current_time: Optional[float] = None) -> int:
        """Prune expired cache entries and return count of evicted entries."""
        now = current_time if current_time is not None else time.time()
        expired_keys = [k for k, v in self._entries.items() if now >= v["expires_at"]]
        for k in expired_keys:
            del self._entries[k]
        return len(expired_keys)


_DEFAULT_CACHE_EXPIRATION_MONITOR = CacheExpirationMonitor()


def get_default_expiration_monitor() -> CacheExpirationMonitor:
    """Return default singleton cache expiration monitor instance."""
    return _DEFAULT_CACHE_EXPIRATION_MONITOR


def record_cache_expiration(
    cache_key: str,
    provider: str = "generic",
    ttl_seconds: Optional[float] = None,
    timestamp: Optional[float] = None,
) -> Dict[str, Any]:
    """Record cache entry access and update expiration lease timestamp."""
    return _DEFAULT_CACHE_EXPIRATION_MONITOR.record_access(
        cache_key,
        provider=provider,
        ttl_seconds=ttl_seconds,
        timestamp=timestamp,
    )


def is_cache_expired(cache_key: str, current_time: Optional[float] = None) -> bool:
    """Check whether recorded cache entry has expired."""
    return _DEFAULT_CACHE_EXPIRATION_MONITOR.is_expired(cache_key, current_time=current_time)


def get_cache_remaining_ttl(cache_key: str, current_time: Optional[float] = None) -> float:
    """Compute remaining time-to-live seconds before entry expiration."""
    return _DEFAULT_CACHE_EXPIRATION_MONITOR.get_remaining_ttl(cache_key, current_time=current_time)


def get_cache_expiration_status(cache_key: str, current_time: Optional[float] = None) -> Dict[str, Any]:
    """Retrieve comprehensive expiration telemetry for cache entry."""
    return _DEFAULT_CACHE_EXPIRATION_MONITOR.get_status(cache_key, current_time=current_time)


def prune_expired_cache_records(current_time: Optional[float] = None) -> int:
    """Prune expired cache entries and return count of evicted entries."""
    return _DEFAULT_CACHE_EXPIRATION_MONITOR.prune_expired(current_time=current_time)


def reset_cache_expiration_monitor() -> None:
    """Reset global cache expiration monitor state."""
    _DEFAULT_CACHE_EXPIRATION_MONITOR.reset()


class KvReuseManager:
    """Manage KV cache prefix reuse across multi-agent sessions and swarm branches."""

    def __init__(self, block_size: int = 32) -> None:
        self.block_size = block_size
        self.reset()

    def reset(self) -> None:
        """Reset manager state and clear registered KV sessions."""
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._total_queries: int = 0
        self._total_matched_tokens: int = 0
        self._total_queried_tokens: int = 0

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"\w+|[^\w\s]", text, re.UNICODE)

    def _fingerprint(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
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
                "cumulative_fingerprint": cumulative_hash,
            })

        return {
            "root_fingerprint": cumulative_hash if blocks else "",
            "total_tokens": total_tokens,
            "blocks": blocks,
        }

    def register_session(self, session_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Register chat message sequence into KV session registry."""
        fp = self._fingerprint(messages)
        record = {
            "session_id": session_id,
            "messages": [dict(m) for m in messages],
            "total_tokens": fp["total_tokens"],
            "root_fingerprint": fp["root_fingerprint"],
            "blocks": fp["blocks"],
            "parent_id": None,
        }
        self._sessions[session_id] = record
        return record

    def find_best_reuse(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Find registered session offering maximum KV cache prefix reuse for messages."""
        if not messages or not self._sessions:
            return {
                "matched_session_id": None,
                "matched_tokens": 0,
                "total_tokens": 0,
                "reuse_ratio": 0.0,
                "matched_blocks": 0,
            }

        fp = self._fingerprint(messages)
        target_blocks = fp["blocks"]
        total_tokens = fp["total_tokens"]

        self._total_queries += 1
        self._total_queried_tokens += total_tokens

        best_session_id = None
        best_matched_tokens = 0
        best_matched_blocks = 0

        for sid, sess in self._sessions.items():
            sess_blocks = sess["blocks"]
            matched_blocks = 0
            matched_tokens = 0
            for idx in range(min(len(target_blocks), len(sess_blocks))):
                if target_blocks[idx]["cumulative_fingerprint"] == sess_blocks[idx]["cumulative_fingerprint"]:
                    matched_blocks += 1
                    matched_tokens += target_blocks[idx]["tokens_count"]
                else:
                    break

            if matched_tokens > best_matched_tokens:
                best_matched_tokens = matched_tokens
                best_matched_blocks = matched_blocks
                best_session_id = sid

        self._total_matched_tokens += best_matched_tokens
        ratio = round(best_matched_tokens / total_tokens, 4) if total_tokens > 0 else 0.0

        return {
            "matched_session_id": best_session_id,
            "matched_tokens": best_matched_tokens,
            "total_tokens": total_tokens,
            "reuse_ratio": ratio,
            "matched_blocks": best_matched_blocks,
        }

    def branch_session(
        self,
        parent_id: str,
        branch_id: str,
        additional_messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Fork child session branch from registered parent session sharing common prefix."""
        parent = self._sessions.get(parent_id)
        if not parent:
            return self.register_session(branch_id, additional_messages)

        full_messages = [dict(m) for m in parent["messages"]] + [dict(m) for m in additional_messages]
        record = self.register_session(branch_id, full_messages)
        record["parent_id"] = parent_id
        return record

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve registered KV session entry."""
        return self._sessions.get(session_id)

    def get_metrics(self) -> Dict[str, Any]:
        """Return aggregated KV cache reuse metrics."""
        overall_ratio = (
            round(self._total_matched_tokens / self._total_queried_tokens, 4)
            if self._total_queried_tokens > 0
            else 0.0
        )
        return {
            "total_sessions": len(self._sessions),
            "total_queries": self._total_queries,
            "total_queried_tokens": self._total_queried_tokens,
            "total_matched_tokens": self._total_matched_tokens,
            "overall_reuse_ratio": overall_ratio,
        }


_DEFAULT_KV_REUSE_MANAGER = KvReuseManager()


def get_default_kv_reuse_manager() -> KvReuseManager:
    """Return default singleton KV reuse manager instance."""
    return _DEFAULT_KV_REUSE_MANAGER


def register_kv_session(session_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Register chat session into global KV reuse registry."""
    return _DEFAULT_KV_REUSE_MANAGER.register_session(session_id, messages)


def find_kv_reuse(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Find best KV prefix reuse match across registered sessions."""
    return _DEFAULT_KV_REUSE_MANAGER.find_best_reuse(messages)


def branch_kv_session(
    parent_id: str,
    branch_id: str,
    additional_messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Fork child session branch sharing cached KV prefix blocks."""
    return _DEFAULT_KV_REUSE_MANAGER.branch_session(parent_id, branch_id, additional_messages)


def get_kv_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve registered KV session entry by identifier."""
    return _DEFAULT_KV_REUSE_MANAGER.get_session(session_id)


def get_kv_reuse_metrics() -> Dict[str, Any]:
    """Retrieve aggregated global KV reuse metrics."""
    return _DEFAULT_KV_REUSE_MANAGER.get_metrics()


def reset_kv_reuse_manager() -> None:
    """Reset global KV reuse manager state."""
    _DEFAULT_KV_REUSE_MANAGER.reset()


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
                usage_dict = res_json.get("usage")
                if isinstance(usage_dict, dict):
                    record_cache_hit_telemetry(url, usage_dict)
                if return_meta:
                    upstream = res_json.get("provider")
                    meta_dict = {
                        "content": content,
                        "model": res_json.get("model") or effective_model,
                        "upstream": upstream if isinstance(upstream, str) else None,
                        "usage": usage_dict if isinstance(usage_dict, dict) else None,
                    }
                    if isinstance(usage_dict, dict):
                        cached_t, prompt_t = CacheHitTelemetry.extract_cache_tokens(usage_dict)
                        meta_dict["cached_tokens"] = cached_t
                        meta_dict["cache_hit_ratio"] = round(cached_t / prompt_t, 4) if prompt_t > 0 else 0.0
                    return meta_dict
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
