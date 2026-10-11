"""
Integration test suite for Hydra Desktop Atomic Workspace Snapshotter.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import os
import tempfile
import pytest

from desktop.state_snapshotter import (
    WorkspaceSnapshotter,
    WorkspaceSnapshot,
    SnapshotDiff,
    compute_file_sha256,
    compute_bytes_sha256,
    get_workspace_snapshotter,
    reset_workspace_snapshotter,
)


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory(prefix="hydra_test_ws_") as tmp_dir:
        # Create sample workspace structure
        file1 = os.path.join(tmp_dir, "config.json")
        with open(file1, "w", encoding="utf-8") as f:
            f.write('{"theme": "dark", "version": 1}')

        sub = os.path.join(tmp_dir, "src")
        os.makedirs(sub, exist_ok=True)
        file2 = os.path.join(sub, "main.py")
        with open(file2, "w", encoding="utf-8") as f:
            f.write("print('hydra sovereign')\n")

        yield tmp_dir


def test_capture_snapshot(temp_workspace: str):
    """Verify capturing point-in-time snapshot of workspace files and hashes."""
    snapshotter = WorkspaceSnapshotter(root_dir=temp_workspace)
    snap = snapshotter.capture_snapshot(name="initial")

    assert snap.total_files == 2
    assert "config.json" in snap.files
    assert "src/main.py" in snap.files
    assert snap.files["config.json"].size_bytes > 0
    assert len(snap.files["config.json"].sha256) == 64
    assert snapshotter.total_snapshots == 1


def test_ignore_patterns_filter_untracked_dirs(temp_workspace: str):
    """Verify git, pycache, and log files are excluded from snapshot."""
    git_dir = os.path.join(temp_workspace, ".git")
    os.makedirs(git_dir, exist_ok=True)
    with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
        f.write("ref: refs/heads/main\n")

    pycache_dir = os.path.join(temp_workspace, "src", "__pycache__")
    os.makedirs(pycache_dir, exist_ok=True)
    with open(os.path.join(pycache_dir, "main.cpython-314.pyc"), "wb") as f:
        f.write(b"\x00\x01\x02")

    snapshotter = WorkspaceSnapshotter(root_dir=temp_workspace)
    snap = snapshotter.capture_snapshot(name="filtered")

    assert "config.json" in snap.files
    assert "src/main.py" in snap.files
    assert not any(".git" in k for k in snap.files.keys())
    assert not any("__pycache__" in k for k in snap.files.keys())


def test_differential_analysis_added_modified_deleted(temp_workspace: str):
    """Verify differential computation across snapshots detects additions, modifications, and deletions."""
    snapshotter = WorkspaceSnapshotter(root_dir=temp_workspace)
    snap1 = snapshotter.capture_snapshot(name="base")

    # Modify config.json
    with open(os.path.join(temp_workspace, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"theme": "slate", "version": 2}')

    # Add new file
    with open(os.path.join(temp_workspace, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("test note")

    # Delete src/main.py
    os.remove(os.path.join(temp_workspace, "src", "main.py"))

    snap2 = snapshotter.capture_snapshot(name="mutated")

    diff = snapshotter.compute_diff(snap1.snapshot_id, snap2.snapshot_id)
    assert diff.has_changes is True
    assert diff.added_files == ["notes.txt"]
    assert diff.modified_files == ["config.json"]
    assert diff.deleted_files == ["src/main.py"]
    assert diff.total_changes_count == 3

    summary = diff.serialize_summary()
    assert "files added : 1." in summary
    assert "files modified : 1." in summary
    assert "files deleted : 1." in summary


def test_atomic_rollback_restores_clean_state(temp_workspace: str):
    """Verify atomic rollback restores modified/deleted files and deletes rogue files."""
    snapshotter = WorkspaceSnapshotter(root_dir=temp_workspace)
    snap = snapshotter.capture_snapshot(name="baseline")

    # Mutate disk
    with open(os.path.join(temp_workspace, "config.json"), "w", encoding="utf-8") as f:
        f.write("corrupted content")
    os.remove(os.path.join(temp_workspace, "src", "main.py"))
    rogue_path = os.path.join(temp_workspace, "rogue.tmp")
    with open(rogue_path, "w", encoding="utf-8") as f:
        f.write("should be deleted")

    # Dry run check
    dry_res = snapshotter.rollback(snap.snapshot_id, dry_run=True)
    assert dry_res["status"] == "DRY_RUN"
    assert "config.json" in dry_res["restored_files"]
    assert "src/main.py" in dry_res["restored_files"]
    assert "rogue.tmp" in dry_res["deleted_files"]
    assert os.path.exists(rogue_path)  # Unmodified during dry run

    # Actual rollback
    res = snapshotter.rollback(snap.snapshot_id, dry_run=False)
    assert res["status"] == "COMPLETED"
    assert not os.path.exists(rogue_path)  # Deleted

    with open(os.path.join(temp_workspace, "config.json"), "r", encoding="utf-8") as f:
        assert '{"theme": "dark", "version": 1}' in f.read()

    with open(os.path.join(temp_workspace, "src", "main.py"), "r", encoding="utf-8") as f:
        assert "print('hydra sovereign')" in f.read()


def test_json_export_and_import(temp_workspace: str):
    """Verify serialization of snapshots to/from JSON strings."""
    snapshotter = WorkspaceSnapshotter(root_dir=temp_workspace)
    snap = snapshotter.capture_snapshot(name="export_test")

    exported = snapshotter.export_snapshot_to_json(snap.snapshot_id)
    assert "export_test" in exported

    imported = snapshotter.import_snapshot_from_json(exported)
    assert imported.snapshot_id == snap.snapshot_id
    assert imported.total_files == snap.total_files
    assert imported.files["config.json"].sha256 == snap.files["config.json"].sha256


def test_global_singleton_snapshotter():
    """Verify singleton lifecycle for WorkspaceSnapshotter."""
    s1 = get_workspace_snapshotter()
    s2 = get_workspace_snapshotter()
    assert s1 is s2

    s3 = reset_workspace_snapshotter()
    assert s3 is not s1
    assert get_workspace_snapshotter() is s3
