"""
Hydra Desktop Sandbox Jail Filesystem Enforcer Subsystem.
Enforces strict path confinement, symlink resolution traps, directory escape blocking,
read-only root guarantees, and tamper-evident security violation audit logging.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union


class SecurityViolationType(str, Enum):
    """Categories of trapped sandbox boundary security violations."""
    PATH_ESCAPE = "path_escape"
    SYMLINK_TRAP = "symlink_trap"
    READ_ONLY_VIOLATION = "read_only_violation"
    NULL_BYTE_INJECTION = "null_byte_injection"
    OUT_OF_BOUNDS_ACCESS = "out_of_bounds_access"


class SandboxViolationError(PermissionError):
    """Exception raised upon trapped sandbox jail boundary breach attempt."""

    def __init__(
        self,
        violation_type: SecurityViolationType,
        attempted_path: str,
        jail_root: str,
        actor: str = "unknown",
        message: str = "",
    ) -> None:
        self.violation_type = violation_type
        self.attempted_path = attempted_path
        self.jail_root = jail_root
        self.actor = actor
        super().__init__(message or f"Sandbox boundary violation [{violation_type.value}]: {attempted_path}")


@dataclass
class SecurityAuditRecord:
    """Cryptographically chained audit ledger record of security event."""
    record_id: str
    violation_type: SecurityViolationType
    attempted_path: str
    resolved_path: str
    actor: str
    timestamp: float
    previous_hash: str
    record_hash: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize security record to dictionary representation."""
        return {
            "record_id": self.record_id,
            "violation_type": self.violation_type.value,
            "attempted_path": self.attempted_path,
            "resolved_path": self.resolved_path,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
            "details": dict(self.details),
        }


def compute_security_hash(
    violation_type: str,
    attempted_path: str,
    resolved_path: str,
    actor: str,
    timestamp: float,
    previous_hash: str,
    details: Dict[str, Any],
) -> str:
    """Compute hex SHA-256 digest of security audit log event."""
    hasher = hashlib.sha256()
    hasher.update(previous_hash.encode("utf-8"))
    prefix = f"{violation_type}:{attempted_path}:{resolved_path}:{actor}:{timestamp:.6f}:"
    hasher.update(prefix.encode("utf-8"))
    canonical_details = json.dumps(details, sort_keys=True, separators=(",", ":"))
    hasher.update(canonical_details.encode("utf-8"))
    return hasher.hexdigest()


