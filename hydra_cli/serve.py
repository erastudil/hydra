"""
Sovereign Gateway Server for Hydra CLI.
Exposes zero-dependency OpenAI-compatible endpoints (/v1/models, /v1/chat/completions)
enabling external agent runners (Hermes, Pi) and local tools to summon Hydra backends.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Generator, List, Optional, Tuple

from hydra_cli import __version__
from hydra_cli.config import (
    CATALOG,
    FREE_MODELS,
    MODEL_MAP,
    load_dotenv,
    resolve_route,
)
from hydra_cli.providers import (
    ProviderError,
    UsageError,
    adapt_model_for_url,
    attach_tool_capability,
    completion_timeout,
    ensure_temperature,
    CredentialsMissingError,
    describe_endpoint,
    get_frontier_providers,
    providers_for_model,
    reasoning_fields,
    redact,
)


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


def get_registered_models() -> Dict[str, Any]:
    """Return all registered models and aliases in OpenAI /v1/models format."""
    seen = set()
    models = []

    # 1. Registered aliases
    for alias in sorted(MODEL_MAP.keys()):
        if alias not in seen:
            seen.add(alias)
            models.append({
                "id": alias,
                "object": "model",
                "created": 1700000000,
                "owned_by": "hydra",
                "permission": [],
                "root": MODEL_MAP[alias],
                "parent": None,
            })

    # 2. Canonical target model IDs
    for spec in CATALOG.get("aliases", {}).values():
        model_id = spec.get("model")
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append({
                "id": model_id,
                "object": "model",
                "created": 1700000000,
                "owned_by": "hydra",
                "permission": [],
                "root": model_id,
                "parent": None,
            })

    # 3. Free models
    for model_id in FREE_MODELS:
        if model_id not in seen:
            seen.add(model_id)
            models.append({
                "id": model_id,
                "object": "model",
                "created": 1700000000,
                "owned_by": "hydra-free",
                "permission": [],
                "root": model_id,
                "parent": None,
            })

    return {
        "object": "list",
        "data": models,
    }


def build_upstream_payload(
    provider_url: str,
    target_model: str,
    messages: List[Dict[str, Any]],
    stream: bool = False,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Any] = None,
    reasoning: Optional[Dict[str, str]] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct an OpenAI-compatible payload adapted for the provider endpoint."""
    effective_model = adapt_model_for_url(provider_url, target_model)
    payload: Dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if tools is not None:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if reasoning:
        payload["reasoning"] = reasoning
    if extra_fields:
        for k, v in extra_fields.items():
            if k not in payload and v is not None:
                payload[k] = v
    attach_tool_capability(provider_url, payload)
    return payload


def forward_chat_completion(
    provider: Dict[str, Any],
    payload: Dict[str, Any],
    timeout: int = 180,
) -> Dict[str, Any]:
    """Send a non-streaming chat completion request to an upstream provider."""
    url = provider["url"]
    headers = dict(provider.get("headers", {}))
    headers["Content-Type"] = "application/json"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            res_json = json.loads(raw)
            if "error" in res_json:
                raise ProviderError(redact(_error_message(raw, "Provider returned an error")))
            return res_json
    except urllib.error.HTTPError as exc:
        raw_body = ""
        try:
            raw_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        err_msg = _error_message(raw_body, str(exc))
        raise ProviderError(redact(f"HTTP {exc.code} error from {describe_endpoint(url)}: {err_msg}")) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(redact(f"Connection failed to {describe_endpoint(url)}: {exc.reason}")) from exc
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Invalid JSON response from {describe_endpoint(url)}: {exc}") from exc


def forward_stream_completion(
    provider: Dict[str, Any],
    payload: Dict[str, Any],
    timeout: int = 180,
) -> Generator[str, None, None]:
    """Send a streaming chat completion request and yield raw SSE data lines."""
    url = provider["url"]
    headers = dict(provider.get("headers", {}))
    headers["Content-Type"] = "application/json"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            saw_done = False
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]":
                    saw_done = True
                    yield "data: [DONE]\n\n"
                    break
                if line.startswith("data: "):
                    try:
                        chunk = json.loads(line[6:].strip())
                        _raise_if_provider_error(chunk)
                    except json.JSONDecodeError:
                        pass
                    yield f"{line}\n\n"
            if not saw_done:
                yield "data: [DONE]\n\n"
    except urllib.error.HTTPError as exc:
        raw_body = ""
        try:
            raw_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        err_msg = _error_message(raw_body, str(exc))
        raise ProviderError(redact(f"HTTP {exc.code} error from {describe_endpoint(url)}: {err_msg}")) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(redact(f"Connection failed to {describe_endpoint(url)}: {exc.reason}")) from exc


class HydraGatewayHandler(BaseHTTPRequestHandler):
    """
    Sovereign OpenAI-compatible HTTP handler.
    Serves /v1/models and /v1/chat/completions with automatic route resolution.
    """

    def log_message(self, format: str, *args: Any) -> None:
        if os.environ.get("HYDRA_GATEWAY_QUIET"):
            return
        sys.stderr.write(f"[HYDRA GATEWAY] {self.address_string()} - {format % args}\n")

    def send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: int, message: str, error_type: str = "invalid_request_error") -> None:
        self.send_json(status, {
            "error": {
                "message": message,
                "type": error_type,
                "code": status,
            }
        })

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("/v1/models", "/models"):
            self.send_json(200, get_registered_models())
        elif clean_path in ("", "/", "/health", "/v1/health"):
            self.send_json(200, {
                "status": "ok",
                "service": "hydra-gateway",
                "version": __version__,
            })
        else:
            self.send_error_json(404, f"Path '{self.path}' not found", "not_found")

    def do_POST(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path not in ("/v1/chat/completions", "/chat/completions"):
            self.send_error_json(404, f"Path '{self.path}' not found", "not_found")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_error_json(400, "Invalid Content-Length header")
            return

        if content_length <= 0:
            self.send_error_json(400, "Request body required")
            return

        try:
            body = self.rfile.read(content_length)
            req_data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            self.send_error_json(400, f"Malformed JSON body: {exc}")
            return

        if not isinstance(req_data, dict):
            self.send_error_json(400, "JSON body must be an object")
            return

        model_requested = req_data.get("model")
        if not model_requested or not isinstance(model_requested, str):
            self.send_error_json(400, "Missing required parameter 'model'")
            return

        messages = req_data.get("messages")
        if not isinstance(messages, list):
            self.send_error_json(400, "Missing or invalid parameter 'messages', must be an array")
            return

        stream = bool(req_data.get("stream", False))
        temperature = req_data.get("temperature")
        max_tokens = req_data.get("max_tokens")
        tools = req_data.get("tools")
        tool_choice = req_data.get("tool_choice")

        # Capture optional standard OpenAI passthrough fields
        passthrough_keys = ("top_p", "frequency_penalty", "presence_penalty", "stop", "response_format", "seed")
        extra_fields = {k: req_data[k] for k in passthrough_keys if k in req_data}

        # Resolve model alias via Hydra route catalog
        route = resolve_route(model_requested)
        target_model = route.get("model") or model_requested
        route_effort = route.get("effort")
        route_mode = route.get("reasoning_mode")
        reasoning = reasoning_fields(route_effort, route_mode)

        # Enforce model temperature policy
        try:
            ensure_temperature(target_model, temperature)
        except UsageError as ue:
            self.send_error_json(400, str(ue), "invalid_request_error")
            return

        try:
            providers = providers_for_model(target_model, get_frontier_providers())
        except CredentialsMissingError as cme:
            self.send_error_json(503, str(cme), "credentials_missing_error")
            return
        if not providers:
            self.send_error_json(
                503,
                "No frontier providers configured. Set OPENROUTER_API_KEY, AI_GATEWAY_API_KEY, or CHEAPERINFERENCE_API_KEY.",
                "credentials_missing_error",
            )
            return

        wait = completion_timeout(reasoning)

        # Non-streaming branch
        if not stream:
            last_error: Optional[Exception] = None
            for provider in providers:
                try:
                    payload = build_upstream_payload(
                        provider_url=provider["url"],
                        target_model=target_model,
                        messages=messages,
                        stream=False,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        tool_choice=tool_choice,
                        reasoning=reasoning,
                        extra_fields=extra_fields,
                    )
                    completion_json = forward_chat_completion(provider, payload, timeout=wait)
                    self.send_json(200, completion_json)
                    return
                except UsageError as ue:
                    self.send_error_json(400, str(ue), "invalid_request_error")
                    return
                except Exception as exc:
                    last_error = exc
                    continue

            self.send_error_json(502, redact(f"All configured providers failed: {last_error}"), "provider_error")
            return

        # Streaming branch (Server-Sent Events)
        last_error = None
        for provider in providers:
            try:
                payload = build_upstream_payload(
                    provider_url=provider["url"],
                    target_model=target_model,
                    messages=messages,
                    stream=True,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                    reasoning=reasoning,
                    extra_fields=extra_fields,
                )
                stream_gen = forward_stream_completion(provider, payload, timeout=wait)
                stream_iter = iter(stream_gen)
                try:
                    first_chunk = next(stream_iter)
                except StopIteration:
                    first_chunk = "data: [DONE]\n\n"

                # Send SSE HTTP headers only after first chunk succeeds
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                def _write_sse_chunk(chunk_val: Any) -> None:
                    if isinstance(chunk_val, dict):
                        chunk_str = f"data: {json.dumps(chunk_val)}\n\n"
                    elif isinstance(chunk_val, str):
                        if not chunk_val.startswith("data:"):
                            chunk_str = f"data: {chunk_val}\n\n"
                        elif not chunk_val.endswith("\n\n"):
                            chunk_str = chunk_val.rstrip() + "\n\n"
                        else:
                            chunk_str = chunk_val
                    else:
                        chunk_str = f"data: {chunk_val}\n\n"
                    self.wfile.write(chunk_str.encode("utf-8"))
                    self.wfile.flush()

                try:
                    _write_sse_chunk(first_chunk)
                    for chunk in stream_iter:
                        _write_sse_chunk(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    try:
                        err_payload = json.dumps({"error": {"message": str(exc), "type": "stream_error"}})
                        self.wfile.write(f"data: {err_payload}\n\ndata: [DONE]\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except Exception:
                        pass
                    return
                finally:
                    self.close_connection = True
                return
            except Exception as exc:
                last_error = exc
                continue

        self.send_error_json(502, redact(f"All configured providers failed: {last_error}"), "provider_error")


def create_server(host: str = "127.0.0.1", port: int = 7777) -> HTTPServer:
    """Create and return an HTTPServer configured with HydraGatewayHandler."""
    load_dotenv()
    return HTTPServer((host, port), HydraGatewayHandler)


def run_server(host: str = "127.0.0.1", port: int = 7777) -> None:
    """Start the sovereign Hydra Gateway HTTP server."""
    httpd = create_server(host, port)
    print(f"Hydra Sovereign Gateway running on http://{host}:{port}/v1")
    print(f"OpenAI Base URL: http://{host}:{port}/v1")
    print(f"Endpoints: GET /v1/models, POST /v1/chat/completions")
    print("Press Ctrl+C to terminate.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Hydra Gateway.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hydra Sovereign OpenAI Gateway Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7777, help="Port to bind (default: 7777)")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
