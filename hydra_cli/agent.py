"""
Autonomous ReAct agent execution loop with MCP tool calling and multi-turn state.
Zero external dependencies.
"""

import json
import sys
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hydra_cli.config import DEFAULT_SYSTEM_PROMPT, resolve_route
from hydra_cli.providers import (
    ProviderError,
    UsageError,
    adapt_model_for_url,
    completion_timeout,
    describe_endpoint,
    ensure_temperature,
    get_frontier_providers,
    providers_for_model,
    reasoning_fields,
    redact,
)
from hydra_cli.ui import GREEN_BRIGHT, GREEN_MID, RESET, supports_color


def _fetch_raw_completion(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    """Execute raw HTTP POST request returning parsed JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    where = describe_endpoint(url)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ProviderError(redact(f"HTTP {e.code} from {where}: {error_body}")) from e
    except URLError as e:
        raise ProviderError(redact(f"Connection failed to {where}: {e.reason}")) from e
    except json.JSONDecodeError as e:
        raise ProviderError(f"Invalid JSON from {where}: {e}") from e
    if isinstance(parsed, dict) and parsed.get("error"):
        err = parsed["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise ProviderError(redact(f"Provider error from {where}: {message}"))
    return parsed


def tool_result_text(result: Any) -> str:
    """Serialize an MCP tool result for the model. Strings pass through; anything else is JSON."""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(result), ensure_ascii=False)


def run_agent_loop(
    alias: str,
    prompt: str,
    system_prompt: Optional[str] = None,
    registry: Optional[Any] = None,
    max_turns: int = 15,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Execute autonomous agent loop with MCP tool invocation until completion or max turns.
    Each turn tries the configured providers in order (OpenRouter, Vercel, then others).
    Once a provider answers, later turns start with it.
    Returns the final synthesized response text.
    """
    route = resolve_route(alias)
    requested_model = route["model"]
    ensure_temperature(requested_model, temperature)
    providers = providers_for_model(requested_model, get_frontier_providers())
    if not providers:
        raise ProviderError("No frontier provider credentials found in ~/.hydra/.env or environment.")

    sys_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": prompt},
    ]

    tools = registry.get_openai_tools() if registry else []
    reasoning = reasoning_fields(route.get("effort"), route.get("reasoning_mode"))
    timeout = completion_timeout(reasoning)

    color_on = supports_color()
    c_tool_head = f"{GREEN_BRIGHT}[HYDRA AGENT]{RESET}" if color_on else "[HYDRA AGENT]"
    c_tool_call = f"{GREEN_MID}" if color_on else ""
    c_reset = RESET if color_on else ""

    order = list(providers)

    def complete_turn() -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for index, provider in enumerate(order):
            payload: Dict[str, Any] = {
                "model": adapt_model_for_url(provider["url"], requested_model),
                "messages": messages,
                "stream": False,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"
            if reasoning:
                payload["reasoning"] = reasoning
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            try:
                res_json = _fetch_raw_completion(provider["url"], provider["headers"], payload, timeout=timeout)
                if not res_json.get("choices"):
                    raise ProviderError(f"{provider.get('name', 'Provider')} returned an empty choices array.")
            except UsageError:
                raise
            except Exception as exc:
                last_error = exc
                if index + 1 < len(order):
                    sys.stderr.write(
                        f"{c_tool_head} {provider.get('name', 'Provider')} failed ({redact(exc)}). "
                        f"Trying {order[index + 1].get('name', 'next provider')}.\n"
                    )
                    sys.stderr.flush()
                continue
            if index:
                order.insert(0, order.pop(index))
            return res_json
        raise ProviderError(redact(f"All configured providers failed for '{requested_model}'. Last error: {last_error}"))

    for _turn in range(max_turns):
        res_json = complete_turn()
        choice = res_json["choices"][0]
        message = choice.get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            # Model emitted final answer
            return message.get("content", "") or ""

        # Execute each tool call
        for tc in tool_calls:
            call_id = tc.get("id", "")
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
            except Exception:
                args = {}

            sys.stderr.write(f"\n{c_tool_head} Invoking tool: {c_tool_call}{fn_name}{c_reset}\n")
            sys.stderr.flush()

            if registry:
                try:
                    result_content: Any = registry.dispatch(fn_name, args)
                except Exception as e:
                    result_content = {"isError": True, "error": f"Error executing {fn_name}: {redact(e)}"}
            else:
                result_content = {"isError": True, "error": f"MCP registry not available to execute {fn_name}"}

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": fn_name,
                "content": tool_result_text(result_content),
            })

    sys.stderr.write(f"\n{c_tool_head} Reached maximum iterations ({max_turns}).\n")
    return messages[-1].get("content", "Agent loop reached maximum turns without termination.")
