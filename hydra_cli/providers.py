"""
Provider integrations and streaming transport for Hydra CLI.
Supports OpenRouter, Vercel AI Gateway, Cloudflare Workers AI, and Local Inference (Ollama/llama.cpp/EasyLM).
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from hydra_cli.config import (
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_EASYLM_ENDPOINT,
    DEFAULT_FREE_MODEL,
    DEFAULT_LLAMACPP_ENDPOINT,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_OLLAMA_ENDPOINT,
    DEFAULT_SYSTEM_PROMPT,
    FREE_MODELS,
)


class ProviderError(Exception):
    """Base exception for provider invocation failures."""
    pass


class CredentialsMissingError(ProviderError):
    """Raised when required credentials are missing."""
    pass


def is_port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    """Check if a TCP port is open locally with a fast timeout."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def detect_local_endpoint() -> Tuple[str, str]:
    """
    Detect an active local inference endpoint and return (endpoint_url, provider_name).
    Checks LOCAL_AI_BASE first, then Ollama (11434), llama.cpp (8080), EasyLM (8000).
    """
    custom_base = os.environ.get("LOCAL_AI_BASE")
    if custom_base:
        base = custom_base.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        return f"{base}/chat/completions", "Custom Local AI"

    # Ollama on 11434
    ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    port = 11434
    try:
        if ":" in ollama_host.split("//")[-1]:
            port = int(ollama_host.split(":")[-1].split("/")[0])
    except Exception:
        port = 11434

    if is_port_open("127.0.0.1", port):
        return f"{ollama_host}/v1/chat/completions", "Ollama"

    # llama.cpp on 8080
    llamacpp_host = os.environ.get("LLAMACPP_HOST", "http://localhost:8080").rstrip("/")
    if is_port_open("127.0.0.1", 8080):
        return f"{llamacpp_host}/v1/chat/completions", "llama.cpp"

    # EasyLM / WebGPU local server on 8000
    if is_port_open("127.0.0.1", 8000):
        return f"{DEFAULT_EASYLM_ENDPOINT}/v1/chat/completions", "EasyLM"

    # Fallback default: Ollama
    return f"{DEFAULT_OLLAMA_ENDPOINT}/v1/chat/completions", "Ollama (unverified)"


def get_frontier_providers() -> List[Dict[str, Any]]:
    """Return configured frontier cloud providers in order of priority."""
    providers = []
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    vercel_key = os.environ.get("AI_GATEWAY_API_KEY", "").strip()

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

    return providers


def get_free_provider() -> Tuple[Dict[str, Any], str]:
    """
    Resolve provider and model for Free Forge zero-cost routing.
    Priority:
    1. Cloudflare Workers AI if CLOUDFLARE_API_TOKEN & CLOUDFLARE_ACCOUNT_ID exist.
    2. OpenRouter with free-tier model (uses OPENROUTER_API_KEY if present, or OpenRouter free route).
    """
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    cf_account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()

    if cf_token and cf_account:
        return {
            "name": "Cloudflare Workers AI",
            "url": f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {cf_token}",
                "Content-Type": "application/json",
            },
        }, DEFAULT_CLOUDFLARE_MODEL

    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
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

    # If neither credential is set, suggest setting one or falling back to local
    raise CredentialsMissingError(
        "Free Forge requires either:\n"
        "  - CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID (for Cloudflare Workers AI zero-cost tier), or\n"
        "  - OPENROUTER_API_KEY (for OpenRouter free models like Llama-3.3-70B:free)\n"
        "To run with zero external keys completely offline, use: hydra local \"<prompt>\""
    )


def adapt_model_for_url(url: str, model: str) -> str:
    """Translate provider-specific namespaces based on destination gateway URL."""
    u_lower = url.lower()
    if "vercel" in u_lower:
        if model.startswith("x-ai/"):
            return model.replace("x-ai/", "spacexai/")
        if model.startswith("meta-llama/"):
            v_id = model.replace("meta-llama/", "meta/")
            if v_id.endswith("-instruct"):
                v_id = v_id[:-len("-instruct")]
            return v_id
        if model.startswith("qwen/"):
            return model.replace("qwen/", "alibaba/")
        if model == "openai/gpt-6.1-sol-pro":
            return "openai/gpt-6.1-sol"
        if model == "openai/gpt-6-luna-pro":
            return "openai/gpt-6-luna"
    elif "openrouter" in u_lower:
        if model.startswith("spacexai/"):
            return model.replace("spacexai/", "x-ai/")
        if model.startswith("alibaba/"):
            return model.replace("alibaba/", "qwen/")
        if model.startswith("meta/"):
            return model.replace("meta/", "meta-llama/")
    return model


def stream_chat_completion(
    url: str,
    headers: Dict[str, str],
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: int = 120,
) -> Generator[str, None, None]:
    """
    Send streaming chat completion request and yield text delta tokens via SSE.
    """
    effective_model = adapt_model_for_url(url, model)
    payload: Dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data_bytes, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]":
                    break
                if line.startswith("data: "):
                    raw_json = line[6:].strip()
                    try:
                        chunk = json.loads(raw_json)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
            parsed_err = json.loads(error_body)
            msg = parsed_err.get("error", {}).get("message", error_body)
        except Exception:
            msg = error_body or str(e)
        raise ProviderError(f"HTTP {e.code} error from {url}: {msg}") from e
    except urllib.error.URLError as e:
        raise ProviderError(f"Connection failed to {url}: {e.reason}") from e
    except Exception as e:
        raise ProviderError(f"Streaming error: {e}") from e


def fetch_chat_completion(
    url: str,
    headers: Dict[str, str],
    model: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: int = 120,
) -> str:
    """
    Fetch complete chat completion response without streaming.
    """
    effective_model = adapt_model_for_url(url, model)
    payload: Dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data_bytes, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8", errors="replace")
            res_json = json.loads(raw_body)
            choices = res_json.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
            parsed_err = json.loads(error_body)
            msg = parsed_err.get("error", {}).get("message", error_body)
        except Exception:
            msg = error_body or str(e)
        raise ProviderError(f"HTTP {e.code} error from {url}: {msg}") from e
    except Exception as e:
        raise ProviderError(f"Request failed: {e}") from e
