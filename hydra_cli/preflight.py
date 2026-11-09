"""Launch preflight and route truth strip for Hydra CLI/REPL/TUI (WO-2).

Authority: PREFLIGHT-SPEC.md.
Probes active routes concurrently with zero tokens in the normal path, at most
3 GETs (/props on local llama.cpp is a local metadata check alongside /v1/models),
~2.5s wall-clock budget, and a 120s PASS cache.

Closed list of failure reasons (shown verbatim as data):
  - timeout
  - http <code>
  - model absent
  - context <n> < 131072
  - no server
  - no key configured
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from hydra_cli import local_server, modes
from hydra_cli.config import (
    DEFAULT_CHEAPERINFERENCE_BASE,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_NVIDIA_BASE,
    load_dotenv,
    resolve_route,
)
from hydra_cli.providers import (
    CHEAPERINFERENCE_BALANCE_FLOOR,
    chat_completions_url,
    clean_secret,
    get_cheaperinference_balance,
    is_port_open,
)

PREFLIGHT_CACHE_TTL: float = 120.0
PREFLIGHT_WALL_BUDGET_SEC: float = 2.5
LOCAL_TCP_TIMEOUT_SEC: float = 0.4
LOCAL_HTTP_TIMEOUT_SEC: float = 2.0
CLOUD_HTTP_TIMEOUT_SEC: float = 2.4
COMPLETION_PROBE_TIMEOUT_SEC: float = 10.0
REQUIRED_LOCAL_CONTEXT: int = 131072

DEFAULT_LOCAL_PORTS: Tuple[int, ...] = (8080, 11434, 8000)
LOCAL_SEAT_MODEL_ID: str = "prism-ml/Ternary-Bonsai-2-2B-gguf"
LIGHT_SEAT_MODEL_ID: str = "muse-spark-1.3-contributor"
FREE_SEAT_MODEL_ID: str = "nvidia/nemotron-3.5-lightning:free"

_HTTP_REASON_RE = re.compile(r"^http \d{3}$")
_CTX_REASON_RE = re.compile(r"^context \d+ < 131072$")
_CLOSED_STATIC_REASONS = frozenset(
    {"timeout", "model absent", "no server", "no key configured"}
)

_CACHE_LOCK = threading.RLock()
_ROUTE_CACHE: Dict[str, Tuple[float, "RouteProbeResult"]] = {}
_LAST_REPORT: Optional["PreflightReport"] = None
_COMPLETION_PROBE_USED: bool = False


def is_valid_failure_reason(reason: Optional[str]) -> bool:
    """Verify that a failure reason belongs to the spec's closed list."""
    if not reason or not isinstance(reason, str):
        return False
    if reason in _CLOSED_STATIC_REASONS:
        return True
    if _HTTP_REASON_RE.match(reason):
        return True
    if _CTX_REASON_RE.match(reason):
        return True
    return False


@dataclass
class RouteProbeResult:
    route: str
    status: str  # "PASS", "FAIL", or "SKIPPED"
    model_id: str
    short_label: str
    reason: Optional[str] = None
    n_ctx: Optional[int] = None
    port: Optional[int] = None
    endpoint: Optional[str] = None
    managed_configured: bool = False
    start_available: bool = False
    down_message: Optional[str] = None
    warn: Optional[str] = None
    balance: Optional[float] = None
    inconclusive_candidate: bool = False
    completion_probe_used: bool = False
    cached: bool = False
    gets_count: int = 0
    probed_at: float = field(default_factory=time.monotonic)

    @property
    def ok(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route": self.route,
            "status": self.status,
            "ok": self.ok,
            "model_id": self.model_id,
            "short_label": self.short_label,
            "reason": self.reason,
            "n_ctx": self.n_ctx,
            "port": self.port,
            "endpoint": self.endpoint,
            "managed_configured": self.managed_configured,
            "start_available": self.start_available,
            "down_message": self.down_message,
            "warn": self.warn,
            "balance": self.balance,
            "completion_probe_used": self.completion_probe_used,
            "cached": self.cached,
        }


