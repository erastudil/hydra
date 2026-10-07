from unittest.mock import MagicMock, patch
import json
import os
import sys
import pytest

from hydra_cli.agent import (
    DEFAULT_AGENT_SYSTEM_PROMPT,
    run_agent_loop,
    run_interactive_agent,
)
from hydra_cli.config import DEFAULT_SYSTEM_PROMPT


def test_default_agent_system_prompt_contract():
    """Verify DEFAULT_AGENT_SYSTEM_PROMPT contains Cursor/Antigravity autonomous instructions."""
    assert DEFAULT_AGENT_SYSTEM_PROMPT is not None
    prompt = DEFAULT_AGENT_SYSTEM_PROMPT
    assert "Cursor" in prompt
    assert "Antigravity" in prompt
    # Multi-turn steps: inspect -> plan -> implement -> verify
    assert "Inspect" in prompt
    assert "Plan" in prompt
    assert "Implement" in prompt
    assert "Verify" in prompt
    # Required tools mentioned
    for tool_name in ["read_file", "edit_file", "write_file", "run_command", "grep_search", "find_files", "list_dir"]:
        assert tool_name in prompt
    # Verification and completion requirements
    assert "Exit code 0" in prompt or "exit code 0" in prompt.lower()
    assert "verifiable" in prompt.lower()


def test_telemetry_read_file(tmp_path, capsys):
    """Verify [HYDRA AGENT] Read: <path> (lines <start>-<end>) telemetry."""
    test_file = tmp_path / "sample.py"
    lines = [f"# line {i}\n" for i in range(1, 11)]
    test_file.write_text("".join(lines), encoding="utf-8")

    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.mock/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Let me read the file.",
                    "tool_calls": [
                        {
                            "id": "c_read",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "sample.py", "start_line": 2, "end_line": 5}),
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
                    "content": "Finished reading.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop("glm 5.3 flash", "Read sample", cwd=str(tmp_path))

    err = capsys.readouterr().err
    assert "[HYDRA AGENT] Read: sample.py (lines 2-5)" in err
    assert "Let me read the file." in err


def test_telemetry_run_command(tmp_path, capsys):
    """Verify [HYDRA AGENT] $ <command>, command output summary, and exit status."""
    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.mock/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_cmd",
                            "function": {
                                "name": "run_command",
                                "arguments": json.dumps({"command": "echo TelemetryTest"}),
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
                    "content": "Command finished.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop("glm 5.3 flash", "Execute command", cwd=str(tmp_path))

    err = capsys.readouterr().err
    assert "[HYDRA AGENT] $ echo TelemetryTest" in err
    assert "TelemetryTest" in err
    assert "Exit code: 0" in err


def test_telemetry_grep_and_find_and_list(tmp_path, capsys):
    """Verify grep_search, find_files, list_dir telemetry with query and match count."""
    (tmp_path / "hello.txt").write_text("Sovereign Agent\nSecond Line\n", encoding="utf-8")

    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.mock/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_grep",
                            "function": {
                                "name": "grep_search",
                                "arguments": json.dumps({"query": "Sovereign"}),
                            },
                        },
                        {
                            "id": "c_find",
                            "function": {
                                "name": "find_files",
                                "arguments": json.dumps({"pattern": "*.txt"}),
                            },
                        },
                        {
                            "id": "c_list",
                            "function": {
                                "name": "list_dir",
                                "arguments": json.dumps({"path": "."}),
                            },
                        },
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
                    "content": "All queries complete.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop("glm 5.3 flash", "Search workspace", cwd=str(tmp_path))

    err = capsys.readouterr().err
    assert "[HYDRA AGENT] Grep: 'Sovereign' in ." in err
    assert "[HYDRA AGENT] Find: '*.txt' in ." in err
    assert "[HYDRA AGENT] List: ." in err
    assert "Match count: 1" in err


