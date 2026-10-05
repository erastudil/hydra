"""
Unit tests for MCP client, registry, tool namespacing, OpenAI translation,
env variable interpolation, and dispatch mocking.
"""

import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from hydra_cli.mcp import McpSubprocessClient, should_use_shell
from hydra_cli.mcp_registry import (
    DEFAULT_CONFIG_PATH,
    McpRegistry,
    get_default_home_config,
    interpolate_env_vars,
)


@pytest.fixture(autouse=True)
def isolate_home_and_cwd(tmp_path, monkeypatch):
    """
    Ensure unit tests run hermetically without discovering or loading
    the active developer's ~/.hydra/mcp_servers.json.
    """
    fake_home = tmp_path / "fake_home"
    (fake_home / ".hydra").mkdir(parents=True)
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda path: str(fake_home) if path == "~" or path.startswith("~") else path,
    )
    fake_cwd = tmp_path / "fake_cwd"
    fake_cwd.mkdir()
    monkeypatch.chdir(fake_cwd)


# =====================================================================
# 1. Environment Variable Interpolation Tests
# =====================================================================

def test_interpolate_env_vars_simple(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret_value_123")
    assert interpolate_env_vars("${TEST_KEY}") == "secret_value_123"
    assert interpolate_env_vars("Bearer ${TEST_KEY}") == "Bearer secret_value_123"


def test_interpolate_env_vars_multiple(monkeypatch):
    monkeypatch.setenv("HOST", "api.example.com")
    monkeypatch.setenv("PORT", "8080")
    assert interpolate_env_vars("http://${HOST}:${PORT}/v1") == "http://api.example.com:8080/v1"


def test_interpolate_env_vars_with_default(monkeypatch):
    monkeypatch.delenv("UNSET_VAR_XYZ", raising=False)
    assert interpolate_env_vars("${UNSET_VAR_XYZ:-fallback_val}") == "fallback_val"

    monkeypatch.setenv("SET_VAR_XYZ", "actual_val")
    assert interpolate_env_vars("${SET_VAR_XYZ:-fallback_val}") == "actual_val"


def test_interpolate_env_vars_unset_no_default(monkeypatch):
    monkeypatch.delenv("MISSING_VAR_ABC", raising=False)
    assert interpolate_env_vars("${MISSING_VAR_ABC}") == ""


def test_interpolate_env_vars_nested(monkeypatch):
    monkeypatch.setenv("CMD", "uvx")
    monkeypatch.setenv("PARAM", "param_val")
    monkeypatch.setenv("ENV_KEY", "env_val")

    payload = {
        "command": "${CMD}",
        "args": ["run", "--param", "${PARAM}"],
        "env": {"KEY": "${ENV_KEY}"},
        "count": 42,
        "active": True,
        "extra": None,
    }

    interpolated = interpolate_env_vars(payload)
    assert interpolated == {
        "command": "uvx",
        "args": ["run", "--param", "param_val"],
        "env": {"KEY": "env_val"},
        "count": 42,
        "active": True,
        "extra": None,
    }


# =====================================================================
# 2. Windows Compatibility & Shell Selection Tests
# =====================================================================

def test_should_use_shell_on_win32():
    # npx and uvx always use shell=True on win32
    assert should_use_shell("npx", platform="win32") is True
    assert should_use_shell("uvx", platform="win32") is True
    assert should_use_shell("npx.cmd", platform="win32") is True
    assert should_use_shell("uvx.exe", platform="win32") is True
    assert should_use_shell(r"C:\Program Files\nodejs\npx", platform="win32") is True

    # Commands without .exe/.cmd use shell=True on win32
    assert should_use_shell("python", platform="win32") is True
    assert should_use_shell("node", platform="win32") is True
    assert should_use_shell("script.bat", platform="win32") is True

    # Standard executables ending in .exe or .cmd (not npx/uvx) use shell=False
    assert should_use_shell(r"C:\Python314\python.exe", platform="win32") is False
    assert should_use_shell("app.exe", platform="win32") is False
    assert should_use_shell("tool.cmd", platform="win32") is False


def test_should_use_shell_on_posix():
    # On Linux/Darwin, shell=False
    assert should_use_shell("npx", platform="linux") is False
    assert should_use_shell("uvx", platform="darwin") is False
    assert should_use_shell("python", platform="linux") is False


# =====================================================================
# 3. McpSubprocessClient Unit Tests (Mocked Subprocess)
# =====================================================================

class FakeStdout:
    def __init__(self, lines):
        self.lines = list(lines)
        self.index = 0

    def readline(self):
        if self.index < len(self.lines):
            line = self.lines[self.index]
            self.index += 1
            return line
        return ""


class FakeStdin:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeStderr:
    def __init__(self, lines=None):
        self.lines = list(lines) if lines else []
        self.index = 0

    def readline(self):
        if self.index < len(self.lines):
            line = self.lines[self.index]
            self.index += 1
            return line
        return ""


def test_client_start_and_initialize():
    init_response = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "test-server", "version": "1.0.0"},
            "capabilities": {"tools": {}},
        },
    }) + "\n"

    fake_stdin = FakeStdin()
    fake_stdout = FakeStdout([init_response])
    fake_stderr = FakeStderr()

    mock_proc = MagicMock()
    mock_proc.stdin = fake_stdin
    mock_proc.stdout = fake_stdout
    mock_proc.stderr = fake_stderr
    mock_proc.poll.return_value = None

    client = McpSubprocessClient(command="test_server")

    with patch("subprocess.Popen", return_value=mock_proc):
        client.start()

    assert client.server_info == {"name": "test-server", "version": "1.0.0"}
    assert client.server_capabilities == {"tools": {}}
    assert len(fake_stdin.written) == 2

    # Request 1: initialize
    req1 = json.loads(fake_stdin.written[0])
    assert req1["method"] == "initialize"
    assert req1["id"] == 1

    # Notification 2: notifications/initialized
    req2 = json.loads(fake_stdin.written[1])
    assert req2["method"] == "notifications/initialized"
    assert "id" not in req2


