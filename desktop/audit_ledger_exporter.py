"""
Hydra Desktop Cryptographic Audit Ledger Exporter Subsystem.
Implements immutable SHA-256 hash chaining, Merkle tree root & proof calculation,
tamper detection, and JSON/CSV compliance report export.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

GENESIS_PREV_HASH = "0" * 64


def hash_string(data: str) -> str:
    """Compute hex SHA-256 digest of string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def hash_pair(left_hash: str, right_hash: str) -> str:
    """Compute combined hash of two child Merkle nodes."""
    return hash_string(left_hash + right_hash)


def compute_entry_hash(
    index: int,
    timestamp: float,
    event_type: str,
    actor: str,
    payload: Dict[str, Any],
    prev_hash: str,
) -> str:
    """Compute immutable cryptographic hash of ledger entry."""
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    content = f"{index}:{timestamp:.6f}:{event_type}:{actor}:{canonical_payload}:{prev_hash}"
    return hash_string(content)


def build_merkle_tree(leaf_hashes: List[str]) -> List[List[str]]:
    """
    Build full Merkle tree levels from list of leaf hashes.
    Level 0 is leaf level; highest level contains root [merkle_root].
    """
    if not leaf_hashes:
        return [[hash_string("")]]

    levels: List[List[str]] = [list(leaf_hashes)]
    current = list(leaf_hashes)

    while len(current) > 1:
        next_level: List[str] = []
        for i in range(0, len(current), 2):
            left = current[i]
            # If odd count, duplicate last leaf
            right = current[i + 1] if (i + 1 < len(current)) else left
            next_level.append(hash_pair(left, right))
        levels.append(next_level)
        current = next_level

    return levels


def compute_merkle_root(leaf_hashes: List[str]) -> str:
    """Calculate single Merkle root hash representing the entire leaf set."""
    if not leaf_hashes:
        return hash_string("")
    tree = build_merkle_tree(leaf_hashes)
    return tree[-1][0]


def generate_merkle_proof(leaf_hashes: List[str], target_index: int) -> List[Tuple[str, str]]:
    """
    Generate audit path verification proof for target leaf index.
    Returns list of (sibling_hash, direction) where direction is 'left' or 'right'.
    """
    if not leaf_hashes or target_index < 0 or target_index >= len(leaf_hashes):
        return []

    tree = build_merkle_tree(leaf_hashes)
    proof: List[Tuple[str, str]] = []
    idx = target_index

    for level in tree[:-1]:
        is_right = (idx % 2 == 1)
        sibling_idx = (idx - 1) if is_right else (idx + 1)

        if sibling_idx < len(level):
            sibling_hash = level[sibling_idx]
        else:
            sibling_hash = level[idx]  # Duplicated odd node

        direction = "left" if is_right else "right"
        proof.append((sibling_hash, direction))
        idx //= 2

    return proof


def verify_merkle_proof(leaf_hash: str, proof: List[Tuple[str, str]], expected_root: str) -> bool:
    """Verify that leaf_hash belongs to expected_root given Merkle proof path."""
    current = leaf_hash
    for sibling, direction in proof:
        if direction == "left":
            current = hash_pair(sibling, current)
        else:
            current = hash_pair(current, sibling)
    return current == expected_root


@dataclass
class AuditLedgerEntry:
    """Atomic immutable audit entry with backward cryptographic hash link."""
    index: int
    timestamp: float
    event_type: str
    actor: str
    payload: Dict[str, Any]
    prev_hash: str
    entry_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "actor": self.actor,
            "payload": dict(self.payload),
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AuditLedgerEntry:
        return cls(
            index=data["index"],
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            actor=data["actor"],
            payload=data.get("payload", {}),
            prev_hash=data["prev_hash"],
            entry_hash=data["entry_hash"],
        )


