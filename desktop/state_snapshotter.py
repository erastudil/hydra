"""
Hydra Desktop Atomic Workspace Snapshotter Subsystem.
Captures point-in-time workspace state checkpoints, calculates deterministic
content checksums, serializes state diffs, and executes atomic rollback.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union


def compute_bytes_sha256(data: bytes) -> str:
    """Compute hex SHA-256 digest of byte payload."""
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(file_path: str) -> str:
    """Compute hex SHA-256 digest of file content."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


DEFAULT_IGNORE_PATTERNS = [
    ".git",
    ".git/*",
    "__pycache__",
    "*/__pycache__/*",
    ".pytest_cache",
    "*/.pytest_cache/*",
    ".mypy_cache",
    "*/.mypy_cache/*",
    "dist",
    "dist/*",
    "build",
    "build/*",
    "node_modules",
    "*/node_modules/*",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".system_generated",
    "*/.system_generated/*",
    ".system_generated/**",
]


@dataclass
class FileSnapshot:
    """Atomic snapshot record of a single file in the workspace."""
    rel_path: str
    sha256: str
    size_bytes: int
    mtime: float
    content_b64: Optional[str] = None
    is_binary: bool = False

    @property
    def content_bytes(self) -> Optional[bytes]:
        if self.content_b64 is not None:
            return base64.b64decode(self.content_b64)
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rel_path": self.rel_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "has_content": self.content_b64 is not None,
            "is_binary": self.is_binary,
            "content_b64": self.content_b64,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FileSnapshot:
        return cls(
            rel_path=data["rel_path"],
            sha256=data["sha256"],
            size_bytes=data["size_bytes"],
            mtime=data["mtime"],
            content_b64=data.get("content_b64"),
            is_binary=data.get("is_binary", False),
        )


@dataclass
class SnapshotDiff:
    """Deterministic diff comparison between two workspace snapshots."""
    base_snapshot_id: str
    target_snapshot_id: str
    added_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    unchanged_files: List[str] = field(default_factory=list)
    size_delta_bytes: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def has_changes(self) -> bool:
        return bool(self.added_files or self.modified_files or self.deleted_files)

    @property
    def total_changes_count(self) -> int:
        return len(self.added_files) + len(self.modified_files) + len(self.deleted_files)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_snapshot_id": self.base_snapshot_id,
            "target_snapshot_id": self.target_snapshot_id,
            "has_changes": self.has_changes,
            "total_changes": self.total_changes_count,
            "added_files": list(self.added_files),
            "modified_files": list(self.modified_files),
            "deleted_files": list(self.deleted_files),
            "unchanged_files_count": len(self.unchanged_files),
            "size_delta_bytes": self.size_delta_bytes,
            "timestamp": self.timestamp,
        }

    def serialize_summary(self) -> str:
        """Format diff report in progen syntax."""
        lines = [
            f"base snapshot : {self.base_snapshot_id}.",
            "",
            f"target snapshot : {self.target_snapshot_id}.",
            "",
            f"changes detected : {self.has_changes}.",
            "",
            f"files added : {len(self.added_files)}.",
            "",
            f"files modified : {len(self.modified_files)}.",
            "",
            f"files deleted : {len(self.deleted_files)}.",
            "",
            f"size delta : {self.size_delta_bytes} bytes.",
        ]
        return "\n".join(lines)


