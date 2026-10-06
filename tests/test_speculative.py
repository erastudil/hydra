import json
import math
from unittest.mock import MagicMock, patch
import pytest

from hydra_cli.providers import ProviderError
from hydra_cli.router import route_command
from hydra_cli.speculative import (
    SpeculativeEngine,
    SpeculativeResult,
    compute_verification_score,
    detokenize,
    speculative_complete,
    tokenize,
    verify_candidate_tokens,
)


def test_tokenize_detokenize_invariants():
    test_cases = [
        "The quick brown fox jumps over the lazy dog.",
        "def compute_hash(data: bytes, salt: str = '0x123') -> str:\n    return sha256(data + salt.encode()).hexdigest()",
        "  leading and trailing whitespace   \n\twith newlines\n",
        "Unicode test: 3μm clad, 2260 kJ/kg, temperature >= 140°C",
        "",
    ]
    for text in test_cases:
        tokens = tokenize(text)
        assert detokenize(tokens) == text, f"Roundtrip failed for: {text!r}"


def test_verification_score_math():
    # Equal probabilities -> score == 0.0
    assert math.isclose(compute_verification_score(0.5, 0.5), 0.0, abs_tol=1e-6)

    # Target more likely -> positive score
    score_pos = compute_verification_score(0.8, 0.2)
    assert score_pos > 0.0
    assert math.isclose(score_pos, math.log(4.0), abs_tol=1e-6)

    # Target less likely -> negative score
    score_neg = compute_verification_score(0.1, 0.9)
    assert score_neg < 0.0

    # Zero probability boundaries do not throw ZeroDivisionError
    s_zero_tgt = compute_verification_score(0.0, 0.5)
    assert s_zero_tgt < -20.0
    s_zero_draft = compute_verification_score(0.5, 0.0)
    assert s_zero_draft > 20.0


def test_verify_candidate_tokens_full_acceptance():
    candidates = ["The", " ", "quick", " "]
    target_tokens = ["The", " ", "quick", " ", "brown", " ", "fox"]

    accepted, rejected, corrected = verify_candidate_tokens(candidates, target_tokens)
    assert accepted == ["The", " ", "quick", " "]
    assert rejected is None
    assert corrected == "brown"


def test_verify_candidate_tokens_partial_acceptance():
    candidates = ["The", " ", "fast", " "]
    target_tokens = ["The", " ", "slow", " ", "turtle"]

    accepted, rejected, corrected = verify_candidate_tokens(candidates, target_tokens)
    assert accepted == ["The", " "]
    assert rejected == "fast"
    assert corrected == "slow"


def test_verify_candidate_tokens_full_rejection():
    candidates = ["Alpha", " ", "Beta"]
    target_tokens = ["Omega", " ", "Psi"]

    accepted, rejected, corrected = verify_candidate_tokens(candidates, target_tokens)
    assert accepted == []
    assert rejected == "Alpha"
    assert corrected == "Omega"


def test_verify_candidate_tokens_target_exhausted():
    candidates = ["A", " ", "B", " ", "C"]
    target_tokens = ["A", " "]

    accepted, rejected, corrected = verify_candidate_tokens(candidates, target_tokens)
    assert accepted == ["A", " "]
    assert rejected == "B"
    assert corrected is None


@patch("hydra_cli.speculative.fetch_chat_completion")
def test_speculative_engine_step_acceptance(mock_fetch):
    # Call 1: Draft model generates K=4 tokens
    # Call 2: Target model verifies
    mock_fetch.side_effect = [
        "The quick ",  # draft: ['The', ' ', 'quick', ' ']
        "The quick brown fox jumps",  # target: ['The', ' ', 'quick', ' ', 'brown', ' ', 'fox', ' ', 'jumps']
    ]

    engine = SpeculativeEngine(
        draft_provider={"url": "http://127.0.0.1:11434/v1/chat/completions", "headers": {}},
        target_provider={"url": "https://api.openai.com/v1/chat/completions", "headers": {}},
        k=4,
    )

    res = engine.step([{"role": "user", "content": "Start phrase"}])

    assert isinstance(res, SpeculativeResult)
    assert res.accepted_tokens == ["The", " ", "quick", " "]
    assert res.rejected_token is None
    assert res.corrected_token == "brown"
    assert res.accepted_count == 4
    assert res.total_draft_tokens == 4
    assert res.fallback_used is False
    assert res.text == "The quick brown"
    assert res.acceptance_rate == 1.0


