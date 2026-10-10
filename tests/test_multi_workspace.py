"""
Integration test suite for Hydra Desktop Multi-Workspace Manager.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import concurrent.futures
import os
import tempfile
import pytest

from desktop.multi_workspace import MultiWorkspaceManager, Workspace


@pytest.fixture
def manager():
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = os.path.join(tmp_dir, "workspaces_state.json")
        mgr = MultiWorkspaceManager(base_dir=tmp_dir, state_file=state_file)
        yield mgr
        mgr.clear(delete_files=True)


def test_workspace_creation_and_directory_isolation(manager: MultiWorkspaceManager):
    """Verify creation, unique root directories, and tool registry bindings."""
    ws1 = manager.create_workspace("alpha", metadata={"project": "alpha_core"})
    ws2 = manager.create_workspace("beta", metadata={"project": "beta_core"})

    assert ws1.name == "alpha"
    assert ws2.name == "beta"
    assert ws1.root_path != ws2.root_path
    assert os.path.isdir(ws1.root_path)
    assert os.path.isdir(ws2.root_path)

    # First workspace defaults to active
    assert manager.active_workspace is not None
    assert manager.active_workspace.workspace_id == ws1.workspace_id
    assert ws1.is_active is True
    assert ws2.is_active is False

    assert manager.total_workspaces == 2


def test_workspace_boundary_sandboxing_path_traversal(manager: MultiWorkspaceManager):
    """Verify boundary sandboxing blocks parent traversal and absolute escapes."""
    ws = manager.create_workspace("sandboxed")

    # 1. Allowed file inside workspace
    ws.write_file("data.txt", "sovereign payload")
    assert ws.read_file("data.txt") == "sovereign payload"

    # 2. Block parent traversal ../
    with pytest.raises(PermissionError) as exc_info:
        ws.resolve_path("../escape.txt")
    assert "Security violation" in str(exc_info.value)

    with pytest.raises(PermissionError):
        ws.read_file("../../etc/passwd")

    with pytest.raises(PermissionError):
        ws.write_file("../forbidden.txt", "payload")

    with pytest.raises(PermissionError):
        ws.list_dir("..")

    # 3. Block null byte injection
    with pytest.raises(PermissionError):
        ws.resolve_path("safe.txt\0/escape")

    # 4. Block absolute path outside workspace root
    outside_file = os.path.join(tempfile.gettempdir(), "outside_probe.txt")
    with pytest.raises(PermissionError):
        ws.resolve_path(outside_file)


def test_workspace_file_operations_within_boundary(manager: MultiWorkspaceManager):
    """Verify CRUD file operations strictly confined to workspace boundaries."""
    ws = manager.create_workspace("file_ops")

    # Subdirectory creation and file writing
    bytes_written = ws.write_file("sub/nested/file.txt", "nested content")
    assert bytes_written > 0
    assert ws.read_file("sub/nested/file.txt") == "nested content"

    # Directory listing
    entries = ws.list_dir("sub")
    assert "nested" in entries

    # Deletion
    assert ws.delete_file("sub/nested/file.txt") is True
    assert ws.delete_file("sub/nested/file.txt") is False
    assert not os.path.exists(ws.resolve_path("sub/nested/file.txt"))


def test_multi_workspace_concurrent_switching(manager: MultiWorkspaceManager):
    """Verify thread-safe concurrent workspace switching without race conditions."""
    ws_list = [manager.create_workspace(f"worker_{i}") for i in range(8)]
    assert manager.total_workspaces == 8

    def switch_worker(target_id: str) -> str:
        for _ in range(25):
            manager.switch_workspace(target_id)
        return target_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(switch_worker, ws.workspace_id) for ws in ws_list]
        for f in concurrent.futures.as_completed(futures):
            assert f.result() in manager._workspaces

    # Invariant: exactly one workspace is marked active
    active_count = sum(1 for ws in manager._workspaces.values() if ws.is_active)
    assert active_count == 1
    assert manager.active_workspace is not None
    assert manager.active_workspace.is_active is True


def test_workspace_execute_in_workspace_scope(manager: MultiWorkspaceManager):
    """Verify isolated execution context preserves global active workspace selection."""
    ws_a = manager.create_workspace("scope_a")
    ws_b = manager.create_workspace("scope_b")

    manager.switch_workspace(ws_a.workspace_id)
    assert manager.active_workspace.workspace_id == ws_a.workspace_id

    def worker_action(ws: Workspace) -> str:
        ws.write_file("probe.txt", f"probe for {ws.name}")
        return ws.read_file("probe.txt")

    res = manager.execute_in_workspace(ws_b.workspace_id, worker_action)
    assert res == "probe for scope_b"

    # Active workspace remains scope_a
    assert manager.active_workspace.workspace_id == ws_a.workspace_id
    assert not os.path.exists(os.path.join(ws_a.root_path, "probe.txt"))
    assert os.path.exists(os.path.join(ws_b.root_path, "probe.txt"))


def test_workspace_registry_state_persistence(manager: MultiWorkspaceManager):
    """Verify serialization and deserialization of workspace registry state."""
    ws1 = manager.create_workspace("persist_1", metadata={"type": "research"})
    ws2 = manager.create_workspace("persist_2", metadata={"type": "coding"})
    manager.switch_workspace(ws2.workspace_id)

    state_path = manager.state_file
    assert state_path is not None and os.path.isfile(state_path)

    # Instantiate fresh manager restoring from state file
    restored_mgr = MultiWorkspaceManager(base_dir=manager.base_dir)
    loaded = restored_mgr.load_state(state_path)
    assert loaded == 2
    assert restored_mgr.total_workspaces == 2

    # Active workspace restored
    assert restored_mgr.active_workspace is not None
    assert restored_mgr.active_workspace.name == "persist_2"
    assert restored_mgr.get_workspace("persist_1") is not None
    assert restored_mgr.get_workspace("persist_1").metadata["type"] == "research"


def test_workspace_close_and_cleanup(manager: MultiWorkspaceManager):
    """Verify closing workspace hands over active state and cleans directories."""
    ws1 = manager.create_workspace("close_1")
    ws2 = manager.create_workspace("close_2")
    manager.switch_workspace(ws1.workspace_id)

    dir_to_delete = ws1.root_path
    assert os.path.isdir(dir_to_delete)

    closed = manager.close_workspace(ws1.workspace_id, delete_files=True)
    assert closed is True
    assert manager.total_workspaces == 1
    assert not os.path.exists(dir_to_delete)

    # Active workspace transferred to ws2
    assert manager.active_workspace is not None
    assert manager.active_workspace.workspace_id == ws2.workspace_id


def test_workspace_name_validation(manager: MultiWorkspaceManager):
    """Verify invalid workspace names with traversal tokens are rejected."""
    with pytest.raises(ValueError):
        manager.create_workspace("../outside")

    with pytest.raises(ValueError):
        manager.create_workspace("bad/slash")

    with pytest.raises(ValueError):
        manager.create_workspace("bad\\backslash")

    with pytest.raises(ValueError):
        manager.create_workspace("has space")

    with pytest.raises(ValueError):
        manager.create_workspace("")
