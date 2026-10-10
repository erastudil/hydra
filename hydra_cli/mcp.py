"""
Zero-dependency Model Context Protocol (MCP) subprocess client for Hydra CLI.
Manages JSON-RPC 2.0 stdio subprocess connection with thread-safe RPC lock
and background daemon thread for stderr to prevent pipe deadlocks.
"""

import fnmatch
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import concurrent.futures
from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

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


class McpTimeoutGuard:
    """Manages per-tool timeout policies, cancellation notifications, and timeout telemetry."""

    def __init__(self, default_timeout: float = DEFAULT_RPC_TIMEOUT) -> None:
        self.default_timeout = float(default_timeout)
        self.reset()

    def reset(self) -> None:
        """Reset timeout policies and telemetry counters."""
        self._rules: List[Tuple[str, float]] = []
        self._timeout_counts: Dict[str, int] = {}
        self._cancellations_sent: int = 0
        self._last_timeout_timestamp: Optional[float] = None

    def register_rule(self, pattern: str, timeout_seconds: float) -> None:
        """Register pattern-based timeout rule for matching tools."""
        if timeout_seconds <= 0:
            raise ValueError("Timeout seconds must be strictly positive.")
        clean_pat = pattern.strip().lower()
        self._rules.append((clean_pat, float(timeout_seconds)))

    def resolve_timeout(self, tool_name: str, explicit_timeout: Optional[float] = None) -> float:
        """Resolve effective timeout for tool name across explicit, pattern, and default rules."""
        if explicit_timeout is not None and explicit_timeout > 0:
            return float(explicit_timeout)
        target = tool_name.strip().lower()
        for pattern, t_val in reversed(self._rules):
            if fnmatch.fnmatch(target, pattern):
                return t_val
        return self.default_timeout

    def record_timeout(self, tool_name: str) -> None:
        """Record timeout occurrence for telemetry tracking."""
        self._last_timeout_timestamp = time.time()
        self._timeout_counts[tool_name] = self._timeout_counts.get(tool_name, 0) + 1

    def record_cancellation(self) -> None:
        """Record transmission of cancellation notification."""
        self._cancellations_sent += 1

    def get_metrics(self) -> Dict[str, Any]:
        """Return timeout guard telemetry statistics."""
        return {
            "default_timeout": self.default_timeout,
            "rules_count": len(self._rules),
            "total_timeouts": sum(self._timeout_counts.values()),
            "cancellations_sent": self._cancellations_sent,
            "last_timeout": self._last_timeout_timestamp,
            "timeouts_by_tool": dict(self._timeout_counts),
        }


_DEFAULT_TIMEOUT_GUARD = McpTimeoutGuard()


def get_default_timeout_guard() -> McpTimeoutGuard:
    """Return default singleton MCP timeout guard."""
    return _DEFAULT_TIMEOUT_GUARD


def reset_timeout_guard() -> None:
    """Reset global MCP timeout guard state."""
    _DEFAULT_TIMEOUT_GUARD.reset()


DEFAULT_NAMESPACE_SEPARATOR = "__"


def parse_qualified_tool_name(
    qualified_name: str,
    separator: str = DEFAULT_NAMESPACE_SEPARATOR,
) -> Tuple[str, str]:
    """Parse qualified tool name into namespace and raw tool name tuple."""
    if not qualified_name or separator not in qualified_name:
        raise ValueError(
            f"Invalid qualified tool name '{qualified_name}'. Expected format: '<namespace>{separator}<tool>'"
        )
    parts = qualified_name.split(separator, 1)
    namespace, tool = parts[0].strip(), parts[1].strip()
    if not namespace or not tool:
        raise ValueError(
            f"Malformed qualified tool name '{qualified_name}'. Namespace and tool must both be non-empty."
        )
    return namespace, tool


def format_qualified_tool_name(
    namespace: str,
    tool_name: str,
    separator: str = DEFAULT_NAMESPACE_SEPARATOR,
) -> str:
    """Format namespace and tool name into unified qualified tool string."""
    clean_ns = str(namespace).strip()
    clean_tool = str(tool_name).strip()
    if not clean_ns or not clean_tool:
        raise ValueError("Namespace and tool name must both be non-empty.")
    return f"{clean_ns}{separator}{clean_tool}"


