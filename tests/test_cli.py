from unittest.mock import patch
import pytest

from hydra_cli.router import (
    format_combined_prompt,
    route_command,
)


def test_help_flags():
    assert route_command(["--help"]) == 0
    assert route_command(["-h"]) == 0
    assert route_command(["help"]) == 0


def test_version_flags():
    assert route_command(["--version"]) == 0
    assert route_command(["-v"]) == 0


def test_list_models():
    assert route_command(["--list-models"]) == 0


def test_combined_prompt_formatting():
    # Only user prompt
    assert format_combined_prompt("Hello", None) == "Hello"

    # Only piped input
    assert format_combined_prompt("", "Piped text") == "Piped text"

    # Both user prompt and piped input
    combined = format_combined_prompt("Analyze this", "Data 123")
    assert "[Piped Input]:\nData 123" in combined
    assert "[Instruction]:\nAnalyze this" in combined


def test_missing_prompt_returns_error(capsys):
    ret = route_command(["opus 5.5"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "No prompt or piped input" in err


@patch("hydra_cli.router.execute_summon")
def test_compound_alias_parsing(mock_summon):
    mock_summon.return_value = 0
    ret = route_command(["opus", "5.5", "explain quantum computing"])
    assert ret == 0
    mock_summon.assert_called_once()
    args, kwargs = mock_summon.call_args
    assert kwargs["alias"] == "opus 5.5"
    assert kwargs["prompt"] == "explain quantum computing"


@patch("hydra_cli.router.execute_summon")
def test_flags_parsing(mock_summon):
    mock_summon.return_value = 0
    ret = route_command([
        "sonnet", "refactor this code",
        "--system", "Be extremely concise.",
        "--temperature", "0.2",
        "--max-tokens", "1000",
        "--no-stream",
    ])
    assert ret == 0
    _, kwargs = mock_summon.call_args
    assert kwargs["alias"] == "sonnet"
    assert kwargs["prompt"] == "refactor this code"
    assert kwargs["system_prompt"] == "Be extremely concise."
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 1000
    assert kwargs["stream"] is False
