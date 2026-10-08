import pytest
import os

from hydra_cli.config import (
    MODEL_MAP,
    FREE_MODELS,
    SWARM_HEADS,
    consume_alias,
    load_dotenv,
    get_context_window,
    input_char_budget,
    resolve_model,
    resolve_route,
    schema_chars,
    is_compound_alias,
)


def test_resolve_standard_aliases():
    assert resolve_model("opus 5.5") == "anthropic/claude-opus-5.5"
    assert resolve_model("opus") == "anthropic/claude-opus-5.5"
    assert resolve_model("sonnet 5.5") == "anthropic/claude-sonnet-5.5"
    assert resolve_model("sonnet") == "anthropic/claude-sonnet-5.5"
    assert resolve_model("haiku 4.5") == "anthropic/claude-haiku-4.5"
    assert resolve_model("fable 5.1") == "anthropic/claude-fable-5.1"
    assert resolve_model("sol 6.1") == "openai/gpt-6.1-sol"
    assert resolve_model("sol") == "openai/gpt-6.1-sol"
    assert resolve_model("luna") == "openai/gpt-6-luna"
    assert resolve_model("gemini 3.8") == "google/gemini-3.8-flash"
    assert resolve_model("gemini 3.5") == "google/gemini-3.5-flash"
    assert resolve_model("gemini 2.5") == "google/gemini-2.5-pro"
    assert resolve_model("qwen 3.8") == "qwen/qwen3.8-27b"
    assert resolve_model("grok 4.7") == "x-ai/grok-4.7"
    assert resolve_model("grok") == "x-ai/grok-4.7"
    assert resolve_model("llama 4") == "meta-llama/llama-4-maverick"
    assert resolve_model("llama") == "meta-llama/llama-3.3-70b-instruct"
    assert resolve_model("glm 5.3") == "glm-5.3"
    assert resolve_model("glm 5.3 prime") == "glm-5.3-prime"
    assert resolve_model("glm") == "glm-5.3"
    assert resolve_model("glm 5") == "glm-5.3"
    assert resolve_model("glm 4.7") == "glm-4.7"
    assert resolve_model("glm 4.7 flash") == "glm-4.7-flash"


def test_resolve_raw_model_fallback():
    raw_model = "meta-llama/llama-3.1-8b-instruct"
    assert resolve_model(raw_model) == raw_model


def test_is_compound_alias():
    assert is_compound_alias("opus", "5.5") is True
    assert is_compound_alias("sol", "6.1") is True
    assert is_compound_alias("sonnet", "5.5") is True
    assert is_compound_alias("haiku", "4.5") is True
    assert is_compound_alias("fable", "5.1") is True
    assert is_compound_alias("gemini", "3.8") is True
    assert is_compound_alias("gemini", "2.5") is True
    assert is_compound_alias("gemini", "3.5") is True
    assert is_compound_alias("qwen", "3.8") is True
    assert is_compound_alias("grok", "4.7") is True
    assert is_compound_alias("llama", "4") is True
    assert is_compound_alias("glm", "5.3") is True
    assert is_compound_alias("glm", "5") is True
    assert is_compound_alias("glm", "4.7") is True
    assert is_compound_alias("single", "token") is False


def test_free_models_exist():
    assert len(FREE_MODELS) > 0
    assert all(m.endswith(":free") for m in FREE_MODELS)
    assert "google/gemma-4-26b-a4b-it:free" in FREE_MODELS


def test_removed_dead_aliases():
    # These model ids exist on neither OpenRouter nor the Vercel AI Gateway.
    for alias in ("sonnet 3.7", "grok 2", "qwen 3b", "kolibri", "alice-emap"):
        assert alias not in MODEL_MAP
    assert "Aleph-Alpha/Kolibri-1" not in MODEL_MAP.values()
    assert "Bluebarrels/alice-emap-adapter" not in MODEL_MAP.values()


def test_single_provider_models_are_marked():
    assert resolve_route("opus 5.5 fast")["providers"] == ["vercel"]
    assert resolve_route("sol 6.1 fast")["providers"] == ["vercel"]
    assert resolve_route("glm")["providers"] == ["cheaperinference"]
    assert resolve_route("sonnet 5.5")["providers"] == []
    assert resolve_route("llama 3.1 8b")["providers"] == ["huggingface"]
    # Raw model ids pick up the same limits.
    assert resolve_route("openai/gpt-6.1-sol-fast")["providers"] == ["vercel"]


