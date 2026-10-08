from unittest.mock import MagicMock, patch
import os
import pytest

from hydra_cli.providers import (
    CredentialsMissingError,
    adapt_model_for_url,
    detect_local_endpoint,
    get_cheaperinference_provider,
    get_free_provider,
    get_frontier_providers,
    get_huggingface_provider,
    get_hf_provider,
    get_modal_provider,
    get_runpod_provider,
)


@pytest.fixture(autouse=True)
def clean_frontier_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("VERCEL_AI_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("CHEAPERINFERENCE_API_KEY", raising=False)
    monkeypatch.delenv("CHEAPERINFERENCE_API_BASE", raising=False)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.delenv("RUNPOD_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("MODAL_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("MODAL_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HF_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("HF_INFERENCE_BASE", raising=False)


def test_adapt_model_for_url():
    vercel_url = "https://ai-gateway.vercel.sh/v1/chat/completions"
    openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
    cheaper_url = "https://api.cheaperinference.com/v1/chat/completions"

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

    # CheaperInference conversions (strip vendor namespace)
    assert adapt_model_for_url(cheaper_url, "z-ai/glm-5.3") == "glm-5.3"
    assert adapt_model_for_url(cheaper_url, "anthropic/claude-opus-5.5") == "claude-opus-5.5"
    assert adapt_model_for_url(cheaper_url, "openai/gpt-6.1-sol") == "gpt-6.1-sol"
    assert adapt_model_for_url(cheaper_url, "Aleph-Alpha/Kolibri-1") == "Kolibri-1"
    assert adapt_model_for_url(cheaper_url, "glm-5.3") == "glm-5.3"


def test_frontier_providers_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    providers = get_frontier_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "OpenRouter"
    assert "Bearer sk-or-v1-test" in providers[0]["headers"]["Authorization"]


def test_frontier_providers_vercel(monkeypatch):
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
    assert providers[0]["name"] == "Vercel AI Gateway"
    assert providers[1]["name"] == "OpenRouter"


def test_frontier_providers_full_hierarchy(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf-test")
    monkeypatch.setenv("CHEAPERINFERENCE_API_KEY", "ci-test")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vercel-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    monkeypatch.setenv("MODAL_ENDPOINT_URL", "https://modal.test")
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "rp-ep")
    providers = get_frontier_providers()
    names = [p["name"] for p in providers]
    assert names == [
        "Hugging Face Inference",
        "CheaperInference",
        "Vercel AI Gateway",
        "OpenRouter",
        "Modal",
        "RunPod",
    ]


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


def test_frontier_providers_cheaperinference(monkeypatch):
    monkeypatch.setenv("CHEAPERINFERENCE_API_KEY", "ci-test-key")
    providers = get_frontier_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "CheaperInference"
    assert providers[0]["url"] == "https://api.cheaperinference.com/v1/chat/completions"
    assert "Bearer ci-test-key" in providers[0]["headers"]["Authorization"]


def test_cheaperinference_custom_base(monkeypatch):
    monkeypatch.setenv("CHEAPERINFERENCE_API_KEY", "ci-test-key")
    monkeypatch.setenv("CHEAPERINFERENCE_API_BASE", "https://custom.cheaperinference.com/v1")
    p = get_cheaperinference_provider()
    assert p is not None
    assert p["url"] == "https://custom.cheaperinference.com/v1/chat/completions"
    assert "Bearer ci-test-key" in p["headers"]["Authorization"]


def test_cheaperinference_missing():
    assert get_cheaperinference_provider() is None


def test_runpod_provider_with_endpoint_id(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "runpod-test-key")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-12345")
    p = get_runpod_provider()
    assert p is not None
    assert p["name"] == "RunPod"
    assert p["url"] == "https://api.runpod.ai/v2/ep-12345/openai/v1/chat/completions"
    assert "Bearer runpod-test-key" in p["headers"]["Authorization"]


def test_runpod_provider_with_endpoint_url(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "runpod-test-key")
    monkeypatch.setenv("RUNPOD_ENDPOINT_URL", "https://my-pod.internal/v1")
    p = get_runpod_provider()
    assert p is not None
    assert p["url"] == "https://my-pod.internal/v1/chat/completions"
    assert "Bearer runpod-test-key" in p["headers"]["Authorization"]


def test_runpod_provider_missing(monkeypatch):
    assert get_runpod_provider() is None
    monkeypatch.setenv("RUNPOD_API_KEY", "runpod-test-key")
    assert get_runpod_provider() is None


def test_modal_provider_with_url_and_key(monkeypatch):
    monkeypatch.setenv("MODAL_ENDPOINT_URL", "https://my-modal-app.modal.run")
    monkeypatch.setenv("MODAL_API_KEY", "modal-key-123")
    p = get_modal_provider()
    assert p is not None
    assert p["name"] == "Modal"
    assert p["url"] == "https://my-modal-app.modal.run/v1/chat/completions"
    assert "Bearer modal-key-123" in p["headers"]["Authorization"]


def test_modal_provider_url_only(monkeypatch):
    monkeypatch.setenv("MODAL_ENDPOINT_URL", "https://my-modal-app.modal.run")
    p = get_modal_provider()
    assert p is not None
    assert p["name"] == "Modal"
    assert p["url"] == "https://my-modal-app.modal.run/v1/chat/completions"
    assert "Authorization" not in p["headers"]


def test_modal_provider_missing():
    assert get_modal_provider() is None


def test_frontier_providers_all(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vercel-test")
    monkeypatch.setenv("CHEAPERINFERENCE_API_KEY", "ci-test")
    monkeypatch.setenv("RUNPOD_API_KEY", "runpod-test")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "pod-123")
    monkeypatch.setenv("MODAL_ENDPOINT_URL", "https://modal-test.modal.run")
    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    providers = get_frontier_providers()
    assert len(providers) == 6
    assert [p["name"] for p in providers] == [
        "Hugging Face Inference",
        "CheaperInference",
        "Vercel AI Gateway",
        "OpenRouter",
        "Modal",
        "RunPod",
    ]


def test_huggingface_provider_with_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_key_123")
    monkeypatch.delenv("HF_ENDPOINT_URL", raising=False)
    p = get_huggingface_provider()
    assert p is not None
    assert p["name"] == "Hugging Face Inference"
    assert p["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert "Bearer hf_test_key_123" in p["headers"]["Authorization"]


def test_huggingface_provider_with_endpoint_url(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_key_123")
    monkeypatch.setenv("HF_ENDPOINT_URL", "https://custom-endpoint.endpoints.huggingface.cloud/v1")
    p = get_huggingface_provider()
    assert p is not None
    assert p["name"] == "Hugging Face Inference"
    assert p["url"] == "https://custom-endpoint.endpoints.huggingface.cloud/v1/chat/completions"
    assert "Bearer hf_test_key_123" in p["headers"]["Authorization"]


def test_huggingface_provider_missing(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HF_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("HF_INFERENCE_BASE", raising=False)
    assert get_huggingface_provider() is None


def test_openrouter_tools_require_parameters_keep_existing_route():
    from hydra_cli.providers import attach_tool_capability

    payload = {
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
        "provider": {"order": ["anthropic", "google"], "sort": "throughput"},
    }
    attach_tool_capability("https://openrouter.ai/api/v1/chat/completions", payload)
    assert payload["provider"]["require_parameters"] is True
    assert payload["provider"]["order"] == ["anthropic", "google"]
    assert payload["provider"]["sort"] == "throughput"


def test_cheaperinference_tools_leave_route_unset():
    from hydra_cli.providers import attach_tool_capability

    payload = {"tools": [{"type": "function", "function": {"name": "read_file"}}]}
    attach_tool_capability("https://api.cheaperinference.com/v1/chat/completions", payload)
    assert "provider" not in payload