@patch("hydra_cli.speculative.fetch_chat_completion")
def test_speculative_engine_step_partial_rejection(mock_fetch):
    mock_fetch.side_effect = [
        "The fast race car",  # draft tokens: ['The', ' ', 'fast', ' ']
        "The slow turtle crawls",  # target tokens: ['The', ' ', 'slow', ' ', 'turtle']
    ]

    engine = SpeculativeEngine(
        draft_provider={"url": "http://127.0.0.1:11434/v1/chat/completions"},
        target_provider={"url": "https://api.openai.com/v1/chat/completions"},
        k=4,
    )

    res = engine.step([{"role": "user", "content": "Story"}])

    assert res.accepted_tokens == ["The", " "]
    assert res.rejected_token == "fast"
    assert res.corrected_token == "slow"
    assert res.accepted_count == 2
    assert res.fallback_used is False
    assert res.text == "The slow"


@patch("hydra_cli.speculative.fetch_chat_completion")
def test_speculative_engine_draft_failure_fallback(mock_fetch):
    # Draft throws connection error, target handles directly
    def side_effect(url, headers, model, messages, max_tokens=None, **kwargs):
        if "11434" in url:
            raise ProviderError("Connection refused to local draft daemon at 127.0.0.1:11434")
        return "Direct fallback response from target verifier."

    mock_fetch.side_effect = side_effect

    engine = SpeculativeEngine(
        draft_provider={"url": "http://127.0.0.1:11434/v1/chat/completions"},
        target_provider={"url": "https://api.openai.com/v1/chat/completions"},
        k=4,
    )

    res = engine.step([{"role": "user", "content": "Compute"}])

    assert res.fallback_used is True
    assert res.text == "Direct fallback response from target verifier."
    assert res.accepted_count == 0
    assert res.target_calls == 1


@patch("hydra_cli.speculative.fetch_chat_completion")
def test_speculative_engine_generate_loop(mock_fetch):
    # Iteration 1: draft: "Hello world ", target: "Hello world from Hydra"
    # Then target direct continuation
    mock_fetch.side_effect = [
        "Hello world ",
        "Hello world from Hydra speculative bridge.",
    ]

    engine = SpeculativeEngine(
        draft_provider={"url": "http://127.0.0.1:11434/v1/chat/completions"},
        target_provider={"url": "https://api.openai.com/v1/chat/completions"},
        k=3,
    )

    res = engine.generate(prompt="Greeting", max_tokens=10, max_iterations=2)

    assert isinstance(res, SpeculativeResult)
    assert "Hello world" in res.text
    assert res.total_draft_tokens >= 3
    assert res.accepted_count >= 3
    assert res.to_dict()["k"] == 3


@patch("hydra_cli.speculative.fetch_chat_completion")
def test_speculative_complete_function(mock_fetch):
    mock_fetch.side_effect = [
        "Speculative output ",
        "Speculative output verified.",
    ]

    res = speculative_complete(
        prompt="Test prompt",
        draft_model="local",
        target_model="anthropic/claude-sonnet-5.5",
        k=3,
        max_tokens=8,
    )
    assert isinstance(res, SpeculativeResult)
    assert "Speculative output" in res.text


@patch("hydra_cli.speculative.fetch_chat_completion")
def test_cli_speculative_command_json(mock_fetch, capsys):
    mock_fetch.side_effect = [
        "Candidate ",
        "Candidate accepted.",
    ]

    code = route_command([
        "speculative",
        "Test CLI dispatch",
        "--k", "2",
        "--json",
    ])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["k"] == 2
    assert "Candidate" in data["text"]


@patch("hydra_cli.speculative.fetch_chat_completion")
def test_cli_speculative_alias_flag(mock_fetch, capsys):
    mock_fetch.side_effect = [
        "Local draft ",
        "Local draft token stream.",
    ]

    code = route_command([
        "sonnet 5.5",
        "--speculative",
        "Explain quantum computing",
        "--k", "3",
    ])
    assert code == 0
    captured = capsys.readouterr()
    assert "[HYDRA SPECULATIVE]" in captured.out
    assert "Local draft" in captured.out
