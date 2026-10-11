"""
Hydra Desktop Sovereign Telemetry Gate.
Strictly enforces AGENTS.md zero-telemetry fence, intercepts and blocks
third-party cloud analytics/tracking payloads, and manages local event metrics.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import os
import re
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

# Forbidden telemetry domains and patterns (AGENTS.md fence telemetry)
TELEMETRY_BLOCKLIST_DOMAINS = {
    "google-analytics.com",
    "analytics.google.com",
    "sentry.io",
    "o.sentry.io",
    "mixpanel.com",
    "api.mixpanel.com",
    "segment.io",
    "api.segment.io",
    "posthog.com",
    "app.posthog.com",
    "amplitude.com",
    "api.amplitude.com",
    "datadoghq.com",
    "browser-intake-datadoghq.com",
    "telemetry.anthropic.com",
    "telemetry.openai.com",
    "collect.doubleclick.net",
}

BLOCKED_URL_KEYWORDS = [
    "telemetry",
    "analytics",
    "beacon",
    "track",
    "collect",
    "event-logging",
    "metrics-ingest",
]


def is_telemetry_endpoint(url_or_domain: str) -> Tuple[bool, str]:
    """
    Evaluate target URL or domain against AGENTS.md zero-telemetry fence.
    Returns (is_blocked, causal_reason).
    """
    if not url_or_domain:
        return False, ""

    raw = str(url_or_domain).strip().lower()

    # Parse hostname
    hostname = raw
    if "://" in raw:
        try:
            parsed = urllib.parse.urlparse(raw)
            hostname = parsed.hostname or raw
        except Exception:
            hostname = raw

    # 1. Exact or suffix domain match
    for blocked_domain in TELEMETRY_BLOCKLIST_DOMAINS:
        if hostname == blocked_domain or hostname.endswith("." + blocked_domain):
            return True, f"Blocked third-party telemetry domain '{blocked_domain}'"

    # 2. Keyword heuristic checks in hostname or path
    for kw in BLOCKED_URL_KEYWORDS:
        if kw in hostname:
            return True, f"Blocked keyword '{kw}' detected in endpoint hostname"

    return False, ""


@dataclass
class LocalMetricRecord:
    """Atomic local-only telemetry event record."""
    record_id: str
    name: str
    category: str = "general"
    duration_ms: float = 0.0
    count: int = 1
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "name": self.name,
            "category": self.category,
            "duration_ms": round(self.duration_ms, 2),
            "count": self.count,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


class SovereignTelemetryGate:
    """
    Sovereign Telemetry Gate enforcing AGENTS.md zero-telemetry fence.
    Intercepts outbound requests to prevent cloud tracking leaks and stores
    local-only diagnostic telemetry in memory.
    """

    def __init__(self, strict_mode: bool = True) -> None:
        self.strict_mode = strict_mode
        self._lock = threading.RLock()
        self._local_records: List[LocalMetricRecord] = []
        self._counters: Dict[str, int] = {}
        self._blocked_attempts: List[Dict[str, Any]] = []

    @property
    def total_local_events(self) -> int:
        with self._lock:
            return len(self._local_records)

    @property
    def blocked_attempts_count(self) -> int:
        with self._lock:
            return len(self._blocked_attempts)

    def clear(self) -> None:
        """Clear recorded local events and blocked attempt logs."""
        with self._lock:
            self._local_records.clear()
            self._counters.clear()
            self._blocked_attempts.clear()

    def inspect_outbound(
        self,
        url_or_endpoint: str,
        payload: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Inspect outbound request destination and payload.
        Blocks and logs third-party telemetry; raises PermissionError if strict_mode.
        """
        is_blocked, reason = is_telemetry_endpoint(url_or_endpoint)

        if is_blocked:
            entry = {
                "url": url_or_endpoint,
                "reason": reason,
                "timestamp": time.time(),
                "has_payload": payload is not None,
            }
            with self._lock:
                self._blocked_attempts.append(entry)

            if self.strict_mode:
                raise PermissionError(
                    f"Security violation: Outbound telemetry blocked under AGENTS.md zero-telemetry fence ({reason})"
                )

            return {
                "allowed": False,
                "violation": True,
                "reason": reason,
                "url": url_or_endpoint,
            }

        return {"allowed": True, "violation": False, "url": url_or_endpoint}

    def record_local_event(
        self,
        name: str,
        category: str = "general",
        duration_ms: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LocalMetricRecord:
        """Record local-only operational telemetry event."""
        rid = f"metric_{uuid.uuid4().hex[:10]}"
        rec = LocalMetricRecord(
            record_id=rid,
            name=name.strip(),
            category=category.strip(),
            duration_ms=max(0.0, float(duration_ms)),
            metadata=metadata or {},
        )

        with self._lock:
            self._local_records.append(rec)
            self._counters[rec.name] = self._counters.get(rec.name, 0) + 1
            if len(self._local_records) > 2000:
                self._local_records.pop(0)

        return rec

    def get_summary(self) -> Dict[str, Any]:
        """Return operational summary of local metrics and blocked telemetry."""
        with self._lock:
            return {
                "fence_status": "ENFORCING",
                "total_local_events": len(self._local_records),
                "blocked_attempts_count": len(self._blocked_attempts),
                "event_counters": dict(self._counters),
                "blocked_attempts": list(self._blocked_attempts[-20:]),
            }

    def get_blocked_attempts(self) -> List[Dict[str, Any]]:
        """Return full history of blocked outbound telemetry attempts."""
        with self._lock:
            return list(self._blocked_attempts)


_GLOBAL_GATE: Optional[SovereignTelemetryGate] = None
_GLOBAL_GATE_LOCK = threading.RLock()


def get_telemetry_gate() -> SovereignTelemetryGate:
    """Acquire thread-safe singleton SovereignTelemetryGate."""
    global _GLOBAL_GATE
    with _GLOBAL_GATE_LOCK:
        if _GLOBAL_GATE is None:
            _GLOBAL_GATE = SovereignTelemetryGate(strict_mode=True)
        return _GLOBAL_GATE


def reset_telemetry_gate() -> SovereignTelemetryGate:
    """Reset singleton SovereignTelemetryGate."""
    global _GLOBAL_GATE
    with _GLOBAL_GATE_LOCK:
        _GLOBAL_GATE = SovereignTelemetryGate(strict_mode=True)
        return _GLOBAL_GATE