@dataclass
class PreflightReport:
    mode: str
    default_route: str
    local: RouteProbeResult
    light: RouteProbeResult
    free: RouteProbeResult
    elapsed_ms: float = 0.0
    gets_count: int = 0
    completion_probes_count: int = 0

    @property
    def default_route_result(self) -> RouteProbeResult:
        if self.default_route == "local":
            return self.local
        if self.default_route == "free":
            return self.free
        return self.light

    @property
    def default_route_ok(self) -> bool:
        return self.default_route_result.ok

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "default_route": self.default_route,
            "default_route_ok": self.default_route_ok,
            "strip": format_route_strip(self),
            "notice": format_preflight_notice(self, mode=self.mode),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "gets_count": self.gets_count,
            "completion_probes_count": self.completion_probes_count,
            "routes": {
                "local": self.local.to_dict(),
                "light": self.light.to_dict(),
                "free": self.free.to_dict(),
            },
        }


def default_route_for_mode(mode: Optional[str] = None) -> str:
    """Map a Hydra mode name to its primary preflight route key."""
    active = (mode or modes.current_mode() or "local").strip().lower()
    if active == "local":
        return "local"
    if active == "free":
        return "free"
    return "light"


def invalidate_preflight_cache(route: Optional[str] = None) -> None:
    """Clear cached preflight results (all routes, or one named route)."""
    with _CACHE_LOCK:
        if route is None:
            _ROUTE_CACHE.clear()
        else:
            _ROUTE_CACHE.pop(route, None)


def reset_preflight_state() -> None:
    """Reset both the route cache and the per-launch 1-token completion probe guard."""
    global _COMPLETION_PROBE_USED, _LAST_REPORT
    with _CACHE_LOCK:
        _ROUTE_CACHE.clear()
        _LAST_REPORT = None
        _COMPLETION_PROBE_USED = False


def get_last_preflight_report() -> Optional[PreflightReport]:
    with _CACHE_LOCK:
        return _LAST_REPORT


def _is_managed_local_configured() -> bool:
    """Check whether ~/.hydra/local-server-state.json exists (or a managed state is recorded)."""
    try:
        st_path = local_server.state_path()
        if os.path.isfile(st_path):
            return True
        state = local_server.read_server_state()
        return bool(state)
    except Exception:
        return False


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return True
        if "timed out" in str(reason or exc).lower():
            return True
    return "timed out" in str(exc).lower()


def _extract_model_ids(payload: Any) -> Optional[List[str]]:
    """Extract model IDs from an OpenAI-style /v1/models or Ollama payload.
    Returns None if the payload does not contain a recognizable models list."""
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("data"), list):
        ids: List[str] = []
        for item in payload["data"]:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name") or item.get("model")
                if mid:
                    ids.append(str(mid))
            elif isinstance(item, str) and item:
                ids.append(item)
        return ids
    if isinstance(payload.get("models"), list):
        ids = []
        for item in payload["models"]:
            if isinstance(item, dict):
                mid = item.get("name") or item.get("id") or item.get("model")
                if mid:
                    ids.append(str(mid))
            elif isinstance(item, str) and item:
                ids.append(item)
        return ids
    return None


def _model_present(target_model: str, listed_ids: Sequence[str]) -> bool:
    target = target_model.strip()
    target_lower = target.lower()
    target_stripped = target_lower[:-5] if target_lower.endswith(":free") else target_lower
    for raw in listed_ids:
        cand = str(raw).strip()
        cand_lower = cand.lower()
        if cand_lower == target_lower or cand_lower == target_stripped:
            return True
        if cand_lower.endswith(":latest") and cand_lower[:-7] == target_lower:
            return True
    return False


