"""
Zero-dependency Model Context Protocol (MCP) subprocess client for Hydra CLI.
Manages JSON-RPC 2.0 stdio subprocess connection with thread-safe RPC lock
and background daemon thread for stderr to prevent pipe deadlocks.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from hydra_cli._version import __version__

# Variables a server process gets from the parent. Everything else, API keys
# included, stays out unless the server's config names it in "env" or "env_passthrough".
BASE_ENV_KEYS = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
    "TZ", "TMPDIR", "TEMP", "TMP", "PWD",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
    "NVM_DIR", "NVM_BIN", "NODE_PATH", "npm_config_cache", "npm_config_prefix",
    "NPM_CONFIG_CACHE", "NPM_CONFIG_PREFIX", "UV_CACHE_DIR", "UV_TOOL_DIR", "UV_PYTHON_INSTALL_DIR",
    "VIRTUAL_ENV",
    # Windows
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "HOMEDRIVE", "HOMEPATH", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)

DEFAULT_RPC_TIMEOUT = 60.0
# The first npx/uvx launch may download the package, so the handshake gets longer.
DEFAULT_INIT_TIMEOUT = 120.0


def minimal_env(
    extra: Optional[Dict[str, str]] = None,
    passthrough: Optional[Iterable[str]] = None,
    source: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Environment for an MCP server: base system variables, named passthrough, then explicit extras."""
    parent = os.environ if source is None else source
    keys = set(BASE_ENV_KEYS)
    if sys.platform == "win32":
        upper = {k.upper(): k for k in parent}
        env = {upper[k.upper()]: parent[upper[k.upper()]] for k in keys if k.upper() in upper}
    else:
        env = {k: parent[k] for k in keys if k in parent}
    for name in passthrough or ():
        if name in parent:
            env[name] = parent[name]
    for key, value in (extra or {}).items():
        if value is None:
            continue
        env[str(key)] = str(value)
    return env