def test_telemetry_invoke_subagent(tmp_path, capsys):
    """Verify invoke_subagent telemetry displaying child agent role and task."""
    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.mock/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c_sub",
                            "function": {
                                "name": "invoke_subagent",
                                "arguments": json.dumps({"alias": "coder", "prompt": "Audit verification module"}),
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
                    "content": "Subagent returned success.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("hydra_cli.native_tools.NativeToolRegistry.invoke_subagent", return_value="Subagent verified exit code 0"):
        run_agent_loop("glm 5.3 flash", "Dispatch subagent", cwd=str(tmp_path))

    err = capsys.readouterr().err
    assert "[HYDRA AGENT] Subagent [coder]: Audit verification module" in err


def test_telemetry_model_thought_and_preamble(tmp_path, capsys):
    """Verify printing model thought/reasoning_content and preamble before tool calls."""
    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.mock/v1/chat/completions", "headers": {}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "reasoning_content": "Chain-of-thought: verify preconditions before mutation.",
                    "content": "Pre-execution plan: listing directory to check context.",
                    "tool_calls": [
                        {
                            "id": "c_list",
                            "function": {
                                "name": "list_dir",
                                "arguments": json.dumps({"path": "."}),
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
                    "content": "Verification completed.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop("glm 5.3 flash", "Run plan", cwd=str(tmp_path))

    err = capsys.readouterr().err
    assert "[Thought] Chain-of-thought: verify preconditions before mutation." in err
    assert "Pre-execution plan: listing directory to check context." in err
    assert "[HYDRA AGENT] List: ." in err


def test_hydra_terminal_bat_invariants():
    """Verify launcher batch script contains all required configuration and launch sequences."""
    bat_path = r"C:\Users\jpm05\Documents\bin\hydra-terminal.bat"
    assert os.path.isfile(bat_path), f"Launcher script not found at {bat_path}"
    with open(bat_path, "r", encoding="utf-8") as f:
        bat_content = f.read()

    assert r'set "PYTHONPATH=C:\Users\jpm05\Documents\hydra;%PYTHONPATH%"' in bat_content
    assert r'cd /d "C:\Users\jpm05\Documents"' in bat_content
    assert 'python -m hydra_cli agent --model "glm 5.3 flash"' in bat_content
    assert 'cmd /k' in bat_content
    assert "doskey agent=" in bat_content


def test_telemetry_edit_file_and_diff(tmp_path, capsys):
    """Verify [HYDRA AGENT] Edit: <path> and colorized unified diff."""
    app_file = tmp_path / "calc.py"
    app_file.write_text("def mul(a, b):\n    return a * b\n", encoding="utf-8")

    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.mock/v1/chat/completions", "headers": {}}]
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
                                "arguments": json.dumps({
                                    "path": "calc.py",
                                    "old_text": "return a * b",
                                    "new_text": "# Fast multiply\n    return a * b",
                                }),
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
                    "content": "Updated calculation.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop("glm 5.3 flash", "Update mul", cwd=str(tmp_path))

    err = capsys.readouterr().err
    assert "[HYDRA AGENT] Edit: calc.py" in err
    assert "--- Diff: calc.py ---" in err
    assert "+    # Fast multiply" in err


def test_system_prompt_defaults_to_agent_prompt(tmp_path):
    """Verify run_agent_loop uses DEFAULT_AGENT_SYSTEM_PROMPT when system_prompt is None or default."""
    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.mock/v1/chat/completions", "headers": {}}]
    captured_payload = {}

    def mock_fetch(url, headers, payload, timeout=60):
        captured_payload.update(payload)
        return {"choices": [{"message": {"role": "assistant", "content": "Done", "tool_calls": None}}]}

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=mock_fetch), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        run_agent_loop("glm 5.3 flash", "Inspect workspace", system_prompt=None, cwd=str(tmp_path))

    sys_msg = [m for m in captured_payload["messages"] if m["role"] == "system"][0]["content"]
    assert "You are Hydra" in sys_msg and "an elite autonomous software engineering agent" in sys_msg
    assert "Cursor and Antigravity" in sys_msg


def test_glm_flash_tool_adapter_fallback_on_schema_error(tmp_path, capsys):
    """Verify GLM 5.3 Flash seamlessly falls back to prompt adapter when native tools fail."""
    test_file = tmp_path / "greeting.txt"
    test_file.write_text("Hello Sovereign Hydra\n", encoding="utf-8")

    # Turn 1: provider rejects native tool schema with HTTP 400
    # Turn 2: prompt adapter retry sends tools in system prompt, model responds with <tool_call>
    # Turn 3: final answer
    turn2_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": '<tool_call>\n{"name": "read_file", "arguments": {"path": "greeting.txt"}}\n</tool_call>',
                    "tool_calls": None,
                }
            }
        ]
    }
    turn3_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Greeting read successfully: Hello Sovereign Hydra",
                    "tool_calls": None,
                }
            }
        ]
    }

    mock_provider = [{"id": "cheaperinference", "name": "CheaperInference", "url": "https://api.cheaperinference.com/v1/chat/completions", "headers": {}}]
    err_msg = "HTTP 400: tools is not supported on this model endpoint"

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[Exception(err_msg), turn2_resp, turn3_resp]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):

        res = run_agent_loop(
            alias="glm 5.3 flash",
            prompt="Read greeting.txt",
            cwd=str(tmp_path),
            enable_native_tools=True,
        )

        assert "Hello Sovereign Hydra" in res
        err_out = capsys.readouterr().err
        assert "Switching to prompt-based tool calling adapter" in err_out


def test_interactive_agent_glm_flash_session(tmp_path, capsys):
    """Verify interactive agent REPL initializes with GLM 5.3 Flash and rules detection."""
    # Create AGENTS.md in tmp_path
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("topic : test\n\ninvariant : exit 0\n", encoding="utf-8")

    with patch("builtins.input", side_effect=["/exit"]), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        exit_code = run_interactive_agent(
            alias="glm 5.3 flash",
            cwd=str(tmp_path),
        )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "HYDRA CODING AGENT" in out
    assert "glm 5.3 flash" in out
    assert "AGENTS.md" in out
    assert "Exiting Hydra agent session" in out
