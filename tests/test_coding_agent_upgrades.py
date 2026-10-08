from unittest.mock import MagicMock, patch
import json
import os
import subprocess
import time
import pytest

from hydra_cli.agent import (
    HierarchicalScratchpad,
    SessionCheckpointer,
    _build_bounded_messages,
    classify_provider_output,
    print_unified_diff,
    retry_after_seconds,
    run_agent_loop,
    run_interactive_agent,
    wire_chars,
)
from hydra_cli.providers import ProviderError


def test_tool_adapter_fallback_on_unsupported_schema(tmp_path, capsys):
    test_file = tmp_path / "greeting.txt"
    test_file.write_text("Hello Sovereign Hydra\n", encoding="utf-8")

    mock_provider = [{"name": "MockEndpoint", "url": "https://api.mock.endpoint/v1/chat/completions", "headers": {}}]

    call_count = 0

    def mock_fetch(url, headers, payload, timeout=120):
        nonlocal call_count
        call_count += 1
        # Attempt 1: Provider rejects native tools in payload
        if "tools" in payload and call_count == 1:
            raise ProviderError("HTTP 400 from api.mock.endpoint: unrecognized field `tools`")

        # Attempt 2 (fallback to prompt adapter): payload has no tools key
        if call_count == 2:
            assert "tools" not in payload
            assert "[AVAILABLE TOOLS]" in payload["messages"][0]["content"]
            # Assistant returns prompt-based tool call
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                "I will read greeting.txt\n"
                                "<tool_call>\n"
                                '{"name": "read_file", "arguments": {"path": "greeting.txt"}}\n'
                                "</tool_call>"
                            ),
                        }
                    }
                ]
            }

        # Attempt 3: Assistant returns final answer after receiving tool observation
        if call_count == 3:
            assert "tools" not in payload
            # Verify tool observation was sent as role user
            user_obs = [m for m in payload["messages"] if m.get("role") == "user" and "<tool_observation" in m.get("content", "")]
            assert len(user_obs) >= 1
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "File greeting.txt contains: Hello Sovereign Hydra",
                        }
                    }
                ]
            }

        return {"choices": [{"message": {"role": "assistant", "content": "Done"}}]}

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=mock_fetch), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):

        res = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Read greeting.txt",
            cwd=str(tmp_path),
            enable_native_tools=True,
        )

        assert "Hello Sovereign Hydra" in res
        err_out = capsys.readouterr().err
        assert "Switching to prompt-based tool calling adapter" in err_out


def test_unified_diff_printing(capsys):
    old_content = "def add(a, b):\n    return a + b\n"
    new_content = "def add(a, b):\n    # Optimized\n    return a + b\n"

    print_unified_diff("math_util.py", old_content, new_content)
    err = capsys.readouterr().err
    assert "--- Diff: math_util.py ---" in err
    assert "+    # Optimized" in err


def test_diff_printing_in_agent_loop(tmp_path, capsys):
    app_file = tmp_path / "app.py"
    app_file.write_text("counter = 1\n", encoding="utf-8")

    mock_provider = [{"url": "https://api.mock/v1/chat/completions", "headers": {}}]

    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_edit",
                            "function": {
                                "name": "edit_file",
                                "arguments": json.dumps({"path": "app.py", "old_text": "counter = 1", "new_text": "counter = 99"}),
                            },
                        }
                    ],
                }
            }
        ]
    }
    turn2_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Edited app.py counter.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):

        res = run_agent_loop("sonnet 5.5", "Update counter", cwd=str(tmp_path))
        assert "Edited app.py" in res
        err_out = capsys.readouterr().err
        assert "--- Diff: app.py ---" in err_out
        assert "-counter = 1" in err_out
        assert "+counter = 99" in err_out


def test_repl_slash_commands_auth_diff_tokens_undo(tmp_path, capsys):
    # Setup git repo mock and inputs
    inputs = [
        "/auth",
        "/diff",
        "/tokens",
        "/undo",
        "/exit",
    ]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("hydra_cli.auth.auth_wizard", return_value=0) as mock_auth, \
         patch("subprocess.run") as mock_subproc:

        # Mock git diff and git status / checkout
        diff_res = MagicMock()
        diff_res.stdout = "diff --git a/foo.py b/foo.py\n+new line\n"
        diff_res.returncode = 0

        status_res = MagicMock()
        status_res.stdout = " M foo.py\n"
        status_res.returncode = 0

        checkout_res = MagicMock()
        checkout_res.stdout = ""
        checkout_res.returncode = 0

        def subproc_side_effect(cmd, **kwargs):
            if "diff" in cmd:
                return diff_res
            if "status" in cmd:
                return status_res
            if "checkout" in cmd:
                return checkout_res
            return MagicMock(stdout="", returncode=0)

        mock_subproc.side_effect = subproc_side_effect

        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0
        mock_auth.assert_called_once()

    out = capsys.readouterr().out
    assert "Active Session Context Size" in out
    assert "Context Window" in out
    assert "131,072 tokens" in out
    assert "Reverted last file change via git checkout: foo.py" in out


def test_repl_ctrl_c_handling(tmp_path, capsys):
    # Simulate single KeyboardInterrupt (warns), then instruction, then double KeyboardInterrupt (exits)
    state = {"count": 0}

    def input_mock(prompt):
        state["count"] += 1
        if state["count"] == 1:
            raise KeyboardInterrupt()
        elif state["count"] == 2:
            return "/exit"
        return "/exit"

    with patch("builtins.input", side_effect=input_mock):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "Ctrl+C interrupted. Press Ctrl+C again within 1.5s to exit REPL." in out