class SandboxJailEnforcer:
    """
    Sandbox filesystem jail isolation enforcer.
    Validates all path requests against canonical jail boundaries, traps symlink escapes,
    blocks parent traversal maneuvers, guarantees read-only root restrictions, and logs audit events.
    """

    def __init__(
        self,
        jail_root: str,
        read_only_root: bool = False,
        writable_subpaths: Optional[Sequence[str]] = None,
        create_root_if_missing: bool = True,
    ) -> None:
        if create_root_if_missing:
            os.makedirs(os.path.abspath(jail_root), exist_ok=True)

        self.jail_root = os.path.realpath(os.path.abspath(jail_root))
        self.read_only_root = read_only_root
        self.writable_subpaths = [
            os.path.normpath(p).lstrip("/\\").replace(os.sep, "/")
            for p in (writable_subpaths or ["tmp", "workspace", "output"])
        ]

        self._audit_log: List[SecurityAuditRecord] = []
        self._last_audit_hash: str = "0" * 64
        self._lock = threading.RLock()

    def _append_violation_audit(
        self,
        violation_type: SecurityViolationType,
        attempted_path: str,
        resolved_path: str,
        actor: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> SecurityAuditRecord:
        """Append tamper-evident security breach event to hash chain."""
        now = time.time()
        rid = f"sec_{uuid.uuid4().hex[:12]}"
        d = dict(details) if details else {}
        h = compute_security_hash(
            violation_type.value,
            attempted_path,
            resolved_path,
            actor,
            now,
            self._last_audit_hash,
            d,
        )
        record = SecurityAuditRecord(
            record_id=rid,
            violation_type=violation_type,
            attempted_path=attempted_path,
            resolved_path=resolved_path,
            actor=actor,
            timestamp=now,
            previous_hash=self._last_audit_hash,
            record_hash=h,
            details=d,
        )
        self._audit_log.append(record)
        self._last_audit_hash = h
        return record

    def validate_and_resolve_path(
        self,
        user_path: str,
        for_write: bool = False,
        actor: str = "sandbox_worker",
    ) -> str:
        """
        Resolve and strictly validate user path against sandbox jail confinement invariants.
        Raises SandboxViolationError upon any escape, symlink redirection, or unauthorized write.
        """
        with self._lock:
            # Trap 1: Null byte injection
            if "\x00" in user_path:
                self._append_violation_audit(
                    SecurityViolationType.NULL_BYTE_INJECTION,
                    user_path,
                    "",
                    actor,
                    {"reason": "Null byte trapped in path string"},
                )
                raise SandboxViolationError(
                    SecurityViolationType.NULL_BYTE_INJECTION,
                    user_path,
                    self.jail_root,
                    actor,
                    "Null byte characters forbidden in path strings",
                )

            # Normalize path string
            clean_input = user_path.replace(os.sep, "/").strip()

            # Construct preliminary path
            if os.path.isabs(clean_input):
                candidate_path = os.path.abspath(clean_input)
            else:
                candidate_path = os.path.abspath(os.path.join(self.jail_root, clean_input))

            # Canonical path resolution
            resolved_jail = self.jail_root
            resolved_target = os.path.realpath(candidate_path)

            # Trap 2: Confinement containment check via commonpath
            try:
                common = os.path.commonpath([resolved_jail, resolved_target])
            except ValueError:
                # Different drives on Windows (e.g. C: vs D:)
                common = ""

            if common != resolved_jail:
                # Check whether escape occurred via symlink
                is_symlink = os.path.islink(candidate_path) or any(
                    os.path.islink(part) for part in self._get_path_ancestors(candidate_path)
                )
                v_type = SecurityViolationType.SYMLINK_TRAP if is_symlink else SecurityViolationType.PATH_ESCAPE

                self._append_violation_audit(
                    v_type,
                    user_path,
                    resolved_target,
                    actor,
                    {"candidate_path": candidate_path, "resolved_target": resolved_target},
                )
                raise SandboxViolationError(
                    v_type,
                    user_path,
                    self.jail_root,
                    actor,
                    f"Path escape attempt trapped: resolved destination outside sandbox root",
                )

            # Trap 3: Read-only root guarantees
            if for_write and self.read_only_root:
                rel_path = os.path.relpath(resolved_target, resolved_jail).replace(os.sep, "/")
                # Check if path falls inside declared writable subpaths
                permitted = False
                for w in self.writable_subpaths:
                    if rel_path == w or rel_path.startswith(w + "/"):
                        permitted = True
                        break

                if not permitted:
                    self._append_violation_audit(
                        SecurityViolationType.READ_ONLY_VIOLATION,
                        user_path,
                        resolved_target,
                        actor,
                        {"relative_path": rel_path, "writable_subpaths": self.writable_subpaths},
                    )
                    raise SandboxViolationError(
                        SecurityViolationType.READ_ONLY_VIOLATION,
                        user_path,
                        self.jail_root,
                        actor,
                        f"Write access denied: path outside designated writable subpaths in read-only root",
                    )

            return resolved_target

    def _get_path_ancestors(self, path: str) -> List[str]:
        """Collect component directory chain from path up to jail root."""
        ancestors: List[str] = []
        curr = os.path.dirname(path)
        while curr and curr != self.jail_root and os.path.dirname(curr) != curr:
            ancestors.append(curr)
            curr = os.path.dirname(curr)
        return ancestors

    def safe_read_text(self, path: str, actor: str = "reader") -> str:
        """Read text content with validated boundary confinement."""
        canonical = self.validate_and_resolve_path(path, for_write=False, actor=actor)
        with open(canonical, "r", encoding="utf-8") as f:
            return f.read()

    def safe_read_bytes(self, path: str, actor: str = "reader") -> bytes:
        """Read binary bytes with validated boundary confinement."""
        canonical = self.validate_and_resolve_path(path, for_write=False, actor=actor)
        with open(canonical, "rb") as f:
            return f.read()

    def safe_write_text(self, path: str, content: str, actor: str = "writer") -> int:
        """Write text content with validated write boundary permissions."""
        canonical = self.validate_and_resolve_path(path, for_write=True, actor=actor)
        os.makedirs(os.path.dirname(canonical), exist_ok=True)
        with open(canonical, "w", encoding="utf-8") as f:
            return f.write(content)

    def safe_write_bytes(self, path: str, content: bytes, actor: str = "writer") -> int:
        """Write binary payload with validated write boundary permissions."""
        canonical = self.validate_and_resolve_path(path, for_write=True, actor=actor)
        os.makedirs(os.path.dirname(canonical), exist_ok=True)
        with open(canonical, "wb") as f:
            return f.write(content)

    def safe_list_dir(self, path: str = "", actor: str = "lister") -> List[str]:
        """List directory entries within confined sandbox boundaries."""
        canonical = self.validate_and_resolve_path(path or ".", for_write=False, actor=actor)
        if not os.path.isdir(canonical):
            raise NotADirectoryError(f"Path is not a directory: {path}")
        return sorted(os.listdir(canonical))

    def safe_exists(self, path: str, actor: str = "checker") -> bool:
        """Check file existence within sandbox confinement."""
        canonical = self.validate_and_resolve_path(path, for_write=False, actor=actor)
        return os.path.exists(canonical)

    def safe_delete_file(self, path: str, actor: str = "cleaner") -> None:
        """Delete file inside writable boundary."""
        canonical = self.validate_and_resolve_path(path, for_write=True, actor=actor)
        if os.path.isdir(canonical):
            shutil.rmtree(canonical)
        elif os.path.exists(canonical):
            os.remove(canonical)

    def safe_mkdir(self, path: str, actor: str = "creator") -> str:
        """Create directory path inside writable boundary."""
        canonical = self.validate_and_resolve_path(path, for_write=True, actor=actor)
        os.makedirs(canonical, exist_ok=True)
        return canonical

    def get_audit_log(self) -> List[SecurityAuditRecord]:
        """Retrieve complete security violation audit ledger."""
        with self._lock:
            return list(self._audit_log)

    def verify_audit_log_integrity(self) -> Tuple[bool, List[str]]:
        """Verify cryptographic SHA-256 hash chaining across security audit log."""
        with self._lock:
            violations: List[str] = []
            expected_prev = "0" * 64

            for idx, entry in enumerate(self._audit_log):
                if entry.previous_hash != expected_prev:
                    violations.append(f"Security entry {idx} invalid previous hash link")
                h = compute_security_hash(
                    entry.violation_type.value,
                    entry.attempted_path,
                    entry.resolved_path,
                    entry.actor,
                    entry.timestamp,
                    entry.previous_hash,
                    entry.details,
                )
                if h != entry.record_hash:
                    violations.append(f"Security entry {idx} tampered record hash")
                expected_prev = entry.record_hash

            return len(violations) == 0, violations


_GLOBAL_SANDBOX_ENFORCER: Optional[SandboxJailEnforcer] = None
_GLOBAL_ENFORCER_LOCK = threading.RLock()


def get_sandbox_enforcer(jail_root: Optional[str] = None) -> SandboxJailEnforcer:
    """Acquire thread-safe singleton SandboxJailEnforcer."""
    global _GLOBAL_SANDBOX_ENFORCER
    with _GLOBAL_ENFORCER_LOCK:
        if _GLOBAL_SANDBOX_ENFORCER is None:
            root = jail_root or os.path.join(os.getcwd(), ".sandbox_jail")
            _GLOBAL_SANDBOX_ENFORCER = SandboxJailEnforcer(root)
        return _GLOBAL_SANDBOX_ENFORCER


def reset_sandbox_enforcer(jail_root: Optional[str] = None) -> SandboxJailEnforcer:
    """Reset singleton SandboxJailEnforcer."""
    global _GLOBAL_SANDBOX_ENFORCER
    with _GLOBAL_ENFORCER_LOCK:
        root = jail_root or os.path.join(os.getcwd(), ".sandbox_jail")
        _GLOBAL_SANDBOX_ENFORCER = SandboxJailEnforcer(root)
        return _GLOBAL_SANDBOX_ENFORCER
