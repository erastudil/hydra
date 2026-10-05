"""
Autonomous ReAct agent execution loop with MCP tool calling and multi-turn state.
Zero external dependencies.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hydra_cli.config import DEFAULT_SYSTEM_PROMPT, resolve_route
from hydra_cli.providers import (
    ProviderError,
    adapt_model_for_url,
    get_frontier_providers,
    reasoning_fields,
)
from hydra_cli.ui import GREEN_BRIGHT, GREEN_MID, RESET, supports_color


def _fetch_raw_completion(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
    """Execute raw HTTP POST request returning parsed JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise ProviderError(f"HTTP {e.code} from {url}: {error_body}") from e
    except URLError as e:
        raise ProviderError(f"Connection failed to {url}: {e.reason}") from e


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
    Returns the final synthesized response text.
    """
    route = resolve_route(alias)
    requested_model = route["model"]
    providers = get_frontier_providers()
    if not providers:
        raise ProviderError("No frontier provider credentials found in ~/.hydra/.env or environment.")

    provider = providers[0]
    model_id = adapt_model_for_url(requested_model, provider["url"])

    sys_text = system_prompt or DEFAULT_SYSTEM_PROMPT
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": sys_text},
        {"role": "user", "content": prompt},
    ]

    tools = registry.get_openai_tools() if registry else []
    reasoning = reasoning_fields(route.get("effort"), route.get("reasoning_mode"))

    color_on = supports_color()
    c_tool_head = f"{GREEN_BRIGHT}[HYDRA AGENT]{RESET}" if color_on else "[HYDRA AGENT]"
    c_tool_call = f"{GREEN_MID}" if color_on else ""
    c_reset = RESET if color_on else ""

    for turn in range(max_turns):
        payload: Dict[str, Any] = {
            "model": model_id,
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

        res_json = _fetch_raw_completion(provider["url"], provider["headers"], payload)
        choices = res_json.get("choices", [])
        if not choices:
            raise ProviderError("Upstream provider returned empty choices array.")

        choice = choices[0]
        message = choice.get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            # Model emitted final answer
            return message.get("content", "")

        # Execute each tool call
        for tc in tool_calls:
            call_id = tc.get("id", "")
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                args = {}

            sys.stderr.write(f"\n{c_tool_head} Invoking tool: {c_tool_call}{fn_name}{c_reset}\n")
            sys.stderr.flush()

            if registry:
                try:
                    result_content = registry.dispatch(fn_name, args)
                except Exception as e:
                    result_content = f"Error executing {fn_name}: {str(e)}"
            else:
                result_content = f"Error: MCP registry not available to execute {fn_name}"

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": fn_name,
                "content": str(result_content),
            })

    sys.stderr.write(f"\n{c_tool_head} Reached maximum iterations ({max_turns}).\n")
    return messages[-1].get("content", "Agent loop reached maximum turns without termination.")
