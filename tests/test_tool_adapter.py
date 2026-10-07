import json
import pytest

from hydra_cli.tool_adapter import (
    adapt_messages_for_prompt_tools,
    extract_tool_calls,
    format_tool_observation,
    format_tool_observation_message,
    inject_tool_prompt,
    is_tool_unsupported_error,
)


def test_inject_tool_prompt():
    base_sys = "You are a sovereign engineer."
    # Empty tools -> unchanged
    assert inject_tool_prompt(base_sys, []) == base_sys

    sample_tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file contents",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to file"},
                        "max_bytes": {"type": "integer", "description": "Max bytes"},
                    },
                    "required": ["path"],
                },
            },
        }
    ]

    injected = inject_tool_prompt(base_sys, sample_tools)
    assert "[AVAILABLE TOOLS]" in injected
    assert "[TOOL INVOCATION CONTRACT]" in injected
    assert "read_file" in injected
    assert "path (string, required)" in injected
    assert "max_bytes (integer, optional)" in injected
    assert "<tool_call>" in injected

    # Calling again does not duplicate
    re_injected = inject_tool_prompt(injected, sample_tools)
    assert re_injected.count("[AVAILABLE TOOLS]") == 1


def test_extract_tool_calls():
    # 1. Standard single call
    text1 = 'I will inspect the file.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "main.py"}}\n</tool_call>'
    calls1 = extract_tool_calls(text1)
    assert len(calls1) == 1
    assert calls1[0]["function"]["name"] == "read_file"
    args1 = json.loads(calls1[0]["function"]["arguments"])
    assert args1 == {"path": "main.py"}
    assert calls1[0]["id"].startswith("call_")

    # 2. Multiple calls in one turn
    text2 = """
    <tool_call>
    {"name": "read_file", "arguments": {"path": "a.txt"}}
    </tool_call>
    Some intermediate reasoning.
    <tool_call>
    {"name": "write_file", "arguments": {"path": "b.txt", "content": "hello"}}
    </tool_call>
    """
    calls2 = extract_tool_calls(text2)
    assert len(calls2) == 2
    assert calls2[0]["function"]["name"] == "read_file"
    assert calls2[1]["function"]["name"] == "write_file"

    # 3. Call with inner markdown code block
    text3 = '<tool_call>\n```json\n{"name": "edit_file", "arguments": {"path": "x.py", "old_text": "1", "new_text": "2"}}\n```\n</tool_call>'
    calls3 = extract_tool_calls(text3)
    assert len(calls3) == 1
    assert calls3[0]["function"]["name"] == "edit_file"

    # 4. Fallback code fence without tags
    text4 = '```tool_call\n{"name": "find_files", "arguments": {"pattern": "*.py"}}\n```'
    calls4 = extract_tool_calls(text4)
    assert len(calls4) == 1
    assert calls4[0]["function"]["name"] == "find_files"

    # 5. Fault tolerant JSON recovery (trailing comma, single quotes)
    text5 = "<tool_call>\n{'name': 'list_dir', 'arguments': {'path': 'src',}}\n</tool_call>"
    calls5 = extract_tool_calls(text5)
    assert len(calls5) == 1
    assert calls5[0]["function"]["name"] == "list_dir"

    # 6. No calls in normal conversational response
    assert extract_tool_calls("Here is the completed solution.") == []


def test_format_tool_observation():
    # String result
    obs1 = format_tool_observation("read_file", "hello world", call_id="c123")
    assert '<tool_observation name="read_file" id="c123">' in obs1
    assert "hello world" in obs1

    # Dict result
    obs2 = format_tool_observation("stat", {"size": 42, "exists": True})
    assert '<tool_observation name="stat">' in obs2
    assert '"size": 42' in obs2

    # Message format
    msg = format_tool_observation_message("read_file", "data 100")
    assert msg["role"] == "user"
    assert "data 100" in msg["content"]
    assert '<tool_observation name="read_file">' in msg["content"]


def test_is_tool_unsupported_error():
    assert is_tool_unsupported_error(Exception("tools is not supported on this model")) is True
    assert is_tool_unsupported_error(Exception("Invalid value for role: tool")) is True
    assert is_tool_unsupported_error(Exception("HTTP 400 from api.cloudflare.com: unrecognized field `tools`")) is True
    assert is_tool_unsupported_error(Exception("HTTP 422 Unprocessable Entity: tool_choice is invalid")) is True
    assert is_tool_unsupported_error(Exception("Functions are not supported by the endpoint")) is True

    # Standard unrelated errors return False
    assert is_tool_unsupported_error(Exception("Connection timed out")) is False
    assert is_tool_unsupported_error(Exception("HTTP 500 Internal Server Error")) is False
    assert is_tool_unsupported_error(None) is False


def test_adapt_messages_for_prompt_tools():
    tools = [
        {"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {}}}
    ]
    raw_messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Inspect code"},
        {
            "role": "assistant",
            "content": "Let me read the file.",
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "read_file",
            "content": "print('ok')",
        },
    ]

    adapted = adapt_messages_for_prompt_tools(raw_messages, tools)
    assert len(adapted) == 4
    # 1. System prompt has tools injected
    assert "[AVAILABLE TOOLS]" in adapted[0]["content"]
    # 2. Assistant message has <tool_call> in content and no tool_calls key
    assert "<tool_call>" in adapted[2]["content"]
    assert "tool_calls" not in adapted[2]
    # 3. Role tool is converted to role user with <tool_observation>
    assert adapted[3]["role"] == "user"
    assert "<tool_observation name=\"read_file\"" in adapted[3]["content"]