def _env_timeout(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


def should_use_shell(command: str, platform: Optional[str] = None) -> bool:
    """
    Determine if subprocess should run with shell=True.
    On Windows (win32), use shell=True when command doesn't end with .exe/.cmd, or for npx/uvx.
    """
    plat = platform if platform is not None else sys.platform
    if plat != "win32":
        return False

    cmd_lower = command.strip().lower()
    base_name = os.path.basename(cmd_lower)
    name, ext = os.path.splitext(base_name)

    if name in ("npx", "uvx") or cmd_lower in ("npx", "uvx"):
        return True
    if ext in (".exe", ".cmd"):
        return False
    return True


class McpSubprocessClient:
    """
    MCP stdio subprocess client implementing JSON-RPC 2.0.
    Handles startup handshake, tool discovery, execution, and process lifecycle.
    """

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        env_passthrough: Optional[Iterable[str]] = None,
        timeout: Optional[float] = None,
        init_timeout: Optional[float] = None,
    ):
        self.command = command
        self.args: List[str] = list(args) if args else []
        self.env = env
        self.cwd = cwd
        self.env_passthrough: List[str] = list(env_passthrough or [])
        self.timeout = float(timeout) if timeout else _env_timeout("HYDRA_MCP_TIMEOUT", DEFAULT_RPC_TIMEOUT)
        self.init_timeout = (
            float(init_timeout) if init_timeout
            else max(self.timeout, _env_timeout("HYDRA_MCP_INIT_TIMEOUT", DEFAULT_INIT_TIMEOUT))
        )
        self._stdout_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stdout_reader_for: Any = None

        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._req_id = 0
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_lines: List[str] = []
        self._closed = False

        self.server_info: Dict[str, Any] = {}
        self.server_capabilities: Dict[str, Any] = {}

    @property
    def is_running(self) -> bool:
        """True if the subprocess is currently active."""
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_output(self) -> str:
        """Recent captured stderr lines from the server process."""
        return "\n".join(self._stderr_lines)

    def _drain_stderr(self) -> None:
        """Background daemon thread target to drain stderr and prevent pipe buffer deadlocks."""
        if not self._process or not self._process.stderr:
            return
        try:
            for line in iter(self._process.stderr.readline, ""):
                if not line:
                    break
                stripped = line.rstrip("\r\n")
                self._stderr_lines.append(stripped)
                if len(self._stderr_lines) > 500:
                    self._stderr_lines.pop(0)
        except Exception:
            pass

    def _drain_stdout(self, stream: Any, sink: "queue.Queue[Optional[str]]") -> None:
        """Background reader so a silent server cannot block the caller past its timeout."""
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                sink.put(line)
        except Exception:
            pass
        finally:
            sink.put(None)

    def _ensure_stdout_reader(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None or self._stdout_reader_for is proc:
            return
        self._stdout_reader_for = proc
        self._stdout_queue = queue.Queue()
        thread = threading.Thread(target=self._drain_stdout, args=(proc.stdout, self._stdout_queue), daemon=True)
        thread.start()

    def build_env(self) -> Dict[str, str]:
        """The environment the server process receives."""
        return minimal_env(extra=self.env, passthrough=self.env_passthrough)

    def start(self) -> None:
        """Start subprocess, initiate background stderr reader, and perform MCP handshake."""
        with self._lock:
            if self._process is not None:
                return
            self._closed = False
            use_shell = should_use_shell(self.command)
            cmd = [self.command] + self.args

            full_env = self.build_env()

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=use_shell,
                env=full_env,
                cwd=self.cwd,
            )

            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()
            self._ensure_stdout_reader()

        # Handshake outside lock so _send_rpc can acquire lock
        try:
            self._initialize()
        except Exception:
            self.close()
            raise

    def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC 2.0 notification (no response expected)."""
        with self._lock:
            if self._process is None or self._process.stdin is None:
                return
            payload: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params if params is not None else {},
            }
            msg = json.dumps(payload) + "\n"
            self._process.stdin.write(msg)
            self._process.stdin.flush()

    def _send_rpc(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """
        Send a JSON-RPC 2.0 request and wait for the matching response, at most `timeout` seconds.
        Thread-safe under self._lock.
        """
        wait = float(timeout) if timeout else self.timeout
        with self._lock:
            if self._process is None or self._process.stdin is None or self._process.stdout is None:
                raise RuntimeError("MCP client process is not running. Call start() first.")

            self._req_id += 1
            req_id = self._req_id
            payload: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params if params is not None else {},
            }
            msg = json.dumps(payload) + "\n"
            self._ensure_stdout_reader()
            self._process.stdin.write(msg)
            self._process.stdin.flush()

            deadline = time.monotonic() + wait
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"MCP server did not answer '{method}' within {wait:g}s")
                try:
                    line = self._stdout_queue.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(f"MCP server did not answer '{method}' within {wait:g}s")
                if not line:
                    self._stdout_queue.put(None)  # keep EOF visible to later calls
                    exit_code = self._process.poll() if self._process else None
                    tail = "\n".join(self._stderr_lines[-5:]) if self._stderr_lines else ""
                    raise RuntimeError(
                        f"MCP server closed stdout unexpectedly (exit code {exit_code}). {tail}".strip()
                    )

                line = line.strip()
                if not line:
                    continue

                try:
                    res = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(res, dict):
                    continue

                res_id = res.get("id")
                if res_id == req_id or str(res_id) == str(req_id):
                    if "error" in res and res["error"]:
                        err = res["error"]
                        if isinstance(err, dict):
                            code = err.get("code", "unknown")
                            message = err.get("message", "Unknown error")
                            raise RuntimeError(f"MCP RPC Error ({code}): {message}")
                        raise RuntimeError(f"MCP RPC Error: {err}")
                    return res.get("result")

    def _initialize(self) -> Dict[str, Any]:
        """Execute MCP initialize handshake and send notifications/initialized."""
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "hydra",
                "version": __version__,
            },
        }
        result = self._send_rpc("initialize", params, timeout=self.init_timeout)
        if isinstance(result, dict):
            self.server_info = result.get("serverInfo", {})
            self.server_capabilities = result.get("capabilities", {})

        self._send_notification("notifications/initialized", {})
        return result or {}

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all tools exposed by the MCP server."""
        result = self._send_rpc("tools/list", {})
        if isinstance(result, dict):
            return result.get("tools", [])
        if isinstance(result, list):
            return result
        return []

    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call a specific tool on the MCP server with the provided arguments."""
        params = {
            "name": tool_name,
            "arguments": arguments if arguments is not None else {},
        }
        return self._send_rpc("tools/call", params)

    def close(self) -> None:
        """Terminate subprocess and cleanly join stderr background thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            proc = self._process
            self._process = None

            if proc:
                try:
                    if proc.stdin and not proc.stdin.closed:
                        proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=0.5)
                except Exception:
                    pass

        if self._stderr_thread and self._stderr_thread.is_alive():
            try:
                self._stderr_thread.join(timeout=0.5)
            except Exception:
                pass

    def __enter__(self) -> "McpSubprocessClient":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
