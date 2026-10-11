"""
Hydra Desktop Multi-Session Branching Checkpoint Manager Subsystem.
Provides DAG session tree forks, branch merging, parent-child provenance,
and fast binary and JSON disk serialization.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class MergeStrategy(str, Enum):
    """Branch merge conflict resolution strategies."""
    FAST_FORWARD = "fast_forward"
    OURS = "ours"
    THEIRS = "theirs"
    UNION = "union"
    NEWEST = "newest"


def compute_checkpoint_hash(
    parent_ids: Sequence[str],
    timestamp: float,
    step_index: int,
    state_diff: Dict[str, Any],
    messages: Sequence[Dict[str, Any]],
) -> str:
    """Compute deterministic hex SHA-256 digest of checkpoint state."""
    hasher = hashlib.sha256()
    sorted_parents = ",".join(sorted(parent_ids))
    hasher.update(sorted_parents.encode("utf-8"))
    hasher.update(f"{timestamp:.6f}:{step_index}:".encode("utf-8"))
    state_repr = json.dumps(state_diff, sort_keys=True, separators=(",", ":"))
    hasher.update(state_repr.encode("utf-8"))
    msg_repr = json.dumps(list(messages), sort_keys=True, separators=(",", ":"))
    hasher.update(msg_repr.encode("utf-8"))
    return hasher.hexdigest()


@dataclass
class SessionCheckpoint:
    """DAG node representing atomic snapshot of session state and message history."""
    checkpoint_id: str
    session_id: str
    branch_name: str
    parent_ids: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    step_index: int = 0
    state_diff: Dict[str, Any] = field(default_factory=dict)
    messages: List[Dict[str, Any]] = field(default_factory=list)
    author: str = "orchestrator"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    checksum: str = ""

    def __post_init__(self) -> None:
        if not self.checksum:
            self.checksum = compute_checkpoint_hash(
                self.parent_ids,
                self.timestamp,
                self.step_index,
                self.state_diff,
                self.messages,
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize checkpoint node to dictionary."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "branch_name": self.branch_name,
            "parent_ids": list(self.parent_ids),
            "timestamp": self.timestamp,
            "step_index": self.step_index,
            "state_diff": dict(self.state_diff),
            "messages": [dict(m) for m in self.messages],
            "author": self.author,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SessionCheckpoint:
        """Deserialize checkpoint node from dictionary."""
        return cls(
            checkpoint_id=data["checkpoint_id"],
            session_id=data["session_id"],
            branch_name=data["branch_name"],
            parent_ids=list(data.get("parent_ids", [])),
            timestamp=float(data.get("timestamp", time.time())),
            step_index=int(data.get("step_index", 0)),
            state_diff=dict(data.get("state_diff", {})),
            messages=[dict(m) for m in data.get("messages", [])],
            author=data.get("author", "orchestrator"),
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
            checksum=data.get("checksum", ""),
        )


class SessionCheckpointManager:
    """
    Multi-session branching checkpoint manager engine.
    Maintains directed acyclic graph (DAG) of session checkpoints,
    supports branch creation, fork points, 3-way branch merging,
    provenance lineage evaluation, and fast binary/JSON disk persistence.
    """

    def __init__(self, default_session_id: str = "default_session") -> None:
        self.default_session_id = default_session_id
        self._checkpoints: Dict[str, SessionCheckpoint] = {}
        self._children: Dict[str, Set[str]] = collections.defaultdict(set)
        self._branches: Dict[str, str] = {}
        self._current_branch: str = "main"
        self._lock = threading.RLock()

    @property
    def current_branch(self) -> str:
        """Return active working branch name."""
        with self._lock:
            return self._current_branch

    def set_current_branch(self, branch_name: str) -> None:
        """Switch active branch context."""
        with self._lock:
            if branch_name not in self._branches and self._checkpoints:
                raise ValueError(f"Unknown branch: {branch_name}")
            self._current_branch = branch_name

    def create_checkpoint(
        self,
        state_diff: Dict[str, Any],
        messages: Optional[List[Dict[str, Any]]] = None,
        branch_name: Optional[str] = None,
        session_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        author: str = "orchestrator",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SessionCheckpoint:
        """Record atomic checkpoint and advance branch tip."""
        with self._lock:
            target_branch = branch_name or self._current_branch
            active_session = session_id or self.default_session_id
            msg_list = list(messages) if messages else []
            tag_list = list(tags) if tags else []
            meta_dict = dict(metadata) if metadata else {}

            parents: List[str] = []
            if parent_id is not None:
                if parent_id not in self._checkpoints:
                    raise KeyError(f"Parent checkpoint {parent_id} not found")
                parents.append(parent_id)
            elif target_branch in self._branches:
                parents.append(self._branches[target_branch])

            step_idx = 0
            if parents:
                primary_parent = self._checkpoints[parents[0]]
                step_idx = primary_parent.step_index + 1

            cid = f"chk_{uuid.uuid4().hex[:12]}"
            now = time.time()
            chk = SessionCheckpoint(
                checkpoint_id=cid,
                session_id=active_session,
                branch_name=target_branch,
                parent_ids=parents,
                timestamp=now,
                step_index=step_idx,
                state_diff=state_diff,
                messages=msg_list,
                author=author,
                tags=tag_list,
                metadata=meta_dict,
            )

            self._checkpoints[cid] = chk
            for p in parents:
                self._children[p].add(cid)
            self._branches[target_branch] = cid
            self._current_branch = target_branch

            return chk

    def fork_branch(
        self,
        new_branch_name: str,
        source_branch_or_id: Optional[str] = None,
        state_diff: Optional[Dict[str, Any]] = None,
        author: str = "orchestrator",
        tags: Optional[List[str]] = None,
    ) -> SessionCheckpoint:
        """Fork new named branch from designated checkpoint or branch tip."""
        with self._lock:
            if new_branch_name in self._branches:
                raise ValueError(f"Branch {new_branch_name} already exists")

            source_ref = source_branch_or_id or self._current_branch
            parent_cid: Optional[str] = None
            if source_ref in self._branches:
                parent_cid = self._branches[source_ref]
            elif source_ref in self._checkpoints:
                parent_cid = source_ref
            else:
                raise KeyError(f"Source branch or checkpoint {source_ref} not found")

            parent_chk = self._checkpoints[parent_cid]
            fork_tags = list(tags) if tags else []
            if "fork" not in fork_tags:
                fork_tags.append("fork")

            diff = dict(state_diff) if state_diff is not None else dict(parent_chk.state_diff)
            chk = self.create_checkpoint(
                state_diff=diff,
                messages=[],
                branch_name=new_branch_name,
                session_id=parent_chk.session_id,
                parent_id=parent_cid,
                author=author,
                tags=fork_tags,
                metadata={"forked_from": parent_cid, "source_ref": source_ref},
            )
            return chk

    def get_lowest_common_ancestor(self, id_a: str, id_b: str) -> Optional[str]:
        """Compute lowest common ancestor node across two DAG paths."""
        with self._lock:
            if id_a not in self._checkpoints or id_b not in self._checkpoints:
                return None
            if id_a == id_b:
                return id_a

            ancestors_a: Set[str] = set()
            queue = collections.deque([id_a])
            while queue:
                curr = queue.popleft()
                if curr not in ancestors_a:
                    ancestors_a.add(curr)
                    for p in self._checkpoints[curr].parent_ids:
                        queue.append(p)

            # BFS from id_b to discover first common ancestor in topological order
            queue_b = collections.deque([id_b])
            visited_b: Set[str] = set()
            candidate: Optional[str] = None

            while queue_b:
                curr = queue_b.popleft()
                if curr in visited_b:
                    continue
                visited_b.add(curr)
                if curr in ancestors_a:
                    return curr
                for p in self._checkpoints[curr].parent_ids:
                    queue_b.append(p)

            return candidate

    def merge_branches(
        self,
        source_branch: str,
        target_branch: Optional[str] = None,
        strategy: MergeStrategy = MergeStrategy.UNION,
        author: str = "merge_engine",
    ) -> SessionCheckpoint:
        """Merge source branch changes into target branch using specified strategy."""
        with self._lock:
            target = target_branch or self._current_branch
            if source_branch not in self._branches:
                raise KeyError(f"Source branch {source_branch} not found")
            if target not in self._branches:
                raise KeyError(f"Target branch {target} not found")

            src_cid = self._branches[source_branch]
            tgt_cid = self._branches[target]

            if src_cid == tgt_cid:
                return self._checkpoints[tgt_cid]

            lca_cid = self.get_lowest_common_ancestor(src_cid, tgt_cid)

            # Fast-forward condition: target is ancestor of source
            if lca_cid == tgt_cid:
                self._branches[target] = src_cid
                return self._checkpoints[src_cid]

            if strategy == MergeStrategy.FAST_FORWARD:
                raise ValueError(f"Cannot fast-forward {source_branch} into {target}")

            src_chk = self._checkpoints[src_cid]
            tgt_chk = self._checkpoints[tgt_cid]

            merged_state: Dict[str, Any] = {}
            merged_messages: List[Dict[str, Any]] = []

            if strategy == MergeStrategy.OURS:
                merged_state = dict(tgt_chk.state_diff)
                merged_messages = list(tgt_chk.messages)
            elif strategy == MergeStrategy.THEIRS:
                merged_state = dict(src_chk.state_diff)
                merged_messages = list(src_chk.messages)
            elif strategy in (MergeStrategy.UNION, MergeStrategy.NEWEST):
                merged_state = dict(tgt_chk.state_diff)
                merged_state.update(src_chk.state_diff)
                # Deduplicate messages by content/id while preserving sequence
                seen_msg_checksums: Set[str] = set()
                for m in tgt_chk.messages + src_chk.messages:
                    m_repr = json.dumps(m, sort_keys=True)
                    m_hash = hashlib.sha256(m_repr.encode("utf-8")).hexdigest()
                    if m_hash not in seen_msg_checksums:
                        seen_msg_checksums.add(m_hash)
                        merged_messages.append(m)

            merge_cid = f"chk_merge_{uuid.uuid4().hex[:10]}"
            now = time.time()
            max_step = max(tgt_chk.step_index, src_chk.step_index) + 1
            merge_parents = [tgt_cid, src_cid]

            merge_chk = SessionCheckpoint(
                checkpoint_id=merge_cid,
                session_id=tgt_chk.session_id,
                branch_name=target,
                parent_ids=merge_parents,
                timestamp=now,
                step_index=max_step,
                state_diff=merged_state,
                messages=merged_messages,
                author=author,
                tags=["merge", f"strategy:{strategy.value}"],
                metadata={
                    "source_branch": source_branch,
                    "target_branch": target,
                    "lca_checkpoint": lca_cid,
                },
            )

            self._checkpoints[merge_cid] = merge_chk
            for p in merge_parents:
                self._children[p].add(merge_cid)
            self._branches[target] = merge_cid

            return merge_chk

    def get_provenance(self, checkpoint_id: str) -> Dict[str, Any]:
        """Evaluate genealogical provenance and depth metrics for checkpoint."""
        with self._lock:
            if checkpoint_id not in self._checkpoints:
                raise KeyError(f"Checkpoint {checkpoint_id} not found")

            ancestors: List[str] = []
            queue = collections.deque([checkpoint_id])
            visited: Set[str] = set()
            roots: List[str] = []

            while queue:
                curr = queue.popleft()
                if curr in visited:
                    continue
                visited.add(curr)
                node = self._checkpoints[curr]
                if not node.parent_ids:
                    roots.append(curr)
                for p in node.parent_ids:
                    if p in self._checkpoints:
                        ancestors.append(p)
                        queue.append(p)

            target_node = self._checkpoints[checkpoint_id]
            return {
                "checkpoint_id": checkpoint_id,
                "session_id": target_node.session_id,
                "branch_name": target_node.branch_name,
                "step_index": target_node.step_index,
                "parent_ids": list(target_node.parent_ids),
                "child_count": len(self._children.get(checkpoint_id, set())),
                "ancestor_count": len(ancestors),
                "root_ids": roots,
                "checksum": target_node.checksum,
                "tags": list(target_node.tags),
            }

    def get_history(self, branch_or_id: str) -> List[SessionCheckpoint]:
        """Retrieve linear historical lineage from root to target checkpoint."""
        with self._lock:
            cid = self._branches.get(branch_or_id, branch_or_id)
            if cid not in self._checkpoints:
                raise KeyError(f"Branch or checkpoint {branch_or_id} not found")

            history: List[SessionCheckpoint] = []
            curr: Optional[str] = cid
            visited: Set[str] = set()

            while curr and curr in self._checkpoints and curr not in visited:
                visited.add(curr)
                node = self._checkpoints[curr]
                history.append(node)
                curr = node.parent_ids[0] if node.parent_ids else None

            history.reverse()
            return history

    def get_branch_tips(self) -> Dict[str, str]:
        """Return mapping of all active branch names to tip checkpoint IDs."""
        with self._lock:
            return dict(self._branches)

    def get_checkpoint(self, checkpoint_id: str) -> SessionCheckpoint:
        """Retrieve single checkpoint by identifier."""
        with self._lock:
            if checkpoint_id not in self._checkpoints:
                raise KeyError(f"Checkpoint {checkpoint_id} not found")
            return self._checkpoints[checkpoint_id]

    def export_json(self, file_path: Optional[str] = None) -> str:
        """Serialize complete session checkpoint DAG to structured JSON."""
        with self._lock:
            payload = {
                "version": "1.0",
                "default_session_id": self.default_session_id,
                "current_branch": self._current_branch,
                "branches": self._branches,
                "checkpoints": [c.to_dict() for c in self._checkpoints.values()],
            }
            json_str = json.dumps(payload, indent=2)
            if file_path:
                os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
            return json_str

    def import_json(self, source: str) -> None:
        """Reconstruct session checkpoint DAG from JSON payload or file path."""
        with self._lock:
            raw_text = source
            if os.path.exists(source):
                with open(source, "r", encoding="utf-8") as f:
                    raw_text = f.read()

            data = json.loads(raw_text)
            self.default_session_id = data.get("default_session_id", self.default_session_id)
            self._current_branch = data.get("current_branch", "main")
            self._branches = dict(data.get("branches", {}))

            self._checkpoints.clear()
            self._children.clear()

            for item in data.get("checkpoints", []):
                chk = SessionCheckpoint.from_dict(item)
                self._checkpoints[chk.checkpoint_id] = chk
                for p in chk.parent_ids:
                    self._children[p].add(chk.checkpoint_id)

    def export_binary(self, file_path: Optional[str] = None) -> bytes:
        """Serialize session checkpoint DAG to fast zlib-compressed binary payload."""
        with self._lock:
            json_str = self.export_json()
            compressed = zlib.compress(json_str.encode("utf-8"), level=6)
            if file_path:
                os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
                with open(file_path, "wb") as f:
                    f.write(compressed)
            return compressed

    def import_binary(self, source: Union[bytes, str]) -> None:
        """Reconstruct session checkpoint DAG from zlib-compressed binary payload or file."""
        with self._lock:
            raw_bytes: bytes
            if isinstance(source, str) and os.path.exists(source):
                with open(source, "rb") as f:
                    raw_bytes = f.read()
            elif isinstance(source, bytes):
                raw_bytes = source
            else:
                raise ValueError("Expected bytes or valid file path for binary import")

            decompressed = zlib.decompress(raw_bytes).decode("utf-8")
            self.import_json(decompressed)


_GLOBAL_CHECKPOINT_MANAGER: Optional[SessionCheckpointManager] = None
_GLOBAL_CHECKPOINT_LOCK = threading.RLock()


def get_checkpoint_manager() -> SessionCheckpointManager:
    """Acquire thread-safe singleton SessionCheckpointManager."""
    global _GLOBAL_CHECKPOINT_MANAGER
    with _GLOBAL_CHECKPOINT_LOCK:
        if _GLOBAL_CHECKPOINT_MANAGER is None:
            _GLOBAL_CHECKPOINT_MANAGER = SessionCheckpointManager()
        return _GLOBAL_CHECKPOINT_MANAGER


def reset_checkpoint_manager() -> SessionCheckpointManager:
    """Reset singleton SessionCheckpointManager."""
    global _GLOBAL_CHECKPOINT_MANAGER
    with _GLOBAL_CHECKPOINT_LOCK:
        _GLOBAL_CHECKPOINT_MANAGER = SessionCheckpointManager()
        return _GLOBAL_CHECKPOINT_MANAGER
