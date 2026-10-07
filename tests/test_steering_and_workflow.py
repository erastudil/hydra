from unittest.mock import MagicMock, patch
import json
import os
import pytest

from hydra_cli.agent import (
    HydraReplCompleter,
    SessionCheckpointer,
    run_agent_loop,
    run_interactive_agent,
    setup_readline_completer,
)
from hydra_cli.router import route_command, HELP_BANNER


def test_steer_mode_tool_confirmation_accept(tmp_path):
    mock_provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "filesystem__read_file",
                                "arguments": '{"path": "sample.txt"}',
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
                    "content": "Sample file processed.",
                    "tool_calls": None,
                }
            }
        ]
    }

    mock_reg = MagicMock()
    mock_reg.get_openai_tools.return_value = [{"type": "function", "function": {"name": "filesystem__read_file"}}]
    mock_reg.dispatch.return_value = "sample content"

    inputs = ["y"]
    input_iter = iter(inputs)

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("builtins.input", lambda _: next(input_iter)):

        res = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Read sample.txt",
            registry=mock_reg,
            steer_mode=True,
            cwd=str(tmp_path),
        )

        assert res == "Sample file processed."
        mock_reg.dispatch.assert_called_once_with("filesystem__read_file", {"path": "sample.txt"})


def test_steer_mode_tool_confirmation_skip(tmp_path):
    mock_provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_skip",
                            "function": {
                                "name": "filesystem__read_file",
                                "arguments": '{"path": "forbidden.txt"}',
                            },
                        }
                    ],
                }
            }
        ]
    }

    observed_messages = []

    def mock_fetch(url, headers, payload, timeout=120):
        if len(payload["messages"]) > 2:
            observed_messages.extend(payload["messages"])
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Skipped forbidden file cleanly.",
                            "tool_calls": None,
                        }
                    }
                ]
            }
        return turn1_resp

    mock_reg = MagicMock()
    mock_reg.get_openai_tools.return_value = [{"type": "function", "function": {"name": "filesystem__read_file"}}]

    # Enter 'n' at confirmation prompt
    inputs = ["n"]
    input_iter = iter(inputs)

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=mock_fetch), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("builtins.input", lambda _: next(input_iter)):

        res = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Read forbidden.txt",
            registry=mock_reg,
            steer_mode=True,
            cwd=str(tmp_path),
        )

        assert res == "Skipped forbidden file cleanly."
        mock_reg.dispatch.assert_not_called()
        # Verify observation tells model it was skipped
        tool_obs = [m for m in observed_messages if m.get("role") == "tool"]
        assert len(tool_obs) >= 1
        assert "[Tool execution skipped by developer]" in tool_obs[0]["content"]


def test_steer_mode_tool_confirmation_steer(tmp_path):
    mock_provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_steer",
                            "function": {
                                "name": "filesystem__read_file",
                                "arguments": '{"path": "initial.txt"}',
                            },
                        }
                    ],
                }
            }
        ]
    }

    observed_messages = []

    def mock_fetch(url, headers, payload, timeout=120):
        if len(payload["messages"]) > 2:
            observed_messages.extend(payload["messages"])
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Redirected according to steering directive.",
                            "tool_calls": None,
                        }
                    }
                ]
            }
        return turn1_resp

    mock_reg = MagicMock()
    mock_reg.get_openai_tools.return_value = [{"type": "function", "function": {"name": "filesystem__read_file"}}]

    # Select 's' (steer), then enter steering instruction
    inputs = ["s", "Target alternative.txt instead"]
    input_iter = iter(inputs)

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=mock_fetch), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("builtins.input", lambda _: next(input_iter)):

        res = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Analyze initial.txt",
            registry=mock_reg,
            steer_mode=True,
            cwd=str(tmp_path),
        )

        assert res == "Redirected according to steering directive."
        mock_reg.dispatch.assert_not_called()
        # Verify steering directive injected as user message
        user_msgs = [m for m in observed_messages if m.get("role") == "user"]
        assert any("[IN-FLIGHT STEERING DIRECTIVE]: Target alternative.txt instead" in m.get("content", "") for m in user_msgs)


