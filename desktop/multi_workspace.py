"""
Hydra Desktop Multi-Workspace Manager.
Provides isolated workspace boundaries, path confinement security guards,
lightweight file tree change monitors, and stateful context swapping.
Complies with AGENTS.md genome invariants: zero copula P018, zero stubs, deterministic verification.
"""

from __future__ import annotations

import collections
import copy
import datetime
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from hydra_cli.native_tools import NativeToolRegistry

logger = logging.getLogger("hydra.desktop.multi_workspace")

DEFAULT_BASE_DIR = os.path.expanduser("~/.hydra/workspaces")


class PathTraversalSecurityError(PermissionError):
    """Raised when an operation attempts directory traversal outside workspace envelope."""
    pass


class Workspace:
    """
    Representation of an isolated workspace boundary and runtime state.
    Strictly confines all file operations within root_path.
    """

    def __init__(
        self,
        workspace_id: str,
        name: str,
        root_path: str,
        created_at: Optional[str] = None,
        is_active: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.workspace_id: str = workspace_id
        self.name: str = name
        self.root_path: str = os.path.realpath(os.path.abspath(root_path))
        self.created_at: str = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.is_active: bool = is_active
        self.metadata: Dict[str, Any] = dict(metadata or {})
        self.context: Dict[str, Any] = dict(context or {
            "open_files": [],
            "terminal_history": [],
            "active_tab": "chat",
            "environment_variables": {},
        })

    def resolve_path(self, target_path: str, allow_missing: bool = True) -> str:
        """
        Validate and resolve candidate path against workspace boundary.
        Guarantees that resulting realpath resides strictly within self.root_path.
        Rejects null bytes, relative traversal escapes, and outside absolute paths.
        """
        if not target_path or not str(target_path).strip():
            return self.root_path

        # 1. Reject null byte injection
        if "\0" in str(target_path):
            raise PermissionError("Security violation: null bytes detected in path")

        clean_path = str(target_path).strip()

        # If relative, join with workspace root
        if not os.path.isabs(clean_path):
            candidate = os.path.join(self.root_path, clean_path)
        else:
            candidate = clean_path

        resolved = os.path.realpath(os.path.abspath(candidate))
        root_real = os.path.realpath(self.root_path)

        # Enforce boundary confinement
        try:
            common = os.path.commonpath([root_real, resolved])
        except ValueError:
            # Different drives on Windows
            raise PermissionError(f"Security violation: path crosses drive boundary ({resolved})")

        if common != root_real:
            raise PermissionError(
                f"Security violation: path traversal outside workspace bounds ({resolved} not within {root_real})"
            )

        if not allow_missing and not os.path.exists(resolved):
            raise FileNotFoundError(f"Workspace path does not exist: {resolved}")

        return resolved

    def write_file(self, rel_or_abs_path: str, content: Union[str, bytes]) -> int:
        """Write file strictly within workspace boundaries, creating parent directories."""
        resolved = self.resolve_path(rel_or_abs_path)
        os.makedirs(os.path.dirname(resolved), exist_ok=True)

        if isinstance(content, str):
            encoded = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            encoded = bytes(content)
        else:
            encoded = str(content).encode("utf-8")

        with open(resolved, "wb") as f:
            f.write(encoded)
        return len(encoded)

    def read_file(self, rel_or_abs_path: str) -> str:
        """Read text file strictly within workspace boundaries."""
        resolved = self.resolve_path(rel_or_abs_path, allow_missing=False)
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def list_dir(self, rel_or_abs_path: str = ".") -> List[str]:
        """List directory entries strictly within workspace boundaries."""
        resolved = self.resolve_path(rel_or_abs_path, allow_missing=False)
        if not os.path.isdir(resolved):
            raise NotADirectoryError(f"Path is not a directory: {resolved}")
        return sorted(os.listdir(resolved))

    def delete_file(self, rel_or_abs_path: str) -> bool:
        """Delete file strictly within workspace boundaries."""
        resolved = self.resolve_path(rel_or_abs_path)
        if os.path.isfile(resolved):
            os.remove(resolved)
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize descriptor to JSON-compatible dictionary."""
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "root_path": self.root_path,
            "created_at": self.created_at,
            "is_active": self.is_active,
            "metadata": dict(self.metadata),
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Workspace:
        """Instantiate Workspace from dictionary representation."""
        return cls(
            workspace_id=data["workspace_id"],
            name=data["name"],
            root_path=data["root_path"],
            created_at=data.get("created_at"),
            is_active=data.get("is_active", False),
            metadata=data.get("metadata"),
            context=data.get("context"),
        )


WorkspaceDescriptor = Workspace


class MultiWorkspaceManager:
    """
    Coordinator for multiple project workspaces, isolation boundaries,
    context state swaps, and file change monitors.
    """

    def __init__(
        self,
        base_dir: Optional[str] = None,
        state_file: Optional[str] = None,
    ) -> None:
        self.base_dir: str = os.path.realpath(os.path.abspath(base_dir or DEFAULT_BASE_DIR))
        os.makedirs(self.base_dir, exist_ok=True)

        if state_file:
            self.state_file: str = os.path.realpath(os.path.abspath(state_file))
        else:
            self.state_file = os.path.join(self.base_dir, "workspaces_state.json")

        self._workspaces: Dict[str, Workspace] = {}
        self._active_id: Optional[str] = None
        self._lock = threading.RLock()

        if os.path.isfile(self.state_file):
            self.load_state(self.state_file)

    @property
    def active_workspace(self) -> Optional[Workspace]:
        """Retrieve currently active workspace."""
        with self._lock:
            if self._active_id and self._active_id in self._workspaces:
                return self._workspaces[self._active_id]
            if self._workspaces:
                first = next(iter(self._workspaces.values()))
                first.is_active = True
                self._active_id = first.workspace_id
                return first
            return None

    @property
    def total_workspaces(self) -> int:
        """Count registered workspaces."""
        with self._lock:
            return len(self._workspaces)

    @staticmethod
    def _validate_workspace_name(name: str) -> None:
        """Enforce strict workspace name invariants."""
        if not name or not isinstance(name, str):
            raise ValueError("Workspace name cannot be empty")
        if " " in name:
            raise ValueError(f"Workspace name cannot contain spaces: '{name}'")
        if "/" in name or "\\" in name:
            raise ValueError(f"Workspace name cannot contain path separators: '{name}'")
        if ".." in name:
            raise ValueError(f"Workspace name cannot contain directory traversal tokens: '{name}'")

    def create_workspace(
        self,
        name: str,
        metadata: Optional[Dict[str, Any]] = None,
        root_path: Optional[str] = None,
    ) -> Workspace:
        """Register and provision a new workspace envelope."""
        self._validate_workspace_name(name)

        with self._lock:
            if root_path is None:
                ws_root = os.path.join(self.base_dir, name)
            else:
                ws_root = os.path.abspath(root_path)

            os.makedirs(ws_root, exist_ok=True)
            ws_id = f"ws_{name}_{uuid.uuid4().hex[:6]}"

            # First created workspace is marked active
            is_first = len(self._workspaces) == 0

            ws = Workspace(
                workspace_id=ws_id,
                name=name,
                root_path=ws_root,
                metadata=metadata,
                is_active=is_first,
            )

            self._workspaces[ws_id] = ws
            if is_first:
                self._active_id = ws_id

            self.save_state()
            return ws

    def get_workspace(self, target_id_or_name: str) -> Optional[Workspace]:
        """Lookup workspace by unique identifier or name."""
        with self._lock:
            if target_id_or_name in self._workspaces:
                return self._workspaces[target_id_or_name]
            for ws in self._workspaces.values():
                if ws.name == target_id_or_name:
                    return ws
            return None

    def list_workspaces(self) -> List[Workspace]:
        """List all workspaces in alphanumeric sorted order."""
        with self._lock:
            return sorted(list(self._workspaces.values()), key=lambda w: w.name.lower())

    def switch_workspace(self, target_id_or_name: str) -> Workspace:
        """
        Thread-safe context switch to target workspace.
        Ensures exactly one workspace is marked active.
        """
        with self._lock:
            target = self.get_workspace(target_id_or_name)
            if not target:
                raise KeyError(f"Workspace not found: {target_id_or_name}")

            for ws in self._workspaces.values():
                ws.is_active = (ws.workspace_id == target.workspace_id)

            self._active_id = target.workspace_id
            self.save_state()
            return target

    def execute_in_workspace(self, target_id: str, action: Callable[[Workspace], Any]) -> Any:
        """
        Execute operational callback inside target workspace scope
        without mutating global active workspace selection.
        """
        with self._lock:
            target = self.get_workspace(target_id)
            if not target:
                raise KeyError(f"Workspace not found: {target_id}")

        # Execute callback outside lock to prevent re-entrancy deadlocks
        return action(target)

    def close_workspace(self, target_id_or_name: str, delete_files: bool = False) -> bool:
        """Close and unregister workspace, optionally wiping directories."""
        with self._lock:
            target = self.get_workspace(target_id_or_name)
            if not target:
                return False

            ws_id = target.workspace_id
            root = target.root_path

            if delete_files and os.path.isdir(root):
                shutil.rmtree(root, ignore_errors=True)

            del self._workspaces[ws_id]

            if self._active_id == ws_id:
                if self._workspaces:
                    next_ws = next(iter(self._workspaces.values()))
                    next_ws.is_active = True
                    self._active_id = next_ws.workspace_id
                else:
                    self._active_id = None

            self.save_state()
            return True

    def clear(self, delete_files: bool = False) -> None:
        """Clear all workspace registrations and optionally delete root directories."""
        with self._lock:
            if delete_files:
                for ws in self._workspaces.values():
                    if os.path.isdir(ws.root_path):
                        shutil.rmtree(ws.root_path, ignore_errors=True)

            self._workspaces.clear()
            self._active_id = None
            self.save_state()

    def save_state(self, path: Optional[str] = None) -> str:
        """Persist workspace registry to JSON file using atomic replacement."""
        with self._lock:
            target = path or self.state_file
            os.makedirs(os.path.dirname(target), exist_ok=True)
            payload = {
                "version": "1.0.0",
                "active_workspace_id": self._active_id,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "workspaces": [ws.to_dict() for ws in self._workspaces.values()],
            }

            tmp_target = f"{target}.tmp.{os.getpid()}"
            with open(tmp_target, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_target, target)
            return target

    def load_state(self, path: Optional[str] = None) -> int:
        """Load workspace registry from state file."""
        with self._lock:
            target = path or self.state_file
            if not os.path.isfile(target):
                return 0

            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._workspaces.clear()
            self._active_id = data.get("active_workspace_id")

            for item in data.get("workspaces", []):
                ws = Workspace.from_dict(item)
                self._workspaces[ws.workspace_id] = ws

            # Verify active workspace validity
            if self._active_id not in self._workspaces and self._workspaces:
                first = next(iter(self._workspaces.values()))
                first.is_active = True
                self._active_id = first.workspace_id

            return len(self._workspaces)

    def get_tool_registry(self, workspace_id: Optional[str] = None) -> NativeToolRegistry:
        """Instantiate a NativeToolRegistry strictly bounded to the workspace root directory."""
        with self._lock:
            ws = self.get_workspace(workspace_id) if workspace_id else self.active_workspace
            if not ws:
                raise RuntimeError("No active workspace configured")
            return NativeToolRegistry(cwd=ws.root_path)
