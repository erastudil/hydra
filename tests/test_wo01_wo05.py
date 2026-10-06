import io
import json
import os
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from hydra_cli.config import (
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_FREE_MODEL,
    DEFAULT_LOCAL_MODEL,
    hydra_home,
)
from hydra_cli.providers import (
    ProviderError,
    ProviderHealthTracker,
    complete,
    fallback_cascade,
    fetch_chat_completion,
    get_provider_health,
    providers_for_model,
    reset_provider_health,
    stream_chat_completion,
)
from hydra_cli.router import execute_summon, route_command


# =====================================================================
# 1. WO-01: MCP Community Registry & snowgate-mcp
# =====================================================================

def test_default_mcp_config_registers_snowgate_mcp():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_cfg = os.path.join(root, "hydra_cli", "mcp_servers.default.json")
    with open(default_cfg, "r", encoding="utf-8") as f:
        data = json.load(f)["mcpServers"]
    assert "snowgate-mcp" in data
    server = data["snowgate-mcp"]
    assert server["command"] == "node"
    assert any("server.js" in arg for arg in server["args"])
    assert "--stdio" in server["args"]


def test_user_mcp_config_registers_snowgate_mcp():
    home_cfg = os.path.join(hydra_home(), "mcp_servers.json")
    if os.path.isfile(home_cfg):
        with open(home_cfg, "r", encoding="utf-8") as f:
            data = json.load(f)["mcpServers"]
        assert "snowgate-mcp" in data
        assert data["snowgate-mcp"]["command"] == "node"


# =====================================================================
# 2. WO-05: Health-Based Dynamic Routing & Circuit Breaker
# =====================================================================

def test_provider_health_tracker_lifecycle():
    tracker = ProviderHealthTracker(failure_threshold=2, cooldown_seconds=0.1, backoff_factor=2.0)
    endpoint = "https://api.example.com/v1"

    assert tracker.is_healthy(endpoint) is True
    assert tracker.get_health_status(endpoint) == "healthy"

    # Single failure -> degraded
    tracker.record_failure(endpoint, error=Exception("timeout"))
    assert tracker.is_healthy(endpoint) is True
    assert tracker.get_health_status(endpoint) == "degraded"

    # Second failure reaches threshold -> unhealthy (circuit open)
    tracker.record_failure(endpoint, error=Exception("connection refused"))
    assert tracker.is_healthy(endpoint) is False
    assert tracker.get_health_status(endpoint) == "unhealthy"

    # Cooldown expiry -> enters degraded / half-open probe
    import time
    time.sleep(0.12)
    assert tracker.is_healthy(endpoint) is True
    assert tracker.get_health_status(endpoint) == "degraded"

    # Success closes circuit
    tracker.record_success(endpoint)
    assert tracker.is_healthy(endpoint) is True
    assert tracker.get_health_status(endpoint) == "healthy"


def test_health_based_dynamic_provider_reranking():
    tracker = ProviderHealthTracker(failure_threshold=1, cooldown_seconds=60.0)
    p1 = {"id": "openrouter", "name": "OpenRouter", "url": "https://openrouter.ai/api/v1/chat/completions"}
    p2 = {"id": "vercel", "name": "Vercel AI Gateway", "url": "https://ai-gateway.vercel.sh/v1/chat/completions"}

    # Initially both healthy, order preserved [p1, p2]
    ranked = tracker.rank_providers([p1, p2])
    assert ranked == [p1, p2]

    # OpenRouter trips 429
    tracker.record_failure("openrouter", error=Exception("Rate limit 429"), is_429=True)
    assert tracker.get_health_status("openrouter") == "unhealthy"
    assert tracker.get_health_status("vercel") == "healthy"

    # Dynamic re-ranking promotes healthy Vercel to front
    reranked = tracker.rank_providers([p1, p2])
    assert reranked == [p2, p1]


# =====================================================================
# 3. WO-05: HTTP 429 Exponential Backoff
# =====================================================================

def test_fetch_chat_completion_http_429_backoff_and_retry(monkeypatch):
    monkeypatch.setenv("HYDRA_MAX_RETRIES", "2")
    monkeypatch.setenv("HYDRA_BACKOFF_BASE", "0.01")
    url = "https://api.test/v1/chat/completions"

    calls = 0

    def mock_urlopen(req, timeout=180):
        nonlocal calls
        calls += 1
        if calls < 3:
            # Raise 429 for first two attempts
            headers = {"Retry-After": "0.01"}
            err = urllib.error.HTTPError(url, 429, "Too Many Requests", headers, io.BytesIO(b'{"error":"rate_limited"}'))
            raise err
        # Attempt 3 succeeds
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "backoff passed"}}]}).encode()
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_chat_completion(url, {}, "test-model", [{"role": "user", "content": "hi"}])

    assert calls == 3
    assert res == "backoff passed"