def test_steer_mode_tool_confirmation_abort(tmp_path):
    mock_provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_abort",
                            "function": {
                                "name": "dangerous_tool",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }

    mock_reg = MagicMock()
    mock_reg.get_openai_tools.return_value = [{"type": "function", "function": {"name": "dangerous_tool"}}]

    inputs = ["q"]
    input_iter = iter(inputs)

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=turn1_resp), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("builtins.input", lambda _: next(input_iter)):

        res = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Run dangerous tool",
            registry=mock_reg,
            steer_mode=True,
            cwd=str(tmp_path),
        )

        assert res == "[Turn aborted by developer]"
        mock_reg.dispatch.assert_not_called()


def test_ctrl_c_during_complete_turn_with_directive(tmp_path):
    mock_provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    turn2_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Handled after interrupt.",
                    "tool_calls": None,
                }
            }
        ]
    }

    call_count = 0
    observed_payloads = []

    def mock_fetch(url, headers, payload, timeout=120):
        nonlocal call_count
        call_count += 1
        observed_payloads.append(payload)
        if call_count == 1:
            raise KeyboardInterrupt()
        return turn2_resp

    inputs = ["Pivot to security scan"]
    input_iter = iter(inputs)

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=mock_fetch), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("builtins.input", lambda _: next(input_iter)):

        res = run_agent_loop(alias="sonnet 5.5", prompt="Run long job", cwd=str(tmp_path))

        assert res == "Handled after interrupt."
        # Verify second completion payload contains steering directive
        second_msgs = observed_payloads[1]["messages"]
        assert any("[IN-FLIGHT STEERING DIRECTIVE]: Pivot to security scan" in m.get("content", "") for m in second_msgs)


def test_ctrl_c_during_complete_turn_empty_abort(tmp_path):
    mock_provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]

    def mock_fetch(url, headers, payload, timeout=120):
        raise KeyboardInterrupt()

    inputs = [""]  # Enter without typing instruction -> abort
    input_iter = iter(inputs)

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=mock_fetch), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("builtins.input", lambda _: next(input_iter)):

        res = run_agent_loop(alias="sonnet 5.5", prompt="Run task", cwd=str(tmp_path))
        assert res == "[Turn aborted by developer]"


def test_ctrl_c_during_tool_execution(tmp_path):
    mock_provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_slow",
                            "function": {
                                "name": "slow_network_fetch",
                                "arguments": "{}",
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
                    "content": "Recovered with steering directive.",
                    "tool_calls": None,
                }
            }
        ]
    }

    mock_reg = MagicMock()
    mock_reg.get_openai_tools.return_value = [{"type": "function", "function": {"name": "slow_network_fetch"}}]
    # Tool execution raises KeyboardInterrupt
    mock_reg.dispatch.side_effect = KeyboardInterrupt()

    inputs = ["Cancel network fetch and use cached file"]
    input_iter = iter(inputs)

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("builtins.input", lambda _: next(input_iter)):

        res = run_agent_loop(
            alias="sonnet 5.5",
            prompt="Fetch remote",
            registry=mock_reg,
            cwd=str(tmp_path),
        )

        assert res == "Recovered with steering directive."


def test_repl_steer_command(tmp_path, capsys):
    inputs = [
        "/steer status",
        "/steer on",
        "/steer status",
        "/steer off",
        "/steer",
        "/steer",
        "/exit",
    ]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda _: next(input_iter)):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "Steer mode is currently disabled" in out
    assert "Steer mode enabled (step-by-step confirmation on)" in out
    assert "Steer mode is currently enabled" in out
    assert "Steer mode disabled" in out
    assert "Steer mode toggled: enabled" in out
    assert "Steer mode toggled: disabled" in out


def test_repl_skip_command(tmp_path, capsys):
    inputs = [
        "/skip",
        "/exit",
    ]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda _: next(input_iter)):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "Next tool execution set to skipped" in out


