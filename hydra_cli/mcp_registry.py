"""
Zero-dependency MCP client registry for Hydra CLI.
Discovers and manages MCP server configurations, tool namespacing,
OpenAI function calling translation, and dispatch routing.
"""

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional

from hydra_cli.config import load_dotenv
from hydra_cli.mcp import McpSubprocessClient


def get_default_home_config() -> str:
    return os.path.join(os.path.expanduser("~"), ".hydra", "mcp_servers.json")


def get_default_cwd_config() -> str:
    return os.path.join(os.getcwd(), ".hydra", "mcp_servers.json")


DEFAULT_PACKAGE_CONFIG = os.path.join(os.path.dirname(__file__), "mcp_servers.default.json")
DEFAULT_HOME_CONFIG = get_default_home_config()
DEFAULT_CWD_CONFIG = get_default_cwd_config()
DEFAULT_CONFIG_PATH = DEFAULT_HOME_CONFIG


def interpolate_env_vars(obj: Any) -> Any:
    """
    Recursively interpolate ${VAR_NAME} or ${VAR_NAME:-default} in strings,
    lists, and dictionaries using values from os.environ.
    """
    if isinstance(obj, str):
        def _replace(match: re.Match) -> str:
            expr = match.group(1)
            if ":-" in expr:
                var_name, default_val = expr.split(":-", 1)
            else:
                var_name, default_val = expr, ""
            return os.environ.get(var_name, default_val)

        return re.sub(r"\$\{([^}]+)\}", _replace, obj)
    elif isinstance(obj, dict):
        return {k: interpolate_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [interpolate_env_vars(item) for item in obj]
    return obj


class McpRegistry:
    """
    Registry for configuring, launching, and routing requests to MCP subprocess servers.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        servers: Optional[Dict[str, Any]] = None,
        client_factory: Optional[Callable[..., Any]] = None,
        auto_load: bool = True,
    ):
        load_dotenv()
        self.client_factory = client_factory or McpSubprocessClient
        self.config_path: Optional[str] = None
        self._server_configs: Dict[str, Dict[str, Any]] = {}
        self._clients: Dict[str, Any] = {}
        self._tools_cache: Optional[List[Dict[str, Any]]] = None

        if config_path is not None:
            if not os.path.isfile(config_path):
                raise FileNotFoundError(f"Configuration file not found: {config_path}")
            self.load_config(config_path)
        elif auto_load and servers is None:
            discovered = self.discover_config_path()
            if discovered and os.path.isfile(discovered):
                self.load_config(discovered)
            else:
                self.config_path = discovered or get_default_home_config()

        if servers:
            interpolated = interpolate_env_vars(self._extract_servers(servers))
            for name, srv_conf in interpolated.items():
                self.register_server(name, srv_conf)

    @staticmethod
    def discover_config_path() -> str:
        """
        Locate mcp_servers.json configuration file.
        Checks ./.hydra/mcp_servers.json first, then ~/.hydra/mcp_servers.json,
        or returns the default config path (~/.hydra/mcp_servers.json).
        """
        cwd_path = get_default_cwd_config()
        if os.path.isfile(cwd_path):
            return cwd_path
        home_path = get_default_home_config()
        if os.path.isfile(home_path):
            return home_path
        return home_path

    @staticmethod
    def _extract_servers(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Normalize configuration payload to a dictionary of server configs."""
        if not isinstance(data, dict):
            return {}
        if "mcpServers" in data and isinstance(data["mcpServers"], dict):
            return data["mcpServers"]
        if "servers" in data and isinstance(data["servers"], dict):
            return data["servers"]
        return data

    def load_config(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Load and parse server configurations from a JSON file with env interpolation."""
        target_path = path or self.discover_config_path()
        if not target_path or not os.path.isfile(target_path):
            return {}

        self.config_path = target_path
        with open(target_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        interpolated = interpolate_env_vars(raw_data)
        servers = self._extract_servers(interpolated)
        for name, srv_conf in servers.items():
            self.register_server(name, srv_conf)
        return servers

    def register_server(self, name: str, config: Dict[str, Any]) -> None:
        """Register a server configuration by name."""
        interpolated = interpolate_env_vars(config)
        self._server_configs[name] = interpolated
        self._tools_cache = None

    def register_client(self, name: str, client: Any) -> None:
        """Register an already initialized or mock client directly."""
        self._clients[name] = client
        self._tools_cache = None

    def get_client(self, server_name: str) -> Any:
        """Retrieve or lazily spawn an McpSubprocessClient for a registered server."""
        if server_name in self._clients:
            return self._clients[server_name]

        if server_name not in self._server_configs:
            raise KeyError(f"Server '{server_name}' not found in registry")

        cfg = self._server_configs[server_name]
        command = cfg.get("command", "")
        args = cfg.get("args", [])
        env = cfg.get("env")
        cwd = cfg.get("cwd")

        client = self.client_factory(command=command, args=args, env=env, cwd=cwd)
        client.start()
        self._clients[server_name] = client
        return client

    def get_tools(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """
        List all tools across all registered servers, namespaced as <server_name>__<tool_name>.
        """
        if self._tools_cache is not None and not refresh:
            return self._tools_cache

        tools: List[Dict[str, Any]] = []
        all_servers = sorted(set(self._server_configs.keys()) | set(self._clients.keys()))

        for s_name in all_servers:
            try:
                client = self.get_client(s_name)
                raw_tools = client.list_tools()
                for tool in raw_tools:
                    orig_name = tool.get("name", "")
                    namespaced_name = f"{s_name}__{orig_name}"
                    tool_copy = dict(tool)
                    tool_copy["name"] = namespaced_name
                    tool_copy["_server"] = s_name
                    tool_copy["_original_name"] = orig_name
                    tools.append(tool_copy)
            except Exception:
                continue

        self._tools_cache = tools
        return tools

    def get_openai_tools(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Convert registered MCP tools into OpenAI function calling schema:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        tools = self.get_tools(refresh=refresh)
        openai_tools: List[Dict[str, Any]] = []

        for tool in tools:
            schema: Dict[str, Any] = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema") or {
                    "type": "object",
                    "properties": {},
                },
            }
            openai_tools.append({
                "type": "function",
                "function": schema,
            })

        return openai_tools

    def dispatch(self, qualified_tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """
        Route tool execution to the target client.
        qualified_tool_name must follow <server_name>__<tool_name>.
        """
        if "__" not in qualified_tool_name:
            raise ValueError(
                f"Invalid qualified tool name '{qualified_tool_name}'. Expected format: '<server_name>__<tool_name>'"
            )

        server_name, tool_name = qualified_tool_name.split("__", 1)
        client = self.get_client(server_name)
        return client.call_tool(tool_name, arguments if arguments is not None else {})

    def shutdown(self) -> None:
        """Clean up and close all running client processes."""
        for client in list(self._clients.values()):
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()
        self._tools_cache = None

    def __enter__(self) -> "McpRegistry":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown()

    def __del__(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass
