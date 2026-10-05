"""
Provider integrations and streaming transport for Hydra CLI.
Supports OpenRouter, Vercel AI Gateway, Cloudflare Workers AI, and local inference.
"""

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Generator, List, Optional, Tuple
from urllib.parse import urlparse

from hydra_cli.config import (
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_FREE_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    load_dotenv,
    model_rejects_temperature,
    resolve_route,
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
        "name": "Modal",
        "url": chat_completions_url(endpoint_url),
        "headers": headers,
    }


def get_frontier_providers() -> List[Dict[str, Any]]:
    """Return configured frontier cloud providers. OpenRouter is tried first."""
    providers = []
    openrouter_key = clean_secret(os.environ.get("OPENROUTER_API_KEY"))
    vercel_key = gateway_api_key()

    if openrouter_key:
        providers.append({
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

    return providers


def get_free_provider() -> Tuple[Dict[str, Any], str]:
    """
    Resolve provider and model for Free Forge.
    Cloudflare Workers AI wins when both Cloudflare variables are set.
    Otherwise OpenRouter's free-tier model is used. A key is required either way.
    """
    cf_token = clean_secret(os.environ.get("CLOUDFLARE_API_TOKEN"))
    cf_account = clean_secret(os.environ.get("CLOUDFLARE_ACCOUNT_ID"))

    if cf_token and cf_account:
        return {
            "name": "Cloudflare Workers AI",
            "url": f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {cf_token}",
                "Content-Type": "application/json",
            },
        }, DEFAULT_CLOUDFLARE_MODEL

    openrouter_key = clean_secret(os.environ.get("OPENROUTER_API_KEY"))
    if openrouter_key:
        return {
            "name": "OpenRouter Free Forge",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/erastudil/hydra",
                "X-Title": "Hydra Free Forge",
            },
        }, DEFAULT_FREE_MODEL

    raise CredentialsMissingError(
        "Free Forge requires either:\n"
        "  - CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID, or\n"
        "  - OPENROUTER_API_KEY\n"
        "For a machine with no cloud key, use: hydra local \"<prompt>\""
    )


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
    raise ProviderError(message)


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
                raise ProviderError(f"Stream from {url} ended before data: [DONE]")
    except ProviderError:
        raise
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        raise ProviderError(f"HTTP {exc.code} error from {url}: {_error_message(error_body, str(exc))}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Connection failed to {url}: {exc.reason}") from exc
    except Exception as exc:
        raise ProviderError(f"Streaming error: {exc}") from exc


def fetch_chat_completion(
    url: str,
    headers: Dict[str, str],
    model: str,
    messages: List[Dict[str, str]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
    reasoning: Optional[Dict[str, str]] = None,
) -> str:
    """Fetch one complete chat completion."""
    ensure_temperature(model, temperature)
    effective_model = adapt_model_for_url(url, model)
    payload = _build_payload(effective_model, messages, False, temperature, max_tokens, reasoning)
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data_bytes, method="POST")
    wait = completion_timeout(reasoning, timeout)

    try:
        with urllib.request.urlopen(req, timeout=wait) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
            res_json = json.loads(raw_body)
            _raise_if_provider_error(res_json)
            choices = res_json.get("choices") or []
            if not choices:
                raise ProviderError(f"Empty completion from {url}")
            content = choices[0].get("message", {}).get("content")
            if not isinstance(content, str) or not content:
                raise ProviderError(f"Empty completion from {url}")
            return content
    except ProviderError:
        raise
    except urllib.error.HTTPError as exc:
        error_body = ""
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        raise ProviderError(f"HTTP {exc.code} error from {url}: {_error_message(error_body, str(exc))}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Connection failed to {url}: {exc.reason}") from exc
    except Exception as exc:
        if isinstance(exc, ProviderError):
            raise
        raise ProviderError(f"Request failed: {exc}") from exc


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
    providers = get_frontier_providers()
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
    raise ProviderError(f"All configured providers failed for '{route['model']}'. Last error: {last_error}")