def _http_get_models(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 2.0,
    has_key_signal: bool = False,
) -> Tuple[Optional[List[str]], Optional[str], bool]:
    """Perform GET <url> and parse model IDs.

    Returns (model_ids_or_None, failure_reason_or_None, inconclusive_candidate).
    """
    req_headers = {"Accept": "application/json", "User-Agent": "Hydra-Preflight"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode() or 200
            raw = resp.read().decode("utf-8", errors="replace")
            if status != 200:
                inc = status in (404,) or (status == 401 and has_key_signal)
                return None, f"http {status}", inc
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 500) or 500)
        inc = code in (404,) or (code == 401 and has_key_signal)
        return None, f"http {code}", inc
    except Exception as exc:
        if _is_timeout_error(exc):
            return None, "timeout", False
        return None, "no server", False

    try:
        parsed = json.loads(raw)
    except Exception:
        # Spec 5.3: non-JSON 200 is an inconclusive candidate for the default route
        return None, "model absent", True

    model_ids = _extract_model_ids(parsed)
    if model_ids is None:
        return None, "model absent", True
    return model_ids, None, False


def _run_one_token_completion_probe(
    chat_url: str,
    headers: Optional[Dict[str, str]],
    model_id: str,
    timeout: float = COMPLETION_PROBE_TIMEOUT_SEC,
) -> Tuple[bool, Optional[str]]:
    """Spec 5.3: 1-token completion probe ('ping', max_tokens=1, 10s cap).
    Returns (passed, updated_failure_reason_if_failed)."""
    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "Hydra-Preflight",
    }
    if headers:
        req_headers.update(headers)
    body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    ).encode("utf-8")
    req = urllib.request.Request(chat_url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode() or 200
            if status != 200:
                return False, f"http {status}"
            raw = resp.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 500) or 500)
        return False, f"http {code}"
    except Exception as exc:
        if _is_timeout_error(exc):
            return False, "timeout"
        return False, None

    if not raw:
        return False, None

    # Accept well-formed JSON completion or SSE first chunk
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            data_str = stripped[5:].strip()
            if data_str == "[DONE]":
                return True, None
            try:
                chunk = json.loads(data_str)
                if isinstance(chunk, dict) and isinstance(chunk.get("choices"), list) and chunk["choices"]:
                    return True, None
            except Exception:
                continue
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("choices"), list) and parsed["choices"]:
            return True, None
    except Exception:
        pass
    return False, None


def _resolve_local_candidates(
    ports: Optional[Sequence[int]] = None,
    base_url: Optional[str] = None,
) -> List[Tuple[str, int]]:
    """Determine (host, port) candidates for Route 1 (LOCAL) in priority order."""
    if base_url:
        parsed = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8080
        return [(host, port)]

    if ports is not None:
        return [("127.0.0.1", int(p)) for p in ports]

    # Check explicit environment overrides if set
    for env_key, default_port in (
        ("LLAMACPP_HOST", 8080),
        ("LOCAL_AI_BASE", 8000),
        ("OLLAMA_HOST", 11434),
    ):
        val = os.environ.get(env_key, "").strip()
        if val:
            parsed = urlparse(val if "://" in val else f"http://{val}")
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or default_port
            return [(host, port)]

    return [("127.0.0.1", p) for p in DEFAULT_LOCAL_PORTS]