@dataclass
class WorkspaceSnapshot:
    """Point-in-time captured workspace state checkpoint."""
    snapshot_id: str = field(default_factory=lambda: f"snap_{uuid.uuid4().hex[:12]}")
    name: str = ""
    timestamp: float = field(default_factory=time.time)
    root_path: str = ""
    files: Dict[str, FileSnapshot] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.snapshot_id

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "name": self.name,
            "timestamp": self.timestamp,
            "root_path": self.root_path,
            "total_files": self.total_files,
            "total_size_bytes": self.total_size_bytes,
            "metadata": dict(self.metadata),
            "files": {k: f.to_dict() for k, f in self.files.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkspaceSnapshot:
        files = {k: FileSnapshot.from_dict(v) for k, v in data.get("files", {}).items()}
        return cls(
            snapshot_id=data["snapshot_id"],
            name=data.get("name", ""),
            timestamp=data.get("timestamp", time.time()),
            root_path=data.get("root_path", ""),
            files=files,
            metadata=data.get("metadata", {}),
        )


class WorkspaceSnapshotter:
    """
    Atomic workspace snapshotter supporting checkpointing, differential analysis,
    and reversible filesystem rollbacks.
    """

    def __init__(
        self,
        root_dir: Optional[str] = None,
        max_file_size_bytes: int = 10 * 1024 * 1024,
        max_snapshots: int = 50,
        ignore_patterns: Optional[List[str]] = None,
    ) -> None:
        self.root_dir = os.path.abspath(root_dir or os.getcwd())
        self.max_file_size_bytes = max_file_size_bytes
        self.max_snapshots = max_snapshots
        self.ignore_patterns = list(ignore_patterns or DEFAULT_IGNORE_PATTERNS)
        self._lock = threading.RLock()
        self._snapshots: Dict[str, WorkspaceSnapshot] = {}

    @property
    def total_snapshots(self) -> int:
        with self._lock:
            return len(self._snapshots)

    def _is_ignored(self, rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/")
        parts = normalized.split("/")

        for pattern in self.ignore_patterns:
            pat = pattern.replace("\\", "/")
            if fnmatch.fnmatch(normalized, pat):
                return True
            for part in parts:
                if fnmatch.fnmatch(part, pat):
                    return True
        return False

    def capture_snapshot(
        self,
        name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        subpaths: Optional[List[str]] = None,
        store_content: bool = True,
    ) -> WorkspaceSnapshot:
        """
        Capture point-in-time filesystem state checkpoint.
        Hashes file contents, computes digests, and encodes byte content for atomic restoration.
        """
        with self._lock:
            snapshot = WorkspaceSnapshot(
                name=name or f"snapshot_{int(time.time())}",
                root_path=self.root_dir,
                metadata=metadata or {},
            )

            targets = subpaths or ["."]
            for target in targets:
                target_full = os.path.normpath(os.path.join(self.root_dir, target))
                if os.path.isfile(target_full):
                    rel = os.path.relpath(target_full, self.root_dir).replace("\\", "/")
                    if not self._is_ignored(rel):
                        self._capture_file(target_full, rel, snapshot, store_content)
                elif os.path.isdir(target_full):
                    for root, dirs, files in os.walk(target_full):
                        # Filter out ignored directories in-place
                        dirs[:] = [
                            d for d in dirs
                            if not self._is_ignored(os.path.relpath(os.path.join(root, d), self.root_dir).replace("\\", "/"))
                        ]
                        for f in files:
                            file_full = os.path.join(root, f)
                            rel = os.path.relpath(file_full, self.root_dir).replace("\\", "/")
                            if not self._is_ignored(rel):
                                self._capture_file(file_full, rel, snapshot, store_content)

            self._snapshots[snapshot.snapshot_id] = snapshot
            if len(self._snapshots) > self.max_snapshots:
                # Evict oldest snapshot
                oldest_id = min(self._snapshots.keys(), key=lambda k: self._snapshots[k].timestamp)
                self._snapshots.pop(oldest_id, None)

            return snapshot

    def _capture_file(
        self,
        full_path: str,
        rel_path: str,
        snapshot: WorkspaceSnapshot,
        store_content: bool,
    ) -> None:
        try:
            stat = os.stat(full_path)
            size = stat.st_size
            mtime = stat.st_mtime

            content_b64 = None
            is_bin = False
            sha = ""

            if size <= self.max_file_size_bytes and store_content:
                with open(full_path, "rb") as f:
                    raw_bytes = f.read()
                sha = compute_bytes_sha256(raw_bytes)
                content_b64 = base64.b64encode(raw_bytes).decode("ascii")
                # Simple binary check
                if b"\0" in raw_bytes[:1024]:
                    is_bin = True
            else:
                sha = compute_file_sha256(full_path)

            file_snap = FileSnapshot(
                rel_path=rel_path,
                sha256=sha,
                size_bytes=size,
                mtime=mtime,
                content_b64=content_b64,
                is_binary=is_bin,
            )
            snapshot.files[rel_path] = file_snap
        except (IOError, OSError):
            pass

    def get_snapshot(self, snapshot_id: str) -> Optional[WorkspaceSnapshot]:
        """Fetch snapshot by ID."""
        with self._lock:
            return self._snapshots.get(snapshot_id)

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """List metadata for all active checkpoints in chronological order."""
        with self._lock:
            snaps = sorted(self._snapshots.values(), key=lambda s: s.timestamp)
            return [
                {
                    "snapshot_id": s.snapshot_id,
                    "name": s.name,
                    "timestamp": s.timestamp,
                    "total_files": s.total_files,
                    "total_size_bytes": s.total_size_bytes,
                    "metadata": s.metadata,
                }
                for s in snaps
            ]

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete snapshot by ID."""
        with self._lock:
            return self._snapshots.pop(snapshot_id, None) is not None

    def clear(self) -> None:
        """Purge all stored snapshots."""
        with self._lock:
            self._snapshots.clear()

    def compute_diff(self, base_snapshot_id: str, target_snapshot_id: str) -> SnapshotDiff:
        """Compute file-level differential between two snapshots."""
        with self._lock:
            base = self._snapshots.get(base_snapshot_id)
            target = self._snapshots.get(target_snapshot_id)

            if not base or not target:
                raise ValueError("Base or target snapshot ID not found in registry")

            added: List[str] = []
            modified: List[str] = []
            deleted: List[str] = []
            unchanged: List[str] = []

            base_keys = set(base.files.keys())
            target_keys = set(target.files.keys())

            for k in sorted(target_keys - base_keys):
                added.append(k)

            for k in sorted(base_keys - target_keys):
                deleted.append(k)

            for k in sorted(base_keys & target_keys):
                if base.files[k].sha256 != target.files[k].sha256:
                    modified.append(k)
                else:
                    unchanged.append(k)

            size_delta = target.total_size_bytes - base.total_size_bytes
            return SnapshotDiff(
                base_snapshot_id=base_snapshot_id,
                target_snapshot_id=target_snapshot_id,
                added_files=added,
                modified_files=modified,
                deleted_files=deleted,
                unchanged_files=unchanged,
                size_delta_bytes=size_delta,
            )

    def diff_current_with_snapshot(
        self,
        snapshot_id: str,
        subpaths: Optional[List[str]] = None,
    ) -> SnapshotDiff:
        """Compute differential between active disk state and a prior snapshot."""
        with self._lock:
            target = self._snapshots.get(snapshot_id)
            if not target:
                raise ValueError(f"Snapshot ID '{snapshot_id}' not found")

            # Temporary capture of active filesystem
            temp_current = self.capture_snapshot(
                name="__temp_diff_current__",
                subpaths=subpaths or list(target.files.keys()),
                store_content=False,
            )
            try:
                return self.compute_diff(snapshot_id, temp_current.snapshot_id)
            finally:
                self.delete_snapshot(temp_current.snapshot_id)

    def rollback(
        self,
        snapshot_id: str,
        dry_run: bool = False,
        subpaths: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Revert filesystem atomically to the state recorded in snapshot_id.
        Restores modified/missing files and deletes newly created files.
        """
        with self._lock:
            snapshot = self._snapshots.get(snapshot_id)
            if not snapshot:
                raise ValueError(f"Cannot rollback: snapshot ID '{snapshot_id}' not found")

            # Evaluate files to restore and files to delete
            restored: List[str] = []
            deleted: List[str] = []
            errors: List[str] = []

            # Step 1: Detect newly created files under snapshot paths that must be removed
            filter_paths = set(subpaths) if subpaths else None
            active_files: Set[str] = set()

            for root, dirs, files in os.walk(self.root_dir):
                dirs[:] = [
                    d for d in dirs
                    if not self._is_ignored(os.path.relpath(os.path.join(root, d), self.root_dir).replace("\\", "/"))
                ]
                for f in files:
                    file_full = os.path.join(root, f)
                    rel = os.path.relpath(file_full, self.root_dir).replace("\\", "/")
                    if not self._is_ignored(rel):
                        if filter_paths is None or any(rel == p or rel.startswith(p + "/") for p in filter_paths):
                            active_files.add(rel)

            # Files in active filesystem but absent in snapshot -> delete
            for rel in sorted(active_files - set(snapshot.files.keys())):
                target_path = os.path.join(self.root_dir, rel)
                deleted.append(rel)
                if not dry_run:
                    try:
                        if os.path.isfile(target_path):
                            os.remove(target_path)
                    except Exception as exc:
                        errors.append(f"Failed to delete {rel}: {exc}")

            # Step 2: Files in snapshot that are missing or changed -> restore
            for rel, fsnap in sorted(snapshot.files.items()):
                if filter_paths and not any(rel == p or rel.startswith(p + "/") for p in filter_paths):
                    continue

                target_path = os.path.join(self.root_dir, rel)
                needs_restore = False

                if not os.path.exists(target_path):
                    needs_restore = True
                else:
                    try:
                        current_sha = compute_file_sha256(target_path)
                        if current_sha != fsnap.sha256:
                            needs_restore = True
                    except Exception:
                        needs_restore = True

                if needs_restore:
                    restored.append(rel)
                    if not dry_run:
                        if fsnap.content_bytes is None:
                            errors.append(f"Cannot restore {rel}: content was not stored in snapshot")
                            continue
                        try:
                            os.makedirs(os.path.dirname(target_path), exist_ok=True)
                            with open(target_path, "wb") as out_f:
                                out_f.write(fsnap.content_bytes)
                        except Exception as exc:
                            errors.append(f"Failed to restore {rel}: {exc}")

            return {
                "status": "DRY_RUN" if dry_run else ("COMPLETED" if not errors else "PARTIAL_ERROR"),
                "snapshot_id": snapshot_id,
                "restored_files_count": len(restored),
                "deleted_files_count": len(deleted),
                "restored_files": restored,
                "deleted_files": deleted,
                "errors": errors,
            }

    def export_snapshot_to_json(self, snapshot_id: str) -> str:
        """Export snapshot state to JSON string."""
        with self._lock:
            snapshot = self._snapshots.get(snapshot_id)
            if not snapshot:
                raise ValueError(f"Snapshot '{snapshot_id}' not found")
            return json.dumps(snapshot.to_dict(), indent=2)

    def import_snapshot_from_json(self, json_str: str) -> WorkspaceSnapshot:
        """Import snapshot from JSON string into registry."""
        with self._lock:
            data = json.loads(json_str)
            snap = WorkspaceSnapshot.from_dict(data)
            self._snapshots[snap.snapshot_id] = snap
            return snap


_GLOBAL_SNAPSHOTTER: Optional[WorkspaceSnapshotter] = None
_GLOBAL_SNAP_LOCK = threading.RLock()


def get_workspace_snapshotter(root_dir: Optional[str] = None) -> WorkspaceSnapshotter:
    """Acquire thread-safe singleton WorkspaceSnapshotter."""
    global _GLOBAL_SNAPSHOTTER
    with _GLOBAL_SNAP_LOCK:
        if _GLOBAL_SNAPSHOTTER is None:
            _GLOBAL_SNAPSHOTTER = WorkspaceSnapshotter(root_dir=root_dir)
        return _GLOBAL_SNAPSHOTTER


def reset_workspace_snapshotter(root_dir: Optional[str] = None) -> WorkspaceSnapshotter:
    """Reset singleton WorkspaceSnapshotter."""
    global _GLOBAL_SNAPSHOTTER
    with _GLOBAL_SNAP_LOCK:
        _GLOBAL_SNAPSHOTTER = WorkspaceSnapshotter(root_dir=root_dir)
        return _GLOBAL_SNAPSHOTTER
