from unittest.mock import MagicMock, patch
import json
import os
import pytest

from hydra_cli.agent import (
    HierarchicalScratchpad,
    SessionCheckpointer,
    _build_bounded_messages,
    run_agent_loop,
)


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


def test_hierarchical_scratchpad():
    pad = HierarchicalScratchpad(task="Refactor parser")
    assert pad.task == "Refactor parser"
    assert pad.status == "PLANNING"

    pad.hypotheses.append("AST visitor pattern will decouple grammar from semantics")
    pad.record_tool_invocation("read_file", {"path": "parser.py"}, "class Parser: pass", is_error=False)

    ctx = pad.format_for_context()
    assert "[WORKING MEMORY SCRATCHPAD]" in ctx
    assert "Refactor parser" in ctx
    assert "AST visitor pattern" in ctx
    assert "read_file" in ctx

    # Test serialization round-trip
    dumped = pad.to_dict()
    restored = HierarchicalScratchpad.from_dict(dumped)
    assert restored.task == pad.task
    assert restored.hypotheses == pad.hypotheses
    assert restored.entity_graph == pad.entity_graph


def test_session_checkpointer_lifecycle(tmp_path):
    with patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        cp = SessionCheckpointer(
            session_id="test_sess_001",
            alias="sonnet 5.5",
            model="anthropic/claude-sonnet-5.5",
            task="Audit code security",
            max_turns=10,
        )
        assert cp.state == "INIT"
        cp.transition("PLANNING")
        assert cp.state == "PLANNING"

        # Record a turn
        cp.record_turn(
            turn_number=1,
            assistant_msg={"role": "assistant", "content": "Checking secrets."},
            tool_executions=[{"name": "grep", "arguments": {"pattern": "KEY"}, "result": "none", "is_error": False}],
        )
        assert os.path.isfile(cp.file_path)

        # Load session and verify state
        loaded = SessionCheckpointer.load("test_sess_001")
        assert loaded is not None
        assert loaded.session_id == "test_sess_001"
        assert loaded.alias == "sonnet 5.5"
        assert loaded.task == "Audit code security"
        assert loaded.turn == 1
        assert len(loaded.turns) == 1
        assert loaded.turns[0]["tool_executions"][0]["name"] == "grep"


def test_build_bounded_messages():
    pad = HierarchicalScratchpad(task="Test bounding")
    pad.working_notes.append("Step 1 done")

    messages = [
        {"role": "system", "content": "SYSTEM_PROMPT"},
        {"role": "user", "content": "USER_TASK"},
    ]
    turn_groups = []

    # Under threshold (max_history_turns=2)
    bounded = _build_bounded_messages(messages, turn_groups, pad, max_history_turns=2)
    assert len(bounded) == 2

    # Add 3 turn groups
    for i in range(1, 4):
        asst = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c_{i}"}]}
        tool = {"role": "tool", "tool_call_id": f"c_{i}", "content": f"res_{i}"}
        messages.extend([asst, tool])
        turn_groups.append([asst, tool])

    # With max_history_turns=2, should compact turn 1 and keep turn 2 & 3
    bounded_pruned = _build_bounded_messages(messages, turn_groups, pad, max_history_turns=2)
    # [system, user, compact_asst, compact_user, asst_2, tool_2, asst_3, tool_3]
    assert len(bounded_pruned) == 8
    assert bounded_pruned[0]["content"] == "SYSTEM_PROMPT"
    assert bounded_pruned[1]["content"] == "USER_TASK"
    assert "[ACTIVE WORKING MEMORY" in bounded_pruned[2]["content"]
    assert bounded_pruned[3]["role"] == "user"
    assert bounded_pruned[4]["tool_calls"][0]["id"] == "c_2"
    assert bounded_pruned[6]["tool_calls"][0]["id"] == "c_3"


def test_agent_loop_saves_and_resumes_session(tmp_path):
    mock_provider = [{"url": "https://api.openai.com/v1/chat/completions", "headers": {"Authorization": "Bearer test"}}]
    turn1_resp = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Mission accomplished.",
                    "tool_calls": None,
                }
            }
        ]
    }

    with patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)), \
         patch("hydra_cli.agent.get_frontier_providers", return_value=mock_provider), \
         patch("hydra_cli.agent._fetch_raw_completion", return_value=turn1_resp):
        res = run_agent_loop("sonnet 5.5", "Run task", session_id="custom_sess_42")
        assert res == "Mission accomplished."

        # Verify session file was created and is completed
        sess_file = tmp_path / "sessions" / "custom_sess_42.json"
        assert sess_file.is_file()
        with open(sess_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["session_id"] == "custom_sess_42"
        assert data["state"] == "COMPLETED"
        assert data["final_response"] == "Mission accomplished."
