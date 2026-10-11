"""
Zero-dependency Model Context Protocol (MCP) subprocess client for Hydra CLI.
Manages JSON-RPC 2.0 stdio subprocess connection with thread-safe RPC lock
and background daemon thread for stderr to prevent pipe deadlocks.
"""

import enum
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


class McpRateLimitExceededError(RuntimeError):
    """
    Raised when an MCP tool invocation exceeds configured rate limits.
    Asserts compliance : not possible in error details.
    """
    pass


@dataclass
class _TokenBucket:
    """
    Internal token bucket tracking available invocation tokens.
    """
    fill_rate: float
    burst: float
    tokens: float
    last_updated: float


class _RateLimitContext:
    """Context manager acquiring rate limit tokens on entry."""

    def __init__(
        self,
        limiter: "McpRateLimiter",
        key: str = "default",
        cost: float = 1.0,
        blocking: bool = True,
        timeout: Optional[float] = None,
        raise_on_limit: Optional[bool] = None,
    ) -> None:
        self.limiter = limiter
        self.key = key
        self.cost = cost
        self.blocking = blocking
        self.timeout = timeout
        self.raise_on_limit = raise_on_limit

    def __enter__(self) -> "McpRateLimiter":
        self.limiter.acquire(
            key=self.key,
            cost=self.cost,
            blocking=self.blocking,
            timeout=self.timeout,
            raise_on_limit=self.raise_on_limit,
        )
        return self.limiter

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Exit rate limit context manager without suppressing exceptions."""
        return False


class McpRateLimiter:
    """
    Token-bucket rate limiter managing invocation frequency for MCP tools.
    Supports global, per-namespace, and per-tool limits with pattern matching.
    """

    def __init__(
        self,
        default_rate: float = 60.0,
        per_seconds: float = 60.0,
        default_burst: Optional[int] = None,
        raise_on_limit: bool = False,
    ) -> None:
        self.default_rate = float(default_rate)
        self.per_seconds = float(per_seconds) if per_seconds > 0 else 1.0
        self.default_burst = int(default_burst) if default_burst is not None else max(1, int(self.default_rate))
        self.raise_on_limit = bool(raise_on_limit)
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset rate limiter rules, active buckets, and telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._rules: List[Tuple[str, float, float, int, bool]] = []
            self._buckets: Dict[str, _TokenBucket] = {}
            self._total_requests: int = 0
            self._allowed_requests: int = 0
            self._throttled_requests: int = 0
            self._rejected_requests: int = 0
            self._wait_time_total_seconds: float = 0.0

    def register_rule(
        self,
        pattern: str,
        rate: float,
        per_seconds: float = 1.0,
        burst: Optional[int] = None,
        shared: bool = False,
    ) -> None:
        """Register pattern-based rate limit rule."""
        if rate <= 0:
            raise ValueError("Rate must be strictly positive.")
        clean_pat = pattern.strip().lower()
        clean_per = float(per_seconds) if per_seconds > 0 else 1.0
        clean_burst = int(burst) if burst is not None else max(1, int(rate))
        with self._lock:
            self._rules.append((clean_pat, float(rate), clean_per, clean_burst, bool(shared)))

    def _resolve_config(self, key: str) -> Tuple[float, int, str]:
        """Resolve fill rate, burst capacity, and bucket key for target string."""
        target = key.strip().lower()
        for pat, r_val, per_val, b_val, is_shared in reversed(self._rules):
            if fnmatch.fnmatch(target, pat):
                fill_rate = r_val / per_val
                bucket_key = f"rule:{pat}" if is_shared else f"key:{target}"
                return fill_rate, b_val, bucket_key

        fill_rate = self.default_rate / self.per_seconds
        return fill_rate, self.default_burst, f"key:{target}"

    def _get_bucket(self, bucket_key: str, fill_rate: float, burst: int) -> _TokenBucket:
        """Retrieve existing token bucket or instantiate new bucket."""
        now = time.time()
        bucket = self._buckets.get(bucket_key)
        if bucket is None:
            bucket = _TokenBucket(
                fill_rate=fill_rate,
                burst=float(burst),
                tokens=float(burst),
                last_updated=now,
            )
            self._buckets[bucket_key] = bucket
        else:
            elapsed = max(0.0, now - bucket.last_updated)
            bucket.tokens = min(bucket.burst, bucket.tokens + elapsed * bucket.fill_rate)
            bucket.last_updated = now
            bucket.fill_rate = fill_rate
            bucket.burst = float(burst)
        return bucket

    def get_wait_time(self, key: str = "default", cost: float = 1.0) -> float:
        """Calculate wait time in seconds required before cost tokens become available."""
        with self._lock:
            fill_rate, burst, bucket_key = self._resolve_config(key)
            bucket = self._get_bucket(bucket_key, fill_rate, burst)
            if bucket.tokens >= cost:
                return 0.0
            deficit = cost - bucket.tokens
            return max(0.0, deficit / bucket.fill_rate)

    def can_acquire(self, key: str = "default", cost: float = 1.0) -> bool:
        """Return true when cost tokens immediately available without blocking."""
        with self._lock:
            return self.get_wait_time(key=key, cost=cost) <= 0.0

    def get_available_tokens(self, key: str = "default") -> float:
        """Return count of currently available tokens for designated key."""
        with self._lock:
            fill_rate, burst, bucket_key = self._resolve_config(key)
            bucket = self._get_bucket(bucket_key, fill_rate, burst)
            return max(0.0, bucket.tokens)

    def acquire(
        self,
        key: str = "default",
        cost: float = 1.0,
        blocking: bool = True,
        timeout: Optional[float] = None,
        raise_on_limit: Optional[bool] = None,
    ) -> bool:
        """
        Acquire token bucket capacity for invocation.
        Waits when blocking requested and capacity depleted.
        """
        should_raise = self.raise_on_limit if raise_on_limit is None else bool(raise_on_limit)

        with self._lock:
            self._total_requests += 1
            fill_rate, burst, bucket_key = self._resolve_config(key)
            bucket = self._get_bucket(bucket_key, fill_rate, burst)

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                self._allowed_requests += 1
                return True

            deficit = cost - bucket.tokens
            wait_time = max(0.0, deficit / bucket.fill_rate)

            if not blocking:
                self._rejected_requests += 1
                if should_raise:
                    raise McpRateLimitExceededError(
                        f"compliance : not possible. Rate limit exceeded for '{key}'. retry_after: {wait_time:.3f}s"
                    )
                return False

            if timeout is not None and wait_time > timeout:
                self._rejected_requests += 1
                if should_raise:
                    raise McpRateLimitExceededError(
                        f"compliance : not possible. Rate limit wait {wait_time:.3f}s exceeds timeout {timeout:.3f}s for '{key}'."
                    )
                return False

        time.sleep(wait_time)

        with self._lock:
            bucket = self._get_bucket(bucket_key, fill_rate, burst)
            bucket.tokens = max(0.0, bucket.tokens - cost)
            self._allowed_requests += 1
            self._throttled_requests += 1
            self._wait_time_total_seconds += wait_time
            return True

    def limit(
        self,
        key: str = "default",
        cost: float = 1.0,
        blocking: bool = True,
        timeout: Optional[float] = None,
        raise_on_limit: Optional[bool] = None,
    ) -> _RateLimitContext:
        """Return context manager acquiring token capacity on entrance."""
        return _RateLimitContext(
            limiter=self,
            key=key,
            cost=cost,
            blocking=blocking,
            timeout=timeout,
            raise_on_limit=raise_on_limit,
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Return rate limiter telemetry counters and active bucket counts."""
        with self._lock:
            return {
                "default_rate": self.default_rate,
                "per_seconds": self.per_seconds,
                "default_burst": self.default_burst,
                "rules_count": len(self._rules),
                "total_requests": self._total_requests,
                "allowed_requests": self._allowed_requests,
                "throttled_requests": self._throttled_requests,
                "rejected_requests": self._rejected_requests,
                "active_buckets_count": len(self._buckets),
                "wait_time_total_seconds": self._wait_time_total_seconds,
            }

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configured rules and active buckets."""
        with self._lock:
            self._total_requests = 0
            self._allowed_requests = 0
            self._throttled_requests = 0
            self._rejected_requests = 0
            self._wait_time_total_seconds = 0.0


_DEFAULT_RATE_LIMITER = McpRateLimiter()


def get_default_rate_limiter() -> McpRateLimiter:
    """Return default singleton MCP rate limiter."""
    return _DEFAULT_RATE_LIMITER


def reset_rate_limiter() -> None:
    """Reset global MCP rate limiter state."""
    _DEFAULT_RATE_LIMITER.reset()


def create_rate_limiter(
    default_rate: float = 60.0,
    per_seconds: float = 60.0,
    default_burst: Optional[int] = None,
    raise_on_limit: bool = False,
) -> McpRateLimiter:
    """Instantiate a new dedicated MCP rate limiter."""
    return McpRateLimiter(
        default_rate=default_rate,
        per_seconds=per_seconds,
        default_burst=default_burst,
        raise_on_limit=raise_on_limit,
    )

class McpLogLevel(str, enum.Enum):
    """
    Standard Model Context Protocol log level enumeration.
    Ordered monotonically from least to most severe.
    """
    DEBUG = "debug"
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    ALERT = "alert"
    EMERGENCY = "emergency"


_LOG_LEVEL_SEVERITY: Dict[McpLogLevel, int] = {
    McpLogLevel.DEBUG: 10,
    McpLogLevel.INFO: 20,
    McpLogLevel.NOTICE: 25,
    McpLogLevel.WARNING: 30,
    McpLogLevel.ERROR: 40,
    McpLogLevel.CRITICAL: 50,
    McpLogLevel.ALERT: 60,
    McpLogLevel.EMERGENCY: 70,
}


def _coerce_log_level(level: Union[McpLogLevel, str]) -> McpLogLevel:
    """Coerce string or enum instance to canonical McpLogLevel."""
    if isinstance(level, McpLogLevel):
        return level
    clean = str(level).strip().lower()
    if clean in ("warn", "warning"):
        return McpLogLevel.WARNING
    if clean in ("err", "error"):
        return McpLogLevel.ERROR
    if clean in ("crit", "critical"):
        return McpLogLevel.CRITICAL
    if clean in ("emerg", "emergency", "fatal"):
        return McpLogLevel.EMERGENCY
    try:
        return McpLogLevel(clean)
    except (ValueError, KeyError):
        return McpLogLevel.INFO


@dataclass
class McpLogEntry:
    """
    Immutable representation of single MCP log event record.
    """
    level: McpLogLevel
    logger: str
    message: str
    timestamp: float = field(default_factory=time.time)
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize log entry to standard dictionary format."""
        out: Dict[str, Any] = {
            "level": self.level.value,
            "logger": self.logger,
            "message": self.message,
            "timestamp": self.timestamp,
        }
        if self.data is not None:
            out["data"] = dict(self.data)
        return out


