"""
Provider integrations and streaming transport for Hydra CLI.
Supports OpenRouter, Vercel AI Gateway, Cloudflare Workers AI, and local inference.
"""

import json
import os
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.parse import urlparse

from hydra_cli.config import (
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_FREE_MODEL,
    DEFAULT_HF_BASE,
    DEFAULT_HF_MODEL,
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
    """Return configured frontier cloud providers. OpenRouter is tried first."""
    providers = []
    openrouter_key = clean_secret(os.environ.get("OPENROUTER_API_KEY"))
    vercel_key = gateway_api_key()

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

    cheaper = get_cheaperinference_provider()
    if cheaper:
        providers.append(cheaper)

    runpod = get_runpod_provider()
    if runpod:
        providers.append(runpod)

    modal = get_modal_provider()
    if modal:
        providers.append(modal)

    hf = get_huggingface_provider()
    if hf:
        providers.append(hf)

    return providers


def providers_for_model(model: str, providers: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Configured providers that may serve this model, in fallback order.

    The catalog's model_providers map limits a model to the providers that carry it
    (for example a Vercel-only alias). Other models may use every configured provider.
    """
    configured = get_frontier_providers() if providers is None else list(providers)
    allowed = model_providers(model)
    if not allowed:
        return configured
    rank = {pid: index for index, pid in enumerate(allowed)}
    usable = [p for p in configured if p.get("id") in rank]
    usable.sort(key=lambda p: rank[p["id"]])
    if configured and not usable:
        names = ", ".join(PROVIDER_KEY_NAMES.get(pid, pid) for pid in allowed)
        raise CredentialsMissingError(
            f"{model} is only served by: {', '.join(allowed)}. Set {names} to use it."
        )
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


def get_free_candidates(model_override: Optional[str] = None) -> List[Tuple[Dict[str, Any], str]]:
    """Ordered (provider, model) pairs for Free Forge.

    Cloudflare Workers AI goes first when both Cloudflare variables are set. OpenRouter's
    free models follow, so a Cloudflare failure still gets an answer. An @cf/ override
    stays on Cloudflare. Any other override goes to OpenRouter first.
    """
    cloudflare = _cloudflare_provider()
    openrouter = _openrouter_free_provider()
    if not cloudflare and not openrouter:
        raise CredentialsMissingError(
            "Free Forge requires either:\n"
            "  - CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID, or\n"
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
        if openrouter:
            candidates.append((openrouter, model_override))
        elif cloudflare:
            candidates.append((cloudflare, model_override))
        return candidates

    if cloudflare:
        candidates.append((cloudflare, DEFAULT_CLOUDFLARE_MODEL))
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


def stream_chat_completion(
    url: str,
    headers: Dict[str, str],
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    reasoning: Optional[Dict[str, str]] = None,
) -> Generator[str, None, None]:
    """Send a streaming chat completion and yield text deltas."""
    ensure_temperature(model, temperature)
    effective_model = adapt_model_for_url(url, model)
    payload = _build_payload(effective_model, messages, True, temperature, max_tokens, reasoning)
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data_bytes, method="POST")
    wait = completion_timeout(reasoning, timeout)

    try:
        with urllib.request.urlopen(req, timeout=wait) as resp:
            saw_done = False
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]":
                    saw_done = True
                    break
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:].strip())
                except json.JSONDecodeError:
                    continue
                _raise_if_provider_error(chunk)
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content") or ""
                if content:
                    yield content
            if not saw_done:
                raise ProviderError(f"Stream from {describe_endpoint(url)} ended before data: [DONE]")
    except ProviderError:
        raise
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(redact(f"Connection failed to {describe_endpoint(url)}: {exc.reason}")) from exc
    except Exception as exc:
        raise ProviderError(redact(f"Streaming error from {describe_endpoint(url)}: {exc}")) from exc


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
) -> Any:
    """Fetch one complete chat completion.

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
            if return_meta:
                upstream = res_json.get("provider")
                return {
                    "content": content,
                    "model": res_json.get("model") or effective_model,
                    "upstream": upstream if isinstance(upstream, str) else None,
                    "usage": res_json.get("usage") if isinstance(res_json.get("usage"), dict) else None,
                }
            return content
    except ProviderError:
        raise
    except urllib.error.HTTPError as exc:
        raise _http_error(url, exc) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(redact(f"Connection failed to {where}: {exc.reason}")) from exc
    except Exception as exc:
        raise ProviderError(redact(f"Request to {where} failed: {exc}")) from exc


def complete(
    alias: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """Summon one alias and return the assistant text. Tries each configured provider."""
    load_dotenv()
    route = resolve_route(alias)
    providers = providers_for_model(route["model"])
    if not providers:
        raise CredentialsMissingError(
            "No frontier credentials found. Export OPENROUTER_API_KEY, AI_GATEWAY_API_KEY, or CHEAPERINFERENCE_API_KEY."
        )
    messages = [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    reasoning = reasoning_fields(route.get("effort"), route.get("reasoning_mode"))
    ensure_temperature(route["model"], temperature)
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
    raise ProviderError(redact(f"All configured providers failed for '{route['model']}'. Last error: {last_error}"))