def probe_local_route(
    ports: Optional[Sequence[int]] = None,
    base_url: Optional[str] = None,
    target_model: Optional[str] = None,
    min_ctx: int = REQUIRED_LOCAL_CONTEXT,
    tcp_timeout: float = LOCAL_TCP_TIMEOUT_SEC,
    http_timeout: float = LOCAL_HTTP_TIMEOUT_SEC,
) -> RouteProbeResult:
    """Probe Route 1 — LOCAL per PREFLIGHT-SPEC.md §5.2."""
    seat = target_model or modes.default_alias("local") or DEFAULT_LOCAL_MODEL or LOCAL_SEAT_MODEL_ID
    # Probe by model id (what the server serves); display by seat name.
    model_id = modes.resolve_local_model(seat) or seat
    short_label = seat
    managed = _is_managed_local_configured()
    candidates = _resolve_local_candidates(ports=ports, base_url=base_url)

    open_target: Optional[Tuple[str, int]] = None
    for host, port in candidates:
        if is_port_open(host, port, timeout=tcp_timeout):
            open_target = (host, port)
            break

    if open_target is None:
        if managed:
            down_msg = "local DOWN: no server on :8080 (managed server configured; start available)"
        else:
            down_msg = "local DOWN: no server and none configured."
        return RouteProbeResult(
            route="local",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason="no server",
            managed_configured=managed,
            start_available=managed,
            down_message=down_msg,
            gets_count=0,
        )

    host, port = open_target
    origin = f"http://{host}:{port}"
    models_url = f"{origin}/v1/models"
    listed_ids, err_reason, inconclusive = _http_get_models(
        models_url, timeout=http_timeout, has_key_signal=True
    )
    gets = 1

    if err_reason is not None:
        return RouteProbeResult(
            route="local",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason=err_reason,
            port=port,
            endpoint=origin,
            managed_configured=managed,
            start_available=managed,
            inconclusive_candidate=inconclusive,
            gets_count=gets,
        )

    if listed_ids is None or not _model_present(model_id, listed_ids):
        return RouteProbeResult(
            route="local",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason="model absent",
            port=port,
            endpoint=origin,
            managed_configured=managed,
            start_available=managed,
            inconclusive_candidate=inconclusive,
            gets_count=gets,
        )

    # Step 3: Context verification on llama.cpp servers (port 8080 or /props endpoint)
    if port != 11434:
        props_url = f"{origin}/props"
        req = urllib.request.Request(
            props_url,
            headers={"Accept": "application/json", "User-Agent": "Hydra-Preflight"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode() or 200
                raw_props = resp.read().decode("utf-8", errors="replace")
                if status != 200:
                    return RouteProbeResult(
                        route="local",
                        status="FAIL",
                        model_id=model_id,
                        short_label=short_label,
                        reason=f"http {status}",
                        port=port,
                        endpoint=origin,
                        managed_configured=managed,
                        start_available=managed,
                        gets_count=gets,
                    )
                props_data = json.loads(raw_props)
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 500) or 500)
            return RouteProbeResult(
                route="local",
                status="FAIL",
                model_id=model_id,
                short_label=short_label,
                reason=f"http {code}",
                port=port,
                endpoint=origin,
                managed_configured=managed,
                start_available=managed,
                gets_count=gets,
            )
        except Exception as exc:
            reason = "timeout" if _is_timeout_error(exc) else "no server"
            return RouteProbeResult(
                route="local",
                status="FAIL",
                model_id=model_id,
                short_label=short_label,
                reason=reason,
                port=port,
                endpoint=origin,
                managed_configured=managed,
                start_available=managed,
                gets_count=gets,
            )

        n_ctx = local_server.extract_props_n_ctx(props_data)
        if n_ctx is None or n_ctx < min_ctx:
            observed = n_ctx if n_ctx is not None else 0
            return RouteProbeResult(
                route="local",
                status="FAIL",
                model_id=model_id,
                short_label=short_label,
                reason=f"context {observed} < {min_ctx}",
                n_ctx=n_ctx,
                port=port,
                endpoint=origin,
                managed_configured=managed,
                start_available=managed,
                gets_count=gets,
            )

        return RouteProbeResult(
            route="local",
            status="PASS",
            model_id=model_id,
            short_label=short_label,
            reason=None,
            n_ctx=n_ctx,
            port=port,
            endpoint=origin,
            managed_configured=managed,
            start_available=managed,
            gets_count=gets,
        )

    return RouteProbeResult(
        route="local",
        status="PASS",
        model_id=model_id,
        short_label=short_label,
        reason=None,
        n_ctx=None,
        port=port,
        endpoint=origin,
        managed_configured=managed,
        start_available=managed,
        gets_count=gets,
    )