def test_repl_retry_command(tmp_path, capsys):
    inputs = [
        "/retry",  # Initially empty history
        "First step",
        "/retry with safety checks",
        "/exit",
    ]
    input_iter = iter(inputs)

    call_prompts = []

    def mock_run_agent(prompt, **kwargs):
        call_prompts.append(prompt)
        return f"Completed: {prompt[:30]}"

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("hydra_cli.agent.run_agent_loop", side_effect=mock_run_agent):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "No previous turn to retry in active session" in out
    assert "Retrying previous turn with: First step" in out
    assert len(call_prompts) == 2
    assert call_prompts[0] == "First step"
    assert "First step" in call_prompts[1]
    assert "[Additional Guidance]: with safety checks" in call_prompts[1]


def test_repl_context_and_dump_command(tmp_path, capsys):
    inputs = [
        "/context",
        "/context dump",
        "/exit",
    ]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda _: next(input_iter)):
        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

    out = capsys.readouterr().out
    assert "Active Session Context Size" in out
    assert "Current Bounded Messages Payload Dump" in out
    assert '"role": "system"' in out


def test_repl_auth_status_command(tmp_path, capsys):
    inputs = [
        "/auth status",
        "/exit",
    ]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("hydra_cli.auth.print_auth_status") as mock_auth_status, \
         patch("hydra_cli.auth.auth_wizard") as mock_wizard:

        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

        mock_auth_status.assert_called_once()
        mock_wizard.assert_not_called()


def test_repl_undo_with_specific_file(tmp_path, capsys):
    inputs = [
        "/undo specific_module.py",
        "/exit",
    ]
    input_iter = iter(inputs)

    with patch("builtins.input", lambda _: next(input_iter)), \
         patch("subprocess.run") as mock_subproc:

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_subproc.return_value = mock_res

        exit_code = run_interactive_agent(cwd=str(tmp_path))
        assert exit_code == 0

        # Verify git checkout -- specific_module.py was invoked
        mock_subproc.assert_called_once()
        call_args = mock_subproc.call_args[0][0]
        assert call_args == ["git", "checkout", "--", "specific_module.py"]

    out = capsys.readouterr().out
    assert "Reverted file change via git checkout: specific_module.py" in out


def test_readline_completer_matching():
    completer = HydraReplCompleter(model_aliases=["opus 5.5", "sonnet 5.5", "gemini 3.8"])

    # Slash command prefixes
    assert "/steer" in completer.get_candidates("/st")
    assert "/status" in completer.get_candidates("/st")
    assert "/skip" in completer.get_candidates("/sk")
    assert "/context" in completer.get_candidates("/co")
    assert "/retry" in completer.get_candidates("/re")

    # Model alias completion after /model
    model_cands = completer.get_candidates("son", "/model son")
    assert "sonnet 5.5" in model_cands

    # Tier completion after /tier
    tier_cands = completer.get_candidates("fr", "/tier fr")
    assert "free" in tier_cands
    assert "frontier" in tier_cands

    # Steer options completion after /steer
    steer_cands = completer.get_candidates("o", "/steer o")
    assert "on" in steer_cands
    assert "off" in steer_cands

    # Auth options after /auth
    assert completer.get_candidates("s", "/auth s") == ["status"]

    # Context options after /context
    assert completer.get_candidates("d", "/context d") == ["dump"]

    # Readline stateful interface complete(text, state)
    first = completer.complete("/sk", 0)
    assert first == "/skip"
    assert completer.complete("/sk", 1) == "/skills"
    assert completer.complete("/sk", 2) is None


def test_router_steer_flag():
    # 1. Test agent with --steer flag
    with patch("hydra_cli.router.execute_agent_mode", return_value=0) as mock_agent:
        ret = route_command(["agent", "--steer", "Review PR"])
        assert ret == 0
        mock_agent.assert_called_once()
        assert mock_agent.call_args.kwargs["steer_mode"] is True
        assert mock_agent.call_args.kwargs["prompt"] == "Review PR"

    # 2. Test interactive agent with --steer
    with patch("hydra_cli.router.execute_agent_mode", return_value=0) as mock_agent:
        ret = route_command(["agent", "--steer"])
        assert ret == 0
        mock_agent.assert_called_once()
        assert mock_agent.call_args.kwargs["steer_mode"] is True
        assert mock_agent.call_args.kwargs["interactive"] is True

    # 3. Help banner includes steering documentation
    assert "--steer" in HELP_BANNER
    assert "agent --steer" in HELP_BANNER