def test_repl_ctrl_c_during_agent_turn(tmp_path, capsys):
    # Simulate turn that raises KeyboardInterrupt, ensuring it aborts turn and doesn't crash REPL
    inputs = [
        "Long running task",
        "/exit",
    ]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("hydra_cli.agent.run_agent_loop", side_effect=KeyboardInterrupt()):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    err = capsys.readouterr().err
    assert "^C Agent turn aborted by user." in err


def test_bounded_messages_context_budget_compression():
    pad = HierarchicalScratchpad(task="Large context task")
    messages = [
        {"role": "system", "content": "SYSTEM PROMPT"},
        {"role": "user", "content": "USER TASK"},
    ]
    # Create large tool turns exceeding max_context_chars=10_000
    huge_content = "X" * 15_000
    turn_groups = [
        [
            {"role": "assistant", "content": None, "tool_calls": []},
            {"role": "tool", "content": huge_content},
        ]
        for _ in range(6)
    ]

    bounded = _build_bounded_messages(
        messages=messages,
        turn_groups=turn_groups,
        scratchpad=pad,
        max_history_turns=3,
        max_context_chars=10_000,
    )

    assert bounded[0]["content"] == "SYSTEM PROMPT"
    assert wire_chars(bounded) <= 10_000
    assert any("OUTPUT FITS WINDOW" in str(m.get("content") or "") for m in bounded)
    assert all(str(m.get("content") or "") != huge_content for m in bounded)
    assert turn_groups[0][1]["content"] == huge_content


def test_reasoning_trace_yields_before_tool_output():
    pad = HierarchicalScratchpad(task="trace")
    messages = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "TASK"},
    ]
    tool_text = "TOOL-OUTPUT-" + ("y" * 800)
    turn_groups = [[
        {"role": "assistant", "content": "calling", "reasoning_content": "R" * 4000},
        {"role": "tool", "tool_call_id": "c1", "content": tool_text},
    ]]
    bounded = _build_bounded_messages(
        messages=messages,
        turn_groups=turn_groups,
        scratchpad=pad,
        max_history_turns=2,
        max_context_chars=2_000,
    )
    assert wire_chars(bounded) <= 2_000
    reasoning = next(m.get("reasoning_content", "") for m in bounded if m.get("role") == "assistant" and m.get("reasoning_content"))
    tool = next(m["content"] for m in bounded if m.get("role") == "tool")
    assert "OUTPUT FITS WINDOW" in reasoning
    assert tool == tool_text
    assert turn_groups[0][0]["reasoning_content"] == "R" * 4000


def test_provider_output_actions():
    context = classify_provider_output(
        ProviderError("HTTP 400: Your input exceeds the context window of this model")
    )
    inflight = classify_provider_output(
        ProviderError('HTTP 402 {"reason":"in_flight_budget_exhausted","headers":{"Retry-After":"120"}}')
    )
    other = classify_provider_output(ProviderError("HTTP 500 from upstream"))
    assert context == "shrink"
    assert inflight == "retry_same"
    assert other == "failover"
    assert retry_after_seconds(
        ProviderError('HTTP 402 {"headers":{"Retry-After":"120"}}')
    ) == 120.0


def test_context_rejection_refits_same_provider(tmp_path):
    providers = [
        {"name": "OpenRouter", "url": "https://openrouter.ai/api/v1/chat/completions", "headers": {}},
        {"name": "Vercel AI Gateway", "url": "https://ai-gateway.vercel.sh/v1/chat/completions", "headers": {}},
    ]
    calls = []

    def fetch(url, headers, payload, timeout=120):
        calls.append(url)
        if len(calls) == 1:
            raise ProviderError("HTTP 400: Your input exceeds the context window of this model")
        return {"choices": [{"message": {"role": "assistant", "content": "fitted"}}]}

    with patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("hydra_cli.agent.get_frontier_providers", return_value=providers), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fetch):
        result = run_agent_loop("sol 6.1 high", "short task", session_id="fit-window", cwd=str(tmp_path))

    assert result == "fitted"
    assert calls == [
        "https://openrouter.ai/api/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
    ]


def test_inflight_budget_refits_same_provider(tmp_path):
    providers = [
        {"name": "OpenRouter", "url": "https://openrouter.ai/api/v1/chat/completions", "headers": {}},
        {"name": "Vercel AI Gateway", "url": "https://ai-gateway.vercel.sh/v1/chat/completions", "headers": {}},
    ]
    calls = []

    def fetch(url, headers, payload, timeout=120):
        calls.append(url)
        if len(calls) == 1:
            raise ProviderError(
                'HTTP 402 {"error":{"metadata":{"reason":"in_flight_budget_exhausted",'
                '"headers":{"Retry-After":"120"}}}}'
            )
        return {"choices": [{"message": {"role": "assistant", "content": "retried"}}]}

    with patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("hydra_cli.agent.get_frontier_providers", return_value=providers), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fetch), \
         patch("hydra_cli.agent.time.sleep") as sleep:
        result = run_agent_loop("sol 6.1", "short task", session_id="fit-budget", cwd=str(tmp_path))

    assert result == "retried"
    assert calls == [
        "https://openrouter.ai/api/v1/chat/completions",
        "https://openrouter.ai/api/v1/chat/completions",
    ]
    sleep.assert_called_once_with(20.0)
