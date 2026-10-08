"""
Universal prompt-based tool calling adapter for models and free/edge endpoints.
Bridges models that lack native OpenAI function calling or reject the "tool" role
via structured <tool_call> tags and observation sequences.
Standard library only. Zero external dependencies.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

TOOL_UNSUPPORTED_PATTERNS = [
    re.compile(r"tools?\s+is\s+not\s+supported", re.IGNORECASE),
    re.compile(r"tools?\s+not\s+supported", re.IGNORECASE),
    re.compile(r"tool_calls?\s+not\s+supported", re.IGNORECASE),
    re.compile(r"tool_choice", re.IGNORECASE),
    re.compile(r"does\s+not\s+support\s+tool", re.IGNORECASE),
    re.compile(r"functions?\s+are\s+not\s+supported", re.IGNORECASE),
    re.compile(r"function\s+calling\s+is\s+not\s+supported", re.IGNORECASE),
    re.compile(r"invalid\s+(?:value\s+for\s+)?role[:\s]+['\"]?tool", re.IGNORECASE),
    re.compile(r"unrecognized\s+field\s+`?tools`?", re.IGNORECASE),
    re.compile(r"unexpected\s+field\s+`?tools`?", re.IGNORECASE),
    re.compile(r"extra\s+fields?\s+not\s+permitted.*tools?", re.IGNORECASE),
    re.compile(r"failed\s+to\s+deserialize.*tools?", re.IGNORECASE),
    re.compile(r"schema\s+(?:error|violation|validation).*tools?", re.IGNORECASE),
    re.compile(r"(?:invalid|unsupported)\s+schema", re.IGNORECASE),
    re.compile(r"tools\s+is\s+not\s+allowed", re.IGNORECASE),
    re.compile(r"unknown\s+(?:field|parameter).*tools?", re.IGNORECASE),
    re.compile(r"invalid\s+(?:field|parameter).*tools?", re.IGNORECASE),
]


def is_tool_unsupported_error(exc: Exception) -> bool:
    """
    Detect whether an HTTP error or provider exception indicates rejection of
    native tool schemas, function calling, or the 'tool' role.
    """
    if exc is None:
        return False

    err_str = str(exc)

    # Fast pattern matching across known provider error signatures
    for pat in TOOL_UNSUPPORTED_PATTERNS:
        if pat.search(err_str):
            return True

    # Cloudflare / Ollama / edge HTTP 400 rejection heuristics
    lower = err_str.lower()
    if ("http 400" in lower or "400 bad request" in lower or "http 422" in lower or "http 404" in lower):
        if any(kw in lower for kw in ("tool", "tools", "function", "functions", "schema", "role", "unknown field", "unrecognized", "parameter")):
            return True
        if "api.cloudflare.com" in lower or "cheaperinference" in lower:
            return True

    return False


def inject_tool_prompt(system_prompt: str, tools: List[Dict[str, Any]]) -> str:
    """
    Inject structured tool descriptions and explicit XML invocation instructions
    into system prompt for prompt-based tool calling fallback.
    """
    if not tools:
        return system_prompt

    if "[AVAILABLE TOOLS]" in system_prompt:
        return system_prompt

    tool_docs: List[str] = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            fn = t["function"]
            name = fn.get("name", "")
            desc = fn.get("description", "")
            params = fn.get("parameters", {})
            props = params.get("properties", {}) if isinstance(params, dict) else {}
            reqs = set(params.get("required", [])) if isinstance(params, dict) else set()
            param_list = []
            for p_name, p_info in props.items():
                p_type = p_info.get("type", "any") if isinstance(p_info, dict) else "any"
                p_desc = p_info.get("description", "") if isinstance(p_info, dict) else ""
                req_marker = "required" if p_name in reqs else "optional"
                desc_str = f": {p_desc}" if p_desc else ""
                param_list.append(f"    * {p_name} ({p_type}, {req_marker}){desc_str}")
            params_str = ("\n" + "\n".join(param_list)) if param_list else " none"
            tool_docs.append(f"- Tool: `{name}`\n  Description: {desc}\n  Parameters:{params_str}")
        else:
            name = t.get("name", "tool")
            desc = t.get("description", "")
            tool_docs.append(f"- Tool: `{name}`: {desc}")

    tools_block = (
        "=== [AVAILABLE TOOLS] ===\n"
        "You have access to the following execution tools:\n\n"
        + "\n\n".join(tool_docs)
        + "\n\n"
        "=== [TOOL INVOCATION CONTRACT] ===\n"
        "To invoke one or more tools, you MUST emit an explicit <tool_call> block with JSON payload:\n"
        "<tool_call>\n"
        '{"name": "tool_name", "arguments": {"param1": "value1"}}\n'
        "</tool_call>\n\n"
        "Rules:\n"
        "1. Emit valid JSON inside <tool_call>...</tool_call> tags.\n"
        "2. You may emit multiple <tool_call> blocks in a single turn.\n"
        "3. You may include concise explanation before or between tool calls.\n"
        "4. Tool execution results will be provided to you as observation turns.\n"
        "5. When no more tool calls are required, provide your final response directly without <tool_call> tags.\n"
        "================================"
    )

    base = system_prompt.rstrip()
    return f"{base}\n\n{tools_block}"


def extract_tool_calls(text: str) -> List[Dict[str, Any]]:
    """
    Parse JSON tool calls from assistant text wrapped in <tool_call> tags.
    Robust against markdown code fences, leading text, and minor JSON malformations.
    Returns standardized OpenAI-compatible tool_calls list.
    """
    if not text or "<tool_call>" not in text:
        # Check for code fence block: ```tool_call ... ```
        if "```tool_call" not in text and "```tool" not in text:
            return []

    calls: List[Dict[str, Any]] = []

    # Primary regex: <tool_call>...</tool_call>
    matches = re.findall(r"<tool_call>\s*(.*?)\s*</tool_call>", text, flags=re.DOTALL | re.IGNORECASE)

    # Fallback regex for markdown code blocks: ```tool_call ... ```
    if not matches:
        matches = re.findall(r"```(?:tool_call|tool)\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)

    # Fallback for unclosed tag at end of text: <tool_call>\s*({.*})
    if not matches:
        unclosed = re.findall(r"<tool_call>\s*(\{.*)", text, flags=re.DOTALL | re.IGNORECASE)
        if unclosed:
            matches = unclosed

    for raw in matches:
        body = raw.strip()
        # Remove inner markdown code blocks if assistant emitted ```json ... ```
        if body.startswith("```"):
            body = re.sub(r"^```(?:json)?\s*", "", body, flags=re.IGNORECASE)
            body = re.sub(r"\s*```$", "", body)
            body = body.strip()

        data: Optional[Dict[str, Any]] = None
        try:
            data = json.loads(body)
        except Exception:
            # Clean common trailing commas or single quotes
            cleaned = re.sub(r",\s*([\]}])", r"\1", body)
            cleaned = re.sub(r"'([a-zA-Z0-9_]+)':", r'"\1":', cleaned)
            try:
                data = json.loads(cleaned)
            except Exception:
                # Regex heuristic extraction of name and arguments
                name_match = re.search(r'["\']?(?:name|tool|function)["\']?\s*:\s*["\']([^"\']+)["\']', body)
                if name_match:
                    fn_name = name_match.group(1).strip()
                    args_match = re.search(r'["\']?(?:arguments|parameters|args)["\']?\s*:\s*(\{.*\}|\[.*\])', body, re.DOTALL)
                    if args_match:
                        try:
                            fn_args = json.loads(args_match.group(1))
                        except Exception:
                            fn_args = {}
                    else:
                        fn_args = {}
                    data = {"name": fn_name, "arguments": fn_args}

        if isinstance(data, dict):
            fn_name = data.get("name") or data.get("tool") or data.get("function")
            if not fn_name:
                continue
            fn_args = data.get("arguments") or data.get("parameters") or data.get("args")
            if fn_args is None:
                fn_args = {k: v for k, v in data.items() if k not in ("name", "tool", "function")}
            if isinstance(fn_args, dict):
                args_str = json.dumps(fn_args, ensure_ascii=False)
            elif isinstance(fn_args, str):
                args_str = fn_args
            else:
                args_str = json.dumps(str(fn_args), ensure_ascii=False)

            call_id = f"call_{uuid.uuid4().hex[:8]}"
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": str(fn_name).strip(),
                    "arguments": args_str,
                },
            })

    return calls


def format_tool_observation(name: str, result: Any, call_id: Optional[str] = None) -> str:
    """
    Format execution outcome into structured observation text for user/assistant turn sequences.
    """
    if isinstance(result, str):
        res_text = result
    else:
        try:
            res_text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            res_text = str(result)

    id_attr = f' id="{call_id}"' if call_id else ""
    return f'<tool_observation name="{name}"{id_attr}>\n{res_text}\n</tool_observation>'


def format_tool_observation_message(name: str, result: Any, call_id: Optional[str] = None) -> Dict[str, str]:
    """
    Format tool observation into a user role message compatible with endpoints rejecting role 'tool'.
    """
    return {
        "role": "user",
        "content": format_tool_observation(name, result, call_id=call_id),
    }


def adapt_messages_for_prompt_tools(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Adapt full conversation turn history for prompt-based tool calling:
    1. Injects tool definitions into system prompt.
    2. Rewrites assistant tool_calls into explicit <tool_call> tags in content.
    3. Transforms role: 'tool' messages into role: 'user' observation turns.
    """
    adapted: List[Dict[str, Any]] = []

    for idx, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", "") or ""

        if role == "system":
            adapted.append({
                "role": "system",
                "content": inject_tool_prompt(content, tools),
            })

        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                call_blocks = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        parsed_args = raw_args
                    call_json = json.dumps({"name": fn_name, "arguments": parsed_args}, ensure_ascii=False, indent=2)
                    call_blocks.append(f"<tool_call>\n{call_json}\n</tool_call>")

                synth_calls = "\n\n".join(call_blocks)
                merged_content = f"{content}\n\n{synth_calls}".strip() if content else synth_calls
                adapted.append({
                    "role": "assistant",
                    "content": merged_content,
                })
            else:
                adapted.append({"role": "assistant", "content": content})

        elif role == "tool":
            fn_name = msg.get("name", "tool")
            cid = msg.get("tool_call_id")
            adapted.append({
                "role": "user",
                "content": format_tool_observation(fn_name, content, call_id=cid),
            })

        else:
            adapted.append(dict(msg))

    return adapted