class McpNamespaceRouter:
    """Multi-namespace router managing server namespaces, aliases, and tool dispatch."""

    def __init__(
        self,
        separator: str = DEFAULT_NAMESPACE_SEPARATOR,
        manifest_cache: Optional[Any] = None,
        result_sanitizer: Optional[Any] = None,
    ) -> None:
        self.separator = separator
        self.manifest_cache = manifest_cache
        self.result_sanitizer = result_sanitizer
        self.reset()

    def reset(self) -> None:
        """Reset all registered namespaces, aliases, and counters."""
        self._namespaces: Dict[str, Any] = {}
        self._aliases: Dict[str, str] = {}
        self._dispatch_count: int = 0

    def register_client(
        self,
        namespace: str,
        client: Any,
        aliases: Optional[List[str]] = None,
    ) -> None:
        """Register client under primary namespace and optional aliases."""
        clean_ns = namespace.strip().lower()
        if not clean_ns:
            raise ValueError("Namespace must not be empty.")
        self._namespaces[clean_ns] = client
        if aliases:
            for alias in aliases:
                self.add_alias(alias, clean_ns)

    def add_alias(self, alias: str, target_namespace: str) -> None:
        """Register shorthand alias pointing to target namespace."""
        clean_alias = alias.strip().lower()
        clean_target = target_namespace.strip().lower()
        if clean_alias == clean_target:
            return
        self._aliases[clean_alias] = clean_target

    def resolve_namespace(self, name: str) -> str:
        """Resolve alias to canonical namespace with cycle protection."""
        current = name.strip().lower()
        visited = set()
        while current in self._aliases:
            if current in visited:
                break
            visited.add(current)
            current = self._aliases[current]
        return current

    def get_client(self, namespace: str) -> Optional[Any]:
        """Retrieve client associated with namespace or alias."""
        canonical = self.resolve_namespace(namespace)
        return self._namespaces.get(canonical)

    def list_namespaces(self) -> List[str]:
        """Return list of canonical registered namespaces."""
        return sorted(self._namespaces.keys())

    def list_all_tools(self) -> List[Dict[str, Any]]:
        """List all tools across all registered namespaces with qualified names."""
        all_tools: List[Dict[str, Any]] = []
        for ns, client in self._namespaces.items():
            if hasattr(client, "list_tools"):
                try:
                    raw_tools = client.list_tools()
                    for t in raw_tools:
                        orig = t.get("name", "")
                        tool_entry = dict(t)
                        tool_entry["name"] = format_qualified_tool_name(ns, orig, self.separator)
                        tool_entry["_namespace"] = ns
                        tool_entry["_original_name"] = orig
                        all_tools.append(tool_entry)
                except Exception:
                    continue
        return all_tools

    def dispatch(
        self,
        qualified_tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        sanitize: bool = False,
    ) -> Any:
        """Route tool execution through namespace router."""
        self._dispatch_count += 1
        ns, tool_name = parse_qualified_tool_name(qualified_tool_name, self.separator)
        canonical = self.resolve_namespace(ns)
        client = self._namespaces.get(canonical)
        if not client:
            raise KeyError(f"Unknown MCP namespace '{ns}'")
        if hasattr(client, "call_tool"):
            if timeout is not None:
                try:
                    res = client.call_tool(tool_name, arguments or {}, timeout=timeout)
                except TypeError:
                    res = client.call_tool(tool_name, arguments or {})
            else:
                res = client.call_tool(tool_name, arguments or {})

            if sanitize or self.result_sanitizer:
                sanitizer = self.result_sanitizer or get_default_result_sanitizer()
                return sanitizer.sanitize(res)
            return res
        raise RuntimeError(f"Client for namespace '{ns}' does not implement call_tool")

    def dispatch_concurrent(
        self,
        calls: Sequence[Any],
        max_workers: Optional[int] = None,
        timeout: Optional[float] = None,
        fail_fast: bool = False,
        sanitize: bool = False,
    ) -> List["McpDispatchResult"]:
        """Dispatch batch of tool calls concurrently across registered namespaces."""
        dispatcher = McpConcurrentDispatcher(
            router=self,
            max_workers=max_workers or 8,
            default_timeout=timeout,
            sanitizer=self.result_sanitizer if (sanitize or self.result_sanitizer) else None,
        )
        return dispatcher.dispatch_batch(
            calls,
            router=self,
            max_workers=max_workers,
            timeout=timeout,
            fail_fast=fail_fast,
            sanitize=sanitize,
        )

    def register_lazy_client(
        self,
        namespace: str,
        factory_or_command: Any,
        args: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
        predeclared_tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Any:
        """Register lazily-spawned MCP client under namespace."""
        if "manifest_cache" not in kwargs and self.manifest_cache is not None:
            kwargs["manifest_cache"] = self.manifest_cache
        lazy_client = McpLazyClient(
            factory_or_command=factory_or_command,
            args=args,
            namespace=namespace,
            predeclared_tools=predeclared_tools,
            **kwargs,
        )
        self.register_client(namespace, lazy_client, aliases=aliases)
        return lazy_client

    def discover_and_register(
        self,
        discoverer: Optional[Any] = None,
        lazy: bool = True,
    ) -> int:
        """Discover server configurations and register into router."""
        disc = discoverer or get_default_discoverer()
        return disc.load_into_router(self, lazy=lazy)

    def get_metrics(self) -> Dict[str, Any]:
        """Return telemetry counters for namespace routing."""
        lazy_count = sum(
            1 for c in self._namespaces.values()
            if isinstance(c, McpLazyClient) or getattr(c, "lazy", False)
        )
        return {
            "registered_namespaces": len(self._namespaces),
            "registered_aliases": len(self._aliases),
            "lazy_clients": lazy_count,
            "total_dispatches": self._dispatch_count,
        }


_DEFAULT_NAMESPACE_ROUTER = McpNamespaceRouter()


def get_default_namespace_router() -> McpNamespaceRouter:
    """Return default singleton MCP namespace router."""
    return _DEFAULT_NAMESPACE_ROUTER


def reset_namespace_router() -> None:
    """Reset global MCP namespace router state."""
    _DEFAULT_NAMESPACE_ROUTER.reset()


@dataclass
class McpToolCall:
    """Specification of individual tool call for concurrent or sequential dispatch."""

    tool_name: str
    arguments: Optional[Dict[str, Any]] = None
    call_id: Optional[str] = None
    timeout: Optional[float] = None

    def __post_init__(self) -> None:
        if self.arguments is None:
            self.arguments = {}


def normalize_tool_call(call: Any) -> McpToolCall:
    """Normalize arbitrary tool call specification into typed McpToolCall."""
    if isinstance(call, McpToolCall):
        return call
    if isinstance(call, dict):
        name = call.get("tool_name") or call.get("name") or call.get("tool") or ""
        arguments = call.get("arguments") or call.get("args") or {}
        call_id = call.get("call_id") or call.get("id")
        timeout = call.get("timeout")
        return McpToolCall(
            tool_name=str(name),
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
            call_id=str(call_id) if call_id is not None else None,
            timeout=float(timeout) if timeout is not None else None,
        )
    if isinstance(call, (list, tuple)):
        name = str(call[0]) if len(call) > 0 else ""
        arguments = call[1] if len(call) > 1 and isinstance(call[1], dict) else {}
        call_id = str(call[2]) if len(call) > 2 and call[2] is not None else None
        timeout = float(call[3]) if len(call) > 3 and call[3] is not None else None
        return McpToolCall(
            tool_name=name,
            arguments=dict(arguments),
            call_id=call_id,
            timeout=timeout,
        )
    return McpToolCall(tool_name=str(call), arguments={})


@dataclass
class McpDispatchResult:
    """Result envelope capturing outcome, timing, and error state of tool dispatch."""

    tool_name: str
    call_id: Optional[str] = None
    namespace: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: Optional[str] = None
    status: str = "success"
    duration_sec: float = 0.0

    @property
    def is_success(self) -> bool:
        """Return True when dispatch succeeded without error."""
        return self.status == "success"

    @property
    def is_error(self) -> bool:
        """Return True when dispatch failed or encountered error."""
        return self.status in ("error", "timeout", "cancelled")

    def to_dict(self) -> Dict[str, Any]:
        """Convert result envelope into standard dictionary."""
        return {
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "namespace": self.namespace,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "status": self.status,
            "duration_sec": self.duration_sec,
        }


class McpConcurrentDispatcher:
    """Concurrent dispatcher executing parallel MCP tool calls across namespaces."""

    def __init__(
        self,
        router: Optional[McpNamespaceRouter] = None,
        max_workers: int = 8,
        default_timeout: Optional[float] = None,
        sanitizer: Optional[Any] = None,
    ) -> None:
        self.router = router
        self.max_workers = max(1, int(max_workers))
        self.default_timeout = default_timeout
        self.sanitizer = sanitizer
        self._lock = threading.RLock()
        self._metrics: Dict[str, Any] = {
            "total_batches": 0,
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "timeout_calls": 0,
            "cancelled_calls": 0,
            "peak_concurrency": 0,
            "total_duration_sec": 0.0,
        }

    def _execute_single(
        self,
        call: McpToolCall,
        router: McpNamespaceRouter,
        timeout_override: Optional[float] = None,
        sanitize: bool = False,
    ) -> McpDispatchResult:
        """Execute single tool call under timeout guard and record execution duration."""
        sep = getattr(router, "separator", DEFAULT_NAMESPACE_SEPARATOR)
        ns, _ = parse_qualified_tool_name(call.tool_name, sep)
        start_time = time.perf_counter()
        effective_timeout = (
            call.timeout
            if call.timeout is not None
            else (timeout_override if timeout_override is not None else self.default_timeout)
        )

        try:
            output = router.dispatch(call.tool_name, call.arguments, timeout=effective_timeout)
            if sanitize or self.sanitizer:
                effective_sanitizer = self.sanitizer or get_default_result_sanitizer()
                output = effective_sanitizer.sanitize(output)
            duration = round(time.perf_counter() - start_time, 4)
            return McpDispatchResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                namespace=ns,
                arguments=call.arguments or {},
                result=output,
                error=None,
                status="success",
                duration_sec=duration,
            )
        except TimeoutError as exc:
            duration = round(time.perf_counter() - start_time, 4)
            return McpDispatchResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                namespace=ns,
                arguments=call.arguments or {},
                result=None,
                error=f"Timeout: {exc}",
                status="timeout",
                duration_sec=duration,
            )
        except Exception as exc:
            duration = round(time.perf_counter() - start_time, 4)
            err_msg = str(exc)
            status = "timeout" if "timeout" in err_msg.lower() else "error"
            return McpDispatchResult(
                tool_name=call.tool_name,
                call_id=call.call_id,
                namespace=ns,
                arguments=call.arguments or {},
                result=None,
                error=err_msg,
                status=status,
                duration_sec=duration,
            )

    def dispatch_batch(
        self,
        calls: Sequence[Any],
        router: Optional[McpNamespaceRouter] = None,
        max_workers: Optional[int] = None,
        timeout: Optional[float] = None,
        fail_fast: bool = False,
        sanitize: bool = False,
    ) -> List[McpDispatchResult]:
        """Execute batch of tool calls concurrently while preserving submission sequence."""
        if not calls:
            return []

        effective_router = router or self.router or get_default_namespace_router()
        normalized_calls = [normalize_tool_call(c) for c in calls]
        pool_size = min(len(normalized_calls), max(1, max_workers or self.max_workers))

        with self._lock:
            self._metrics["total_batches"] += 1
            self._metrics["total_calls"] += len(normalized_calls)
            if pool_size > self._metrics["peak_concurrency"]:
                self._metrics["peak_concurrency"] = pool_size

        batch_start = time.perf_counter()
        results: List[Optional[McpDispatchResult]] = [None] * len(normalized_calls)

        with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as executor:
            futures_map: Dict[concurrent.futures.Future, Tuple[int, McpToolCall]] = {
                executor.submit(self._execute_single, call, effective_router, timeout, sanitize): (idx, call)
                for idx, call in enumerate(normalized_calls)
            }

            if fail_fast:
                try:
                    for fut in concurrent.futures.as_completed(futures_map.keys(), timeout=timeout):
                        idx, call = futures_map[fut]
                        try:
                            res = fut.result()
                        except Exception as exc:
                            ns, _ = parse_qualified_tool_name(call.tool_name, effective_router.separator)
                            res = McpDispatchResult(
                                tool_name=call.tool_name,
                                call_id=call.call_id,
                                namespace=ns,
                                arguments=call.arguments or {},
                                result=None,
                                error=str(exc),
                                status="error",
                                duration_sec=0.0,
                            )
                        results[idx] = res

                        if res.status in ("error", "timeout"):
                            for other_fut, (other_idx, other_call) in futures_map.items():
                                if other_fut != fut and not other_fut.done():
                                    other_fut.cancel()
                                    if results[other_idx] is None:
                                        ns_other, _ = parse_qualified_tool_name(
                                            other_call.tool_name, effective_router.separator
                                        )
                                        results[other_idx] = McpDispatchResult(
                                            tool_name=other_call.tool_name,
                                            call_id=other_call.call_id,
                                            namespace=ns_other,
                                            arguments=other_call.arguments or {},
                                            result=None,
                                            error="Execution cancelled due to fail_fast trigger",
                                            status="cancelled",
                                            duration_sec=0.0,
                                        )
                            break
                except concurrent.futures.TimeoutError:
                    for fut, (idx, call) in futures_map.items():
                        if results[idx] is None:
                            ns, _ = parse_qualified_tool_name(call.tool_name, effective_router.separator)
                            results[idx] = McpDispatchResult(
                                tool_name=call.tool_name,
                                call_id=call.call_id,
                                namespace=ns,
                                arguments=call.arguments or {},
                                result=None,
                                error="Batch execution timed out",
                                status="timeout",
                                duration_sec=round(time.perf_counter() - batch_start, 4),
                            )
            else:
                for fut, (idx, call) in futures_map.items():
                    call_timeout = (
                        call.timeout
                        if call.timeout is not None
                        else (timeout if timeout is not None else self.default_timeout)
                    )
                    try:
                        res = fut.result(timeout=call_timeout)
                    except concurrent.futures.TimeoutError:
                        ns, _ = parse_qualified_tool_name(call.tool_name, effective_router.separator)
                        res = McpDispatchResult(
                            tool_name=call.tool_name,
                            call_id=call.call_id,
                            namespace=ns,
                            arguments=call.arguments or {},
                            result=None,
                            error="Batch execution timed out",
                            status="timeout",
                            duration_sec=round(time.perf_counter() - batch_start, 4),
                        )
                    except Exception as exc:
                        ns, _ = parse_qualified_tool_name(call.tool_name, effective_router.separator)
                        res = McpDispatchResult(
                            tool_name=call.tool_name,
                            call_id=call.call_id,
                            namespace=ns,
                            arguments=call.arguments or {},
                            result=None,
                            error=str(exc),
                            status="error",
                            duration_sec=0.0,
                        )
                    results[idx] = res

        final_results: List[McpDispatchResult] = []
        for idx, item in enumerate(results):
            if item is None:
                call = normalized_calls[idx]
                ns, _ = parse_qualified_tool_name(call.tool_name, effective_router.separator)
                final_results.append(
                    McpDispatchResult(
                        tool_name=call.tool_name,
                        call_id=call.call_id,
                        namespace=ns,
                        arguments=call.arguments or {},
                        result=None,
                        error="Execution unfulfilled",
                        status="cancelled",
                        duration_sec=0.0,
                    )
                )
            else:
                final_results.append(item)

        batch_duration = round(time.perf_counter() - batch_start, 4)
        with self._lock:
            for r in final_results:
                if r.status == "success":
                    self._metrics["successful_calls"] += 1
                elif r.status == "timeout":
                    self._metrics["timeout_calls"] += 1
                    self._metrics["failed_calls"] += 1
                elif r.status == "cancelled":
                    self._metrics["cancelled_calls"] += 1
                else:
                    self._metrics["failed_calls"] += 1
            self._metrics["total_duration_sec"] = round(
                self._metrics["total_duration_sec"] + batch_duration, 4
            )

        return final_results

    def get_metrics(self) -> Dict[str, Any]:
        """Return snapshot copy of dispatcher execution metrics."""
        with self._lock:
            return dict(self._metrics)

    def reset_metrics(self) -> None:
        """Reset dispatcher metrics to initial zero values."""
        with self._lock:
            for key in self._metrics:
                if isinstance(self._metrics[key], (int, float)):
                    self._metrics[key] = 0 if isinstance(self._metrics[key], int) else 0.0


