"""
Unit tests for Hydra Desktop Sandbox Jail Filesystem Enforcer Subsystem.
Verifies path confinement, symlink resolution traps, directory escape blocking,
read-only root guarantees, and security violation audit logging.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import os
import tempfile
import pytest
from desktop.sandbox_jail_enforcer import (
    SandboxJailEnforcer,
    SandboxViolationError,
    SecurityAuditRecord,
    SecurityViolationType,
    get_sandbox_enforcer,
    reset_sandbox_enforcer,
)


def test_confined_file_operations():
    with tempfile.TemporaryDirectory() as tmp_root:
        enforcer = SandboxJailEnforcer(jail_root=tmp_root)

        # Write text
        bytes_written = enforcer.safe_write_text("hello.txt", "hello sovereign sandbox")
        assert bytes_written > 0
        assert enforcer.safe_exists("hello.txt") is True

        # Read text
        content = enforcer.safe_read_text("hello.txt")
        assert content == "hello sovereign sandbox"

        # List directory
        entries = enforcer.safe_list_dir()
        assert "hello.txt" in entries

        # Subdirectory creation and nested file
        nested_dir = enforcer.safe_mkdir("nested/sub")
        assert os.path.isdir(nested_dir)
        enforcer.safe_write_text("nested/sub/data.json", '{"status": "ok"}')
        assert enforcer.safe_exists("nested/sub/data.json") is True

        # Delete file
        enforcer.safe_delete_file("hello.txt")
        assert enforcer.safe_exists("hello.txt") is False


def test_path_escape_trapping():
    with tempfile.TemporaryDirectory() as tmp_root:
        enforcer = SandboxJailEnforcer(jail_root=tmp_root)

        # Parent traversal attempt
        with pytest.raises(SandboxViolationError) as exc_info:
            enforcer.safe_read_text("../outside.txt")
        assert exc_info.value.violation_type in (
            SecurityViolationType.PATH_ESCAPE,
            SecurityViolationType.SYMLINK_TRAP,
        )

        # Deep parent traversal attempt
        with pytest.raises(SandboxViolationError):
            enforcer.safe_write_text("../../../etc/passwd", "payload")

        # Absolute path outside jail
        outside_path = os.path.abspath(os.path.join(tmp_root, "..", "outside_marker.txt"))
        with pytest.raises(SandboxViolationError):
            enforcer.safe_write_text(outside_path, "payload")


def test_null_byte_injection_blocking():
    with tempfile.TemporaryDirectory() as tmp_root:
        enforcer = SandboxJailEnforcer(jail_root=tmp_root)

        with pytest.raises(SandboxViolationError) as exc_info:
            enforcer.safe_read_text("allowed.txt\x00.exe")
        assert exc_info.value.violation_type == SecurityViolationType.NULL_BYTE_INJECTION


def test_read_only_root_guarantees():
    with tempfile.TemporaryDirectory() as tmp_root:
        enforcer = SandboxJailEnforcer(
            jail_root=tmp_root,
            read_only_root=True,
            writable_subpaths=["workspace", "tmp"],
        )

        # Attempt to write to read-only root directory fails
        with pytest.raises(SandboxViolationError) as exc_info:
            enforcer.safe_write_text("root_payload.txt", "unauthorized")
        assert exc_info.value.violation_type == SecurityViolationType.READ_ONLY_VIOLATION

        # Attempt to write to unauthorized subdirectory fails
        with pytest.raises(SandboxViolationError):
            enforcer.safe_write_text("forbidden/data.txt", "unauthorized")

        # Permitted write to declared writable subpath succeeds
        written = enforcer.safe_write_text("workspace/authorized.txt", "permitted data")
        assert written > 0
        assert enforcer.safe_read_text("workspace/authorized.txt") == "permitted data"


def test_symlink_trap_detection():
    with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as external_dir:
        enforcer = SandboxJailEnforcer(jail_root=tmp_root)

        # Create external file
        external_file = os.path.join(external_dir, "secret.txt")
        with open(external_file, "w", encoding="utf-8") as f:
            f.write("confidential")

        # Attempt to create in-jail symlink pointing outside
        symlink_path = os.path.join(tmp_root, "evil_link.txt")
        try:
            os.symlink(external_file, symlink_path)
        except (OSError, NotImplementedError):
            # If OS privileges do not permit symlink creation, verify mock path logic
            pytest.skip("Symlink creation requires elevated privilege on host OS")

        # Reading through symlink pointing outside jail must raise violation
        with pytest.raises(SandboxViolationError) as exc_info:
            enforcer.safe_read_text("evil_link.txt")
        assert exc_info.value.violation_type in (
            SecurityViolationType.SYMLINK_TRAP,
            SecurityViolationType.PATH_ESCAPE,
        )


def test_security_audit_log_and_integrity():
    with tempfile.TemporaryDirectory() as tmp_root:
        enforcer = SandboxJailEnforcer(jail_root=tmp_root)

        # Trigger violations
        try:
            enforcer.safe_read_text("../outside.txt")
        except SandboxViolationError:
            pass

        try:
            enforcer.safe_write_text("file.txt\x00", "payload")
        except SandboxViolationError:
            pass

        log = enforcer.get_audit_log()
        assert len(log) == 2
        assert log[0].violation_type in (SecurityViolationType.PATH_ESCAPE, SecurityViolationType.SYMLINK_TRAP)
        assert log[1].violation_type == SecurityViolationType.NULL_BYTE_INJECTION

        # Verify unbroken hash chain
        valid, violations = enforcer.verify_audit_log_integrity()
        assert valid is True
        assert len(violations) == 0


def test_singleton_enforcer_and_reset():
    with tempfile.TemporaryDirectory() as tmp_root:
        e1 = get_sandbox_enforcer(tmp_root)
        e2 = get_sandbox_enforcer()
        assert e1 is e2

        e3 = reset_sandbox_enforcer(tmp_root)
        assert e3 is not e1
        assert get_sandbox_enforcer() is e3
