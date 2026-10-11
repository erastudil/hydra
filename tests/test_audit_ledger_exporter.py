"""
Integration test suite for Hydra Desktop Cryptographic Audit Ledger Exporter.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import pytest

from desktop.audit_ledger_exporter import (
    CryptographicAuditLedger,
    AuditLedgerEntry,
    compute_entry_hash,
    compute_merkle_root,
    generate_merkle_proof,
    verify_merkle_proof,
    get_audit_ledger,
    reset_audit_ledger,
)


@pytest.fixture
def ledger():
    ldg = CryptographicAuditLedger(genesis_actor="test_system")
    yield ldg


def test_genesis_block_initialization(ledger: CryptographicAuditLedger):
    """Verify ledger initializes with deterministic genesis entry at index 0."""
    assert ledger.total_entries == 1
    genesis = ledger.get_entry(0)
    assert genesis is not None
    assert genesis.index == 0
    assert genesis.event_type == "GENESIS"
    assert genesis.prev_hash == "0" * 64
    assert len(genesis.entry_hash) == 64


def test_immutable_hash_chaining(ledger: CryptographicAuditLedger):
    """Verify append operations maintain backward cryptographic hash links."""
    e1 = ledger.record_event("MODEL_SUMMON", "agent_coder", {"model": "sonnet-5.5", "tokens": 150})
    e2 = ledger.record_event("TOOL_EXECUTION", "agent_auditor", {"tool": "run_command", "exit": 0})

    assert ledger.total_entries == 3
    assert e1.index == 1
    assert e1.prev_hash == ledger.get_entry(0).entry_hash
    assert e2.index == 2
    assert e2.prev_hash == e1.entry_hash


def test_merkle_tree_proof_generation_and_verification(ledger: CryptographicAuditLedger):
    """Verify Merkle root calculation and leaf inclusion proof validation."""
    for i in range(5):
        ledger.record_event("ACTION_EVENT", "user", {"step": i})

    root = ledger.get_merkle_root()
    assert len(root) == 64

    # Verify inclusion proof for entry index 2
    target_entry = ledger.get_entry(2)
    proof = ledger.generate_proof(2)
    assert len(proof) > 0

    assert verify_merkle_proof(target_entry.entry_hash, proof, root) is True

    # Tampered leaf hash fails verification
    tampered_leaf = "f" * 64
    assert verify_merkle_proof(tampered_leaf, proof, root) is False


def test_tamper_detection_flags_corrupted_payload(ledger: CryptographicAuditLedger):
    """Verify tamper detection spots altered payload data in hash chain."""
    e = ledger.record_event("STATE_MUTATION", "agent_runner", {"status": "PASS"})
    valid, violations = ledger.verify_integrity()
    assert valid is True
    assert len(violations) == 0

    # Simulate in-memory tampering
    e.payload["status"] = "FAIL_TAMPERED"

    tampered_valid, tampered_violations = ledger.verify_integrity()
    assert tampered_valid is False
    assert len(tampered_violations) >= 1
    assert "tampered" in tampered_violations[0].lower()


def test_json_and_csv_export(ledger: CryptographicAuditLedger):
    """Verify JSON and CSV compliance reports export clean records."""
    ledger.record_event("AUDIT_CHECK", "auditor", {"rule": "P018", "passed": True})

    # JSON export
    json_text = ledger.export_json()
    parsed = json.loads(json_text)
    assert "merkle_root" in parsed
    assert parsed["is_verified"] is True
    assert len(parsed["entries"]) == ledger.total_entries

    # CSV export
    csv_text = ledger.export_csv()
    reader = csv.reader(csv_text.strip().splitlines())
    rows = list(reader)
    assert rows[0] == ["index", "timestamp", "event_type", "actor", "entry_hash", "prev_hash", "payload"]
    assert len(rows) == ledger.total_entries + 1


def test_global_singleton_ledger():
    """Verify singleton lifecycle for CryptographicAuditLedger."""
    l1 = get_audit_ledger()
    l2 = get_audit_ledger()
    assert l1 is l2

    l3 = reset_audit_ledger()
    assert l3 is not l1
    assert get_audit_ledger() is l3
