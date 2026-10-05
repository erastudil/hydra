"""
Unit tests for Hydra Sovereign Gateway Server (serve.py) and External Agent Runners (agent_runners.py).
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")

from hydra_cli.agent_runners import (
    find_hermes_binary,
    find_pi_binary,
    get_agent_status,
    hermes_available,
    pi_available,
    run_hermes,
    run_pi,
)
from hydra_cli.serve import (
    HydraGatewayHandler,
    build_upstream_payload,
    forward_chat_completion,
    forward_stream_completion,
    get_registered_models,
)


# =============================================================================
# Fixture: Ephemeral Gateway HTTP Server
# =============================================================================

@pytest.fixture
def gateway_server(monkeypatch):
    """Start an ephemeral Hydra Gateway HTTP server on a dynamic port."""
    monkeypatch.setenv("HYDRA_GATEWAY_QUIET", "1")
    httpd = HTTPServer(("127.0.0.1", 0), HydraGatewayHandler)
    host, port = httpd.server_address
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    yield base_url

    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


# =============================================================================
# 1. Models & Health Endpoint Tests
# =============================================================================

def test_get_registered_models_structure():
    """Verify get_registered_models returns OpenAI format list."""
    res = get_registered_models()
    assert res["object"] == "list"
    assert isinstance(res["data"], list)
    model_ids = {m["id"] for m in res["data"]}
    assert "opus 5.5" in model_ids
    assert "sonnet 5.5" in model_ids
    assert "sol 6.1 pro" in model_ids
    assert "anthropic/claude-opus-5.5" in model_ids


def test_http_get_models(gateway_server):
    """GET /v1/models returns HTTP 200 with OpenAI model list."""
    url = f"{gateway_server}/v1/models"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        data = json.loads(resp.read().decode("utf-8"))
        assert data["object"] == "list"
        ids = [m["id"] for m in data["data"]]
        assert "opus 5.5" in ids
        assert "gemini 3.8" in ids


def test_http_get_models_alias_path(gateway_server):
    """GET /models also routes to model list."""
    url = f"{gateway_server}/models"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["object"] == "list"


def test_http_get_health(gateway_server):
    """GET /health returns HTTP 200 ok."""
    url = f"{gateway_server}/health"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert data["service"] == "hydra-gateway"


def test_http_get_not_found(gateway_server):
    """GET on unknown path returns 404."""
    url = f"{gateway_server}/v1/unknown_endpoint"
    req = urllib.request.Request(url, method="GET")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 404


def test_http_options_cors(gateway_server):
    """OPTIONS request returns 204 with CORS headers."""
    url = f"{gateway_server}/v1/chat/completions"
    req = urllib.request.Request(url, method="OPTIONS")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# =============================================================================
# 2. Non-Streaming Chat Completions Tests
# =============================================================================

@patch("hydra_cli.serve.get_frontier_providers")
@patch("hydra_cli.serve.forward_chat_completion")
def test_post_chat_completions_non_streaming(mock_forward, mock_providers, gateway_server):
    """POST /v1/chat/completions resolves alias and returns mock provider JSON."""
    mock_provider = {
        "name": "MockOpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "headers": {"Authorization": "Bearer mock-token"},
    }
    mock_providers.return_value = [mock_provider]

    mock_resp_payload = {
        "id": "chatcmpl-test-001",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "anthropic/claude-opus-5.5",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Sovereign intelligence online.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }
    mock_forward.return_value = mock_resp_payload

    request_body = {
        "model": "opus 5.5",
        "messages": [{"role": "user", "content": "Status report."}],
        "stream": False,
        "max_tokens": 512,
        "tools": [{"type": "function", "function": {"name": "test_tool"}}],
        "tool_choice": "auto",
    }

    url = f"{gateway_server}/v1/chat/completions"
    data_bytes = json.dumps(request_body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        res_data = json.loads(resp.read().decode("utf-8"))
        assert res_data["id"] == "chatcmpl-test-001"
        assert res_data["choices"][0]["message"]["content"] == "Sovereign intelligence online."

    mock_forward.assert_called_once()
    called_provider, called_payload = mock_forward.call_args[0][:2]
    assert called_provider["name"] == "MockOpenRouter"
    assert called_payload["model"] == "anthropic/claude-opus-5.5"
    assert called_payload["messages"] == [{"role": "user", "content": "Status report."}]
    assert called_payload["stream"] is False
    assert called_payload["max_tokens"] == 512
    assert called_payload["tools"] == [{"type": "function", "function": {"name": "test_tool"}}]
    assert called_payload["tool_choice"] == "auto"


# =============================================================================
# 3. Streaming Chat Completions Tests (Server-Sent Events)
# =============================================================================

@patch("hydra_cli.serve.get_frontier_providers")
@patch("hydra_cli.serve.forward_stream_completion")
def test_post_chat_completions_streaming(mock_stream, mock_providers, gateway_server):
    """POST /v1/chat/completions with stream: true streams SSE events."""
    mock_provider = {
        "name": "MockOpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "headers": {"Authorization": "Bearer mock-token"},
    }
    mock_providers.return_value = [mock_provider]

    mock_stream.return_value = iter([
        'data: {"id":"c1","choices":[{"delta":{"content":"Hello"}}]}\n\n',
        'data: {"id":"c1","choices":[{"delta":{"content":" world"}}]}\n\n',
        "data: [DONE]\n\n",
    ])

    request_body = {
        "model": "sonnet 5.5",
        "messages": [{"role": "user", "content": "Greetings"}],
        "stream": True,
    }

    url = f"{gateway_server}/v1/chat/completions"
    data_bytes = json.dumps(request_body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")
        lines = [line.decode("utf-8").strip() for line in resp if line.decode("utf-8").strip()]

    assert 'data: {"id":"c1","choices":[{"delta":{"content":"Hello"}}]}' in lines
    assert 'data: {"id":"c1","choices":[{"delta":{"content":" world"}}]}' in lines
    assert "data: [DONE]" in lines


# =============================================================================
# 4. Error Handling and Invariant Tests
# =============================================================================

def test_post_missing_body(gateway_server):
    """POST with empty body returns HTTP 400."""
    url = f"{gateway_server}/v1/chat/completions"
    req = urllib.request.Request(url, data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400


def test_post_malformed_json(gateway_server):
    """POST with invalid JSON returns HTTP 400."""
    url = f"{gateway_server}/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=b"not-json-content",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400


def test_post_missing_model_parameter(gateway_server):
    """POST without model parameter returns HTTP 400."""
    url = f"{gateway_server}/v1/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400


def test_post_temperature_rejection(gateway_server):
    """POST with temperature on a model that rejects temperature returns 400."""
    url = f"{gateway_server}/v1/chat/completions"
    payload = {
        "model": "opus 5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.5,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400
    err_body = json.loads(exc.value.read().decode("utf-8"))
    assert "rejects temperature" in err_body["error"]["message"]


@patch("hydra_cli.serve.get_frontier_providers", return_value=[])
def test_post_no_frontier_providers(mock_prov, gateway_server):
    """POST returns HTTP 503 when no frontier providers are configured."""
    url = f"{gateway_server}/v1/chat/completions"
    payload = {
        "model": "sol 6.1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 503


@patch("hydra_cli.serve.get_frontier_providers")
@patch("hydra_cli.serve.forward_chat_completion")
def test_post_provider_fallback_and_failure(mock_forward, mock_providers, gateway_server):
    """POST tries all providers and returns 502 if all fail."""
    mock_providers.return_value = [
        {"name": "P1", "url": "https://p1.ai", "headers": {}},
        {"name": "P2", "url": "https://p2.ai", "headers": {}},
    ]
    mock_forward.side_effect = RuntimeError("Provider unreachable")

    url = f"{gateway_server}/v1/chat/completions"
    payload = {
        "model": "sol 6.1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 502
    assert mock_forward.call_count == 2


# =============================================================================
# 5. External Agent Runners Tests (Hermes & Pi)
# =============================================================================

def test_hermes_available_detection():
    """hermes_available returns bool based on binary discovery."""
    with patch("hydra_cli.agent_runners.find_hermes_binary", return_value="/usr/bin/hermes"):
        assert hermes_available() is True
    with patch("hydra_cli.agent_runners.find_hermes_binary", return_value=None):
        assert hermes_available() is False


def test_pi_available_detection():
    """pi_available returns bool based on binary discovery."""
    with patch("hydra_cli.agent_runners.find_pi_binary", return_value="/usr/bin/pi"):
        assert pi_available() is True
    with patch("hydra_cli.agent_runners.find_pi_binary", return_value=None):
        assert pi_available() is False


def test_agent_status_capabilities():
    """get_agent_status returns availability and capabilities for both agents."""
    with patch("hydra_cli.agent_runners.find_hermes_binary", return_value="/usr/bin/hermes"), \
         patch("hydra_cli.agent_runners.find_pi_binary", return_value=None):
        status = get_agent_status()
        assert status["hermes"]["available"] is True
        assert status["hermes"]["path"] == "/usr/bin/hermes"
        assert "one-shot" in status["hermes"]["capabilities"]
        assert status["pi"]["available"] is False
        assert status["pi"]["path"] is None


def test_run_hermes_not_installed():
    """run_hermes returns None when hermes is not installed."""
    with patch("hydra_cli.agent_runners.find_hermes_binary", return_value=None):
        assert run_hermes("Design an orderbook") is None


@patch("subprocess.run")
@patch("hydra_cli.agent_runners.find_hermes_binary", return_value="/usr/local/bin/hermes")
def test_run_hermes_success(mock_find, mock_run):
    """run_hermes invokes hermes with -z flag and parses stdout."""
    mock_run.return_value = MagicMock(returncode=0, stdout="Architecture finalized.\n", stderr="")

    result = run_hermes(
        prompt="Design an orderbook",
        model="opus 5.5",
        toolsets=["web", "code"],
    )

    assert result == "Architecture finalized."
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "/usr/local/bin/hermes"
    assert cmd[1] == "-z"
    assert cmd[2] == "Design an orderbook"
    assert "--model" in cmd
    assert "opus 5.5" in cmd
    assert "--toolsets" in cmd
    assert "web,code" in cmd


@patch("subprocess.run")
@patch("hydra_cli.agent_runners.find_hermes_binary", return_value="/usr/local/bin/hermes")
def test_run_hermes_failure(mock_find, mock_run):
    """run_hermes raises RuntimeError when hermes exits with nonzero status."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Model quota exceeded")

    with pytest.raises(RuntimeError) as exc_info:
        run_hermes("Test prompt")
    assert "Hermes execution failed" in str(exc_info.value)
    assert "Model quota exceeded" in str(exc_info.value)


