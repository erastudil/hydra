from unittest.mock import MagicMock, patch
import os
import pytest

from hydra_cli.providers import (
    CredentialsMissingError,
    detect_local_endpoint,
    get_free_provider,
    get_frontier_providers,
)


def test_frontier_providers_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    providers = get_frontier_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "OpenRouter"
    assert "Bearer sk-or-v1-test" in providers[0]["headers"]["Authorization"]


def test_frontier_providers_vercel(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vercel-test-key")
    providers = get_frontier_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "Vercel AI Gateway"
    assert "Bearer vercel-test-key" in providers[0]["headers"]["Authorization"]


def test_frontier_providers_priority(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
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
    url, name = detect_local_endpoint()
    assert "9090/v1/chat/completions" in url
    assert name == "Custom Local AI"


@patch("hydra_cli.providers.is_port_open")
def test_detect_local_endpoint_ollama(mock_port, monkeypatch):
    monkeypatch.delenv("LOCAL_AI_BASE", raising=False)
    # Return true for 11434, false for others
    def side_effect(host, port, timeout=0.4):
        return port == 11434

    mock_port.side_effect = side_effect
    url, name = detect_local_endpoint()
    assert "11434" in url
    assert name == "Ollama"