def probe_light_route(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    target_model: Optional[str] = None,
    http_timeout: float = CLOUD_HTTP_TIMEOUT_SEC,
    check_balance: bool = True,
) -> RouteProbeResult:
    """Probe Route 2 — LIGHT (spark) per PREFLIGHT-SPEC.md §5.2."""
    light_alias = modes.default_alias("light") or "muse spark contributor"
    model_id = target_model or resolve_route(light_alias)["model"] or LIGHT_SEAT_MODEL_ID
    short_label = "spark"

    key = clean_secret(api_key if api_key is not None else os.environ.get("CHEAPERINFERENCE_API_KEY"))
    if not key:
        return RouteProbeResult(
            route="light",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason="no key configured",
            gets_count=0,
        )

    raw_base = (base_url or os.environ.get("CHEAPERINFERENCE_API_BASE", DEFAULT_CHEAPERINFERENCE_BASE)).strip().rstrip("/")
    if not raw_base.endswith("/v1"):
        raw_base = f"{raw_base}/v1"
    models_url = f"{raw_base}/models"
    headers = {"Authorization": f"Bearer {key}"}

    balance_val: Optional[float] = None
    warn_msg: Optional[str] = None

    if check_balance:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            models_fut = pool.submit(
                _http_get_models, models_url, headers, http_timeout, True
            )
            bal_fut = pool.submit(
                get_cheaperinference_balance,
                key,
                raw_base,
                min(1.5, http_timeout),
            )
            listed_ids, err_reason, inconclusive = models_fut.result()
            try:
                balance_val = bal_fut.result(timeout=0.5)
            except Exception:
                balance_val = None
    else:
        listed_ids, err_reason, inconclusive = _http_get_models(
            models_url, headers=headers, timeout=http_timeout, has_key_signal=True
        )

    if balance_val is None:
        warn_msg = "balance unverified"
    elif balance_val <= CHEAPERINFERENCE_BALANCE_FLOOR:
        warn_msg = f"low balance (${balance_val:.2f})"

    if err_reason is not None:
        return RouteProbeResult(
            route="light",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason=err_reason,
            endpoint=raw_base,
            warn=warn_msg,
            balance=balance_val,
            inconclusive_candidate=inconclusive,
            gets_count=1,
        )

    if listed_ids is None or not _model_present(model_id, listed_ids):
        return RouteProbeResult(
            route="light",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason="model absent",
            endpoint=raw_base,
            warn=warn_msg,
            balance=balance_val,
            inconclusive_candidate=inconclusive,
            gets_count=1,
        )

    return RouteProbeResult(
        route="light",
        status="PASS",
        model_id=model_id,
        short_label=short_label,
        reason=None,
        endpoint=raw_base,
        warn=warn_msg,
        balance=balance_val,
        gets_count=1,
    )


def probe_free_route(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    target_model: Optional[str] = None,
    http_timeout: float = CLOUD_HTTP_TIMEOUT_SEC,
    should_network_probe: bool = True,
) -> RouteProbeResult:
    """Probe Route 3 — FREE per PREFLIGHT-SPEC.md §5.2.

    Probed over the network ONLY if routes 1-2 both fail or mode=free.
    Requires NVIDIA_API_KEY for the primary free seat (nvidia/nemotron-3.5-lightning:free);
    without that key, reason is 'no key configured'.
    """
    model_id = target_model or FREE_SEAT_MODEL_ID
    short_label = "nemotron"

    key = clean_secret(api_key if api_key is not None else os.environ.get("NVIDIA_API_KEY"))
    if not key:
        return RouteProbeResult(
            route="free",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason="no key configured",
            gets_count=0,
        )

    if not should_network_probe:
        return RouteProbeResult(
            route="free",
            status="SKIPPED",
            model_id=model_id,
            short_label=short_label,
            reason=None,
            gets_count=0,
        )

    raw_base = (base_url or os.environ.get("NVIDIA_API_BASE", DEFAULT_NVIDIA_BASE)).strip().rstrip("/")
    if not raw_base.endswith("/v1"):
        raw_base = f"{raw_base}/v1"
    models_url = f"{raw_base}/models"
    headers = {"Authorization": f"Bearer {key}"}

    listed_ids, err_reason, inconclusive = _http_get_models(
        models_url, headers=headers, timeout=http_timeout, has_key_signal=True
    )
    if err_reason is not None:
        return RouteProbeResult(
            route="free",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason=err_reason,
            endpoint=raw_base,
            inconclusive_candidate=inconclusive,
            gets_count=1,
        )

    if listed_ids is None or not _model_present(model_id, listed_ids):
        return RouteProbeResult(
            route="free",
            status="FAIL",
            model_id=model_id,
            short_label=short_label,
            reason="model absent",
            endpoint=raw_base,
            inconclusive_candidate=inconclusive,
            gets_count=1,
        )

    return RouteProbeResult(
        route="free",
        status="PASS",
        model_id=model_id,
        short_label=short_label,
        reason=None,
        endpoint=raw_base,
        gets_count=1,
    )