class McpLogBridge:
    """
    Bridges MCP notifications/message and process stderr streams into unified telemetry.
    Supports level filtering, handler subscriptions, ring buffering, and result sanitization.
    """

    def __init__(
        self,
        min_level: Union[McpLogLevel, str] = McpLogLevel.INFO,
        max_entries: int = 1000,
        sanitizer: Optional[Any] = None,
    ) -> None:
        self.min_level: McpLogLevel = _coerce_log_level(min_level)
        self.max_entries: int = max(10, int(max_entries))
        self.sanitizer: Optional[Any] = sanitizer
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset log buffer, telemetry counters, and handlers."""
        with getattr(self, "_lock", threading.RLock()):
            self._entries: List[McpLogEntry] = []
            self._handlers: List[Callable[[McpLogEntry], None]] = []
            self._level_counts: Dict[str, int] = {lvl.value: 0 for lvl in McpLogLevel}
            self._total_ingested: int = 0
            self._total_dropped: int = 0

    def add_handler(self, handler: Callable[[McpLogEntry], None]) -> None:
        """Register subscriber callback invoked upon each accepted log entry."""
        with self._lock:
            self._handlers.append(handler)

    def is_enabled_for(self, level: Union[McpLogLevel, str]) -> bool:
        """Return true when specified level meets or exceeds configured min_level."""
        lvl = _coerce_log_level(level)
        return _LOG_LEVEL_SEVERITY[lvl] >= _LOG_LEVEL_SEVERITY[self.min_level]

    def log(
        self,
        level: Union[McpLogLevel, str],
        message: str,
        logger: str = "root",
        data: Optional[Dict[str, Any]] = None,
    ) -> Optional[McpLogEntry]:
        """Record structured log event if severity satisfies threshold."""
        lvl = _coerce_log_level(level)
        now = time.time()

        with self._lock:
            self._total_ingested += 1
            if not self.is_enabled_for(lvl):
                self._total_dropped += 1
                return None

            clean_msg = str(message).strip()
            if self.sanitizer and hasattr(self.sanitizer, "sanitize_text"):
                clean_msg = self.sanitizer.sanitize_text(clean_msg)

            clean_data = dict(data) if data is not None else None
            if self.sanitizer and clean_data and hasattr(self.sanitizer, "sanitize"):
                clean_data = self.sanitizer.sanitize(clean_data)

            entry = McpLogEntry(
                level=lvl,
                logger=str(logger).strip() or "root",
                message=clean_msg,
                timestamp=now,
                data=clean_data,
            )

            self._entries.append(entry)
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries:]

            self._level_counts[lvl.value] = self._level_counts.get(lvl.value, 0) + 1

            for handler in self._handlers:
                try:
                    handler(entry)
                except Exception:
                    pass

            return entry

    def debug(self, message: str, logger: str = "root", data: Optional[Dict[str, Any]] = None) -> Optional[McpLogEntry]:
        """Record debug level log message."""
        return self.log(McpLogLevel.DEBUG, message, logger=logger, data=data)

    def info(self, message: str, logger: str = "root", data: Optional[Dict[str, Any]] = None) -> Optional[McpLogEntry]:
        """Record info level log message."""
        return self.log(McpLogLevel.INFO, message, logger=logger, data=data)

    def warning(self, message: str, logger: str = "root", data: Optional[Dict[str, Any]] = None) -> Optional[McpLogEntry]:
        """Record warning level log message."""
        return self.log(McpLogLevel.WARNING, message, logger=logger, data=data)

    def error(self, message: str, logger: str = "root", data: Optional[Dict[str, Any]] = None) -> Optional[McpLogEntry]:
        """Record error level log message."""
        return self.log(McpLogLevel.ERROR, message, logger=logger, data=data)

    def critical(self, message: str, logger: str = "root", data: Optional[Dict[str, Any]] = None) -> Optional[McpLogEntry]:
        """Record critical level log message."""
        return self.log(McpLogLevel.CRITICAL, message, logger=logger, data=data)

    def ingest_notification(
        self,
        params: Dict[str, Any],
        default_logger: str = "server",
    ) -> Optional[McpLogEntry]:
        """Parse and ingest JSON-RPC notifications/message payload."""
        if not isinstance(params, dict):
            return None

        raw_level = params.get("level", "info")
        raw_logger = params.get("logger") or default_logger
        raw_data = params.get("data")
        msg = ""

        if isinstance(raw_data, str):
            msg = raw_data
            data_payload = None
        elif isinstance(raw_data, dict):
            msg = str(raw_data.get("message") or raw_data.get("msg") or raw_data.get("text") or "")
            data_payload = raw_data
        else:
            msg = str(raw_data or params.get("message") or "")
            data_payload = None

        if not msg:
            msg = str(params.get("message") or "")

        return self.log(
            level=raw_level,
            message=msg,
            logger=raw_logger,
            data=data_payload,
        )

    def ingest_stderr(
        self,
        line: str,
        logger: str = "stderr",
        level: Optional[Union[McpLogLevel, str]] = None,
    ) -> Optional[McpLogEntry]:
        """Parse raw process stderr line and extract log severity."""
        raw = line.strip()
        if not raw:
            return None

        detected_level = McpLogLevel.INFO if level is None else _coerce_log_level(level)

        if level is None:
            upper = raw.upper()
            if upper.startswith("[DEBUG]") or " DEBUG " in upper or upper.startswith("DEBUG:"):
                detected_level = McpLogLevel.DEBUG
            elif upper.startswith("[WARN]") or upper.startswith("[WARNING]") or " WARN " in upper or upper.startswith("WARN:"):
                detected_level = McpLogLevel.WARNING
            elif upper.startswith("[ERROR]") or " ERROR " in upper or upper.startswith("ERROR:"):
                detected_level = McpLogLevel.ERROR
            elif upper.startswith("[FATAL]") or upper.startswith("[CRITICAL]") or " CRITICAL " in upper:
                detected_level = McpLogLevel.CRITICAL

        return self.log(
            level=detected_level,
            message=raw,
            logger=logger,
        )

    def get_entries(
        self,
        min_level: Optional[Union[McpLogLevel, str]] = None,
        logger: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return serialized list of recorded log events matching optional filters."""
        threshold_val = _LOG_LEVEL_SEVERITY[_coerce_log_level(min_level)] if min_level else None
        target_logger = logger.strip().lower() if logger else None

        with self._lock:
            filtered = []
            for entry in self._entries:
                if threshold_val is not None:
                    if _LOG_LEVEL_SEVERITY[entry.level] < threshold_val:
                        continue
                if target_logger is not None:
                    if entry.logger.strip().lower() != target_logger:
                        continue
                filtered.append(entry.to_dict())

            if limit is not None and limit > 0:
                return filtered[-limit:]
            return filtered

    def clear(self) -> None:
        """Clear recorded log entries."""
        with self._lock:
            self._entries.clear()

    def get_metrics(self) -> Dict[str, Any]:
        """Return log bridge telemetry counters and distribution metrics."""
        with self._lock:
            return {
                "min_level": self.min_level.value,
                "total_ingested": self._total_ingested,
                "total_dropped": self._total_dropped,
                "buffered_entries": len(self._entries),
                "handlers_count": len(self._handlers),
                "levels": dict(self._level_counts),
            }

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving buffered logs and handlers."""
        with self._lock:
            self._total_ingested = 0
            self._total_dropped = 0
            self._level_counts = {lvl.value: 0 for lvl in McpLogLevel}


_DEFAULT_LOG_BRIDGE = McpLogBridge()


def get_default_log_bridge() -> McpLogBridge:
    """Return default singleton MCP log bridge."""
    return _DEFAULT_LOG_BRIDGE


def reset_log_bridge() -> None:
    """Reset global MCP log bridge state."""
    _DEFAULT_LOG_BRIDGE.reset()


def create_log_bridge(
    min_level: Union[McpLogLevel, str] = McpLogLevel.INFO,
    max_entries: int = 1000,
    sanitizer: Optional[Any] = None,
) -> McpLogBridge:
    """Instantiate a new dedicated MCP log bridge."""
    return McpLogBridge(min_level=min_level, max_entries=max_entries, sanitizer=sanitizer)







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
        rate_limiter: Optional[Any] = None,
        log_bridge: Optional[Any] = None,
    ) -> None:
        self.separator = separator
        self.manifest_cache = manifest_cache
        self.result_sanitizer = result_sanitizer
        self.rate_limiter = rate_limiter
        self.log_bridge = log_bridge
        self.reset()

    def set_rate_limiter(self, rate_limiter: Optional[Any]) -> None:
        """Configure rate limiter on namespace router."""
        self.rate_limiter = rate_limiter

    def set_log_bridge(self, log_bridge: Optional[Any]) -> None:
        """Configure log bridge on namespace router."""
        self.log_bridge = log_bridge

    def get_logs(
        self,
        namespace: Optional[str] = None,
        min_level: Optional[Union[Any, str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve collected log entries from attached log bridge."""
        if self.log_bridge:
            return self.log_bridge.get_entries(min_level=min_level, logger=namespace, limit=limit)
        return []

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

    def get_client_lifecycle_state(self, namespace: str) -> Optional[str]:
        """Return lifecycle state string for named client namespace."""
        client = self.get_client(namespace)
        if client and hasattr(client, "lifecycle"):
            return client.lifecycle.state.value
        return None

    def list_lifecycle_states(self) -> Dict[str, str]:
        """Return dictionary mapping client namespaces to their current lifecycle state string."""
        res: Dict[str, str] = {}
        for ns, c in self._namespaces.items():
            if hasattr(c, "lifecycle"):
                res[ns] = c.lifecycle.state.value
            else:
                res[ns] = "unknown"
        return res

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
        rate_limit: bool = True,
    ) -> Any:
        """Route tool execution through namespace router."""
        self._dispatch_count += 1
        if rate_limit and self.rate_limiter:
            self.rate_limiter.acquire(qualified_tool_name)
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
        rate_limit: bool = True,
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


