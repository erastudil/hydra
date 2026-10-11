"""
Hydra Desktop Secure Credential Token Rotator Subsystem.
Provides cryptographic key lifecycle states, expiration alarms, zero-downtime
hot-swapping, key derivation versioning, and tamper-evident audit logging.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


class KeyLifecycleState(str, Enum):
    """Lifecycle states for managed cryptographic credential keys."""
    STAGED = "staged"
    ACTIVE = "active"
    RETIRING = "retiring"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class RotatedKeyRecord:
    """Versioned cryptographic key entry with lifecycle bounds."""
    key_id: str
    key_version: int
    secret_bytes: bytes
    state: KeyLifecycleState = KeyLifecycleState.STAGED
    created_at: float = field(default_factory=time.time)
    activated_at: Optional[float] = None
    retired_at: Optional[float] = None
    expires_at: float = 0.0
    grace_period_seconds: float = 3600.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = hashlib.sha256(self.secret_bytes).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize key record metadata excluding raw secret bytes."""
        return {
            "key_id": self.key_id,
            "key_version": self.key_version,
            "state": self.state.value,
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "retired_at": self.retired_at,
            "expires_at": self.expires_at,
            "grace_period_seconds": self.grace_period_seconds,
            "fingerprint": self.fingerprint,
            "metadata": dict(self.metadata),
        }