def _cached_pass(route: str, now: float) -> Optional[RouteProbeResult]:
    with _CACHE_LOCK:
        entry = _ROUTE_CACHE.get(route)
        if entry is None:
            return None
        ts, res = entry
        if res.status == "PASS" and (now - ts) < PREFLIGHT_CACHE_TTL:
            clone = RouteProbeResult(
                route=res.route,
                status=res.status,
                model_id=res.model_id,
                short_label=res.short_label,
                reason=res.reason,
                n_ctx=res.n_ctx,
                port=res.port,
                endpoint=res.endpoint,
                managed_configured=res.managed_configured,
                start_available=res.start_available,
                down_message=res.down_message,
                warn=res.warn,
                balance=res.balance,
                inconclusive_candidate=False,
                completion_probe_used=res.completion_probe_used,
                cached=True,
                gets_count=0,
                probed_at=ts,
            )
            return clone
        return None


def _store_cache(res: RouteProbeResult, now: float) -> None:
    with _CACHE_LOCK:
        _ROUTE_CACHE[res.route] = (now, res)


def _maybe_run_inconclusive_probe(
    res: RouteProbeResult,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[RouteProbeResult, int]:
    """Spec 5.3: Only the session's DEFAULT route, and only when /models returns
    neither PASS nor a hard failure (401 with valid-key signal, 404, or non-JSON 200),
    runs ONE 1-token completion probe per launch."""
    global _COMPLETION_PROBE_USED
    if res.status == "PASS" or not res.inconclusive_candidate:
        return res, 0

    with _CACHE_LOCK:
        if _COMPLETION_PROBE_USED:
            return res, 0
        _COMPLETION_PROBE_USED = True

    if not res.endpoint:
        return res, 0

    chat_url = chat_completions_url(res.endpoint)
    probe_headers: Dict[str, str] = {}
    if headers:
        probe_headers.update(headers)
    elif res.route == "light":
        k = clean_secret(os.environ.get("CHEAPERINFERENCE_API_KEY"))
        if k:
            probe_headers["Authorization"] = f"Bearer {k}"
    elif res.route == "free":
        k = clean_secret(os.environ.get("NVIDIA_API_KEY"))
        if k:
            probe_headers["Authorization"] = f"Bearer {k}"

    passed, new_reason = _run_one_token_completion_probe(
        chat_url=chat_url,
        headers=probe_headers,
        model_id=res.model_id,
        timeout=COMPLETION_PROBE_TIMEOUT_SEC,
    )
    res.completion_probe_used = True
    if passed:
        if res.route == "local" and res.port and res.port != 11434:
            ctx_check = local_server.verify_server_context(
                res.endpoint, min_ctx=REQUIRED_LOCAL_CONTEXT
            )
            if not ctx_check.get("ok"):
                n_ctx = ctx_check.get("n_ctx") or 0
                res.status = "FAIL"
                res.n_ctx = ctx_check.get("n_ctx")
                res.reason = f"context {n_ctx} < {REQUIRED_LOCAL_CONTEXT}"
                return res, 1
            res.n_ctx = ctx_check.get("n_ctx")
        res.status = "PASS"
        res.reason = None
    elif new_reason and is_valid_failure_reason(new_reason):
        res.reason = new_reason
    return res, 1


def run_preflight(
    mode: Optional[str] = None,
    force: bool = False,
    scoped: bool = False,
    local_ports: Optional[Sequence[int]] = None,
    local_base_url: Optional[str] = None,
    light_base_url: Optional[str] = None,
    free_base_url: Optional[str] = None,
    check_balance: bool = True,
) -> PreflightReport:
    """Run Hydra launch preflight probes and return a structured PreflightReport."""
    global _LAST_REPORT
    load_dotenv()
    started = time.perf_counter()
    now = time.monotonic()

    active_mode = (mode or modes.current_mode() or "local").strip().lower()
    default_route = default_route_for_mode(active_mode)

    local_res = None if force else _cached_pass("local", now)
    light_res = None if force else _cached_pass("light", now)
    free_res = None if force else _cached_pass("free", now)

    need_local = local_res is None
    need_light = light_res is None

    if scoped:
        if active_mode == "local":
            need_light = False
        elif active_mode == "free":
            need_local = False
            need_light = False
        elif active_mode in ("light", "heavy", "max"):
            need_local = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        fut_local = (
            pool.submit(
                probe_local_route,
                ports=local_ports,
                base_url=local_base_url,
            )
            if need_local
            else None
        )
        fut_light = (
            pool.submit(
                probe_light_route,
                base_url=light_base_url,
                check_balance=check_balance,
            )
            if need_light
            else None
        )

        if fut_local is not None:
            local_res = fut_local.result()
            _store_cache(local_res, now)
        elif local_res is None:
            # Scoped switch skipped live probe; use previous cached entry if any or fast local check
            with _CACHE_LOCK:
                prev = _ROUTE_CACHE.get("local")
            local_res = prev[1] if prev else probe_local_route(ports=local_ports, base_url=local_base_url)
            if not prev:
                _store_cache(local_res, now)

        if fut_light is not None:
            light_res = fut_light.result()
            _store_cache(light_res, now)
        elif light_res is None:
            with _CACHE_LOCK:
                prev = _ROUTE_CACHE.get("light")
            if prev:
                light_res = prev[1]
            else:
                light_res = probe_light_route(
                    base_url=light_base_url,
                    check_balance=False,
                )
                _store_cache(light_res, now)

    assert local_res is not None and light_res is not None

    # Route 3 (FREE): Probed over the network ONLY if routes 1-2 both fail, or on explicit mode=free.
    should_probe_free_net = (
        active_mode == "free"
        or (not local_res.ok and not light_res.ok)
    )
    if free_res is None or (should_probe_free_net and free_res.status == "SKIPPED"):
        free_res = probe_free_route(
            base_url=free_base_url,
            should_network_probe=should_probe_free_net,
        )
        _store_cache(free_res, now)

    # Spec 5.3: Inconclusive 1-token completion probe ONLY on the session's default route
    completion_probes = 0
    if default_route == "local" and not local_res.ok and local_res.inconclusive_candidate:
        local_res, used = _maybe_run_inconclusive_probe(local_res)
        completion_probes += used
        _store_cache(local_res, now)
    elif default_route == "light" and not light_res.ok and light_res.inconclusive_candidate:
        light_res, used = _maybe_run_inconclusive_probe(light_res)
        completion_probes += used
        _store_cache(light_res, now)
    elif default_route == "free" and not free_res.ok and free_res.inconclusive_candidate:
        free_res, used = _maybe_run_inconclusive_probe(free_res)
        completion_probes += used
        _store_cache(free_res, now)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    total_gets = local_res.gets_count + light_res.gets_count + free_res.gets_count

    report = PreflightReport(
        mode=active_mode,
        default_route=default_route,
        local=local_res,
        light=light_res,
        free=free_res,
        elapsed_ms=elapsed_ms,
        gets_count=total_gets,
        completion_probes_count=completion_probes,
    )
    with _CACHE_LOCK:
        _LAST_REPORT = report
    return report


def reprobe_after_pre_token_failure(mode: Optional[str] = None) -> PreflightReport:
    """Re-run preflight automatically after any turn that fails before its first token."""
    invalidate_preflight_cache()
    return run_preflight(mode=mode, force=True)


def _format_ctx_k(n_ctx: Optional[int]) -> str:
    if not n_ctx or n_ctx <= 0:
        return ""
    if n_ctx % 1024 == 0:
        return f"{n_ctx // 1024}K"
    return str(n_ctx)


def format_route_strip(report: PreflightReport) -> str:
    """Render the one-line route status strip per PREFLIGHT-SPEC.md §5.5.

    Example:
      local ● gemma-e2b 128K · light ● spark · free ○ no key configured
    """
    # 1. local
    loc = report.local
    if loc.ok:
        ctx_lbl = _format_ctx_k(loc.n_ctx)
        loc_seg = f"local ● {loc.short_label} {ctx_lbl}".strip()
    else:
        loc_seg = f"local ○ {loc.reason or 'no server'}"

    # 2. light
    lgt = report.light
    if lgt.ok:
        lgt_seg = f"light ● {lgt.short_label}"
    else:
        lgt_seg = f"light ○ {lgt.reason or 'no key configured'}"

    # 3. free
    fre = report.free
    if fre.ok:
        fre_seg = f"free ● {fre.short_label}"
    elif fre.status == "SKIPPED":
        fre_seg = f"free ○ {fre.short_label} (standby)"
    else:
        fre_seg = f"free ○ {fre.reason or 'no key configured'}"

    return f"{loc_seg} · {lgt_seg} · {fre_seg}"


def format_preflight_notice(
    report: PreflightReport,
    mode: Optional[str] = None,
) -> Optional[str]:
    """Render the pre-turn notice line when the session's default route is DOWN at launch or mode switch."""
    active_mode = (mode or report.mode or modes.current_mode() or "local").strip().lower()

    if active_mode == "local":
        loc = report.local
        if loc.ok:
            return None
        reason = loc.reason or "no server"
        if reason == "no server":
            head = loc.down_message or (
                "local DOWN: no server on :8080 (managed server configured; start available)"
                if loc.managed_configured
                else "local DOWN: no server and none configured."
            )
        else:
            suffix = " (managed server configured; start available)" if loc.managed_configured else ""
            head = f"local DOWN: {reason}{suffix}"

        if loc.managed_configured:
            offer = (
                f"local is down: {reason} — managed server start available "
                f"(type /start or y to launch and warm {loc.model_id} on :8080; "
                "local mode never substitutes a cloud route)."
            )
        else:
            offer = f"local is down: {reason} — local mode never substitutes a cloud route."
        return f"{head}\n{offer}"

    if active_mode in ("light", "heavy", "max"):
        lgt = report.light
        if lgt.ok:
            return None
        reason = lgt.reason or "no key configured"
        return f"spark unreachable ({reason}). Answering on free (Nemotron) until spark returns."

    if active_mode == "free":
        fre = report.free
        if fre.ok:
            return None
        reason = fre.reason or "no key configured"
        return f"free route unreachable ({reason})."

    return None


def format_provenance_header(provenance: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Format requested-vs-answered-by provenance from LAST_COMPLETION_PROVENANCE."""
    from hydra_cli.providers import LAST_COMPLETION_PROVENANCE

    prov_data = provenance if provenance is not None else LAST_COMPLETION_PROVENANCE
    if not prov_data:
        return None
    requested = str(prov_data.get("requested_model") or "").strip()
    answered = str(prov_data.get("answered_by") or "").strip()
    provider = str(prov_data.get("provider") or "").strip()
    fallback = bool(prov_data.get("fallback"))
    if not answered:
        return None
    if fallback or (requested and answered != requested):
        via = f" ({provider})" if provider else ""
        return f"answered by {answered}{via} [fallback for {requested}]"
    via = f" ({provider})" if provider else ""
    return f"answered by {answered}{via}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    report = run_preflight(force=True)
    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_route_strip(report))
        notice = format_preflight_notice(report)
        if notice:
            print(notice)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