def test_client_list_tools():
    client = McpSubprocessClient(command="test_server")
    mock_proc = MagicMock()
    mock_proc.stdin = FakeStdin()
    tools_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {
                    "name": "search_docs",
                    "description": "Search documentation",
                    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                }
            ]
        },
    }
    mock_proc.stdout = FakeStdout([json.dumps(tools_payload) + "\n"])
    mock_proc.stderr = FakeStderr()
    mock_proc.poll.return_value = None
    client._process = mock_proc

    tools = client.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "search_docs"
    assert tools[0]["description"] == "Search documentation"


def test_client_call_tool():
    client = McpSubprocessClient(command="test_server")
    mock_proc = MagicMock()
    mock_proc.stdin = FakeStdin()
    call_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": "result content"}],
            "isError": False,
        },
    }
    mock_proc.stdout = FakeStdout([json.dumps(call_payload) + "\n"])
    mock_proc.stderr = FakeStderr()
    mock_proc.poll.return_value = None
    client._process = mock_proc

    result = client.call_tool("search_docs", {"q": "python"})
    assert result == {
        "content": [{"type": "text", "text": "result content"}],
        "isError": False,
    }

    sent = json.loads(mock_proc.stdin.written[0])
    assert sent["method"] == "tools/call"
    assert sent["params"]["name"] == "search_docs"
    assert sent["params"]["arguments"] == {"q": "python"}


def test_client_rpc_error_handling():
    client = McpSubprocessClient(command="test_server")
    mock_proc = MagicMock()
    mock_proc.stdin = FakeStdin()
    err_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32601,
            "message": "Method not found",
        },
    }
    mock_proc.stdout = FakeStdout([json.dumps(err_payload) + "\n"])
    mock_proc.stderr = FakeStderr()
    mock_proc.poll.return_value = None
    client._process = mock_proc

    with pytest.raises(RuntimeError) as exc_info:
        client.call_tool("nonexistent")
    assert "MCP RPC Error (-32601): Method not found" in str(exc_info.value)


def test_client_close():
    client = McpSubprocessClient(command="test_server")
    mock_proc = MagicMock()
    mock_proc.stdin = FakeStdin()
    mock_proc.stdout = FakeStdout([])
    mock_proc.stderr = FakeStderr()
    client._process = mock_proc

    client.close()
    assert client._process is None
    assert mock_proc.terminate.called or mock_proc.stdin.closed


# =====================================================================
# 4. McpRegistry Unit Tests
# =====================================================================

def test_registry_discover_cwd_config(tmp_path, monkeypatch):
    hydra_dir = tmp_path / "project" / ".hydra"
    hydra_dir.mkdir(parents=True)
    config_file = hydra_dir / "mcp_servers.json"
    config_file.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    monkeypatch.chdir(tmp_path / "project")
    discovered = McpRegistry.discover_config_path()
    assert os.path.abspath(discovered) == os.path.abspath(str(config_file))


def test_registry_discover_fallback(tmp_path, monkeypatch):
    empty_cwd = tmp_path / "empty_dir"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)
    discovered = McpRegistry.discover_config_path()
    assert discovered == get_default_home_config()