def test_run_pi_not_installed():
    """run_pi returns None when pi is not installed."""
    with patch("hydra_cli.agent_runners.find_pi_binary", return_value=None):
        assert run_pi("Implement fast ring buffer") is None


@patch("subprocess.run")
@patch("hydra_cli.agent_runners.find_pi_binary", return_value="/usr/local/bin/pi")
def test_run_pi_success_z_flag(mock_find, mock_run):
    """run_pi invokes pi with -z flag by default and parses stdout."""
    mock_run.return_value = MagicMock(returncode=0, stdout="Ring buffer implemented.\n", stderr="")

    result = run_pi(
        prompt="Implement fast ring buffer",
        model="qwen 3.8",
    )

    assert result == "Ring buffer implemented."
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "/usr/local/bin/pi"
    assert cmd[1] == "-z"
    assert cmd[2] == "Implement fast ring buffer"
    assert "--model" in cmd
    assert "qwen 3.8" in cmd


@patch("subprocess.run")
@patch("hydra_cli.agent_runners.find_pi_binary", return_value="/usr/local/bin/pi")
def test_run_pi_fallback_to_oneshot(mock_find, mock_run):
    """run_pi falls back to --oneshot if -z is reported as an unrecognized option."""
    # First invocation fails with unrecognized option, retry with --oneshot succeeds
    fail_mock = MagicMock(returncode=2, stdout="", stderr="error: unrecognized option '-z'")
    succ_mock = MagicMock(returncode=0, stdout="Code generated via --oneshot.\n", stderr="")
    mock_run.side_effect = [fail_mock, succ_mock]

    result = run_pi("Write quicksort")
    assert result == "Code generated via --oneshot."
    assert mock_run.call_count == 2
    first_cmd = mock_run.call_args_list[0][0][0]
    second_cmd = mock_run.call_args_list[1][0][0]
    assert "-z" in first_cmd
    assert "--oneshot" in second_cmd


@patch("subprocess.run")
@patch("hydra_cli.agent_runners.find_pi_binary", return_value="/usr/local/bin/pi")
def test_run_pi_failure(mock_find, mock_run):
    """run_pi raises RuntimeError when execution fails."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Fatal syntax error")

    with pytest.raises(RuntimeError) as exc_info:
        run_pi("Write code")
    assert "Pi execution failed" in str(exc_info.value)
    assert "Fatal syntax error" in str(exc_info.value)