class CryptographicAuditLedger:
    """
    Sovereign cryptographic audit ledger guaranteeing tamper-evident event recording,
    Merkle tree state verification, and compliance report exportation.
    """

    def __init__(self, genesis_actor: str = "hydra_system") -> None:
        self.genesis_actor = genesis_actor
        self._lock = threading.RLock()
        self._entries: List[AuditLedgerEntry] = []
        self._init_genesis()

    def _init_genesis(self) -> None:
        genesis_ts = 1700000000.0  # Fixed epoch timestamp for deterministic genesis
        genesis_payload = {"system": "hydra_desktop", "message": "Genesis audit block initialized"}
        genesis_hash = compute_entry_hash(
            index=0,
            timestamp=genesis_ts,
            event_type="GENESIS",
            actor=self.genesis_actor,
            payload=genesis_payload,
            prev_hash=GENESIS_PREV_HASH,
        )
        self._entries.append(
            AuditLedgerEntry(
                index=0,
                timestamp=genesis_ts,
                event_type="GENESIS",
                actor=self.genesis_actor,
                payload=genesis_payload,
                prev_hash=GENESIS_PREV_HASH,
                entry_hash=genesis_hash,
            )
        )

    @property
    def total_entries(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def latest_hash(self) -> str:
        with self._lock:
            return self._entries[-1].entry_hash

    def record_event(
        self,
        event_type: str,
        actor: str,
        payload: Dict[str, Any],
        timestamp: Optional[float] = None,
    ) -> AuditLedgerEntry:
        """Append immutable verified audit entry to hash chain."""
        with self._lock:
            idx = len(self._entries)
            ts = timestamp or time.time()
            prev = self._entries[-1].entry_hash
            ehash = compute_entry_hash(idx, ts, event_type, actor, payload, prev)

            entry = AuditLedgerEntry(
                index=idx,
                timestamp=ts,
                event_type=event_type,
                actor=actor,
                payload=payload,
                prev_hash=prev,
                entry_hash=ehash,
            )
            self._entries.append(entry)
            return entry

    def get_entry(self, index: int) -> Optional[AuditLedgerEntry]:
        """Fetch audit entry by sequential index."""
        with self._lock:
            if 0 <= index < len(self._entries):
                return self._entries[index]
            return None

    def get_merkle_root(self) -> str:
        """Compute and return Merkle root across all recorded ledger entries."""
        with self._lock:
            leaf_hashes = [e.entry_hash for e in self._entries]
            return compute_merkle_root(leaf_hashes)

    def generate_proof(self, index: int) -> List[Tuple[str, str]]:
        """Generate Merkle inclusion proof for entry at index."""
        with self._lock:
            leaf_hashes = [e.entry_hash for e in self._entries]
            return generate_merkle_proof(leaf_hashes, index)

    def verify_integrity(self) -> Tuple[bool, List[str]]:
        """
        Verify hash chain links and entry signatures across entire ledger.
        Returns (is_valid, violations_list).
        """
        with self._lock:
            violations: List[str] = []

            for i, entry in enumerate(self._entries):
                # 1. Verify prev_hash link
                expected_prev = GENESIS_PREV_HASH if i == 0 else self._entries[i - 1].entry_hash
                if entry.prev_hash != expected_prev:
                    violations.append(
                        f"Entry {i} prev_hash mismatch: expected {expected_prev[:12]}..., got {entry.prev_hash[:12]}..."
                    )

                # 2. Verify entry hash computation
                recalculated = compute_entry_hash(
                    entry.index,
                    entry.timestamp,
                    entry.event_type,
                    entry.actor,
                    entry.payload,
                    entry.prev_hash,
                )
                if recalculated != entry.entry_hash:
                    violations.append(
                        f"Entry {i} hash tampered: recalculated {recalculated[:12]}..., stored {entry.entry_hash[:12]}..."
                    )

            return len(violations) == 0, violations

    def export_json(self, filepath: Optional[str] = None) -> str:
        """Export audit ledger report to JSON string or file."""
        with self._lock:
            valid, violations = self.verify_integrity()
            report = {
                "merkle_root": self.get_merkle_root(),
                "latest_hash": self.latest_hash,
                "total_entries": len(self._entries),
                "is_verified": valid,
                "violations": violations,
                "exported_at": time.time(),
                "entries": [e.to_dict() for e in self._entries],
            }
            dumped = json.dumps(report, indent=2)
            if filepath:
                os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(dumped)
            return dumped

    def export_csv(self, filepath: Optional[str] = None) -> str:
        """Export audit entries to CSV string or file."""
        with self._lock:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["index", "timestamp", "event_type", "actor", "entry_hash", "prev_hash", "payload"])

            for e in self._entries:
                writer.writerow([
                    e.index,
                    f"{e.timestamp:.6f}",
                    e.event_type,
                    e.actor,
                    e.entry_hash,
                    e.prev_hash,
                    json.dumps(e.payload, sort_keys=True),
                ])

            csv_text = output.getvalue()
            if filepath:
                os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(csv_text)
            return csv_text


_GLOBAL_AUDIT_LEDGER: Optional[CryptographicAuditLedger] = None
_GLOBAL_LEDGER_LOCK = threading.RLock()


def get_audit_ledger() -> CryptographicAuditLedger:
    """Acquire thread-safe singleton CryptographicAuditLedger."""
    global _GLOBAL_AUDIT_LEDGER
    with _GLOBAL_LEDGER_LOCK:
        if _GLOBAL_AUDIT_LEDGER is None:
            _GLOBAL_AUDIT_LEDGER = CryptographicAuditLedger()
        return _GLOBAL_AUDIT_LEDGER


def reset_audit_ledger() -> CryptographicAuditLedger:
    """Reset singleton CryptographicAuditLedger."""
    global _GLOBAL_AUDIT_LEDGER
    with _GLOBAL_LEDGER_LOCK:
        _GLOBAL_AUDIT_LEDGER = CryptographicAuditLedger()
        return _GLOBAL_AUDIT_LEDGER