def test_registry_load_mcp_servers_format(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "bearer_token_99")
    config_file = tmp_path / "mcp_servers.json"
    config_content = {
        "mcpServers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${TEST_API_TOKEN}"},
            }
        }
    }
    config_file.write_text(json.dumps(config_content), encoding="utf-8")

    registry = McpRegistry(config_path=str(config_file))
    assert "github" in registry._server_configs
    cfg = registry._server_configs["github"]
    assert cfg["command"] == "npx"
    assert cfg["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "bearer_token_99"


def test_registry_load_servers_format(tmp_path):
    config_file = tmp_path / "mcp_servers.json"
    config_content = {
        "servers": {
            "sqlite": {
                "command": "uvx",
                "args": ["mcp-server-sqlite"],
            }
        }
    }
    config_file.write_text(json.dumps(config_content), encoding="utf-8")

    registry = McpRegistry(config_path=str(config_file))
    assert "sqlite" in registry._server_configs
    assert registry._server_configs["sqlite"]["command"] == "uvx"


def test_registry_load_flat_format(tmp_path):
    config_file = tmp_path / "mcp_servers.json"
    config_content = {
        "git": {
            "command": "uvx",
            "args": ["mcp-server-git"],
        }
    }
    config_file.write_text(json.dumps(config_content), encoding="utf-8")

    registry = McpRegistry(config_path=str(config_file))
    assert "git" in registry._server_configs
    assert registry._server_configs["git"]["command"] == "uvx"


def test_registry_tool_namespacing():
    registry = McpRegistry()

    mock_client = MagicMock()
    mock_client.list_tools.return_value = [
        {"name": "fetch", "description": "Fetch a webpage", "inputSchema": {}},
        {"name": "eval", "description": "Evaluate code", "inputSchema": {}},
    ]

    registry.register_client("browser", mock_client)
    tools = registry.get_tools()

    assert len(tools) == 2
    assert tools[0]["name"] == "browser__fetch"
    assert tools[0]["_original_name"] == "fetch"
    assert tools[1]["name"] == "browser__eval"
    assert tools[1]["_original_name"] == "eval"


def test_registry_get_openai_tools():
    registry = McpRegistry()

    mock_client = MagicMock()
    mock_client.list_tools.return_value = [
        {
            "name": "lookup",
            "description": "Look up database record",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Record ID"}
                },
                "required": ["id"],
            },
        }
    ]

    registry.register_client("db", mock_client)
    openai_tools = registry.get_openai_tools()

    assert len(openai_tools) == 1
    tool = openai_tools[0]
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "db__lookup"
    assert fn["description"] == "Look up database record"
    assert fn["parameters"]["type"] == "object"
    assert "id" in fn["parameters"]["properties"]
    assert fn["parameters"]["required"] == ["id"]


def test_registry_dispatch_routing():
    registry = McpRegistry()

    mock_client_git = MagicMock()
    mock_client_git.call_tool.return_value = {"content": [{"type": "text", "text": "commit successful"}]}

    mock_client_db = MagicMock()
    mock_client_db.call_tool.return_value = {"content": [{"type": "text", "text": "row inserted"}]}

    registry.register_client("git", mock_client_git)
    registry.register_client("db", mock_client_db)

    # Route to git
    res_git = registry.dispatch("git__commit", {"message": "feat: mcp"})
    assert res_git == {"content": [{"type": "text", "text": "commit successful"}]}
    mock_client_git.call_tool.assert_called_once_with("commit", {"message": "feat: mcp"})

    # Route to db
    res_db = registry.dispatch("db__insert", {"table": "users", "val": "alice"})
    assert res_db == {"content": [{"type": "text", "text": "row inserted"}]}
    mock_client_db.call_tool.assert_called_once_with("insert", {"table": "users", "val": "alice"})


def test_registry_dispatch_invalid_name():
    registry = McpRegistry()
    with pytest.raises(ValueError) as exc:
        registry.dispatch("invalid_tool_without_namespace", {})
    assert "Expected format: '<server_name>__<tool_name>'" in str(exc.value)


def test_registry_dispatch_unknown_server():
    registry = McpRegistry()
    with pytest.raises(KeyError) as exc:
        registry.dispatch("unknown__action", {})
    assert "Server 'unknown' not found in registry" in str(exc.value)


def test_registry_shutdown():
    registry = McpRegistry()

    mock_client_1 = MagicMock()
    mock_client_2 = MagicMock()

    registry.register_client("srv1", mock_client_1)
    registry.register_client("srv2", mock_client_2)

    registry.shutdown()

    mock_client_1.close.assert_called_once()
    mock_client_2.close.assert_called_once()
    assert len(registry._clients) == 0


def test_registry_context_manager():
    mock_client = MagicMock()
    with McpRegistry() as registry:
        registry.register_client("srv", mock_client)
    mock_client.close.assert_called_once()
