"""
Deterministic verification test suite for Hydra Desktop Application and Web Desk Server.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import pytest
from fastapi.testclient import TestClient

from hydra_cli import __version__
from hydra_cli.desktop import (
    create_desktop_app,
    get_desktop_html,
    DesktopServer,
    run_desktop_app,
)

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


def test_desktop_html_generation():
    html = get_desktop_html()
    assert "<!DOCTYPE html>" in html
    assert "Hydra Sovereign Desktop" in html
    assert "<title>Hydra Sovereign Desktop</title>" in html
    assert "brand" in html
    assert "header-controls" in html


def test_desktop_index_route(desktop_client):
    res = desktop_client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "Hydra Sovereign Desktop" in res.text


def test_desktop_api_status(desktop_client):
    res = desktop_client.get("/api/status")
    assert res.status_code == 200
    data = res.json()
    assert data.get("status") == "ok"
    assert "version" in data
    assert "uptime_seconds" in data
    assert "models_count" in data
    assert "computer_use" in data
    assert "screen_width" in data["computer_use"]
    assert "screen_height" in data["computer_use"]


def test_desktop_api_models(desktop_client):
    res = desktop_client.get("/api/models")
    assert res.status_code == 200
    data = res.json()
    assert "models" in data
    models = data["models"]
    assert isinstance(models, list)
    assert len(models) > 0
    aliases = [m["alias"].lower() for m in models]
    assert any("sonnet" in a or "opus" in a or "sol" in a for a in aliases)


def test_desktop_api_terminal_run(desktop_client):
    cmd = "python -c \"print('hydra-desktop-test-echo')\""
    res = desktop_client.post("/api/terminal/run", json={"command": cmd})
    assert res.status_code == 200
    data = res.json()
    assert data.get("exit_code") == 0
    assert "hydra-desktop-test-echo" in data.get("stdout", "")


def test_desktop_api_computer_screen(desktop_client):
    # Test JSON mode
    res = desktop_client.post("/api/computer/screen", json={"as_json": True})
    assert res.status_code == 200
    data = res.json()
    assert not data.get("isError")
    assert data.get("format") == "PNG"
    assert "base64" in data
    assert "sha256" in data
    assert data.get("size_bytes", 0) > 0

    # Test raw image mode
    res_raw = desktop_client.get("/api/computer/screen")
    assert res_raw.status_code == 200
    assert "image/png" in res_raw.headers.get("content-type", "")
    assert res_raw.content.startswith(PNG_MAGIC)


def test_desktop_api_computer_action(desktop_client):
    payload = {"action": "normalize_coordinates", "x": 960, "y": 540}
    res = desktop_client.post("/api/computer/action", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert not data.get("isError")
    assert data.get("norm_x") == 500.0
    assert data.get("norm_y") == 500.0


def test_desktop_api_browser_action(desktop_client):
    payload = {"action": "browser_inspect", "selector": "body"}
    res = desktop_client.post("/api/browser/action", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert not data.get("isError")
    assert "count" in data


def test_desktop_openai_gateway_models(desktop_client):
    res = desktop_client.get("/v1/models")
    assert res.status_code == 200
    data = res.json()
    assert "data" in data
    models = data["data"]
    assert isinstance(models, list)
    assert len(models) > 0
    first = models[0]
    assert "id" in first
    assert first.get("object") == "model"


def test_desktop_openai_gateway_chat_completions(desktop_client):
    # Non-streaming
    req = {
        "model": "sonnet 5.5",
        "messages": [
            {"role": "user", "content": "ping"}
        ],
        "stream": False,
    }
    res = desktop_client.post("/v1/chat/completions", json=req)
    assert res.status_code == 200
    data = res.json()
    assert data.get("object") == "chat.completion"
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "usage" in data

    # Streaming
    req["stream"] = True
    stream_res = desktop_client.post("/v1/chat/completions", json=req)
    assert stream_res.status_code == 200
    assert "text/event-stream" in stream_res.headers.get("content-type", "")
    content = stream_res.text
    assert "data: " in content
    assert "[DONE]" in content


def test_desktop_server_lifecycle():
    port = 7794
    server = DesktopServer(host="127.0.0.1", port=port)
    try:
        server.start(open_browser=False, background=True)
        time.sleep(1.0)
        url = f"http://127.0.0.1:{port}/api/status"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200
            body = json.loads(response.read().decode("utf-8"))
            assert body.get("status") == "ok"
    finally:
        server.stop()
        time.sleep(0.5)


def test_desktop_cli_subcommand():
    cmd = [sys.executable, "-m", "hydra_cli.cli", "desktop", "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert res.returncode == 0
    assert "Hydra Sovereign Desktop Application" in res.stdout
    assert "--host" in res.stdout
    assert "--port" in res.stdout
    assert "--headless" in res.stdout