_DEFAULT_CONCURRENT_DISPATCHER = McpConcurrentDispatcher()


def get_default_concurrent_dispatcher() -> McpConcurrentDispatcher:
    """Return default singleton concurrent dispatcher."""
    return _DEFAULT_CONCURRENT_DISPATCHER


def reset_concurrent_dispatcher() -> None:
    """Reset global concurrent dispatcher singleton state and metrics."""
    global _DEFAULT_CONCURRENT_DISPATCHER
    _DEFAULT_CONCURRENT_DISPATCHER = McpConcurrentDispatcher()


def dispatch_concurrent(
    calls: Sequence[Any],
    router: Optional[McpNamespaceRouter] = None,
    max_workers: int = 8,
    timeout: Optional[float] = None,
    fail_fast: bool = False,
    sanitize: bool = False,
) -> List[McpDispatchResult]:
    """Dispatch batch of tool calls concurrently via default dispatcher."""
    dispatcher = get_default_concurrent_dispatcher()
    return dispatcher.dispatch_batch(
        calls,
        router=router,
        max_workers=max_workers,
        timeout=timeout,
        fail_fast=fail_fast,
        sanitize=sanitize,
    )


class McpResultSanitizer:
    """Sanitize raw MCP tool outputs against credential leaks, ANSI escapes, and payload overflow."""

    ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:\[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    BEARER_PATTERN = re.compile(r"Bearer\s+([a-zA-Z0-9_\-\.\=]{20,})", re.IGNORECASE)
    API_KEY_PATTERN = re.compile(r"\b(sk-[a-zA-Z0-9_\-]{20,}|ghp_[a-zA-Z0-9]{20,}|hf_[a-zA-Z0-9]{20,}|xox[baprs]-[a-zA-Z0-9\-]{20,})\b")
    KV_SECRET_PATTERN = re.compile(r"""(?i)(["']?(?:api[_-]?key|secret|token|password|auth_token)["']?\s*[:=]\s*["']?)([a-zA-Z0-9_\-\.\=]{12,})(["']?)""")
    PEM_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----")

    def __init__(
        self,
        max_chars: int = 65536,
        redact_secrets: bool = True,
        strip_ansi: bool = True,
        strip_null_bytes: bool = True,
        custom_patterns: Optional[List[re.Pattern]] = None,
    ) -> None:
        self.max_chars = max(256, int(max_chars))
        self.redact_secrets = redact_secrets
        self.strip_ansi = strip_ansi
        self.strip_null_bytes = strip_null_bytes
        self.custom_patterns = custom_patterns or []
        self._lock = threading.RLock()
        self._metrics: Dict[str, Any] = {
            "total_sanitized": 0,
            "redacted_secrets_count": 0,
            "stripped_ansi_count": 0,
            "truncated_payloads_count": 0,
            "null_bytes_cleaned_count": 0,
        }

    def sanitize_text(self, text: str) -> str:
        """Sanitize textual payload through credential redaction, ANSI stripping, and length bounding."""
        if not text:
            return ""

        redactions = 0
        ansi_count = 0
        null_count = 0
        truncated = False

        result = text

        if self.strip_ansi and "\x1B" in result:
            matches = len(self.ANSI_ESCAPE_PATTERN.findall(result))
            if matches > 0:
                ansi_count += matches
                result = self.ANSI_ESCAPE_PATTERN.sub("", result)

        if self.strip_null_bytes and "\x00" in result:
            null_count += result.count("\x00")
            result = result.replace("\x00", "")

        if self.redact_secrets:
            if "-----BEGIN " in result:
                pem_matches = len(self.PEM_KEY_PATTERN.findall(result))
                if pem_matches > 0:
                    redactions += pem_matches
                    result = self.PEM_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", result)

            if "sk-" in result or "ghp_" in result or "hf_" in result or "xox" in result:
                key_matches = len(self.API_KEY_PATTERN.findall(result))
                if key_matches > 0:
                    redactions += key_matches
                    result = self.API_KEY_PATTERN.sub("[REDACTED_API_KEY]", result)

            if "Bearer " in result or "bearer " in result:
                bearer_matches = len(self.BEARER_PATTERN.findall(result))
                if bearer_matches > 0:
                    redactions += bearer_matches
                    result = self.BEARER_PATTERN.sub("Bearer [REDACTED_BEARER]", result)

            kv_matches = len(self.KV_SECRET_PATTERN.findall(result))
            if kv_matches > 0:
                redactions += kv_matches
                result = self.KV_SECRET_PATTERN.sub(r"\g<1>[REDACTED_SECRET]\g<3>", result)

            for pat in self.custom_patterns:
                custom_matches = len(pat.findall(result))
                if custom_matches > 0:
                    redactions += custom_matches
                    result = pat.sub("[REDACTED]", result)

        if len(result) > self.max_chars:
            truncated = True
            original_len = len(result)
            cutoff = self.max_chars
            result = result[:cutoff] + f"\n[TRUNCATED: original payload {original_len} chars truncated to {cutoff} chars]"

        with self._lock:
            self._metrics["total_sanitized"] += 1
            self._metrics["redacted_secrets_count"] += redactions
            self._metrics["stripped_ansi_count"] += ansi_count
            self._metrics["null_bytes_cleaned_count"] += null_count
            if truncated:
                self._metrics["truncated_payloads_count"] += 1

        return result

    def sanitize(
        self,
        data: Any,
        seen: Optional[Set[int]] = None,
    ) -> Any:
        """Recursively sanitize nested data structure while guarding against cyclic references."""
        if seen is None:
            seen = set()

        if isinstance(data, str):
            return self.sanitize_text(data)

        if isinstance(data, (bytes, bytearray)):
            decoded = data.decode("utf-8", errors="replace")
            return self.sanitize_text(decoded)

        if isinstance(data, (int, float, bool)) or data is None:
            return data

        ptr = id(data)
        if ptr in seen:
            return "[CYCLIC_REFERENCE]"
        seen.add(ptr)

        try:
            if isinstance(data, dict):
                clean_dict: Dict[str, Any] = {}
                for k, v in data.items():
                    clean_k = self.sanitize_text(str(k))
                    clean_dict[clean_k] = self.sanitize(v, seen=seen)
                return clean_dict

            if isinstance(data, (list, tuple)):
                clean_list: List[Any] = [self.sanitize(item, seen=seen) for item in data]
                return clean_list

            if isinstance(data, set):
                clean_set_list: List[Any] = [self.sanitize(item, seen=seen) for item in sorted(list(data), key=str)]
                return clean_set_list

            if isinstance(data, BaseException):
                return self.sanitize_text(f"{type(data).__name__}: {data}")

            return self.sanitize_text(str(data))
        finally:
            seen.remove(ptr)

    def sanitize_mcp_result(self, raw_result: Any) -> Dict[str, Any]:
        """Sanitize and format output into standard MCP content block envelope."""
        if isinstance(raw_result, dict) and "content" in raw_result and isinstance(raw_result["content"], list):
            sanitized_content = []
            for item in raw_result["content"]:
                if isinstance(item, dict):
                    block = dict(item)
                    if "text" in block and isinstance(block["text"], str):
                        block["text"] = self.sanitize_text(block["text"])
                    sanitized_content.append(block)
                else:
                    sanitized_content.append(self.sanitize(item))
            return {
                "content": sanitized_content,
                "isError": bool(raw_result.get("isError", False)),
            }

        if isinstance(raw_result, str):
            return {
                "content": [{"type": "text", "text": self.sanitize_text(raw_result)}],
                "isError": False,
            }

        sanitized_payload = self.sanitize(raw_result)
        formatted_text = json.dumps(sanitized_payload, ensure_ascii=False)
        return {
            "content": [{"type": "text", "text": formatted_text}],
            "isError": False,
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Return snapshot copy of result sanitizer telemetry counters."""
        with self._lock:
            return dict(self._metrics)

    def reset_metrics(self) -> None:
        """Reset result sanitizer telemetry counters to initial zero values."""
        with self._lock:
            for k in self._metrics:
                self._metrics[k] = 0


_DEFAULT_RESULT_SANITIZER = McpResultSanitizer()


def get_default_result_sanitizer() -> McpResultSanitizer:
    """Return default singleton MCP result sanitizer."""
    return _DEFAULT_RESULT_SANITIZER


def reset_result_sanitizer() -> None:
    """Reset global result sanitizer singleton state and metrics."""
    global _DEFAULT_RESULT_SANITIZER
    _DEFAULT_RESULT_SANITIZER = McpResultSanitizer()


def sanitize_mcp_result(
    raw_result: Any,
    max_chars: int = 65536,
    redact_secrets: bool = True,
) -> Any:
    """Sanitize arbitrary MCP tool output via default result sanitizer."""
    sanitizer = get_default_result_sanitizer()
    if max_chars != sanitizer.max_chars:
        custom_sanitizer = McpResultSanitizer(max_chars=max_chars, redact_secrets=redact_secrets)
        return custom_sanitizer.sanitize(raw_result)
    return sanitizer.sanitize(raw_result)


class McpHeartbeatMonitor:
    """Background monitor emitting periodic ping keepalives to an MCP client."""

    def __init__(
        self,
        client: Any,
        interval: float = 30.0,
        timeout: float = 5.0,
        max_consecutive_failures: int = 3,
        on_failure: Optional[Any] = None,
    ) -> None:
        self.client = client
        self.interval = float(interval)
        self.timeout = float(timeout)
        self.max_consecutive_failures = int(max_consecutive_failures)
        self.on_failure = on_failure

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self.last_ping_time: Optional[float] = None
        self.last_ping_latency_ms: Optional[float] = None
        self.consecutive_failures: int = 0
        self.total_pings: int = 0
        self.total_failures: int = 0

    @property
    def is_running(self) -> bool:
        """Return boolean status indicating if monitor loop is active."""
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def is_healthy(self) -> bool:
        """Return boolean status indicating if client passed recent heartbeats."""
        return self.consecutive_failures < self.max_consecutive_failures

    def start(self) -> None:
        """Start background heartbeat thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background heartbeat thread and join."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None

    def execute_ping(self) -> bool:
        """Execute single synchronous ping check and update health metrics."""
        self.total_pings += 1
        start_time = time.monotonic()
        try:
            if hasattr(self.client, "ping"):
                success = bool(self.client.ping(timeout=self.timeout))
            else:
                success = False
        except Exception:
            success = False

        latency = (time.monotonic() - start_time) * 1000.0
        self.last_ping_time = time.time()
        self.last_ping_latency_ms = latency

        if success:
            self.consecutive_failures = 0
            return True
        else:
            self.consecutive_failures += 1
            self.total_failures += 1
            if self.consecutive_failures >= self.max_consecutive_failures and self.on_failure:
                try:
                    self.on_failure(self.client)
                except Exception:
                    pass
            return False

    def _run_loop(self) -> None:
        """Internal background loop executing periodic pings."""
        while self._running and not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.interval):
                break
            if not self._running:
                break
            is_client_active = getattr(self.client, "is_running", True)
            if is_client_active:
                self.execute_ping()

    def get_status(self) -> Dict[str, Any]:
        """Return current health and telemetry status dictionary."""
        return {
            "is_running": self.is_running,
            "is_healthy": self.is_healthy,
            "interval": self.interval,
            "timeout": self.timeout,
            "consecutive_failures": self.consecutive_failures,
            "total_pings": self.total_pings,
            "total_failures": self.total_failures,
            "last_ping_time": self.last_ping_time,
            "last_ping_latency_ms": self.last_ping_latency_ms,
        }


def compute_server_fingerprint(
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> str:
    """Compute deterministic SHA-256 fingerprint from server execution specification."""
    parts = [
        str(command).strip(),
        json.dumps(list(args or []), sort_keys=True),
        json.dumps(dict(env or {}), sort_keys=True),
        str(cwd or "").strip(),
    ]
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class McpManifestCache:
    """
    Thread-safe manifest cache for Model Context Protocol tools and server capabilities.
    Maintains cached tool schemas with fingerprint-based invalidation and TTL expiration.
    Supports atomic persistence to disk and memory caching.
    """

    def __init__(
        self,
        cache_file: Optional[str] = None,
        default_ttl: float = 3600.0,
        enabled: bool = True,
    ) -> None:
        self.cache_file = cache_file
        self.default_ttl = float(default_ttl)
        self.enabled = bool(enabled)
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0
        self._writes: int = 0
        self._evictions: int = 0

        if self.cache_file and os.path.isfile(self.cache_file):
            self.load()

    def get(
        self,
        server_name: str,
        fingerprint: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve valid cached manifest entry; return None when expired or mismatched."""
        if not self.enabled:
            return None

        clean_name = server_name.strip().lower()
        now = time.time()
        with self._lock:
            entry = self._entries.get(clean_name)
            if not entry:
                self._misses += 1
                return None

            expires_at = entry.get("expires_at", 0)
            if expires_at and now > expires_at:
                del self._entries[clean_name]
                self._evictions += 1
                self._misses += 1
                return None

            if fingerprint is not None and entry.get("fingerprint") != fingerprint:
                del self._entries[clean_name]
                self._evictions += 1
                self._misses += 1
                return None

            self._hits += 1
            return {
                "tools": list(entry.get("tools") or []),
                "capabilities": dict(entry.get("capabilities") or {}),
                "server_info": dict(entry.get("server_info") or {}),
                "fingerprint": entry.get("fingerprint"),
                "timestamp": entry.get("timestamp"),
                "expires_at": entry.get("expires_at"),
            }

    def put(
        self,
        server_name: str,
        tools: List[Dict[str, Any]],
        fingerprint: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        server_info: Optional[Dict[str, Any]] = None,
        ttl: Optional[float] = None,
    ) -> None:
        """Store server manifest entry with fingerprint and expiration window."""
        if not self.enabled:
            return

        clean_name = server_name.strip().lower()
        now = time.time()
        lifetime = float(ttl) if ttl is not None and ttl > 0 else self.default_ttl
        expires_at = now + lifetime if lifetime > 0 else 0

        entry = {
            "tools": [dict(t) for t in tools],
            "capabilities": dict(capabilities or {}),
            "server_info": dict(server_info or {}),
            "fingerprint": fingerprint,
            "timestamp": now,
            "expires_at": expires_at,
        }

        with self._lock:
            self._entries[clean_name] = entry
            self._writes += 1

        if self.cache_file:
            self.save()

    def invalidate(self, server_name: str) -> bool:
        """Invalidate and remove cached manifest entry for named server."""
        clean_name = server_name.strip().lower()
        with self._lock:
            if clean_name in self._entries:
                del self._entries[clean_name]
                self._evictions += 1
                if self.cache_file:
                    self.save()
                return True
            return False

    def clear(self) -> None:
        """Clear all cached manifest entries and reset telemetry."""
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0
            self._writes = 0
            self._evictions = 0
            if self.cache_file and os.path.isfile(self.cache_file):
                try:
                    os.remove(self.cache_file)
                except Exception:
                    pass

    def load(self) -> bool:
        """Load cached manifest entries from persistent JSON cache file."""
        if not self.cache_file or not os.path.isfile(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                with self._lock:
                    self._entries = data.get("entries", {})
                return True
        except Exception:
            return False
        return False

    def save(self) -> bool:
        """Atomically persist cached manifest entries to JSON cache file."""
        if not self.cache_file:
            return False
        payload = {
            "version": "1.0",
            "entries": self._entries,
            "saved_at": time.time(),
        }
        target_dir = os.path.dirname(self.cache_file)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        tmp_file = f"{self.cache_file}.tmp.{os.getpid()}"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_file, self.cache_file)
            return True
        except Exception:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
            return False

    def get_metrics(self) -> Dict[str, Any]:
        """Return manifest cache performance and capacity metrics."""
        with self._lock:
            total_reqs = self._hits + self._misses
            hit_ratio = (self._hits / total_reqs) if total_reqs > 0 else 0.0
            return {
                "enabled": self.enabled,
                "cached_servers_count": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
                "writes": self._writes,
                "evictions": self._evictions,
                "hit_ratio": round(hit_ratio, 4),
                "cached_servers": sorted(self._entries.keys()),
            }


_DEFAULT_MANIFEST_CACHE = McpManifestCache()


def get_default_manifest_cache() -> McpManifestCache:
    """Return default singleton MCP manifest cache."""
    return _DEFAULT_MANIFEST_CACHE


def reset_manifest_cache() -> None:
    """Reset global MCP manifest cache state."""
    _DEFAULT_MANIFEST_CACHE.clear()


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
        namespace: Optional[str] = None,
        lazy: bool = False,
        predeclared_tools: Optional[List[Dict[str, Any]]] = None,
        manifest_cache: Optional[McpManifestCache] = None,
    ):
        self.command = command
        self.args: List[str] = list(args) if args else []
        self.env = env
        self.cwd = cwd
        self.env_passthrough: List[str] = list(env_passthrough or [])
        self.timeout = float(timeout) if timeout else _env_timeout("HYDRA_MCP_TIMEOUT", DEFAULT_RPC_TIMEOUT)
        self.namespace: Optional[str] = namespace.strip().lower() if namespace else None
        self.init_timeout = (
            float(init_timeout) if init_timeout
            else max(self.timeout, _env_timeout("HYDRA_MCP_INIT_TIMEOUT", DEFAULT_INIT_TIMEOUT))
        )
        self.lazy = bool(lazy)
        self.predeclared_tools: Optional[List[Dict[str, Any]]] = (
            [dict(t) for t in predeclared_tools] if predeclared_tools is not None else None
        )
        self.manifest_cache = manifest_cache
        self.spawn_count: int = 0
        self.last_spawn_time: Optional[float] = None

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
        self._heartbeat_monitor: Optional[McpHeartbeatMonitor] = None
        self._timeout_guard: McpTimeoutGuard = McpTimeoutGuard(default_timeout=self.timeout)

    @property
    def is_spawned(self) -> bool:
        """Return boolean status indicating whether subprocess spawned."""
        return self._process is not None

    @property
    def is_running(self) -> bool:
        """True when the subprocess currently active."""
        return self._process is not None and self._process.poll() is None

    def ensure_started(self) -> None:
        """Ensure subprocess running; spawn and perform handshake if not already started."""
        if not self.is_running:
            self.start()

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
            if self._process is not None and self._process.poll() is None:
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

            self.spawn_count += 1
            self.last_spawn_time = time.time()

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

    def _emit_cancellation(self, request_id: int, reason: str = "timeout") -> None:
        """Send notifications/cancelled to notify server of aborted request."""
        try:
            params = {
                "requestId": request_id,
                "reason": reason,
            }
            self._send_notification("notifications/cancelled", params)
            if hasattr(self, "_timeout_guard") and self._timeout_guard:
                self._timeout_guard.record_cancellation()
        except Exception:
            pass

    def _send_rpc(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        """
        Send a JSON-RPC 2.0 request and wait for the matching response, at most `timeout` seconds.
        Thread-safe under self._lock.
        """
        if self.lazy and not self.is_running:
            self.ensure_started()
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
                    self._emit_cancellation(req_id, reason=f"timeout after {wait:g}s")
                    if hasattr(self, "_timeout_guard") and self._timeout_guard:
                        self._timeout_guard.record_timeout(method)
                    raise TimeoutError(f"MCP server did not answer '{method}' within {wait:g}s")
                try:
                    line = self._stdout_queue.get(timeout=remaining)
                except queue.Empty:
                    self._emit_cancellation(req_id, reason=f"timeout after {wait:g}s")
                    if hasattr(self, "_timeout_guard") and self._timeout_guard:
                        self._timeout_guard.record_timeout(method)
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
        if self.lazy and self.predeclared_tools is not None and not self.is_running:
            return [dict(t) for t in self.predeclared_tools]

        fp = compute_server_fingerprint(self.command, self.args, self.env, self.cwd)
        name_key = self.namespace or self.command

        if self.manifest_cache:
            cached = self.manifest_cache.get(name_key, fingerprint=fp)
            if cached and cached.get("tools"):
                if self.lazy and not self.is_running:
                    return cached["tools"]

        if self.lazy and not self.is_running:
            self.ensure_started()

        result = self._send_rpc("tools/list", {})
        tools: List[Dict[str, Any]] = []
        if isinstance(result, dict):
            tools = result.get("tools", [])
        elif isinstance(result, list):
            tools = result

        if self.manifest_cache and tools:
            self.manifest_cache.put(
                name_key,
                tools=tools,
                fingerprint=fp,
                capabilities=self.server_capabilities,
                server_info=self.server_info,
            )

        return tools

    def qualify_tool_name(self, tool_name: str, separator: str = DEFAULT_NAMESPACE_SEPARATOR) -> str:
        """Qualify tool name with client namespace if configured."""
        if not self.namespace:
            return tool_name
        return format_qualified_tool_name(self.namespace, tool_name, separator=separator)

    def unqualify_tool_name(self, qualified_name: str, separator: str = DEFAULT_NAMESPACE_SEPARATOR) -> str:
        """Strip client namespace from qualified tool name if matching."""
        if not self.namespace or separator not in qualified_name:
            return qualified_name
        try:
            ns, tool = parse_qualified_tool_name(qualified_name, separator=separator)
            if ns == self.namespace:
                return tool
        except ValueError:
            pass
        return qualified_name

    def list_namespaced_tools(self, separator: str = DEFAULT_NAMESPACE_SEPARATOR) -> List[Dict[str, Any]]:
        """List tools with qualified names using client namespace."""
        tools = self.list_tools()
        if not self.namespace:
            return tools
        namespaced: List[Dict[str, Any]] = []
        for t in tools:
            copy_t = dict(t)
            orig = copy_t.get("name", "")
            copy_t["name"] = format_qualified_tool_name(self.namespace, orig, separator=separator)
            copy_t["_namespace"] = self.namespace
            copy_t["_original_name"] = orig
            namespaced.append(copy_t)
        return namespaced

    def ping(self, timeout: Optional[float] = None) -> bool:
        """Send MCP JSON-RPC ping request and verify server responds."""
        if self.lazy and not self.is_running:
            try:
                self.ensure_started()
            except Exception:
                return False
        if not self.is_running:
            return False
        try:
            self._send_rpc("ping", {}, timeout=timeout or 5.0)
            return True
        except RuntimeError as e:
            if "MCP RPC Error" in str(e):
                return True
            return False
        except Exception:
            return False

    def start_heartbeat(
        self,
        interval: float = 30.0,
        timeout: float = 5.0,
        max_consecutive_failures: int = 3,
        on_failure: Optional[Any] = None,
    ) -> McpHeartbeatMonitor:
        """Start background heartbeat keepalive monitor for server connection."""
        if self._heartbeat_monitor and self._heartbeat_monitor.is_running:
            return self._heartbeat_monitor
        self._heartbeat_monitor = McpHeartbeatMonitor(
            client=self,
            interval=interval,
            timeout=timeout,
            max_consecutive_failures=max_consecutive_failures,
            on_failure=on_failure,
        )
        self._heartbeat_monitor.start()
        return self._heartbeat_monitor

    def stop_heartbeat(self) -> None:
        """Stop active background heartbeat monitor."""
        if self._heartbeat_monitor:
            self._heartbeat_monitor.stop()
            self._heartbeat_monitor = None

    def heartbeat_status(self) -> Optional[Dict[str, Any]]:
        """Return status telemetry of active heartbeat monitor."""
        if self._heartbeat_monitor:
            return self._heartbeat_monitor.get_status()
        return None

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Call a specific tool on the MCP server with the provided arguments and timeout guard."""
        if self.lazy and not self.is_running:
            self.ensure_started()
        effective_timeout = self.get_tool_timeout(tool_name, explicit_timeout=timeout)
        params = {
            "name": tool_name,
            "arguments": arguments if arguments is not None else {},
        }
        return self._send_rpc("tools/call", params, timeout=effective_timeout)

    def set_tool_timeout(self, pattern: str, timeout_seconds: float) -> None:
        """Register pattern-based timeout rule on client timeout guard."""
        self._timeout_guard.register_rule(pattern, timeout_seconds)

    def get_tool_timeout(self, tool_name: str, explicit_timeout: Optional[float] = None) -> float:
        """Resolve effective timeout for tool name."""
        return self._timeout_guard.resolve_timeout(tool_name, explicit_timeout=explicit_timeout)

    def close(self) -> None:
        """Terminate subprocess and cleanly join stderr background thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._heartbeat_monitor:
                try:
                    self._heartbeat_monitor.stop()
                except Exception:
                    pass
                self._heartbeat_monitor = None
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


class McpLazyClient:
    """
    Lazy proxy client managing deferred spawning of an underlying MCP client.
    Supports either an existing client factory callable or direct command invocation.
    Defers process startup until first operational tool dispatch or discovery request.
    """

    def __init__(
        self,
        factory_or_command: Any,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        env_passthrough: Optional[Iterable[str]] = None,
        timeout: Optional[float] = None,
        init_timeout: Optional[float] = None,
        namespace: Optional[str] = None,
        predeclared_tools: Optional[List[Dict[str, Any]]] = None,
        auto_start: bool = True,
        manifest_cache: Optional[McpManifestCache] = None,
    ) -> None:
        self.factory_or_command = factory_or_command
        self.args: List[str] = list(args) if args else []
        self.env = env
        self.cwd = cwd
        self.env_passthrough: List[str] = list(env_passthrough or [])
        self.timeout = float(timeout) if timeout else _env_timeout("HYDRA_MCP_TIMEOUT", DEFAULT_RPC_TIMEOUT)
        self.init_timeout = float(init_timeout) if init_timeout else DEFAULT_INIT_TIMEOUT
        self.namespace: Optional[str] = namespace.strip().lower() if namespace else None
        self.predeclared_tools: Optional[List[Dict[str, Any]]] = (
            [dict(t) for t in predeclared_tools] if predeclared_tools else None
        )
        self.auto_start = bool(auto_start)
        self.manifest_cache = manifest_cache

        self._client: Optional[Any] = None
        self._lock = threading.Lock()
        self.spawn_count: int = 0
        self.last_spawn_time: Optional[float] = None
        self.total_calls: int = 0

    @property
    def is_spawned(self) -> bool:
        """Return boolean status indicating whether target client spawned."""
        return self._client is not None

    @property
    def is_running(self) -> bool:
        """Return boolean status indicating whether underlying process active."""
        if self._client is None:
            return False
        return getattr(self._client, "is_running", False)

    @property
    def client(self) -> Optional[Any]:
        """Return underlying client instance when spawned."""
        return self._client

    def ensure_started(self) -> Any:
        """Ensure underlying MCP client instantiated and started thread-safely."""
        with self._lock:
            if self._client is not None and getattr(self._client, "is_running", True):
                return self._client

            if callable(self.factory_or_command):
                client = self.factory_or_command()
                if hasattr(client, "is_running") and not client.is_running and hasattr(client, "start"):
                    client.start()
                elif hasattr(client, "start") and not hasattr(client, "is_running"):
                    client.start()
            else:
                client = McpSubprocessClient(
                    command=str(self.factory_or_command),
                    args=self.args,
                    env=self.env,
                    cwd=self.cwd,
                    env_passthrough=self.env_passthrough,
                    timeout=self.timeout,
                    init_timeout=self.init_timeout,
                    namespace=self.namespace,
                    lazy=False,
                )
                client.start()

            self._client = client
            self.spawn_count += 1
            self.last_spawn_time = time.time()
            return self._client

    def list_tools(self) -> List[Dict[str, Any]]:
        """List exposed tools; return predeclared tools without spawning when available."""
        if not self.is_spawned and self.predeclared_tools is not None:
            return [dict(t) for t in self.predeclared_tools]
        if not self.is_spawned and self.manifest_cache:
            name_key = self.namespace or str(self.factory_or_command)
            fp = compute_server_fingerprint(str(self.factory_or_command), self.args, self.env, self.cwd) if not callable(self.factory_or_command) else None
            cached = self.manifest_cache.get(name_key, fingerprint=fp)
            if cached and cached.get("tools"):
                return cached["tools"]
        client = self.ensure_started()
        if hasattr(client, "list_tools"):
            return client.list_tools()
        return []

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Dispatch tool execution to underlying spawned client."""
        self.total_calls += 1
        client = self.ensure_started()
        if hasattr(client, "call_tool"):
            try:
                return client.call_tool(tool_name, arguments or {}, timeout=timeout)
            except TypeError:
                return client.call_tool(tool_name, arguments or {})
        raise RuntimeError("Underlying client does not implement call_tool.")

    def ping(self, timeout: Optional[float] = None) -> bool:
        """Ping underlying client; spawn when auto_start configured."""
        if not self.is_spawned and not self.auto_start:
            return False
        client = self.ensure_started()
        if hasattr(client, "ping"):
            return bool(client.ping(timeout=timeout))
        return True

    def qualify_tool_name(self, tool_name: str, separator: str = DEFAULT_NAMESPACE_SEPARATOR) -> str:
        """Qualify tool name with namespace."""
        if not self.namespace:
            return tool_name
        return format_qualified_tool_name(self.namespace, tool_name, separator=separator)

    def unqualify_tool_name(self, qualified_name: str, separator: str = DEFAULT_NAMESPACE_SEPARATOR) -> str:
        """Strip namespace from qualified tool name."""
        if not self.namespace or separator not in qualified_name:
            return qualified_name
        try:
            ns, tool = parse_qualified_tool_name(qualified_name, separator=separator)
            if ns == self.namespace:
                return tool
        except ValueError:
            pass
        return qualified_name

    def set_tool_timeout(self, pattern: str, timeout_seconds: float) -> None:
        """Configure tool timeout pattern on client or delegate when spawned."""
        if self._client and hasattr(self._client, "set_tool_timeout"):
            self._client.set_tool_timeout(pattern, timeout_seconds)

    def get_tool_timeout(self, tool_name: str, explicit_timeout: Optional[float] = None) -> float:
        """Resolve effective tool timeout."""
        if self._client and hasattr(self._client, "get_tool_timeout"):
            return self._client.get_tool_timeout(tool_name, explicit_timeout=explicit_timeout)
        if explicit_timeout is not None and explicit_timeout > 0:
            return float(explicit_timeout)
        return self.timeout

    def close(self) -> None:
        """Close and shutdown underlying client process."""
        with self._lock:
            if self._client is not None:
                try:
                    if hasattr(self._client, "close"):
                        self._client.close()
                except Exception:
                    pass
                self._client = None

    def get_metrics(self) -> Dict[str, Any]:
        """Return lazy spawning telemetry and lifecycle metrics."""
        return {
            "is_spawned": self.is_spawned,
            "is_running": self.is_running,
            "spawn_count": self.spawn_count,
            "last_spawn_time": self.last_spawn_time,
            "total_calls": self.total_calls,
            "has_predeclared_tools": self.predeclared_tools is not None,
            "predeclared_tools_count": len(self.predeclared_tools) if self.predeclared_tools else 0,
        }

    def __enter__(self) -> "McpLazyClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class McpRegistryDiscoverer:
    """
    Multi-source discovery engine for Model Context Protocol server configurations.
    Discovers, parses, normalizes, and merges server declarations across workspace,
    user home, desktop clients, and environment variables with strict precedence.
    """

    def __init__(
        self,
        search_paths: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        home: Optional[str] = None,
    ) -> None:
        self.search_paths: List[str] = list(search_paths) if search_paths else []
        self.cwd: str = cwd or os.getcwd()
        self.home: str = home or os.path.expanduser("~")
        self.reset()

    def reset(self) -> None:
        """Reset discovered servers, scanned files, and discovery metrics."""
        self._discovered_servers: Dict[str, Dict[str, Any]] = {}
        self._scanned_files: List[str] = []
        self._errors: Dict[str, str] = {}
        self._discovery_timestamp: Optional[float] = None

    def find_candidate_locations(self) -> List[str]:
        """Collect potential MCP configuration file paths in precedence order."""
        candidates: List[str] = []

        # User home locations
        candidates.append(os.path.join(self.home, ".hydra", "mcp_servers.json"))
        candidates.append(os.path.join(self.home, ".config", "hydra", "mcp_servers.json"))

        # Desktop client paths
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA")
            if appdata:
                candidates.append(os.path.join(appdata, "Claude", "claude_desktop_config.json"))
                candidates.append(os.path.join(appdata, "antigravity", "mcp_config.json"))
        else:
            candidates.append(os.path.join(self.home, ".config", "Claude", "claude_desktop_config.json"))

        # Upward project hierarchy from cwd
        curr = os.path.abspath(self.cwd)
        visited = set()
        while curr and curr not in visited:
            visited.add(curr)
            candidates.append(os.path.join(curr, ".hydra", "mcp_servers.json"))
            candidates.append(os.path.join(curr, "mcp_servers.json"))
            candidates.append(os.path.join(curr, "mcp.json"))
            parent = os.path.dirname(curr)
            if parent == curr:
                break
            curr = parent

        # Explicit search paths
        for path in self.search_paths:
            candidates.append(os.path.abspath(path))

        # Environment variable override file
        env_file = os.environ.get("HYDRA_MCP_CONFIG")
        if env_file:
            candidates.append(os.path.abspath(env_file))

        # Deduplicate while preserving precedence ordering
        seen = set()
        unique_candidates = []
        for p in candidates:
            norm = os.path.normpath(p)
            if norm not in seen:
                seen.add(norm)
                unique_candidates.append(norm)

        return unique_candidates

    def normalize_server_entry(self, name: str, raw_config: Any) -> Optional[Dict[str, Any]]:
        """Normalize raw server dictionary into canonical server configuration format."""
        if not isinstance(raw_config, dict):
            return None

        if raw_config.get("disabled") is True or raw_config.get("enabled") is False:
            return None

        cmd = raw_config.get("command")
        if not cmd or not str(cmd).strip():
            return None

        clean_name = str(name).strip().lower()
        if not clean_name:
            return None

        normalized: Dict[str, Any] = {
            "name": clean_name,
            "command": str(cmd).strip(),
            "args": list(raw_config.get("args") or []),
            "env": dict(raw_config.get("env") or {}),
            "cwd": raw_config.get("cwd"),
            "timeout": float(raw_config.get("timeout", DEFAULT_RPC_TIMEOUT)),
        }
        if "env_passthrough" in raw_config:
            normalized["env_passthrough"] = list(raw_config["env_passthrough"])
        if "predeclared_tools" in raw_config:
            normalized["predeclared_tools"] = list(raw_config["predeclared_tools"])
        if "aliases" in raw_config:
            normalized["aliases"] = list(raw_config["aliases"])

        return normalized

    def parse_file(self, file_path: str) -> Dict[str, Dict[str, Any]]:
        """Parse configuration file and extract normalized server configurations."""
        if not os.path.isfile(file_path):
            return {}

        self._scanned_files.append(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._errors[file_path] = str(e)
            return {}

        if not isinstance(data, dict):
            return {}

        raw_servers = data.get("mcpServers") or data.get("servers") or data
        if not isinstance(raw_servers, dict):
            return {}

        discovered: Dict[str, Dict[str, Any]] = {}
        for s_name, s_val in raw_servers.items():
            norm = self.normalize_server_entry(s_name, s_val)
            if norm:
                discovered[norm["name"]] = norm

        return discovered

    def discover_all(self) -> Dict[str, Dict[str, Any]]:
        """
        Execute full discovery across candidate locations and environment variables.
        Applies hierarchical merging so higher precedence entries overwrite lower.
        """
        self._discovery_timestamp = time.time()
        merged: Dict[str, Dict[str, Any]] = {}

        candidate_files = self.find_candidate_locations()
        for filepath in candidate_files:
            file_servers = self.parse_file(filepath)
            for s_name, s_config in file_servers.items():
                s_copy = dict(s_config)
                s_copy["_source"] = filepath
                merged[s_name] = s_copy

        env_inline = os.environ.get("HYDRA_MCP_SERVERS")
        if env_inline:
            try:
                raw_env_data = json.loads(env_inline)
                if isinstance(raw_env_data, dict):
                    env_servers = raw_env_data.get("mcpServers") or raw_env_data.get("servers") or raw_env_data
                    if isinstance(env_servers, dict):
                        for s_name, s_val in env_servers.items():
                            norm = self.normalize_server_entry(s_name, s_val)
                            if norm:
                                norm["_source"] = "HYDRA_MCP_SERVERS"
                                merged[norm["name"]] = norm
            except Exception as e:
                self._errors["HYDRA_MCP_SERVERS"] = str(e)

        self._discovered_servers = merged
        return dict(self._discovered_servers)

    def load_into_router(
        self,
        router: McpNamespaceRouter,
        lazy: bool = True,
    ) -> int:
        """Register all discovered servers into an McpNamespaceRouter."""
        if not self._discovered_servers:
            self.discover_all()

        loaded_count = 0
        for s_name, s_conf in self._discovered_servers.items():
            aliases = s_conf.get("aliases")
            predeclared = s_conf.get("predeclared_tools")
            cmd = s_conf["command"]
            args = s_conf.get("args")
            env = s_conf.get("env")
            cwd = s_conf.get("cwd")
            timeout = s_conf.get("timeout")

            if lazy:
                router.register_lazy_client(
                    namespace=s_name,
                    factory_or_command=cmd,
                    args=args,
                    aliases=aliases,
                    predeclared_tools=predeclared,
                    env=env,
                    cwd=cwd,
                    timeout=timeout,
                )
            else:
                client = McpSubprocessClient(
                    command=cmd,
                    args=args,
                    env=env,
                    cwd=cwd,
                    timeout=timeout,
                    namespace=s_name,
                    lazy=False,
                )
                router.register_client(s_name, client, aliases=aliases)

            loaded_count += 1
        return loaded_count

    def get_metrics(self) -> Dict[str, Any]:
        """Return discovery telemetry counters and diagnostic state."""
        return {
            "discovered_servers_count": len(self._discovered_servers),
            "scanned_files_count": len(self._scanned_files),
            "errors_count": len(self._errors),
            "scanned_files": list(self._scanned_files),
            "errors": dict(self._errors),
            "last_discovery_time": self._discovery_timestamp,
        }


_DEFAULT_DISCOVERER = McpRegistryDiscoverer()


def get_default_discoverer() -> McpRegistryDiscoverer:
    """Return default singleton MCP registry discoverer."""
    return _DEFAULT_DISCOVERER


def reset_discoverer() -> None:
    """Reset global MCP registry discoverer state."""
    _DEFAULT_DISCOVERER.reset()


def discover_mcp_configs(
    search_paths: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    home: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Discover and return normalized MCP server configurations."""
    discoverer = McpRegistryDiscoverer(search_paths=search_paths, cwd=cwd, home=home)
    return discoverer.discover_all()