@dataclass
class TokenAlarm:
    """Expiration warning or breach signal for credential key."""
    alarm_id: str
    key_id: str
    key_version: int
    state: KeyLifecycleState
    remaining_ttl_seconds: float
    threshold_seconds: float
    severity: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize alarm structure to dictionary representation."""
        return {
            "alarm_id": self.alarm_id,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "state": self.state.value,
            "remaining_ttl_seconds": round(self.remaining_ttl_seconds, 2),
            "threshold_seconds": round(self.threshold_seconds, 2),
            "severity": self.severity,
            "timestamp": self.timestamp,
        }


@dataclass
class AuditLogEvent:
    """Tamper-evident audit ledger record with cryptographic hash chaining."""
    event_id: str
    event_type: str
    key_id: str
    key_version: int
    actor: str
    timestamp: float
    previous_hash: str
    event_hash: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize audit record to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
            "details": dict(self.details),
        }


def compute_audit_hash(
    event_type: str,
    key_id: str,
    key_version: int,
    actor: str,
    timestamp: float,
    previous_hash: str,
    details: Dict[str, Any],
) -> str:
    """Calculate deterministic SHA-256 digest of audit event payload."""
    hasher = hashlib.sha256()
    hasher.update(previous_hash.encode("utf-8"))
    payload = f"{event_type}:{key_id}:{key_version}:{actor}:{timestamp:.6f}:"
    hasher.update(payload.encode("utf-8"))
    canonical_details = json.dumps(details, sort_keys=True, separators=(",", ":"))
    hasher.update(canonical_details.encode("utf-8"))
    return hasher.hexdigest()


class SecureTokenRotator:
    """
    Cryptographic credential token rotator engine.
    Maintains versioned key lifecycle, provides seamless zero-downtime hot-swapping,
    verifies tokens across active and retiring grace periods, and generates tamper-evident audit logs.
    """

    def __init__(
        self,
        master_secret: Optional[bytes] = None,
        default_ttl_seconds: float = 86400.0,
        grace_period_seconds: float = 3600.0,
        warning_threshold_seconds: float = 7200.0,
    ) -> None:
        self.master_secret = master_secret or secrets.token_bytes(32)
        self.default_ttl_seconds = max(1.0, default_ttl_seconds)
        self.grace_period_seconds = max(0.0, grace_period_seconds)
        self.warning_threshold_seconds = max(0.0, warning_threshold_seconds)

        self._keys: Dict[str, RotatedKeyRecord] = {}
        self._keys_by_version: Dict[int, RotatedKeyRecord] = {}
        self._active_key_id: Optional[str] = None
        self._next_version: int = 1
        self._audit_log: List[AuditLogEvent] = []
        self._last_audit_hash: str = "0" * 64
        self._lock = threading.RLock()

        # Seed initial active key
        self.rotate_key(actor="genesis_system")

    def _append_audit(
        self,
        event_type: str,
        key_id: str,
        key_version: int,
        actor: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditLogEvent:
        """Append immutable audit entry to cryptographic hash chain."""
        d = dict(details) if details else {}
        now = time.time()
        eid = f"evt_{uuid.uuid4().hex[:12]}"
        h = compute_audit_hash(
            event_type, key_id, key_version, actor, now, self._last_audit_hash, d
        )
        entry = AuditLogEvent(
            event_id=eid,
            event_type=event_type,
            key_id=key_id,
            key_version=key_version,
            actor=actor,
            timestamp=now,
            previous_hash=self._last_audit_hash,
            event_hash=h,
            details=d,
        )
        self._audit_log.append(entry)
        self._last_audit_hash = h
        return entry

    def derive_versioned_key(self, version: int, purpose: str = "token_auth") -> bytes:
        """Derive deterministic subkey for specific version via HMAC-SHA256."""
        info = f"{purpose}:version_{version}".encode("utf-8")
        return hmac.new(self.master_secret, info, hashlib.sha256).digest()

    def generate_key(
        self,
        version: Optional[int] = None,
        ttl_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        actor: str = "rotator_service",
    ) -> RotatedKeyRecord:
        """Generate new versioned credential key in STAGED lifecycle state."""
        with self._lock:
            ver = version if version is not None else self._next_version
            self._next_version = max(self._next_version, ver + 1)

            kid = f"key_v{ver}_{uuid.uuid4().hex[:8]}"
            secret = self.derive_versioned_key(ver)
            ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
            now = time.time()

            key = RotatedKeyRecord(
                key_id=kid,
                key_version=ver,
                secret_bytes=secret,
                state=KeyLifecycleState.STAGED,
                created_at=now,
                expires_at=now + ttl,
                grace_period_seconds=self.grace_period_seconds,
                metadata=dict(metadata) if metadata else {},
            )

            self._keys[kid] = key
            self._keys_by_version[ver] = key

            self._append_audit(
                "KEY_GENERATED", kid, ver, actor, {"ttl_seconds": ttl, "fingerprint": key.fingerprint}
            )
            return key

    def promote_staged_key(
        self,
        key_id: str,
        actor: str = "rotator_service",
    ) -> RotatedKeyRecord:
        """Promote STAGED key to ACTIVE state with atomic zero-downtime hot swap."""
        with self._lock:
            if key_id not in self._keys:
                raise KeyError(f"Key {key_id} not found")

            staged_key = self._keys[key_id]
            if staged_key.state != KeyLifecycleState.STAGED:
                raise ValueError(f"Key {key_id} state is {staged_key.state}, expected STAGED")

            now = time.time()
            prev_active_id = self._active_key_id

            if prev_active_id and prev_active_id in self._keys:
                old_key = self._keys[prev_active_id]
                old_key.state = KeyLifecycleState.RETIRING
                old_key.retired_at = now
                old_key.expires_at = now + old_key.grace_period_seconds
                self._append_audit(
                    "KEY_RETIRING",
                    old_key.key_id,
                    old_key.key_version,
                    actor,
                    {"retired_at": now, "grace_period": old_key.grace_period_seconds},
                )

            staged_key.state = KeyLifecycleState.ACTIVE
            staged_key.activated_at = now
            self._active_key_id = staged_key.key_id

            self._append_audit(
                "KEY_PROMOTED",
                staged_key.key_id,
                staged_key.key_version,
                actor,
                {"activated_at": now, "superseded_key_id": prev_active_id},
            )
            return staged_key

    def rotate_key(
        self,
        ttl_seconds: Optional[float] = None,
        actor: str = "rotator_service",
    ) -> Tuple[RotatedKeyRecord, Optional[RotatedKeyRecord]]:
        """Generate next key version and promote atomically in single operation."""
        with self._lock:
            old_active_id = self._active_key_id
            old_active = self._keys.get(old_active_id) if old_active_id else None

            staged = self.generate_key(ttl_seconds=ttl_seconds, actor=actor)
            promoted = self.promote_staged_key(staged.key_id, actor=actor)
            return promoted, old_active

    def revoke_key(
        self,
        key_id: str,
        actor: str = "security_admin",
        reason: str = "manual_revocation",
    ) -> RotatedKeyRecord:
        """Revoke credential key immediately, barring subsequent signature verification."""
        with self._lock:
            if key_id not in self._keys:
                raise KeyError(f"Key {key_id} not found")

            key = self._keys[key_id]
            key.state = KeyLifecycleState.REVOKED
            if self._active_key_id == key_id:
                self._active_key_id = None

            self._append_audit(
                "KEY_REVOKED", key.key_id, key.key_version, actor, {"reason": reason}
            )
            return key

    def get_active_key(self) -> Optional[RotatedKeyRecord]:
        """Retrieve currently active credential key record."""
        with self._lock:
            if not self._active_key_id or self._active_key_id not in self._keys:
                return None
            key = self._keys[self._active_key_id]
            return key if key.state == KeyLifecycleState.ACTIVE else None

    def get_key(self, key_id: str) -> Optional[RotatedKeyRecord]:
        """Retrieve key record by unique identifier."""
        with self._lock:
            return self._keys.get(key_id)

    def list_keys(self, state: Optional[KeyLifecycleState] = None) -> List[RotatedKeyRecord]:
        """List credential key records with optional lifecycle state filter."""
        with self._lock:
            if state is None:
                return list(self._keys.values())
            return [k for k in self._keys.values() if k.state == state]

    def create_token_signature(
        self,
        payload: str,
        actor: str = "orchestrator",
    ) -> str:
        """Generate signed token string using currently active credential key."""
        with self._lock:
            active = self.get_active_key()
            if not active:
                raise RuntimeError("Cannot sign token: zero active credential keys present")

            signature = hmac.new(
                active.secret_bytes, payload.encode("utf-8"), hashlib.sha256
            ).hexdigest()

            token = f"v{active.key_version}.{active.key_id}.{signature}"
            self._append_audit(
                "TOKEN_SIGNED",
                active.key_id,
                active.key_version,
                actor,
                {"payload_len": len(payload)},
            )
            return token

    def verify_token_signature(
        self,
        payload: str,
        token_str: str,
        actor: str = "verifier",
    ) -> bool:
        """Verify token signature against ACTIVE or RETIRING keys in grace period."""
        with self._lock:
            parts = token_str.split(".", 2)
            if len(parts) != 3:
                return False

            ver_str, kid, sig = parts
            if kid not in self._keys:
                return False

            key = self._keys[kid]
            now = time.time()

            # Lifecycle checks
            if key.state == KeyLifecycleState.REVOKED:
                return False
            if key.state == KeyLifecycleState.EXPIRED:
                return False
            if key.state == KeyLifecycleState.RETIRING and now > key.expires_at:
                key.state = KeyLifecycleState.EXPIRED
                return False

            expected_sig = hmac.new(
                key.secret_bytes, payload.encode("utf-8"), hashlib.sha256
            ).hexdigest()

            valid = hmac.compare_digest(sig, expected_sig)
            event_type = "TOKEN_VERIFIED" if valid else "TOKEN_VERIFICATION_FAILED"
            self._append_audit(
                event_type, key.key_id, key.key_version, actor, {"valid": valid}
            )
            return valid

    def check_expirations(self) -> List[TokenAlarm]:
        """Inspect key records, transition expired keys, and generate alarms."""
        with self._lock:
            now = time.time()
            alarms: List[TokenAlarm] = []

            for key in self._keys.values():
                if key.state in (KeyLifecycleState.EXPIRED, KeyLifecycleState.REVOKED):
                    continue

                remaining = key.expires_at - now

                if key.state == KeyLifecycleState.RETIRING and remaining <= 0:
                    key.state = KeyLifecycleState.EXPIRED
                    self._append_audit(
                        "KEY_EXPIRED",
                        key.key_id,
                        key.key_version,
                        "expiration_daemon",
                        {"expired_at": now},
                    )
                    alarm = TokenAlarm(
                        alarm_id=f"alm_{uuid.uuid4().hex[:8]}",
                        key_id=key.key_id,
                        key_version=key.key_version,
                        state=KeyLifecycleState.EXPIRED,
                        remaining_ttl_seconds=0.0,
                        threshold_seconds=0.0,
                        severity="CRITICAL",
                        timestamp=now,
                    )
                    alarms.append(alarm)
                elif key.state == KeyLifecycleState.ACTIVE and remaining <= self.warning_threshold_seconds:
                    alarm = TokenAlarm(
                        alarm_id=f"alm_{uuid.uuid4().hex[:8]}",
                        key_id=key.key_id,
                        key_version=key.key_version,
                        state=KeyLifecycleState.ACTIVE,
                        remaining_ttl_seconds=max(0.0, remaining),
                        threshold_seconds=self.warning_threshold_seconds,
                        severity="WARNING" if remaining > 0 else "CRITICAL",
                        timestamp=now,
                    )
                    alarms.append(alarm)

            return alarms

    def get_audit_log(self) -> List[AuditLogEvent]:
        """Retrieve complete cryptographic audit log ledger."""
        with self._lock:
            return list(self._audit_log)

    def verify_audit_log_integrity(self) -> Tuple[bool, List[str]]:
        """Verify unbroken SHA-256 hash chaining across entire audit log."""
        with self._lock:
            violations: List[str] = []
            expected_prev = "0" * 64

            for idx, entry in enumerate(self._audit_log):
                if entry.previous_hash != expected_prev:
                    violations.append(
                        f"Entry {idx} ({entry.event_id}) invalid previous hash link"
                    )
                calculated = compute_audit_hash(
                    entry.event_type,
                    entry.key_id,
                    entry.key_version,
                    entry.actor,
                    entry.timestamp,
                    entry.previous_hash,
                    entry.details,
                )
                if calculated != entry.event_hash:
                    violations.append(
                        f"Entry {idx} ({entry.event_id}) tampered event hash"
                    )
                expected_prev = entry.event_hash

            return (len(violations) == 0, violations)


_GLOBAL_TOKEN_ROTATOR: Optional[SecureTokenRotator] = None
_GLOBAL_ROTATOR_LOCK = threading.RLock()


def get_token_rotator() -> SecureTokenRotator:
    """Acquire thread-safe singleton SecureTokenRotator."""
    global _GLOBAL_TOKEN_ROTATOR
    with _GLOBAL_ROTATOR_LOCK:
        if _GLOBAL_TOKEN_ROTATOR is None:
            _GLOBAL_TOKEN_ROTATOR = SecureTokenRotator()
        return _GLOBAL_TOKEN_ROTATOR


def reset_token_rotator() -> SecureTokenRotator:
    """Reset singleton SecureTokenRotator."""
    global _GLOBAL_TOKEN_ROTATOR
    with _GLOBAL_ROTATOR_LOCK:
        _GLOBAL_TOKEN_ROTATOR = SecureTokenRotator()
        return _GLOBAL_TOKEN_ROTATOR
