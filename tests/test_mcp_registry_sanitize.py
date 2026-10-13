"""Stale MCP configs from older Hydra versions must load repaired.

Covers the three failures seen in the desktop app: Brave search demanding
BRAVE_API_KEY, fetch 404ing on a nonexistent npm package, and sqlite
crashing against mcp>=2. Real config files on disk, real registry load.
"""

from __future__ import annotations

import json

from hydra_cli.mcp_registry import McpRegistry, sanitize_server_configs

STALE = {
    "mcpServers": {
        "fetch": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-fetch"]},
        "sqlite": {"command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "x.db"]},
        "brave-search": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env": {"BRAVE_API_KEY": "${BRAVE_API_KEY}"},
        },
        "memory": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
    }
}


def test_sanitize_repairs_known_broken_entries():
    out = sanitize_server_configs(STALE["mcpServers"])
    assert "brave-search" not in out
    assert out["searxng"]["args"] == ["-y", "mcp-searxng"]
    assert "BRAVE" not in json.dumps(out)
    assert out["fetch"] == {"command": "uvx", "args": ["mcp-server-fetch"]}
    assert out["sqlite"]["args"][:3] == ["--with", "mcp==1.*", "mcp-server-sqlite"]
    assert out["memory"]["args"] == ["-y", "@modelcontextprotocol/server-memory"]


def test_sanitize_leaves_current_entries_alone():
    current = {
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
        "sqlite": {"command": "uvx", "args": ["--with", "mcp==1.*", "mcp-server-sqlite"]},
        "searxng": {"command": "npx", "args": ["-y", "mcp-searxng"]},
    }
    assert sanitize_server_configs(current) == current


def test_sanitize_normalizes_shell_unsafe_pin():
    # mcp<2 under a Windows shell launch dies on cmd.exe redirection.
    stale = {"sqlite": {"command": "uvx", "args": ["--with", "mcp<2", "mcp-server-sqlite"]}}
    out = sanitize_server_configs(stale)
    assert out["sqlite"]["args"] == ["--with", "mcp==1.*", "mcp-server-sqlite"]


def test_registry_loads_stale_config_file_repaired(tmp_path):
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps(STALE), encoding="utf-8")
    registry = McpRegistry(config_path=str(cfg), auto_load=False)
    names = set(registry._server_configs)
    assert "brave-search" not in names
    assert "searxng" in names
    assert registry._server_configs["fetch"]["command"] == "uvx"
    assert registry._server_configs["sqlite"]["args"][1] == "mcp==1.*"