def test_stream_chat_completion_http_429_backoff_and_retry(monkeypatch):
    monkeypatch.setenv("HYDRA_MAX_RETRIES", "2")
    monkeypatch.setenv("HYDRA_BACKOFF_BASE", "0.01")
    url = "https://api.test/v1/chat/completions"

    calls = 0

    def mock_urlopen(req, timeout=180):
        nonlocal calls
        calls += 1
        if calls < 2:
            err = urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b'{}'))
            raise err
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = [
            b"data: {\"choices\": [{\"delta\": {\"content\": \"stream token\"}}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        tokens = list(stream_chat_completion(url, {}, "test-model", [{"role": "user", "content": "hi"}]))

    assert calls == 2
    assert tokens == ["stream token"]


# =====================================================================
# 4. WO-05: Universal Fallback Routing Cascade
# =====================================================================

def test_fallback_cascade_full_chain(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cf-act")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    attempts = []

    def mock_fetch(url, headers, model, messages, **kwargs):
        attempts.append((url, model))
        if "cloudflare" in url:
            raise ProviderError("CF down")
        if "openrouter" in url:
            raise ProviderError("OR down")
        return "local response"

    with patch("hydra_cli.providers.fetch_chat_completion", side_effect=mock_fetch):
        res = fallback_cascade("test prompt", frontier_error=Exception("Frontier exhausted"))

    assert res == "local response"
    # Verifies cascade visited Cloudflare, then OpenRouter, then Local
    assert any("cloudflare" in u for u, m in attempts)
    assert any("openrouter" in u for u, m in attempts)
    assert any("11434" in u or "8080" in u or "8000" in u for u, m in attempts)


def test_execute_summon_cascades_to_cloudflare_on_frontier_error(monkeypatch, capsys):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cf-act")

    fake_frontier = [{"id": "mock_frontier", "name": "Mock Frontier", "url": "https://api.frontier.mock/v1/chat/completions", "headers": {}}]

    def mock_fetch(url, headers, model, messages, **kwargs):
        if "frontier.mock" in url:
            raise ProviderError("Frontier 503 Service Unavailable")
        return "cloudflare cascade answer"

    with patch("hydra_cli.router.providers_for_model", return_value=fake_frontier), \
         patch("hydra_cli.router.fetch_chat_completion", side_effect=mock_fetch):
        ret = execute_summon("sonnet 5.5", "prompt", "sys", stream=False)

    assert ret == 0
    captured = capsys.readouterr()
    assert "cloudflare cascade answer" in captured.out
    assert "[HYDRA CASCADE]" in captured.err


def test_execute_summon_respects_no_fallback_flag(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cf-act")

    fake_frontier = [{"id": "mock_frontier", "name": "Mock Frontier", "url": "https://api.frontier.mock/v1/chat/completions", "headers": {}}]

    def mock_fetch(url, headers, model, messages, **kwargs):
        if "frontier.mock" in url:
            raise ProviderError("Frontier 503")
        return "cf answer"

    with patch("hydra_cli.router.providers_for_model", return_value=fake_frontier), \
         patch("hydra_cli.router.fetch_chat_completion", side_effect=mock_fetch):
        with pytest.raises(ProviderError, match="All configured providers failed"):
            execute_summon("sonnet 5.5", "prompt", "sys", stream=False, fallback_cascade=False)


def test_complete_programmatic_fallback_cascade(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cf-act")

    fake_frontier = [{"id": "mock_frontier", "name": "Mock Frontier", "url": "https://api.frontier.mock/v1/chat/completions", "headers": {}}]

    def mock_fetch(url, headers, model, messages, **kwargs):
        if "frontier.mock" in url:
            raise ProviderError("Frontier credit limit reached")
        return "cascaded completion"

    with patch("hydra_cli.providers.providers_for_model", return_value=fake_frontier), \
         patch("hydra_cli.providers.fetch_chat_completion", side_effect=mock_fetch):
        answer = complete("sonnet 5.5", "prompt")

    assert answer == "cascaded completion"