def test_current_gateway_aliases():
    assert resolve_model("sol 5.6") == "openai/gpt-5.6-sol"
    assert resolve_model("gpt-5.6") == "openai/gpt-5.6-sol"
    assert resolve_model("muse spark 1.3") == "meta/muse-spark-1.3"
    assert resolve_route("sol 5.6 pro")["reasoning_mode"] == "pro"


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
    glm = resolve_route("glm 5.3")
    assert glm["model"] == "glm-5.3"
    assert glm["effort"] == "high"
    glm_prime = resolve_route("glm 5.3 prime")
    assert glm_prime["model"] == "glm-5.3-prime"
    assert glm_prime["effort"] == "high"
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
    alias, rest = consume_alias(["glm", "5.3", "prime", "execute"])
    assert alias == "glm 5.3 prime"
    assert rest == ["execute"]
    alias, rest = consume_alias(["glm", "4.7", "flash", "run"])
    assert alias == "glm 4.7 flash"
    assert rest == ["run"]
    alias, rest = consume_alias(["glm", "translate"])
    assert alias == "glm"
    assert rest == ["translate"]


def test_project_env_skips_secrets(monkeypatch, tmp_path):
    env = os.environ.copy()
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("HYDRA_FREE_MODEL", None)
    env.pop("OLLAMA_HOST", None)
    env.pop("HYDRA_TRUST_CWD_ENV", None)
    env.pop("CHEAPERINFERENCE_API_KEY", None)
    env.pop("RUNPOD_ENDPOINT_ID", None)
    env.pop("MODAL_ENDPOINT_URL", None)
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    (home / ".hydra").mkdir(parents=True)
    (home / ".hydra" / ".env").write_text('OPENROUTER_API_KEY=from-home\n', encoding="utf-8")
    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=from-cwd\n"
        "HYDRA_FREE_MODEL=demo-free\n"
        "OLLAMA_HOST=http://evil\n"
        "CHEAPERINFERENCE_API_KEY=ci-evil-key\n"
        "RUNPOD_ENDPOINT_ID=evil-pod\n"
        "MODAL_ENDPOINT_URL=https://evil.modal.run\n",
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
    assert "CHEAPERINFERENCE_API_KEY" not in os.environ
    assert "RUNPOD_ENDPOINT_ID" not in os.environ
    assert "MODAL_ENDPOINT_URL" not in os.environ


def test_sensitive_env_keys():
    from hydra_cli.config import sensitive_env_key
    assert sensitive_env_key("CHEAPERINFERENCE_API_KEY") is True
    assert sensitive_env_key("CHEAPERINFERENCE_API_BASE") is True
    assert sensitive_env_key("RUNPOD_API_KEY") is True
    assert sensitive_env_key("RUNPOD_ENDPOINT_URL") is True
    assert sensitive_env_key("RUNPOD_ENDPOINT_ID") is True
    assert sensitive_env_key("MODAL_ENDPOINT_URL") is True
    assert sensitive_env_key("MODAL_API_KEY") is True
    assert sensitive_env_key("CLOUDFLARE_ACCOUNT_ID") is True
    assert sensitive_env_key("HYDRA_FREE_MODEL") is False
    assert sensitive_env_key("HYDRA_SYSTEM_PROMPT") is False


def test_build_cached_system_prompt():
    from hydra_cli.config import (
        DEFAULT_SYSTEM_PROMPT,
        IMMUTABLE_AGENT_INVARIANTS,
        build_cached_system_prompt,
    )
    header = IMMUTABLE_AGENT_INVARIANTS.strip()

    # None defaults to header + DEFAULT_SYSTEM_PROMPT
    p1 = build_cached_system_prompt(None)
    assert p1.startswith(header)
    assert DEFAULT_SYSTEM_PROMPT.strip() in p1

    # Custom prompt is appended after header
    p2 = build_cached_system_prompt("Be extremely concise.")
    assert p2.startswith(header)
    assert "Be extremely concise." in p2

    # Already containing header does not duplicate
    p3 = build_cached_system_prompt(p2)
    assert p3.count(header) == 1


def test_context_window_follows_summon_alias():
    assert get_context_window("sol 6.1 high") == 131072
    assert get_context_window("sol 6.1 high") == get_context_window("openai/gpt-6.1-sol")
    assert get_context_window("gemini 3.8") == 1000000


def test_input_budget_reserves_tool_schema_and_output():
    tools = [{"type": "function", "function": {"name": "read_file", "description": "x" * 300}}]
    bare = input_char_budget(8192, None, 1024)
    fitted = input_char_budget(8192, tools, 1024)
    assert fitted < bare
    assert fitted == max(4096, 8192 * 3 - schema_chars(tools) - 1024 * 3)
    assert schema_chars(tools) > 300
