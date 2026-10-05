from unittest.mock import patch
import pytest

from hydra_cli.swarm import execute_swarm, run_single_head


@patch("hydra_cli.swarm.fetch_chat_completion")
def test_run_single_head(mock_fetch):
    mock_fetch.return_value = "Architectural blueprint: raft module with state machine replication."
    cfg = {
        "title": "Architect",
        "model": "anthropic/claude-opus-5.5",
        "system": "System architect system prompt."
    }
    provider = {
        "url": "https://api.test/v1",
        "headers": {"Authorization": "Bearer test"}
    }

    res = run_single_head("architect", cfg, "Build raft", provider)
    assert res.role == "architect"
    assert res.title == "Architect"
    assert "Architectural blueprint" in res.content
    assert res.error is None
    assert res.duration_sec >= 0


@patch("hydra_cli.swarm.get_frontier_providers")
@patch("hydra_cli.swarm.fetch_chat_completion")
def test_execute_swarm_parallel_heads(mock_fetch, mock_providers):
    mock_providers.return_value = [
        {
            "name": "OpenRouter",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "headers": {"Authorization": "Bearer test"},
        }
    ]
    mock_fetch.return_value = "Specialized head analysis completed."

    results = execute_swarm(
        task="Build zero-copy parser",
        heads=["architect", "coder"],
        json_output=True,
    )

    assert len(results) == 2
    roles = {r.role for r in results}
    assert "architect" in roles
    assert "coder" in roles
    for r in results:
        assert r.content == "Specialized head analysis completed."
