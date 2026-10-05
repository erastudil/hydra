from unittest.mock import MagicMock, patch
import pytest

from hydra_cli.agent import run_agent_loop


def test_agent_loop_direct_response():
    # Model returns final response immediately without tool calls
    mock_provider = [{"url": "https://api.openai.com/v1/chat/completions", "headers": {"Authorization": "Bearer test"}}]
    mock_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "This is the final response.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=mock_resp):
        res = run_agent_loop("sonnet 5.5", "Hello")
        assert res == "This is the final response."


def test_agent_loop_with_tool_call():
    # Model returns tool call on turn 1, then final response on turn 2
    mock_provider = [{"url": "https://api.openai.com/v1/chat/completions", "headers": {"Authorization": "Bearer test"}}]
    
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "function": {
                                "name": "filesystem__read_file",
                                "arguments": '{"path": "test.txt"}',
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
                    "content": "File content read successfully: hello world",
                    "tool_calls": None,
                }
            }
        ]
    }

    mock_registry = MagicMock()
    mock_registry.get_openai_tools.return_value = [{"type": "function", "function": {"name": "filesystem__read_file"}}]
    mock_registry.dispatch.return_value = "hello world"

    with patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=[turn1_resp, turn2_resp]):
        res = run_agent_loop("sonnet 5.5", "Read test.txt", registry=mock_registry)
        assert res == "File content read successfully: hello world"
        mock_registry.dispatch.assert_called_once_with("filesystem__read_file", {"path": "test.txt"})
