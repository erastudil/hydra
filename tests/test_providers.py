from unittest.mock import MagicMock, patch
import os
import pytest

from hydra_cli.providers import (
    CredentialsMissingError,
    adapt_model_for_url,
    detect_local_endpoint,
    get_free_provider,
    get_frontier_providers,
)


def test_adapt_model_for_url():
    vercel_url = "https://ai-gateway.vercel.sh/v1/chat/completions"
    openrouter_url = "https://openrouter.ai/api/v1/chat/completions"

    # Vercel conversions
    assert adapt_model_for_url(vercel_url, "x-ai/grok-4.7") == "spacexai/grok-4.7"
    assert adapt_model_for_url(vercel_url, "meta-llama/llama-3.3-70b-instruct") == "meta/llama-3.3-70b"
    assert adapt_model_for_url(vercel_url, "meta-llama/llama-4-maverick") == "meta/llama-4-maverick"
    assert adapt_model_for_url(vercel_url, "qwen/qwen3.8-27b") == "alibaba/qwen3.8-27b"
    assert adapt_model_for_url(vercel_url, "openai/gpt-6.1-sol-pro") == "openai/gpt-6.1-sol-pro"
    assert adapt_model_for_url(vercel_url, "anthropic/claude-sonnet-5.5") == "anthropic/claude-sonnet-5.5"

    # OpenRouter conversions
    assert adapt_model_for_url(openrouter_url, "spacexai/grok-4.7") == "x-ai/grok-4.7"
    assert adapt_model_for_url(openrouter_url, "alibaba/qwen3.8-27b") == "qwen/qwen3.8-27b"
    assert adapt_model_for_url(openrouter_url, "anthropic/claude-sonnet-5.5") == "anthropic/claude-sonnet-5.5"


def test_frontier_providers_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("VERCEL_AI_GATEWAY_TOKEN", raising=False)
    providers = get_frontier_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "OpenRouter"
    assert "Bearer sk-or-v1-test" in providers[0]["headers"]["Authorization"]


def test_frontier_providers_vercel(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("VERCEL_AI_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vercel-test-key")
    providers = get_frontier_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "Vercel AI Gateway"
    assert "Bearer vercel-test-key" in providers[0]["headers"]["Authorization"]


def test_frontier_providers_priority(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.delenv("VERCEL_AI_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vercel-test-key")
    providers = get_frontier_providers()
    assert len(providers) == 2
    assert providers[0]["name"] == "OpenRouter"
    assert providers[1]["name"] == "Vercel AI Gateway"


def test_free_provider_cloudflare(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token-123")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cf-account-456")
    provider_info, model = get_free_provider()
    assert provider_info["name"] == "Cloudflare Workers AI"
    assert "cf-account-456" in provider_info["url"]
    assert "Bearer cf-token-123" in provider_info["headers"]["Authorization"]


def test_free_provider_openrouter(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-free-key")
    provider_info, model = get_free_provider()
    assert provider_info["name"] == "OpenRouter Free Forge"
    assert ":free" in model


def test_free_provider_missing_credentials(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(CredentialsMissingError):
        get_free_provider()


def test_detect_local_endpoint_custom_base(monkeypatch):
    monkeypatch.setenv("LOCAL_AI_BASE", "http://127.0.0.1:9090")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("LLAMACPP_HOST", raising=False)
    url, name = detect_local_endpoint()
    assert "9090/v1/chat/completions" in url
    assert name == "Custom Local AI"


@patch("hydra_cli.providers.is_port_open")
def test_detect_local_endpoint_ollama(mock_port, monkeypatch):
    monkeypatch.delenv("LOCAL_AI_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("LLAMACPP_HOST", raising=False)
    # Return true for 11434, false for others
    def side_effect(host, port, timeout=0.4):
        return port == 11434

    mock_port.side_effect = side_effect
    url, name = detect_local_endpoint()
    assert "11434" in url
    assert name == "Ollama"