class McpServerState(str, enum.Enum):
    """
    Formal enumeration of MCP server lifecycle states.
    Permits direct string equivalence and deterministic serialization.
    """
    UNINITIALIZED = "uninitialized"
    STARTING = "starting"
    INITIALIZING = "initializing"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    RESTARTING = "restarting"


VALID_TRANSITIONS: Dict[McpServerState, Set[McpServerState]] = {
    McpServerState.UNINITIALIZED: {
        McpServerState.STARTING,
        McpServerState.STOPPED,
    },
    McpServerState.STARTING: {
        McpServerState.INITIALIZING,
        McpServerState.READY,
        McpServerState.FAILED,
        McpServerState.STOPPING,
        McpServerState.STOPPED,
    },
    McpServerState.INITIALIZING: {
        McpServerState.READY,
        McpServerState.FAILED,
        McpServerState.STOPPING,
        McpServerState.STOPPED,
    },
    McpServerState.READY: {
        McpServerState.STOPPING,
        McpServerState.STOPPED,
        McpServerState.FAILED,
        McpServerState.RESTARTING,
    },
    McpServerState.STOPPING: {
        McpServerState.STOPPED,
        McpServerState.FAILED,
    },
    McpServerState.STOPPED: {
        McpServerState.STARTING,
        McpServerState.UNINITIALIZED,
    },
    McpServerState.FAILED: {
        McpServerState.RESTARTING,
        McpServerState.STARTING,
        McpServerState.STOPPING,
        McpServerState.STOPPED,
        McpServerState.UNINITIALIZED,
    },
    McpServerState.RESTARTING: {
        McpServerState.STARTING,
        McpServerState.INITIALIZING,
        McpServerState.READY,
        McpServerState.FAILED,
        McpServerState.STOPPING,
        McpServerState.STOPPED,
    },
}


