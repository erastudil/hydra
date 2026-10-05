import pytest
import os

from hydra_cli.config import (
    MODEL_MAP,
    FREE_MODELS,
    SWARM_HEADS,
    consume_alias,
    load_dotenv,
    resolve_model,
    resolve_route,
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
    assert SWARM_HEADS["architect"]["effort"] == "high"
    assert SWARM_HEADS["auditor"]["reasoning_mode"] == "pro"


def test_reasoning_aliases():
    opus = resolve_route("opus 5.5 high")
    assert opus["model"] == "anthropic/claude-opus-5.5"
    assert opus["effort"] == "high"
    sol = resolve_route("sol 6.1 pro")
    assert sol["model"] == "openai/gpt-6.1-sol"
    assert sol["effort"] == "high"
    assert sol["reasoning_mode"] == "pro"
    assert resolve_model("llama 4 scout") == "meta-llama/llama-4-scout"
    assert resolve_model("sol 6.1 fast") == "openai/gpt-6.1-sol-fast"


def test_consume_alias_longest_and_separator():
    alias, rest = consume_alias(["sol", "6.1", "pro", "and", "con"])
    assert alias == "sol 6.1 pro"
    assert rest == ["and", "con"]
    alias, rest = consume_alias(["opus", "5.5", "--", "high", "ground"])
    assert alias == "opus 5.5"
    assert rest == ["high", "ground"]
    alias, rest = consume_alias(["llama", "4", "scout", "summarize"])
    assert alias == "llama 4 scout"
    assert rest == ["summarize"]


def test_project_env_skips_secrets(monkeypatch, tmp_path):
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("HYDRA_FREE_MODEL", None)
    env.pop("OLLAMA_HOST", None)
    env.pop("HYDRA_TRUST_CWD_ENV", None)
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    (home / ".hydra").mkdir(parents=True)
    (home / ".hydra" / ".env").write_text('OPENROUTER_API_KEY=from-home\n', encoding="utf-8")
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=from-cwd\nHYDRA_FREE_MODEL=demo-free\nOLLAMA_HOST=http://evil\n",
        encoding="utf-8",
    )
    original_expanduser = os.path.expanduser
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda path, _orig=original_expanduser: str(home) if path == "~" else _orig(path),
    )
    load_dotenv()
    assert os.environ["OPENROUTER_API_KEY"] == "from-home"
    assert os.environ["HYDRA_FREE_MODEL"] == "demo-free"
    assert "OLLAMA_HOST" not in os.environ
