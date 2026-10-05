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

    assert [result.role for result in results] == ["architect", "coder", "synthesizer"]
    for result in results:
        assert result.content == "Specialized head analysis completed."
        assert result.to_dict()["status"] == "ok"
    synthesis_messages = mock_fetch.call_args.kwargs["messages"]
    assert "Specialized head analysis completed." in synthesis_messages[1]["content"]


@patch("hydra_cli.agent_runners.run_hermes")
def test_run_single_head_with_hermes_runner(mock_run_hermes):
    mock_run_hermes.return_value = "Hermes one-shot architectural output."
    cfg = {"title": "Architect (hermes)", "runner": "hermes"}
    res = run_single_head("architect:hermes", cfg, "Build distributed queue", provider=[])
    assert res.role == "architect:hermes"
    assert res.model == "hermes-agent"
    assert res.content == "Hermes one-shot architectural output."
    assert res.error is None
    mock_run_hermes.assert_called_once_with("Build distributed queue", timeout=180)


def test_head_config_runner_parsing():
    from hydra_cli.swarm import _head_config
    cfg = _head_config("coder:pi", None)
    assert cfg.get("runner") == "pi"
    assert "pi" in cfg.get("title", "").lower()