class IllegalStateTransitionError(ValueError):
    """
    Raised when an illegal MCP lifecycle state transition requested.
    Asserts compliance : not possible in error details.
    """
    pass


def _coerce_mcp_state(val: Union[McpServerState, str]) -> McpServerState:
    """Convert input string or state enum to canonical McpServerState."""
    if isinstance(val, McpServerState):
        return val
    try:
        return McpServerState(str(val).strip().lower())
    except (ValueError, KeyError):
        valid = ", ".join(s.value for s in McpServerState)
        raise ValueError(f"Unknown MCP server state '{val}'. Valid states: {valid}")


@dataclass
class McpLifecycleTransition:
    """
    Immutable lifecycle transition event record.
    """
    source: McpServerState
    target: McpServerState
    timestamp: float = field(default_factory=time.time)
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize transition record to standard dictionary format."""
        return {
            "source": self.source.value,
            "target": self.target.value,
            "timestamp": self.timestamp,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


class McpLifecycleFSM:
    """
    Finite state machine governing Model Context Protocol client lifecycle.
    Maintains deterministic transition tables, transition ledger, and event hooks.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        initial_state: Union[McpServerState, str] = McpServerState.UNINITIALIZED,
        max_history: int = 500,
    ) -> None:
        self.name: str = str(name or "default").strip()
        self._state: McpServerState = _coerce_mcp_state(initial_state)
        self._max_history: int = max(10, int(max_history))
        self._lock = threading.RLock()

        self._history: List[McpLifecycleTransition] = []
        self._enter_hooks: Dict[McpServerState, List[Callable[[McpLifecycleTransition], None]]] = {
            s: [] for s in McpServerState
        }
        self._exit_hooks: Dict[McpServerState, List[Callable[[McpLifecycleTransition], None]]] = {
            s: [] for s in McpServerState
        }
        self._transition_hooks: List[Callable[[McpLifecycleTransition], None]] = []

        self._transitions_count: int = 0
        self._failed_transitions_count: int = 0
        self._state_entry_time: float = time.time()
        self._cumulative_ready_time: float = 0.0
        self._last_ready_enter: Optional[float] = None
        self._bound_client: Any = None

    @property
    def state(self) -> McpServerState:
        """Current lifecycle state."""
        with self._lock:
            return self._state

    @property
    def is_ready(self) -> bool:
        """Return true when current state equals READY."""
        with self._lock:
            return self._state == McpServerState.READY

    @property
    def is_running(self) -> bool:
        """Return true when current state equals READY."""
        with self._lock:
            return self._state == McpServerState.READY

    @property
    def is_active(self) -> bool:
        """Return true when current state represents active server."""
        with self._lock:
            return self._state in (
                McpServerState.STARTING,
                McpServerState.INITIALIZING,
                McpServerState.READY,
                McpServerState.RESTARTING,
            )

    @property
    def is_terminal(self) -> bool:
        """Return true when current state represents terminal state."""
        with self._lock:
            return self._state in (McpServerState.STOPPED, McpServerState.FAILED)

    @property
    def is_failed(self) -> bool:
        """Return true when current state equals FAILED."""
        with self._lock:
            return self._state == McpServerState.FAILED

    def can_transition(
        self,
        target_state: Union[McpServerState, str],
        allow_noop: bool = False,
    ) -> bool:
        """Evaluate whether transition to target state permitted from current state."""
        target = _coerce_mcp_state(target_state)
        with self._lock:
            if target == self._state:
                return bool(allow_noop)
            allowed = VALID_TRANSITIONS.get(self._state, set())
            return target in allowed

    def transition_to(
        self,
        target_state: Union[McpServerState, str],
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        allow_noop: bool = False,
    ) -> bool:
        """Execute deterministic state transition with hook dispatch and ledger recording."""
        target = _coerce_mcp_state(target_state)
        now = time.time()

        with self._lock:
            source = self._state
            if source == target:
                if allow_noop:
                    return True
                self._failed_transitions_count += 1
                raise IllegalStateTransitionError(
                    f"compliance : not possible. Invalid state transition from {source.value} to {target.value} for server '{self.name}'. reason: self-transition disallowed."
                )

            allowed = VALID_TRANSITIONS.get(source, set())
            if target not in allowed:
                self._failed_transitions_count += 1
                detail = f"compliance : not possible. Invalid state transition from {source.value} to {target.value} for server '{self.name}'."
                if reason:
                    detail += f" reason: {reason}"
                raise IllegalStateTransitionError(detail)

            trans = McpLifecycleTransition(
                source=source,
                target=target,
                timestamp=now,
                reason=reason,
                metadata=dict(metadata or {}),
            )

            for cb in self._exit_hooks.get(source, []):
                self._safe_invoke_callback(cb, trans)

            if source == McpServerState.READY and self._last_ready_enter is not None:
                self._cumulative_ready_time += max(0.0, now - self._last_ready_enter)
                self._last_ready_enter = None
            if target == McpServerState.READY:
                self._last_ready_enter = now

            self._state = target
            self._state_entry_time = now
            self._transitions_count += 1

            self._history.append(trans)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            for cb in self._enter_hooks.get(target, []):
                self._safe_invoke_callback(cb, trans)

            for cb in self._transition_hooks:
                self._safe_invoke_callback(cb, trans)

            return True

    def force_state(
        self,
        target_state: Union[McpServerState, str],
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Forcibly set state overriding transition table for emergency recovery."""
        target = _coerce_mcp_state(target_state)
        now = time.time()
        meta = dict(metadata or {})
        meta["forced"] = True

        with self._lock:
            source = self._state
            trans = McpLifecycleTransition(
                source=source,
                target=target,
                timestamp=now,
                reason=reason or "emergency force state",
                metadata=meta,
            )

            for cb in self._exit_hooks.get(source, []):
                self._safe_invoke_callback(cb, trans)

            if source == McpServerState.READY and self._last_ready_enter is not None:
                self._cumulative_ready_time += max(0.0, now - self._last_ready_enter)
                self._last_ready_enter = None
            if target == McpServerState.READY:
                self._last_ready_enter = now

            self._state = target
            self._state_entry_time = now
            self._transitions_count += 1

            self._history.append(trans)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            for cb in self._enter_hooks.get(target, []):
                self._safe_invoke_callback(cb, trans)

            for cb in self._transition_hooks:
                self._safe_invoke_callback(cb, trans)

    def on_enter(
        self,
        state: Union[McpServerState, str],
        callback: Callable[..., Any],
    ) -> None:
        """Register callback executed upon entering designated target state."""
        st = _coerce_mcp_state(state)
        with self._lock:
            self._enter_hooks[st].append(callback)

    def on_exit(
        self,
        state: Union[McpServerState, str],
        callback: Callable[..., Any],
    ) -> None:
        """Register callback executed upon exiting designated state."""
        st = _coerce_mcp_state(state)
        with self._lock:
            self._exit_hooks[st].append(callback)

    def on_transition(self, callback: Callable[..., Any]) -> None:
        """Register callback executed on all valid state transitions."""
        with self._lock:
            self._transition_hooks.append(callback)

    def _safe_invoke_callback(
        self,
        cb: Callable[..., Any],
        transition: McpLifecycleTransition,
    ) -> None:
        """Safely invoke callback catching exceptions to prevent lifecycle pipeline aborts."""
        try:
            cb(transition)
        except TypeError:
            try:
                cb()
            except Exception:
                pass
        except Exception:
            pass

    def get_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return list of recorded transition events in chronological order."""
        with self._lock:
            items = [t.to_dict() for t in self._history]
            if limit is not None and limit > 0:
                return items[-limit:]
            return items

    def get_metrics(self) -> Dict[str, Any]:
        """Return lifecycle telemetry counters and transition statistics."""
        with self._lock:
            now = time.time()
            time_in_state = max(0.0, now - self._state_entry_time)
            ready_time = self._cumulative_ready_time
            if self._state == McpServerState.READY and self._last_ready_enter is not None:
                ready_time += max(0.0, now - self._last_ready_enter)

            return {
                "name": self.name,
                "current_state": self._state.value,
                "transitions_count": self._transitions_count,
                "failed_transitions_count": self._failed_transitions_count,
                "history_length": len(self._history),
                "state_entry_time": self._state_entry_time,
                "time_in_current_state_seconds": time_in_state,
                "ready_duration_seconds": ready_time,
                "is_ready": self._state == McpServerState.READY,
                "is_active": self.is_active,
                "is_terminal": self.is_terminal,
            }

    def reset_metrics(self) -> None:
        """Reset transition counters, active timers, and clear history ledger."""
        with self._lock:
            self._transitions_count = 0
            self._failed_transitions_count = 0
            self._cumulative_ready_time = 0.0
            self._last_ready_enter = time.time() if self._state == McpServerState.READY else None
            self._state_entry_time = time.time()
            self._history.clear()

    def reset(
        self,
        initial_state: Union[McpServerState, str] = McpServerState.UNINITIALIZED,
    ) -> None:
        """Reset state machine to initial state and clear hooks and history."""
        with self._lock:
            self._state = _coerce_mcp_state(initial_state)
            self._state_entry_time = time.time()
            self._last_ready_enter = None
            self._cumulative_ready_time = 0.0
            self._transitions_count = 0
            self._failed_transitions_count = 0
            self._history.clear()
            self._enter_hooks = {s: [] for s in McpServerState}
            self._exit_hooks = {s: [] for s in McpServerState}
            self._transition_hooks.clear()

    def bind_client(self, client: Any) -> None:
        """Bind state machine to target MCP client instance."""
        with self._lock:
            self._bound_client = client
            try:
                setattr(client, "lifecycle", self)
                setattr(client, "_lifecycle_fsm", self)
            except Exception:
                pass

    def __enter__(self) -> "McpLifecycleFSM":
        """Enter context manager transitioning state toward starting."""
        if self.can_transition(McpServerState.STARTING):
            self.transition_to(McpServerState.STARTING, reason="context manager entered")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager transitioning state toward stopped."""
        if exc_type is not None:
            if self.can_transition(McpServerState.FAILED):
                self.transition_to(McpServerState.FAILED, reason=str(exc_val))
            elif self.can_transition(McpServerState.STOPPED):
                self.transition_to(McpServerState.STOPPED, reason="context manager error")
        else:
            if self.can_transition(McpServerState.STOPPING):
                self.transition_to(McpServerState.STOPPING, reason="context manager exit")
            if self.can_transition(McpServerState.STOPPED):
                self.transition_to(McpServerState.STOPPED, reason="context manager completed")


_DEFAULT_LIFECYCLE_FSM = McpLifecycleFSM(name="default_singleton")


def get_default_lifecycle_fsm() -> McpLifecycleFSM:
    """Return default singleton MCP lifecycle state machine."""
    return _DEFAULT_LIFECYCLE_FSM


def reset_lifecycle_fsm() -> None:
    """Reset global MCP lifecycle state machine."""
    _DEFAULT_LIFECYCLE_FSM.reset()


def create_lifecycle_fsm(
    name: Optional[str] = None,
    initial_state: Union[McpServerState, str] = McpServerState.UNINITIALIZED,
    max_history: int = 500,
) -> McpLifecycleFSM:
    """Instantiate a new dedicated MCP lifecycle finite state machine."""
    return McpLifecycleFSM(name=name, initial_state=initial_state, max_history=max_history)


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
        self.rate_limiter: Optional[Any] = None
        self.log_bridge: Optional[Any] = None
        self.lifecycle: McpLifecycleFSM = create_lifecycle_fsm(name=self.namespace or self.command or "subprocess")
        self.lifecycle.bind_client(self)

    @property
    def lifecycle_state(self) -> McpServerState:
        """Current lifecycle state."""
        return self.lifecycle.state

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
                if hasattr(self, "log_bridge") and self.log_bridge:
                    self.log_bridge.ingest_stderr(stripped, logger=self.namespace or "stderr")
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
            if self.lifecycle.can_transition(McpServerState.STARTING):
                self.lifecycle.transition_to(McpServerState.STARTING, reason="starting subprocess")
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
            if self.lifecycle.can_transition(McpServerState.INITIALIZING):
                self.lifecycle.transition_to(McpServerState.INITIALIZING, reason="initializing mcp handshake")

            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()
            self._ensure_stdout_reader()

        # Handshake outside lock so _send_rpc can acquire lock
        try:
            self._initialize()
            if self.lifecycle.can_transition(McpServerState.READY):
                self.lifecycle.transition_to(McpServerState.READY, reason="handshake complete")
        except Exception as exc:
            if self.lifecycle.can_transition(McpServerState.FAILED):
                self.lifecycle.transition_to(McpServerState.FAILED, reason=f"handshake failed: {exc}")
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

                if res.get("method") == "notifications/message" or "notifications/" in str(res.get("method", "")):
                    if hasattr(self, "log_bridge") and self.log_bridge:
                        self.log_bridge.ingest_notification(res.get("params", {}), default_logger=self.namespace or "server")
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

    def set_rate_limiter(self, rate_limiter: Optional[Any]) -> None:
        """Configure rate limiter on client."""
        self.rate_limiter = rate_limiter

    def set_log_bridge(self, log_bridge: Optional[Any]) -> None:
        """Configure log bridge on client."""
        self.log_bridge = log_bridge

    def call_tool(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Call a specific tool on the MCP server with the provided arguments and timeout guard."""
        if self.rate_limiter:
            self.rate_limiter.acquire(tool_name)
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
            if self.lifecycle.can_transition(McpServerState.STOPPING):
                self.lifecycle.transition_to(McpServerState.STOPPING, reason="close initiated")
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
        self.rate_limiter: Optional[Any] = None
        self.log_bridge: Optional[Any] = None
        self.lifecycle: McpLifecycleFSM = create_lifecycle_fsm(name=self.namespace or "mcp_lazy_client")
        self.lifecycle.bind_client(self)

    @property
    def lifecycle_state(self) -> McpServerState:
        """Current lifecycle state."""
        return self.lifecycle.state

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
            if self.lifecycle.can_transition(McpServerState.STOPPING):
                self.lifecycle.transition_to(McpServerState.STOPPING, reason="lazy client close initiated")
            if self._client is not None:
                try:
                    if hasattr(self._client, "close"):
                        self._client.close()
                except Exception:
                    pass
                self._client = None
            if self.lifecycle.can_transition(McpServerState.STOPPED):
                self.lifecycle.transition_to(McpServerState.STOPPED, reason="lazy client closed")

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
