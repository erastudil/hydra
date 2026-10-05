import pytest
from hydra_cli.config import (
    MODEL_MAP,
    FREE_MODELS,
    SWARM_HEADS,
    resolve_model,
    is_compound_alias,
)


def test_resolve_standard_aliases():
    assert resolve_model("opus 5.5") == "anthropic/claude-opus-5.5"
    assert resolve_model("opus") == "anthropic/claude-opus-5.5"
    assert resolve_model("sonnet 5.5") == "anthropic/claude-sonnet-5.5"
    assert resolve_model("sonnet") == "anthropic/claude-sonnet-5.5"
    assert resolve_model("sonnet 3.7") == "anthropic/claude-3.7-sonnet"
    assert resolve_model("haiku 4.5") == "anthropic/claude-haiku-4.5"
    assert resolve_model("fable 5.1") == "anthropic/claude-fable-5.1"
    assert resolve_model("sol 6.1") == "openai/gpt-6.1-sol"
    assert resolve_model("sol") == "openai/gpt-6.1-sol"
    assert resolve_model("luna") == "openai/gpt-6-luna"
    assert resolve_model("gemini 3.8") == "google/gemini-3.8-flash"
    assert resolve_model("gemini 3.5") == "google/gemini-3.5-flash"
    assert resolve_model("gemini 2.5") == "google/gemini-2.5-pro"
    assert resolve_model("qwen 3.8") == "qwen/qwen3.8-27b"
    assert resolve_model("qwen 3b") == "qwen/qwen-2.5-3b-instruct"
    assert resolve_model("grok 4.7") == "x-ai/grok-4.7"
    assert resolve_model("grok") == "x-ai/grok-4.7"
    assert resolve_model("llama 4") == "meta-llama/llama-4-maverick"
    assert resolve_model("llama") == "meta-llama/llama-3.3-70b-instruct"


def test_resolve_raw_model_fallback():
    raw_model = "meta-llama/llama-3.1-8b-instruct"
    assert resolve_model(raw_model) == raw_model


def test_is_compound_alias():
    assert is_compound_alias("opus", "5.5") is True
    assert is_compound_alias("sol", "6.1") is True
    assert is_compound_alias("sonnet", "5.5") is True
    assert is_compound_alias("sonnet", "3.7") is True
    assert is_compound_alias("haiku", "4.5") is True
    assert is_compound_alias("fable", "5.1") is True
    assert is_compound_alias("gemini", "3.8") is True
    assert is_compound_alias("gemini", "2.5") is True
    assert is_compound_alias("gemini", "3.5") is True
    assert is_compound_alias("qwen", "3.8") is True
    assert is_compound_alias("qwen", "3b") is True
    assert is_compound_alias("grok", "4.7") is True
    assert is_compound_alias("llama", "4") is True
    assert is_compound_alias("single", "token") is False


def test_free_models_exist():
    assert len(FREE_MODELS) > 0
    assert any("llama-3.3-70b-instruct:free" in m for m in FREE_MODELS)


def test_swarm_heads_defaults():
    assert "architect" in SWARM_HEADS
    assert "coder" in SWARM_HEADS
    assert "auditor" in SWARM_HEADS
    assert "synthesizer" in SWARM_HEADS
